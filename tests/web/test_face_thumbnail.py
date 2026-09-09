"""担当の正面画像（2026-09-09・主人のご提案）。

> この画面は正直 vrm は読み込まなくても、正面画像をサーバーに保存するようにすれば
> 画像だけで済むと思います。……vrm を差し替えた時だけ、表示画像も撮り直しておき、
> それを表示します。

⚠ 一覧はそれまで、担当ごとに VRM（18MB）を読んで1フレーム焼いて**捨てて**いた。
"""

from __future__ import annotations

import base64
from pathlib import Path

from fastapi.testclient import TestClient

from manor.web import app as web_app_mod

# 1x1 の PNG（実物の魔法数を持つ最小の絵）
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
DATA_URL = "data:image/png;base64," + base64.b64encode(PNG_1PX).decode()


def _client(home: Path, *, read_only: bool = False) -> TestClient:
    return TestClient(web_app_mod.create_app(home, read_only=read_only))


def test_thumbnail_is_404_before_anyone_takes_one(home_path: Path) -> None:
    """まだ撮っていなければ 404——一覧はそれを見て VRM から焼く側へ倒す。"""
    res = _client(home_path).get("/face/thumbnail.png", params={"agent": "butler"})
    assert res.status_code == 404
    assert "home/face/butler.png" in res.json()["detail"]


def test_post_then_get_the_thumbnail(home_path: Path) -> None:
    client = _client(home_path)
    posted = client.post("/api/v1/face/thumbnail", json={"agent": "butler", "data_url": DATA_URL})
    assert posted.status_code == 200
    assert posted.json()["bytes"] == len(PNG_1PX)

    got = client.get("/face/thumbnail.png", params={"agent": "butler"})
    assert got.status_code == 200
    assert got.content == PNG_1PX
    assert got.headers["content-type"] == "image/png"
    # 姿と同じ配り方（304 が効く）
    assert got.headers["cache-control"] == "private, max-age=60, must-revalidate"
    again = client.get(
        "/face/thumbnail.png", params={"agent": "butler"},
        headers={"If-None-Match": got.headers["etag"]},
    )
    assert again.status_code == 304


def test_thumbnail_is_checked_by_its_content_not_its_name(home_path: Path) -> None:
    """PNG でないものは受け取らない（先頭の魔法数で見る。VRM の受け口と同じ作法）。"""
    client = _client(home_path)
    fake = "data:image/png;base64," + base64.b64encode(b"this is not a png").decode()
    res = client.post("/api/v1/face/thumbnail", json={"agent": "butler", "data_url": fake})
    assert res.status_code == 400
    assert "PNG" in res.json()["detail"]


def test_unknown_agent_is_refused(home_path: Path) -> None:
    client = _client(home_path)
    assert client.post(
        "/api/v1/face/thumbnail", json={"agent": "../etc", "data_url": DATA_URL}
    ).status_code == 404


def test_replacing_the_avatar_drops_the_old_thumbnail(home_path: Path) -> None:
    """姿を差し替えたら古い絵を捨てる——残すと、一覧が前の顔を出し続ける。"""
    client = _client(home_path)
    client.post("/api/v1/face/thumbnail", json={"agent": "butler", "data_url": DATA_URL})
    assert (home_path / "face" / "butler.png").is_file()

    vrm = b"glTF" + b"\0" * 512
    res = client.post(
        "/api/v1/face/model",
        data={"agent": "butler"},
        files={"file": ("butler.vrm", vrm, "model/gltf-binary")},
    )
    assert res.status_code == 200
    assert not (home_path / "face" / "butler.png").is_file(), "古い正面画像が残っている"


def test_read_only_mode_refuses_to_store(home_path: Path) -> None:
    res = _client(home_path, read_only=True).post(
        "/api/v1/face/thumbnail", json={"agent": "butler", "data_url": DATA_URL}
    )
    assert res.status_code in (403, 405)
