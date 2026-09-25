"""YouTube の再生リストの同期（30 日の控え）とレシピ帳の検索（ADR-023 D1・D5）の試験。

本物の API は呼ばない——`sync(fetcher_factory=…)` に偽物を渡す。家族2人の再生リストで、
同じ動画は1件にまとめ・どちらの再生リストからかを残すこと、外したら消えること、30 日で消えること、
取り込み済みは並ばないことを固定する。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from manor import extensions as ext_mod
from manor import user as user_mod
from manor.staff.chef import recipes, youtube as yt

PL_MASTER = "PLmaster0000000000"
PL_PARTNER = "PLpartner000000000"

RECIPE_COMMENT = "【材料】\n・豚バラ肉（200g）\n・キャベツ（1/4個）\n・醤油（大さじ1）\n"


def _video(vid: str, title: str) -> dict[str, Any]:
    return {
        "id": vid,
        "snippet": {"title": title, "channelTitle": "ch", "channelId": "UCowner", "description": "",
                    "tags": [], "thumbnails": {"high": {"url": f"https://i.ytimg.com/{vid}.jpg"}}},
        "contentDetails": {"duration": "PT40S"},
    }


class FakeYouTube:
    """再生リストの中身を差し替えられる偽の API。"""

    def __init__(self) -> None:
        self.playlists: dict[str, list[str]] = {}
        self.hidden: set[str] = set()
        self.videos: dict[str, dict[str, Any]] = {
            "aaaaaaaaaaa": _video("aaaaaaaaaaa", "豚バラキャベツ炒め #豚肉レシピ"),
            "bbbbbbbbbbb": _video("bbbbbbbbbbb", "やみつききゅうり #きゅうり"),
            "ccccccccccc": _video("ccccccccccc", "パラパラ炒飯"),
        }
        self.calls: list[str] = []

    def factory(self, key: str):
        def fetch(api: str, params) -> dict[str, Any]:
            self.calls.append(api)
            if api == "playlists":
                pid = params["id"]
                if pid in self.hidden or pid not in self.playlists:
                    return {"items": []}
                return {"items": [{"snippet": {"title": f"title-{pid[:6]}"}}]}
            if api == "playlistItems":
                ids = self.playlists[params["playlistId"]]
                return {"items": [{"contentDetails": {"videoId": v}} for v in ids]}
            if api == "videos":
                return {"items": [self.videos[v] for v in params["id"].split(",") if v in self.videos]}
            if api == "commentThreads":
                text = RECIPE_COMMENT if params["videoId"] == "aaaaaaaaaaa" else "おいしそう"
                return {"items": [{"snippet": {"topLevelComment": {"snippet": {
                    "authorDisplayName": "ch", "authorChannelId": {"value": "UCowner"}, "textOriginal": text}}}}]}
            raise AssertionError(api)

        return fetch


@pytest.fixture
def family(conn, home: Path) -> FakeYouTube:
    partner = user_mod.add(conn, "パートナー", callname="はなこ")
    conn.commit()
    ext_mod.save_settings(home, "youtube", {"api_key": "key-master", "playlists": f"https://www.youtube.com/playlist?list={PL_MASTER}"})
    ext_mod.save_settings(home, "youtube", {"playlists": f"https://www.youtube.com/playlist?list={PL_PARTNER}"}, user_id=partner)
    fake = FakeYouTube()
    fake.playlists = {PL_MASTER: ["aaaaaaaaaaa", "bbbbbbbbbbb"], PL_PARTNER: ["aaaaaaaaaaa", "ccccccccccc"]}
    return fake


def _sync(conn, home, fake, now="2026-09-25T10:00:00"):
    result = yt.sync(conn, home, fetcher_factory=fake.factory, now=now)
    conn.commit()
    return result


def test_same_video_in_two_playlists_is_one_row_with_both_sources(conn, home, family) -> None:
    result = _sync(conn, home, family)
    assert result["videos"] == 3
    assert conn.execute("SELECT COUNT(*) AS n FROM chef_video").fetchone()["n"] == 3
    shared = yt.list_videos(conn, q="豚バラ")[0]
    assert shared["video_id"] == "aaaaaaaaaaa"
    assert {s["playlist_title"] for s in shared["sources"]} == {f"title-{PL_MASTER[:6]}", f"title-{PL_PARTNER[:6]}"}
    assert {s["user_name"] for s in shared["sources"]} >= {"はなこ"}
    # 動画の中身（videos・コメント）は同じ動画でも1回しか読まない
    assert family.calls.count("commentThreads") == 3


def test_search_uses_synonyms_and_says_why(conn, home, family) -> None:
    _sync(conn, home, family)
    pork = yt.list_videos(conn, q="豚肉")
    assert [v["video_id"] for v in pork] == ["aaaaaaaaaaa"]
    assert yt.list_videos(conn, q="チャーハン")[0]["video_id"] == "ccccccccccc"  # 炒飯
    # 題名に無く材料にだけある語は「材料に 醤油」が理由になる（題名に当たれば題名が先）
    soy = yt.list_videos(conn, q="しょうゆ")
    assert soy == [] or soy[0]["matched"][0]["field"] == "ingredient"
    by_ingredient = yt.list_videos(conn, q="醤油")[0]
    assert by_ingredient["matched"] == [{"field": "ingredient", "text": "醤油"}] and by_ingredient["has_recipe"] is True
    assert yt.list_videos(conn, q="キャベツ")[0]["matched"][0]["field"] == "title"


def test_removed_from_playlist_disappears_but_unreadable_playlist_keeps_videos(conn, home, family) -> None:
    _sync(conn, home, family)
    family.playlists[PL_MASTER] = ["aaaaaaaaaaa"]  # きゅうりを外した
    family.hidden = {PL_PARTNER}  # はなこの再生リストが一時的に読めない
    _sync(conn, home, family, now="2026-09-26T10:00:00")
    ids = {v["video_id"] for v in yt.list_videos(conn)}
    assert "bbbbbbbbbbb" not in ids
    assert "ccccccccccc" in ids  # 読めなかっただけでは消さない
    assert yt.last_sync(conn)["failed"][0]["reason"] == "playlist_not_visible"


def test_videos_not_refreshed_for_30_days_are_deleted(conn, home, family) -> None:
    _sync(conn, home, family, now="2026-09-01T10:00:00")
    family.hidden = {PL_MASTER, PL_PARTNER}  # 以後ずっと読めない
    result = _sync(conn, home, family, now="2026-10-02T10:00:00")
    assert result["expired"] == 3
    assert conn.execute("SELECT COUNT(*) AS n FROM chef_video").fetchone()["n"] == 0


def test_imported_video_is_not_listed(conn, home, family, sample_recipe=None) -> None:
    _sync(conn, home, family)
    recipes.add(conn, {
        "title": "豚バラキャベツ", "source_url": "https://youtu.be/aaaaaaaaaaa?si=x",
        "ingredients": [{"name": "豚バラ肉", "qty": "200", "unit": "g"}],
        "phases": [{"id": "p", "title": "作る"}],
        "steps": [{"index": 1, "phase": "p", "title": "作る", "instruction": "作る。", "completion": "manual"}],
    })
    assert "aaaaaaaaaaa" not in {v["video_id"] for v in yt.list_videos(conn)}


def test_sync_due(conn, home, family) -> None:
    assert yt.sync_due(conn, now="2026-09-25T10:00:00") is True
    _sync(conn, home, family)
    assert yt.sync_due(conn, now="2026-09-25T21:00:00") is False
    assert yt.sync_due(conn, now="2026-09-25T22:00:01") is True


def test_classification_comes_from_title_and_ingredients(conn, home, family) -> None:
    _sync(conn, home, family)
    row = conn.execute("SELECT main_ingredient, ingredients FROM chef_video WHERE video_id = 'aaaaaaaaaaa'").fetchone()
    assert row["main_ingredient"] == "肉" and "キャベツ" in json.loads(row["ingredients"])
    assert [v["video_id"] for v in yt.list_videos(conn, main_ingredient="肉")] == ["aaaaaaaaaaa"]
