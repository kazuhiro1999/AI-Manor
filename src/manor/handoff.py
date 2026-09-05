"""委譲の型（ADR-001 §8）。指示書の生成と、報告の受け取り・裁定。

指示書は10個の `##` 見出しを必ず持つ。報告は5個の見出しを必須とし、
欠けていれば `manor handoff report` が拒否する（終了コード1）。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from . import graph, policy
from . import task as task_mod
from . import util
from .errors import ManorError

VALID_MODES: frozenset[str] = frozenset({"read", "write"})

CONSTRAINTS: tuple[str, ...] = (
    "課金しない",
    "外部送信しない",
    "home/ の②（主人の情報）を報告に写さない",
    "git を触らない",
    "常駐プロセスの権利: なし",
)

BOILERPLATE: tuple[str, ...] = (
    "迷ったら仮定を明記して進む",
    "失敗したら異なる方法を3つ試してから報告する",
    "定義で曖昧だった点・矛盾を指摘する",
)

#: 報告に必須の見出し（順序どおり）。`manor handoff report` が検査する。
REPORT_HEADINGS: tuple[str, ...] = (
    "やったこと",
    "証跡",
    "やっていないこと",
    "曖昧だった点",
    "主人にしか決められないこと",
)

_AXIS_KEYS = (
    "autonomy",
    "risk",
    "verification",
    "approval",
    "scope",
    "research_freedom",
    "escalation",
)


def _title(conn: sqlite3.Connection, node_id: str) -> str:
    row = conn.execute("SELECT title FROM node WHERE id = ?", (node_id,)).fetchone()
    return str(row["title"]) if row else "（不明）"


def _build_brief(
    handoff_id: int,
    task_id: str,
    title: str,
    *,
    to_agent: str,
    scope: str,
    mode: str,
    body: str,
    goal: str,
    now: str,
    next_: str,
    axes: dict[str, str],
    related: list[dict[str, object]],
    verify: str,
) -> str:
    lines = [f"# 委譲 H{handoff_id} — {task_id} {title}（→ {to_agent}）", ""]

    lines.append("## 役割とスコープ")
    lines.append(f"- 触ってよい範囲: {scope or '（指定なし。過度に広げないこと）'}")
    lines.append(f"- モード: {mode}（read=読むだけ / write=動かしてよい。既定 read）")
    lines.append("")

    lines.append("## 背景")
    lines.append(body.strip() or "（なし）")
    lines.append("")

    lines.append("## 目的 / 今の状態 / 次の一手")
    lines.append(f"- 目的: {goal or '（なし）'}")
    lines.append(f"- 今の状態: {now or '（なし）'}")
    lines.append(f"- 次の一手: {next_ or '（なし）'}")
    lines.append("")

    lines.append("## Project Policy")
    for key in _AXIS_KEYS:
        lines.append(f"- {key}: {axes.get(key, '—')}")
    lines.append("")

    lines.append("## 制約")
    for c in CONSTRAINTS:
        lines.append(f"- {c}")
    lines.append("")

    lines.append("## 関連（グラフから）")
    if related:
        for r in related:
            lines.append(f"- {r['id']}（{r['rel']}）{r['title']}")
    else:
        lines.append("（なし）")
    lines.append("")

    lines.append("## 成果物と置き場")
    lines.append("--scope の範囲内に置くこと。")
    lines.append(f"報告は `manor handoff report H{handoff_id} --file <report.md>` で提出する。")
    lines.append("")

    lines.append("## 検証要件")
    lines.append(verify.strip() or "（指定なし）")
    lines.append("根拠なき完了報告は無効。")
    lines.append("")

    lines.append("## 定型")
    for b in BOILERPLATE:
        lines.append(f"- {b}")
    lines.append("")

    lines.append("## 報告の型")
    lines.append("下の見出しで返すこと（空なら「なし」）:")
    for h in REPORT_HEADINGS:
        lines.append(f"- ## {h}")
    lines.append("")

    return "\n".join(lines)


def new(
    conn: sqlite3.Connection,
    home: Path,
    task_id: str,
    to_agent: str,
    *,
    scope: str = "",
    verify: str = "",
    mode: str = "read",
) -> dict[str, object]:
    if mode not in VALID_MODES:
        raise ManorError(
            f"語彙外の mode です: {mode!r}",
            code=2,
            key="error.handoff.mode_unknown",
            params={"mode": repr(mode)},
        )
    trow = conn.execute("SELECT * FROM task WHERE id = ?", (task_id,)).fetchone()
    if trow is None:
        raise ManorError(
            f"task が見つかりません: {task_id}",
            code=2,
            key="error.handoff.task_not_found",
            params={"task_id": task_id},
        )
    node = graph.require_node(conn, task_id)

    preset = "standard"
    if trow["project_id"]:
        prow = conn.execute(
            "SELECT preset FROM project WHERE id = ?", (trow["project_id"],)
        ).fetchone()
        if prow is not None:
            preset = str(prow["preset"])
    axes = policy.axes(preset)

    related: list[dict[str, object]] = []
    for rel in ("depends_on", "part_of", "relates_to"):
        for e in graph.edges_from(conn, task_id, rel):
            related.append({"id": e["dst"], "rel": rel, "title": _title(conn, str(e["dst"]))})

    now_ts = util.now()
    cur = conn.execute(
        "INSERT INTO handoff (task_id, agent, brief, created_at) VALUES (?, ?, '', ?)",
        (task_id, to_agent, now_ts),
    )
    handoff_id = int(cur.lastrowid or 0)

    brief = _build_brief(
        handoff_id,
        task_id,
        str(node["title"]),
        to_agent=to_agent,
        scope=scope,
        mode=mode,
        body=str(node["body"]),
        goal=str(trow["goal"]),
        now=str(trow["now"]),
        next_=str(trow["next"]),
        axes=axes,
        related=related,
        verify=verify,
    )
    conn.execute("UPDATE handoff SET brief = ? WHERE id = ?", (brief, handoff_id))

    task_mod.status(conn, task_id, "doing", owner=to_agent, actor="butler", note=f"{to_agent} へ委譲")

    home = Path(home)
    handoffs_dir = home / "handoffs"
    handoffs_dir.mkdir(parents=True, exist_ok=True)
    file_path = handoffs_dir / f"H{handoff_id}_{task_id}_{to_agent}.md"
    # `write_text` は Windows で改行を \r\n に変換する。DB に保存した brief（\n のまま）と
    # ファイル実体をバイト単位で一致させるため、バイト列を直接書く（render.py と同じ理由）。
    file_path.write_bytes(brief.encode("utf-8"))

    return {
        "id": handoff_id,
        "task_id": task_id,
        "agent": to_agent,
        "path": str(file_path),
        "brief": brief,
    }


def missing_report_headings(report_text: str) -> list[str]:
    """報告に足りない見出しの一覧（空なら全部ある）。"""
    missing = []
    for heading in REPORT_HEADINGS:
        pattern = re.compile(rf"^##\s+{re.escape(heading)}\s*$", re.MULTILINE)
        if not pattern.search(report_text):
            missing.append(heading)
    return missing


def _get_handoff(conn: sqlite3.Connection, handoff_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM handoff WHERE id = ?", (handoff_id,)).fetchone()
    if row is None:
        raise ManorError(
            f"handoff が見つかりません: H{handoff_id}",
            code=2,
            key="error.handoff.not_found",
            params={"handoff_id": handoff_id},
        )
    return row


def report(conn: sqlite3.Connection, handoff_id: int, report_text: str) -> dict[str, object]:
    """報告を受け取る。5見出しが欠けていれば拒否する（列挙して `ManorError`）。"""
    _get_handoff(conn, handoff_id)
    missing = missing_report_headings(report_text)
    if missing:
        joined = " / ".join(f"## {m}" for m in missing)
        raise ManorError(
            f"報告に必須の見出しが欠けています: {joined}",
            key="error.handoff.report_missing_headings",
            params={"joined": joined},
        )
    conn.execute(
        "UPDATE handoff SET report = ?, reported_at = ? WHERE id = ?",
        (report_text, util.now(), handoff_id),
    )
    return {"id": handoff_id, "reported_at": util.now()}


def accept(conn: sqlite3.Connection, handoff_id: int, *, note: str = "") -> dict[str, object]:
    row = _get_handoff(conn, handoff_id)
    now_ts = util.now()
    conn.execute(
        "UPDATE handoff SET verdict = 'accepted', verdict_note = ?, verdict_at = ? WHERE id = ?",
        (note, now_ts, handoff_id),
    )
    # ADR-006 D6: handoff の裁定（accept/reject）も task_event.authorized_by に
    # handoff id（`H<n>`）を残す（decision の id と同じ列を共有する。逆引きは
    # decision.show 側のみ——handoff 側の逆引きは handoff.report/verdict 自身が持つ）。
    task_mod.status(
        conn, str(row["task_id"]), "doing", owner="butler", actor="butler", note="",
        authorized_by=f"H{handoff_id}",
    )
    return {"id": handoff_id, "verdict": "accepted", "task_id": row["task_id"]}


def reject(conn: sqlite3.Connection, handoff_id: int, *, note: str) -> dict[str, object]:
    if not note.strip():
        raise ManorError(
            "reject には --note（理由）が必須です",
            key="error.handoff.reject_needs_note",
        )
    row = _get_handoff(conn, handoff_id)
    now_ts = util.now()
    conn.execute(
        "UPDATE handoff SET verdict = 'rejected', verdict_note = ?, verdict_at = ? WHERE id = ?",
        (note, now_ts, handoff_id),
    )
    # ADR-001 §13 裁定5: 据え置くと「委譲中」が射影に残り続ける。
    # owner を執事へ戻し、hold に置いて理由を残す。次に誰へ渡すかは執事が決める。
    task_mod.status(
        conn, str(row["task_id"]), "hold", owner="butler", actor="butler", note=f"却下: {note}",
        authorized_by=f"H{handoff_id}",
    )
    return {"id": handoff_id, "verdict": "rejected", "task_id": row["task_id"]}


def list_handoffs(conn: sqlite3.Connection, *, open_only: bool = False) -> list[dict[str, object]]:
    sql = "SELECT * FROM handoff WHERE 1=1"
    if open_only:
        sql += " AND verdict IS NULL"
    sql += " ORDER BY id"
    return [dict(r) for r in conn.execute(sql).fetchall()]
