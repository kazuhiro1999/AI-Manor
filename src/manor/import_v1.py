"""`manor import-v1`: v1（AI執事 v1 / butler-board）の QUEUE.md / PROJECTS.md を
取り込む（ADR-003）。

対応表は ADR-003 §3 を読むこと。ここでは要点だけ:

- id は v1 のままノードの id に使う（`B101` `Q29` → task、`DQ29` → decision、
  `P1` `X1` → project）。`ids.py` の採番（`T1` `D1` `P1`...）は使わない
  （ADR-003 D3。同じファイルを2回取り込んでも増えない、という冪等性の要）
- v1 の id には `task.add()` / `decision.ask()` / `project.add()` のような
  既存 API では対応できない（どれも `ids.py.next_id` で新規採番するだけで、
  明示 id を受け付けない）。そのため task / decision / project / milestone の
  行そのものは `conn.execute` で直接 INSERT する。ノードの生成（`node` 表）は
  `graph.create_node(node_id=...)` を使う。辺は必ず `graph.link` を使う
  （これは既存 API の対応範囲内）。**この乖離は意図的な実装判断**であり、
  handoff 報告の「曖昧だった点」に書いてある
- 状態機械（`task.py` の `ALLOWED_TRANSITIONS`）は「これから起こる遷移」を
  検算する仕組みで、「過去のスナップショットを1回で取り込む」用途には合わない
  （例: v1 の `完了` 行は `todo -> done` を1手で表すが、状態機械はその遷移を
  許していない）。そのため取り込みは `task.status()` を経由せず、
  最終状態を直接書く。ただし `task_event` には「取り込み時にこの状態だった」
  という1行を必ず残す（ADR-001 §4「すべての遷移は task_event に1行」の精神を
  可能な範囲で守る）
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import date as _date
from pathlib import Path

from . import graph, util
from .compat.v1 import projects_doc as v1_projects
from .compat.v1 import queue_doc as v1_queue

#: 取り込んだノードの body に足す1行（ADR-003 D5）。
SOURCE_LINE = "（v1 から取り込み）"
#: task_event.note / task_event.actor（ADR-003 D5）。
EVENT_NOTE = "v1 import"
EVENT_ACTOR = "v1-import"

#: 状態欄・内容欄から拾う依存参照。`\b[BQ]\d+\b`（ADR-003 の原文）は Python の
#: 正規表現だと「日本語に直接くっついた ID」を取りこぼす。`\b` は Unicode モードで
#: 日本語の文字も `\w` とみなすため、"B2の後に" のように空白が無い場合に
#: 境界が成立しない（"B88 の後に" のように空白があれば ADR の書き方でも拾える）。
#: ここでは「直前が英数字/アンダースコアでない」「直後にもう1桁数字が続かない」
#: という条件に置き換えて、空白の有無に依らず拾えるようにしてある
#: （曖昧だった点として報告する）。
_DEP_RE = re.compile(r"(?<![A-Za-z0-9_])[BQ]\d+(?!\d)")
#: マイルストーンのタイトル・伝達キューの本文から拾う project 参照（`P<n>` / `X<n>`）。
_PROJ_REF_RE = re.compile(r"(?<![A-Za-z0-9_])[PX]\d+(?!\d)")

_VALID_LEVELS = frozenset({"L0", "L1", "L2", "L3", "HG"})
_VALID_RISK = frozenset({"", "low", "medium", "high"})

#: v1 の状態コード → manor の task.status。`other` は todo にして原文を note に残す
#: （ADR-003 §3「B（自走）」の行）。
_STATUS_MAP: dict[str, str] = {
    "todo": "todo",
    "doing": "doing",
    "waiting": "waiting",
    "hold": "hold",
    "resident": "resident",
    "done": "done",
    "withdrawn": "withdrawn",
    "other": "todo",
}


class _Ctx:
    """1回の取り込みで使う作業状態。

    `--dry-run` では実際には書き込まない。しかし「このプロジェクトはこの回で
    作られる予定」という情報が無いと、後続の行（例: task.project_id の解決）が
    毎回「まだ無い」と誤判定してしまう。そこで実際の DB 存在確認
    （`graph.node_exists`）に加えて `created_ids`（この回で作った/作る予定の id）
    も見る。書き込みを伴う本番実行では `graph.node_exists` が同一トランザクション
    内の INSERT を即座に見られるので `created_ids` は実質的に冗長だが、
    dry-run と本番で同じロジックにするためあえて両対応にしてある。
    """

    def __init__(self, conn: sqlite3.Connection, *, dry_run: bool) -> None:
        self.conn = conn
        self.dry_run = dry_run
        self.created_ids: set[str] = set()
        self.counts: dict[str, int] = {
            "node": 0,
            "task": 0,
            "decision": 0,
            "project": 0,
            "milestone": 0,
            "edge": 0,
        }
        self.unresolved: set[str] = set()
        self.notes: list[str] = []

    def exists(self, node_id: str) -> bool:
        if node_id in self.created_ids:
            return True
        return graph.node_exists(self.conn, node_id)

    def create_node(self, *, node_id: str, kind: str, title: str, body: str = "") -> None:
        self.created_ids.add(node_id)
        self.counts["node"] += 1
        if not self.dry_run:
            graph.create_node(self.conn, kind=kind, title=title, body=body, node_id=node_id)

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> None:
        if not self.dry_run:
            self.conn.execute(sql, params)

    def edge_exists(self, src: str, rel: str, dst: str) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM edge WHERE src = ? AND rel = ? AND dst = ?", (src, rel, dst)
            ).fetchone()
            is not None
        )

    def link(self, src: str, rel: str, dst: str, *, note: str = "") -> None:
        if not self.exists(src):
            self.unresolved.add(src)
            return
        if not self.exists(dst):
            self.unresolved.add(dst)
            return
        if not self.edge_exists(src, rel, dst):
            self.counts["edge"] += 1
        if not self.dry_run:
            graph.link(self.conn, src, rel, dst, note=note)

    def link_dependency(self, src: str, ref: str) -> None:
        """`depends_on` を張る。ただし `ref`（例: `Q22`）が task として存在せず
        `D<ref>`（例: `DQ22`）が decision として存在するなら、代わりに `decided_by`
        （task → decision）を張る。

        v1 の通常運用では、A の行が裁定されると行ごと C へ移り、task の元になる行が
        消える。「Q22/Q23 の裁定後」という状態欄の文言は「その task を待っている」
        のではなく「その決定を待っている」という意味であり、対応する task
        （`Q22`/`Q23`）は既に存在しない——`DQ22`/`DQ23` という decision だけが残る。
        実データ（実測）ではこの形の参照が unresolved に17件出た（すべて `Q<n>`）。
        このフォールバックにより、それらは `depends_on` ではなく `decided_by` として
        解決される（執事の裁定。ADR-003 §8-11）。
        """
        if not self.exists(ref) and self.exists(f"D{ref}"):
            self.link(src, "decided_by", f"D{ref}")
            return
        self.link(src, "depends_on", ref)

    def link_mention(self, src: str, ref: str) -> None:
        """`content`（内容欄）に出てきた ID への言及。依存ではないので `relates_to`
        （弱い関連）を張る。**存在しなくても unresolved には積まず、黙って無視する**
        ——言及先の行が後から消えているのは普通のことで、警告に値しない
        （執事の裁定。ADR-003 §8-10）。
        """
        if not self.exists(src) or not self.exists(ref):
            return
        if not self.edge_exists(src, "relates_to", ref):
            self.counts["edge"] += 1
        if not self.dry_run:
            graph.link(self.conn, src, "relates_to", ref)


# --- 補助 ---------------------------------------------------------------------


def _with_marker(*parts: str) -> str:
    """node.body を組み立てる。末尾に必ず `SOURCE_LINE` を付ける（ADR-003 D5）。"""
    lines = [p for p in parts if p.strip()]
    lines.append(SOURCE_LINE)
    return "\n".join(lines)


def _compose_detail(detail: v1_queue.DetailItem | None) -> tuple[str, str, str]:
    """D セクションの1件から (body の先頭に置く文, goal, next) を組み立てる。

    ADR-003 §3「D（詳細）」: 背景→node.body、目的→task.goal、
    fields の「次の一手」（あれば）→task.next、それ以外の fields は
    body の末尾に `- ラベル: 本文` で足す。
    """
    if detail is None:
        return "", "", ""
    body_parts: list[str] = []
    if detail.background:
        body_parts.append(detail.background)
    next_ = ""
    for f in detail.fields:
        label, text = f["label"], f["text"]
        if label in ("背景", "目的"):
            continue  # 背景は先頭、目的は goal へ別枠で入れる
        if label == "次の一手":
            next_ = text
            continue
        body_parts.append(f"- {label}: {text}")
    return "\n".join(body_parts), detail.purpose or "", next_


def _extract_date(text: str) -> str | None:
    d = v1_queue.parse_date(text or "")
    return d.isoformat() if d else None


def _date_range(text: str) -> tuple[str | None, str | None]:
    m = re.search(r"@(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})", text or "")
    if not m:
        return None, None
    return m.group(1), m.group(2)


def _dep_source_text(status_code: str, status_note: str, status_raw: str) -> str:
    """`depends_on` を拾う対象の「状態欄」テキストだけを返す。**`content`（内容欄）は
    絶対に見ない**——v1 は「B88 の後に」「Q22/Q23 の裁定後」を状態欄に書く運用で、
    内容欄に出てくる ID（例: `B92`）への言及は依存ではなく単なる言及だった
    （実データで確認。執事の裁定。ADR-003 §8-10）。

    `status_code == 'other'` のときは `status_note`（`—` 以降の補足）が空のことが
    多いので、状態セルの原文全体を見る。それ以外は `status_note` を見る。
    """
    if status_code == "other":
        return v1_queue.strip_md(status_raw or "").strip()
    return status_note or ""


def _a_verdict(status_raw: str) -> tuple[str | None, str]:
    """A セクションの状態欄から裁定を読む（ADR-003 §3「A」の行）。

    v1 の運用では通常、裁定が付くと A の行そのものが削除されて C へ移る
    （`apply_decision`）。しかし ADR は「A に残ったまま状態欄に承認/却下等が
    入っているケース」を明示的に挙げているので、それも扱えるようにしてある。
    戻り値は (verdict|None, 状態欄の素の文)。
    """
    plain = v1_queue.strip_md(status_raw or "").strip()
    if not plain or plain.lower() in v1_queue.PLACEHOLDER_CELLS:
        return None, plain
    if plain.startswith("却下"):
        return "rejected", plain
    if plain.startswith("承認"):
        return "approved", plain
    if plain.startswith("こう直して") or plain.startswith("修正"):
        return "modified", plain
    return None, plain


def _decision_verdict_from_text(text: str) -> str:
    """C セクション（裁定文）から verdict を読む。引けなければ `modified` に寄せる。

    v1 の裁定文は `build_decision_text` が生成する定型句（**承認**／**却下**／
    **こう直して**）だが、手で書かれた行も想定して緩めに判定する。
    """
    plain = v1_queue.strip_md(text or "")
    if "却下" in plain:
        return "rejected"
    if "承認" in plain:
        return "approved"
    return "modified"


#: `task` 表のうち、v1 から意味のある値が来る列（`manor import-v1 --sync` が
#: 追いつかせる対象。ADR-003 §8-18）。goal/next/recommendation/risk/body は
#: 自由文でありここには含めない（D セクションの解釈揺れで実害の薄い差が出やすい）。
_TASK_SYNC_FIELDS: tuple[str, ...] = (
    "status", "status_note", "owner", "level", "section", "project_id", "done_at", "start", "end",
)


def _pending_sync_fields(item: v1_queue.PendingItem, exists_fn) -> dict[str, object]:
    """A（PendingItem）から task の同期対象フィールド（`_TASK_SYNC_FIELDS`）の
    期待値を組み立てる。`_import_pending`（INSERT の値）と `sync()`（UPDATE の
    比較対象）の両方がここを通ることで、2つの実装が違う答えを出す事故を防ぐ
    （「同じ Markdown を別実装で読むと必ず違う答えを出す」という ADR-003 D1 の
    教訓を、import と sync の間でも守る）。

    `exists_fn` は project の存在確認（`_Ctx.exists` 相当）。project_id は
    実在する project にしか張れない（外部キー制約）ので、ここで解決する。
    """
    verdict, _status_plain = _a_verdict(item.status)
    pj = item.pj.strip()
    project_id = pj if pj and exists_fn(pj) else None
    return {
        "status": "todo",
        "status_note": item.status_note or "",
        "owner": "butler",
        "level": "HG",
        "section": "B" if verdict else "A",
        "project_id": project_id,
        "done_at": None,
        "start": None,
        "end": None,
    }


def _running_sync_fields(item: v1_queue.RunningItem, exists_fn) -> dict[str, object]:
    """B（RunningItem）版の `_pending_sync_fields`。"""
    pj = item.pj.strip()
    project_id = pj if pj and exists_fn(pj) else None
    status = _STATUS_MAP.get(item.status_code, "todo")
    level = item.level if item.level in _VALID_LEVELS else "L2"
    if item.status_code == "other":
        status_note = v1_queue.strip_md(item.status).strip()
    else:
        status_note = item.status_note or ""
    if status in ("waiting", "hold") and not status_note.strip():
        # C3（waiting で status_note が空）を避ける。取り込み側で埋める。
        status_note = "（v1: 待つ理由が未記載）"
    done_at = f"{item.done_date}T00:00:00" if item.done_date else None
    start, end = _date_range(item.content)
    return {
        "status": status,
        "status_note": status_note,
        "owner": item.owner,
        "level": level,
        "section": "B",
        "project_id": project_id,
        "done_at": done_at,
        "start": start,
        "end": end,
    }


# --- project -------------------------------------------------------------------


def _import_projects(ctx: _Ctx, doc: v1_projects.ProjectsDoc) -> None:
    for p in doc.projects:
        pid = p.id.strip()
        if not pid:
            continue
        if ctx.exists(pid):
            continue
        code = pid.lower()
        title = v1_queue.strip_md(p.name)
        status = _project_status(p.status_plain)
        due = _extract_date(p.deadline)
        next_action = v1_queue.strip_md(p.next_action)
        ctx.create_node(node_id=pid, kind="project", title=title, body=_with_marker(""))
        ctx.execute(
            "INSERT INTO project (id, code, kind, priority, preset, status, next_action, due)"
            " VALUES (?, ?, ?, ?, 'standard', ?, ?, ?)",
            (pid, code, p.category, _priority_from_rank(p.priority_rank), status, next_action, due),
        )
        ctx.counts["project"] += 1


# --- milestone -------------------------------------------------------------------


def _import_milestones(ctx: _Ctx, doc: v1_projects.ProjectsDoc) -> None:
    """マイルストーンには v1 側に安定した id が無いので、(date, title, project) の
    組み合わせで既存行を探して重複を避ける（冪等性の担保）。id 自体は v1 の id を
    引き継ぐ対象ではないので、通常どおり `ids.py` の採番（`M<n>`）を使う。
    """
    from .ids import next_id  # 局所 import: milestone だけ v1 id を持たないためここでだけ要る

    seen_this_run: set[tuple[str, str, str | None]] = set()
    for m in doc.milestones:
        if not m.date_iso:
            ctx.notes.append(f"マイルストーンの日付が読めません: {m.title_plain!r}")
            continue
        title = m.title_plain
        proj_match = _PROJ_REF_RE.search(m.title_plain)
        project_id = proj_match.group(0) if proj_match and ctx.exists(proj_match.group(0)) else None

        key = (m.date_iso, title, project_id)
        if key in seen_this_run:
            continue
        seen_this_run.add(key)
        existing = ctx.conn.execute(
            "SELECT m.id FROM milestone m JOIN node n ON n.id = m.id"
            " WHERE m.date = ? AND n.title = ? AND m.project_id IS ?",
            (m.date_iso, title, project_id),
        ).fetchone()
        if existing is not None:
            continue

        ctx.counts["node"] += 1
        ctx.counts["milestone"] += 1
        if ctx.dry_run:
            continue
        milestone_id = next_id(ctx.conn, "M")
        now_ts = util.now()
        ctx.conn.execute(
            "INSERT INTO node (id, kind, title, body, created_at, updated_at)"
            " VALUES (?, 'milestone', ?, ?, ?, ?)",
            (milestone_id, title, _with_marker(""), now_ts, now_ts),
        )
        ctx.conn.execute(
            "INSERT INTO milestone (id, date, approximate, project_id) VALUES (?, ?, ?, ?)",
            (milestone_id, m.date_iso, 1 if m.approximate else 0, project_id),
        )


# --- note（伝達キュー） -----------------------------------------------------------


def _import_relays(ctx: _Ctx, doc: v1_projects.ProjectsDoc) -> None:
    """伝達キュー（`RelayItem`）→ note（ADR-003 §3「PROJECTS.md」の行）。

    v1 の relay 行の `#` 列はファイル内でのみ安定した番号で、他の kind と衝突しない
    よう `RL` を前置して node id にする。`about` の相手（project）は origin / to /
    content のいずれかに現れる `P<n>` / `X<n>` を拾う（どの列を見るべきかは ADR に
    明記が無く、この実装判断は「曖昧だった点」に書いてある）。
    """
    for r in doc.relays:
        raw_id = v1_queue.strip_md(r.id).strip()
        if raw_id:
            node_id = f"RL{raw_id}"
        else:
            digest = hashlib.sha1(r.content.encode("utf-8")).hexdigest()[:8]
            node_id = f"RL{digest}"
        if ctx.exists(node_id):
            continue
        title = v1_queue.strip_md(r.content)
        ctx.create_node(node_id=node_id, kind="note", title=title, body=_with_marker(""))

        about_match = None
        for field_text in (r.origin, r.to, r.content):
            about_match = _PROJ_REF_RE.search(field_text or "")
            if about_match:
                break
        if about_match:
            ctx.link(node_id, "about", about_match.group(0))


# --- QUEUE.md: A（PendingItem） ---------------------------------------------------


def _import_pending(ctx: _Ctx, doc: v1_queue.QueueDoc) -> None:
    for item in doc.pending:
        qid = item.id.strip()
        if not qid:
            ctx.notes.append("A セクションに # が空の行があります（無視）")
            continue
        did = f"D{qid}"
        title = v1_queue.strip_md(item.title)
        recommendation = v1_queue.strip_md(item.recommendation).strip()
        if not recommendation:
            # C4（section=A は recommendation 必須）に必ず引っかかるため、
            # 取り込み側で埋める（曖昧だった点として報告する）。
            recommendation = "（v1: 執事の推奨が未記載）"
        risk = item.risk_level if item.risk_level in _VALID_RISK else ""

        detail = doc.details.get(qid)
        body_lead, goal, next_ = _compose_detail(detail)

        verdict, status_plain = _a_verdict(item.status)
        asked_at = f"{item.raised_date}T00:00:00" if item.raised_date else util.now()
        fields = _pending_sync_fields(item, ctx.exists)

        if not ctx.exists(qid):
            ctx.create_node(node_id=qid, kind="task", title=title, body=_with_marker(body_lead))
            ctx.execute(
                "INSERT INTO task (id, project_id, status, status_note, owner, level, section,"
                " goal, now, next, recommendation, risk, due, start, \"end\", done_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, NULL, ?, ?, ?)",
                (
                    qid, fields["project_id"], fields["status"], fields["status_note"], fields["owner"],
                    fields["level"], fields["section"], goal, next_, recommendation, risk,
                    fields["start"], fields["end"], fields["done_at"],
                ),
            )
            ctx.execute(
                "INSERT INTO task_event (task_id, at, from_status, to_status, note, actor)"
                " VALUES (?, ?, NULL, ?, ?, ?)",
                (qid, util.now(), fields["status"], EVENT_NOTE, EVENT_ACTOR),
            )
            ctx.counts["task"] += 1

        if fields["project_id"]:
            # ADR-003 §3「B（自走）」の行: pj → project_id（あれば part_of 辺も）。
            # A（PendingItem）にも pj 列があり、ADR の A の行には明記が無いが
            # 対称に扱うのが自然なので、A・B とも project_id を解決した先には
            # part_of を張っている（曖昧だった点として報告する）。
            ctx.link(qid, "part_of", fields["project_id"])

        if not ctx.exists(did):
            dec_status = verdict or "open"
            ruling = status_plain if verdict else ""
            decided_at = asked_at if verdict else None
            ctx.create_node(node_id=did, kind="decision", title=title, body=_with_marker(""))
            ctx.execute(
                "INSERT INTO decision (id, status, recommendation, background, risk, ruling,"
                " asked_at, decided_at) VALUES (?, ?, ?, '', ?, ?, ?, ?)",
                (did, dec_status, recommendation, risk, ruling, asked_at, decided_at),
            )
            ctx.counts["decision"] += 1

        ctx.link(qid, "decided_by", did)


def _link_pending_deps(ctx: _Ctx, doc: v1_queue.QueueDoc) -> None:
    """`depends_on` は全ての A/B タスクが作られた後の別パスで張る。

    ADR-003 の例（「B88 の後に」）は依存先が表の上のほうに先に出てくる想定だが、
    実データの並び順は保証されない。1パスで作りながら張ると、依存先がまだ
    テーブルの後方にしか出てきていない「前方参照」で `unresolved` に落ちてしまう
    （実際には存在するのに、処理順の都合で見つからないと誤判定する）。
    A・B のノードを全部作り終えてから張ることでこれを避ける。
    """
    for item in doc.pending:
        qid = item.id.strip()
        if not qid:
            continue
        text = _dep_source_text(item.status_code, item.status_note, item.status)
        for ref in sorted(set(_DEP_RE.findall(text))):
            if ref == qid:
                continue
            ctx.link_dependency(qid, ref)


# --- QUEUE.md: B（RunningItem） ---------------------------------------------------


def _import_running(ctx: _Ctx, doc: v1_queue.QueueDoc) -> None:
    for item in doc.running:
        bid = item.id.strip()
        if not bid:
            ctx.notes.append("B セクションに # が空の行があります（無視）")
            continue
        if ctx.exists(bid):
            continue

        title = v1_queue.strip_md(item.content)
        fields = _running_sync_fields(item, ctx.exists)

        detail = doc.details.get(bid)
        body_lead, goal, next_ = _compose_detail(detail)

        ctx.create_node(node_id=bid, kind="task", title=title, body=_with_marker(body_lead))
        ctx.execute(
            "INSERT INTO task (id, project_id, status, status_note, owner, level, section,"
            " goal, now, next, recommendation, risk, due, start, \"end\", done_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 'B', ?, '', ?, '', '', NULL, ?, ?, ?)",
            (
                bid, fields["project_id"], fields["status"], fields["status_note"], fields["owner"],
                fields["level"], goal, next_, fields["start"], fields["end"], fields["done_at"],
            ),
        )
        at = fields["done_at"] or util.now()
        ctx.execute(
            "INSERT INTO task_event (task_id, at, from_status, to_status, note, actor)"
            " VALUES (?, ?, NULL, ?, ?, ?)",
            (bid, at, fields["status"], EVENT_NOTE, EVENT_ACTOR),
        )
        ctx.counts["task"] += 1

        if fields["project_id"]:
            # ADR-003 §3「B（自走）」の行: pj → project_id（あれば part_of 辺も）。
            ctx.link(bid, "part_of", fields["project_id"])


def _link_running_deps(ctx: _Ctx, doc: v1_queue.QueueDoc) -> None:
    """`_link_pending_deps` と同じ理由で、依存の張り替えは別パスにしてある。

    実データで確認した誤り（執事の裁定。ADR-003 §8-10）: `content`（内容欄）に
    出てくる ID への参照は「言及」であって「依存」ではない。依存は状態欄
    （`status_note` / `other` のときは状態セルの原文全体）からだけ拾い、
    `content` からの参照は弱い関連 `relates_to` として別に張る
    （存在しなくても黙って無視。`link_mention` 参照）。
    """
    for item in doc.running:
        bid = item.id.strip()
        if not bid:
            continue

        dep_text = _dep_source_text(item.status_code, item.status_note, item.status)
        for ref in sorted(set(_DEP_RE.findall(dep_text))):
            if ref == bid:
                continue
            ctx.link_dependency(bid, ref)

        for ref in sorted(set(_DEP_RE.findall(item.content or ""))):
            if ref == bid:
                continue
            ctx.link_mention(bid, ref)


# --- QUEUE.md: C（DecidedItem） ---------------------------------------------------


def _import_decided(ctx: _Ctx, doc: v1_queue.QueueDoc) -> None:
    for item in doc.decided:
        qid = item.id.strip()
        if not qid:
            continue
        did = f"D{qid}"
        title = v1_queue.strip_md(item.title)
        decided_date = _extract_date(item.decided)
        decided_at = f"{decided_date}T00:00:00" if decided_date else util.now()
        verdict = _decision_verdict_from_text(item.decision)
        ruling = v1_queue.strip_md(item.decision)

        detail = doc.details.get(qid)
        body_lead, _goal, _next = _compose_detail(detail)

        if not ctx.exists(did):
            # C にしか無い（A から移された）決定。task は作らない
            # （ADR-003 §3「C」の行は decision の列だけを対応づけている）。
            ctx.create_node(node_id=did, kind="decision", title=title, body=_with_marker(body_lead))
            ctx.execute(
                "INSERT INTO decision (id, status, recommendation, background, risk, ruling,"
                " asked_at, decided_at) VALUES (?, ?, '', '', '', ?, ?, ?)",
                (did, verdict, ruling, decided_at, decided_at),
            )
            ctx.counts["decision"] += 1
        else:
            # 既に A の pass で作られている（同じファイル内に A・C 両方あるレアケース）。
            # C を正として上書きする。
            ctx.execute(
                "UPDATE decision SET status = ?, ruling = ?, decided_at = ? WHERE id = ?",
                (verdict, ruling, decided_at, did),
            )


# --- 本体 ---------------------------------------------------------------------


def run(
    conn: sqlite3.Connection,
    *,
    queue_path: Path,
    projects_path: Path,
    dry_run: bool = False,
) -> dict[str, object]:
    """QUEUE.md / PROJECTS.md を読み、manor の node/task/decision/project/milestone/
    edge に取り込む。`dry_run=True` のときは DB を一切書き換えず、件数だけ返す。

    冪等性（ADR-003 D3）: 既に同じ id のノードがあれば作り直さない。辺は
    `graph.link` が ON CONFLICT で更新するだけなので、何度呼んでも行が増えない。
    """
    today_date = _date.fromisoformat(util.today())

    qdoc = v1_queue.parse_queue(Path(queue_path), today_date)
    pdoc = v1_projects.parse_projects(Path(projects_path), today_date)

    ctx = _Ctx(conn, dry_run=dry_run)

    # 順序が要る: project → milestone/note（project を参照する）→ task（project_id を
    # 解決する）→ C（A で作られた decision があれば上書き。C のみの decision — 例えば
    # DQ22 — もここで生まれる）→ depends_on（全タスク・全 decision が揃ってから。
    # 前方参照を取りこぼさないためだけでなく、`link_dependency` の decided_by
    # フォールバック（Q22 が task として無く DQ22 が decision としてあるとき）が
    # C のインポートより前に走ると、まだ存在しない DQ22 を見つけられない。
    # 執事の裁定。ADR-003 §8-11）。
    _import_projects(ctx, pdoc)
    _import_milestones(ctx, pdoc)
    _import_relays(ctx, pdoc)
    _import_pending(ctx, qdoc)
    _import_running(ctx, qdoc)
    _import_decided(ctx, qdoc)
    _link_pending_deps(ctx, qdoc)
    _link_running_deps(ctx, qdoc)

    counts = dict(ctx.counts)

    return {
        "dry_run": dry_run,
        "counts": counts,
        "unresolved": sorted(ctx.unresolved),
        "notes": ctx.notes,
        "errors": {"queue": list(qdoc.errors), "projects": list(pdoc.errors)},
    }


# --- reconcile（`manor import-v1 --reconcile`） -------------------------------------
#
# 主人の要望「v1 の実データで DB を作り、齟齬がないかテストする」の道具。
# `run()` と違って**書き込みは一切しない**。v1 の Markdown を再パースし、
# 「もし今 import-v1 したら何が出来るはずか」を `run()` と同じ規則で期待値として
# 組み立て、DB の実際の行と1件ずつ突き合わせる。
#
# 比べるのは値として意味のある列だけ（body/goal/next のような自由文は、
# markdown の解釈の揺れで実害の無い差が出やすいので比べない）。


def _reconcile_check(
    mismatches: list[dict[str, object]], counters: dict[str, int], entity_id: str, field: str,
    v1_value: object, db_value: object,
) -> None:
    """v1 側の期待値と DB の実値を1項目ぶん突き合わせる。"""
    if v1_value == db_value:
        counters["matched"] += 1
    else:
        mismatches.append({"id": entity_id, "field": field, "v1": v1_value, "db": db_value})


def reconcile(
    conn: sqlite3.Connection,
    *,
    queue_path: Path,
    projects_path: Path,
) -> dict[str, object]:
    """QUEUE.md / PROJECTS.md を再パースし、DB と1件ずつ突き合わせる（取り込まない）。

    戻り値: `matched`（一致した項目数）、`mismatches`（`{id, field, v1, db}` の一覧）、
    `only_in_v1`（v1 にあって DB に無い id）、`only_in_db`（DB にあって v1 に無い id。
    v1 由来でない — body 末尾に `SOURCE_LINE` が無い — ものは除く）、`errors`（v1
    パーサの errors）。
    """
    today_date = _date.fromisoformat(util.today())
    qdoc = v1_queue.parse_queue(Path(queue_path), today_date)
    pdoc = v1_projects.parse_projects(Path(projects_path), today_date)

    counters = {"matched": 0}
    mismatches: list[dict[str, object]] = []
    only_in_v1: list[str] = []
    v1_ids: set[str] = set()

    def check(entity_id: str, field: str, v1_value: object, db_value: object) -> None:
        _reconcile_check(mismatches, counters, entity_id, field, v1_value, db_value)

    def check_dependency(src: str, ref: str) -> None:
        """v1 の状態欄から拾える依存が DB にもあるか（`_Ctx.link_dependency` と同じ
        解決規則: `ref` が task として無く `D<ref>` が decision としてあれば
        decided_by を期待する）。どちらも無ければ import 時も unresolved になり
        辺は張られていないはずなので、比べようが無く何もしない。
        """
        if graph.node_exists(conn, ref):
            exists = (
                conn.execute(
                    "SELECT 1 FROM edge WHERE src = ? AND rel = 'depends_on' AND dst = ?", (src, ref)
                ).fetchone()
                is not None
            )
            check(src, f"depends_on:{ref}", True, exists)
        else:
            decision_ref = f"D{ref}"
            if graph.node_exists(conn, decision_ref):
                exists = (
                    conn.execute(
                        "SELECT 1 FROM edge WHERE src = ? AND rel = 'decided_by' AND dst = ?",
                        (src, decision_ref),
                    ).fetchone()
                    is not None
                )
                check(src, f"decided_by:{decision_ref}", True, exists)
            # else: v1 側でも解決できない参照（import なら unresolved）。比較対象が無い。

    # --- project -----------------------------------------------------------------
    for p in pdoc.projects:
        pid = p.id.strip()
        if not pid:
            continue
        v1_ids.add(pid)
        row = conn.execute("SELECT * FROM project WHERE id = ?", (pid,)).fetchone()
        if row is None:
            only_in_v1.append(pid)
            continue
        counters["matched"] += 1  # 存在
        expected_status = _project_status(p.status_plain)
        check(pid, "status", expected_status, str(row["status"]))
        # priority / preset は比べない（主人からの明示指示。ADR-003 §8-13）

    # --- QUEUE.md: A（PendingItem） ------------------------------------------------
    #: decision の期待値。C（DecidedItem）が後で上書きする（`run()` と同じ優先度）。
    expected_decisions: dict[str, dict[str, object]] = {}

    for item in qdoc.pending:
        qid = item.id.strip()
        if not qid:
            continue
        v1_ids.add(qid)
        did = f"D{qid}"
        v1_ids.add(did)

        verdict, _status_plain = _a_verdict(item.status)
        expected_decisions[did] = {"status": verdict or "open", "ruling_present": bool(verdict)}

        row = conn.execute("SELECT * FROM task WHERE id = ?", (qid,)).fetchone()
        if row is None:
            only_in_v1.append(qid)
            continue
        counters["matched"] += 1  # 存在
        project_id = item.pj.strip() or None
        section = "B" if verdict else "A"
        check(qid, "status", "todo", str(row["status"]))
        check(qid, "owner", "butler", str(row["owner"]))
        check(qid, "level", "HG", str(row["level"]))
        check(qid, "section", section, str(row["section"]))
        check(qid, "project_id", project_id, row["project_id"])
        check(qid, "done_at", None, row["done_at"])
        check(qid, "start", None, row["start"])
        check(qid, "end", None, row["end"])

        dep_text = _dep_source_text(item.status_code, item.status_note, item.status)
        for ref in sorted(set(_DEP_RE.findall(dep_text))):
            if ref == qid:
                continue
            check_dependency(qid, ref)

    # --- QUEUE.md: B（RunningItem） -------------------------------------------------
    for item in qdoc.running:
        bid = item.id.strip()
        if not bid:
            continue
        v1_ids.add(bid)

        row = conn.execute("SELECT * FROM task WHERE id = ?", (bid,)).fetchone()
        if row is None:
            only_in_v1.append(bid)
            continue
        counters["matched"] += 1  # 存在
        project_id = item.pj.strip() or None
        status = _STATUS_MAP.get(item.status_code, "todo")
        level = item.level if item.level in _VALID_LEVELS else "L2"
        start, end = _date_range(item.content)
        check(bid, "status", status, str(row["status"]))
        check(bid, "owner", item.owner, str(row["owner"]))
        check(bid, "level", level, str(row["level"]))
        check(bid, "section", "B", str(row["section"]))
        check(bid, "project_id", project_id, row["project_id"])
        db_done_date = str(row["done_at"]).split("T", 1)[0] if row["done_at"] else None
        check(bid, "done_at", item.done_date, db_done_date)
        check(bid, "start", start, row["start"])
        check(bid, "end", end, row["end"])

        dep_text = _dep_source_text(item.status_code, item.status_note, item.status)
        for ref in sorted(set(_DEP_RE.findall(dep_text))):
            if ref == bid:
                continue
            check_dependency(bid, ref)

    # --- QUEUE.md: C（DecidedItem） -------------------------------------------------
    for item in qdoc.decided:
        qid = item.id.strip()
        if not qid:
            continue
        did = f"D{qid}"
        v1_ids.add(did)
        expected_decisions[did] = {
            "status": _decision_verdict_from_text(item.decision),
            "ruling_present": True,
        }

    for did, expected in expected_decisions.items():
        row = conn.execute("SELECT * FROM decision WHERE id = ?", (did,)).fetchone()
        if row is None:
            only_in_v1.append(did)
            continue
        counters["matched"] += 1  # 存在
        check(did, "status", expected["status"], str(row["status"]))
        ruling_present_db = bool(str(row["ruling"] or "").strip())
        check(did, "ruling_present", expected["ruling_present"], ruling_present_db)

    # --- PROJECTS.md: マイルストーン ---------------------------------------------------
    #: v1 に安定した id が無いので、`_import_milestones` と同じ (date, title, project)
    #: をキーに DB 側の行を探して照合する。見つかれば date/title は定義上一致する
    #: （突き合わせの鍵そのものなので）——実質的な確認は「存在するか」。
    for m in pdoc.milestones:
        if not m.date_iso:
            continue  # 日付が読めないものは import 側でも取り込まれない
        title = m.title_plain
        proj_match = _PROJ_REF_RE.search(m.title_plain)
        project_id = (
            proj_match.group(0) if proj_match and graph.node_exists(conn, proj_match.group(0)) else None
        )
        display_id = f"milestone:{m.date_iso}:{title}"
        row = conn.execute(
            "SELECT m.id FROM milestone m JOIN node n ON n.id = m.id"
            " WHERE m.date = ? AND n.title = ? AND m.project_id IS ?",
            (m.date_iso, title, project_id),
        ).fetchone()
        if row is None:
            only_in_v1.append(display_id)
            continue
        counters["matched"] += 1  # 存在（date/title は突き合わせキーなので一致は自明）
        v1_ids.add(str(row["id"]))

    # --- only_in_db: v1 由来（body 末尾のマーカー付き）なのに v1 に見つからない行 ---------
    only_in_db: list[str] = []
    for r in conn.execute(
        "SELECT id, body FROM node WHERE kind IN ('task', 'decision', 'project', 'milestone')"
    ).fetchall():
        node_id = str(r["id"])
        body = str(r["body"] or "")
        if body.rstrip().endswith(SOURCE_LINE) and node_id not in v1_ids:
            only_in_db.append(node_id)

    return {
        "matched": counters["matched"],
        "mismatches": sorted(mismatches, key=lambda m: (str(m["id"]), str(m["field"]))),
        "only_in_v1": sorted(set(only_in_v1)),
        "only_in_db": sorted(set(only_in_db)),
        "errors": {"queue": list(qdoc.errors), "projects": list(pdoc.errors)},
    }


def _project_status(status_text: str) -> str:
    """v1 の状態欄からプロジェクトの状態を決める。

    2026-09-02 実データで判明: 「9/11軸完了・残2軸」「主要機能は完了」のように**途中経過**として
    「完了」を含む行が `done` になっていた（5件）。`done` は状態欄が「完了」で**始まる**ときだけ。
    """
    text = (status_text or "").strip().lstrip("*＊ 　")
    return "done" if text.startswith(("完了", "終了", "done")) else "active"


# --- sync（`manor import-v1 --sync`） -----------------------------------------------
#
# 主人は当面 v1 と manor を併用する。v1 の執事は QUEUE.md / PROJECTS.md を更新し
# 続けるので、`run()`（既存 id はスキップするだけ）では manor 側が v1 に追従できない
# （実データで `--reconcile` が「B189 の status/done_at が違う」「B190 が v1 にだけ
# ある」を返した。ADR-003 §8-19）。`sync()` は
#   ① v1 にあって DB に無い行は通常どおり追加する（`run()` と同じ経路を再利用）
#   ② v1 由来で、かつ import／前回の sync 以降 manor 側で誰も触っていない行だけ、
#      v1 の現在値に追いつかせる
#   ③ manor 側で触られた行は更新せず `skipped_local` に列挙する
#   ④ v1 から消えた行には触らない（それを検出するのは `reconcile()` の仕事）

#: `sync()` が行を更新したときに残す `task_event`（`run()`/`_import_*` の
#: `EVENT_NOTE`/`EVENT_ACTOR` とは別の印にする。理由は `_task_is_untouched` 参照）。
SYNC_EVENT_NOTE = "v1 sync"
SYNC_EVENT_ACTOR = "v1-sync"

#: task_event の最新 actor がこの集合に入っていれば「manor 側で人（butler/master/
#: 部下エージェント）が触っていない」とみなす。import 由来（`EVENT_ACTOR`）だけでなく
#: 過去の sync 由来（`SYNC_EVENT_ACTOR`）も含めないと、1回 sync した行は actor が
#: `v1-sync` に変わり、次の sync では「もう manor 側で触られた」と誤判定されて
#: 永久に追従できなくなる。
_AUTOMATED_ACTORS: frozenset[str] = frozenset({EVENT_ACTOR, SYNC_EVENT_ACTOR})


def _is_v1_origin(conn: sqlite3.Connection, node_id: str) -> bool:
    node = graph.get_node(conn, node_id)
    if node is None:
        return False
    return str(node["body"] or "").rstrip().endswith(SOURCE_LINE)


def _task_is_untouched(
    conn: sqlite3.Connection, task_id: str, *, expected_decision: dict[str, object] | None = None
) -> bool:
    """import／sync 以降、manor 側で誰も手を動かしていないか。

    2つを見る:
    - `task_event` の最新行の `actor` が `_AUTOMATED_ACTORS`（import か過去の
      sync 自身）のままであること。`manor task status` 等を人間や部下が使うと
      actor が `butler`/`master`/エージェント名などに変わるので、そこで区別が付く
    - `decided_by` で結ばれた decision があれば、その decision がまだ人の手で
      裁定されていない（`decision.rule()` を経ていない）こと

    2つめの判定は「`status='open'` かつ `ruling` が空」だけでは足りない。
    ADR-003 §8-11 のとおり、A の行が状態欄に「承認/却下」を含んだまま取り込まれる
    ケースでは、**import 自身**が decision を最初から approved/rejected で作る
    （`_import_pending` 参照）。これを「人が裁定した」と誤判定すると、A の行が
    最初から裁定済みのタスクは永久に sync 対象から外れてしまう（実際に見つけた
    バグ。fixture の Q2 で再現した）。

    そこで `expected_decision`（「今の v1 データならこの decision はこうなるはず」
    ——`_a_verdict` で導く。B の行など該当が無ければ `None`）を渡してもらい、
    decision の実際の (status, ruling の有無) が **これと完全に一致するなら**
    「import/sync が v1 のデータからそのまま作った状態」とみなして通す。
    一致しなければ（`expected_decision` が無い場合を含む）保守的に「触られた」
    扱いにする——`decision.rule()` が実際に動かしたのか、v1 側が変わったのに
    decision だけ古いのか区別が付かない以上、task.section 等を巻き戻すよりは
    止める方が安全。
    """
    row = conn.execute(
        "SELECT actor FROM task_event WHERE task_id = ? ORDER BY id DESC LIMIT 1", (task_id,)
    ).fetchone()
    if row is None or str(row["actor"]) not in _AUTOMATED_ACTORS:
        return False
    for e in conn.execute(
        "SELECT dst FROM edge WHERE src = ? AND rel = 'decided_by'", (task_id,)
    ).fetchall():
        drow = conn.execute(
            "SELECT status, ruling FROM decision WHERE id = ?", (e["dst"],)
        ).fetchone()
        if drow is None:
            continue
        actual_status = str(drow["status"])
        actual_ruling_present = bool(str(drow["ruling"] or "").strip())
        if expected_decision is None:
            return False
        if (
            actual_status != expected_decision["status"]
            or actual_ruling_present != expected_decision["ruling_present"]
        ):
            return False
    return True


def _apply_task_sync(
    conn: sqlite3.Connection, task_id: str, fields: dict[str, object], *, dry_run: bool
) -> list[str]:
    """`fields`（`_pending_sync_fields`/`_running_sync_fields` の戻り値）と DB の
    現在値を比べ、違う列だけ UPDATE する。変わった列名の一覧を返す（0件なら
    何もしない＝呼び出し側で `unchanged` に数える）。

    状態機械（`task.status()`）は経由しない。`run()` と同じ理由——「これから
    起こる遷移」を検算する仕組みは「過去のスナップショットへ追いつく」という
    用途の前提と合わない（例: v1 側で `未着手` から一気に `完了` になっていた場合、
    状態機械は `todo -> done` の直接遷移を許さない）。
    """
    row = conn.execute("SELECT * FROM task WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        return []
    changed = [f for f in _TASK_SYNC_FIELDS if fields[f] != row[f]]
    if changed and not dry_run:
        assignments = ", ".join(f'"{f}" = ?' if f == "end" else f"{f} = ?" for f in changed)
        conn.execute(
            f"UPDATE task SET {assignments} WHERE id = ?",
            (*(fields[f] for f in changed), task_id),
        )
        now_ts = util.now()
        conn.execute("UPDATE node SET updated_at = ? WHERE id = ?", (now_ts, task_id))
        conn.execute(
            "INSERT INTO task_event (task_id, at, from_status, to_status, note, actor)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, now_ts, str(row["status"]), str(fields["status"]), SYNC_EVENT_NOTE, SYNC_EVENT_ACTOR),
        )
    return changed


def sync(
    conn: sqlite3.Connection,
    *,
    queue_path: Path,
    projects_path: Path,
    dry_run: bool = False,
) -> dict[str, object]:
    """v1 の Markdown へ manor を追いつかせる（`--sync`。ADR-003 §8-18〜20）。

    `run()` と違い、既存 id を無条件にスキップしない。v1 由来で manor 側が
    誰も触っていない行は、v1 の現在値で `_TASK_SYNC_FIELDS` を更新する。
    `--dry-run` は何も書かず、同じ集計を返す（`_Ctx.dry_run` と
    `_apply_task_sync`/`sync_project` の `dry_run` 引数が両方効く）。
    """
    today_date = _date.fromisoformat(util.today())
    qdoc = v1_queue.parse_queue(Path(queue_path), today_date)
    pdoc = v1_projects.parse_projects(Path(projects_path), today_date)

    # ① v1 にあって DB に無い行は通常どおり追加する（`run()` と全く同じ経路）。
    ctx = _Ctx(conn, dry_run=dry_run)
    _import_projects(ctx, pdoc)
    _import_milestones(ctx, pdoc)
    _import_relays(ctx, pdoc)
    _import_pending(ctx, qdoc)
    _import_running(ctx, qdoc)
    _import_decided(ctx, qdoc)
    _link_pending_deps(ctx, qdoc)
    _link_running_deps(ctx, qdoc)
    added = dict(ctx.counts)

    updated: list[dict[str, object]] = []
    skipped_local: list[str] = []
    unchanged = 0

    def sync_task(
        task_id: str, fields: dict[str, object], *, expected_decision: dict[str, object] | None
    ) -> None:
        nonlocal unchanged
        if not graph.node_exists(conn, task_id) or not _is_v1_origin(conn, task_id):
            return  # ①フェーズで（dry-run につき）まだ実在しない、または v1 由来でない
        if not _task_is_untouched(conn, task_id, expected_decision=expected_decision):
            skipped_local.append(task_id)
            return
        changed = _apply_task_sync(conn, task_id, fields, dry_run=dry_run)
        if changed:
            updated.append({"id": task_id, "fields": changed})
        else:
            unchanged += 1

    for item in qdoc.pending:
        qid = item.id.strip()
        if not qid:
            continue
        verdict, _status_plain = _a_verdict(item.status)
        expected_decision = {"status": verdict or "open", "ruling_present": bool(verdict)}
        sync_task(qid, _pending_sync_fields(item, ctx.exists), expected_decision=expected_decision)

    for item in qdoc.running:
        bid = item.id.strip()
        if bid:
            # B の行に decided_by が付くのは通常の運用には無い（手で `task link` した
            # 場合など）。何が「正しい」decision の状態かは v1 データから導けないので、
            # decided_by が付いていたら常に保守的に「触られた」扱いにする
            # （`_task_is_untouched` の `expected_decision=None` 分岐）。
            sync_task(bid, _running_sync_fields(item, ctx.exists), expected_decision=None)

    # ⑤ project も同じ規則。ただし project には task_event 相当の監査ログが無く
    # 「manor 側で触られたか」を判定する手段が無い（`project.set()` は誰が呼んだか
    # 記録しない）。status だけを対象に、v1 由来の project は無条件に v1 の値へ
    # 揃えている（曖昧だった点。報告に書く。ADR-003 §8-20）。
    for p in pdoc.projects:
        pid = p.id.strip()
        if not pid or not graph.node_exists(conn, pid) or not _is_v1_origin(conn, pid):
            continue
        expected_status = _project_status(p.status_plain)
        row = conn.execute("SELECT status FROM project WHERE id = ?", (pid,)).fetchone()
        if row is None:
            continue
        if str(row["status"]) == expected_status:
            unchanged += 1
            continue
        if not dry_run:
            conn.execute("UPDATE project SET status = ? WHERE id = ?", (expected_status, pid))
            conn.execute("UPDATE node SET updated_at = ? WHERE id = ?", (util.now(), pid))
        updated.append({"id": pid, "fields": ["status"]})

    return {
        "dry_run": dry_run,
        "added": added,
        "updated": sorted(updated, key=lambda u: str(u["id"])),
        "skipped_local": sorted(set(skipped_local)),
        "unchanged": unchanged,
        "unresolved": sorted(ctx.unresolved),
        "notes": ctx.notes,
        "errors": {"queue": list(qdoc.errors), "projects": list(pdoc.errors)},
    }


def _priority_from_rank(rank: int) -> int:
    """v1 の優先度（★の数。3 が最高）を manor の優先度（1 が最高）へ。

    2026-09-02 実データで判明: 生の ★ 数をそのまま入れていたため、★★★（最高）が manor では
    最低（3）扱いになっていた。3→1・2→2・1→3・0（無印）→4。
    """
    try:
        r = int(rank)
    except (TypeError, ValueError):
        return 4
    return max(1, 4 - r) if r > 0 else 4
