"""姿（VRM）を再訪で流し直さないこと（2026-09-09・主人のご質問）。

> 「担当と話す」で画像表示が遅いんですが、どういう仕組みでしょうか？
> 画像はキャッシュしてうつしてますか？

⚠ **していませんでした。** `ETag` は出ていたのに `If-None-Match` を送っても 200 で
18MB が丸ごと返っていた——Starlette の `FileResponse` は検証子を**付けるだけ**で、
条件付きリクエストを解釈しません。`Cache-Control` も無かったので、ブラウザは
**毎回 18MB をダウンロードし直していました**。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from manor.board import app as board_app_mod
from manor.web import app as web_app_mod

VRM_BYTES = b"glTF" + b"\0" * 2048


def _put_vrm(home: Path, name: str = "butler.vrm", body: bytes = VRM_BYTES) -> Path:
    face_dir = Path(home) / "face"
    face_dir.mkdir(parents=True, exist_ok=True)
    path = face_dir / name
    path.write_bytes(body)
    return path


def _web(home: Path) -> TestClient:
    return TestClient(web_app_mod.create_app(home, read_only=True))


def test_vrm_is_served_with_cache_headers(home_path: Path) -> None:
    _put_vrm(home_path)
    res = _web(home_path).get("/face/model.vrm", params={"agent": "butler"})

    assert res.status_code == 200
    assert res.headers["cache-control"] == "private, max-age=60, must-revalidate"
    assert res.headers["etag"]
    assert res.headers["last-modified"]
    # ⚠ `immutable` は付けない——姿は主人が差し替えられる（`POST /api/v1/face/model`）
    assert "immutable" not in res.headers["cache-control"]
    # ②の資産なので共有プロキシに載せない
    assert res.headers["cache-control"].startswith("private")


def test_revisit_with_etag_gets_304_and_no_body(home_path: Path) -> None:
    _put_vrm(home_path)
    client = _web(home_path)
    first = client.get("/face/model.vrm", params={"agent": "butler"})

    again = client.get(
        "/face/model.vrm", params={"agent": "butler"},
        headers={"If-None-Match": first.headers["etag"]},
    )
    assert again.status_code == 304
    assert again.content == b"", "304 なのに本体が流れている"
    assert again.headers["etag"] == first.headers["etag"]


def test_revisit_with_date_gets_304(home_path: Path) -> None:
    _put_vrm(home_path)
    client = _web(home_path)
    first = client.get("/face/model.vrm", params={"agent": "butler"})

    again = client.get(
        "/face/model.vrm", params={"agent": "butler"},
        headers={"If-Modified-Since": first.headers["last-modified"]},
    )
    assert again.status_code == 304


def test_replacing_the_avatar_sends_the_new_one(home_path: Path) -> None:
    """差し替えたら、古い ETag を持っていても新しい姿が届くこと。"""
    _put_vrm(home_path)
    client = _web(home_path)
    old_etag = client.get("/face/model.vrm", params={"agent": "butler"}).headers["etag"]

    _put_vrm(home_path, body=b"glTF" + b"\1" * 4096)
    res = client.get(
        "/face/model.vrm", params={"agent": "butler"}, headers={"If-None-Match": old_etag}
    )
    assert res.status_code == 200
    assert len(res.content) == 4100


def test_board_serves_the_avatar_the_same_way(home_path: Path) -> None:
    """board も同じ配り方を通ること（**同じ配り方を2箇所に書かない**）。"""
    _put_vrm(home_path, name="model.vrm")
    client = TestClient(board_app_mod.create_app(home_path))

    first = client.get("/face/model.vrm")
    assert first.status_code == 200
    assert first.headers["cache-control"] == "private, max-age=60, must-revalidate"

    again = client.get("/face/model.vrm", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
