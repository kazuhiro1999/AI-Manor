"""Context Graph: node / edge の共通 API（ADR-001 D2）。

task / project / decision はそれぞれの表を持つが、`node` 表（title/body/時刻）は共通。
ここに node の作成・edge の張り替え・note・milestone・agent 同期をまとめる。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import util
from .errors import ManorError
from .ids import next_id

#: `edge.rel` の語彙（core.sql の CHECK と一致させること）。
REL_TYPES: frozenset[str] = frozenset(
    {
        "depends_on",
        "blocks",
        "part_of",
        "duplicates",
        "supersedes",
        "derived_from",
        "decided_by",
        "delegated_to",
        "relates_to",
        "about",
    }
)


def node_exists(conn: sqlite3.Connection, node_id: str) -> bool:
    return conn.execute("SELECT 1 FROM node WHERE id = ?", (node_id,)).fetchone() is not None


def get_node(conn: sqlite3.Connection, node_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM node WHERE id = ?", (node_id,)).fetchone()


def require_node(conn: sqlite3.Connection, node_id: str) -> sqlite3.Row:
    row = get_node(conn, node_id)
    if row is None:
        raise ManorError(
            f"{node_id} は見つかりません",
            code=2,
            key="error.graph.node_not_found",
            params={"node_id": node_id},
        )
    return row


def create_node(
    conn: sqlite3.Connection,
    *,
    kind: str,
    title: str,
    body: str = "",
    node_id: str | None = None,
    id_prefix: str | None = None,
) -> str:
    """node を1行作る。`node_id` を渡せば固定 ID（agent 用）、無ければ `id_prefix` で採番する。"""
    if node_id is None:
        if id_prefix is None:
            raise ManorError(
                "create_node には node_id か id_prefix のどちらかが要ります",
                key="error.graph.create_node_needs_id",
            )
        node_id = next_id(conn, id_prefix)
    now = util.now()
    conn.execute(
        "INSERT INTO node (id, kind, title, body, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (node_id, kind, title, body, now, now),
    )
    return node_id


def touch_node(conn: sqlite3.Connection, node_id: str, *, at: str | None = None) -> None:
    conn.execute("UPDATE node SET updated_at = ? WHERE id = ?", (at or util.now(), node_id))


def link(conn: sqlite3.Connection, src: str, rel: str, dst: str, *, note: str = "") -> None:
    """`src --rel--> dst` を張る。既にあれば note を更新する（冪等）。"""
    if rel not in REL_TYPES:
        raise ManorError(
            f"語彙外の辺の種類です: {rel!r}",
            code=2,
            key="error.graph.rel_unknown",
            params={"rel": repr(rel)},
        )
    if src == dst:
        raise ManorError(
            "自分自身への辺は張れません",
            key="error.graph.self_edge_denied",
        )
    require_node(conn, src)
    require_node(conn, dst)
    conn.execute(
        "INSERT INTO edge (src, rel, dst, note, created_at) VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(src, rel, dst) DO UPDATE SET note = excluded.note",
        (src, rel, dst, note, util.now()),
    )


def unlink(conn: sqlite3.Connection, src: str, rel: str, dst: str) -> None:
    cur = conn.execute("DELETE FROM edge WHERE src = ? AND rel = ? AND dst = ?", (src, rel, dst))
    if cur.rowcount == 0:
        raise ManorError(
            f"辺が見つかりません: {src} --{rel}--> {dst}",
            code=2,
            key="error.graph.edge_not_found",
            params={"src": src, "rel": rel, "dst": dst},
        )


def edges_from(conn: sqlite3.Connection, node_id: str, rel: str | None = None) -> list[sqlite3.Row]:
    if rel is None:
        return conn.execute(
            "SELECT * FROM edge WHERE src = ? ORDER BY rel, dst", (node_id,)
        ).fetchall()
    return conn.execute(
        "SELECT * FROM edge WHERE src = ? AND rel = ? ORDER BY dst", (node_id, rel)
    ).fetchall()


def edges_to(conn: sqlite3.Connection, node_id: str, rel: str | None = None) -> list[sqlite3.Row]:
    if rel is None:
        return conn.execute(
            "SELECT * FROM edge WHERE dst = ? ORDER BY rel, src", (node_id,)
        ).fetchall()
    return conn.execute(
        "SELECT * FROM edge WHERE dst = ? AND rel = ? ORDER BY src", (node_id, rel)
    ).fetchall()


# --- note --------------------------------------------------------------------


def note_add(
    conn: sqlite3.Connection, title: str, *, about: str | None = None, body: str = ""
) -> str:
    note_id = create_node(conn, kind="note", title=title, body=body, id_prefix="N")
    if about:
        link(conn, note_id, "about", about)
    return note_id


# --- milestone -----------------------------------------------------------------


def milestone_add(
    conn: sqlite3.Connection,
    title: str,
    *,
    date: str,
    project_id: str | None = None,
    approximate: bool = False,
) -> str:
    milestone_id = create_node(conn, kind="milestone", title=title, id_prefix="M")
    conn.execute(
        "INSERT INTO milestone (id, date, approximate, project_id) VALUES (?, ?, ?, ?)",
        (milestone_id, date, 1 if approximate else 0, project_id),
    )
    return milestone_id


def milestone_done(conn: sqlite3.Connection, milestone_id: str, *, at: str | None = None) -> bool:
    """節目を「済んだ」にする。既に済んでいれば何もしない（冪等）。**日付は書き換えない**
    ——「2026-09-03 に予定し、済んだ」という事実をそのまま残すため（執事の裁定 2026-09-05）。

    戻り値は「この呼び出しで変わったか」。存在しない id なら `False`。
    """
    from . import util

    row = conn.execute("SELECT done_at FROM milestone WHERE id = ?", (milestone_id,)).fetchone()
    if row is None or row["done_at"]:
        return False
    conn.execute(
        "UPDATE milestone SET done_at = ? WHERE id = ?", (at or util.now(), milestone_id)
    )
    conn.execute("UPDATE node SET updated_at = ? WHERE id = ?", (util.now(), milestone_id))
    return True


def milestone_undone(conn: sqlite3.Connection, milestone_id: str) -> bool:
    """「済んだ」を取り消す（押し間違いの戻し道。無ければ主人が直せない）。"""
    from . import util

    row = conn.execute("SELECT done_at FROM milestone WHERE id = ?", (milestone_id,)).fetchone()
    if row is None or not row["done_at"]:
        return False
    conn.execute("UPDATE milestone SET done_at = NULL WHERE id = ?", (milestone_id,))
    conn.execute("UPDATE node SET updated_at = ? WHERE id = ?", (util.now(), milestone_id))
    return True


def milestone_list(
    conn: sqlite3.Connection,
    *,
    project_id: str | None = None,
    upcoming_days: int | None = None,
    include_done: bool = True,
) -> list[dict[str, object]]:
    sql = (
        "SELECT m.id, n.title, m.date, m.approximate, m.project_id, m.done_at"
        " FROM milestone m JOIN node n ON n.id = m.id WHERE 1=1"
    )
    params: list[object] = []
    if project_id:
        sql += " AND m.project_id = ?"
        params.append(project_id)
    if not include_done:
        sql += " AND m.done_at IS NULL"
    if upcoming_days is not None:
        sql += " AND date(m.date) BETWEEN date('now','localtime') AND date('now','localtime', ?)"
        params.append(f"+{upcoming_days} days")
    sql += " ORDER BY m.date"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


# --- agent 同期 ------------------------------------------------------------------


def _agent_summary(path: Path, *, limit: int = 500) -> str:
    """agent 定義ファイルから短い要約（文脈用の1段落）を作る。フロントマターは飛ばす。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                lines = lines[i + 1 :]
                break
    body = "\n".join(lines).strip()
    flat = " ".join(body.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def agent_sync(conn: sqlite3.Connection, agents_dir: Path | None = None) -> list[str]:
    """`.claude/agents/*.md` を走査して `node(kind='agent')` を作る/更新する。

    ディレクトリが無ければ何もしない（`manor init` が呼ぶが、まだ揃っていなくてよい）。
    """
    agents_dir = agents_dir or (util.repo_root() / ".claude" / "agents")
    if not agents_dir.is_dir():
        return []
    names: list[str] = []
    for path in sorted(agents_dir.glob("*.md")):
        name = path.stem
        node_id = f"A:{name}"
        summary = _agent_summary(path)
        if node_exists(conn, node_id):
            conn.execute(
                "UPDATE node SET title = ?, body = ?, updated_at = ? WHERE id = ?",
                (name, summary, util.now(), node_id),
            )
        else:
            create_node(conn, kind="agent", title=name, body=summary, node_id=node_id)
        names.append(name)
    return names
