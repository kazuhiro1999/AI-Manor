"""task の API と状態機械（ADR-001 §4）。

状態機械はここで守る。CLI からも試験からも同じ関数を呼ぶので、
「機構が規則を守る」の中心はこのモジュールにある。
"""

from __future__ import annotations

import re
import sqlite3

from . import graph, i18n, policy, project as project_mod, task_kind as task_kind_mod, util
from .errors import ManorError
from .ids import next_id

#: ADR-006 D21 補足: handoff id（`task_event.authorized_by` に入る `H<n>` の形）。
#: done への遷移では受け付けない（下記 `status()` 参照）。
_HANDOFF_ID_RE = re.compile(r"^H\d+$")

VALID_STATUSES: frozenset[str] = frozenset(
    {"todo", "doing", "waiting", "hold", "resident", "done", "withdrawn"}
)
VALID_LEVELS: frozenset[str] = frozenset({"L0", "L1", "L2", "L3", "HG"})
VALID_SECTIONS: frozenset[str] = frozenset({"A", "B"})
VALID_RISK: frozenset[str] = frozenset({"", "low", "medium", "high"})

#: 許される遷移（ADR-001 §4 の図を読み下したもの）。
#: waiting/hold は「止まっている」クラスタとして同居し、互いに行き来できる。
#: resident は withdrawn にしかなれない。done/withdrawn は終端。
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "todo": frozenset({"doing", "waiting", "hold", "resident", "withdrawn"}),
    "doing": frozenset({"done", "waiting", "hold", "withdrawn"}),
    "waiting": frozenset({"doing", "todo", "hold", "withdrawn"}),
    "hold": frozenset({"doing", "todo", "waiting", "withdrawn"}),
    "resident": frozenset({"withdrawn"}),
    "done": frozenset(),
    "withdrawn": frozenset(),
}

#: 入るときに --note が必須な状態。
NOTE_REQUIRED: frozenset[str] = frozenset({"waiting", "withdrawn"})


