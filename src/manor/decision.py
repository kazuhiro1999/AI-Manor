"""decision の API（ADR-001 §5）。

`ask` は task を section A に上げ、`decided_by` で結ぶ。
task の `recommendation` 列は decision と同じ「無回答時の既定案」を指すので、
`--recommend` の値をそのまま task 側にも複製する（ADR は CLI に `task add --recommendation`
を明示していないが、C4 の検査対象である以上どこかで埋める経路が要る。曖昧だった点として報告する）。
`rule` は裁定を記録し、対象タスクを section A → B に戻す。
"""

from __future__ import annotations

import sqlite3

from . import graph, util
from .errors import ManorError

VALID_VERDICTS: frozenset[str] = frozenset({"approved", "rejected", "modified"})
VALID_RISK: frozenset[str] = frozenset({"", "low", "medium", "high"})


def ask(
    conn: sqlite3.Connection,
    title: str,
    *,
    task_id: str,
    recommend: str,
    background: str,
    risk: str = "",
    evidence: str = "",
) -> str:
    """decision を open で作る。`evidence`（ADR-006 D5）: 何を見て推奨したか
    （ファイル・数字・出典を `- ` 箇条書きで）。**空でも通す**——`manor check` の C11 が
    「evidence の無い open decision」を数える（警告。終了コードは変えない）。
    """
    if risk not in VALID_RISK:
        raise ManorError(
            f"語彙外の risk です: {risk!r}",
            code=2,
            key="error.decision.risk_unknown",
            params={"risk": repr(risk)},
        )
    trow = conn.execute("SELECT * FROM task WHERE id = ?", (task_id,)).fetchone()
    if trow is None:
        raise ManorError(
            f"task が見つかりません: {task_id}",
            code=2,
            key="error.decision.task_not_found",
            params={"task_id": task_id},
        )

    decision_id = graph.create_node(conn, kind="decision", title=title, body=background, id_prefix="D")
    now_ts = util.now()
    conn.execute(
        "INSERT INTO decision (id, status, recommendation, background, risk, evidence, asked_at)"
        " VALUES (?, 'open', ?, ?, ?, ?, ?)",
        (decision_id, recommend, background, risk, evidence, now_ts),
    )
    graph.link(conn, task_id, "decided_by", decision_id)
    conn.execute(
        "UPDATE task SET section = 'A', recommendation = ?, risk = COALESCE(NULLIF(?, ''), risk)"
        " WHERE id = ?",
        (recommend, risk, task_id),
    )
    graph.touch_node(conn, task_id)
    return decision_id


def rule(
    conn: sqlite3.Connection, decision_id: str, verdict: str, *, ruling: str,
    actor: str = "butler",
) -> dict[str, object]:
    """裁定を下す。

    `actor`（S12・2026-09-06）: **誰が裁定したか。** `task.status()` と同じ語・同じ既定
    （`"butler"`）。Web の口は `"web"`、CLI は `"cli"` を明示的に渡す。

    なぜ要るか: 一言なしの承認・却下は `ruling` に既定の「承認」「却下」が入るので、
    **主人がアプリで押したものと、執事が CLI から書いたものが台帳上で同一**になっていた。
    D6（`external_send` という fixed=true への例外を①層へ書き込む裁定）でそれが起き、
    検分側が「誰の承認か判定できない」と指摘した——**いちばん記録が要るところで、
    いちばん薄かった。**
    """
    if verdict not in VALID_VERDICTS:
        raise ManorError(
            f"語彙外の裁定です: {verdict!r}（approved/rejected/modified）",
            code=2,
            key="error.decision.verdict_unknown",
            params={"verdict": repr(verdict)},
        )
    # 主人の指摘（2026-09-02）: 「入力せずに却下できないのは仕様か」→ 仕様を変えた。
    # 裁定は「OK／こう直して／却下」の一言でよい（v1 QUEUE.md の約束）。承認・却下は文が無くてよく
    # 既定の一言を入れる。**修正だけは指示文が要る**（何をどう直すかが無いと執事が動けない）。
    if not ruling.strip():
        if verdict == "modified":
            raise ManorError(
                "修正の裁定には --ruling（どう直すか）が必須です",
                key="error.decision.modified_needs_ruling",
            )
        # ruling は台帳に永続する記録そのもの（主人が入れたデータと同じ扱い）——
        # CLI の表示言語に関わらず常に日本語で残す（D12 の趣旨に合わせた判断。詳細は報告）。
        ruling = {"approved": "承認", "rejected": "却下"}[verdict]
    row = conn.execute("SELECT * FROM decision WHERE id = ?", (decision_id,)).fetchone()
    if row is None:
        raise ManorError(
            f"decision が見つかりません: {decision_id}",
            code=2,
            key="error.decision.not_found",
            params={"decision_id": decision_id},
        )

    now_ts = util.now()
    conn.execute(
        "UPDATE decision SET status = ?, ruling = ?, decided_at = ?, actor = ? WHERE id = ?",
        (verdict, ruling, now_ts, actor, decision_id),
    )
    graph.touch_node(conn, decision_id)

    moved: list[str] = []
    for e in graph.edges_to(conn, decision_id, rel="decided_by"):
        tid = str(e["src"])
        trow = conn.execute("SELECT section FROM task WHERE id = ?", (tid,)).fetchone()
        if trow is not None and str(trow["section"]) == "A":
            conn.execute("UPDATE task SET section = 'B' WHERE id = ?", (tid,))
            graph.touch_node(conn, tid)
            moved.append(tid)
    # `actor` も返す——Web の口はこれをそのまま返すので、押した側が「何が記録されたか」を
    # 確かめられる（画面の「判断待ち」は open だけを出すので、裁定後はここでしか見えない）。
    return {"id": decision_id, "status": verdict, "moved_to_b": moved, "actor": actor}



