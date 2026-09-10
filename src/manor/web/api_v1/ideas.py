"""`ideas`（意見箱。ADR-010 D2 系の続き）。画面のフォームと一覧。

**起票は `task_mod.add_idea` を通す**——Slack（`#idea`）と同じ関数（`slack._create_from_intake`
も呼ぶ先はここと同じ）。2箇所に書かない（夜勤 N6 の指示）。
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel, Field

from ... import task as task_mod
from ...errors import ManorError
from .._common import WebContext, commit_and_render, manor_error_to_http, open_conn, require_writable


class IdeaAddRequest(BaseModel):
    #: 本文の先頭行を題名として機械が取る（`task_mod.add_idea` 参照）。主人に2度書かせない。
    body: str = Field(..., min_length=1)


def register(app: FastAPI, ctx: WebContext) -> None:
    @app.get("/api/v1/ideas")
    def ideas_list() -> dict[str, object]:
        with open_conn(ctx) as conn:
            items = task_mod.list_tasks(conn, source="idea", include_settled=True)
            return {"items": items}

    @app.post("/api/v1/ideas")
    def ideas_add(body: IdeaAddRequest) -> dict[str, object]:
        require_writable(ctx)
        with open_conn(ctx) as conn:
            try:
                task_id = task_mod.add_idea(conn, body.body)
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return {"id": task_id}
