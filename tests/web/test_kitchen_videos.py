"""`/api/v1/kitchen/videos`（ADR-023 D5）の試験。同期は偽の API で済ませ、一覧の口だけを見る。

`tests/conftest.py` が `MANOR_YOUTUBE_SYNC=off` にしているので、一覧を開いても本物の同期は走らない。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from manor import extensions as ext_mod
from manor.staff.chef import youtube as yt
from manor.web import app as web_app_mod

PL_MASTER = "PLmaster0000000000"


class FakeYouTube:
    """最小の偽の API（`tests/staff/test_chef_youtube_sync.py` の縮小版。tests はパッケージでないので写す）。"""

    def __init__(self) -> None:
        self.playlists: dict[str, list[str]] = {}
        self.titles = {"aaaaaaaaaaa": "豚バラキャベツ炒め #豚肉レシピ", "bbbbbbbbbbb": "やみつききゅうり"}

    def factory(self, key: str):
        def fetch(api: str, params):
            if api == "playlists":
                return {"items": [{"snippet": {"title": "レシピ"}}]}
            if api == "playlistItems":
                return {"items": [{"contentDetails": {"videoId": v}} for v in self.playlists[params["playlistId"]]]}
            if api == "videos":
                return {"items": [
                    {"id": v, "snippet": {"title": self.titles[v], "channelTitle": "ch", "description": "", "tags": []},
                     "contentDetails": {"duration": "PT40S"}}
                    for v in params["id"].split(",")
                ]}
            return {"items": []}

        return fetch


def make_client(home: Path, *, read_only: bool = False) -> TestClient:
    return TestClient(web_app_mod.create_app(home, read_only=read_only))


def test_not_configured_is_empty(home: Path) -> None:
    body = make_client(home).get("/api/v1/kitchen/videos").json()
    assert body["items"] == [] and body["configured"] is False


def test_lists_synced_videos_with_the_same_filters_as_recipes(conn, home: Path) -> None:
    ext_mod.save_settings(home, "youtube", {"api_key": "k", "playlists": f"https://www.youtube.com/playlist?list={PL_MASTER}"})
    fake = FakeYouTube()
    fake.playlists = {PL_MASTER: ["aaaaaaaaaaa", "bbbbbbbbbbb"]}
    yt.sync(conn, home, fetcher_factory=fake.factory, now="2026-09-25T10:00:00")
    conn.commit()
    client = make_client(home)
    body = client.get("/api/v1/kitchen/videos").json()
    assert body["configured"] is True and body["sync"]["synced_at"] == "2026-09-25T10:00:00"
    assert {v["video_id"] for v in body["items"]} == {"aaaaaaaaaaa", "bbbbbbbbbbb"}
    pork = client.get("/api/v1/kitchen/videos", params={"q": "豚肉"}).json()["items"]
    assert [v["video_id"] for v in pork] == ["aaaaaaaaaaa"]
    assert pork[0]["url"] == "https://www.youtube.com/watch?v=aaaaaaaaaaa"
    assert client.get("/api/v1/kitchen/videos", params={"main_ingredient": "肉"}).json()["items"][0]["video_id"] == "aaaaaaaaaaa"


def test_sync_is_refused_when_read_only(home: Path) -> None:
    res = make_client(home, read_only=True).post("/api/v1/kitchen/videos/sync")
    assert res.status_code in (403, 405, 409)
