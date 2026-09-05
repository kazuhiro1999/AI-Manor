"""文脈パック（ADR-001 §10）。`manor ctx <id>` が呼ぶ。

「全文を読ませない」が目的。本体と1ホップは常に載せ、budget を超えたときだけ
relates_to → 2ホップ（depends_on の連鎖）→ 直近イベント の順に落とす。
"""

from __future__ import annotations

import sqlite3

from . import graph, util

#: 1ホップで見る辺の種類。「関連（グラフから）」の全部。
ONEHOP_RELS: tuple[str, ...] = (
    "depends_on",
    "part_of",
    "decided_by",
    "delegated_to",
    "duplicates",
    "supersedes",
    "relates_to",
)

_REL_LABEL = {
    "depends_on": "依存（depends_on。相手が片付くまで進めない）",
    "part_of": "part_of（自分はこれの一部）",
    "decided_by": "decided_by（この判断待ち）",
    "delegated_to": "delegated_to（委譲先）",
    "duplicates": "duplicates（重複）",
    "supersedes": "supersedes（これを置き換えた）",
    "relates_to": "relates_to（弱い関連）",
}


def _title(conn: sqlite3.Connection, node_id: str) -> str:
    row = conn.execute("SELECT title FROM node WHERE id = ?", (node_id,)).fetchone()
    return str(row["title"]) if row else "（不明）"


def _task_status(conn: sqlite3.Connection, task_id: str) -> str | None:
    row = conn.execute("SELECT status FROM task WHERE id = ?", (task_id,)).fetchone()
    return str(row["status"]) if row else None


def _edge_entry(conn: sqlite3.Connection, rel: str, e: sqlite3.Row) -> dict[str, object]:
    dst = str(e["dst"])
    entry: dict[str, object] = {"id": dst, "title": _title(conn, dst), "note": e["note"]}
    if rel == "depends_on":
        entry["status"] = _task_status(conn, dst)
    elif rel == "decided_by":
        drow = conn.execute("SELECT status, ruling FROM decision WHERE id = ?", (dst,)).fetchone()
        if drow is not None:
            entry["status"] = drow["status"]
            entry["ruling"] = drow["ruling"]
    elif rel == "delegated_to":
        # dst は 'A:<name>' というノード id。handoff.task_id は起点（src）で引く。
        pass
    return entry


def build(conn: sqlite3.Connection, node_id: str, *, depth: int = 2, budget: int = 2000) -> dict[str, object]:
    """1件について、いま判断するのに要る文脈だけを組み立てる。"""
    node = graph.require_node(conn, node_id)
    kind = str(node["kind"])
    core: dict[str, object] = {
        "id": node_id,
        "kind": kind,
        "title": node["title"],
        "body": node["body"],
    }
    if kind == "task":
        trow = conn.execute("SELECT * FROM task WHERE id = ?", (node_id,)).fetchone()
        if trow is not None:
            core.update(
                {
                    "status": trow["status"],
                    "owner": trow["owner"],
                    "level": trow["level"],
                    "due": trow["due"],
                    "goal": trow["goal"],
                    "now": trow["now"],
                    "next": trow["next"],
                }
            )

    onehop: dict[str, list[dict[str, object]]] = {rel: [] for rel in ONEHOP_RELS}
    for rel in ONEHOP_RELS:
        for e in graph.edges_from(conn, node_id, rel):
            onehop[rel].append(_edge_entry(conn, rel, e))
        if rel == "delegated_to":
            for entry in onehop[rel]:
                agent_name = str(entry["id"]).split(":", 1)[-1]
                hrow = conn.execute(
                    "SELECT verdict, verdict_note FROM handoff"
                    " WHERE task_id = ? AND agent = ? ORDER BY id DESC LIMIT 1",
                    (node_id, agent_name),
                ).fetchone()
                if hrow is not None:
                    entry["verdict"] = hrow["verdict"]
                    entry["verdict_note"] = hrow["verdict_note"]

    # 逆向きの depends_on ＝ 自分を待っているもの（自分が塞いでいるもの）
    blocking: list[dict[str, object]] = []
    for e in graph.edges_to(conn, node_id, "depends_on"):
        src = str(e["src"])
        blocking.append({"id": src, "title": _title(conn, src), "status": _task_status(conn, src)})

    # 2ホップ: depends_on の連鎖だけ辿る
    twohop: list[dict[str, object]] = []
    visited: set[str] = {node_id} | {str(x["id"]) for x in onehop["depends_on"]}
    frontier: set[str] = {str(x["id"]) for x in onehop["depends_on"]}
    hop = 2
    while frontier and hop <= depth:
        next_frontier: set[str] = set()
        for n in sorted(frontier):
            for e in graph.edges_from(conn, n, "depends_on"):
                dst = str(e["dst"])
                if dst in visited:
                    continue
                visited.add(dst)
                next_frontier.add(dst)
                twohop.append(
                    {"id": dst, "title": _title(conn, dst), "status": _task_status(conn, dst), "via": n, "hop": hop}
                )
        frontier = next_frontier
        hop += 1

    events: list[dict[str, object]] = []
    if kind == "task":
        rows = conn.execute(
            "SELECT at, from_status, to_status, note, actor FROM task_event"
            " WHERE task_id = ? ORDER BY id DESC LIMIT 5",
            (node_id,),
        ).fetchall()
        events = [dict(r) for r in reversed(rows)]

    return {
        "core": core,
        "onehop": onehop,
        "blocking": blocking,
        "twohop": twohop,
        "events": events,
        "budget": budget,
    }


