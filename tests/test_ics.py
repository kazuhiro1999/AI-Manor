"""`manor.ics`（ADR-012 §2「5d カレンダー拡張」D4・D5）の試験。

**この層は純粋関数**——ネットワークもDBも触らない。文字列を渡して辞書の一覧を受け取るだけ
なので、ここでは `home`/`conn` フィクスチャを一切使わない（`test_calendar.py` は別）。

## タイムゾーンについて（2026-09-05 改訂）

当初、この試験機（Windows）では `zoneinfo.ZoneInfo("Asia/Tokyo")` が `tzdata` パッケージ
未導入のため `ZoneInfoNotFoundError` になっていた（Windows は OS 自体に IANA のタイムゾーン
データベースを持たない）。**執事の判断で `tzdata` を `pyproject.toml` の依存に加えた**
（ADR-012 D4 改訂。`uv pip install tzdata` 済み・`ZoneInfo("Asia/Tokyo")`/`ZoneInfo("UTC")`
が実際に解決することを実測で確認済み）ので、以降は `ZoneInfo` をそのまま使ってよい。

とはいえ、大半の試験では引き続き固定オフセット `timezone(timedelta(hours=9))`（`JST`）を
`local_tz` として渡す——`tzdata` の有無に依らずどの試験機でも同じ結果になるほうが壊れにくい
（`Asia/Tokyo` は夏時間が無く常に UTC+9 なので、実際の `ZoneInfo("Asia/Tokyo")` を使っても
数値上の結果は同じ）。**TZID の実際の解決とタイムゾーンをまたいだ変換**（D4 改訂の本題）を
証明する試験（`test_tzid_resolves_via_real_zoneinfo_and_converts_across_zones` 以下）だけは
本物の `ZoneInfo` を使う——固定オフセットのフォールバックでは「解決できた」ことの証明に
ならないため。
"""

from __future__ import annotations

from datetime import date, timedelta, timezone
from zoneinfo import ZoneInfo

from manor import ics as ics_mod

JST = timezone(timedelta(hours=9))
TODAY = date(2026, 9, 5)


def _wrap(*vevents: str) -> str:
    body = "\n".join(vevents)
    return f"BEGIN:VCALENDAR\nVERSION:2.0\n{body}\nEND:VCALENDAR\n"


# --- 行の折り返し（unfold） ------------------------------------------------------------------


def test_unfold_restores_folded_lines() -> None:
    folded = (
        "SUMMARY:これは75オクテットを超える長い要約であり折り返されて複数行に分かれ\n"
        " ているが実際には1つのSUMMARYプロパティである\n"
        "LOCATION:会議室"
    )
    unfolded = ics_mod.unfold(folded)
    assert unfolded == (
        "SUMMARY:これは75オクテットを超える長い要約であり折り返されて複数行に分かれているが実際には1つのSUMMARYプロパティである\n"
        "LOCATION:会議室"
    )


def test_folded_description_is_reconstructed_before_parsing() -> None:
    text = _wrap(
        "BEGIN:VEVENT\n"
        "UID:folded@example.com\n"
        "DTSTART:20260905T080000\n"
        "SUMMARY:折り返しの確認\n"
        "DESCRIPTION:これは長い説明文であり折り返されて複数行に\n"
        " 分割されているが\\n実際には1つのDESCRIPTIONプロパティである\n"
        "END:VEVENT"
    )
    events = ics_mod.parse_ics(text, today=TODAY, local_tz=JST)
    assert len(events) == 1
    assert events[0]["note"] == (
        "これは長い説明文であり折り返されて複数行に分割されているが\n実際には1つのDESCRIPTIONプロパティである"
    )


# --- 3種類の時刻（D4） -----------------------------------------------------------------------


def test_three_datetime_forms_all_resolve_to_local_time() -> None:
    text = _wrap(
        "BEGIN:VEVENT\n"
        "UID:tzid@example.com\n"
        "DTSTART;TZID=Asia/Tokyo:20260910T100000\n"
        "DTEND;TZID=Asia/Tokyo:20260910T110000\n"
        "SUMMARY:TZID会議\n"
        "END:VEVENT",
        "BEGIN:VEVENT\n"
        "UID:utc@example.com\n"
        "DTSTART:20260910T010000Z\n"
        "DTEND:20260910T020000Z\n"
        "SUMMARY:UTC会議\n"
        "END:VEVENT",
        "BEGIN:VEVENT\n"
        "UID:allday@example.com\n"
        "DTSTART;VALUE=DATE:20260910\n"
        "DTEND;VALUE=DATE:20260911\n"
        "SUMMARY:終日の予定\n"
        "END:VEVENT",
    )
    events = ics_mod.parse_ics(text, today=TODAY, local_tz=JST)
    by_uid = {e["uid"]: e for e in events}

    assert by_uid["tzid@example.com"]["start"] == "2026-09-10T10:00:00"
    assert by_uid["tzid@example.com"]["end"] == "2026-09-10T11:00:00"

    # UTC 01:00 は Asia/Tokyo（UTC+9）で 10:00。TZID 版と同じ時刻に揃うはず。
    assert by_uid["utc@example.com"]["start"] == "2026-09-10T10:00:00"
    assert by_uid["utc@example.com"]["end"] == "2026-09-10T11:00:00"

    assert by_uid["allday@example.com"]["start"] == "2026-09-10"
    assert by_uid["allday@example.com"]["end"] == "2026-09-11"


