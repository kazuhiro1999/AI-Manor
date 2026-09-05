"""chef（料理長）の試験（ADR-002 §3・§7）。すべて合成データ・架空の品目。

`manor.staff.chef.ops` の純粋関数は表駆動で境界を試す。
`manor chef ...` の CLI は `cli.main([...])` を直接回す（test_cli.py と同じ流儀）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manor import check as check_mod
from manor import cli
from manor import db as db_mod
from manor.staff.chef import ops


# --- ops.item_match（表駆動。ADR-002 §3「突き合わせの規則」） -----------------------


@pytest.mark.parametrize(
    "a, b, expected",
    [
        ("卵", "ゆで卵", True),  # 規則1: 含む
        ("ゆで卵", "卵", True),  # 対称性
        ("豚肉", "豚こま肉", True),  # 規則2: 2文字以上で先頭・末尾が一致
        ("豚こま肉", "豚肉", True),
        ("牛乳", "牛肉", False),  # 当たらない例（先頭は一致するが末尾が違う）
        ("にんじん", "にんじん", True),  # 完全一致（含む、で拾える）
        ("肉", "豚肉", True),  # 1文字でも「含む」なら当たる
        ("肉", "魚", False),  # 1文字同士で不一致
        ("", "卵", False),  # 空文字
        ("卵", "", False),
        ("キャベツ", "レタス", False),  # 無関係
    ],
)
def test_item_match_table(a: str, b: str, expected: bool) -> None:
    assert ops.item_match(a, b) is expected


def test_is_staple_uses_item_match() -> None:
    staples = ["塩", "醤油", "味噌"]
    assert ops.is_staple("醤油", staples) is True
    assert ops.is_staple("本醸造醤油", staples) is True  # 含む、で当たる
    assert ops.is_staple("豚肉", staples) is False


def test_check_missing_excludes_staples_and_reports_found() -> None:
    staples = ["醤油", "塩"]
    pantry_items = ["卵", "豚こま肉"]
    result = ops.check_missing(["卵", "牛肉", "醤油"], pantry_items, staples)
    names = [r["item"] for r in result]
    assert "醤油" not in names  # 基礎調味料は数えない
    by_item = {r["item"]: r for r in result}
    assert by_item["卵"]["found"] is True
    assert by_item["牛肉"]["found"] is False


# --- ops.sort_by_expiry / is_expiring（表駆動） -------------------------------------


def test_sort_by_expiry_puts_unknown_last() -> None:
    rows = [
        {"item": "b", "expires": None},
        {"item": "a", "expires": "2026-09-01"},
        {"item": "c", "expires": "2026-08-30"},
    ]
    ordered = [r["item"] for r in ops.sort_by_expiry(rows)]
    assert ordered == ["c", "a", "b"]


@pytest.mark.parametrize(
    "expires, today, days, expected",
    [
        (None, "2026-09-02", 3, True),  # 不明は安全側で対象に含める
        ("2026-09-02", "2026-09-02", 3, True),  # 今日
        ("2026-09-05", "2026-09-02", 3, True),  # ちょうど境界
        ("2026-09-06", "2026-09-02", 3, False),  # 境界の翌日
        ("2026-08-30", "2026-09-02", 3, True),  # 既に期限切れ
    ],
)
def test_is_expiring_table(expires: str | None, today: str, days: int, expected: bool) -> None:
    assert ops.is_expiring(expires, today, days) is expected


# --- ops.subtract_qty ---------------------------------------------------------------


@pytest.mark.parametrize(
    "current, amount, expected",
    [
        ("300", "100", "200"),
        ("3", "3", ""),  # ちょうど使い切り
        ("3", "5", ""),  # 足りない分もマイナスにせず使い切り扱い
        ("不明", "1", None),  # 数量不明なら差し引けない
        ("300", "不明", None),
    ],
)
def test_subtract_qty_table(current: str, amount: str, expected: str | None) -> None:
    assert ops.subtract_qty(current, amount) == expected


# --- ops.aggregate_week（表駆動。欠けた枠・planned 件数） ---------------------------


def test_aggregate_week_counts_missing_slots_and_planned() -> None:
    dish_types = {"焼き物": ["焼き"], "炒め物": ["炒め"]}
    categories = {"肉": ["豚肉"], "葉物": ["キャベツ"]}
    meals = [
        {
            "date": "2026-09-01",
            "slot": "dinner",
            "dish": "豚肉の生姜焼き",
            "ingredients": "豚肉、キャベツ",
            "planned": 0,
        },
        {
            "date": "2026-09-02",
            "slot": "dinner",
            "dish": "野菜炒め（予定）",
            "ingredients": "キャベツ",
            "planned": 1,
        },
    ]
    result = ops.aggregate_week(meals, "2026-09-01", "2026-09-02", dish_types, categories)
    assert result["dish_type_counts"] == {"焼き物": 1, "炒め物": 1}
    assert result["ingredient_category_counts"] == {"肉": 1, "葉物": 2}
    assert result["planned_count"] == 1
    # 2日 x 3食(breakfast/lunch/dinner) = 6枠。埋まっているのは dinner の2つだけ
    assert len(result["missing_slots"]) == 4
    assert result["coverage_rate"] == pytest.approx(round(2 / 6, 3))


def test_aggregate_week_all_slots_filled_has_no_missing() -> None:
    meals = [
        {"date": "2026-09-01", "slot": slot, "dish": "何か", "ingredients": "", "planned": 0}
        for slot in ("breakfast", "lunch", "dinner")
    ]
    result = ops.aggregate_week(meals, "2026-09-01", "2026-09-01", {}, {})
    assert result["missing_slots"] == []
    assert result["coverage_rate"] == 1.0


# --- ops.validate_date / validate_choice --------------------------------------------


def test_validate_date_rejects_bad_format() -> None:
    from manor.errors import ManorError

    assert ops.validate_date("2026-09-02") == "2026-09-02"
    with pytest.raises(ManorError):
        ops.validate_date("2026/09/02")
    with pytest.raises(ManorError):
        ops.validate_date("明日")


# --- CLI: 表名が chef_ で始まること -----------------------------------------------


def test_all_chef_tables_are_prefixed(home: Path) -> None:
    conn = db_mod.connect(home)
    try:
        tables = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'chef_%'"
            ).fetchall()
        }
    finally:
        conn.close()
    assert tables == {"chef_pantry", "chef_meal", "chef_shopping", "chef_taste"}


def test_manor_check_does_not_flag_c9_for_chef(home: Path) -> None:
    conn = db_mod.connect(home)
    try:
        violations = check_mod.check_c9(conn)
    finally:
        conn.close()
    assert "chef_pantry" not in violations
    assert "chef_meal" not in violations
    assert "chef_shopping" not in violations
    assert "chef_taste" not in violations


# --- CLI 一連（全コマンド最低1回。--json も複数回） ----------------------------------


def test_chef_cli_full_round_trip(home_path: Path, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANOR_TODAY", "2026-09-02")

    assert cli.main(["init"]) == 0
    init_out = capsys.readouterr().out
    assert "chef" in init_out  # 「部下: chef」が出ること

    # --- pantry ---
    assert cli.main(["chef", "pantry", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []

    assert (
        cli.main(
            [
                "chef", "pantry", "add", "ひき肉",
                "--qty", "200", "--unit", "g", "--expires", "2026-09-03", "--place", "冷蔵",
                "--json",
            ]
        )
        == 0
    )
    hikiniku_id = json.loads(capsys.readouterr().out)["id"]

    assert (
        cli.main(
            [
                "chef", "pantry", "add", "豚こま肉",
                "--qty", "300", "--unit", "g", "--expires", "2026-09-04", "--place", "冷蔵",
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        cli.main(
            [
                "chef", "pantry", "add", "卵",
                "--qty", "6", "--unit", "個", "--expires", "2026-09-20", "--place", "冷蔵",
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert cli.main(["chef", "pantry", "add", "キャベツ", "--place", "冷蔵"]) == 0
    capsys.readouterr()

    assert cli.main(["chef", "pantry", "list"]) == 0
    list_out = capsys.readouterr().out
    assert "ひき肉" in list_out and "キャベツ" in list_out

    # expiring: --days 3 以内 + 不明（キャベツ）を含み、卵（18日後）は含まない。不明は末尾
    assert cli.main(["chef", "pantry", "expiring", "--days", "3", "--json"]) == 0
    expiring = json.loads(capsys.readouterr().out)
    names = [r["item"] for r in expiring]
    assert names == ["ひき肉", "豚こま肉", "キャベツ"]
    assert "卵" not in names

    # missing: 卵は在庫にあり、牛肉は無い、醤油は基礎調味料なので数えない
    assert cli.main(["chef", "pantry", "missing", "卵,牛肉,醤油", "--json"]) == 0
    missing = json.loads(capsys.readouterr().out)
    by_item = {r["item"]: r for r in missing}
    assert by_item["卵"]["found"] is True
    assert by_item["牛肉"]["found"] is False
    assert "醤油" not in by_item

    # use: 数量を減らす
    assert cli.main(["chef", "pantry", "use", "ひき肉", "--qty", "50", "--json"]) == 0
    used = json.loads(capsys.readouterr().out)
    assert used["qty"] == "150"

    # use --all: 使い切り
    assert cli.main(["chef", "pantry", "use", "卵", "--all", "--json"]) == 0
    used_all = json.loads(capsys.readouterr().out)
    assert used_all["removed"] is True

    # remove: 存在しない品目は見つからない扱い（終了コード2）
    code = cli.main(["chef", "pantry", "remove", "存在しない品"])
    assert code == 2

    assert cli.main(["chef", "pantry", "remove", "豚こま肉"]) == 0
    capsys.readouterr()

    # --- shopping ---
    assert cli.main(["chef", "shopping", "add", "玉ねぎ", "--reason", "カレー用", "--aisle", "野菜"]) == 0
    capsys.readouterr()
    assert cli.main(["chef", "shopping", "add", "牛肉", "--reason", "カレー用", "--aisle", "肉魚"]) == 0
    capsys.readouterr()

    assert cli.main(["chef", "shopping", "list", "--json"]) == 0
    shopping_list = json.loads(capsys.readouterr().out)
    assert len(shopping_list) == 2

    # bought: リストに無い品目でも失敗しない。既に在庫にある品目（キャベツ）は重複して足さない
    assert (
        cli.main(
            ["chef", "shopping", "bought", "玉ねぎ,存在しない品,キャベツ", "--place", "常温", "--json"]
        )
        == 0
    )
    bought = json.loads(capsys.readouterr().out)
    by_bought_item = {r["item"]: r for r in bought}
    assert by_bought_item["玉ねぎ"]["crossed_off"] == ["玉ねぎ"]
    assert by_bought_item["玉ねぎ"]["added_to_pantry"] is True
    assert by_bought_item["存在しない品"]["crossed_off"] == []
    assert by_bought_item["存在しない品"]["added_to_pantry"] is True  # 失敗しない
    assert by_bought_item["キャベツ"]["added_to_pantry"] is False  # 重複して足さない
    assert by_bought_item["キャベツ"]["already_in_pantry"] == ["キャベツ"]

    assert cli.main(["chef", "shopping", "list"]) == 0
    capsys.readouterr()

    # --- meal ---
    assert (
        cli.main(
            [
                "chef", "meal", "log",
                "--date", "2026-08-31", "--slot", "dinner",
                "--dish", "豚肉の生姜焼き", "--ingredients", "豚肉、キャベツ",
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        cli.main(
            [
                "chef", "meal", "log",
                "--date", "2026-09-01", "--slot", "dinner",
                "--dish", "野菜炒め", "--ingredients", "キャベツ",
                "--planned", "--json",
            ]
        )
        == 0
    )
    planned_meal_id = json.loads(capsys.readouterr().out)["id"]

    assert cli.main(["chef", "meal", "confirm", str(planned_meal_id), "--json"]) == 0
    confirmed = json.loads(capsys.readouterr().out)
    assert confirmed["planned"] is False

    code = cli.main(["chef", "meal", "confirm", "99999"])
    assert code == 2
    capsys.readouterr()

    assert cli.main(["chef", "meal", "week", "--days", "7", "--json"]) == 0
    week = json.loads(capsys.readouterr().out)
    assert week["dish_type_counts"].get("焼き物") == 1
    assert week["dish_type_counts"].get("炒め物") == 1
    assert week["planned_count"] == 0  # confirm 済みなので0
    assert 0.0 <= week["coverage_rate"] <= 1.0

    assert cli.main(["chef", "meal", "week"]) == 0
    capsys.readouterr()

    # --- taste ---
    assert cli.main(["chef", "taste", "show", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []

    assert cli.main(["chef", "taste", "set", "allergies", "えび、かに"]) == 0
    capsys.readouterr()

    code = cli.main(["chef", "taste", "set", "not_a_valid_key", "x"])
    assert code == 2
    capsys.readouterr()

    assert cli.main(["chef", "taste", "show", "--json"]) == 0
    taste = json.loads(capsys.readouterr().out)
    assert {"key": "allergies", "value": "えび、かに"}.items() <= taste[0].items()

    # --- manor check は C9 で怒らない ---
    assert cli.main(["check", "--json"]) == 0
    check_out = json.loads(capsys.readouterr().out)
    assert check_out["ok"] is True
    assert check_out["results"]["C9"] == []


def test_pantry_expiring_and_missing_exit_2_when_pantry_empty(
    home_path: Path, capsys: pytest.CaptureFixture
) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()

    code = cli.main(["chef", "pantry", "expiring"])
    assert code == 2
    out = capsys.readouterr().out
    assert "申告" in out

    code = cli.main(["chef", "pantry", "missing", "卵,牛肉"])
    assert code == 2
    capsys.readouterr()