def _core_md(data: dict[str, object]) -> str:
    core = data["core"]  # type: ignore[assignment]
    lines = [f"# {core['id']} {core['title']}", ""]
    if core.get("body"):
        lines.append(str(core["body"]))
        lines.append("")
    if core["kind"] == "task":
        lines.append(
            f"- status: {core.get('status')} / owner: {core.get('owner')} /"
            f" level: {core.get('level')} / due: {core.get('due') or '—'}"
        )
        if core.get("goal"):
            lines.append(f"- goal: {core['goal']}")
        if core.get("now"):
            lines.append(f"- now: {core['now']}")
        if core.get("next"):
            lines.append(f"- next: {core['next']}")
        lines.append("")
    return "\n".join(lines)


def _onehop_md(data: dict[str, object], *, include_relates: bool) -> str:
    onehop: dict[str, list[dict[str, object]]] = data["onehop"]  # type: ignore[assignment]
    blocking: list[dict[str, object]] = data["blocking"]  # type: ignore[assignment]
    lines: list[str] = []
    for rel in ONEHOP_RELS:
        if rel == "relates_to" and not include_relates:
            continue
        entries = onehop.get(rel, [])
        if not entries:
            continue
        lines.append(f"## {_REL_LABEL[rel]}")
        for e in entries:
            extra = f" [{e['status']}]" if e.get("status") else ""
            ruling = f" ruling: {e['ruling']}" if e.get("ruling") else ""
            verdict = f" verdict: {e['verdict']}" if e.get("verdict") else ""
            lines.append(f"- {e['id']}{extra} {e['title']}{ruling}{verdict}")
        lines.append("")
    if blocking:
        lines.append("## 自分を待っているもの（自分が塞いでいる）")
        for e in blocking:
            extra = f" [{e['status']}]" if e.get("status") else ""
            lines.append(f"- {e['id']}{extra} {e['title']}")
        lines.append("")
    return "\n".join(lines)


def _twohop_md(data: dict[str, object]) -> str:
    twohop: list[dict[str, object]] = data["twohop"]  # type: ignore[assignment]
    if not twohop:
        return ""
    lines = ["## 2ホップ（depends_on の連鎖。ブロッカーの根）"]
    for e in twohop:
        lines.append(f"- {e['via']} <- {e['id']} [{e.get('status')}] {e['title']}")
    lines.append("")
    return "\n".join(lines)


def _events_md(data: dict[str, object]) -> str:
    events: list[dict[str, object]] = data["events"]  # type: ignore[assignment]
    if not events:
        return ""
    lines = ["## 直近のイベント"]
    for ev in events:
        frm = ev["from_status"] or "（新規）"
        lines.append(f"- {ev['at']} {frm} -> {ev['to_status']}（{ev['actor']}）{ev['note']}")
    lines.append("")
    return "\n".join(lines)


_DROP_ORDER: tuple[str, ...] = ("relates_to", "twohop", "events")
_DROP_LABEL = {"relates_to": "relates_to", "twohop": "2ホップ", "events": "直近イベント"}


def render_markdown(data: dict[str, object]) -> str:
    budget = int(data.get("budget", 2000))  # type: ignore[arg-type]
    core_md = _core_md(data)
    included = {"relates_to": True, "twohop": True, "events": True}

    def assemble() -> str:
        parts = [core_md, _onehop_md(data, include_relates=included["relates_to"])]
        if included["twohop"]:
            parts.append(_twohop_md(data))
        if included["events"]:
            parts.append(_events_md(data))
        return "\n".join(p for p in parts if p)

    text = assemble()
    dropped: list[str] = []
    for key in _DROP_ORDER:
        if len(text) <= budget:
            break
        included[key] = False
        dropped.append(key)
        text = assemble()

    if dropped:
        labels = "、".join(_DROP_LABEL[d] for d in dropped)
        text += f"\n\n（budget {budget} 字を超えたため省略: {labels}）"
    return text


def to_json(data: dict[str, object]) -> dict[str, object]:
    """`--json` 用。budget で落ちたカテゴリは `dropped` に列挙し、本体からは外す。"""
    budget = int(data.get("budget", 2000))  # type: ignore[arg-type]
    dropped: list[str] = []
    out = dict(data)
    # 落ちた分類を markdown と同じ規則で決め直す（文字数は markdown 換算）。
    included = {"relates_to": True, "twohop": True, "events": True}

    def size() -> int:
        parts = [_core_md(data), _onehop_md(data, include_relates=included["relates_to"])]
        if included["twohop"]:
            parts.append(_twohop_md(data))
        if included["events"]:
            parts.append(_events_md(data))
        return len("\n".join(p for p in parts if p))

    for key in _DROP_ORDER:
        if size() <= budget:
            break
        included[key] = False
        dropped.append(key)

    if not included["relates_to"]:
        out_onehop = dict(out["onehop"])  # type: ignore[arg-type]
        out_onehop.pop("relates_to", None)
        out["onehop"] = out_onehop
    if not included["twohop"]:
        out["twohop"] = []
    if not included["events"]:
        out["events"] = []
    out["dropped"] = dropped
    return out
