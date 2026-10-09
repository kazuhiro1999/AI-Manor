"""カレンダーの利用者ごとの取り込み（ADR-014 D5・段C）の試験。**実際の URL へは繋がない**
（`calendar_mod.urllib.request.urlopen` を必ず差し替える。`tests/test_calendar.py` と同じ
道具立て）。URL・予定・人名はすべて合成データ。
"""

from __future__ import annotations

import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from manor import calendar as calendar_mod
from manor import ics as ics_mod
from manor import secrets as secrets_mod
from manor import user as user_mod
from manor.web import config as web_config

PRINCIPAL_URL = "https://example.com/private/master.ics"
MEMBER_URL = "https://example.com/private/member.ics"


def _fix_clock(monkeypatch: pytest.MonkeyPatch, date_: str = "2026-09-05") -> None:
    monkeypatch.setenv("MANOR_TODAY", date_)
    monkeypatch.setenv("MANOR_NOW", f"{date_}T09:00:00")
    # 予定は Asia/Tokyo で書いてあるので、PC のローカル時刻も東京に固定する（CI は UTC で動く）
    monkeypatch.setattr(ics_mod, "_resolve_local_tz", lambda tz: tz or ZoneInfo("Asia/Tokyo"))


def _ics_bytes(events: list[tuple[str, str, str]]) -> bytes:
    body = "\n".join(
        f"BEGIN:VEVENT\nUID:{uid}\nDTSTART;TZID=Asia/Tokyo:{dtstart}\nSUMMARY:{summary}\nEND:VEVENT"
        for uid, dtstart, summary in events
    )
    return f"BEGIN:VCALENDAR\nVERSION:2.0\n{body}\nEND:VCALENDAR\n".encode("utf-8")


class _FakeResp:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *_a: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._data


def _dispatch_by_url(monkeypatch: pytest.MonkeyPatch, feeds: dict[str, bytes]) -> None:
    def fake_urlopen(req, timeout=0):  # noqa: ANN001
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url not in feeds:
            raise AssertionError(f"想定外の URL です: {url}")
        return _FakeResp(feeds[url])

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def _set_two_user_urls(conn, home: Path) -> tuple[str, str]:
    principal = user_mod.principal_id(conn)
    member = user_mod.add(conn, "相方")
    conn.commit()
    secrets_mod.set("calendar", f"url@{principal}", PRINCIPAL_URL)
    secrets_mod.set("calendar", f"url@{member}", MEMBER_URL)
    return principal, member


# --- manifest -----------------------------------------------------------------------------


def test_url_and_write_calendar_id_are_per_user() -> None:
    from manor import extensions as ext_mod

    manifest = ext_mod.get("calendar")
    fields = {f["key"]: f for f in manifest["fields"]}
    assert fields["url"].get("per_user") is True
    assert fields["write_calendar_id"].get("per_user") is True


# --- 2人の URL を同期して同じ UID でも壊れない ---------------------------------------------


