"""`/api/staff/<name>` — 部下4名のデータを読む口（ROADMAP §7「タブ＝部下＝API を1対1に」）。

**集計は部下の `ops.py` / `cli.py` の `cmd_*` を呼ぶ。** board 側で SQL の突き合わせ・
期日計算・並べ替えを書き直さない（ADR-002 §2「道具は判断しない」の道具を、board も
2つ持たない）。読める部下が居なければ（表が無ければ）空で返す——board は
「4人とも必ず居る」ことを前提にしない。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .. import util
from ..errors import ManorError
from ._common import BoardContext, commit_and_render, manor_error_to_http, ns, open_conn, require_writable


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
        is not None
    )


def get_chef(conn: sqlite3.Connection, home: Path) -> dict[str, object]:
    if not _table_exists(conn, "chef_pantry"):
        return {"available": False}
    from ..staff.chef import cli as chef_cli
    from ..staff.chef import ops as chef_ops

    pantry_rows = [dict(r) for r in conn.execute("SELECT * FROM chef_pantry").fetchall()]
    pantry = chef_ops.sort_by_expiry(pantry_rows)

    shopping_flat = chef_cli.cmd_shopping_list(conn, home, ns())
    shopping_by_aisle: dict[str, list[dict[str, object]]] = {}
    for row in shopping_flat:  # type: ignore[union-attr]
        shopping_by_aisle.setdefault(str(row["aisle"]), []).append(row)  # type: ignore[index]

    today = util.today()
    start, end = chef_ops.week_range(today, 7)
    meals = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM chef_meal WHERE date BETWEEN ? AND ? ORDER BY date DESC, slot",
            (start, end),
        ).fetchall()
    ]

    taste = chef_cli.cmd_taste_show(conn, home, ns())

    return {
        "available": True,
        "pantry": pantry,
        "shopping_by_aisle": shopping_by_aisle,
        "meals_recent": meals,
        "taste": taste,
    }


def get_house(conn: sqlite3.Connection, home: Path) -> dict[str, object]:
    if not _table_exists(conn, "housekeeper_chore"):
        return {"available": False}
    from ..staff.housekeeper import cli as house_cli

    today = house_cli.cmd_house_today(conn, home, ns())
    return {"available": True, "today": today}


def get_money(conn: sqlite3.Connection, home: Path) -> dict[str, object]:
    if not _table_exists(conn, "steward_expense"):
        return {"available": False}
    from ..staff.steward import cli as money_cli

    month = money_cli.cmd_month(conn, home, ns(ym=None))
    due = money_cli.cmd_due(conn, home, ns(days=14))
    recent = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM steward_expense ORDER BY date DESC, id DESC LIMIT 20"
        ).fetchall()
    ]
    return {"available": True, "month": month, "due": due, "recent_expenses": recent}


def get_sec(conn: sqlite3.Connection, home: Path) -> dict[str, object]:
    if not _table_exists(conn, "secretary_reminder"):
        return {"available": False}
    from ..staff.secretary import cli as sec_cli

    agenda = sec_cli.cmd_agenda(conn, home, ns(days=7))
    reminders_open = sec_cli.cmd_remind_list(conn, home, ns(all=False))
    inbox_unrouted = sec_cli.cmd_inbox_list(conn, home, ns(all=False))
    return {
        "available": True,
        "agenda": agenda,
        "reminders_open": reminders_open,
        "inbox_unrouted": inbox_unrouted,
    }


class NoteRequest(BaseModel):
    note: str = Field("", max_length=2000)


def register(app: FastAPI, ctx: BoardContext) -> None:
    @app.get("/api/staff/chef")
    def api_staff_chef() -> dict[str, object]:
        with open_conn(ctx) as conn:
            return get_chef(conn, ctx.home)

    @app.get("/api/staff/house")
    def api_staff_house() -> dict[str, object]:
        with open_conn(ctx) as conn:
            return get_house(conn, ctx.home)

    @app.get("/api/staff/money")
    def api_staff_money() -> dict[str, object]:
        with open_conn(ctx) as conn:
            return get_money(conn, ctx.home)

    @app.get("/api/staff/sec")
    def api_staff_sec() -> dict[str, object]:
        with open_conn(ctx) as conn:
            return get_sec(conn, ctx.home)

    @app.post("/api/staff/sec/remind/{reminder_id}/done")
    def api_sec_remind_done(reminder_id: int, body: NoteRequest = NoteRequest()) -> dict[str, object]:
        require_writable(ctx)
        from ..staff.secretary import cli as sec_cli

        with open_conn(ctx) as conn:
            if not _table_exists(conn, "secretary_reminder"):
                raise HTTPException(status_code=404, detail="秘書（secretary）が導入されていません")
            try:
                result = sec_cli.cmd_remind_done(conn, ctx.home, ns(id=reminder_id))
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result  # type: ignore[return-value]

    @app.post("/api/staff/house/chore/{chore_id}/done")
    def api_house_chore_done(chore_id: int, body: NoteRequest = NoteRequest()) -> dict[str, object]:
        require_writable(ctx)
        from ..staff.housekeeper import cli as house_cli

        with open_conn(ctx) as conn:
            if not _table_exists(conn, "housekeeper_chore"):
                raise HTTPException(status_code=404, detail="家政婦（housekeeper）が導入されていません")
            try:
                result = house_cli.cmd_house_chore_done(
                    conn, ctx.home, ns(ref=str(chore_id), on=None, note=body.note)
                )
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result  # type: ignore[return-value]