# --- TZID の実解決（ADR-012 D4 改訂の本題。2026-09-05） ----------------------------------------


def test_tzid_resolves_via_real_zoneinfo_and_converts_across_zones() -> None:
    """`tzdata` を依存に足した本題の確認: 固定オフセットのフォールバックに頼らず、
    本物の `ZoneInfo` で実際にタイムゾーンをまたいだ変換ができること。

    22:00（Asia/Tokyo, 2026-09-10）は、America/New_York（この時期は EDT=UTC-4）では
    同日 09:00 になる（手計算: 22:00 JST = 13:00 UTC = 09:00 EDT。
    `ZoneInfo` を直接使った実測でも同じ値になることを別途確認済み）。
    """
    text = _wrap(
        "BEGIN:VEVENT\n"
        "UID:crosszone@example.com\n"
        "DTSTART;TZID=Asia/Tokyo:20260910T220000\n"
        "SUMMARY:海外との定例\n"
        "END:VEVENT"
    )
    events = ics_mod.parse_ics(text, today=TODAY, local_tz=ZoneInfo("America/New_York"))
    assert len(events) == 1
    assert events[0]["start"] == "2026-09-10T09:00:00"
    assert "[TZID未解決]" not in events[0]["note"]  # 解決できたので note は汚れない


def test_unresolvable_tzid_falls_back_and_leaves_note_without_raising() -> None:
    """存在しない TZID（`tzdata` が入っていても解決できない）は、例外にせず `local_tz`
    へフォールバックしつつ、非対応 RRULE と同じ作法で `note` に残す（黙って落とさない。
    ADR-012 D4 改訂）。
    """
    text = _wrap(
        "BEGIN:VEVENT\n"
        "UID:badtz@example.com\n"
        "DTSTART;TZID=Not/AZone:20260905T080000\n"
        "DESCRIPTION:元のメモ\n"
        "SUMMARY:壊れたTZID\n"
        "END:VEVENT"
    )
    events = ics_mod.parse_ics(text, today=TODAY, local_tz=JST)
    assert len(events) == 1
    # 解決できなかったので local_tz（JST）そのものとして扱われる＝時刻の値はそのまま。
    assert events[0]["start"] == "2026-09-05T08:00:00"
    assert "[TZID未解決]" in events[0]["note"]
    assert "Not/AZone" in events[0]["note"]
    assert "元のメモ" in events[0]["note"]  # 元の DESCRIPTION も失わない


# --- STATUS:CANCELLED は取り込まない ----------------------------------------------------------


def test_cancelled_status_is_excluded() -> None:
    text = _wrap(
        "BEGIN:VEVENT\n"
        "UID:cancelled@example.com\n"
        "DTSTART:20260905T100000\n"
        "STATUS:CANCELLED\n"
        "SUMMARY:キャンセル済み\n"
        "END:VEVENT"
    )
    assert ics_mod.parse_ics(text, today=TODAY, local_tz=JST) == []


# --- エスケープの復元 ------------------------------------------------------------------------


def test_text_escapes_are_unescaped() -> None:
    text = _wrap(
        "BEGIN:VEVENT\n"
        "UID:esc@example.com\n"
        "DTSTART:20260905T080000\n"
        "SUMMARY:カンマ\\, セミコロン\\; バックスラッシュ\\\\\n"
        "DESCRIPTION:1行目\\n2行目\n"
        "END:VEVENT"
    )
    events = ics_mod.parse_ics(text, today=TODAY, local_tz=JST)
    assert events[0]["title"] == "カンマ, セミコロン; バックスラッシュ\\"
    assert events[0]["note"] == "1行目\n2行目"


# --- 繰り返しの展開（D5） ---------------------------------------------------------------------


