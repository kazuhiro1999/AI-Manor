"""project の API（ADR-001 §5）。"""

from __future__ import annotations

import sqlite3

from . import graph, util
from .errors import ManorError

VALID_STATUS = {"active", "paused", "done"}
VALID_PRESET = {"careful", "standard", "fast"}


def resolve(conn: sqlite3.Connection, ref: str) -> sqlite3.Row:
    """`code` か `id`（P で始まる）のどちらでも project 行を引く。"""
    if ref.startswith("P"):
        row = conn.execute("SELECT * FROM project WHERE id = ?", (ref,)).fetchone()
        if row is not None:
            return row
    row = conn.execute("SELECT * FROM project WHERE code = ?", (ref,)).fetchone()
    if row is None:
        raise ManorError(
            f"project が見つかりません: {ref}",
            code=2,
            key="error.project.not_found",
            params={"ref": ref},
        )
    return row


def add(
    conn: sqlite3.Connection,
    code: str,
    name: str,
    *,
    kind: str = "",
    priority: int = 3,
    preset: str = "standard",
    status: str = "active",
    due: str | None = None,
    body: str = "",
    next_action: str = "",
) -> str:
    if preset not in VALID_PRESET:
        raise ManorError(
            f"語彙外の preset です: {preset!r}",
            code=2,
            key="error.project.preset_unknown",
            params={"preset": repr(preset)},
        )
    if status not in VALID_STATUS:
        raise ManorError(
            f"語彙外の status です: {status!r}",
            code=2,
            key="error.project.status_unknown",
            params={"status": repr(status)},
        )
    if conn.execute("SELECT 1 FROM project WHERE code = ?", (code,)).fetchone() is not None:
        raise ManorError(
            f"project code が重複しています: {code}",
            key="error.project.code_duplicate",
            params={"code": code},
        )

    project_id = graph.create_node(conn, kind="project", title=name, body=body, id_prefix="P")
    conn.execute(
        "INSERT INTO project (id, code, kind, priority, preset, status, next_action, due)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (project_id, code, kind, priority, preset, status, next_action, due),
    )
    return project_id


def set(
    conn: sqlite3.Connection,
    ref: str,
    *,
    name: str | None = None,
    kind: str | None = None,
    priority: int | None = None,
    preset: str | None = None,
    status: str | None = None,
    due: str | None = None,
    body: str | None = None,
    next_action: str | None = None,
) -> str:
    row = resolve(conn, ref)
    project_id = str(row["id"])
    if preset is not None and preset not in VALID_PRESET:
        raise ManorError(
            f"語彙外の preset です: {preset!r}",
            code=2,
            key="error.project.preset_unknown",
            params={"preset": repr(preset)},
        )
    if status is not None and status not in VALID_STATUS:
        raise ManorError(
            f"語彙外の status です: {status!r}",
            code=2,
            key="error.project.status_unknown",
            params={"status": repr(status)},
        )

    fields: dict[str, object] = {}
    if kind is not None:
        fields["kind"] = kind
    if priority is not None:
        fields["priority"] = priority
    if preset is not None:
        fields["preset"] = preset
    if status is not None:
        fields["status"] = status
    if due is not None:
        fields["due"] = due
    if next_action is not None:
        fields["next_action"] = next_action
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE project SET {sets} WHERE id = ?", (*fields.values(), project_id))

    if name is not None or body is not None:
        node_sets = []
        params: list[object] = []
        if name is not None:
            node_sets.append("title = ?")
            params.append(name)
        if body is not None:
            node_sets.append("body = ?")
            params.append(body)
        node_sets.append("updated_at = ?")
        params.append(util.now())
        params.append(project_id)
        conn.execute(f"UPDATE node SET {', '.join(node_sets)} WHERE id = ?", params)
    return project_id


def show(conn: sqlite3.Connection, ref: str) -> dict[str, object]:
    row = resolve(conn, ref)
    project_id = str(row["id"])
    node = graph.get_node(conn, project_id)
    tasks = [
        dict(r)
        for r in conn.execute(
            "SELECT id, status, section, level, owner FROM task WHERE project_id = ? ORDER BY id",
            (project_id,),
        ).fetchall()
    ]
    milestones = graph.milestone_list(conn, project_id=project_id)
    out = dict(row)
    out["title"] = node["title"] if node else ""
    out["body"] = node["body"] if node else ""
    out["tasks"] = tasks
    out["milestones"] = milestones
    return out


def list_projects(
    conn: sqlite3.Connection, *, status: str | None = None, kind: str | None = None
) -> list[dict[str, object]]:
    sql = (
        "SELECT p.*, n.title AS title FROM project p JOIN node n ON n.id = p.id WHERE 1=1"
    )
    params: list[object] = []
    if status:
        sql += " AND p.status = ?"
        params.append(status)
    if kind:
        sql += " AND p.kind = ?"
        params.append(kind)
    sql += " ORDER BY p.priority, p.code"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]
