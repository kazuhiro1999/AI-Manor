"""`face/open`・`face/mouth`・`face/pin`（小窓の3つの機能。ADR-011 D5・D6・D7）。

主人の指摘「小窓が v1 から機能を落としている」への応答。ADR-011 §1 の訂正どおり、
v1 にはこの3つとも実装済みだった——執事の以前の棚卸しが README の古い一文だけを見て
「未実装」と誤って書いたもの。ここでは実体（`face.py`・`voice.py`・`face_pin.py`）を
薄くルーティングするだけで、判断はそちらに置く（`face_talk.py` と同じ役割分担）。

認証: 専用の分岐は書かない。この3経路とも `/api/v1/...` なので `web/app.py` の
`_is_guarded_path` の一般規則（`_PUBLIC_API_PATHS` に無い限り認証の内側）で**既にカバー
されている**（`face_talk.py` の同じコメント・`tests/web/test_face_talk.py`
`test_is_guarded_path_covers_all_four_routes` と同じ確認をこのファイルの試験でも行う）。

`require_writable`（`--read-only` の拒否）はどちらの POST にも課さない。**書き込みでは
ない**——`/face/open` は窓を開くだけ（D5 で明言）、`/face/pin` はブラウザからは触れない
OS の窓の重なり順を切り替えるだけで、`home/` には何も残らない（開くのと同じ「操作」で
あって「データの書き込み」ではない、という同じ理由をここでも適用する）。
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from ... import face as face_mod
from ... import face_pin
from ... import voice as voice_mod
from ...agent_meta import BUTLER, agent_label
from .._common import WebContext
from ..face import _require_agent


class FaceOpenRequest(BaseModel):
    agent: str = BUTLER


class FacePinRequest(BaseModel):
    agent: str = BUTLER
    pinned: bool = False


def _pin_shape(state: dict[str, object]) -> dict[str, object]:
    """`face_pin` の内部形（`found` を含む）を ADR-011 D7 の契約 `{supported, pinned}` へ絞る。"""
    return {"supported": bool(state.get("supported")), "pinned": bool(state.get("pinned"))}


def register(app: FastAPI, ctx: WebContext) -> None:
    @app.post("/api/v1/face/open")
    def face_open(body: FaceOpenRequest) -> dict[str, object]:
        """D5: ヘッダ無しで小窓を開く。`manor face` と同じ経路（`face.try_open_app_window`）で
        サーバ側から Chrome をアプリモードで起こす。**例外は投げない**——不明な担当だけ
        ここで 404（他の `/face/...` 系と同じ規則）にし、Chrome が無い・起動できない等は
        `{opened: False, ...}` を返すだけにする。
        """
        _require_agent(body.agent)
        try:
            return face_mod.try_open_app_window(agent=body.agent)
        except Exception as exc:  # noqa: BLE001 - 「開けなかった」を1行返すだけで落とさない
            return {"opened": False, "method": "none", "reason": f"予期しないエラー: {exc}"}

    @app.get("/api/v1/face/mouth")
    def face_mouth() -> dict[str, object]:
        """D6: 直近の口の形の予定表。小窓（`face-mouth.js`）が短い間隔で取りに来る
        （読み取り専用。`require_writable` は無い）。
        """
        return voice_mod.latest_mouth(ctx.home)

    @app.get("/api/v1/face/pin")
    def face_pin_get(agent: str = BUTLER) -> dict[str, object]:
        """D7: 固定されているか。窓は担当の日本語名（`agent_label`）で探す——ダッシュボードや
        他の担当の小窓とは題名が違うので取り違えない。
        """
        _require_agent(agent)
        if not face_pin.supported():
            return {"supported": False, "pinned": False}
        title = agent_label(agent)
        return _pin_shape({"supported": True, "pinned": face_pin.is_pinned(title)})

    @app.post("/api/v1/face/pin")
    def face_pin_post(body: FacePinRequest) -> dict[str, object]:
        """D7: 固定を切り替える。Windows 以外では `supported: False` を返すだけ
        （できないものは出さない。画面はボタンごと隠す）。

        窓が見つからなければ（`face_pin.set_pinned` が `None`）、`pinned` は変えず
        （既定の `False`）に `reason` を添えて返す——ページが「なぜ効かないか」を
        言えるように（ブラウザの実窓の題名は完全一致しないことが多い。`face_pin.py`
        の `_title_matches` 参照）。
        """
        _require_agent(body.agent)
        if not face_pin.supported():
            return {"supported": False, "pinned": False}
        title = agent_label(body.agent)
        result = face_pin.set_pinned(title, body.pinned)
        if result is None:
            return {"supported": True, "pinned": False, "reason": "窓が見つかりません"}
        return _pin_shape({"supported": True, "pinned": result})
