"""`/api/v1/sessions`（ADR-025 §10）。他のPCを含む Claude Code のセッションの一覧。

画面は10秒おきに読む。前回の取り込みから10秒以上経っていれば、先に中継（GAS）から取り込む
（背景のスレッドを持たない——画面が開いている間だけ中継を叩く）。中継に届かなくても 500 に
せず、手元の最新を返して `relay.ok=false` と理由を添える。
"""

from __future__ import annotations

from fastapi import FastAPI

from ...remote import relay, store
from .._common import WebContext, open_conn, require_writable, table_exists


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

    @app.post("/api/v1/sessions/{session_id}/ack")
    def sessions_ack(session_id: str) -> dict[str, object]:
        """「あなたの次」を済みにする（主人がカードの「済んだ」を押した）。中継に届かなくても
        manor 側の記録は残す（`relayed=false`）——セッションへの伝達だけが遅れる。"""
        require_writable(ctx)
        with open_conn(ctx) as conn:
            text = store.ack(conn, session_id)
            conn.commit()
        if text is None:
            return {"ok": False, "error": "no_human_next"}
        relayed = True
        if relay.configured():
            try:
                relay.push_ack(session_id, text)
            except relay.RelayError:
                relayed = False
        return {"ok": True, "text": text, "relayed": relayed}

    @app.post("/api/v1/sessions/{session_id}/hold")
    def sessions_hold(session_id: str) -> dict[str, object]:
        """「あなたの次」を保留にする（ダッシュボード上の整理。セッションには伝えない）。"""
        require_writable(ctx)
        with open_conn(ctx) as conn:
            text = store.ack(conn, session_id, kind="hold")
            conn.commit()
        return {"ok": text is not None, "text": text}

    @app.delete("/api/v1/sessions/{session_id}/hold")
    def sessions_unhold(session_id: str) -> dict[str, object]:
        require_writable(ctx)
        with open_conn(ctx) as conn:
            n = store.unhold(conn, session_id)
            conn.commit()
        return {"ok": True, "removed": n}

    @app.post("/api/v1/sessions/{session_id}/close")
    def sessions_close(session_id: str) -> dict[str, object]:
        """「終了にする」（主人の発言が来れば自然に外れる）。"""
        require_writable(ctx)
        with open_conn(ctx) as conn:
            ok = store.close(conn, session_id)
            conn.commit()
        return {"ok": ok}

    @app.delete("/api/v1/sessions/{session_id}/close")
    def sessions_reopen(session_id: str) -> dict[str, object]:
        require_writable(ctx)
        with open_conn(ctx) as conn:
            n = store.reopen(conn, session_id)
            conn.commit()
        return {"ok": True, "removed": n}
