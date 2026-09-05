"""`face/models`（姿の出し入れを画面から。ADR-008 §7 D14・D15。ROADMAP 5e）。

`home/face/<agent>.vrm` の手置きをやめ、「設定」画面から差し替え・削除できるようにする。
配る口（`GET /face/model.vrm`）や語彙（`_require_agent`）は `..face` にある正のものを
再利用する——ここで担当の語彙を作り直さない（D14「`agent` の語彙は `/face` と同じ」）。

書き込みの安全策は2つ:

1. **中身が VRM か確かめる**（先頭4バイトが glTF の魔法数 `b"glTF"`）。拡張子・
   Content-Type は信用しない
2. **一時ファイルへ書いてから `os.replace`**（`archive.py` と同じ順序の原則）。
   失敗（中身が違う・大きすぎる・書き込みエラー）のときは一時ファイルを消すだけで、
   既にある姿（`<agent>.vrm`）には一切触れない

サイズは 64MB を超えたら 413。`UploadFile.read(chunk)` を繰り返し読み、**一括で
`await file.read()` しない**（無制限に読み込まない。読みながら上限を判定する）。
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from ...agent_meta import agent_label, valid_agents
from .._common import WebContext, require_writable
from ..face import _require_agent, _resolved_under

#: VRM の実体は glTF バイナリ。先頭4バイトの魔法数（D14）。
_GLTF_MAGIC = b"glTF"

#: アップロードの上限（D14）。
_MAX_BYTES = 64 * 1024 * 1024

#: 一括で読まず、この大きさずつ読む（D14「無制限に読み込まない」）。
_CHUNK_SIZE = 1024 * 1024


def _face_dir_for_write(ctx: WebContext) -> Path:
    """書き込み用。`home/face/` が無ければ作る（読み取り側では作らない——GET/DELETE で
    ディレクトリを新設するのは副作用として意外なので、POST だけがここを呼ぶ）。
    """
    d = ctx.home / "face"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _stat_entry(agent: str, path: Path, *, legacy: bool) -> dict[str, object]:
    st = path.stat()
    return {
        "agent": agent,
        "label": agent_label(agent),
        "has_model": True,
        "size": st.st_size,
        "updated_at": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        "legacy": legacy,
    }


def _model_info(ctx: WebContext, agent: str) -> dict[str, object]:
    """一覧・POST・DELETE の応答で共通に使う1件分（D14 の形 + `legacy`）。

    `legacy` が立つのは butler だけ、かつ `butler.vrm` が無く `model.vrm` だけがある
    ときに限る（D15。他の担当には `model.vrm` フォールバックが無い——`face.py` と同じ規則）。
    """
    face_dir = ctx.home / "face"
    own = _resolved_under(face_dir / f"{agent}.vrm", face_dir)
    if own is not None and own.is_file():
        return _stat_entry(agent, own, legacy=False)

    if agent == "butler":
        legacy = _resolved_under(face_dir / "model.vrm", face_dir)
        if legacy is not None and legacy.is_file():
            return _stat_entry(agent, legacy, legacy=True)

    return {
        "agent": agent,
        "label": agent_label(agent),
        "has_model": False,
        "size": None,
        "updated_at": None,
        "legacy": False,
    }


def register(app: FastAPI, ctx: WebContext) -> None:
    @app.get("/api/v1/face/models")
    def list_models() -> list[dict[str, object]]:
        return [_model_info(ctx, agent) for agent in valid_agents()]

    @app.post("/api/v1/face/model")
    async def upload_model(agent: str = Form(...), file: UploadFile = File(...)) -> dict[str, object]:
        require_writable(ctx)
        _require_agent(agent)

        face_dir = _face_dir_for_write(ctx)
        target = _resolved_under(face_dir / f"{agent}.vrm", face_dir)
        if target is None:
            raise HTTPException(status_code=400, detail="保存先が home/face/ の外を指しています")

        # 先頭チャンクだけでまず中身を確かめる。拡張子でも Content-Type でもなく実体で見る。
        first = await file.read(_CHUNK_SIZE)
        if first[:4] != _GLTF_MAGIC:
            await file.close()
            raise HTTPException(
                status_code=400,
                detail=(
                    "VRM（glTF バイナリ）として読めません。先頭4バイトが glTF の魔法数ではありません"
                    "（拡張子は見ていません。中身を確かめています）。"
                ),
            )

        tmp_path = _resolved_under(face_dir / f".{agent}.vrm.uploading", face_dir)
        if tmp_path is None:
            await file.close()
            raise HTTPException(status_code=400, detail="保存先が home/face/ の外を指しています")

        total = 0
        ok = False
        try:
            with tmp_path.open("wb") as f:
                chunk = first
                while chunk:
                    total += len(chunk)
                    if total > _MAX_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail=f"ファイルが大きすぎます（上限 {_MAX_BYTES // (1024 * 1024)}MB）",
                        )
                    f.write(chunk)
                    chunk = await file.read(_CHUNK_SIZE)
            # ここまで来て初めて入れ替える。失敗（中身が違う・大きすぎる・書き込みエラー）は
            # すべて一時ファイルの後始末だけで済み、今ある姿は壊れない（archive.py と同じ順序）。
            os.replace(tmp_path, target)
            ok = True
        finally:
            await file.close()
            if not ok:
                tmp_path.unlink(missing_ok=True)

        return _model_info(ctx, agent)

    @app.delete("/api/v1/face/model")
    def delete_model(agent: str = "butler") -> dict[str, object]:
        require_writable(ctx)
        _require_agent(agent)

        face_dir = ctx.home / "face"
        own = _resolved_under(face_dir / f"{agent}.vrm", face_dir)
        if own is not None and own.is_file():
            own.unlink()
            return _model_info(ctx, agent)

        # `model.vrm`（後方互換の名前）はここでは消さない（D15）。旧い名前しか無いときは、
        # 画面から置き換える（アップロードする）よう案内するだけで、拒む。
        if agent == "butler":
            legacy = _resolved_under(face_dir / "model.vrm", face_dir)
            if legacy is not None and legacy.is_file():
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "旧い名前（home/face/model.vrm）はここから削除できません。"
                        "home/face/butler.vrm として新しい姿をアップロードして置き換えてください。"
                    ),
                )

        raise HTTPException(status_code=404, detail=f"姿が置かれていません（home/face/{agent}.vrm）")
