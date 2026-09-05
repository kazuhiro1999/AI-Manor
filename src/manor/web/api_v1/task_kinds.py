"""`task-kinds`（ADR-010 D2「タスクの種類」）。CLI（`manor kind ...`）と同じ `task_kind.py` を呼ぶ。

`rules.py`（ADR-005 §2）と同じ形——一覧・追加・改名・「削除」＝アーカイブ。**物理削除は
しない**（隠しても、既にその種類が付いた task はそのまま残る。D2「過去の記録を書き換えない」）。
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from ... import task_kind as task_kind_mod
from ...errors import ManorError
from .._common import WebContext, commit_and_render, manor_error_to_http, open_conn, require_writable


class TaskKindCreateRequest(BaseModel):
    id: str
    label: str


class TaskKindUpdateRequest(BaseModel):
    label: str


def register(app: FastAPI, ctx: WebContext) -> None:
    @app.get("/api/v1/task-kinds")
    def list_task_kinds(all: bool = False) -> list[dict[str, object]]:
        with open_conn(ctx) as conn:
            return task_kind_mod.list_kinds(conn, include_archived=all)

    @app.post("/api/v1/task-kinds")
    def create_task_kind(body: TaskKindCreateRequest) -> dict[str, object]:
        require_writable(ctx)
        with open_conn(ctx) as conn:
            try:
                result = task_kind_mod.add(conn, body.id, body.label)
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result

    @app.put("/api/v1/task-kinds/{kind_id}")
    def update_task_kind(kind_id: str, body: TaskKindUpdateRequest) -> dict[str, object]:
        require_writable(ctx)
        with open_conn(ctx) as conn:
            try:
                result = task_kind_mod.rename(conn, kind_id, body.label)
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result

    @app.delete("/api/v1/task-kinds/{kind_id}")
    def delete_task_kind(kind_id: str) -> dict[str, object]:
        """物理削除ではなくアーカイブ（`rules.py` の `delete_rule` と同じ約束）。"""
        require_writable(ctx)
        with open_conn(ctx) as conn:
            try:
                result = task_kind_mod.archive(conn, kind_id)
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result

    @app.post("/api/v1/task-kinds/{kind_id}/unarchive")
    def unarchive_task_kind(kind_id: str) -> dict[str, object]:
        """隠したものを戻す。**隠せる場所からは戻せるべき**——core と CLI には
        `unarchive` があるのに Web に経路が無く、画面から隠すと画面では戻せなかった
        （実装の検分で発見。2026-09-04）。
        """
        require_writable(ctx)
        with open_conn(ctx) as conn:
            try:
                result = task_kind_mod.unarchive(conn, kind_id)
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result
