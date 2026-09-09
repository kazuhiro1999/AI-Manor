"""担当の正面画像（サムネイル）。**一覧に VRM を読ませないための仕組み**（2026-09-09）。

主人のご提案:

> この画面は正直 vrm は読み込まなくても、正面画像をサーバーに保存するようにすれば
> 画像だけで済むと思います。小窓は vrm が動いてほしいですが、一覧は画像で十分です。
> vrm を差し替えた時だけ、表示画像も撮り直しておき、それを表示します。

⚠ **一覧はそれまで、担当ごとに VRM（18MB）を丸ごと読み、three.js で1フレームだけ描き、
`toDataURL()` で1枚焼いて、捨てていました。** 焼いた絵はブラウザの変数にしか無いので、
画面を開き直すたびに全部やり直し——担当が2人なら毎回 35MB です。

**撮るのはブラウザのままにします**（サーバで VRM を描くには headless GL が要る）。
変えたのは「焼いた絵を捨てない」ことだけ:

1. 一覧はまず `GET /face/thumbnail.png?agent=` を試す（数十KB・304 が効く）
2. 無ければ従来どおり VRM から焼き、**焼けたら `POST` してサーバに残す**
3. 姿を差し替えたら**その担当の絵を消す**（`POST /api/v1/face/model` の中で）。
   次に誰かが一覧を開いたときに撮り直される

つまり **18MB を読むのは「姿を差し替えた後、最初に一覧を開いた1人」だけ**になります。
"""

from __future__ import annotations

import base64
import binascii
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from .._common import WebContext, require_writable
from ..face import _require_agent, _resolved_under
from ..vrm_cache import vrm_response

#: PNG の魔法数。**拡張子でも Content-Type でも判定しない**（VRM の受け口と同じ作法）。
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

#: 正面画像の上限。三面図でも数百KB で収まるので、これを超えるものは何かが違う。
_MAX_BYTES = 4 * 1024 * 1024

#: `data:image/png;base64,` の頭。画面の `canvas.toDataURL("image/png")` がこの形で返す。
_DATA_URL_PREFIX = "data:image/png;base64,"


class ThumbnailIn(BaseModel):
    agent: str
    data_url: str


def thumbnail_path(home: Path, agent: str) -> Path | None:
    """`home/face/<agent>.png`。`home/face/` の外へ解決されるなら `None`。"""
    face_dir = Path(home) / "face"
    return _resolved_under(face_dir / f"{agent}.png", face_dir)


def drop_thumbnail(home: Path, agent: str) -> bool:
    """姿を差し替えたときに古い絵を捨てる。**消えたかどうかを返す**（黙って失敗しない）。"""
    path = thumbnail_path(home, agent)
    if path is None or not path.is_file():
        return False
    path.unlink()
    return True


def register(app: FastAPI, ctx: WebContext) -> None:
    @app.get("/face/thumbnail.png", include_in_schema=False)
    def face_thumbnail(request: Request, agent: str = "butler") -> Response:
        """正面画像。**無ければ 404**——一覧はそれを見て VRM から焼く側へ倒す。"""
        _require_agent(agent)
        path = thumbnail_path(ctx.home, agent)
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail=f"正面画像がありません（home/face/{agent}.png）")
        # 配り方（Cache-Control・304）は姿と同じ関数を通す——2箇所に書かない
        return vrm_response(request, path, media_type="image/png")

    @app.post("/api/v1/face/thumbnail")
    def put_thumbnail(body: ThumbnailIn) -> dict[str, object]:
        """一覧が焼いた絵を受け取って残す。

        ⚠ **中身で確かめる**（先頭8バイトが PNG の魔法数か）。`data:` の頭を信じない。
        """
        require_writable(ctx)
        _require_agent(body.agent)

        raw = body.data_url
        if not raw.startswith(_DATA_URL_PREFIX):
            raise HTTPException(status_code=400, detail="data:image/png;base64, で始まっていません")
        try:
            blob = base64.b64decode(raw[len(_DATA_URL_PREFIX):], validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=400, detail="base64 として読めません") from None
        if not blob.startswith(_PNG_MAGIC):
            raise HTTPException(status_code=400, detail="PNG として読めません（先頭が PNG の魔法数ではありません）")
        if len(blob) > _MAX_BYTES:
            raise HTTPException(status_code=400, detail=f"大きすぎます（上限 {_MAX_BYTES // (1024 * 1024)}MB）")

        face_dir = ctx.home / "face"
        face_dir.mkdir(parents=True, exist_ok=True)
        path = thumbnail_path(ctx.home, body.agent)
        if path is None:
            raise HTTPException(status_code=400, detail="保存先が home/face/ の外を指しています")
        # 途中で落ちても壊れた PNG を残さない（VRM の受け口と同じ作法）
        tmp = path.with_suffix(".png.writing")
        tmp.write_bytes(blob)
        tmp.replace(path)
        return {"agent": body.agent, "bytes": len(blob)}