def test_expand_daily_with_count() -> None:
    text = _wrap(
        "BEGIN:VEVENT\n"
        "UID:daily@example.com\n"
        "DTSTART;TZID=Asia/Tokyo:20260905T090000\n"
        "RRULE:FREQ=DAILY;COUNT=3\n"
        "SUMMARY:毎日の確認\n"
        "END:VEVENT"
    )
    events = ics_mod.parse_ics(text, today=TODAY, local_tz=JST)
    starts = sorted(e["start"] for e in events)
    assert starts == ["2026-09-05T09:00:00", "2026-09-06T09:00:00", "2026-09-07T09:00:00"]
    # 繰り返しの各回は external_id が別（UID＋開始時刻。ADR-012 D6）。
    assert len({e["external_id"] for e in events}) == 3


def test_expand_weekly_byday_with_interval_and_count() -> None:
    start = date(2026, 9, 7)
    assert start.weekday() == 0  # 前提の明示（月曜）

    text = _wrap(
        "BEGIN:VEVENT\n"
        "UID:weekly@example.com\n"
        f"DTSTART;TZID=Asia/Tokyo:{start.strftime('%Y%m%d')}T190000\n"
        "RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE;COUNT=4\n"
        "SUMMARY:隔週の会議\n"
        "END:VEVENT"
    )
    events = ics_mod.parse_ics(text, today=TODAY, local_tz=JST)
    starts = sorted(e["start"] for e in events)
    assert starts == [
        "2026-09-07T19:00:00",  # 週0・月
        "2026-09-09T19:00:00",  # 週0・水
        "2026-09-21T19:00:00",  # 週2（2週間後）・月
        "2026-09-23T19:00:00",  # 週2・水
    ]


def test_expand_until_stops_including_series() -> None:
    text = _wrap(
        "BEGIN:VEVENT\n"
        "UID:until@example.com\n"
        "DTSTART;TZID=Asia/Tokyo:20260905T080000\n"
        "RRULE:FREQ=DAILY;UNTIL=20260907T080000Z\n"
        "SUMMARY:UNTILの確認\n"
        "END:VEVENT"
    )
    events = ics_mod.parse_ics(text, today=TODAY, local_tz=JST)
    starts = sorted(e["start"] for e in events)
    # UNTIL=20260907T080000Z は Asia/Tokyo で 9/7 17:00。9/7 08:00 は含み、9/8 08:00 は含まない。
    assert starts == ["2026-09-05T08:00:00", "2026-09-06T08:00:00", "2026-09-07T08:00:00"]


def test_exdate_excludes_matching_occurrence() -> None:
    text = _wrap(
        "BEGIN:VEVENT\n"
        "UID:exdate@example.com\n"
        "DTSTART;TZID=Asia/Tokyo:20260905T080000\n"
        "RRULE:FREQ=DAILY;COUNT=3\n"
        "EXDATE;TZID=Asia/Tokyo:20260906T080000\n"
        "SUMMARY:EXDATEの確認\n"
        "END:VEVENT"
    )
    events = ics_mod.parse_ics(text, today=TODAY, local_tz=JST)
    starts = sorted(e["start"] for e in events)
    assert starts == ["2026-09-05T08:00:00", "2026-09-07T08:00:00"]


def test_window_limits_recurrence_expansion_to_window_days() -> None:
    """窓（`window_days`）が掛かるのは**繰り返しの展開だけ**（ADR-012 D5 改訂）。"""
    text = _wrap(
        "BEGIN:VEVENT\n"
        "UID:longrun@example.com\n"
        "DTSTART;TZID=Asia/Tokyo:20260905T080000\n"
        "RRULE:FREQ=DAILY;COUNT=30\n"
        "SUMMARY:長期間の毎日\n"
        "END:VEVENT"
    )
    events = ics_mod.parse_ics(text, today=TODAY, window_days=5, local_tz=JST)
    starts = sorted(e["start"] for e in events)
    assert starts[0] == "2026-09-05T08:00:00"
    assert starts[-1] == "2026-09-10T08:00:00"  # today + 5日
    assert len(starts) == 6


# --- 単発の予定には窓をかけない（D5 改訂・2026-09-05: 4か月先の旅行が消えてはいけない） ------------


def test_single_event_far_in_the_future_is_included_without_window_limit() -> None:
    """繰り返しの無い予定は、窓（既定90日）を超えていても取り込む——4か月先に予約した
    旅行のような実在する予定を manor が持たない、という取り込み漏れが起きてはいけない。
    """
    text = _wrap(
        "BEGIN:VEVENT\n"
        "UID:trip@example.com\n"
        "DTSTART;TZID=Asia/Tokyo:20270105T090000\n"  # today (2026-09-05) から120日以上先
        "SUMMARY:家族旅行\n"
        "END:VEVENT"
    )
    events = ics_mod.parse_ics(text, today=TODAY, local_tz=JST)  # window_days は既定(90)のまま
    assert len(events) == 1
    assert events[0]["start"] == "2027-01-05T09:00:00"


