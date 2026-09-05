"""家令（家計）steward の試験（ADR-002 §5・§7）。**合成データのみ**（架空の金額・名前）。

`src/manor/staff/steward/` は実在のプラグインなので、他の部下の試験のように
`fake_staff` フィクスチャは要らない。`home_path` で `MANOR_HOME` を用意し、
`manor init`（または `db.init`）を通せば `manor.db.iter_staff_modules()` が
自動で拾う（ADR-001 §11「フォルダを1つ置くだけ」）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from manor import check as check_mod
from manor import cli
from manor import db as db_mod
from manor.errors import ManorError
from manor.staff.steward import ops

# --- ops.advance（表駆動: 週・月・年・月末・閏年） ---------------------------------------

ADVANCE_CASES = [
    # (next_due, cycle, expected) -- 何を確かめるかはコメントで
    ("2026-09-02", "weekly", "2026-09-09"),   # 通常の週送り
    ("2026-09-02", "monthly", "2026-10-02"),  # 通常の月送り
    ("2026-12-15", "monthly", "2027-01-15"),  # 月送りで年をまたぐ
    ("2026-01-31", "monthly", "2026-02-28"),  # 月末丸め・平年（2026年は平年）
    ("2028-01-31", "monthly", "2028-02-29"),  # 月末丸め・うるう年（2028年はうるう年）
    ("2026-09-02", "yearly", "2027-09-02"),   # 通常の年送り
    ("2028-02-29", "yearly", "2029-02-28"),   # 2/29 + yearly、結果の年が平年なら 2/28 に丸める
]


@pytest.mark.parametrize("next_due,cycle,expected", ADVANCE_CASES)
def test_advance_table(next_due: str, cycle: str, expected: str) -> None:
    assert ops.advance(next_due, cycle) == expected


def test_advance_rejects_unknown_cycle() -> None:
    with pytest.raises(ManorError):
        ops.advance("2026-09-02", "daily")


def test_advance_rejects_malformed_date() -> None:
    with pytest.raises(ManorError):
        ops.advance("2026/09/02", "monthly")


# --- ops.month_summary（超過が先頭・予算の無い分類・収入の分離） -------------------------


def _expense_rows() -> list[dict]:
    return [
        {"date": "2026-09-01", "amount": 25000, "kind": "expense", "category": "食費"},
        {"date": "2026-09-05", "amount": 5000, "kind": "expense", "category": "娯楽"},
        {"date": "2026-09-10", "amount": 3000, "kind": "expense", "category": "書籍"},
        {"date": "2026-09-15", "amount": 300000, "kind": "income", "category": "給与"},
        {"date": "2026-08-20", "amount": 99999, "kind": "expense", "category": "食費"},  # 別月。除外される
    ]


def test_month_summary_over_budget_is_first() -> None:
    budgets = {"食費": 20000, "娯楽": 10000}
    summary = ops.month_summary(_expense_rows(), budgets, "2026-09")
    assert summary["expenses"][0]["category"] == "食費"
    assert summary["expenses"][0]["over"] is True
    assert summary["expenses"][0]["diff"] == -5000


def test_month_summary_category_without_budget() -> None:
    budgets = {"食費": 20000, "娯楽": 10000}
    summary = ops.month_summary(_expense_rows(), budgets, "2026-09")
    books = next(e for e in summary["expenses"] if e["category"] == "書籍")
    assert books["budget"] is None
    assert books["diff"] is None
    assert books["over"] is False


def test_month_summary_income_is_kept_separate() -> None:
    budgets = {"食費": 20000, "娯楽": 10000}
    summary = ops.month_summary(_expense_rows(), budgets, "2026-09")
    assert summary["income"] == 300000
    assert all(e["category"] != "給与" for e in summary["expenses"])
    assert summary["total_expense"] == 25000 + 5000 + 3000  # 8月分は含めない


def test_month_summary_rejects_malformed_ym() -> None:
    with pytest.raises(ManorError):
        ops.month_summary([], {}, "2026/09")


# --- ops.upcoming（超過の +N日） ---------------------------------------------------------


def _recurring_rows() -> list[dict]:
    return [
        {"name": "サブスクA", "next_due": "2026-08-25", "active": 1, "amount": 1000, "category": "娯楽"},
        {"name": "サブスクB", "next_due": "2026-09-10", "active": 1, "amount": 2000, "category": "通信"},
        {"name": "サブスクC", "next_due": "2026-09-25", "active": 1, "amount": 3000, "category": "通信"},
        {"name": "サブスクD", "next_due": "2026-08-01", "active": 0, "amount": 4000, "category": "娯楽"},
    ]


def test_upcoming_overdue_days_and_window() -> None:
    items = ops.upcoming(_recurring_rows(), "2026-09-02", 14)
    names = [i["name"] for i in items]
    # C は window（cutoff=09-16）外、D は非アクティブなので落ちる
    assert names == ["サブスクA", "サブスクB"]
    a = next(i for i in items if i["name"] == "サブスクA")
    assert a["overdue_days"] == 8


def test_upcoming_excludes_inactive_even_within_window() -> None:
    items = ops.upcoming(_recurring_rows(), "2026-09-02", 60)
    assert all(i["name"] != "サブスクD" for i in items)


# --- ops.trend（数えるだけ） --------------------------------------------------------------


def test_trend_counts_only_months_with_data() -> None:
    rows = [
        {"date": "2026-07-01", "amount": 1000, "kind": "expense", "category": "食費"},
        {"date": "2026-08-01", "amount": 2000, "kind": "expense", "category": "食費"},
        {"date": "2026-09-01", "amount": 3000, "kind": "income", "category": "給与"},
    ]
    result = ops.trend(rows, 6)
    assert [r["ym"] for r in result] == ["2026-07", "2026-08", "2026-09"]
    sept = result[-1]
    assert sept["total_income"] == 3000
    assert sept["total_expense"] == 0


def test_trend_limits_to_recent_months() -> None:
    rows = [
        {"date": f"2026-0{m}-01", "amount": 100, "kind": "expense", "category": "食費"}
        for m in range(1, 7)
    ]
    result = ops.trend(rows, 2)
    assert [r["ym"] for r in result] == ["2026-05", "2026-06"]


# --- ops.parse_amount（不正値の拒否） -----------------------------------------------------


@pytest.mark.parametrize("value", ["12.5", "-100", "abc", "", "1e3"])
def test_parse_amount_rejects_invalid(value: str) -> None:
    with pytest.raises(ManorError):
        ops.parse_amount(value)


@pytest.mark.parametrize("value,expected", [("0", 0), ("1000", 1000), (" 500 ", 500)])
def test_parse_amount_accepts_valid(value: str, expected: int) -> None:
    assert ops.parse_amount(value) == expected


# --- スキーマ検算: 表名の接頭辞・口座やカードの列が無いこと ------------------------------


def test_manor_init_lists_steward_and_applies_schema(home_path: Path) -> None:
    applied = db_mod.init(home_path)
    assert "steward" in applied
    conn = db_mod.connect(home_path)
    try:
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()
    assert {"steward_expense", "steward_recurring", "steward_budget"} <= tables


def test_manor_check_does_not_flag_c9(home_path: Path) -> None:
    db_mod.init(home_path)
    conn = db_mod.connect(home_path)
    try:
        violations = check_mod.check_c9(conn)
    finally:
        conn.close()
    assert "steward_expense" not in violations
    assert "steward_recurring" not in violations
    assert "steward_budget" not in violations


def test_manor_check_cli_is_ok_after_init(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["init", "--json"]) == 0
    init_out = json.loads(capsys.readouterr().out)
    assert "steward" in init_out["staff"]

    assert cli.main(["check", "--json"]) == 0
    check_out = json.loads(capsys.readouterr().out)
    assert check_out["ok"] is True


def test_schema_has_no_account_or_credential_columns(home_path: Path) -> None:
    """設計の絶対の線: 口座番号・カード番号・ログイン情報を持つ列を作らない（ADR-002 §5）。"""
    db_mod.init(home_path)
    conn = db_mod.connect(home_path)
    try:
        tables = [
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if str(r["name"]).startswith("steward_")
        ]
        assert tables, "steward の表が1つも見つかりません"
        forbidden = re.compile(r"account|card|password|token|secret", re.IGNORECASE)
        for table in tables:
            for col in conn.execute(f"PRAGMA table_info({table})").fetchall():
                assert not forbidden.search(str(col["name"])), (
                    f"{table}.{col['name']} が認証情報らしき列名です"
                )
    finally:
        conn.close()


# --- CLI 通し（全コマンド最低1回・--json を1つ・paid/stop の効果） -----------------------


def test_money_cli_full_flow(
    home_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("MANOR_TODAY", "2026-09-02")

    assert cli.main(["init"]) == 0
    capsys.readouterr()

    # budget set / show
    assert cli.main(["money", "budget", "set", "食費", "20000"]) == 0
    capsys.readouterr()
    assert cli.main(["money", "budget", "show"]) == 0
    assert "食費" in capsys.readouterr().out

    # log（支出・収入それぞれ1件）
    assert (
        cli.main(
            [
                "money", "log", "--amount", "3000", "--category", "食費",
                "--date", "2026-09-01", "--memo", "スーパー",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        cli.main(
            [
                "money", "log", "--amount", "250000", "--category", "給与",
                "--income", "--date", "2026-09-01",
            ]
        )
        == 0
    )
    capsys.readouterr()

    # recurring add / list
    assert (
        cli.main(
            [
                "money", "recurring", "add", "配信サービス", "--amount", "1500",
                "--cycle", "monthly", "--next", "2026-08-20",
                "--category", "娯楽", "--kind", "subscription",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert cli.main(["money", "recurring", "list"]) == 0
    assert "配信サービス" in capsys.readouterr().out

    # due（today=09-02, next_due=08-20 → 13日超過）
    assert cli.main(["money", "due", "--days", "30"]) == 0
    due_out = capsys.readouterr().out
    assert "+13日" in due_out
    assert "配信サービス" in due_out

    # paid（支出に1行足し next_due を1周期進める。--json で確認）
    assert cli.main(["money", "recurring", "paid", "配信サービス", "--json"]) == 0
    paid_out = json.loads(capsys.readouterr().out)
    assert paid_out["next_due"] == "2026-09-20"

    # month（超過分類・予算未設定分類・収入の分離を画面でも確認）
    assert cli.main(["money", "month", "--ym", "2026-09"]) == 0
    month_out = capsys.readouterr().out
    assert "食費" in month_out
    assert "娯楽" in month_out

    # trend
    assert cli.main(["money", "trend", "--months", "3"]) == 0
    capsys.readouterr()

    # stop
    assert cli.main(["money", "recurring", "stop", "配信サービス"]) == 0
    capsys.readouterr()

    conn = db_mod.connect(home_path)
    try:
        row = conn.execute(
            "SELECT active FROM steward_recurring WHERE name = ?", ("配信サービス",)
        ).fetchone()
        assert row["active"] == 0

        paid_row = conn.execute(
            "SELECT * FROM steward_expense WHERE memo LIKE '定期:%' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert paid_row is not None
        assert paid_row["category"] == "娯楽"
        assert paid_row["kind"] == "expense"
        assert paid_row["amount"] == 1500

        expense_count = conn.execute("SELECT COUNT(*) AS c FROM steward_expense").fetchone()["c"]
        assert expense_count == 3  # log x2 + paid x1
    finally:
        conn.close()


# --- 終了コード（0/1/2）の約束 ------------------------------------------------------------


def test_cli_log_rejects_invalid_amount_is_exit_2(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    code = cli.main(["money", "log", "--amount", "12.5", "--category", "食費"])
    assert code == 2


def test_cli_recurring_paid_unknown_ref_is_exit_2(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    code = cli.main(["money", "recurring", "paid", "存在しない名前"])
    assert code == 2


def test_cli_recurring_add_duplicate_name_is_exit_1(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    args = [
        "money", "recurring", "add", "重複用", "--amount", "500",
        "--cycle", "monthly", "--next", "2026-09-10", "--category", "娯楽",
    ]
    assert cli.main(args) == 0
    capsys.readouterr()
    assert cli.main(args) == 1


# --- CSV 取り込み（ADR-005 §2「imports」。合成データのみ） ------------------------------------


from manor.staff.steward import importer  # noqa: E402 - 既存のimport群の後にまとめる


def test_importer_normalize_date_accepts_iso_and_slash() -> None:
    assert importer.normalize_date("2026-09-02") == "2026-09-02"
    assert importer.normalize_date("2026/09/02") == "2026-09-02"
    assert importer.normalize_date("2026/9/2") == "2026-09-02"


def test_importer_normalize_date_rejects_unreadable() -> None:
    assert importer.normalize_date("見えない日付") is None
    assert importer.normalize_date("2026-13-40") is None


def test_importer_normalize_amount_strips_yen_comma_and_reads_sign() -> None:
    assert importer.normalize_amount("¥1,200") == (1200, False)
    assert importer.normalize_amount("-500") == (500, True)
    assert importer.normalize_amount("1,000") == (1000, False)


def test_importer_normalize_amount_rejects_decimal() -> None:
    assert importer.normalize_amount("12.5") is None
    assert importer.normalize_amount("見えない") is None


def test_importer_interpret_kind_known_words() -> None:
    assert importer.interpret_kind("支出") == "expense"
    assert importer.interpret_kind("収入") == "income"
    assert importer.interpret_kind("？？？") is None


def test_importer_compute_hash_stable_and_sensitive_to_memo() -> None:
    h1 = importer.compute_hash("2026-09-02", 1000, "スーパーで買い物")
    h2 = importer.compute_hash("2026-09-02", 1000, "スーパーで買い物")
    h3 = importer.compute_hash("2026-09-02", 1000, "別のメモ")
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 16


def test_importer_parse_map_builds_dict() -> None:
    got = importer.parse_map("date=日付,amount=金額,memo=内容")
    assert got == {"date": "日付", "amount": "金額", "memo": "内容"}


def test_importer_resolve_map_generic_requires_map() -> None:
    with pytest.raises(ManorError):
        importer.resolve_map("generic", None)


def test_importer_resolve_map_preset_can_be_overridden() -> None:
    m = importer.resolve_map("zaim", "category=分類")
    assert m["category"] == "分類"
    assert m["date"] == "日付"  # プリセットの推定のまま


def test_importer_resolve_map_unknown_format_rejected() -> None:
    with pytest.raises(ManorError):
        importer.resolve_map("kakeibon", None)


_GENERIC_MAP = "date=日付,amount=金額,category=カテゴリ,memo=内容,kind=種別"


def test_importer_parse_csv_reads_rows_and_flags_duplicates_and_unreadable() -> None:
    csv_text = (
        "日付,金額,カテゴリ,内容,種別\n"
        "2026-09-01,1000,食費,スーパー,支出\n"
        "2026-09-01,1000,食費,スーパー,支出\n"  # 同一行の重複（ファイル内）
        "2026/09/03,-500,給与,バイト代,\n"  # kind無し・負号 → income
        "読めない日付,100,雑費,謎,支出\n"  # 読めない行
    )
    result = importer.parse_csv(csv_text, importer.resolve_map("generic", _GENERIC_MAP))
    assert result.total == 4
    assert len(result.rows) == 2
    assert len(result.duplicates) == 1
    assert len(result.unreadable) == 1
    income_row = next(r for r in result.rows if r.kind == "income")
    assert income_row.amount == 500
    assert income_row.date == "2026-09-03"


def test_importer_parse_csv_dedups_against_existing_hashes() -> None:
    column_map = importer.resolve_map("generic", _GENERIC_MAP)
    csv_text = "日付,金額,カテゴリ,内容,種別\n2026-09-01,1000,食費,スーパー,支出\n"
    existing = frozenset({importer.compute_hash("2026-09-01", 1000, "スーパー")})
    result = importer.parse_csv(csv_text, column_map, existing_hashes=existing)
    assert result.rows == []
    assert len(result.duplicates) == 1


def _write_csv(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    path.write_bytes(text.encode(encoding))


def test_cli_money_import_dry_run_does_not_write(
    home_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("MANOR_TODAY", "2026-09-02")
    assert cli.main(["init"]) == 0
    capsys.readouterr()

    csv_path = tmp_path / "kakeibo.csv"
    _write_csv(csv_path, "日付,金額,カテゴリ,内容,種別\n2026-09-01,1200,食費,スーパー,支出\n")

    code = cli.main(
        ["money", "import", str(csv_path), "--format", "generic", "--map", _GENERIC_MAP, "--dry-run", "--json"]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["to_insert"] == 1

    conn = db_mod.connect(home_path)
    try:
        n = conn.execute("SELECT COUNT(*) AS n FROM steward_expense").fetchone()["n"]
    finally:
        conn.close()
    assert n == 0  # dry-run は書かない


def test_cli_money_import_commits_and_skips_duplicates_on_second_run(
    home_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("MANOR_TODAY", "2026-09-02")
    assert cli.main(["init"]) == 0
    capsys.readouterr()

    csv_path = tmp_path / "kakeibo2.csv"
    _write_csv(csv_path, "日付,金額,カテゴリ,内容,種別\n2026-09-01,1200,食費,スーパー,支出\n")

    args = ["money", "import", str(csv_path), "--format", "generic", "--map", _GENERIC_MAP, "--json"]
    assert cli.main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["inserted"] == 1

    assert cli.main(args) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["inserted"] == 0
    assert second["skipped"] == 1  # 同じ行は二重に入らない

    conn = db_mod.connect(home_path)
    try:
        n = conn.execute("SELECT COUNT(*) AS n FROM steward_expense").fetchone()["n"]
        row = conn.execute("SELECT import_hash FROM steward_expense").fetchone()
    finally:
        conn.close()
    assert n == 1
    assert row["import_hash"]


def test_cli_money_import_reads_cp932_encoding(
    home_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("MANOR_TODAY", "2026-09-02")
    assert cli.main(["init"]) == 0
    capsys.readouterr()

    csv_path = tmp_path / "kakeibo_cp932.csv"
    _write_csv(
        csv_path, "日付,金額,カテゴリ,内容,種別\n2026-09-01,800,食費,コンビニ,支出\n", encoding="cp932"
    )

    code = cli.main(
        [
            "money", "import", str(csv_path), "--format", "generic", "--map", _GENERIC_MAP,
            "--encoding", "cp932", "--json",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["inserted"] == 1


def test_cli_money_import_generic_without_map_is_exit_2(
    home_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    csv_path = tmp_path / "no_map.csv"
    _write_csv(csv_path, "日付,金額\n2026-09-01,100\n")
    code = cli.main(["money", "import", str(csv_path), "--format", "generic"])
    assert code == 2
