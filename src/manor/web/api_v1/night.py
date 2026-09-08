"""`night`（夜勤の閲覧のみ。ADR-005 §2）。既存の `board/night.py`（読んで見せるだけの解析器）
と `night/runner.py`（`status`）をそのまま呼ぶ。書き込み口はここにも置かない。
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from ...board import night as night_report_mod
from ...night import runner as night_runner
from .._common import WebContext


def register(app: FastAPI, ctx: WebContext) -> None:
    @app.get("/api/v1/night/reports")
    def reports() -> dict[str, object]:
        return {"dates": night_report_mod.list_report_dates(ctx.home)}

    @app.get("/api/v1/night/reports/{date}")
    def report_detail(date: str) -> dict[str, object]:
        text = night_report_mod.read_report_text(ctx.home, date)
        if text is None:
            raise HTTPException(status_code=404, detail=f"夜勤の報告が見つかりません: {date}")
        parsed = night_report_mod.parse_report(text, date_hint=date)
        return {"date": date, "text": text, "parsed": parsed}

    @app.get("/api/v1/night/status")
    def status() -> dict[str, object]:
        return night_runner.status(ctx.home)

    @app.get("/api/v1/night/review")
    def review() -> dict[str, object]:
        """朝の点検（走ったか・何が片付かなかったか・何晩続いたか）。

        ⚠ **`record=False`。** 画面を開くたびに連続日数を数え直すと水増しされます
        ——数えて書くのは朝の便（`manor slack morning`）の仕事で、ここは読むだけ
        （この画面に書き込み口を置かない、という `night` の約束にも合います）。
        """
        return night_runner.review(ctx.home, record=False)
