"""料理長の動画リスト（ADR-016）の試験。

`video_id` の抽出は**表で**（ADR-016 D6「`video_id` 抽出の表」）。oEmbed は
`fetch_oembed` を monkeypatch で偽装する——**実際の YouTube を叩かない**
（試験は外部に触れない。`tests/conftest.py` の方針どおり）。
"""

from __future__ import annotations

import sqlite3

import pytest

from manor.errors import ManorError
from manor.staff.chef import media

VIDEO = "dQw4w9WgXcQ"


# --- video_id の抽出（純粋関数。DB も外部も要らない） -----------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # ADR-016 D2-1 が受けると決めた5つの形。
        ("https://www.youtube.com/watch?v=" + VIDEO, VIDEO),
        ("https://youtu.be/" + VIDEO, VIDEO),
        ("https://www.youtube.com/shorts/" + VIDEO, VIDEO),
        ("https://www.youtube.com/embed/" + VIDEO, VIDEO),
        ("https://www.youtube.com/live/" + VIDEO, VIDEO),
        # 付属物が付いていても取れる（主人は Chrome の共有から貼る）。
        ("https://youtu.be/" + VIDEO + "?t=42", VIDEO),
        ("https://www.youtube.com/watch?v=" + VIDEO + "&list=PLabc&index=3", VIDEO),
        ("https://m.youtube.com/watch?v=" + VIDEO, VIDEO),
        ("https://music.youtube.com/watch?v=" + VIDEO, VIDEO),
        ("https://www.youtube-nocookie.com/embed/" + VIDEO, VIDEO),
        ("http://youtube.com/watch?v=" + VIDEO, VIDEO),
        ("  https://www.youtube.com/watch?v=" + VIDEO + "  ", VIDEO),
        ("https://www.youtube.com/v/" + VIDEO, VIDEO),
        # 取れない形——**登録させない**（400 に写る）。
        ("", None),
        ("   ", None),
        ("not a url", None),
        # YouTube 以外のホスト（貼り間違い）。11 文字が取れそうでも拒む。
        ("https://example.com/watch?v=" + VIDEO, None),
        ("https://notyoutube.com/watch?v=" + VIDEO, None),
        # 11 文字でない。
        ("https://www.youtube.com/watch?v=short", None),
        ("https://youtu.be/" + VIDEO + "TOOLONG", None),
        # チャンネル・再生リストのページ（動画そのものではない。ADR-016 D6）。
        ("https://www.youtube.com/playlist?list=PLabc", None),
        ("https://www.youtube.com/@somechannel", None),
        # http/https 以外。
        ("ftp://youtu.be/" + VIDEO, None),
        ("javascript:alert(1)", None),
    ],
)
def test_extract_video_id(url: str, expected: str | None) -> None:
    assert media.extract_video_id(url) == expected


def test_default_thumbnail_is_built_from_video_id() -> None:
    """oEmbed が落ちても出せるサムネイル（ADR-016 D2-3）——外部を呼ばずに組める。"""
    assert media.default_thumbnail_url(VIDEO) == f"https://i.ytimg.com/vi/{VIDEO}/hqdefault.jpg"


# --- 登録・編集・並べ替え（DB） ---------------------------------------------------------


@pytest.fixture
def oembed_ok(monkeypatch: pytest.MonkeyPatch):
    """oEmbed が答える場合の偽装。**実際の YouTube は叩かない。**"""

    def fake(url: str, *, timeout: float = media.OEMBED_TIMEOUT) -> dict[str, object]:
        return {
            "ok": True,
            "reason": "",
            "title": "煮込み用の長い音楽",
            "author_name": "架空チャンネル",
            "thumbnail_url": "https://i.ytimg.com/vi/x/oembed.jpg",
        }

    monkeypatch.setattr(media, "fetch_oembed", fake)


