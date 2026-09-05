"""`money`（家令。ADR-005 §2）。読みは board の `get_money`／`GET .../summary` は
`money_cli.cmd_month` 等をそのまま呼ぶ（board.api_staff.get_money と同じ組み立て）。
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ...board import api_staff as board_staff
from ...errors import ManorError
from .._common import WebContext, commit_and_render, manor_error_to_http, ns, open_conn, require_writable, table_exists


def _require_steward(conn) -> None:
    if not table_exists(conn, "steward_expense"):
        raise HTTPException(status_code=404, detail="家令（steward）が導入されていません")


class ExpenseRequest(BaseModel):
    date: str | None = None
    amount: str | int
    category: str
    memo: str = ""
    income: bool = False


class RecurringPaidRequest(BaseModel):
    on: str | None = None


class BudgetRequest(BaseModel):
    limit: str | int


def register(app: FastAPI, ctx: WebContext) -> None:
    @app.get("/api/v1/money")
    def money() -> dict[str, object]:
        with open_conn(ctx) as conn:
            return board_staff.get_money(conn, ctx.home)

    @app.get("/api/v1/money/summary")
    def summary(ym: str | None = None) -> dict[str, object]:
        from ...staff.steward import cli as money_cli

        with open_conn(ctx) as conn:
            _require_steward(conn)
            month = money_cli.cmd_month(conn, ctx.home, ns(ym=ym))
            due = money_cli.cmd_due(conn, ctx.home, ns(days=14))
            recent = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM steward_expense ORDER BY date DESC, id DESC LIMIT 20"
                ).fetchall()
            ]
            return {"month": month, "due": due, "recent_expenses": recent}

    @app.post("/api/v1/money/expense")
    def expense_add(body: ExpenseRequest) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.steward import cli as money_cli

        with open_conn(ctx) as conn:
            _require_steward(conn)
            try:
                result = money_cli.cmd_log(
                    conn, ctx.home,
                    ns(amount=body.amount, category=body.category, date=body.date, memo=body.memo, income=body.income),
                )
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result  # type: ignore[return-value]

    @app.post("/api/v1/money/recurring/{recurring_id}/paid")
    def recurring_paid(recurring_id: int, body: RecurringPaidRequest) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.steward import cli as money_cli

        with open_conn(ctx) as conn:
            _require_steward(conn)
            try:
                result = money_cli.cmd_recurring_paid(conn, ctx.home, ns(ref=str(recurring_id), on=body.on))
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result  # type: ignore[return-value]

    @app.put("/api/v1/money/budget/{category}")
    def budget_set(category: str, body: BudgetRequest) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.steward import cli as money_cli

        with open_conn(ctx) as conn:
            _require_steward(conn)
            try:
                result = money_cli.cmd_budget_set(conn, ctx.home, ns(category=category, limit=body.limit))
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result  # type: ignore[return-value]
