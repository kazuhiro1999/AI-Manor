"""端末の鍵とペアリング（ADR-017 D1・D2）の試験。**合成データのみ**。

`tests/web/test_kitchen_media.py` と同じ流儀（`TestClient` を直に叩く）。
送信元は必ず名乗る——`TestClient` の既定（`"testclient"`）は `web/net.py` が LAN と
見るので、ここで見たい「鍵の往復」と LAN の規則（`test_lan_rules.py`）が混ざらないように、
ループバックから来たことにしてある。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from manor import user as user_mod
from manor.web import app as web_app_mod
from manor.web import device as device_mod
from manor.web._common import USER_COOKIE_NAME

LOOPBACK = ("127.0.0.1", 50000)


@pytest.fixture(autouse=True)
def _fresh_caches():
    """鍵の照合と `last_seen_at` の間引きはプロセス内に記憶を持つ（`web/device.py`）。
    試験どうしで持ち越さないように、前後で捨てる。
    """
    device_mod.reset_caches()
    yield
    device_mod.reset_caches()


def make_client(home: Path, *, read_only: bool = False) -> TestClient:
    return TestClient(
        web_app_mod.create_app(home, read_only=read_only), client=LOOPBACK
    )


def _pair(client: TestClient, *, name: str = "Quest 3", kind: str = "kitchenxr", user_id: str = "master") -> str:
    """発行 → 許可 → 受け取り。鍵（平文）を返す（D2 の3手をまとめた試験用の道具）。"""
    start = client.post("/api/v1/devices/pair/start", json={"name": name, "kind": kind})
    assert start.status_code == 200, start.text
    code = start.json()["code"]
    approve = client.post("/api/v1/devices/pair/approve", json={"code": code, "user_id": user_id})
    assert approve.status_code == 200, approve.text
    poll = client.post("/api/v1/devices/pair/poll", json={"pair_id": start.json()["pair_id"]})
    assert poll.status_code == 200, poll.text
    assert poll.json()["status"] == "approved"
    return str(poll.json()["token"])


# --- D2 ペアリング -----------------------------------------------------------------


def test_pair_start_shape(home: Path) -> None:
    client = make_client(home)
    res = client.post("/api/v1/devices/pair/start", json={"name": "Quest 3", "kind": "kitchenxr"})
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"pair_id", "code", "expires_in", "poll_after"}
    assert len(body["code"]) == 6 and body["code"].isdigit()
    assert body["expires_in"] == 300
    assert body["poll_after"] == 2
    # 番号だけでは何も起きない（鍵は返らない）。
    assert "token" not in body


def test_poll_is_pending_until_approved(home: Path) -> None:
    client = make_client(home)
    start = client.post("/api/v1/devices/pair/start", json={"name": "Quest 3"}).json()
    res = client.post("/api/v1/devices/pair/poll", json={"pair_id": start["pair_id"]})
    assert res.json() == {"status": "pending"}


def test_token_is_returned_exactly_once(home: Path) -> None:
    """D2-2「鍵は一度しか返さない」。二度目は `expired`。"""
    client = make_client(home)
    start = client.post("/api/v1/devices/pair/start", json={"name": "Quest 3"}).json()
    client.post(
        "/api/v1/devices/pair/approve", json={"code": start["code"], "user_id": "master"}
    )
    first = client.post("/api/v1/devices/pair/poll", json={"pair_id": start["pair_id"]}).json()
    assert first["status"] == "approved"
    assert len(first["token"]) >= 40  # 32 バイトの base64url
    second = client.post("/api/v1/devices/pair/poll", json={"pair_id": start["pair_id"]}).json()
    assert second == {"status": "expired"}


def test_unknown_pair_id_is_expired(home: Path) -> None:
    client = make_client(home)
    res = client.post("/api/v1/devices/pair/poll", json={"pair_id": "no-such-pair"})
    assert res.json() == {"status": "expired"}


def test_approve_does_not_hand_the_token_to_the_screen(home: Path) -> None:
    """鍵は端末だけが受け取る（画面・記録に平文を残さない）。"""
    client = make_client(home)
    start = client.post("/api/v1/devices/pair/start", json={"name": "Quest 3"}).json()
    res = client.post(
        "/api/v1/devices/pair/approve", json={"code": start["code"], "user_id": "master"}
    )
    assert res.status_code == 200
    assert "token" not in res.json()
    assert res.json()["device_id"]


def test_approve_with_wrong_code_is_404(home: Path) -> None:
    client = make_client(home)
    client.post("/api/v1/devices/pair/start", json={"name": "Quest 3"})
    res = client.post("/api/v1/devices/pair/approve", json={"code": "000000", "user_id": "master"})
    # 番号が当たらない限り 404（積み上げても何も起きない）。
    assert res.status_code in (404, 400)


def test_five_wrong_codes_drop_the_pairing(conn, home: Path) -> None:
    """D2-4「照合を5回外したら `pair_id` ごと捨てる」。"""
    client = make_client(home)
    start = client.post("/api/v1/devices/pair/start", json={"name": "Quest 3"}).json()
    wrong = "111111" if start["code"] != "111111" else "222222"
    for _ in range(5):
        client.post("/api/v1/devices/pair/approve", json={"code": wrong, "user_id": "master"})
    assert (
        conn.execute("SELECT COUNT(*) AS n FROM web_device_pairing").fetchone()["n"] == 0
    )
    # 正しい番号でも、もう通らない（捨ててある）。
    res = client.post(
        "/api/v1/devices/pair/approve", json={"code": start["code"], "user_id": "master"}
    )
    assert res.status_code == 404


def test_expired_code_cannot_be_approved(conn, home: Path) -> None:
    """5分を過ぎた番号は許可できない（D2-4）。時刻は DB の `expires_at` を過去にして模す。"""
    client = make_client(home)
    start = client.post("/api/v1/devices/pair/start", json={"name": "Quest 3"}).json()
    conn.execute(
        "UPDATE web_device_pairing SET expires_at = '2000-01-01T00:00:00' WHERE pair_id = ?",
        (start["pair_id"],),
    )
    conn.commit()
    res = client.post(
        "/api/v1/devices/pair/approve", json={"code": start["code"], "user_id": "master"}
    )
    assert res.status_code == 404
    assert client.post(
        "/api/v1/devices/pair/poll", json={"pair_id": start["pair_id"]}
    ).json() == {"status": "expired"}


def test_approve_unknown_user_is_404(home: Path) -> None:
    client = make_client(home)
    start = client.post("/api/v1/devices/pair/start", json={"name": "Quest 3"}).json()
    res = client.post(
        "/api/v1/devices/pair/approve", json={"code": start["code"], "user_id": "dare-mo-inai"}
    )
    assert res.status_code == 404


def test_pair_start_is_rate_limited_per_source(home: Path) -> None:
    """D2-4「送信元アドレスごとに1分10回まで」。11回目は 429。

    **同じ app を別の送信元で叩く**——app を作り直すと速度制限（`ctx.pair_limiter`）も
    新しくなってしまい、「送信元ごと」であることを検算できない。
    """
    app = web_app_mod.create_app(home)
    client = TestClient(app, client=LOOPBACK)
    for _ in range(10):
        assert client.post("/api/v1/devices/pair/start", json={"name": "x"}).status_code == 200
    assert client.post("/api/v1/devices/pair/start", json={"name": "x"}).status_code == 429

    # 別の送信元は妨げられない（1台が積み上げても他の端末のペアリングを止めない）。
    another = TestClient(app, client=("192.168.50.9", 1234))
    assert another.post("/api/v1/devices/pair/start", json={"name": "x"}).status_code == 200


# --- D1 端末鍵 ---------------------------------------------------------------------


def test_token_opens_kitchen_only(home: Path) -> None:
    """D1「端末鍵で触れるのは `/api/v1/kitchen/*` と `/api/v1/devices/me` だけ」。"""
    client = make_client(home)
    token = _pair(client, user_id="master")
    auth = {"Authorization": f"Bearer {token}"}

    assert client.get("/api/v1/kitchen/media", headers=auth).status_code == 200
    assert client.get("/api/v1/devices/me", headers=auth).status_code == 200
    # 範囲外は 403（401 ではない——鍵は通っているが、この口は使えない）。
    assert client.get("/api/v1/tasks/board", headers=auth).status_code == 403
    assert client.get("/api/v1/devices", headers=auth).status_code == 403
    assert client.get("/api/v1/meta", headers=auth).status_code == 403


def test_unknown_token_is_401(home: Path) -> None:
    client = make_client(home)
    res = client.get("/api/v1/kitchen/media", headers={"Authorization": "Bearer detarame"})
    assert res.status_code == 401


def test_revoked_token_is_401(home: Path) -> None:
    """D1「失効した鍵は 401（端末は『ペアリングし直し』を出す）」。"""
    client = make_client(home)
    token = _pair(client)
    auth = {"Authorization": f"Bearer {token}"}
    device_id = client.get("/api/v1/devices").json()["items"][0]["id"]

    assert client.get("/api/v1/kitchen/media", headers=auth).status_code == 200
    assert client.delete(f"/api/v1/devices/{device_id}").status_code == 200
    # 失効は**即座に**効く（照合の記憶が残っていても通さない）。
    assert client.get("/api/v1/kitchen/media", headers=auth).status_code == 401


def test_device_acts_as_its_user(home: Path, conn) -> None:
    """D1「鍵が通ると `viewing_user_id` は端末の `user_id`」。

    動画リストは利用者ごと（ADR-016 D1）なので、**同じ口を叩いても見えるものが違う**。
    """
    user_mod.add(conn, "同居人", user_id="u2")
    conn.commit()
    client = make_client(home)
    token = _pair(client, user_id="u2")
    auth = {"Authorization": f"Bearer {token}"}

    # 主人の cookie で1本入れる（cookie の世界は主人のまま）。
    client.cookies.set(USER_COOKIE_NAME, "master")
    added = client.post(
        "/api/v1/kitchen/media", json={"url": "https://youtu.be/aaaaaaaaaaa"}
    )
    assert added.status_code in (201, 400)  # oEmbed が落ちても登録は通る（ADR-016 D2-3）
    client.cookies.clear()

    # 端末（u2）からは主人の動画は見えない。
    mine = client.get("/api/v1/kitchen/media", headers=auth).json()
    assert mine["items"] == []
    # 端末が入れた1本は u2 のものになる。
    client.post("/api/v1/kitchen/media", json={"url": "https://youtu.be/bbbbbbbbbbb"}, headers=auth)
    rows = conn.execute("SELECT user_id FROM chef_media ORDER BY id").fetchall()
    assert {str(r["user_id"]) for r in rows} == {"master", "u2"}


def test_devices_me_shape(home: Path) -> None:
    client = make_client(home)
    token = _pair(client, name="Quest 3", kind="kitchenxr")
    body = client.get("/api/v1/devices/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert body["device"]["name"] == "Quest 3"
    assert body["device"]["kind"] == "kitchenxr"
    assert body["device"]["user_id"] == "master"
    assert body["user"]["id"] == "master"
    # 鍵（平文・ハッシュ）はどちらも返さない。
    assert "token" not in str(body) or "token_hash" not in str(body)
    assert "token_hash" not in str(body)


def test_devices_me_with_cookie_is_403(home: Path) -> None:
    client = make_client(home)
    assert client.get("/api/v1/devices/me").status_code == 403


def test_last_seen_is_updated_then_throttled(home: Path, conn) -> None:
    """`last_seen_at` は叩かれたら入る。**書き込みは1分に1回まで**（間引き）。"""
    client = make_client(home)
    token = _pair(client)
    auth = {"Authorization": f"Bearer {token}"}
    client.get("/api/v1/kitchen/media", headers=auth)
    first = conn.execute("SELECT last_seen_at FROM web_device").fetchone()["last_seen_at"]
    assert first is not None

    conn.execute("UPDATE web_device SET last_seen_at = '2000-01-01T00:00:00'")
    conn.commit()
    client.get("/api/v1/kitchen/media", headers=auth)
    again = conn.execute("SELECT last_seen_at FROM web_device").fetchone()["last_seen_at"]
    assert again == "2000-01-01T00:00:00"  # 1分たっていないので書き直さない


# --- 一覧・失効（D5） ---------------------------------------------------------------


def test_list_and_revoke(home: Path, conn) -> None:
    client = make_client(home)
    _pair(client, name="Quest 3")
    items = client.get("/api/v1/devices").json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["name"] == "Quest 3"
    assert item["user_id"] == "master"
    assert item["user_name"]  # 画面が名前を引き直さずに済む
    assert "token_hash" not in item

    assert client.delete(f"/api/v1/devices/{item['id']}").status_code == 200
    assert client.get("/api/v1/devices").json()["items"] == []
    # 一覧から消えても**行は残す**（誰がいつ持っていたかの記録。失効は `revoked_at`）。
    row = conn.execute("SELECT revoked_at FROM web_device WHERE id = ?", (item["id"],)).fetchone()
    assert row is not None and row["revoked_at"] is not None
    assert len(device_mod.list_devices(conn, include_revoked=True)) == 1


def test_revoke_unknown_is_404(home: Path) -> None:
    client = make_client(home)
    assert client.delete("/api/v1/devices/no-such-device").status_code == 404


def test_read_only_refuses_pairing(home: Path) -> None:
    client = make_client(home, read_only=True)
    assert client.post("/api/v1/devices/pair/start", json={"name": "x"}).status_code == 403


# --- 段A（`web/device.py`）の直接の検算 ---------------------------------------------


def test_token_hash_roundtrip_and_no_plaintext(conn, home: Path) -> None:
    """D1「manor はハッシュしか持たない」。DB のどこにも平文が残らない。"""
    client = make_client(home)
    token = _pair(client)
    row = conn.execute("SELECT * FROM web_device").fetchone()
    assert row["token_hash"].startswith("pbkdf2_sha256$")
    assert token not in row["token_hash"]
    pairing = conn.execute("SELECT * FROM web_device_pairing").fetchone()
    assert pairing["token_plain_once"] is None  # 渡したら消える
    assert pairing["consumed_at"] is not None
    assert device_mod.authenticate(conn, token) is not None
    assert device_mod.authenticate(conn, token + "x") is None


# --- CLI（D5「Web が使えないときの逃げ道」） ------------------------------------------


def test_cli_list_revoke_and_pair(home: Path, conn, capsys) -> None:
    """`manor web device list|pair|revoke`。**画面と同じ段**（`web/device.py`）を通る。"""
    import json as json_mod

    from manor import cli

    # 端末が番号を取り（ここは API の経路）、主人は CLI で許可する。
    client = make_client(home)
    start = client.post("/api/v1/devices/pair/start", json={"name": "Quest 3"}).json()

    assert cli.main(["web", "device", "pair", start["code"], "--json"]) == 0
    paired = json_mod.loads(capsys.readouterr().out)
    assert paired["user_id"] == "master"
    # **CLI にも鍵は出さない**（端末が `pair/poll` で受け取る）。
    assert "token" not in paired

    assert cli.main(["web", "device", "list", "--json"]) == 0
    items = json_mod.loads(capsys.readouterr().out)["items"]
    assert len(items) == 1 and items[0]["name"] == "Quest 3"
    assert "token_hash" not in items[0]

    assert cli.main(["web", "device", "revoke", items[0]["id"], "--json"]) == 0
    revoked = json_mod.loads(capsys.readouterr().out)
    assert revoked["revoked_at"]
    assert cli.main(["web", "device", "list", "--json"]) == 0
    assert json_mod.loads(capsys.readouterr().out)["items"] == []

    # 端末はそれでも鍵を受け取れるが、その鍵は最初から失効している（401）。
    token = client.post("/api/v1/devices/pair/poll", json={"pair_id": start["pair_id"]}).json()[
        "token"
    ]
    assert (
        client.get(
            "/api/v1/kitchen/media", headers={"Authorization": f"Bearer {token}"}
        ).status_code
        == 401
    )


def test_cli_pair_with_wrong_code_fails(home: Path, capsys) -> None:
    from manor import cli

    assert cli.main(["web", "device", "pair", "000000"]) != 0
