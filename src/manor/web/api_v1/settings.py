"""`settings`（ADR-005 §2「night／settings」）。`home/config.toml` の読み書き。

passcode は**書くだけ・返さない**（漏らさない）。`[notify]` は `notify.py` が読む節と
同じキー（`quiet_from` / `quiet_to`）——ここで書いても壊さない（`config.write_config` は
節ごと丸ごと差し替えるので、他のキー・他の節はそのまま残る）。

`[manor] language`（ADR-012 §3 D11）: 画面の言語（`auto`/`ja`/`en`）。**`[web]` ではなく
新しい `[manor]` という節にする**——言語は画面だけのものではなく、CLI の出力にも効く
（5h-2 で使う想定）ため。ここは書き読みの両方を持つ（passcode と違い秘密ではないので
そのまま返してよい）。`GET /api/v1/meta` にも同じ値を出す（そちらは認証なしで読めるので、
login・setup 画面の初期表示に使う。ここでの変更が正になる）。
"""

from __future__ import annotations

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from .. import auth as auth_mod
from .. import config as web_config
from .._common import WebContext, manor_error_to_http, module_list, open_conn, require_writable
from ...errors import ManorError


class NotifySettings(BaseModel):
    quiet_from: int | None = None
    quiet_to: int | None = None


class WebSettings(BaseModel):
    passcode: str | None = None
    #: ADR-013 D2: `[web] require_passcode`。締め出しを防ぐ2つの検算（未設定のまま on
    #: にできない／非ループバックで待ち受け中は off にできない）は `put_settings` 側でやる
    #: （画面のボタンを塞ぐだけでは API から直接叩けば抜けられるため。両方で塞ぐ）。
    require_passcode: bool | None = None


class ManorSettings(BaseModel):
    # Literal にしておくと語彙外の値は FastAPI が 422 で弾く（config.VALID_LANGUAGES と
    # 同じ語彙をここでも型として持つ——二重管理だが、pydantic の型は文字列にできない）。
    language: Literal["auto", "ja", "en"] | None = None


class SettingsUpdateRequest(BaseModel):
    notify: NotifySettings | None = None
    web: WebSettings | None = None
    manor: ManorSettings | None = None


def _read_settings(ctx: WebContext) -> dict[str, object]:
    cfg = web_config.read_config(ctx.home)
    notify_cfg = cfg.get("notify") if isinstance(cfg.get("notify"), dict) else {}
    with open_conn(ctx) as conn:
        modules = module_list(conn)
    return {
        "notify": {
            "quiet_from": notify_cfg.get("quiet_from", 22),
            "quiet_to": notify_cfg.get("quiet_to", 7),
            "has_speak_command": bool(notify_cfg.get("speak_command")),
        },
        "web": {
            "has_passcode": web_config.has_passcode(ctx.home),
            "require_passcode": web_config.get_require_passcode(ctx.home),
            # ADR-013 D2: 画面が「今ループバックで待ち受けているか」を知るための値
            # （非ループバックで待ち受け中は require_passcode を off にできない、の判定材料）。
            "is_loopback": auth_mod.is_loopback(ctx.host),
            "host": ctx.host,
        },
        "manor": {"language": web_config.get_manor_language(ctx.home)},
        "modules": modules,
    }


def register(app: FastAPI, ctx: WebContext) -> None:
    @app.get("/api/v1/settings")
    def get_settings() -> dict[str, object]:
        return _read_settings(ctx)

    @app.put("/api/v1/settings")
    def put_settings(body: SettingsUpdateRequest) -> dict[str, object]:
        require_writable(ctx)
        if body.notify is not None:
            updates: dict[str, object] = {}
            if body.notify.quiet_from is not None:
                updates["quiet_from"] = body.notify.quiet_from
            if body.notify.quiet_to is not None:
                updates["quiet_to"] = body.notify.quiet_to
            if updates:
                web_config.update_section(ctx.home, "notify", updates)
        if body.web is not None:
            if body.web.passcode is not None:
                web_config.set_passcode(ctx.home, body.web.passcode)
            if body.web.require_passcode is not None:
                # ADR-013 D2「締め出しを防ぐ」の2つの検算。**API 側で拒む**——画面のボタンを
                # 塞ぐだけだと `curl`/直接の PUT から抜けられる（主人自身を締め出す事故を防ぐ
                # のが目的なので、画面の防御だけでは足りない）。
                if body.web.require_passcode and not web_config.has_passcode(ctx.home):
                    raise manor_error_to_http(
                        ManorError("passcode が未設定です。先にパスコードを設定してから要求を有効にしてください")
                    )
                if not body.web.require_passcode and not auth_mod.is_loopback(ctx.host):
                    raise manor_error_to_http(
                        ManorError("ループバック以外で待ち受けている間は解除できません（自分を締め出すのを防ぐため）")
                    )
                web_config.set_require_passcode(ctx.home, body.web.require_passcode)
        if body.manor is not None and body.manor.language is not None:
            web_config.set_manor_language(ctx.home, body.manor.language)
        return _read_settings(ctx)