def _row(conn: sqlite3.Connection, task_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM task WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise ManorError(
            f"task が見つかりません: {task_id}",
            code=2,
            key="error.task.not_found",
            params={"task_id": task_id},
        )
    return row


def _ensure_agent_node(conn: sqlite3.Connection, name: str) -> str:
    node_id = f"A:{name}"
    if not graph.node_exists(conn, node_id):
        graph.create_node(conn, kind="agent", title=name, node_id=node_id)
    return node_id


def _check_section_a_invariant(row: sqlite3.Row) -> None:
    if row["section"] == "A" and not str(row["recommendation"] or "").strip():
        raise ManorError(
            f"{row['id']} は section=A です。recommendation が必要です",
            key="error.task.section_a_needs_recommendation",
            params={"id": row["id"]},
        )


def _check_kind(conn: sqlite3.Connection, kind: str) -> None:
    """`kind`（ADR-010 D2「タスクの種類」）を検算する。**空文字はここに来る前に通す**——
    `add`/`set` の呼び出し側が先に `strip()` して空なら呼ばない（D2「種類は必須ではない」）。
    語彙外（`task_kind` に無い、またはアーカイブ済み）は拒む。
    """
    if not task_kind_mod.is_valid(conn, kind):
        raise ManorError(
            f"語彙外の kind です: {kind!r}（`manor kind list` で確認、または `manor kind add` で追加）",
            code=2,
            key="error.task.kind_unknown",
            params={"kind": repr(kind)},
        )


def add(
    conn: sqlite3.Connection,
    title: str,
    *,
    project: str | None = None,
    level: str | None = None,
    cls: str | None = None,
    section: str = "B",
    goal: str = "",
    now: str = "",
    next_: str = "",
    due: str | None = None,
    body: str = "",
    depends_on: list[str] | None = None,
    part_of: str | None = None,
    owner: str = "butler",
    recommendation: str = "",
    risk: str = "",
    kind: str = "",
) -> str:
    """タスクを1件作る。既定の状態は `todo`。

    `level` を明示しなければ `cls`（policy class）から解決する。
    どちらも無ければ `L2`。`section='A'` なら `recommendation` が必須。

    `kind`（ADR-010 D2「タスクの種類」）は `level` とは無関係の、人向けの分類の札。
    **必須ではない**——空文字（既定）ならそのまま通す。非空なら `task_kind` の語彙に
    あることを確かめる（無ければ `ManorError(code=2)`）。
    """
    if section not in VALID_SECTIONS:
        raise ManorError(
            f"語彙外の section です: {section!r}",
            code=2,
            key="error.task.section_unknown",
            params={"section": repr(section)},
        )
    if risk not in VALID_RISK:
        raise ManorError(
            f"語彙外の risk です: {risk!r}",
            code=2,
            key="error.task.risk_unknown",
            params={"risk": repr(risk)},
        )
    kind = (kind or "").strip()
    if kind:
        _check_kind(conn, kind)

    project_id: str | None = None
    preset = "standard"
    if project:
        prow = project_mod.resolve(conn, project)
        project_id = str(prow["id"])
        preset = str(prow["preset"])

    # 振る舞い試験 S6（2026-09-02）: 執事が `--level L1 --class human_gate`（存在しないクラス）や
    # `--level L3` で外部送信のタスクを起票した。**クラスが level の出どころ**であり、執事が level を
    # 自分で決めるものではない。クラスが渡されたら level はクラスから決め（明示の level は無視）、
    # 存在しないクラスは拒否する。
    if cls:
        if cls not in policy.classes():
            known = " / ".join(sorted(policy.classes()))
            raise ManorError(
                f"語彙外の class です: {cls!r}（使えるのは {known}）",
                code=2,
                key="error.task.class_unknown",
                params={"cls": repr(cls), "known": known},
            )
        level = policy.resolve(cls, preset)
    if level is None:
        level = "L2"
    if level not in VALID_LEVELS:
        raise ManorError(
            f"語彙外の level です: {level!r}",
            code=2,
            key="error.task.level_unknown",
            params={"level": repr(level)},
        )

    if section == "A" and not recommendation.strip():
        raise ManorError(
            "section=A には --recommendation が必要です",
            key="error.task.add_section_a_needs_recommendation",
        )
    # 振る舞い試験 S6（2026-09-02）: HG のタスクを起票しても decision を積まず、チャットで
    # 確認を求めて終わった。「積んで進む」を執事の記憶に頼らず、起票の時点で機械が積む。
    # そのために推奨（無回答時の既定案）を必須にする——推奨の無い判断待ちは主人を止めるだけ。
    if level == "HG" and not recommendation.strip():
        raise ManorError(
            "level=HG のタスクは --recommendation（無回答時の既定案）が必須です。"
            "起票と同時に decision を積み、section A（主人待ち）へ入ります",
            key="error.task.hg_needs_recommendation",
        )

    task_id = next_id(conn, "T")
    now_ts = util.now()
    conn.execute(
        "INSERT INTO node (id, kind, title, body, created_at, updated_at) VALUES (?, 'task', ?, ?, ?, ?)",
        (task_id, title, body, now_ts, now_ts),
    )
    conn.execute(
        "INSERT INTO task (id, project_id, status, status_note, owner, level, section,"
        " goal, now, next, recommendation, risk, due, kind)"
        " VALUES (?, ?, 'todo', '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (task_id, project_id, owner, level, section, goal, now, next_, recommendation, risk, due, kind),
    )
    conn.execute(
        "INSERT INTO task_event (task_id, at, from_status, to_status, note, actor)"
        " VALUES (?, ?, NULL, 'todo', '', ?)",
        (task_id, now_ts, owner or "butler"),
    )
    if part_of:
        graph.link(conn, task_id, "part_of", part_of)
    for dep in depends_on or []:
        link_dependency(conn, task_id, dep, actor=owner or "butler")
    if level == "HG":
        from . import decision as decision_mod  # 循環 import を避けるためここで

        decision_mod.ask(
            conn,
            title,
            task_id=task_id,
            recommend=recommendation,
            background=body or goal,
            risk=risk,
        )
    return task_id


def link_dependency(
    conn: sqlite3.Connection,
    src: str,
    dst: str,
    *,
    note: str = "",
    auto_wait: bool = True,
    actor: str = "butler",
) -> dict[str, object]:
    """`src --depends_on--> dst` を張る。**相手が未完了なら src は自動で `waiting` になる。**

    振る舞い試験 S4（2026-09-02）: 「T2 は T3 が終わるまで待ち」に対し、執事は辺を張ったが
    `waiting` にしなかった。依存を張ることと待つことは同じ意味なので、機械が揃える。
    src が `todo` のときだけ動かす（`doing` の途中で依存を足したときは進め方の判断が要るので触らない）。
    """
    graph.link(conn, src, "depends_on", dst, note=note)
    result: dict[str, object] = {"src": src, "rel": "depends_on", "dst": dst, "auto_wait": False}
    if not auto_wait:
        return result
    srow = conn.execute("SELECT status FROM task WHERE id = ?", (src,)).fetchone()
    drow = conn.execute("SELECT status FROM task WHERE id = ?", (dst,)).fetchone()
    if srow is None or drow is None:
        return result
    if str(srow["status"]) == "todo" and str(drow["status"]) not in ("done", "withdrawn"):
        status(conn, src, "waiting", note=f"{dst} の後に", actor=actor)
        result["auto_wait"] = True
    return result


def set(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    goal: str | None = None,
    now: str | None = None,
    next_: str | None = None,
    due: str | None = None,
    owner: str | None = None,
    level: str | None = None,
    title: str | None = None,
    body: str | None = None,
    recommendation: str | None = None,
    risk: str | None = None,
    kind: str | None = None,
) -> str:
    row = _row(conn, task_id)
    if level is not None and level not in VALID_LEVELS:
        raise ManorError(
            f"語彙外の level です: {level!r}",
            code=2,
            key="error.task.level_unknown",
            params={"level": repr(level)},
        )
    if risk is not None and risk not in VALID_RISK:
        raise ManorError(
            f"語彙外の risk です: {risk!r}",
            code=2,
            key="error.task.risk_unknown",
            params={"risk": repr(risk)},
        )
    if kind is not None:
        kind = kind.strip()
        if kind:
            _check_kind(conn, kind)

    fields: dict[str, object] = {}
    if goal is not None:
        fields["goal"] = goal
    if now is not None:
        fields["now"] = now
    if next_ is not None:
        fields["next"] = next_
    if due is not None:
        fields["due"] = due
    if owner is not None:
        fields["owner"] = owner
    if level is not None:
        fields["level"] = level
    if recommendation is not None:
        fields["recommendation"] = recommendation
    if risk is not None:
        fields["risk"] = risk
    if kind is not None:
        fields["kind"] = kind
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE task SET {sets} WHERE id = ?", (*fields.values(), task_id))

    if title is not None or body is not None:
        node_sets = []
        params: list[object] = []
        if title is not None:
            node_sets.append("title = ?")
            params.append(title)
        if body is not None:
            node_sets.append("body = ?")
            params.append(body)
        node_sets.append("updated_at = ?")
        params.append(util.now())
        params.append(task_id)
        conn.execute(f"UPDATE node SET {', '.join(node_sets)} WHERE id = ?", params)
    else:
        graph.touch_node(conn, task_id)

    _check_section_a_invariant(_row(conn, task_id))
    return task_id


def status(
    conn: sqlite3.Connection,
    task_id: str,
    new_status: str,
    *,
    note: str = "",
    owner: str | None = None,
    actor: str = "butler",
    authorized_by: str | None = None,
) -> dict[str, object]:
    """状態を変える。**すべての遷移で `task_event` に1行残す**（同じ状態への更新も）。

    戻り値には `warnings`（拒否はしないが伝えるべきこと）が入る。

    `authorized_by`（ADR-006 D6）: この遷移を通した decision の id（`D3` 等）か
    handoff の id（`H7` 等）。**明示しなくても**、`level=HG` の task を HG の門を通して
    `done` にするときは、通した decision の id を機械が自動で入れる（呼び出し側が
    渡した値があればそちらを優先する）。

    ADR-006 D21 補足（3周目レビュー）: **`done` への遷移の `authorized_by` は decision の
    id に限る。** `H<n>`（handoff の裁定）は doing/hold 等の遷移にのみ許す——委譲の裁定は
    「作業を任せる/戻す」を通すもので、「完了」を認めるものではない。渡された値が
    `H<n>` の形なら拒否し、それ以外でも `decision` 表に実在しなければ拒否する
    （存在しない id を最初から入れさせない。C12 が事後に検算する内容を入り口でも守る）。
    """
    if new_status not in VALID_STATUSES:
        raise ManorError(
            f"語彙外の状態です: {new_status!r}",
            code=2,
            key="error.task.status_unknown",
            params={"status": repr(new_status)},
        )
    row = _row(conn, task_id)
    # 空文字・空白だけの authorized_by は「未指定」と同じ扱い（HG の自動補完をすり抜けて
    # 空文字が task_event に残る余地を塞ぐ。3周目レビューの指摘）
    authorized_by = (authorized_by or "").strip() or None
    current = str(row["status"])

    if new_status != current:
        allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
        if new_status not in allowed:
            raise ManorError(
                f"状態機械: {current} から {new_status} へは遷移できません",
                key="error.task.transition_denied",
                params={"current": current, "new_status": new_status},
            )

    if new_status in NOTE_REQUIRED and not note.strip():
        # message_ja は常に日本語（web の detail が使うため。errors.py の docstring 参照）。
        # CLI 表示用の params にだけ、現在の言語で解決したラベルを渡す
        # （current_language() はプロセス内で不変なので、ここで解決しても
        # localized_message() が後で解決するのと結果は変わらない）。
        label_ja = "何を待つか" if new_status == "waiting" else "理由"
        label_key = "error.task.note_label_waiting" if new_status == "waiting" else "error.task.note_label_reason"
        raise ManorError(
            f"{new_status} へは --note（{label_ja}）が必須です",
            key="error.task.note_required",
            params={"new_status": new_status, "label": i18n.t(label_key)},
        )

    warnings: list[str] = []
    if new_status == "done":
        if str(row["level"]) == "HG":
            ok = conn.execute(
                "SELECT d.id AS decision_id FROM edge e JOIN decision d ON d.id = e.dst"
                " WHERE e.src = ? AND e.rel = 'decided_by' AND d.status IN ('approved','modified')"
                " ORDER BY d.id",
                (task_id,),
            ).fetchone()
            if ok is None:
                raise ManorError(
                    f"{task_id} は level=HG です。承認/修正済みの decision（decided_by）が無いと"
                    " done にできません",
                    key="error.task.hg_done_requires_decision",
                    params={"task_id": task_id},
                )
            if authorized_by is None:
                authorized_by = str(ok["decision_id"])
        if authorized_by is not None and str(authorized_by).strip():
            value = str(authorized_by).strip()
            if _HANDOFF_ID_RE.match(value):
                raise ManorError(
                    "done の authorized_by は decision の id に限ります"
                    "（H<n> は委譲の裁定なので done を認めません）",
                    code=2,
                    key="error.task.done_authorized_by_must_be_decision",
                )
            dec = conn.execute("SELECT 1 FROM decision WHERE id = ?", (value,)).fetchone()
            if dec is None:
                raise ManorError(
                    f"authorized_by が指す decision が見つかりません: {value}",
                    code=2,
                    key="error.task.authorized_by_decision_not_found",
                    params={"value": value},
                )
        unresolved = conn.execute(
            "SELECT e.dst FROM edge e JOIN task d ON d.id = e.dst"
            " WHERE e.src = ? AND e.rel = 'depends_on' AND d.status NOT IN ('done','withdrawn')"
            " ORDER BY e.dst",
            (task_id,),
        ).fetchall()
        if unresolved:
            ids = ", ".join(str(r["dst"]) for r in unresolved)
            warnings.append(i18n.t("task.done.warning_unresolved_deps", ids=ids))

    _check_section_a_invariant(row)

    new_owner = str(row["owner"])
    if new_status == "doing":
        new_owner = owner or new_owner or "butler"
    elif owner is not None:
        new_owner = owner

    now_ts = util.now()
    done_at = now_ts if new_status == "done" else row["done_at"]
    conn.execute(
        "UPDATE task SET status = ?, status_note = ?, owner = ?, done_at = ? WHERE id = ?",
        (new_status, note, new_owner, done_at, task_id),
    )
    conn.execute("UPDATE node SET updated_at = ? WHERE id = ?", (now_ts, task_id))
    conn.execute(
        "INSERT INTO task_event (task_id, at, from_status, to_status, note, actor, authorized_by)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (task_id, now_ts, current, new_status, note, actor, authorized_by),
    )
    if new_status == "doing" and new_owner not in ("butler", "master"):
        _ensure_agent_node(conn, new_owner)
        graph.link(conn, task_id, "delegated_to", f"A:{new_owner}")

    return {
        "id": task_id,
        "status": new_status,
        "owner": new_owner,
        "warnings": warnings,
        "authorized_by": authorized_by,
    }


def done(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    note: str = "",
    actor: str = "butler",
    authorized_by: str | None = None,
) -> dict[str, object]:
    """`status done` の糖衣。"""
    return status(conn, task_id, "done", note=note, actor=actor, authorized_by=authorized_by)


def show(conn: sqlite3.Connection, task_id: str) -> dict[str, object]:
    row = _row(conn, task_id)
    node = graph.get_node(conn, task_id)
    out = dict(row)
    out["title"] = node["title"] if node else ""
    out["body"] = node["body"] if node else ""
    out["created_at"] = node["created_at"] if node else ""
    out["updated_at"] = node["updated_at"] if node else ""
    out["events"] = [
        dict(r)
        for r in conn.execute(
            "SELECT at, from_status, to_status, note, actor, authorized_by FROM task_event"
            " WHERE task_id = ? ORDER BY id",
            (task_id,),
        ).fetchall()
    ]
    out["edges_out"] = [dict(r) for r in graph.edges_from(conn, task_id)]
    out["edges_in"] = [dict(r) for r in graph.edges_to(conn, task_id)]
    return out


def list_tasks(
    conn: sqlite3.Connection,
    *,
    status_filter: str | None = None,
    section: str | None = None,
    project: str | None = None,
    owner: str | None = None,
    include_settled: bool = False,
) -> list[dict[str, object]]:
    sql = "SELECT t.*, n.title AS title FROM task t JOIN node n ON n.id = t.id WHERE 1=1"
    params: list[object] = []
    if status_filter:
        sql += " AND t.status = ?"
        params.append(status_filter)
    elif not include_settled:
        sql += " AND t.status NOT IN ('done','withdrawn')"
    if section:
        sql += " AND t.section = ?"
        params.append(section)
    if project:
        prow = project_mod.resolve(conn, project)
        sql += " AND t.project_id = ?"
        params.append(str(prow["id"]))
    if owner:
        sql += " AND t.owner = ?"
        params.append(owner)
    sql += " ORDER BY CAST(substr(t.id, 2) AS INTEGER)"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def dup(conn: sqlite3.Connection, src: str, dst: str) -> dict[str, object]:
    """`duplicates` を張り、`src` を withdrawn にする（1コマンド。ADR-001 §5）。"""
    _row(conn, src)
    _row(conn, dst)
    graph.link(conn, src, "duplicates", dst)
    return status(conn, src, "withdrawn", note=f"{dst} と重複のため")
