"""同梱の正規化 CSV（`staff/chef/data/food_composition_8th_2023.csv`）の試験（ADR-019 §4 追補）。

主人「日本語の Excel データは全角も多いし面倒。JSON や SQL など他の扱いやすいデータ形式に
移しておくのはどうか」——`home/manor.db`（実物 2,538 行取り込み済み）から `manor chef food
export` で一度書き出し、リポジトリに同梱した。ここでは**同梱ファイルそのもの**と、それを
読む経路（`import_food_table()` を引数なしで呼ぶ・`manor chef food import` の既定・
`manor init` の静かな取り込み）だけを見る。`home/manor.db` 自体には一切触れない
（読み込みも書き込みもしない。実物とバイト単位で一致するかは検算の対象にしない）。
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from manor import cli, db as db_mod
from manor.staff.chef import cli as chef_cli
from manor.staff.chef import nutrition

CSV_PATH = nutrition.DEFAULT_FOOD_CSV_PATH
FIXTURE_CSV = Path(__file__).resolve().parent.parent / "fixtures" / "food_composition_sample.csv"

#: ADR-019 §4 追補: 2026-09-13 に `home/manor.db` へ実物を取り込んだときの件数。
BUNDLED_ROW_COUNT = 2538


def _read_raw_csv() -> tuple[list[str], list[list[str]]]:
    with CSV_PATH.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    return rows[0], rows[1:]


# --- 同梱ファイルそのもの（バイト単位の約束） ---------------------------------------------


def test_bundled_csv_exists_and_is_utf8_lf_without_bom() -> None:
    assert CSV_PATH.is_file(), "src/manor/staff/chef/data/ に同梱 CSV が無い"
    raw = CSV_PATH.read_bytes()
    assert b"\r\n" not in raw, "LF だけで書く（Windows の既定 CRLF にしない）"
    assert not raw.startswith(b"\xef\xbb\xbf"), "BOM を付けない"


def test_bundled_csv_has_exactly_the_nine_columns_in_order() -> None:
    header, _ = _read_raw_csv()
    assert header == list(nutrition.COMPACT_CSV_FIELDS)
    assert header == [
        "food_code", "food_group", "name", "kcal", "protein_g",
        "fat_g", "carb_g", "salt_g", "refuse_pct",
    ]


def test_bundled_csv_row_count() -> None:
    _, data = _read_raw_csv()
    assert len(data) == BUNDLED_ROW_COUNT


def test_bundled_csv_has_no_duplicate_food_codes() -> None:
    _, data = _read_raw_csv()
    codes = [row[0] for row in data]
    assert len(codes) == len(set(codes))


def test_bundled_csv_names_have_no_double_spaces() -> None:
    """空白の連続を半角1つに畳んである（実測: 「蒸し中華めん  ソテー」が1行だけあった）。"""
    _, data = _read_raw_csv()
    offenders = [row[0] for row in data if "  " in row[2]]
    assert offenders == []


def test_bundled_csv_names_are_not_empty_and_have_no_full_width_space() -> None:
    _, data = _read_raw_csv()
    for row in data:
        name = row[2]
        assert name.strip() != ""
        assert "　" not in name, f"{row[0]}: 全角空白が残っている"


def test_bundled_csv_numeric_fields_are_readable() -> None:
    """`nutrition.read_food_rows()` で全行の数値が読める（`Tr`/`-`/括弧は既に数値化済み）。

    `protein_g`/`fat_g` だけ、成分表に未測定の行が実在する（実物5行）ので空欄を許す。
    """
    rows = nutrition.read_food_rows(CSV_PATH)
    assert len(rows) == BUNDLED_ROW_COUNT
    for row in rows:
        assert isinstance(row["kcal"], float)
        assert isinstance(row["carb_g"], float)
        assert isinstance(row["salt_g"], float)
        assert isinstance(row["refuse_pct"], float)
        assert row["protein_g"] is None or isinstance(row["protein_g"], float)
        assert row["fat_g"] is None or isinstance(row["fat_g"], float)
    unmeasured_protein = [r["food_code"] for r in rows if r["protein_g"] is None]
    assert len(unmeasured_protein) == 5


def test_bundled_csv_a_known_row_reads_correctly() -> None:
    rows = {r["food_code"]: r for r in nutrition.read_food_rows(CSV_PATH)}
    row = rows["01001"]
    assert row["name"] == "アマランサス 玄穀"
    assert row["kcal"] == 343.0
    assert row["protein_g"] == 12.7


# --- 取り込みの既定（`manor chef food import` を引数なしで。ADR-019 §4 追補） -------------


def test_import_food_table_with_no_path_reads_the_bundled_csv(conn: sqlite3.Connection) -> None:
    result = nutrition.import_food_table(conn)
    assert result["path"] == str(CSV_PATH)
    assert result["rows"] == BUNDLED_ROW_COUNT
    assert result["added"] == BUNDLED_ROW_COUNT
    assert result["total"] == BUNDLED_ROW_COUNT
    row = conn.execute("SELECT * FROM chef_food WHERE food_code = '01001'").fetchone()
    assert row["name"] == "アマランサス 玄穀"
    assert row["kcal"] == 343.0
    assert row["source_version"] == nutrition.DEFAULT_SOURCE_VERSION


def test_cli_food_import_with_no_path_reads_the_bundled_csv_and_seeds_aliases(
    conn: sqlite3.Connection, home: Path
) -> None:
    """`manor chef food import`（path 省略）と同じ経路——直後に名寄せの種も入る。"""
    args = SimpleNamespace(path=None, source_version=nutrition.DEFAULT_SOURCE_VERSION, json=True)
    result = chef_cli.cmd_food_import(conn, home, args)
    assert result["rows"] == BUNDLED_ROW_COUNT
    assert result["total"] == BUNDLED_ROW_COUNT
    assert result["seed"]["aliases"] > 0


def test_cli_food_import_still_accepts_an_explicit_path(
    conn: sqlite3.Connection, home: Path
) -> None:
    """版を上げるときの経路（既定 CSV ではなく明示した道）は変わらない。"""
    args = SimpleNamespace(path=str(FIXTURE_CSV), source_version="9th-2030", json=True)
    result = chef_cli.cmd_food_import(conn, home, args)
    assert result["rows"] == 5
    assert result["source_version"] == "9th-2030"


# --- 書き出し（`manor chef food export`。ADR-019 §4 追補）。実物の同梱ファイルへは書かない ---


def test_export_food_table_round_trips_through_the_compact_csv(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """DB → CSV → DB の往復。**実物の同梱ファイルには書かない**（`path` を明示して隔離）。"""
    nutrition.import_food_table(conn, FIXTURE_CSV)
    out = tmp_path / "roundtrip.csv"
    result = nutrition.export_food_table(conn, out)
    assert result["rows"] == 5
    assert out.is_file()
    with out.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == list(nutrition.COMPACT_CSV_FIELDS)
    assert len(rows) - 1 == 5
    reimported = nutrition.read_food_rows(out)
    assert len(reimported) == 5
    by_code = {r["food_code"]: r for r in reimported}
    assert by_code["11221"]["name"] == "ぶた ひき肉 生"
    assert by_code["11221"]["kcal"] == 209.0


def test_cli_food_export_writes_to_the_given_path(
    conn: sqlite3.Connection, home: Path, tmp_path: Path
) -> None:
    nutrition.import_food_table(conn, FIXTURE_CSV)
    out = tmp_path / "out.csv"
    args = SimpleNamespace(path=str(out), json=True)
    result = chef_cli.cmd_food_export(conn, home, args)
    assert result["rows"] == 5
    assert out.is_file()


def test_export_default_path_is_the_bundled_csv_location() -> None:
    assert nutrition.DEFAULT_FOOD_CSV_PATH.name == "food_composition_8th_2023.csv"
    assert nutrition.DEFAULT_FOOD_CSV_PATH.parent.name == "data"


# --- `manor init` の静かな取り込み（ADR-019 §4 追補・`db.init` の docstring 参照） ----------


def test_db_init_seeds_chef_food_only_when_asked(tmp_path: Path) -> None:
    """`seed_chef_food=True`（CLI の `manor init` だけが渡す）でだけ同梱 CSV が入る。

    既定 `False` は `tests/conftest.py` の `home` フィクスチャ・`web.create_app` が使う
    素の呼び出しをそのまま守るため——既定を True にすると、チェフの試験が使う5行の
    偽データ・149行の抜粋が実物2,538行の後ろに積まれて汚染される（`db.init` の
    docstring・2026-09-13 実測）。
    """
    quiet_home = tmp_path / "quiet"
    db_mod.init(quiet_home)
    conn_quiet = sqlite3.connect(quiet_home / "manor.db")
    conn_quiet.row_factory = sqlite3.Row
    try:
        count = conn_quiet.execute("SELECT COUNT(*) AS n FROM chef_food").fetchone()["n"]
        assert count == 0
    finally:
        conn_quiet.close()

    seeded_home = tmp_path / "seeded"
    db_mod.init(seeded_home, seed_chef_food=True)
    conn_seeded = sqlite3.connect(seeded_home / "manor.db")
    conn_seeded.row_factory = sqlite3.Row
    try:
        count = conn_seeded.execute("SELECT COUNT(*) AS n FROM chef_food").fetchone()["n"]
        assert count == BUNDLED_ROW_COUNT
    finally:
        conn_seeded.close()


def test_db_init_seeding_is_idempotent_and_does_not_duplicate(tmp_path: Path) -> None:
    home_dir = tmp_path / "twice"
    db_mod.init(home_dir, seed_chef_food=True)
    db_mod.init(home_dir, seed_chef_food=True)  # 2回目（表は既に空でない）
    conn2 = sqlite3.connect(home_dir / "manor.db")
    conn2.row_factory = sqlite3.Row
    try:
        count = conn2.execute("SELECT COUNT(*) AS n FROM chef_food").fetchone()["n"]
        assert count == BUNDLED_ROW_COUNT
    finally:
        conn2.close()


def test_manor_init_cli_seeds_chef_food_quietly(home_path: Path, capsys) -> None:
    """`manor init`（CLI）を実際に回すと、印字せずに同梱 CSV が入る（ADR-019 §4 追補）。"""
    assert cli.main(["init"]) == 0
    out = capsys.readouterr().out
    assert "food_composition_8th_2023" not in out, "静かに（画面に出さず）取り込む約束"
    conn3 = sqlite3.connect(home_path / "manor.db")
    conn3.row_factory = sqlite3.Row
    try:
        count = conn3.execute("SELECT COUNT(*) AS n FROM chef_food").fetchone()["n"]
        assert count == BUNDLED_ROW_COUNT
    finally:
        conn3.close()
