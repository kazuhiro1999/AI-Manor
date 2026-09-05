"""`/face`（姿の小窓。ADR-008 D2・D3・D4）。

v1・旧 board の `/face` を Web アプリ側へ戻す。**SPA には載せない**——Chrome の
アプリモードで 280×340 の窓に出すものなので、ナビも枠も要らない（D2）。
`web/app.py` の SPA キャッチオール（`/{full_path:path}`）より前に登録すること
（`create_app` 側の責務。ここでは順序を強制できないので、そこに注記がある）。

執事だけでなく担当ごとに姿を持てる（D3）: `home/face/<agent>.vrm`。`<agent>` の語彙は
`agent_meta.valid_agents`（`.claude/agents/*.md` + `butler`）に限り、語彙外は 404
（ADR-005 裁定7 と同じ規則）。`butler` だけ `home/face/model.vrm` へフォールバックする
（後方互換）。パスは必ず `home/face/` の下に解決されることを確かめてから開く
（`hooks._resolved_under_home` と同じ考え方——名前ではなく実体で比べる）。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .. import util
from ..agent_meta import agent_label, valid_agents
from ._common import WebContext

#: face.html・vendor 一式（`/face-static/...` として配る）。
FACE_STATIC_DIR = Path(__file__).resolve().parent / "face_static"

_NO_CACHE_HEADERS = {"Cache-Control": "no-cache, must-revalidate"}


def _require_agent(agent: str) -> str:
    """語彙外なら 404（ADR-005 裁定7: `ManorError(code=2)` は 404 で統一、と同じ扱い）。"""
    agents = valid_agents()
    if agent not in agents:
        raise HTTPException(
            status_code=404,
            detail=f"担当が見つかりません: {agent!r}（使えるのは {' / '.join(agents)}）",
        )
    return agent


def _resolved_under(candidate: Path, base: Path) -> Path | None:
    """`candidate` を実体まで解決して `base` の下にあるときだけ返す。`..` や symlink での
    脱出、`base` の外を指す絶対パスを拒む（`hooks._resolved_under_home` と同じ考え方）。
    """
    try:
        resolved = candidate.resolve()
        base_r = base.resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    try:
        resolved.relative_to(base_r)
    except ValueError:
        return None
    return resolved


def _face_html(agent: str) -> str:
    template = (FACE_STATIC_DIR / "face.html").read_text(encoding="utf-8")
    label = agent_label(agent)
    return template.replace("__AGENT_LABEL__", label)


def bundled_default_model() -> Path | None:
    """同梱の既定アバター（`assets/face/default.vrm`）。無ければ `None`。

    **リポジトリ本体（MIT）とは別のライセンス**で入っている（`assets/face/NOTICE.md`）。
    VRoid Studio の公式サンプルを改変したもので、無償の再配布は許されるが**有償の
    再配布は禁じられている**——同梱物を売り物に含めないこと。

    自分の姿を置いた担当には使わない（`home/face/<agent>.vrm` が常に優先）。
    """
    path = util.repo_root() / "assets" / "face" / "default.vrm"
    return path if path.is_file() else None


def register(app: FastAPI, ctx: WebContext) -> None:
    @app.get("/face", include_in_schema=False)
    def face(agent: str = "butler") -> HTMLResponse:
        _require_agent(agent)
        return HTMLResponse(_face_html(agent), headers=_NO_CACHE_HEADERS)

    @app.get("/face/model.vrm", include_in_schema=False)
    def face_model(agent: str = "butler") -> FileResponse:
        _require_agent(agent)
        face_dir = ctx.home / "face"
        expected = f"home/face/{agent}.vrm"

        candidate = _resolved_under(face_dir / f"{agent}.vrm", face_dir)
        if candidate is not None and candidate.is_file():
            return FileResponse(candidate, media_type="model/gltf-binary")

        # 後方互換: butler だけ home/face/model.vrm を読む（D3）。
        if agent == "butler":
            legacy = _resolved_under(face_dir / "model.vrm", face_dir)
            if legacy is not None and legacy.is_file():
                return FileResponse(legacy, media_type="model/gltf-binary")

            # 同梱の既定アバター（2026-09-05 主人の指示）。**執事だけ**に落とす——
            # 全担当に落とすと、姿を1体も置いていない家で7人が同じ顔になる。
            # 「姿がまだ無い担当は輪郭のまま」の区別（ADR-011 D3）を保つ。
            bundled = bundled_default_model()
            if bundled is not None:
                return FileResponse(bundled, media_type="model/gltf-binary")

        raise HTTPException(status_code=404, detail=f"姿が置かれていません（{expected}）")

    app.mount("/face-static", StaticFiles(directory=FACE_STATIC_DIR), name="face-static")
