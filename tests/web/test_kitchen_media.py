"""`/api/v1/kitchen/media*`（ADR-016 D3）の試験。

oEmbed は `chef/media.fetch_oembed` を monkeypatch で偽装する（**実際の YouTube を
叩かない**）。`tests/web/test_kitchen_recipes.py` と同じ流儀（`TestClient` を直に叩く）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from manor.staff.chef import media as chef_media
from manor.web import app as web_app_mod
from manor.web._common import USER_COOKIE_NAME

VIDEO = "dQw4w9WgXcQ"
WATCH = f"https://www.youtube.com/watch?v={VIDEO}"


def make_client(home: Path, *, read_only: bool = False) -> TestClient:
    return TestClient(web_app_mod.create_app(home, read_only=read_only))


@pytest.fixture(autouse=True)
def oembed_ok(monkeypatch: pytest.MonkeyPatch):
    """既定はこれ（oEmbed が答える）。落ちる場合を見る試験は自分で差し替える。"""

    def fake(url: str, *, timeout: float = chef_media.OEMBED_TIMEOUT) -> dict[str, object]:
        return {
            "ok": True,
            "reason": "",
            "title": "煮込み用の長い音楽",
            "author_name": "架空チャンネル",
            "thumbnail_url": "https://i.ytimg.com/vi/x/oembed.jpg",
        }

    monkeypatch.setattr(chef_media, "fetch_oembed", fake)


def _video_url(prefix: str) -> str:
    """11 文字の合成 `video_id` を作る（実在の動画を指さないための試験用）。"""
    return f"https://youtu.be/{(prefix * 11)[:11]}"


# --- 登録・一覧（XR が読むのはこの2つ） ---------------------------------------------


def test_media_add_then_list(conn, home: Path) -> None:
    client = make_client(home)
    res = client.post("/api/v1/kitchen/media", json={"url": WATCH, "memo": "煮込みのとき"})
    assert res.status_code == 201
    item = res.json()
    assert item["video_id"] == VIDEO
    assert item["title"] == "煮込み用の長い音楽"
    assert item["author"] == "架空チャンネル"
    assert item["memo"] == "煮込みのとき"
    assert item["sort_order"] == 1

    listed = client.get("/api/v1/kitchen/media").json()
    # ADR-016 D3 の契約（**XR 側に写す形**）。過不足があれば XR が壊れる。
    assert set(listed) == {"items", "updated_at"}
    assert listed["updated_at"]
    assert set(listed["items"][0]) == {
        "id", "title", "video_id", "url", "thumbnail_url", "author", "memo", "sort_order",
    }


def test_media_list_is_empty_shape_when_nothing_registered(conn, home: Path) -> None:
    client = make_client(home)
    assert client.get("/api/v1/kitchen/media").json() == {"items": [], "updated_at": None}


def test_media_add_survives_oembed_failure(conn, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-016 D2-3。oEmbed が落ちても 201。題名は `video_id`、サムネイルは既定の URL。"""

    def down(url: str, *, timeout: float = chef_media.OEMBED_TIMEOUT) -> dict[str, object]:
        return {"ok": False, "reason": "タイムアウトしました"}

    monkeypatch.setattr(chef_media, "fetch_oembed", down)
    client = make_client(home)
    item = client.post("/api/v1/kitchen/media", json={"url": WATCH}).json()
    assert item["title"] == VIDEO
    assert item["thumbnail_url"] == chef_media.default_thumbnail_url(VIDEO)


def test_media_add_rejects_unreadable_url_with_400(conn, home: Path) -> None:
    client = make_client(home)
    res = client.post("/api/v1/kitchen/media", json={"url": "https://example.com/watch?v=" + VIDEO})
    assert res.status_code == 400


def test_media_add_duplicate_is_409(conn, home: Path) -> None:
    client = make_client(home)
    assert client.post("/api/v1/kitchen/media", json={"url": WATCH}).status_code == 201
    # 同じ動画を別の形の URL で貼っても `video_id` が同じなら重複。
    res = client.post("/api/v1/kitchen/media", json={"url": f"https://youtu.be/{VIDEO}"})
    assert res.status_code == 409


# --- 編集・削除・並べ替え ------------------------------------------------------------


def test_media_patch_title_and_memo(conn, home: Path) -> None:
    client = make_client(home)
    media_id = client.post("/api/v1/kitchen/media", json={"url": WATCH}).json()["id"]
    res = client.patch(f"/api/v1/kitchen/media/{media_id}", json={"title": "ながら見その1"})
    assert res.status_code == 200
    assert res.json()["title"] == "ながら見その1"
    assert res.json()["memo"] == ""  # 渡さなかった欄は触らない

    res = client.patch(f"/api/v1/kitchen/media/{media_id}", json={"memo": "下ごしらえ用"})
    assert res.json()["title"] == "ながら見その1"
    assert res.json()["memo"] == "下ごしらえ用"


