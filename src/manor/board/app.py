"""FastAPI アプリ本体。起動・静的配信・姿の小窓・health だけをここに置く。

`/api/...` は `api_core.py`（task/project/decision/handoff/ctx）・
`api_staff.py`（部下4名）・`api_night.py`（夜勤の作業報告。GET のみ）に分ける
（ROADMAP §7「画面＝機能モジュールの集合」）。

board 自身は外部ネットワークへ出ない。CDN も使わない（JS/CSS/3Dライブラリは同梱）。
"""

from __future__ import annotations

import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from ._common import BoardContext

STATIC_DIR = Path(__file__).resolve().parent / "static"
#: `.py` の更新時刻を見る範囲。board 自身（`src/manor/board/`）も含めた `src/manor/` 全体。
#: v1 B184 の教訓（「サーバ側が古いまま動いている」）を画面が自分で言うための材料。
_SOURCE_ROOT = Path(__file__).resolve().parent.parent

#: このプロセスが起きた時刻。Python の変更は再起動しないと効かない。
_STARTED_AT = datetime.now()


def _runtime_stale() -> bool:
    """配信しているコードが、いま動いているプロセスより新しくないか。

    静的ファイル（JS/CSS）はディスクから毎回読むので直せばすぐ届くが、`.py` は
    起動時に読み込まれたきり。1分の余裕を持たせる（起動直前の保存で誤検出しない）。
    """
    try:
        latest = max(
            (p.stat().st_mtime for p in _SOURCE_ROOT.rglob("*.py") if "__pycache__" not in p.parts),
            default=0.0,
        )
    except OSError:
        latest = 0.0
    started = _STARTED_AT.timestamp()
    return bool(latest and latest > started + 60)


class NoCacheStatic(StaticFiles):
    """静的ファイルを毎回検証させる（`Cache-Control: no-cache`）。

    ローカル専用の道具なので、キャッシュの得より「更新が確実に届くこと」を取る
    （v1 と同じ理由。butler-board `app.py` の `NoCacheStatic` を踏襲）。
    """

    async def get_response(self, path: str, scope):  # type: ignore[no-untyped-def]
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


def create_app(home: Path, *, read_only: bool = False) -> FastAPI:
    # 起動時に冪等な移行を当てる（web/app.py と同じ理由。読み取り専用なら触らない）
    from .. import db as db_mod

    if (Path(home) / "manor.db").is_file() and not read_only:
        db_mod.init(Path(home))
    ctx = BoardContext(home=Path(home), read_only=read_only)
    app = FastAPI(
        title="manor-board",
        description="manor のローカルダッシュボード（LLM API 不使用・外部送信なし）",
    )
    app.state.board_ctx = ctx

    from . import api_core, api_night, api_staff

    api_core.register(app, ctx)
    api_staff.register(app, ctx)
    api_night.register(app, ctx)

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {
            "ok": True,
            "home": str(ctx.home),
            "read_only": ctx.read_only,
            "started_at": _STARTED_AT.isoformat(timespec="seconds"),
            "stale": _runtime_stale(),
        }

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "index.html",
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )

    @app.get("/face")
    def face() -> FileResponse:
        """執事の姿。画面の隅に置く小さな窓。別サーバを立てず board に同居させる
        （状態の出どころが同じで、同一オリジンなので CORS も起きない）。
        """
        return FileResponse(
            STATIC_DIR / "face.html",
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )

    @app.get("/face/model.vrm")
    def face_model() -> FileResponse:
        """姿（VRM）。`home/face/model.vrm` にあれば配る。主人の資産なのでリポジトリには
        入れない（board の static には置かない）。無ければ 404 —— 小窓側は3行だけで足りる。
        """
        p = ctx.home / "face" / "model.vrm"
        if not p.is_file():
            raise HTTPException(status_code=404, detail="VRM が置かれていません（home/face/model.vrm）")
        return FileResponse(p, media_type="model/gltf-binary")

    @app.get("/favicon.ico")
    def favicon() -> Response:
        return Response(status_code=204)

    app.mount("/static", NoCacheStatic(directory=STATIC_DIR), name="static")
    return app


def run_server(
    *,
    home: Path,
    host: str = "127.0.0.1",
    port: int = 8788,
    read_only: bool = False,
    open_browser: bool = False,
) -> None:
    """`manor board` / `python -m manor.board` の本体。呼ぶとブロックする。

    既定の待ち受けは `127.0.0.1`。`--host` は将来 Tailscale の IP にだけ待ち受けるための
    ものだが、**`0.0.0.0` を既定にはしない**（docs/board.md に理由を書く）。
    """
    import uvicorn

    app = create_app(home, read_only=read_only)

    if open_browser:
        url = f"http://{host}:{port}/"

        def _open() -> None:
            time.sleep(1.0)
            try:
                webbrowser.open(url)
            except Exception:
                pass

        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run(app, host=host, port=port, log_level="info")