@pytest.fixture
def oembed_down(monkeypatch: pytest.MonkeyPatch):
    """oEmbed が落ちている場合の偽装（ADR-016 D2-3「失敗しても登録は通す」）。"""

    def fake(url: str, *, timeout: float = media.OEMBED_TIMEOUT) -> dict[str, object]:
        return {"ok": False, "reason": "タイムアウトしました"}

    monkeypatch.setattr(media, "fetch_oembed", fake)


def test_add_from_url_uses_oembed(conn: sqlite3.Connection, oembed_ok) -> None:
    item = media.add_from_url(conn, f"https://youtu.be/{VIDEO}", user_id="master", memo="煮込みのとき")
    assert item["video_id"] == VIDEO
    assert item["title"] == "煮込み用の長い音楽"
    assert item["author"] == "架空チャンネル"
    assert item["thumbnail_url"] == "https://i.ytimg.com/vi/x/oembed.jpg"
    assert item["memo"] == "煮込みのとき"
    # 貼られた元の URL をそのまま残す（ADR-016 D1）。
    assert item["url"] == f"https://youtu.be/{VIDEO}"
    assert item["sort_order"] == 1
    # `user_id` は返さない（ADR-016 D3 の契約）。
    assert "user_id" not in item


def test_add_from_url_survives_oembed_failure(conn: sqlite3.Connection, oembed_down) -> None:
    """ADR-016 D2-3: 題名は `video_id`、サムネイルは組み立てた既定の URL。登録自体は通る。"""
    item = media.add_from_url(conn, f"https://www.youtube.com/watch?v={VIDEO}", user_id="master")
    assert item["title"] == VIDEO
    assert item["author"] == ""
    assert item["thumbnail_url"] == media.default_thumbnail_url(VIDEO)


def test_add_from_url_rejects_non_youtube(conn: sqlite3.Connection, oembed_ok) -> None:
    with pytest.raises(ManorError) as exc:
        media.add_from_url(conn, "https://example.com/watch?v=" + VIDEO, user_id="master")
    assert exc.value.key == media.ERR_URL_INVALID


def test_add_from_url_rejects_duplicate_for_same_user(conn: sqlite3.Connection, oembed_ok) -> None:
    media.add_from_url(conn, f"https://youtu.be/{VIDEO}", user_id="master")
    with pytest.raises(ManorError) as exc:
        # 同じ動画を別の形の URL で貼っても、`video_id` が同じなら重複。
        media.add_from_url(conn, f"https://www.youtube.com/watch?v={VIDEO}", user_id="master")
    assert exc.value.key == media.ERR_DUPLICATE


def test_same_video_is_allowed_for_another_user(conn: sqlite3.Connection, oembed_ok) -> None:
    """ADR-016 D1「同居人が同じ動画を持つのは重複ではない」。"""
    media.add_from_url(conn, f"https://youtu.be/{VIDEO}", user_id="master")
    other = media.add_from_url(conn, f"https://youtu.be/{VIDEO}", user_id="u2")
    assert other["video_id"] == VIDEO
    assert len(media.list_media(conn, user_id="master")) == 1
    assert len(media.list_media(conn, user_id="u2")) == 1


def _add_three(conn: sqlite3.Connection) -> list[str]:
    ids = []
    for suffix in ("aaa", "bbb", "ccc"):
        item = media.add_from_url(conn, f"https://youtu.be/{suffix}aaaaaaaa", user_id="master")
        ids.append(str(item["id"]))
    return ids


def test_list_is_sorted_and_payload_carries_updated_at(conn: sqlite3.Connection, oembed_down) -> None:
    assert media.list_payload(conn, user_id="master") == {"items": [], "updated_at": None}
    _add_three(conn)
    payload = media.list_payload(conn, user_id="master")
    assert [it["sort_order"] for it in payload["items"]] == [1, 2, 3]  # type: ignore[union-attr]
    assert payload["updated_at"]


