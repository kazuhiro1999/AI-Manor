"""LAN の規則（ADR-017 D4）の試験。`--host 0.0.0.0` を実用上安全にする門。

ADR-017 D4 の表をそのまま並べる:

| 口 | loopback・tailnet | LAN |
|---|---|---|
| Web の頁・合言葉のログイン | 可 | 不可（403。`lan_passcode_login` で許す） |
| `/api/v1/devices/pair/*` | 可 | 可 |
| 端末鍵つきの `/api/v1/kitchen/*` | 可 | 可 |
| それ以外の API | 可（cookie） | 不可 |

**既存の tailscale 経由の使い方を壊さないことを、ここで見張る**（送信元 100.x からの
合言葉ログインが通る・`tailscale serve` の転送（ループバック＋`X-Forwarded-For`）も通る）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from manor.web import app as web_app_mod
from manor.web import config as web_config
from manor.web import device as device_mod
from manor.web import net as net_mod

PASSCODE = "himitsu-desu"

#: 送信元の例（合成。実在の機械を指さない）。
SRC_LOOPBACK = ("127.0.0.1", 50000)
SRC_TAILNET = ("100.100.1.5", 41234)
SRC_LAN = ("192.168.50.9", 51234)


@pytest.fixture(autouse=True)
def _fresh_caches():
    device_mod.reset_caches()
    yield
    device_mod.reset_caches()


def make_app(home: Path, *, host: str = "0.0.0.0"):
    """非ループバックで待ち受けている manor（合言葉あり）。"""
    web_config.set_passcode(home, PASSCODE)
    return web_app_mod.create_app(home, host=host)


def client_from(app, src) -> TestClient:
    return TestClient(app, client=src)


# --- 区分（`web/net.py`） -----------------------------------------------------------


def test_classify_host_three_zones() -> None:
    assert net_mod.classify_host("127.0.0.1") == net_mod.ZONE_LOOPBACK
    assert net_mod.classify_host("::1") == net_mod.ZONE_LOOPBACK
    assert net_mod.classify_host("100.100.1.5") == net_mod.ZONE_TAILNET
    assert net_mod.classify_host("100.64.0.1") == net_mod.ZONE_TAILNET
    assert net_mod.classify_host("100.127.255.254") == net_mod.ZONE_TAILNET
    assert net_mod.classify_host("192.168.1.20") == net_mod.ZONE_LAN
    assert net_mod.classify_host("100.128.0.1") == net_mod.ZONE_LAN  # 100.64/10 の外
    assert net_mod.classify_host("::ffff:100.100.1.5") == net_mod.ZONE_TAILNET


def test_unreadable_source_is_treated_as_lan() -> None:
    """読めない送信元はいちばん狭い側（LAN）に倒す。"""
    assert net_mod.classify_host(None) == net_mod.ZONE_LAN
    assert net_mod.classify_host("testclient") == net_mod.ZONE_LAN


def test_forwarded_for_is_only_trusted_from_loopback() -> None:
    """`tailscale serve` の転送だけを拾い、それ以外の `X-Forwarded-For` は信用しない。"""
    # ループバックからの転送（tailscale serve）→ tailnet 扱い。
    assert net_mod.classify_host("127.0.0.1", "100.100.1.5") == net_mod.ZONE_TAILNET
    assert net_mod.classify_host("127.0.0.1", "100.100.1.5, 10.0.0.1") == net_mod.ZONE_TAILNET
    # LAN の相手が見出しを付けても化けられない。
    assert net_mod.classify_host("192.168.50.9", "100.100.1.5") == net_mod.ZONE_LAN
    # ループバック＋LAN の見出しは、ただのループバック。
    assert net_mod.classify_host("127.0.0.1", "192.168.50.9") == net_mod.ZONE_LOOPBACK


# --- D4 の表 -----------------------------------------------------------------------


def test_tailnet_can_log_in_and_use_the_api(home: Path) -> None:
    """**既存の tailscale 経由の使い方（Web も API も cookie）を壊さない。**"""
    client = client_from(make_app(home), SRC_TAILNET)
    assert client.post("/api/v1/auth/login", json={"passcode": PASSCODE}).status_code == 200
    assert client.get("/api/v1/meta").status_code == 200
    assert client.get("/api/v1/tasks/board").status_code == 200


def test_tailscale_serve_forwarding_can_log_in(home: Path) -> None:
    """`tailscale serve` はループバックから転送する（`X-Forwarded-For` に元の 100.x）。"""
    client = client_from(make_app(home), SRC_LOOPBACK)
    res = client.post(
        "/api/v1/auth/login",
        json={"passcode": PASSCODE},
        headers={"X-Forwarded-For": "100.100.1.5"},
    )
    assert res.status_code == 200


def test_lan_cannot_log_in(home: Path) -> None:
    """D4 の1行目: LAN からの合言葉のログインは 403（401 ではない——口自体を見せない）。"""
    client = client_from(make_app(home), SRC_LAN)
    assert client.post("/api/v1/auth/login", json={"passcode": PASSCODE}).status_code == 403


def test_lan_cannot_touch_other_apis_or_pages(home: Path) -> None:
    """D4 の4行目（それ以外の API）と1行目（Web の頁）。"""
    client = client_from(make_app(home), SRC_LAN)
    assert client.get("/api/v1/meta").status_code == 403
    assert client.get("/api/v1/tasks/board").status_code == 403
    assert client.get("/api/v1/kitchen/media").status_code == 403  # 鍵なしの台所も 403
    assert client.get("/").status_code == 403


def test_lan_can_use_pairing(home: Path) -> None:
    """D4 の2行目: ペアリングの口は LAN からも叩ける（主人が許可しない限り何も起きない）。"""
    client = client_from(make_app(home), SRC_LAN)
    start = client.post("/api/v1/devices/pair/start", json={"name": "Quest 3"})
    assert start.status_code == 200
    poll = client.post("/api/v1/devices/pair/poll", json={"pair_id": start.json()["pair_id"]})
    assert poll.json() == {"status": "pending"}
    # 許可は LAN からはできない（cookie が要る口なので 403）。
    approve = client.post(
        "/api/v1/devices/pair/approve", json={"code": start.json()["code"], "user_id": "master"}
    )
    assert approve.status_code == 403


def test_lan_can_use_kitchen_with_a_device_token(home: Path) -> None:
    """D4 の3行目: 鍵つきの台所は LAN から通る（XR の本番の経路）。"""
    app = make_app(home)
    lan = client_from(app, SRC_LAN)
    start = lan.post("/api/v1/devices/pair/start", json={"name": "Quest 3"}).json()

    # 主人はループバック（または tailnet）から許可する。
    master = client_from(app, SRC_LOOPBACK)
    assert master.post("/api/v1/auth/login", json={"passcode": PASSCODE}).status_code == 200
    assert master.post(
        "/api/v1/devices/pair/approve", json={"code": start["code"], "user_id": "master"}
    ).status_code == 200

    token = lan.post("/api/v1/devices/pair/poll", json={"pair_id": start["pair_id"]}).json()["token"]
    auth = {"Authorization": f"Bearer {token}"}
    assert lan.get("/api/v1/kitchen/media", headers=auth).status_code == 200
    # 範囲外は LAN でも 403（鍵は通っている）。
    assert lan.get("/api/v1/tasks/board", headers=auth).status_code == 403


def test_lan_passcode_login_opens_the_screen(home: Path) -> None:
    """`[web] lan_passcode_login = true` で LAN から合言葉で入れる（既定 false）。

    **追補（ADR-017 D4）**: この印は LAN を loopback・tailnet と同じ扱いにする
    ——ログインだけ通して他の API を止めると、入れた画面が全部 403 になる。
    """
    web_config.update_section(home, "web", {"lan_passcode_login": True})
    client = client_from(make_app(home), SRC_LAN)
    assert client.post("/api/v1/auth/login", json={"passcode": PASSCODE}).status_code == 200
    assert client.get("/api/v1/tasks/board").status_code == 200


def test_lan_rules_do_not_apply_when_bound_to_loopback(home: Path) -> None:
    """ループバックに待ち受けている間は D4 を効かせない（`tailscale serve` の既存の形）。

    `require_passcode = true` ＋ `host=127.0.0.1` が主人のいまの使い方で、そこへ
    LAN の規則を持ち込むと（転送元が読めない分だけ）壊れる。
    """
    web_config.set_passcode(home, PASSCODE)
    web_config.set_require_passcode(home, True)
    app = web_app_mod.create_app(home, host="127.0.0.1")
    # 送信元が何であれ（uvicorn がループバックしか受けないので届かない）門は cookie だけ見る。
    client = client_from(app, SRC_LAN)
    assert client.post("/api/v1/auth/login", json={"passcode": PASSCODE}).status_code == 200
    assert client.get("/api/v1/tasks/board").status_code == 200
