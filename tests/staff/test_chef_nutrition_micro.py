"""足した5項目と野菜の量（ADR-021 D1）の推定と書き込みの試験。

成分表の実データは使わない（ADR-019 D6 と同じ）——合成の数行で「足す・割る・空は 0 と
読まない・`site` のレシピにも足した列だけは書く」を確かめる。
"""

from __future__ import annotations

import sqlite3

import pytest

from manor.staff.chef import nutrition, recipes


@pytest.fixture
def tables() -> nutrition.UnitTables:
    return nutrition.load_unit_tables()


def _food(code: str, name: str, **values) -> dict[str, object]:
    row: dict[str, object] = {
        "food_code": code, "food_group": code[:2], "name": name,
        "kcal": 0.0, "protein_g": 0.0, "fat_g": 0.0, "carb_g": 0.0, "salt_g": 0.0, "refuse_pct": 0.0,
        "fiber_g": 0.0, "potassium_mg": 0.0, "calcium_mg": 0.0, "iron_mg": 0.0, "vitamin_c_mg": 0.0,
    }
    row.update(values)
    return row


def _foods() -> list[dict[str, object]]:
    return [
        # キャベツ（野菜 06）: 100g でビタミンC 40 mg・食物繊維 2 g。廃棄率 10%。
        _food("06061", "キャベツ 結球葉 生", kcal=20.0, fiber_g=2.0, potassium_mg=200.0, vitamin_c_mg=40.0, refuse_pct=10.0),
        # わかめ（藻類 09）
        _food("09041", "わかめ 乾燥わかめ 素干し 水戻し", kcal=20.0, fiber_g=4.0, potassium_mg=400.0, calcium_mg=100.0),
        # 鶏肉（肉 11）: 野菜の量には数えない
        _food("11224", "にわとり 若どり もも 皮つき 生", kcal=190.0, protein_g=16.6, potassium_mg=300.0),
        # 足した列の値を持たない行（古い取り込み）
        _food("17007", "こいくちしょうゆ", kcal=76.0, salt_g=14.5, fiber_g=None, potassium_mg=None,
              calcium_mg=None, iron_mg=None, vitamin_c_mg=None),
    ]


def test_micro_sums_edible_grams_and_divides_by_servings(tables: nutrition.UnitTables) -> None:
    index = nutrition.build_index(_foods(), tables)
    recipe = {
        "title": "キャベツとわかめ",
        "servings": 2,
        "ingredients": [
            {"name": "キャベツ", "qty": "200", "unit": "g"},
            {"name": "わかめ", "qty": "50", "unit": "g"},
            {"name": "鶏もも肉", "qty": "100", "unit": "g"},
        ],
    }
    est = nutrition.estimate_nutrition(recipe, index, {"鶏もも肉": "11224"}, tables)
    # キャベツ 200g × 可食 90% = 180g → ビタミンC 72 mg、わかめ 50g → 0。2 人前で割る。
    assert est.micro["vitamin_c_mg"] == pytest.approx(36.0)
    assert est.micro["fiber_g"] == pytest.approx((180 * 2.0 / 100 + 50 * 4.0 / 100) / 2)
    # 野菜の量は野菜・藻類の可食部だけ（鶏肉は数えない）: (180 + 50) / 2
    assert est.micro[nutrition.VEG_G] == pytest.approx(115.0)
    assert est.micro_coverage == pytest.approx(1.0)


def test_food_without_micro_values_lowers_micro_coverage_not_the_values(tables: nutrition.UnitTables) -> None:
    """足した列が空の食品は 0 と読まず、解決率の分子から外す（5項目の coverage は下がらない）。"""
    index = nutrition.build_index(_foods(), tables)
    recipe = {
        "title": "キャベツの醤油和え",
        "servings": 1,
        "ingredients": [
            {"name": "キャベツ", "qty": "90", "unit": "g"},
            {"name": "醤油", "qty": "10", "unit": "g"},
        ],
    }
    est = nutrition.estimate_nutrition(recipe, index, {"醤油": "17007"}, tables)
    assert est.coverage == pytest.approx(1.0)
    assert est.micro_coverage == pytest.approx(0.9)


def test_blend_with_a_missing_micro_value_is_empty_not_zero(tables: nutrition.UnitTables) -> None:
    index = nutrition.build_index(_foods(), tables)
    row = nutrition.blend_row("混ぜ物", [{"food_code": "06061", "weight": 0.5}, {"food_code": "17007", "weight": 0.5}], index)
    assert row is not None
    assert row["fiber_g"] is None
    row = nutrition.blend_row("混ぜ物", [{"food_code": "06061", "weight": 0.5}, {"food_code": "09041", "weight": 0.5}], index)
    assert row["fiber_g"] == pytest.approx(3.0)


def _load_foods(conn: sqlite3.Connection) -> None:
    for f in _foods():
        conn.execute(
            "INSERT INTO chef_food (food_code, food_group, name, kcal, protein_g, fat_g, carb_g, salt_g,"
            " refuse_pct, per, source_version, updated_at, fiber_g, potassium_mg, calcium_mg, iron_mg, vitamin_c_mg)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '100g', 'test', '2026-09-25', ?, ?, ?, ?, ?)",
            (
                f["food_code"], f["food_group"], f["name"], f["kcal"], f["protein_g"], f["fat_g"], f["carb_g"],
                f["salt_g"], f["refuse_pct"], f["fiber_g"], f["potassium_mg"], f["calcium_mg"], f["iron_mg"],
                f["vitamin_c_mg"],
            ),
        )


def test_rebuild_writes_micro_even_for_site_recipes_without_touching_the_five(conn: sqlite3.Connection) -> None:
    """サイトの値（`site`）は5項目を上書きしない約束のまま、足した列だけは材料から書く。"""
    _load_foods(conn)
    recipe_id = recipes.add(
        conn,
        {
            "title": "キャベツのサラダ",
            "servings": 1,
            "ingredients": [{"name": "キャベツ", "qty": "100", "unit": "g"}],
            "phases": [{"id": "p", "title": "作る"}],
            "steps": [{"index": 1, "phase": "p", "title": "切る", "instruction": "切る。", "completion": "manual"}],
        },
    )
    recipes.set_meta(conn, recipe_id, kcal=999, protein_g=1, fat_g=1, carb_g=1, salt_g=0.5, nutrition_source="site")
    nutrition.rebuild(conn)
    row = conn.execute("SELECT * FROM chef_recipe_meta WHERE recipe_id = ?", (recipe_id,)).fetchone()
    assert row["kcal"] == 999 and row["nutrition_source"] == "site"
    assert row["vitamin_c_mg"] == pytest.approx(36.0)
    assert row["veg_g"] == pytest.approx(90.0)
    assert row["micro_coverage"] == pytest.approx(1.0)


def test_nutrition_payload_carries_micro(conn: sqlite3.Connection) -> None:
    _load_foods(conn)
    recipe_id = recipes.add(
        conn,
        {
            "title": "わかめの小鉢",
            "servings": 1,
            "ingredients": [{"name": "わかめ", "qty": "50", "unit": "g"}],
            "phases": [{"id": "p", "title": "作る"}],
            "steps": [{"index": 1, "phase": "p", "title": "戻す", "instruction": "戻す。", "completion": "manual"}],
        },
    )
    payload = nutrition.nutrition_payload(conn, recipe_id)
    assert payload["micro"]["fiber_g"] == pytest.approx(2.0)
    assert payload["micro"]["micro_coverage"] == pytest.approx(1.0)
