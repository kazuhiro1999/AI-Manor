"""`/api/v1/sessions`（ADR-025 §10）。他のPCを含む Claude Code のセッションの一覧。

画面は10秒おきに読む。**一覧は手元の DB からすぐ返し**、前回の取り込みから10秒以上経っていれば
裏のスレッドで中継（GAS）から取り込む（GAS は1〜3秒、時々30秒かかる——2026-10-09 主人
「ボタンを押しても3秒反応が無い」）。取り込んだ分は次の読み込みで見える。中継に届かなくても
500 にせず、直近の裏の取り込みの失敗を `relay.ok=false` と理由で添える。
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
            status: dict[str, object] = {"configured": relay.configured(),
                                          "ok": relay.LAST_BACKGROUND["ok"],
                                          "error": relay.LAST_BACKGROUND["error"]}
            if relay.configured() and not ctx.read_only:
                relay.pull_in_background(ctx.home)
            items = store.list_sessions(conn, include_ended=bool(include_ended))
            state = relay.load_state(ctx.home)
            status["last_pull_ts"] = state.get("last_pull_ts")
            return {"items": items, "relay": status}

    @app.post("/api/v1/sessions/{session_id}/ack")
    def sessions_ack(session_id: str) -> dict[str, object]:
        """「あなたの次」を完了にする（主人がカードの「完了」を押した）。manor 側の記録は即座に。
        中継への送り出しは待ち行列に積んで裏で送る（失敗しても次の取り込みで送り直す）。"""
        require_writable(ctx)
        with open_conn(ctx) as conn:
            text = store.ack(conn, session_id)
            conn.commit()
        if text is None:
            return {"ok": False, "error": "no_human_next"}
        if relay.configured():
            relay.queue_ack(ctx.home, session_id, text)
            relay.pull_in_background(ctx.home, force=True)
        return {"ok": True, "text": text}

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
