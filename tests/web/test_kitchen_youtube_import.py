"""YouTube の動画を取り込み画面の下書きにする経路と、同じ出典の重複登録の拒否（ADR-023 D2・D3）の試験。

本物の API は呼ばない——`youtube.http_fetcher` を偽物に差し替える。鍵は per_user の秘密の置き場へ入れる。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from manor import extensions as ext_mod
from manor.staff.chef import youtube as yt
from manor.web import app as web_app_mod

COMMENT = """★やみつき大根
・大根（300g）
・塩（小さじ1/2）
【A】ぽん酢（大さじ2）

《作り方》
①大根を切って塩をまぶし、洗い流す。
②ぽん酢で和える。
"""


def make_client(home: Path) -> TestClient:
    return TestClient(web_app_mod.create_app(home))


@pytest.fixture
def fake_youtube(home: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    ext_mod.save_settings(home, "youtube", {"api_key": "test-key"})
    calls: list[str] = []

    def fetcher(key: str, **_kw):
        assert key == "test-key"

        def fetch(api: str, params) -> dict[str, Any]:
            calls.append(api)
            if api == "videos":
                return {"items": [{
                    "id": "bIirGpVJiuQ",
                    "snippet": {"title": "大根レシピ #大根 #shorts", "channelTitle": "ch", "channelId": "UCowner",
                                "description": "", "tags": [], "thumbnails": {"high": {"url": "https://i.ytimg.com/x.jpg"}}},
                    "contentDetails": {"duration": "PT30S"},
                }]}
            return {"items": [{"snippet": {"topLevelComment": {"snippet": {
                "authorDisplayName": "ch", "authorChannelId": {"value": "UCowner"}, "textOriginal": COMMENT}}}}]}

        return fetch

    monkeypatch.setattr(yt, "http_fetcher", fetcher)
    return calls


def test_youtube_url_becomes_a_draft_and_is_not_saved(home: Path, conn, fake_youtube) -> None:
    client = make_client(home)
    res = client.post("/api/v1/kitchen/recipes/import", json={"url": "https://youtu.be/bIirGpVJiuQ?si=abc"})
    assert res.status_code == 200, res.text
    body = res.json()
    recipe = body["recipe"]
    assert recipe["title"] == "やみつき大根"
    assert recipe["source_url"] == "https://www.youtube.com/watch?v=bIirGpVJiuQ"
    assert recipe["hero_image"] == "https://i.ytimg.com/x.jpg"
    assert [i["name"] for i in recipe["ingredients"]] == ["大根", "塩", "ぽん酢"]
    assert len(recipe["steps"]) == 2
    assert body["method"] == "youtube:comment_1_owner" and body["duplicate"] is None
    assert any("投稿者のコメント" in w for w in body["warnings"])
    assert conn.execute("SELECT COUNT(*) AS n FROM chef_recipe").fetchone()["n"] == 0  # 保存しない
    assert fake_youtube == ["videos", "commentThreads"]


def test_registering_the_same_video_twice_is_409_and_the_draft_says_so(home: Path, fake_youtube) -> None:
    client = make_client(home)
    draft = client.post("/api/v1/kitchen/recipes/import", json={"url": "https://youtu.be/bIirGpVJiuQ"}).json()
    recipe = {k: v for k, v in draft["recipe"].items() if k != "meta"}
    first = client.post("/api/v1/kitchen/recipes", json=recipe)
    assert first.status_code == 200, first.text
    again = client.post("/api/v1/kitchen/recipes/import", json={"url": "https://www.youtube.com/shorts/bIirGpVJiuQ"}).json()
    assert again["duplicate"]["id"] == first.json()["id"]
    second = client.post("/api/v1/kitchen/recipes", json=recipe)
    assert second.status_code == 409 and "もうレシピ帳にあります" in second.json()["detail"]


def test_youtube_import_without_a_key_is_400(home: Path) -> None:
    res = make_client(home).post("/api/v1/kitchen/recipes/import", json={"url": "https://youtu.be/bIirGpVJiuQ"})
    assert res.status_code == 400 and "API キー" in res.json()["detail"]
