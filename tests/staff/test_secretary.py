"""秘書 secretary の試験（ADR-002 §6・§7）。**合成データのみ**（架空の予定・控え）。

`src/manor/staff/secretary/` は実在のプラグインなので、他の部下の試験のように
`fake_staff` フィクスチャは要らない。`home_path` で `MANOR_HOME` を用意し、
`manor init`（または `db.init`）を通せば `manor.db.iter_staff_modules()` が
自動で拾う（ADR-001 §11「フォルダを1つ置くだけ」）。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from manor import check as check_mod
from manor import cli
from manor import db as db_mod
from manor.errors import ManorError
from manor.staff.secretary import ops

# --- ops.resolve_date（表駆動: 空・不明・過去・閏年・月越え） ------------------------------

_TODAY = date(2026, 9, 2)  # 水曜日（下の曜日系の試験が前提にする）

RESOLVE_DATE_CASES = [
    ("2026-09-02", _TODAY),
    ("2026-12-25", date(2026, 12, 25)),
    ("今日", _TODAY),
    ("today", _TODAY),
    ("明日", date(2026, 9, 3)),
    ("tomorrow", date(2026, 9, 3)),
    ("明後日", date(2026, 9, 4)),
    ("+0", _TODAY),
    ("+1", date(2026, 9, 3)),
    ("+30", date(2026, 10, 2)),
    ("9/10", date(2026, 9, 10)),   # 今年、まだ先
    ("9/2", _TODAY),               # 今年、今日そのもの（過ぎてはいない）
    ("1/1", date(2027, 1, 1)),     # 今年はもう過ぎている → 来年
]


@pytest.mark.parametrize("expr,expected", RESOLVE_DATE_CASES)
def test_resolve_date_table_driven(expr: str, expected: date) -> None:
    assert ops.resolve_date(expr, _TODAY) == expected


def test_resolve_date_weekday_today_returns_today() -> None:
    assert _TODAY.weekday() == 2  # 水曜日であることの前提を検算
    assert ops.resolve_date("水", _TODAY) == _TODAY
    assert ops.resolve_date("wed", _TODAY) == _TODAY


def test_resolve_date_weekday_future_returns_next_occurrence() -> None:
    assert ops.resolve_date("金", _TODAY) == date(2026, 9, 4)
    assert ops.resolve_date("FRI", _TODAY) == date(2026, 9, 4)  # 英語・大文字
    assert ops.resolve_date("月", _TODAY) == date(2026, 9, 7)   # 週をまたぐ


def test_resolve_date_next_week_weekday_always_next_week() -> None:
    # 今週の月曜は 2026-08-31。来週の火曜日は 2026-09-08（曜日が今日と同じでも次週へ進む）
    assert ops.resolve_date("来週の火", _TODAY) == date(2026, 9, 8)
    assert ops.resolve_date("来週の水", _TODAY) == date(2026, 9, 9)  # 今日と同じ曜日でも来週


def test_resolve_date_leap_year_within_year() -> None:
    assert ops.resolve_date("2/29", date(2024, 1, 1)) == date(2024, 2, 29)


def test_resolve_date_leap_year_already_passed_rolls_to_next_leap_year() -> None:
    # 2024/3/1 時点では今年の 2/29 は過ぎている。来年 (2025) は平年なので解決できない
    with pytest.raises(ManorError) as exc:
        ops.resolve_date("2/29", date(2024, 3, 1))
    assert exc.value.code == 2


def test_resolve_date_non_leap_year_raises() -> None:
    with pytest.raises(ManorError) as exc:
        ops.resolve_date("2/29", date(2025, 1, 1))
    assert exc.value.code == 2


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "asdf", "2026-13-40", "13/40", "0/5", "来週の水曜日", "来週の月曜日", "2026/09/02"],
)
def test_resolve_date_invalid_raises_code_2(bad: str) -> None:
    with pytest.raises(ManorError) as exc:
        ops.resolve_date(bad, _TODAY)
    assert exc.value.code == 2


# --- ops.validate_time / validate_datetime -------------------------------------------------


@pytest.mark.parametrize("value", ["09:00", "23:59", "00:00"])
def test_validate_time_accepts(value: str) -> None:
    assert ops.validate_time(value) == value


@pytest.mark.parametrize("value", ["9:00", "24:00", "09:60", "abc", ""])
def test_validate_time_rejects(value: str) -> None:
    with pytest.raises(ManorError):
        ops.validate_time(value)


@pytest.mark.parametrize("value", ["2026-09-02", "2026-09-02T10:00", "2026-09-02T10:00:30"])
def test_validate_datetime_accepts(value: str) -> None:
    assert ops.validate_datetime(value) == value


@pytest.mark.parametrize("value", ["2026/09/02", "明日", "2026-09-02 10:00", "not-a-date"])
def test_validate_datetime_rejects(value: str) -> None:
    with pytest.raises(ManorError):
        ops.validate_datetime(value)


# --- ops.scan_inbox_files（README を除く・既知は除く。中身は読まない） --------------------


def test_scan_inbox_files_excludes_readme_and_known(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "README.md").write_text("readme", encoding="utf-8")
    (inbox / "新規ファイル.txt").write_text("dummy", encoding="utf-8")
    (inbox / "既知.txt").write_text("dummy", encoding="utf-8")
    sub = inbox / "サブフォルダ"
    sub.mkdir()
    (sub / "中身.txt").write_text("dummy", encoding="utf-8")  # 直下のみ対象。無視される

    found = ops.scan_inbox_files(inbox, existing_refs={"既知.txt"})
    refs = {f["ref"] for f in found}
    assert refs == {"新規ファイル.txt"}


def test_scan_inbox_files_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert ops.scan_inbox_files(tmp_path / "no-such-dir", set()) == []


# --- ops.merge_agenda（4種混在・日付順・超過が先頭・空の日は無い） --------------------------


def test_merge_agenda_mixes_and_sorts_by_date_with_overdue_first() -> None:
    today = date(2026, 9, 2)
    events = [{"id": 1, "start": "2026-09-05T10:00", "title": "打ち合わせ", "place": "会議室"}]
    reminders = [
        {"id": 10, "on_date": "2026-08-30", "at_time": None, "text": "資料の締切"},  # 超過
        {"id": 11, "on_date": "2026-09-05", "at_time": "09:00", "text": "支払い"},
    ]
    milestones = [{"id": "M1", "date": "2026-09-03", "title": "節目A"}]
    tasks = [{"id": "T1", "due": "2026-09-04", "title": "課題A"}]

    items = ops.merge_agenda(
        events=events, reminders=reminders, milestones=milestones, tasks=tasks, today=today
    )

    # 超過（8/30 の控え）が先頭
    assert items[0]["id"] == 10
    assert items[0]["overdue"] is True

    # 残りは日付順（節目 9/3 → 課題 9/4 → 予定・控え 9/5）
    rest_dates = [it["date"] for it in items[1:]]
    assert rest_dates == sorted(rest_dates)
    assert rest_dates == ["2026-09-03", "2026-09-04", "2026-09-05", "2026-09-05"]


def test_merge_agenda_empty_day_produces_no_entry() -> None:
    today = date(2026, 9, 2)
    events = [{"id": 1, "start": "2026-09-03T10:00", "title": "A", "place": ""}]
    tasks = [{"id": "T9", "due": "2026-09-06", "title": "B"}]
    items = ops.merge_agenda(events=events, reminders=[], milestones=[], tasks=tasks, today=today)
    dates = {it["date"] for it in items}
    # 9/4・9/5 には何も無い（空の日は集合に現れない）
    assert "2026-09-04" not in dates
    assert "2026-09-05" not in dates
    assert dates == {"2026-09-03", "2026-09-06"}


def test_merge_agenda_empty_input_is_empty() -> None:
    assert ops.merge_agenda(events=[], reminders=[], milestones=[], tasks=[], today=date(2026, 9, 2)) == []


# --- ADR-012 D5: 対応できない RRULE の印を agenda の detail に出す --------------------------


def test_merge_agenda_event_detail_is_place_when_no_warning() -> None:
    """既存の挙動（place がそのまま detail になる）が壊れていないことの確認。"""
    events = [{"id": 1, "start": "2026-09-05T10:00", "title": "打ち合わせ", "place": "会議室", "note": ""}]
    items = ops.merge_agenda(events=events, reminders=[], milestones=[], tasks=[], today=date(2026, 9, 2))
    assert items[0]["detail"] == "会議室"


def test_merge_agenda_marks_events_with_unsupported_rrule_note() -> None:
    """`calendar.py`/`ics.py` が対応できない RRULE を最初の1回だけ取り込んだとき、
    note に残した印（`[未対応の繰り返し]`）が agenda の detail に短い警告として出ること
    （ADR-012 D5「出ていない予定があることが画面から見える形にする」の直接の確認）。
    """
    events = [
        {
            "id": 2,
            "start": "2026-09-05T10:00",
            "title": "複雑な繰り返し",
            "place": "会議室",
            "note": "元のメモ\n\n[未対応の繰り返し] RRULE:FREQ=MONTHLY;BYSETPOS=1;BYDAY=MO",
        }
    ]
    items = ops.merge_agenda(events=events, reminders=[], milestones=[], tasks=[], today=date(2026, 9, 2))
    assert "⚠繰り返し未対応" in items[0]["detail"]
    assert "会議室" in items[0]["detail"]  # place も失わない


def test_merge_agenda_marks_events_with_unsupported_rrule_note_even_without_place() -> None:
    events = [
        {
            "id": 3,
            "start": "2026-09-05T10:00",
            "title": "複雑な繰り返し",
            "place": "",
            "note": "[未対応の繰り返し] RRULE:FREQ=SECONDLY",
        }
    ]
    items = ops.merge_agenda(events=events, reminders=[], milestones=[], tasks=[], today=date(2026, 9, 2))
    assert items[0]["detail"] == "⚠繰り返し未対応"


def test_merge_agenda_marks_events_with_unresolved_tzid_note() -> None:
    """ADR-012 D4 改訂: `ics.py` が解決できなかった TZID を note に残したとき（`tzdata`
    でも解決できない滅多に無い地域・壊れた環境）、agenda の detail にも警告が出ること。
    """
    events = [
        {
            "id": 4,
            "start": "2026-09-05T10:00",
            "title": "壊れたTZIDの予定",
            "place": "",
            "note": "[TZID未解決] Not/AZone を解決できず、ローカル時刻として扱いました",
        }
    ]
    items = ops.merge_agenda(events=events, reminders=[], milestones=[], tasks=[], today=date(2026, 9, 2))
    assert items[0]["detail"] == "⚠時刻帯未解決"


def test_merge_agenda_shows_both_warnings_together() -> None:
    """未対応RRULEとTZID未解決が同じ予定に両方付くこともありうる（それぞれ独立の印）。"""
    events = [
        {
            "id": 5,
            "start": "2026-09-05T10:00",
            "title": "複合ケース",
            "place": "会議室",
            "note": (
                "[TZID未解決] Not/AZone を解決できず、ローカル時刻として扱いました"
                "\n\n[未対応の繰り返し] RRULE:FREQ=SECONDLY"
            ),
        }
    ]
    items = ops.merge_agenda(events=events, reminders=[], milestones=[], tasks=[], today=date(2026, 9, 2))
    assert "⚠繰り返し未対応" in items[0]["detail"]
    assert "⚠時刻帯未解決" in items[0]["detail"]
    assert "会議室" in items[0]["detail"]


# --- 表名の接頭辞・C9 の検算 -----------------------------------------------------------------


def test_manor_init_lists_secretary_and_applies_schema(home_path: Path) -> None:
    applied = db_mod.init(home_path)
    assert "secretary" in applied
    conn = db_mod.connect(home_path)
    try:
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()
    assert {"secretary_reminder", "secretary_event", "secretary_inbox"} <= tables
    assert all(t.startswith("secretary_") for t in tables if t.startswith("secretary"))


def test_manor_check_does_not_flag_c9(home_path: Path) -> None:
    db_mod.init(home_path)
    conn = db_mod.connect(home_path)
    try:
        violations = check_mod.check_c9(conn)
    finally:
        conn.close()
    assert "secretary_reminder" not in violations
    assert "secretary_event" not in violations
    assert "secretary_inbox" not in violations


def test_manor_init_cli_lists_secretary(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["init", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "secretary" in out["staff"]


# --- CLI 通し（全コマンド最低1回・--json を1つ以上） ----------------------------------------


def test_secretary_cli_full_flow(
    home_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("MANOR_TODAY", "2026-09-02")

    assert cli.main(["init"]) == 0
    capsys.readouterr()

    # remind add（--on は resolve_date を通る。復唱を確認）
    assert cli.main(["sec", "remind", "add", "支払いの控え", "--on", "明日", "--at", "09:00", "--json"]) == 0
    add_out = json.loads(capsys.readouterr().out)
    assert add_out["on_date"] == "2026-09-03"
    assert add_out["text"] == "支払いの控え"
    reminder_id = add_out["id"]

    # 期限切れの控えをもう1件（超過の検算用）
    assert cli.main(["sec", "remind", "add", "資料の締切（超過）", "--on", "2026-08-25"]) == 0
    capsys.readouterr()

    # remind due（超過が先頭に出ること）
    assert cli.main(["sec", "remind", "due", "--days", "7"]) == 0
    due_out = capsys.readouterr().out
    assert "超過" in due_out
    lines = [l for l in due_out.splitlines() if l.strip()]
    assert lines[0].startswith("超過")

    # remind done
    assert cli.main(["sec", "remind", "done", str(reminder_id)]) == 0
    capsys.readouterr()

    # remind list（--all で完了済みも見える）
    assert cli.main(["sec", "remind", "list", "--all"]) == 0
    list_out = capsys.readouterr().out
    assert "支払いの控え" in list_out

    # event add
    assert (
        cli.main(
            [
                "sec", "event", "add", "定例の打ち合わせ",
                "--start", "2026-09-05T10:00", "--end", "2026-09-05T11:00",
                "--place", "会議室", "--json",
            ]
        )
        == 0
    )
    event_out = json.loads(capsys.readouterr().out)
    event_id = event_out["id"]
    assert event_out["title"] == "定例の打ち合わせ"

    # event list
    assert cli.main(["sec", "event", "list", "--days", "10"]) == 0
    event_list_out = capsys.readouterr().out
    assert "定例の打ち合わせ" in event_list_out

    # core: milestone add / task add（core は secretary が読むだけの対象）
    assert cli.main(["milestone", "add", "節目イベント", "--date", "2026-09-04"]) == 0
    capsys.readouterr()
    assert cli.main(["task", "add", "書類を出す", "--due", "2026-09-06", "--json"]) == 0
    task_out = json.loads(capsys.readouterr().out)
    task_id = task_out["id"]

    # agenda（event・reminder・milestone・task.due の4種が混ざって出ること）
    assert cli.main(["sec", "agenda", "--days", "10", "--json"]) == 0
    agenda_out = json.loads(capsys.readouterr().out)
    kinds = {it["kind"] for it in agenda_out}
    assert kinds == {"event", "reminder", "milestone", "task"}
    dates = [it["date"] for it in agenda_out]
    non_overdue_dates = [it["date"] for it in agenda_out if not it["overdue"]]
    assert non_overdue_dates == sorted(non_overdue_dates)
    assert any(it["id"] == task_id for it in agenda_out if it["kind"] == "task")

    # agenda（テキスト）でも件名が出ること
    assert cli.main(["sec", "agenda", "--days", "10"]) == 0
    agenda_text = capsys.readouterr().out
    assert "節目イベント" in agenda_text
    assert "書類を出す" in agenda_text

    # inbox scan（合成ファイルを置いてから）
    inbox_dir = home_path / "inbox"
    (inbox_dir / "見積書.pdf").write_text("dummy", encoding="utf-8")
    assert cli.main(["sec", "inbox", "scan", "--json"]) == 0
    scan_out = json.loads(capsys.readouterr().out)
    assert any(a["ref"] == "見積書.pdf" for a in scan_out["added"])

    # 2回目の scan では増えないこと
    assert cli.main(["sec", "inbox", "scan", "--json"]) == 0
    scan_out2 = json.loads(capsys.readouterr().out)
    assert scan_out2["added"] == []

    # inbox list
    assert cli.main(["sec", "inbox", "list"]) == 0
    inbox_list_out = capsys.readouterr().out
    assert "見積書.pdf" in inbox_list_out

    conn = db_mod.connect(home_path)
    try:
        row = conn.execute(
            "SELECT id FROM secretary_inbox WHERE ref = ?", ("見積書.pdf",)
        ).fetchone()
        inbox_id = row["id"]
    finally:
        conn.close()

    # inbox route（ファイルは動かさない。routed_to/routed_at を入れるだけ）
    assert cli.main(["sec", "inbox", "route", str(inbox_id), "--to", "chef", "--json"]) == 0
    route_out = json.loads(capsys.readouterr().out)
    assert route_out["routed_to"] == "chef"
    assert (inbox_dir / "見積書.pdf").is_file()  # ファイルは元の場所のまま

    # resolve-date（単体でも引ける）
    assert cli.main(["sec", "resolve-date", "来週の金", "--json"]) == 0
    resolve_out = json.loads(capsys.readouterr().out)
    assert resolve_out["resolved"] == "2026-09-11"

    # event remove
    assert cli.main(["sec", "event", "remove", str(event_id)]) == 0
    capsys.readouterr()

    # check が壊れていないこと
    assert cli.main(["check", "--json"]) == 0
    check_out = json.loads(capsys.readouterr().out)
    assert check_out["ok"] is True


# --- agenda（CLI テキスト）: 空の日は見出しに出ない ------------------------------------------


def test_agenda_text_skips_empty_day_headers(
    home_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("MANOR_TODAY", "2026-09-02")
    assert cli.main(["init"]) == 0
    capsys.readouterr()

    # 9/3 と 9/8 にだけ予定を置く（9/4〜9/7 は何も無い）
    assert cli.main(["sec", "event", "add", "初日の予定", "--start", "2026-09-03T09:00"]) == 0
    capsys.readouterr()
    assert cli.main(["sec", "event", "add", "後日の予定", "--start", "2026-09-08T09:00"]) == 0
    capsys.readouterr()

    assert cli.main(["sec", "agenda", "--days", "10"]) == 0
    text = capsys.readouterr().out
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    assert "2026-09-03" in lines
    assert "2026-09-08" in lines
    for empty_day in ["2026-09-04", "2026-09-05", "2026-09-06", "2026-09-07"]:
        assert empty_day not in lines  # 空の日は見出しとして出ない


# --- 終了コード（0/1/2）の約束 --------------------------------------------------------------


def test_cli_resolve_date_invalid_expr_is_exit_2(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    code = cli.main(["sec", "resolve-date", "でたらめ"])
    assert code == 2


def test_cli_remind_done_unknown_id_is_exit_2(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    code = cli.main(["sec", "remind", "done", "9999"])
    assert code == 2


def test_cli_event_remove_unknown_id_is_exit_2(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    code = cli.main(["sec", "event", "remove", "9999"])
    assert code == 2


def test_cli_inbox_route_unknown_id_is_exit_2(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    code = cli.main(["sec", "inbox", "route", "9999", "--to", "chef"])
    assert code == 2


def test_cli_remind_add_malformed_time_is_exit_2(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    code = cli.main(["sec", "remind", "add", "テスト", "--on", "明日", "--at", "25:99"])
    assert code == 2


def test_cli_event_add_malformed_start_is_exit_2(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    code = cli.main(["sec", "event", "add", "テスト", "--start", "2026/09/02"])
    assert code == 2


def test_cli_inbox_route_blank_to_is_exit_2(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    inbox_dir = home_path / "inbox"
    (inbox_dir / "何か.txt").write_text("dummy", encoding="utf-8")
    assert cli.main(["sec", "inbox", "scan"]) == 0
    capsys.readouterr()
    conn = db_mod.connect(home_path)
    try:
        row = conn.execute("SELECT id FROM secretary_inbox LIMIT 1").fetchone()
    finally:
        conn.close()
    code = cli.main(["sec", "inbox", "route", str(row["id"]), "--to", "   "])
    assert code == 2
