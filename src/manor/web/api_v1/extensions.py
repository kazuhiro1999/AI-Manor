"""`extensions`（拡張機能。ADR-009 D6）。

秘密は**書くだけ・返さない**（`settings.py` の passcode と同じ規則）。`PUT`/`POST test`/
`DELETE` の応答は `GET /{id}` と同じ「detail」の形（`{id, manifest, values, install_steps,
status, checked_at, reason}`）に統一する——フロントが保存・試す・削除の直後にその場で
カードを更新できるように（別途 `GET` を挟まなくてよい）。

書き込み（`PUT`/`POST test`/`DELETE`）は `require_writable`。**秘密を含む経路も含めて
ループバックでも passcode を要求しない**——認証の軸は既存のまま（ADR-005 D4・ADR-009 D6）。
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from ... import extensions as extensions_mod
from ...errors import ManorError
from .._common import WebContext, manor_error_to_http, require_writable


class ExtensionSettingsUpdate(BaseModel):
    #: 部分更新。manifest の fields にないキーは無視される（extensions_mod.save_settings）。
    values: dict[str, str | int | float | bool | None] = {}


def register(app: FastAPI, ctx: WebContext) -> None:
    @app.get("/api/v1/extensions")
    def list_extensions() -> list[dict[str, object]]:
        return extensions_mod.statuses(ctx.home)

    @app.get("/api/v1/extensions/{ext_id}")
    def get_extension(ext_id: str) -> dict[str, object]:
        try:
            return extensions_mod.detail(ctx.home, ext_id)
        except ManorError as exc:
            raise manor_error_to_http(exc)

    @app.put("/api/v1/extensions/{ext_id}")
    def put_extension(ext_id: str, body: ExtensionSettingsUpdate) -> dict[str, object]:
        require_writable(ctx)
        try:
            extensions_mod.save_settings(ctx.home, ext_id, dict(body.values))
            return extensions_mod.detail(ctx.home, ext_id)
        except ManorError as exc:
            raise manor_error_to_http(exc)

    @app.post("/api/v1/extensions/{ext_id}/test")
    def test_extension(ext_id: str) -> dict[str, object]:
        require_writable(ctx)
        try:
            extensions_mod.test(ctx.home, ext_id)
            return extensions_mod.detail(ctx.home, ext_id)
        except ManorError as exc:
            raise manor_error_to_http(exc)

    @app.get("/api/v1/extensions/{ext_id}/options/{name}")
    def get_extension_options(ext_id: str, name: str) -> list[dict[str, object]]:
        try:
            extensions_mod.get(ext_id)  # 404 を先に確かめる（無い id への options は 404 にする）
        except ManorError as exc:
            raise manor_error_to_http(exc)
        return extensions_mod.options(ctx.home, ext_id, name)

    @app.delete("/api/v1/extensions/{ext_id}")
    def delete_extension(ext_id: str) -> dict[str, object]:
        require_writable(ctx)
        try:
            extensions_mod.forget(ctx.home, ext_id)
            return extensions_mod.detail(ctx.home, ext_id)
        except ManorError as exc:
            raise manor_error_to_http(exc)
