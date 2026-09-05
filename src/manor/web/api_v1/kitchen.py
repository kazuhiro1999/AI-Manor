"""`kitchen`（料理長。ADR-005 §2）。読みは board の `get_chef` をそのまま呼ぶ。
書きは `staff/chef/cli.py` の `cmd_*` を呼ぶ（SQL は web 層に書かない）。
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ...board import api_staff as board_staff
from ...errors import ManorError
from .._common import WebContext, commit_and_render, manor_error_to_http, ns, open_conn, require_writable, table_exists


def _require_chef(conn) -> None:
    if not table_exists(conn, "chef_pantry"):
        raise HTTPException(status_code=404, detail="料理長（chef）が導入されていません")


class PantryAddRequest(BaseModel):
    item: str = Field(..., min_length=1)
    qty: str = "不明"
    unit: str = ""
    expires: str | None = None
    place: str = "不明"


class PantryUseRequest(BaseModel):
    qty: str | None = None
    all: bool = False


class ShoppingAddRequest(BaseModel):
    item: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)
    aisle: str = "その他"


class ShoppingBoughtRequest(BaseModel):
    items: list[str] = Field(default_factory=list)


class MealLogRequest(BaseModel):
    date: str
    slot: str
    dish: str
    ingredients: str = ""
    planned: bool = False


def register(app: FastAPI, ctx: WebContext) -> None:
    @app.get("/api/v1/kitchen")
    def kitchen() -> dict[str, object]:
        with open_conn(ctx) as conn:
            return board_staff.get_chef(conn, ctx.home)

    @app.post("/api/v1/kitchen/pantry")
    def pantry_add(body: PantryAddRequest) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.chef import cli as chef_cli

        with open_conn(ctx) as conn:
            _require_chef(conn)
            try:
                result = chef_cli.cmd_pantry_add(
                    conn, ctx.home,
                    ns(item=body.item, qty=body.qty, unit=body.unit, expires=body.expires, place=body.place, note=""),
                )
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result  # type: ignore[return-value]

    @app.post("/api/v1/kitchen/pantry/{pantry_id}/use")
    def pantry_use(pantry_id: int, body: PantryUseRequest) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.chef import cli as chef_cli

        with open_conn(ctx) as conn:
            _require_chef(conn)
            try:
                result = chef_cli.cmd_pantry_use(
                    conn, ctx.home, ns(item=str(pantry_id), qty=body.qty, all=body.all)
                )
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result  # type: ignore[return-value]

    @app.delete("/api/v1/kitchen/pantry/{pantry_id}")
    def pantry_remove(pantry_id: int) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.chef import cli as chef_cli

        with open_conn(ctx) as conn:
            _require_chef(conn)
            try:
                result = chef_cli.cmd_pantry_remove(conn, ctx.home, ns(ref=str(pantry_id)))
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result  # type: ignore[return-value]

    @app.post("/api/v1/kitchen/shopping")
    def shopping_add(body: ShoppingAddRequest) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.chef import cli as chef_cli

        with open_conn(ctx) as conn:
            _require_chef(conn)
            try:
                result = chef_cli.cmd_shopping_add(
                    conn, ctx.home, ns(item=body.item, reason=body.reason, aisle=body.aisle)
                )
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result  # type: ignore[return-value]

    @app.post("/api/v1/kitchen/shopping/bought")
    def shopping_bought(body: ShoppingBoughtRequest) -> object:
        require_writable(ctx)
        from ...staff.chef import cli as chef_cli

        if not body.items:
            raise HTTPException(status_code=400, detail="items が空です")
        with open_conn(ctx) as conn:
            _require_chef(conn)
            try:
                result = chef_cli.cmd_shopping_bought(
                    conn, ctx.home, ns(items=",".join(body.items), qty=None, expires=None, place=None)
                )
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result

    @app.post("/api/v1/kitchen/meal")
    def meal_log(body: MealLogRequest) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.chef import cli as chef_cli

        with open_conn(ctx) as conn:
            _require_chef(conn)
            try:
                result = chef_cli.cmd_meal_log(
                    conn, ctx.home,
                    ns(date=body.date, slot=body.slot, dish=body.dish, ingredients=body.ingredients,
                       planned=body.planned, note=""),
                )
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result  # type: ignore[return-value]
