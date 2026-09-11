"""project の API（ADR-001 §5）。"""

from __future__ import annotations

import sqlite3

from . import graph, util
from . import user as user_mod
from .errors import ManorError

VALID_STATUS = {"active", "paused", "done"}
VALID_PRESET = {"careful", "standard", "fast"}

#: 執事自身のプロジェクト（v1 の X 系）を示す project.kind。project.kind は自由文だが
#: この1語だけは import_v1 側で固定して入れている。**owner では判定しない**——
#: 主人の仕事にも owner=butler が付く行があるため。主人の関心事の一覧から執事自身の件を
#: 隠す・関心順の最下部へ落とす、といった判定はすべてここを参照する（2026-09-08 主人の
#: ご指摘・T26。同じ判定を複数箇所に書き直す事故が続いたための一本化）。
BUTLER_PROJECT_KIND = "執事"


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
    user: str | None = None,
) -> str:
    """`user`（ADR-014 D2「誰の件か」）: 省略時は `user.resolve_default(conn, explicit=user,
    owner="master")`——何も言わなければ主人の件。ただし `kind == BUTLER_PROJECT_KIND`
    （執事自身のプロジェクト）なら執事の件（`owner="butler"` として解決する）。
    """
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

    owner_for_default = "butler" if kind == BUTLER_PROJECT_KIND else "master"
    user_id = user_mod.resolve_default(conn, explicit=user, owner=owner_for_default)

    project_id = graph.create_node(conn, kind="project", title=name, body=body, id_prefix="P")
    conn.execute(
        "INSERT INTO project (id, code, kind, priority, preset, status, next_action, due, user_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (project_id, code, kind, priority, preset, status, next_action, due, user_id),
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
    user: str | None = None,
) -> str:
    """`user`（ADR-014 D2）を渡せば、知っていて畳んでいない利用者であることを
    確かめてから書き換える（`user.exists_active`。知らない・畳んだ利用者は
    `ManorError(code=2)`）。
    """
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
    if user is not None and not user_mod.exists_active(conn, user):
        raise ManorError(
            f"user が見つからない、または畳まれています: {user}",
            code=2,
            key="error.user.unknown_or_archived",
            params={"user_id": user},
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
    if user is not None:
        fields["user_id"] = user
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
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    kind: str | None = None,
    user_id: str | None = None,
) -> list[dict[str, object]]:
    """`user_id`（ADR-014 D4）: 渡すと「誰の件か」で絞る。`None`（既定）は絞らない。"""
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
    if user_id is not None:
        sql += " AND p.user_id = ?"
        params.append(user_id)
    sql += " ORDER BY p.priority, p.code"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]
