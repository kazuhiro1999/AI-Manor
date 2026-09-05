"""`settings`（ADR-005 §2）の試験。passcode を返さないこと・他の節を壊さないこと。
**合成データのみ**。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from manor.web import app as web_app_mod
from manor.web import auth as auth_mod
from manor.web import config as web_config


def make_client(home: Path, *, read_only: bool = False, host: str = "127.0.0.1") -> TestClient:
    return TestClient(web_app_mod.create_app(home, host=host, read_only=read_only))


def test_settings_shape_defaults(home: Path) -> None:
    client = make_client(home)
    body = client.get("/api/v1/settings").json()
    assert body["notify"]["quiet_from"] == 22
    assert body["notify"]["quiet_to"] == 7
    assert body["web"]["has_passcode"] is False
    assert body["manor"]["language"] == "auto"
    assert "modules" in body


# --- [manor] language（ADR-012 §3 D11） -----------------------------------------------


def test_settings_put_manor_language(home: Path) -> None:
    client = make_client(home)
    res = client.put("/api/v1/settings", json={"manor": {"language": "en"}})
    assert res.status_code == 200
    assert res.json()["manor"]["language"] == "en"

    # 実際に config.toml へ書かれ、GET でも読み返せる
    assert web_config.get_manor_language(home) == "en"
    assert client.get("/api/v1/settings").json()["manor"]["language"] == "en"


def test_settings_put_manor_language_rejects_unknown_value(home: Path) -> None:
    client = make_client(home)
    res = client.put("/api/v1/settings", json={"manor": {"language": "fr"}})
    assert res.status_code == 422  # pydantic の Literal が語彙外を弾く
    # 弾かれたので config.toml には反映されない（既定 auto のまま）
    assert web_config.get_manor_language(home) == "auto"


def test_settings_put_manor_language_does_not_clobber_other_sections(home: Path) -> None:
    """language を書いても notify/web の既存の値は残る（他の節を壊さない）。"""
    client = make_client(home)
    client.put("/api/v1/settings", json={"notify": {"quiet_from": 23, "quiet_to": 6}})
    res = client.put("/api/v1/settings", json={"manor": {"language": "ja"}})
    assert res.status_code == 200
    body = res.json()
    assert body["notify"]["quiet_from"] == 23
    assert body["notify"]["quiet_to"] == 6
    assert body["manor"]["language"] == "ja"


def test_settings_read_only_blocks_manor_language_put(home: Path) -> None:
    client = make_client(home, read_only=True)
    res = client.put("/api/v1/settings", json={"manor": {"language": "en"}})
    assert res.status_code == 403


def test_settings_put_passcode_does_not_return_it(home: Path) -> None:
    client = make_client(home)
    res = client.put("/api/v1/settings", json={"web": {"passcode": "himitsu-desu"}})
    assert res.status_code == 200
    body = res.json()
    assert "passcode" not in body["web"]
    assert body["web"]["has_passcode"] is True
    assert "himitsu-desu" not in str(body)

    # 実際に設定されている
    # **平文は保存していない**ので読み戻せない（2026-09-05。`web/passcode.py`）。
    # 「設定されたか」は照合で確かめる——これが本来見たかったことでもある。
    assert auth_mod.verify_passcode(home, "himitsu-desu") is True
    assert auth_mod.verify_passcode(home, "chigau-passcode") is False


def test_settings_put_notify_quiet_hours(home: Path) -> None:
    client = make_client(home)
    res = client.put("/api/v1/settings", json={"notify": {"quiet_from": 23, "quiet_to": 6}})
    assert res.status_code == 200
    body = res.json()
    assert body["notify"]["quiet_from"] == 23
    assert body["notify"]["quiet_to"] == 6


def test_settings_put_does_not_clobber_other_sections(home: Path) -> None:
    """`[notify]` に `speak_command` が既にある状態で `[web] passcode` を書いても、
    `speak_command` は残る（既存の他の節を壊さない）。
    """
    web_config.update_section(home, "notify", {"speak_command": "say {text}", "quiet_from": 22, "quiet_to": 7})
    client = make_client(home)
    res = client.put("/api/v1/settings", json={"web": {"passcode": "himitsu-desu"}})
    assert res.status_code == 200

    cfg = web_config.read_config(home)
    assert cfg["notify"]["speak_command"] == "say {text}"
    assert cfg["notify"]["quiet_from"] == 22
    # passcode は `config.toml` に書かない（秘密の置き場へ。2026-09-05）。
    # この試験が見たいのは「他の節を壊さないこと」なので、設定できたことは照合で確かめる。
    assert auth_mod.verify_passcode(home, "himitsu-desu") is True


def test_settings_read_only_blocks_put(home: Path) -> None:
    client = make_client(home, read_only=True)
    res = client.put("/api/v1/settings", json={"web": {"passcode": "だめ"}})
    assert res.status_code == 403


def test_settings_get_never_leaks_passcode(home: Path) -> None:
    web_config.set_passcode(home, "zettai-mienai")
    client = make_client(home)
    res = client.get("/api/v1/settings")
    assert "zettai-mienai" not in res.text


# --- [web] require_passcode（ADR-013 D2:「締め出しを防ぐ」を API で直接叩いて確かめる） -----------
#
# 画面のボタンを塞ぐだけでは `curl`/直接の PUT から抜けられる——ここでは TestClient で
# 直接 PUT を叩き、**画面を経由しない**経路でも拒まれることを確かめる。


def test_require_passcode_on_without_passcode_is_rejected(home: Path) -> None:
    """passcode 未設定のまま require_passcode を on にはできない（自分を締め出す事故）。"""
    client = make_client(home)
    assert web_config.has_passcode(home) is False
    res = client.put("/api/v1/settings", json={"web": {"require_passcode": True}})
    assert res.status_code == 400
    # 実際に config.toml へは反映されていない。
    assert web_config.get_require_passcode(home) is False


def test_require_passcode_on_with_passcode_set_in_same_request_succeeds(home: Path) -> None:
    """同じリクエストで passcode を設定しつつ require_passcode を on にするのは許す
    （passcode を先に処理してから on/off を検算する順序であることの検算）。"""
    client = make_client(home)
    res = client.put(
        "/api/v1/settings",
        json={"web": {"passcode": "atarashii-passcode", "require_passcode": True}},
    )
    assert res.status_code == 200
    assert web_config.get_require_passcode(home) is True


def test_require_passcode_on_with_passcode_already_set_succeeds(home: Path) -> None:
    web_config.set_passcode(home, "himitsu")
    client = make_client(home)
    res = client.put("/api/v1/settings", json={"web": {"require_passcode": True}})
    assert res.status_code == 200
    assert res.json()["web"]["require_passcode"] is True
    assert web_config.get_require_passcode(home) is True


def test_require_passcode_off_while_listening_non_loopback_is_rejected(home: Path) -> None:
    """ループバック以外（Tailscale 経由等）で待ち受けている最中は off にできない
    （外に晒したまま鍵を外せない）。`host="0.0.0.0"` で app を作るのが「非ループバックで
    待ち受け中」の模擬——web/_common.py の `WebContext.host` がそのまま bind host を表す。
    """
    web_config.set_passcode(home, "himitsu")
    web_config.set_require_passcode(home, True)
    client = make_client(home, host="0.0.0.0")
    # host が非ループバックなので、この検算自体の前に一度ログインが要る
    # （`_AuthMiddleware` が /api/v1/settings を素通りさせないため）。
    login = client.post("/api/v1/auth/login", json={"passcode": "himitsu"})
    assert login.status_code == 200
    res = client.put("/api/v1/settings", json={"web": {"require_passcode": False}})
    assert res.status_code == 400
    # 実際に off へは反映されていない。
    assert web_config.get_require_passcode(home) is True


def test_require_passcode_off_while_loopback_succeeds(home: Path) -> None:
    web_config.set_passcode(home, "himitsu")
    web_config.set_require_passcode(home, True)
    client = make_client(home, host="127.0.0.1")
    # require_passcode=true は「ループバックでも passcode を要る」を意味する
    # （tailscale.py の理由と同じ）ので、ここでもログインしてから off にする。
    login = client.post("/api/v1/auth/login", json={"passcode": "himitsu"})
    assert login.status_code == 200
    res = client.put("/api/v1/settings", json={"web": {"require_passcode": False}})
    assert res.status_code == 200
    assert web_config.get_require_passcode(home) is False


def test_settings_shape_includes_require_passcode_and_is_loopback(home: Path) -> None:
    client = make_client(home)
    body = client.get("/api/v1/settings").json()
    assert body["web"]["require_passcode"] is False
    assert body["web"]["is_loopback"] is True
