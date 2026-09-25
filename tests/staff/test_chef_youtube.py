"""YouTube のレシピ動画の検証の道具（`staff/chef/youtube.py`）の試験。本物の API は呼ばない。

概要欄・コメントの書き方は料理チャンネルでよく見る型を合成して並べる（実在の文は写さない）。
"""

from __future__ import annotations

from typing import Any

import pytest

from manor.staff.chef import youtube as yt


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://youtu.be/bIirGpVJiuQ?si=ClG2x2lTm2R2h9CZ", ("video", "bIirGpVJiuQ")),
        ("https://www.youtube.com/watch?v=bIirGpVJiuQ&list=PLabcdefghijk", ("video", "bIirGpVJiuQ")),
        ("https://www.youtube.com/shorts/bIirGpVJiuQ", ("video", "bIirGpVJiuQ")),
        ("https://www.youtube.com/playlist?list=PLabcdefghijklmn", ("playlist", "PLabcdefghijklmn")),
        ("bIirGpVJiuQ", ("video", "bIirGpVJiuQ")),
        ("https://example.com/watch?v=bIirGpVJiuQ", None),
    ],
)
def test_parse_url(url: str, expected) -> None:
    assert yt.parse_url(url) == expected


def test_parse_duration() -> None:
    assert yt.parse_duration("PT19S") == 19
    assert yt.parse_duration("PT1H2M3S") == 3723
    assert yt.parse_duration("") is None


HEADINGS = """今日は簡単チャーハン！
【材料】(2人分)
・ご飯 300g
・豚バラ肉 100g
・卵 2個
・長ねぎ 1/2本
・醤油 小さじ2
・塩こしょう 少々

【作り方】
1. ねぎをみじん切りにする
2. 卵とご飯を炒める
3. 豚肉とねぎを加え、醤油で味を調える

#チャーハン #簡単レシピ
Instagram https://example.com
"""

NO_HEADINGS = """きゅうり 2本
塩昆布 10g
ごま油 大さじ1
白ごま 適量
混ぜるだけ！
"""


def test_extract_recipe_with_headings() -> None:
    got = yt.extract_recipe(HEADINGS)
    names = [i["name"] for i in got["ingredients"]]
    assert names[:3] == ["ご飯", "豚バラ肉", "卵"]
    assert len(got["ingredients"]) == 6
    assert got["steps"][0] == "ねぎをみじん切りにする" and len(got["steps"]) == 3
    assert got["servings"] == 2 and got["method"] == "headings"


def test_extract_recipe_from_amount_lines_without_headings() -> None:
    got = yt.extract_recipe(NO_HEADINGS)
    assert [i["name"] for i in got["ingredients"]] == ["きゅうり", "塩昆布", "ごま油", "白ごま"]
    assert got["method"] == "amount_lines"


def test_no_recipe_in_chat_text() -> None:
    got = yt.extract_recipe("おいしそう！明日作ってみます😋\n最高です")
    assert got["ingredients"] == [] and got["method"] == ""


def test_best_recipe_prefers_the_owner_comment_over_others() -> None:
    comments = [
        {"author": "視聴者", "author_channel_id": "UCviewer", "text": NO_HEADINGS},
        {"author": "投稿者", "author_channel_id": "UCowner", "text": NO_HEADINGS},
    ]
    got = yt.best_recipe("", comments, channel_id="UCowner")
    assert got["source"] == "comment_2_owner"


def test_keywords_come_from_hashtags_tags_and_ingredients() -> None:
    got = yt.extract_recipe(HEADINGS)
    words = yt.keywords("簡単！#チャーハン", HEADINGS, ["中華"], got["ingredients"])
    assert {"チャーハン", "中華", "豚バラ肉", "卵"} <= set(words)


def _fake(responses: dict[str, Any], calls: list[str]):
    def fetch(api: str, params) -> dict[str, Any]:
        calls.append(api)
        value = responses[api]
        if isinstance(value, Exception):
            raise value
        return value(params) if callable(value) else value

    return fetch


def test_probe_reads_a_short_whose_recipe_is_in_the_owner_comment() -> None:
    """共有いただいた動画の型（2026-09-25）: ショート・概要欄が空・レシピはコメント。"""
    calls: list[str] = []
    fetch = _fake(
        {
            "videos": {"items": [{
                "id": "bIirGpVJiuQ",
                "snippet": {"title": "きゅうりレシピ #shorts", "channelTitle": "ch", "channelId": "UCowner",
                            "description": "", "tags": []},
                "contentDetails": {"duration": "PT19S"},
            }]},
            "commentThreads": {"items": [{"snippet": {"topLevelComment": {"snippet": {
                "authorDisplayName": "ch", "authorChannelId": {"value": "UCowner"}, "textOriginal": NO_HEADINGS,
            }}}}]},
        },
        calls,
    )
    result = yt.probe(fetch, ["https://youtu.be/bIirGpVJiuQ"])
    video = result["videos"][0]
    assert video["short"] is True and video["description_chars"] == 0
    assert video["recipe_source"] == "comment_1_owner" and len(video["ingredients"]) == 4
    assert result["units"] == 2 and calls == ["videos", "commentThreads"]


def test_probe_reports_a_private_playlist_and_closed_comments() -> None:
    calls: list[str] = []
    fetch = _fake(
        {
            "playlists": {"items": []},  # 非公開・存在しない
            "videos": {"items": [{"id": "abcdefghijk", "snippet": {"title": "t", "description": HEADINGS},
                                  "contentDetails": {"duration": "PT10M"}}]},
            "commentThreads": yt.YouTubeError("commentsDisabled"),
        },
        calls,
    )
    result = yt.probe(fetch, ["https://www.youtube.com/playlist?list=PLprivate000000", "abcdefghijk"])
    assert result["errors"] == [{"url": "https://www.youtube.com/playlist?list=PLprivate000000", "reason": "playlist_not_visible"}]
    video = result["videos"][0]
    assert video["comments_closed"] == "commentsDisabled"
    assert video["recipe_source"] == "description" and video["short"] is False