def set_fields(
    conn: sqlite3.Connection,
    decision_id: str,
    *,
    title: str | None = None,
    recommend: str | None = None,
    background: str | None = None,
    risk: str | None = None,
    evidence: str | None = None,
) -> dict[str, object]:
    """裁定を**下す前**に、推奨・背景・根拠・題名を書き直す（`manor decision set`）。

    ⚠ 2026-09-08 に新設。それまで書き直す道が無く、**起票のときに書いた長文が主人の画面に
    そのまま残り続けていました**（主人のご指摘「この長い文章は読みたくありません」）。
    `manor task set --status-note` を足したときと同じ形——**直せない欄は、間違ったまま
    主人が読む欄になります。**

    裁定済みのものは触りません（履歴を書き換えないため。直したいなら新しい decision を
    起こす）。`recommend` を書き換えたときは、`ask` と同じく task 側の `recommendation`
    にも写します——**同じ値が2箇所にあるので、片方だけ直すと板と画面で食い違います。**
    """
    row = conn.execute("SELECT * FROM decision WHERE id = ?", (decision_id,)).fetchone()
    if row is None:
        raise ManorError(
            f"decision が見つかりません: {decision_id}",
            code=2,
            key="error.decision.not_found",
            params={"decision_id": decision_id},
        )
    if str(row["status"]) != "open":
        raise ManorError(
            f"裁定済みの decision は書き直せません: {decision_id}（{row['status']}）",
            code=2,
            key="error.decision.already_ruled",
            params={"decision_id": decision_id, "status": str(row["status"])},
        )
    if risk is not None and risk not in VALID_RISK:
        raise ManorError(
            f"語彙外の risk です: {risk!r}",
            code=2,
            key="error.decision.risk_unknown",
            params={"risk": repr(risk)},
        )

    fields: dict[str, object] = {}
    for name, value in (
        ("recommendation", recommend),
        ("background", background),
        ("risk", risk),
        ("evidence", evidence),
    ):
        if value is not None:
            fields[name] = value
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE decision SET {sets} WHERE id = ?", (*fields.values(), decision_id))
    if title is not None:
        conn.execute("UPDATE node SET title = ? WHERE id = ?", (title, decision_id))
    if background is not None:
        # `ask` は background を node.body にも書いている。片方だけ直すと食い違う
        conn.execute("UPDATE node SET body = ? WHERE id = ?", (background, decision_id))
    if recommend is not None:
        for r in conn.execute(
            "SELECT src FROM edge WHERE dst = ? AND rel = 'decided_by'", (decision_id,)
        ).fetchall():
            conn.execute("UPDATE task SET recommendation = ? WHERE id = ?", (recommend, str(r["src"])))
    graph.touch_node(conn, decision_id)
    return {"id": decision_id, "changed": sorted([*fields, *(["title"] if title is not None else [])])}


def list_decisions(conn: sqlite3.Connection, *, open_only: bool = False) -> list[dict[str, object]]:
    sql = "SELECT d.*, n.title AS title FROM decision d JOIN node n ON n.id = d.id WHERE 1=1"
    if open_only:
        sql += " AND d.status = 'open'"
    sql += " ORDER BY CAST(substr(d.id, 2) AS INTEGER)"
    return [dict(r) for r in conn.execute(sql).fetchall()]


def show(conn: sqlite3.Connection, decision_id: str) -> dict[str, object]:
    """`manor decision show <id>`（ADR-006 D7）。この裁定が結ばれた task と、
    「この裁定に基づいて起きたこと」（`task_event WHERE authorized_by = <id>`）の逆引きを持つ。
    """
    row = conn.execute("SELECT * FROM decision WHERE id = ?", (decision_id,)).fetchone()
    if row is None:
        raise ManorError(
            f"decision が見つかりません: {decision_id}",
            code=2,
            key="error.decision.not_found",
            params={"decision_id": decision_id},
        )
    node = conn.execute("SELECT title FROM node WHERE id = ?", (decision_id,)).fetchone()

    out = dict(row)
    out["title"] = str(node["title"]) if node is not None else ""
    out["tasks"] = [
        str(r["src"])
        for r in conn.execute(
            "SELECT src FROM edge WHERE dst = ? AND rel = 'decided_by' ORDER BY src", (decision_id,)
        ).fetchall()
    ]
    out["authorized_events"] = [
        dict(r)
        for r in conn.execute(
            "SELECT id, task_id, at, from_status, to_status, note, actor FROM task_event"
            " WHERE authorized_by = ? ORDER BY id",
            (decision_id,),
        ).fetchall()
    ]
    return out
