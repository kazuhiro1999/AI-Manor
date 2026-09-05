"""`house`（家政婦。ADR-005 §2）。

`POST /api/v1/house/supply/{id}` の `{id}` は `housekeeper_supply` の実際の主キー（`item`
の文字列。表に整数 id が無いため）を指す——**曖昧だった点**: ADR の表記は `{id}` だが、
`housekeeper_supply` は `item TEXT PRIMARY KEY` なので `item` 名をそのまま path に使う。
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ...board import api_staff as board_staff
from ...errors import ManorError
from .._common import WebContext, commit_and_render, manor_error_to_http, ns, open_conn, require_writable, table_exists


def _require_house(conn) -> None:
    if not table_exists(conn, "housekeeper_chore"):
        raise HTTPException(status_code=404, detail="家政婦（housekeeper）が導入されていません")


class ChoreDoneRequest(BaseModel):
    on: str | None = None
    note: str = ""


class SupplySetRequest(BaseModel):
    qty: float


class ChoreAddRequest(BaseModel):
    name: str = Field(..., min_length=1)
    every: int
    area: str = ""


def register(app: FastAPI, ctx: WebContext) -> None:
    @app.get("/api/v1/house")
    def house() -> dict[str, object]:
        with open_conn(ctx) as conn:
            return board_staff.get_house(conn, ctx.home)

    @app.post("/api/v1/house/chore/{chore_id}/done")
    def chore_done(chore_id: int, body: ChoreDoneRequest) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.housekeeper import cli as house_cli

        with open_conn(ctx) as conn:
            _require_house(conn)
            try:
                result = house_cli.cmd_house_chore_done(
                    conn, ctx.home, ns(ref=str(chore_id), on=body.on, note=body.note)
                )
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result  # type: ignore[return-value]

    @app.post("/api/v1/house/supply/{item}")
    def supply_set(item: str, body: SupplySetRequest) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.housekeeper import cli as house_cli

        with open_conn(ctx) as conn:
            _require_house(conn)
            try:
                result = house_cli.cmd_house_supply_set(conn, ctx.home, ns(item=item, qty=body.qty))
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result  # type: ignore[return-value]

    @app.post("/api/v1/house/chore")
    def chore_add(body: ChoreAddRequest) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.housekeeper import cli as house_cli

        with open_conn(ctx) as conn:
            _require_house(conn)
            try:
                result = house_cli.cmd_house_chore_add(
                    conn, ctx.home, ns(name=body.name, every=body.every, area=body.area, note="")
                )
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result  # type: ignore[return-value]
