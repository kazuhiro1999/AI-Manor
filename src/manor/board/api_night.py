"""夜勤の作業報告（GET のみ）。`night.py` の一覧・解析を HTTP へ出す薄い層。

**読み取り専用。** DB には触らない（`home/night/reports/*.md` を読むだけ）。書き込み口
（報告そのものを書く仕組み）は別担当（`src/manor/night/**`）の領分で、ここには置かない。
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from . import night as night_mod
from ._common import BoardContext


def register(app: FastAPI, ctx: BoardContext) -> None:
    @app.get("/api/night/reports")
    def api_night_reports() -> dict[str, object]:
        return {"dates": night_mod.list_report_dates(ctx.home)}

    @app.get("/api/night/reports/{date}")
    def api_night_report(date: str) -> dict[str, object]:
        text = night_mod.read_report_text(ctx.home, date)
        if text is None:
            raise HTTPException(status_code=404, detail=f"夜勤の報告が見つかりません: {date}")
        parsed = night_mod.parse_report(text, date_hint=date)
        return {"date": date, "text": text, "parsed": parsed}