def test_two_users_with_the_same_uid_do_not_collide(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fix_clock(monkeypatch)
    principal, member = _set_two_user_urls(conn, home)
    # **同じ UID・同じ開始時刻**（衝突しうる鍵）だが、内容は別人のもの。
    _dispatch_by_url(
        monkeypatch,
        {
            PRINCIPAL_URL: _ics_bytes([("shared@example.com", "20260906T090000", "主人の予定")]),
            MEMBER_URL: _ics_bytes([("shared@example.com", "20260906T090000", "相方の予定")]),
        },
    )

    result = calendar_mod.sync(home)

    assert result["ok"] is True
    results = result["results"]
    by_user = {r["user_id"]: r for r in results}
    assert set(by_user) == {principal, member}
    assert by_user[principal]["added"] == 1
    assert by_user[member]["added"] == 1

    rows = conn.execute(
        "SELECT title, user_id FROM secretary_event WHERE source = 'ics' ORDER BY user_id"
    ).fetchall()
    titles_by_user = {r["user_id"]: r["title"] for r in rows}
    assert len(rows) == 2  # 1件に潰れていない
    assert titles_by_user[principal] == "主人の予定"
    assert titles_by_user[member] == "相方の予定"


# --- 片方の feed から消えても他方の行が消えない ---------------------------------------------


def test_removing_from_one_feed_does_not_remove_the_others_row(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fix_clock(monkeypatch)
    principal, member = _set_two_user_urls(conn, home)
    _dispatch_by_url(
        monkeypatch,
        {
            PRINCIPAL_URL: _ics_bytes([("shared@example.com", "20260906T090000", "主人の予定")]),
            MEMBER_URL: _ics_bytes([("shared@example.com", "20260906T090000", "相方の予定")]),
        },
    )
    calendar_mod.sync(home)

    # 2回目: member の feed からだけ消える。
    _dispatch_by_url(
        monkeypatch,
        {
            PRINCIPAL_URL: _ics_bytes([("shared@example.com", "20260906T090000", "主人の予定")]),
            MEMBER_URL: _ics_bytes([]),
        },
    )
    result = calendar_mod.sync(home)
    by_user = {r["user_id"]: r for r in result["results"]}
    assert by_user[member]["removed"] == 1
    assert by_user[principal]["removed"] == 0

    rows = conn.execute("SELECT title, user_id FROM secretary_event WHERE source = 'ics'").fetchall()
    assert len(rows) == 1
    assert rows[0]["user_id"] == principal
    assert rows[0]["title"] == "主人の予定"


# --- manual の行は無事 ----------------------------------------------------------------------


def test_manual_rows_survive_multi_user_sync(home: Path, conn, monkeypatch: pytest.MonkeyPatch) -> None:
    _fix_clock(monkeypatch)
    principal, member = _set_two_user_urls(conn, home)
    _dispatch_by_url(
        monkeypatch,
        {
            PRINCIPAL_URL: _ics_bytes([("evt1@example.com", "20260906T090000", "定例会議")]),
            MEMBER_URL: _ics_bytes([]),
        },
    )
    conn.execute(
        "INSERT INTO secretary_event (start, title, source, created_at)"
        " VALUES ('2026-09-06T20:00:00', '手入力の予定', 'manual', '2026-09-01T00:00:00')"
    )
    conn.commit()

    calendar_mod.sync(home)

    manual_rows = conn.execute("SELECT title FROM secretary_event WHERE source = 'manual'").fetchall()
    assert [r["title"] for r in manual_rows] == ["手入力の予定"]


# --- 古い user_id NULL の ics 行が principal の同期で埋まる ---------------------------------


def test_principal_sync_fills_in_old_null_user_id_row(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """既存 DB へ per_user が付く前の ics 行（`user_id IS NULL`）は、principal の同期の
    ときに principal のものとして扱われ、一回の同期で `user_id` が埋まる。
    """
    _fix_clock(monkeypatch)
    principal = user_mod.principal_id(conn)
    secrets_mod.set("calendar", f"url@{principal}", PRINCIPAL_URL)
    conn.commit()

    conn.execute(
        "INSERT INTO secretary_event"
        " (start, title, source, external_id, created_at, user_id)"
        " VALUES ('2026-09-06T09:00:00', '旧タイトル', 'ics', 'evt1@example.com::2026-09-06T09:00:00', "
        "'2026-09-01T00:00:00', NULL)"
    )
    conn.commit()

    _dispatch_by_url(
        monkeypatch,
        {PRINCIPAL_URL: _ics_bytes([("evt1@example.com", "20260906T090000", "旧タイトル")])},
    )

    result = calendar_mod.sync(home)
    assert result["ok"] is True
    assert result["added"] == 0  # 新規ではなく既存行の再利用
    assert result["updated"] == 1  # user_id を埋めるための更新が1件走る

    rows = conn.execute("SELECT user_id FROM secretary_event WHERE source = 'ics'").fetchall()
    assert len(rows) == 1
    assert rows[0]["user_id"] == principal


# --- write_calendar_id の per_user 読み替え --------------------------------------------------


def test_write_calendar_id_per_user_falls_back_to_top_level_for_principal(
    home: Path, conn
) -> None:
    principal = user_mod.principal_id(conn)
    member = user_mod.add(conn, "相方")
    conn.commit()
    web_config.update_section(home, "calendar", {"write_calendar_id": "legacy-top-level-id"})

    assert calendar_mod.write_calendar_id(home, principal) == "legacy-top-level-id"
    assert calendar_mod.write_calendar_id(home, member) == ""  # 引き継がない

    web_config.update_section(home, "calendar", {"users": {member: {"write_calendar_id": "member-cal-id"}}})
    assert calendar_mod.write_calendar_id(home, member) == "member-cal-id"