def test_update_title_and_memo(conn: sqlite3.Connection, oembed_down) -> None:
    media_id = _add_three(conn)[0]
    updated = media.update(conn, media_id, user_id="master", title="ながら見その1", memo="下ごしらえ")
    assert updated["title"] == "ながら見その1"
    assert updated["memo"] == "下ごしらえ"
    # 渡さなかった欄は触らない。
    only_memo = media.update(conn, media_id, user_id="master", memo="")
    assert only_memo["title"] == "ながら見その1"
    assert only_memo["memo"] == ""


def test_update_empty_title_falls_back_to_video_id(conn: sqlite3.Connection, oembed_down) -> None:
    """題名を空にはできない（一覧で見分けが付かなくなる）。`video_id` に戻す。"""
    media_id = _add_three(conn)[0]
    updated = media.update(conn, media_id, user_id="master", title="   ")
    assert updated["title"] == updated["video_id"]


def test_other_users_rows_are_invisible(conn: sqlite3.Connection, oembed_down) -> None:
    media_id = _add_three(conn)[0]
    for call in (
        lambda: media.update(conn, media_id, user_id="u2", title="横取り"),
        lambda: media.remove(conn, media_id, user_id="u2"),
        lambda: media.reorder(conn, [media_id], user_id="u2"),
    ):
        with pytest.raises(ManorError) as exc:
            call()
        assert exc.value.key == media.ERR_NOT_FOUND


def test_remove(conn: sqlite3.Connection, oembed_down) -> None:
    ids = _add_three(conn)
    media.remove(conn, ids[1], user_id="master")
    assert [it["id"] for it in media.list_media(conn, user_id="master")] == [ids[0], ids[2]]


def test_reorder_renumbers_from_one(conn: sqlite3.Connection, oembed_down) -> None:
    ids = _add_three(conn)
    items = media.reorder(conn, [ids[2], ids[0], ids[1]], user_id="master")
    assert [it["id"] for it in items] == [ids[2], ids[0], ids[1]]
    assert [it["sort_order"] for it in items] == [1, 2, 3]


def test_reorder_keeps_unlisted_rows_behind(conn: sqlite3.Connection, oembed_down) -> None:
    """画面が一部だけを送っても、送らなかった行が消えたり先頭に飛んだりしない。"""
    ids = _add_three(conn)
    items = media.reorder(conn, [ids[2]], user_id="master")
    assert [it["id"] for it in items] == [ids[2], ids[0], ids[1]]


def test_reorder_rejects_unknown_id(conn: sqlite3.Connection, oembed_down) -> None:
    ids = _add_three(conn)
    with pytest.raises(ManorError) as exc:
        media.reorder(conn, [ids[0], "no-such-id"], user_id="master")
    assert exc.value.key == media.ERR_NOT_FOUND


# --- CLI（ADR-016 D5。読み取りだけ） -----------------------------------------------------


def test_cli_media_list(conn: sqlite3.Connection, home, oembed_ok) -> None:
    from types import SimpleNamespace

    from manor.staff.chef import cli as chef_cli

    empty = chef_cli.cmd_media_list(conn, home, SimpleNamespace(user=None, json=False))
    assert "動画" in str(empty)

    media.add_from_url(conn, f"https://youtu.be/{VIDEO}", user_id="master")
    payload = chef_cli.cmd_media_list(conn, home, SimpleNamespace(user=None, json=True))
    assert payload["items"][0]["video_id"] == VIDEO  # type: ignore[index]
    line = chef_cli.cmd_media_list(conn, home, SimpleNamespace(user=None, json=False))
    assert "煮込み用の長い音楽" in str(line)


def test_cli_media_list_reports_missing_table(conn: sqlite3.Connection, home) -> None:
    from types import SimpleNamespace

    from manor.staff.chef import cli as chef_cli

    conn.execute("DROP TABLE chef_media")
    with pytest.raises(ManorError) as exc:
        chef_cli.cmd_media_list(conn, home, SimpleNamespace(user=None, json=False))
    assert exc.value.key == "error.chef.media_table_missing"
