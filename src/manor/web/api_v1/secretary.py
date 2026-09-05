"""`secretary`（秘書。ADR-005 §2）。"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ...board import api_staff as board_staff
from ...errors import ManorError
from .._common import WebContext, commit_and_render, manor_error_to_http, ns, open_conn, require_writable, table_exists


def _require_secretary(conn) -> None:
    if not table_exists(conn, "secretary_reminder"):
        raise HTTPException(status_code=404, detail="秘書（secretary）が導入されていません")


class ReminderAddRequest(BaseModel):
    text: str = Field(..., min_length=1)
    on: str = Field(..., min_length=1)
    at: str | None = None


class EventAddRequest(BaseModel):
    title: str = Field(..., min_length=1)
    start: str = Field(..., min_length=1)
    end: str | None = None
    place: str = ""
    note: str = ""


def register(app: FastAPI, ctx: WebContext) -> None:
    @app.get("/api/v1/secretary")
    def secretary() -> dict[str, object]:
        with open_conn(ctx) as conn:
            return board_staff.get_sec(conn, ctx.home)

    @app.post("/api/v1/secretary/reminder")
    def reminder_add(body: ReminderAddRequest) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.secretary import cli as sec_cli

        with open_conn(ctx) as conn:
            _require_secretary(conn)
            try:
                result = sec_cli.cmd_remind_add(
                    conn, ctx.home, ns(text=body.text, on=body.on, at=body.at, source="web")
                )
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result  # type: ignore[return-value]

    @app.post("/api/v1/secretary/reminder/{reminder_id}/done")
    def reminder_done(reminder_id: int) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.secretary import cli as sec_cli

        with open_conn(ctx) as conn:
            _require_secretary(conn)
            try:
                result = sec_cli.cmd_remind_done(conn, ctx.home, ns(id=reminder_id))
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result  # type: ignore[return-value]

    @app.post("/api/v1/secretary/event")
    def event_add(body: EventAddRequest) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.secretary import cli as sec_cli

        with open_conn(ctx) as conn:
            _require_secretary(conn)
            try:
                result = sec_cli.cmd_event_add(
                    conn, ctx.home,
                    ns(title=body.title, start=body.start, end=body.end, place=body.place, note=body.note),
                )
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result  # type: ignore[return-value]

    @app.get("/api/v1/secretary/agenda")
    def agenda(days: int = 7) -> object:
        from ...staff.secretary import cli as sec_cli

        with open_conn(ctx) as conn:
            _require_secretary(conn)
            return sec_cli.cmd_agenda(conn, ctx.home, ns(days=days))
