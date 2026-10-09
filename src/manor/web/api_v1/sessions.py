"""`/api/v1/sessions`（ADR-025 §10）。他のPCを含む Claude Code のセッションの一覧。

画面は10秒おきに読む。前回の取り込みから10秒以上経っていれば、先に中継（GAS）から取り込む
（背景のスレッドを持たない——画面が開いている間だけ中継を叩く）。中継に届かなくても 500 に
せず、手元の最新を返して `relay.ok=false` と理由を添える。
"""

from __future__ import annotations

from fastapi import FastAPI

from ...remote import relay, store
from .._common import WebContext, open_conn, table_exists


def register(app: FastAPI, ctx: WebContext) -> None:
    @app.get("/api/v1/sessions")
    def sessions(include_ended: int = 0) -> dict[str, object]:
        with open_conn(ctx) as conn:
            if not table_exists(conn, "remote_session"):
                return {"items": [], "relay": {"configured": False, "ok": False, "error": "no_table"}}
            status: dict[str, object] = {"configured": relay.configured(), "ok": True, "error": None}
            if relay.configured() and not ctx.read_only:
                try:
                    relay.pull(conn, ctx.home, min_interval=relay.PULL_MIN_INTERVAL)
                except relay.RelayError as exc:
                    conn.rollback()
                    status.update(ok=False, error=str(exc))
            items = store.list_sessions(conn, include_ended=bool(include_ended))
            state = relay.load_state(ctx.home)
            status["last_pull_ts"] = state.get("last_pull_ts")
            return {"items": items, "relay": status}
