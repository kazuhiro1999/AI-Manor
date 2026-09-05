"""housekeeper（家政婦）の試験（ADR-002 §7）。すべて合成データ。

- `ops.py`（純粋関数）は表駆動で境界を試す
- CLI は `manor house ...` を最低1回ずつ、`--json` の形も確かめる
- `supply low --to-shopping`（唯一の他領域書き込み）は `chef_shopping` の有無・重複を試す
- 表名が `housekeeper_` で始まることと、`manor check` の C9 が怒らないことを検算する
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manor import check as check_mod
from manor import cli
from manor import db as db_mod
from manor.errors import ManorError
from manor.staff.housekeeper import ops

# chef が居ても居なくても試験が同じ結果になるよう、ADR-002 §3 の chef_shopping を
# 試験側で直接作る／消す（chef プラグインの有無に依存しない）。
_CHEF_SHOPPING_SCHEMA = """
CREATE TABLE IF NOT EXISTS chef_shopping (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
  aisle TEXT NOT NULL DEFAULT 'その他' CHECK (aisle IN ('野菜','肉魚','乳卵','主食','調味料','その他')),
  added_at TEXT NOT NULL, bought_at TEXT
);
"""


def _ensure_chef_shopping(home: Path) -> None:
    conn = db_mod.connect(home)
    try:
        conn.executescript(_CHEF_SHOPPING_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _drop_chef_shopping(home: Path) -> None:
    conn = db_mod.connect(home)
    try:
        conn.execute("DROP TABLE IF EXISTS chef_shopping")
        conn.commit()
    finally:
        conn.close()


# --- ops.parse_rule / ops.next_dates（表駆動） ---------------------------------------


@pytest.mark.parametrize(
    "rule,today,days,expected",
    [
        # weekly: 2026-09-01 は火曜。mon,thu は範囲内で 09-03(木) と 09-07(月)
        ("weekly:mon,thu", "2026-09-01", 7, ["2026-09-03", "2026-09-07"]),
        # monthly: 2026年9月の第2水曜=09-09、第4水曜=09-23
        ("monthly:2nd-wed,4th-wed", "2026-09-01", 29, ["2026-09-09", "2026-09-23"]),
        # biweekly: 起点日そのもの＋14日後
        ("biweekly:tue@2026-09-01", "2026-09-01", 14, ["2026-09-01", "2026-09-15"]),
        # date: 単発
        ("date:2026-09-15", "2026-09-01", 30, ["2026-09-15"]),
    ],
)
def test_next_dates_table_driven(rule: str, today: str, days: int, expected: list[str]) -> None:
    assert ops.next_dates(rule, today, days) == expected


def test_next_dates_month_end_no_fifth_weekday() -> None:
    # 2026年2月は水曜が4回しかない（4,11,18,25）。5th-wed は月末まで見ても0件。
    assert ops.next_dates("monthly:5th-wed", "2026-02-01", 27) == []


def test_next_dates_leap_year_date_matches() -> None:
    # 2028年はうるう年。2028-02-29 は実在する。
    assert ops.next_dates("date:2028-02-29", "2028-02-20", 10) == ["2028-02-29"]


def test_next_dates_empty_when_out_of_window() -> None:
    assert ops.next_dates("date:2026-09-15", "2026-09-01", 5) == []


@pytest.mark.parametrize(
    "rule",
    [
        "not-a-rule",  # kind: が無い
        "weekly:",  # 内容が空
        "weekly:monday",  # 曜日トークンが不正（mon〜sun ではない）
        "monthly:9th-wed",  # nth が範囲外（1〜5）
        "monthly:2nd-funday",  # 曜日が不正
        "biweekly:tue-2026-09-01",  # @ が無い
        "biweekly:tue@2026-9-1",  # 日付の形式が不正
        "date:2026-13-01",  # 実在しない日付（月が13）
        "date:2027-02-29",  # 実在しない日付（うるう年でない）
        "yearly:jan-01",  # kind 自体が不明
    ],
)
def test_parse_rule_rejects_invalid_grammar(rule: str) -> None:
    with pytest.raises(ManorError):
        ops.parse_rule(rule)


def test_parse_rule_biweekly_anchor_weekday_mismatch() -> None:
    # 2026-09-01 は火曜。mon を指定すると不一致で拒否される。
    with pytest.raises(ManorError):
        ops.parse_rule("biweekly:mon@2026-09-01")


def test_parse_date_rejects_non_iso_formats() -> None:
    for bad in ["2026/09/01", "9-1-2026", "2026-9-1", "20260901", ""]:
        with pytest.raises(ManorError):
            ops.parse_date(bad)


# --- ops.due_chores（表駆動: 未記録が先頭、超過日数降順） ----------------------------------


def test_due_chores_ordering_and_days_filter() -> None:
    rows = [
        {"id": 1, "name": "掃除機がけ", "cadence_days": 7, "last_done": "2026-08-20"},  # +6日
        {"id": 2, "name": "換気扇掃除", "cadence_days": 30, "last_done": None},  # 未記録
        {"id": 3, "name": "床拭き", "cadence_days": 3, "last_done": "2026-09-01"},  # あと2日
        {"id": 4, "name": "窓拭き", "cadence_days": 30, "last_done": "2026-09-01"},  # あと29日（先過ぎ）
    ]
    today = "2026-09-02"

    due_all = ops.due_chores(rows, today)
    assert [r["id"] for r in due_all] == [2, 1, 3, 4]  # 未記録が先頭、以後は超過日数の大きい順
    assert due_all[0]["overdue_days"] is None
    assert due_all[1]["overdue_days"] == 6
    assert due_all[2]["overdue_days"] == -2
    assert due_all[3]["overdue_days"] == -29

    due_windowed = ops.due_chores(rows, today, days=7)
    # 未記録(2)は常に残る。id=4 はあと29日先で --days 7 の範囲外なので落ちる。
    assert [r["id"] for r in due_windowed] == [2, 1, 3]


# --- ops.low_supplies（閾値 NULL は対象外） --------------------------------------------


def test_low_supplies_threshold_and_qty_null_excluded() -> None:
    rows = [
        {"item": "洗剤", "qty": 1.0, "unit": "本", "threshold": 2.0},  # 少ない
        {"item": "ラップ", "qty": 5.0, "unit": "本", "threshold": 2.0},  # 十分
        {"item": "ゴミ袋", "qty": None, "unit": "枚", "threshold": 10.0},  # qty 不明 → 対象外
        {"item": "漂白剤", "qty": 3.0, "unit": "本", "threshold": None},  # threshold 未設定 → 対象外
        {"item": "軍手", "qty": 2.0, "unit": "組", "threshold": 2.0},  # 閾値と同値 → 「少ない」（ADR-002 §9: 以下）
    ]
    low = ops.low_supplies(rows)
    # 不足が大きい順: 洗剤（-1）→ 軍手（0）
    assert [r["item"] for r in low] == ["洗剤", "軍手"]


# --- 表名の検算（C9 と二重でよい。担当側で先に気づく） ------------------------------------


def test_all_table_names_prefixed_with_housekeeper(home: Path) -> None:
    conn = db_mod.connect(home)
    try:
        names = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'housekeeper%'"
            ).fetchall()
        }
    finally:
        conn.close()
    expected = {
        "housekeeper_chore",
        "housekeeper_chore_log",
        "housekeeper_supply",
        "housekeeper_maintenance",
        "housekeeper_waste",
    }
    assert expected.issubset(names)
    assert all(n.startswith("housekeeper_") for n in names)


def test_check_c9_does_not_flag_housekeeper_tables(home: Path) -> None:
    conn = db_mod.connect(home)
    try:
        violations = check_mod.check_c9(conn)
    finally:
        conn.close()
    assert not any(v.startswith("housekeeper") for v in violations)


# --- CLI 一連（全コマンドを最低1回。--json も確かめる） ------------------------------------


def test_cli_full_round_trip(home_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("MANOR_TODAY", "2026-09-02")  # 火曜。明日09-03は木曜

    assert cli.main(["init"]) == 0
    out = capsys.readouterr().out
    assert "housekeeper" in out  # init の出力に部下の名が出る

    # --- chore ---
    assert cli.main(["house", "chore", "list"]) == 0
    assert capsys.readouterr().out.strip() == "（なし）"

    assert cli.main(
        ["house", "chore", "add", "トイレ掃除", "--every", "7", "--area", "浴室", "--json"]
    ) == 0
    add_out = json.loads(capsys.readouterr().out)
    assert add_out["name"] == "トイレ掃除"

    assert cli.main(
        ["house", "chore", "done", "トイレ掃除", "--on", "2026-08-20", "--note", "念入りに"]
    ) == 0
    capsys.readouterr()

    assert cli.main(["house", "chore", "due", "--json"]) == 0
    due_out = json.loads(capsys.readouterr().out)
    assert len(due_out) == 1
    assert due_out[0]["name"] == "トイレ掃除"
    assert due_out[0]["overdue_days"] == 6  # 2026-08-27 が次回、今日2026-09-02との差

    # --- waste ---
    assert cli.main(["house", "waste", "list"]) == 0
    assert capsys.readouterr().out.strip() == "（なし）"

    assert cli.main(["house", "waste", "add", "可燃", "--rule", "weekly:mon,thu"]) == 0
    capsys.readouterr()

    assert cli.main(["house", "waste", "next", "--days", "7", "--json"]) == 0
    next_out = json.loads(capsys.readouterr().out)
    assert next_out == [
        {"date": "2026-09-03", "kind": "可燃"},
        {"date": "2026-09-07", "kind": "可燃"},
    ]

    # --- supply ---
    assert cli.main(
        ["house", "supply", "add", "洗剤", "--qty", "1", "--unit", "本", "--threshold", "2"]
    ) == 0
    capsys.readouterr()

    assert cli.main(["house", "supply", "list", "--json"]) == 0
    supply_list_out = json.loads(capsys.readouterr().out)
    assert supply_list_out[0]["item"] == "洗剤"

    assert cli.main(["house", "supply", "set", "洗剤", "--qty", "0.5"]) == 0
    capsys.readouterr()

    assert cli.main(["house", "supply", "low", "--json"]) == 0
    low_out = json.loads(capsys.readouterr().out)
    assert low_out["low"][0]["item"] == "洗剤"
    assert low_out["low"][0]["qty"] == 0.5

    # --- maint ---
    assert cli.main(["house", "maint", "list"]) == 0
    assert capsys.readouterr().out.strip() == "（なし）"

    assert cli.main(
        ["house", "maint", "add", "エアコン", "--what", "フィルター掃除", "--every", "90", "--json"]
    ) == 0
    maint_add_out = json.loads(capsys.readouterr().out)
    maint_id = maint_add_out["id"]

    assert cli.main(["house", "maint", "done", str(maint_id), "--on", "2026-06-01"]) == 0
    capsys.readouterr()

    assert cli.main(["house", "maint", "due", "--days", "30", "--json"]) == 0
    maint_due_out = json.loads(capsys.readouterr().out)
    assert len(maint_due_out) == 1
    assert maint_due_out[0]["overdue_days"] == 3  # 2026-06-01+90=2026-08-30、今日との差

    # --- today: 5節のうち該当するものだけが出る（空の節は出ない） ---
    assert cli.main(["house", "today", "--json"]) == 0
    today_out = json.loads(capsys.readouterr().out)
    assert "当番の期限切れ" in today_out
    assert today_out["当番の期限切れ"][0]["name"] == "トイレ掃除"
    assert "今週の当番" not in today_out  # 唯一の当番は既に「期限切れ」側なので出ない
    assert today_out["明日のゴミ"] == ["可燃"]
    assert today_out["少ない消耗品"][0]["item"] == "洗剤"
    assert today_out["手入れの期日"][0]["item"] == "エアコン"

    # --- 文字列（非json）でも today が読めること ---
    assert cli.main(["house", "today"]) == 0
    today_text = capsys.readouterr().out
    assert "当番の期限切れ" in today_text
    assert "トイレ掃除" in today_text

    # --- 締め: manor check が C9 等で怒らない ---
    assert cli.main(["check", "--json"]) == 0
    check_out = json.loads(capsys.readouterr().out)
    assert check_out["ok"] is True


def test_cli_today_says_nothing_when_all_empty(home_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("MANOR_TODAY", "2026-09-02")
    cli.main(["init"])
    capsys.readouterr()
    assert cli.main(["house", "today"]) == 0
    assert capsys.readouterr().out.strip() == "今日、特に知らせることはありません"


# --- supply low --to-shopping（唯一の他領域書き込み） --------------------------------------


def test_supply_low_to_shopping_table_missing_does_not_crash(
    home_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("MANOR_TODAY", "2026-09-02")
    cli.main(["init"])
    capsys.readouterr()
    _drop_chef_shopping(home_path)  # chef 未導入の状況を確実に作る

    cli.main(["house", "supply", "add", "洗剤", "--qty", "1", "--threshold", "2"])
    capsys.readouterr()

    code = cli.main(["house", "supply", "low", "--to-shopping", "--json"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["to_shopping"]["table_missing"] is True
    assert out["to_shopping"]["added"] == []


def test_supply_low_to_shopping_adds_with_reason_and_skips_duplicate(
    home_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("MANOR_TODAY", "2026-09-02")
    cli.main(["init"])
    capsys.readouterr()
    _ensure_chef_shopping(home_path)

    cli.main(["house", "supply", "add", "洗剤", "--qty", "1", "--unit", "本", "--threshold", "2"])
    capsys.readouterr()
    cli.main(["house", "supply", "add", "ラップ", "--qty", "5", "--unit", "本", "--threshold", "2"])
    capsys.readouterr()

    code = cli.main(["house", "supply", "low", "--to-shopping", "--json"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["to_shopping"]["added"] == ["洗剤"]  # ラップは閾値を下回っていないので対象外
    assert out["to_shopping"]["skipped"] == []
    assert out["to_shopping"]["table_missing"] is False

    conn = db_mod.connect(home_path)
    try:
        row = conn.execute(
            "SELECT item, reason, aisle, bought_at FROM chef_shopping WHERE item='洗剤'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["aisle"] == "その他"
    assert "家政婦" in row["reason"]
    assert "1" in row["reason"]
    assert row["bought_at"] is None

    # 2度目は重複して足さない
    code2 = cli.main(["house", "supply", "low", "--to-shopping", "--json"])
    assert code2 == 0
    out2 = json.loads(capsys.readouterr().out)
    assert out2["to_shopping"]["added"] == []
    assert out2["to_shopping"]["skipped"] == ["洗剤"]

    conn = db_mod.connect(home_path)
    try:
        count = conn.execute("SELECT COUNT(*) AS c FROM chef_shopping WHERE item='洗剤'").fetchone()["c"]
    finally:
        conn.close()
    assert count == 1  # 重複行が増えていない


def test_supply_low_without_to_shopping_leaves_chef_shopping_untouched(
    home_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("MANOR_TODAY", "2026-09-02")
    cli.main(["init"])
    capsys.readouterr()
    _ensure_chef_shopping(home_path)

    cli.main(["house", "supply", "add", "洗剤", "--qty", "1", "--threshold", "2"])
    capsys.readouterr()

    assert cli.main(["house", "supply", "low"]) == 0
    capsys.readouterr()

    conn = db_mod.connect(home_path)
    try:
        count = conn.execute("SELECT COUNT(*) AS c FROM chef_shopping").fetchone()["c"]
    finally:
        conn.close()
    assert count == 0


# --- CLI の入力検証（日付形式・未知の参照） -------------------------------------------------


def test_cli_rejects_non_iso_date_for_chore_done(home_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("MANOR_TODAY", "2026-09-02")
    cli.main(["init"])
    capsys.readouterr()
    cli.main(["house", "chore", "add", "掃除機がけ", "--every", "7"])
    capsys.readouterr()

    code = cli.main(["house", "chore", "done", "掃除機がけ", "--on", "2026/09/01"])
    assert code == 1
    out = capsys.readouterr().out
    assert "YYYY-MM-DD" in out


def test_cli_unknown_chore_done_is_exit_2(home_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("MANOR_TODAY", "2026-09-02")
    cli.main(["init"])
    capsys.readouterr()
    code = cli.main(["house", "chore", "done", "存在しない当番"])
    assert code == 2


def test_cli_waste_add_rejects_bad_rule_grammar(home_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("MANOR_TODAY", "2026-09-02")
    cli.main(["init"])
    capsys.readouterr()
    code = cli.main(["house", "waste", "add", "資源", "--rule", "everyday"])
    assert code == 1
    # 登録が拒否され、表に残っていないこと
    conn = db_mod.connect(home_path)
    try:
        row = conn.execute("SELECT * FROM housekeeper_waste WHERE kind='資源'").fetchone()
    finally:
        conn.close()
    assert row is None


def test_cli_duplicate_chore_name_rejected(home_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("MANOR_TODAY", "2026-09-02")
    cli.main(["init"])
    capsys.readouterr()
    cli.main(["house", "chore", "add", "掃除機がけ", "--every", "7"])
    capsys.readouterr()
    code = cli.main(["house", "chore", "add", "掃除機がけ", "--every", "3"])
    assert code == 1
