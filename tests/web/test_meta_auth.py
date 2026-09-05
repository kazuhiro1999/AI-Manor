"""meta / 認証（ADR-005 §2 D4）の試験。**合成データのみ**。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from manor import task as task_mod
from manor.errors import ManorError
from manor.web import app as web_app_mod
from manor.web import config as web_config


def make_client(home: Path, *, host: str = "127.0.0.1", read_only: bool = False) -> TestClient:
    app = web_app_mod.create_app(home, host=host, read_only=read_only)
    return TestClient(app)


# --- meta の形 -----------------------------------------------------------------------------


def test_meta_shape(home: Path) -> None:
    client = make_client(home)
    res = client.get("/api/v1/meta")
    assert res.status_code == 200
    body = res.json()
    for key in ("version", "today", "read_only", "stale", "auth", "modules", "home_name", "language"):
        assert key in body
    assert body["auth"]["mode"] == "loopback"
    assert body["auth"]["authenticated"] is True
    module_ids = {m["id"] for m in body["modules"]}
    assert {"tasks", "kitchen", "house", "money", "secretary", "rules", "imports", "night", "settings"} <= module_ids
    # home のフルパスは返さない
    assert str(home) not in str(body)


def test_meta_language_defaults_to_auto_and_is_public(home: Path) -> None:
    """ADR-012 §3 D11: /meta は認証なしで読める唯一の経路——login・setup 画面が
    起動直後から言語を得られるように、認証モードが loopback でなくても読める。"""
    client = make_client(home)
    assert client.get("/api/v1/meta").json()["language"] == "auto"


def test_meta_language_reflects_config(home: Path) -> None:
    web_config.set_manor_language(home, "en")
    client = make_client(home)
    assert client.get("/api/v1/meta").json()["language"] == "en"


def test_meta_modules_enabled_reflects_staff_table_presence(conn, home: Path) -> None:
    """このリポジトリでは chef/house/money/secretary の4部下が実在するので、`db.init` の
    直後から表は揃っている（`enabled: true`）。表そのものを落として「導入されていない」を
    模し、`module_list` がそれを見て `enabled: false` に切り替えることを確かめる。
    """
    client = make_client(home)
    before = {m["id"]: m for m in client.get("/api/v1/meta").json()["modules"]}
    assert before["kitchen"]["enabled"] is True
    assert before["tasks"]["enabled"] is True  # core は常に有効

    conn.execute("DROP TABLE chef_pantry")
    conn.commit()
    after = {m["id"]: m for m in client.get("/api/v1/meta").json()["modules"]}
    assert after["kitchen"]["enabled"] is False


def test_health_shape(home: Path) -> None:
    client = make_client(home)
    body = client.get("/api/v1/health").json()
    assert body["ok"] is True
    assert "started_at" in body
    assert body["stale"] is False


# --- loopback は認証なし --------------------------------------------------------------------


def test_loopback_requires_no_auth_for_protected_endpoint(conn, home: Path) -> None:
    task_mod.add(conn, "何か")
    conn.commit()
    client = make_client(home, host="127.0.0.1")
    res = client.get("/api/v1/tasks/board")
    assert res.status_code == 200


def test_auth_me_loopback_is_authenticated(home: Path) -> None:
    client = make_client(home)
    body = client.get("/api/v1/auth/me").json()
    assert body == {"authenticated": True, "mode": "loopback"}


# --- 非 loopback: 401 -> login -> 200 --------------------------------------------------------


def test_non_loopback_blocks_protected_endpoint_until_login(home: Path) -> None:
    web_config.set_passcode(home, "himitsu-desu")
    client = make_client(home, host="0.0.0.0")

    res = client.get("/api/v1/tasks/board")
    assert res.status_code == 401

    login = client.post("/api/v1/auth/login", json={"passcode": "himitsu-desu"})
    assert login.status_code == 200
    assert "manor_session" in login.cookies

    res2 = client.get("/api/v1/tasks/board")
    assert res2.status_code == 200


def test_non_loopback_wrong_passcode_is_401(home: Path) -> None:
    web_config.set_passcode(home, "tadashii-passcode")
    client = make_client(home, host="0.0.0.0")
    res = client.post("/api/v1/auth/login", json={"passcode": "chigau-passcode"})
    assert res.status_code == 401


def test_non_loopback_meta_and_health_are_public(home: Path) -> None:
    """未認証でも `/api/v1/meta` `/api/v1/health` は見える（ログイン画面自体が出せないと詰む）。"""
    web_config.set_passcode(home, "himitsu-desu")
    client = make_client(home, host="0.0.0.0")
    assert client.get("/api/v1/meta").status_code == 200
    assert client.get("/api/v1/health").status_code == 200
    body = client.get("/api/v1/meta").json()
    assert body["auth"]["mode"] == "passcode"
    assert body["auth"]["authenticated"] is False


def test_auth_logout_clears_session(home: Path) -> None:
    web_config.set_passcode(home, "himitsu-desu")
    client = make_client(home, host="0.0.0.0")
    client.post("/api/v1/auth/login", json={"passcode": "himitsu-desu"})
    assert client.get("/api/v1/tasks/board").status_code == 200

    client.post("/api/v1/auth/logout")
    res = client.get("/api/v1/tasks/board")
    assert res.status_code == 401


# --- 5回超で429 ------------------------------------------------------------------------------


def test_login_rate_limited_after_five_attempts(home: Path) -> None:
    web_config.set_passcode(home, "himitsu-desu")
    client = make_client(home, host="0.0.0.0")
    for _ in range(5):
        res = client.post("/api/v1/auth/login", json={"passcode": "wrong"})
        assert res.status_code == 401
    res6 = client.post("/api/v1/auth/login", json={"passcode": "wrong"})
    assert res6.status_code == 429


# --- --read-only で POST 403 ------------------------------------------------------------------


def test_read_only_blocks_post_even_when_loopback(conn, home: Path) -> None:
    tid = task_mod.add(conn, "読み取り専用テスト")
    conn.commit()
    client = make_client(home, read_only=True)
    res = client.post(f"/api/v1/tasks/task/{tid}/status", json={"status": "doing", "note": ""})
    assert res.status_code == 403


# --- passcode 無しの非 loopback 起動が拒否される ------------------------------------------------


def test_check_startup_auth_rejects_non_loopback_without_passcode(home: Path) -> None:
    with pytest.raises(ManorError):
        web_app_mod.check_startup_auth(home, "0.0.0.0")


def test_check_startup_auth_allows_non_loopback_with_passcode(home: Path) -> None:
    web_config.set_passcode(home, "himitsu-desu")
    web_app_mod.check_startup_auth(home, "0.0.0.0")  # 例外を投げなければ良い


def test_check_startup_auth_allows_loopback_without_passcode(home: Path) -> None:
    web_app_mod.check_startup_auth(home, "127.0.0.1")  # 例外を投げなければ良い


# --- [web] require_passcode（tailscale serve 用。2026-09-03） -----------------------------


def test_require_passcode_forces_login_even_on_loopback(home: Path) -> None:
    web_config.set_passcode(home, "secret-1")
    web_config.update_section(home, "web", {"require_passcode": True})
    client = make_client(home)  # host は既定の 127.0.0.1（ループバック）
    meta = client.get("/api/v1/meta").json()
    assert meta["auth"]["mode"] == "passcode"
    assert meta["auth"]["authenticated"] is False
    assert client.get("/api/v1/tasks/board").status_code == 401
    assert client.post("/api/v1/auth/login", json={"passcode": "secret-1"}).status_code == 200
    assert client.get("/api/v1/tasks/board").status_code == 200


def test_require_passcode_without_passcode_refuses_startup(home: Path) -> None:
    web_config.update_section(home, "web", {"require_passcode": True})
    with pytest.raises(ManorError):
        web_app_mod.check_startup_auth(home, "127.0.0.1")