def test_media_patch_unknown_id_is_404(conn, home: Path) -> None:
    client = make_client(home)
    assert client.patch("/api/v1/kitchen/media/nope", json={"title": "x"}).status_code == 404


def test_media_delete(conn, home: Path) -> None:
    client = make_client(home)
    media_id = client.post("/api/v1/kitchen/media", json={"url": WATCH}).json()["id"]
    assert client.delete(f"/api/v1/kitchen/media/{media_id}").status_code == 200
    assert client.get("/api/v1/kitchen/media").json()["items"] == []
    assert client.delete(f"/api/v1/kitchen/media/{media_id}").status_code == 404


def test_media_reorder(conn, home: Path) -> None:
    client = make_client(home)
    ids = [
        client.post("/api/v1/kitchen/media", json={"url": _video_url(c)}).json()["id"]
        for c in ("a", "b", "c")
    ]
    res = client.post("/api/v1/kitchen/media/reorder", json={"ids": [ids[2], ids[0], ids[1]]})
    assert res.status_code == 200
    assert [it["id"] for it in res.json()["items"]] == [ids[2], ids[0], ids[1]]
    assert [it["sort_order"] for it in res.json()["items"]] == [1, 2, 3]

    # 一覧（XR が読む口）にも同じ並びが出る。
    assert [it["id"] for it in client.get("/api/v1/kitchen/media").json()["items"]] == [
        ids[2], ids[0], ids[1]
    ]


def test_media_reorder_unknown_id_is_404(conn, home: Path) -> None:
    client = make_client(home)
    assert client.post("/api/v1/kitchen/media/reorder", json={"ids": ["nope"]}).status_code == 404


# --- 利用者の分離（ADR-014 D3・ADR-016 D1） ---------------------------------------


def test_media_is_per_viewing_user(conn, home: Path) -> None:
    from manor import user as user_mod

    user_mod.add(conn, "同居人", user_id="u2")
    conn.commit()

    client_master = make_client(home)
    client_u2 = make_client(home)
    client_u2.cookies.set(USER_COOKIE_NAME, "u2")

    master_id = client_master.post("/api/v1/kitchen/media", json={"url": WATCH}).json()["id"]
    # 同じ動画でも利用者が違えば重複ではない（ADR-016 D1）。
    u2_id = client_u2.post("/api/v1/kitchen/media", json={"url": WATCH}).json()["id"]
    assert master_id != u2_id

    assert [it["id"] for it in client_master.get("/api/v1/kitchen/media").json()["items"]] == [master_id]
    assert [it["id"] for it in client_u2.get("/api/v1/kitchen/media").json()["items"]] == [u2_id]

    # 他人の行には触れない（404。存在そのものを教えない）。
    assert client_u2.patch(f"/api/v1/kitchen/media/{master_id}", json={"title": "x"}).status_code == 404
    assert client_u2.delete(f"/api/v1/kitchen/media/{master_id}").status_code == 404


# --- 読み取り専用・未導入 ------------------------------------------------------------


def test_media_writes_forbidden_when_read_only(conn, home: Path) -> None:
    client = make_client(home, read_only=True)
    assert client.get("/api/v1/kitchen/media").status_code == 200
    assert client.post("/api/v1/kitchen/media", json={"url": WATCH}).status_code == 403
    assert client.patch("/api/v1/kitchen/media/x", json={"title": "y"}).status_code == 403
    assert client.delete("/api/v1/kitchen/media/x").status_code == 403
    assert client.post("/api/v1/kitchen/media/reorder", json={"ids": []}).status_code == 403


def test_media_not_available_when_table_missing(conn, home: Path) -> None:
    """更新前に chef を導入した既存 home の想定——500 ではなく 404 で案内する。"""
    client = make_client(home)
    conn.execute("DROP TABLE chef_media")
    conn.commit()

    assert client.get("/api/v1/kitchen/media").status_code == 404
    assert client.post("/api/v1/kitchen/media", json={"url": WATCH}).status_code == 404
    assert client.patch("/api/v1/kitchen/media/x", json={"title": "y"}).status_code == 404
    assert client.delete("/api/v1/kitchen/media/x").status_code == 404
    assert client.post("/api/v1/kitchen/media/reorder", json={"ids": []}).status_code == 404