def test_single_event_before_today_is_excluded() -> None:
    """過去（今日より前）の単発の予定は取り込まない（窓をかけない＝無制限、ではない）。"""
    text = _wrap(
        "BEGIN:VEVENT\n"
        "UID:past@example.com\n"
        "DTSTART;TZID=Asia/Tokyo:20260901T090000\n"  # today (2026-09-05) より前
        "SUMMARY:過ぎた予定\n"
        "END:VEVENT"
    )
    assert ics_mod.parse_ics(text, today=TODAY, local_tz=JST) == []


def test_unsupported_rrule_fallback_far_in_the_future_is_also_included() -> None:
    """対応できない RRULE のフォールバック（最初の1回）も単発扱いなので、窓の上限は
    かからない——DTSTART が今日以降でありさえすれば取り込む。
    """
    text = _wrap(
        "BEGIN:VEVENT\n"
        "UID:weirdfar@example.com\n"
        "DTSTART;TZID=Asia/Tokyo:20270201T090000\n"  # today から150日ほど先
        "RRULE:FREQ=MONTHLY;BYSETPOS=1;BYDAY=MO\n"
        "SUMMARY:複雑な繰り返し（遠い先）\n"
        "END:VEVENT"
    )
    events = ics_mod.parse_ics(text, today=TODAY, local_tz=JST)
    assert len(events) == 1
    assert events[0]["start"] == "2027-02-01T09:00:00"
    assert "[未対応の繰り返し]" in events[0]["note"]


def test_monthly_and_yearly_expand() -> None:
    text = _wrap(
        "BEGIN:VEVENT\n"
        "UID:monthly@example.com\n"
        "DTSTART;TZID=Asia/Tokyo:20260905T080000\n"
        "RRULE:FREQ=MONTHLY;COUNT=2\n"
        "SUMMARY:月次\n"
        "END:VEVENT"
    )
    events = ics_mod.parse_ics(text, today=TODAY, window_days=60, local_tz=JST)
    starts = sorted(e["start"] for e in events)
    assert starts == ["2026-09-05T08:00:00", "2026-10-05T08:00:00"]


# --- 対応できない RRULE のフォールバック（D5の核心） --------------------------------------------


def test_unsupported_rrule_falls_back_to_single_occurrence_with_note() -> None:
    text = _wrap(
        "BEGIN:VEVENT\n"
        "UID:weird@example.com\n"
        "DTSTART;TZID=Asia/Tokyo:20260905T080000\n"
        "RRULE:FREQ=MONTHLY;BYSETPOS=1;BYDAY=MO\n"
        "DESCRIPTION:月初の月曜\n"
        "SUMMARY:複雑な繰り返し\n"
        "END:VEVENT"
    )
    events = ics_mod.parse_ics(text, today=TODAY, local_tz=JST)
    assert len(events) == 1
    assert events[0]["start"] == "2026-09-05T08:00:00"
    assert "[未対応の繰り返し]" in events[0]["note"]
    assert "FREQ=MONTHLY;BYSETPOS=1;BYDAY=MO" in events[0]["note"]
    assert "月初の月曜" in events[0]["note"]  # 元の DESCRIPTION も失わない


def test_byday_on_non_weekly_freq_is_unsupported() -> None:
    parts = ics_mod.parse_rrule("FREQ=MONTHLY;BYDAY=MO")
    assert parts is not None
    assert ics_mod.rrule_supported(parts) is False


def test_count_and_until_together_is_unsupported() -> None:
    """RFC5545 でも COUNT と UNTIL の同時指定は不正。壊れた RRULE として非対応にする。"""
    parts = ics_mod.parse_rrule("FREQ=DAILY;COUNT=3;UNTIL=20260910T000000Z")
    assert parts is not None
    assert ics_mod.rrule_supported(parts) is False


def test_unknown_freq_is_unsupported() -> None:
    parts = ics_mod.parse_rrule("FREQ=SECONDLY;INTERVAL=30")
    assert parts is not None
    assert ics_mod.rrule_supported(parts) is False


def test_supported_rrule_shapes() -> None:
    for rrule in (
        "FREQ=DAILY",
        "FREQ=DAILY;INTERVAL=2",
        "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
        "FREQ=MONTHLY;COUNT=6",
        "FREQ=YEARLY;UNTIL=20301231T000000Z",
    ):
        parts = ics_mod.parse_rrule(rrule)
        assert parts is not None, rrule
        assert ics_mod.rrule_supported(parts) is True, rrule
