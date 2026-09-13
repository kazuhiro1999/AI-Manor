"""食品成分表からの栄養値の推定の試験（ADR-019 D6）。すべて合成データ・架空の家庭。

**成分表の実データは試験に入れない**（ADR-019 D1・D6）。取り込みは
`tests/fixtures/food_composition_sample.csv`（5 行の偽データ。列の見出しだけ八訂の
本表に似せてある——エネルギーが kJ と kcal の 2 列、たんぱく質が「アミノ酸組成による」と
素の 2 列）で見る。推定・名寄せは module の純粋関数に合成 dict を渡して見る。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from manor.errors import ManorError
from manor.staff.chef import nutrition, recipes

FIXTURE_CSV = Path(__file__).resolve().parent.parent / "fixtures" / "food_composition_sample.csv"


@pytest.fixture
def tables() -> nutrition.UnitTables:
    return nutrition.load_unit_tables()


def _food(code: str, name: str, **values) -> dict[str, object]:
    """成分表の1行（合成）。指定しなかった成分は 0。"""
    row: dict[str, object] = {
        "food_code": code, "food_group": code[:2], "name": name,
        "kcal": 0.0, "protein_g": 0.0, "fat_g": 0.0, "carb_g": 0.0, "salt_g": 0.0,
        "refuse_pct": 0.0,
    }
    row.update(values)
    return row


#: 名寄せ・推定の試験で使う偽の成分表（5 行 + 油。八訂の書き方に似せた名前）。
def _fake_foods() -> list[dict[str, object]]:
    return [
        _food("02017", "じゃがいも 塊茎 皮つき 生", kcal=59.0, protein_g=1.8, fat_g=0.1, carb_g=17.3, refuse_pct=1.0),
        _food("06153", "たまねぎ りん茎 生", kcal=33.0, protein_g=1.0, fat_g=0.1, carb_g=8.4, refuse_pct=6.0),
        _food("11221", "ぶた ひき肉 生", kcal=209.0, protein_g=17.7, fat_g=17.2, carb_g=0.1, salt_g=0.1),
        _food("12004", "鶏卵 全卵 生", kcal=142.0, protein_g=12.2, fat_g=10.2, carb_g=0.4, salt_g=0.4, refuse_pct=14.0),
        _food("17007", "こいくちしょうゆ", kcal=76.0, protein_g=7.7, carb_g=7.9, salt_g=14.5),
        _food("14006", "調合油", kcal=886.0, fat_g=100.0),
    ]


@pytest.fixture
def index(tables: nutrition.UnitTables) -> nutrition.FoodIndex:
    return nutrition.build_index(_fake_foods(), tables)


def _recipe(ingredients: list[dict[str, str]], *, servings: int | None = 2) -> dict[str, object]:
    return {"title": "試験用の一品", "servings": servings, "ingredients": ingredients}


# --- D3 分量 → グラム ------------------------------------------------------------------


def test_spoon_and_cup_use_lexicon_volumes(tables: nutrition.UnitTables) -> None:
    # 水は比重 1——大さじ 15ml・小さじ 5ml・カップ 200ml がそのまま g になる。
    assert nutrition.to_grams("1", "大さじ", "水", tables) == (15.0, "")
    assert nutrition.to_grams("1", "小さじ", "水", tables) == (5.0, "")
    assert nutrition.to_grams("1", "カップ", "水", tables) == (200.0, "")


def test_density_corrects_volume_for_oil_and_soy_sauce(tables: nutrition.UnitTables) -> None:
    """油 0.92・醤油 1.15（`[units.density]`）。ml をそのまま g にしない。"""
    oil, _ = nutrition.to_grams("1", "大さじ", "調合油", tables)
    soy, _ = nutrition.to_grams("1", "大さじ", "醤油", tables)
    assert oil == pytest.approx(13.8)
    assert soy == pytest.approx(17.25)


def test_weight_units(tables: nutrition.UnitTables) -> None:
    assert nutrition.to_grams("300", "g", "じゃがいも", tables) == (300.0, "")
    assert nutrition.to_grams("1", "kg", "じゃがいも", tables) == (1000.0, "")


def test_piece_weights_are_per_food_and_per_unit(tables: nutrition.UnitTables) -> None:
    """`[units.piece]` は**単位の語ごと**に持つ（「1 本」と「1 個」は別の量）。"""
    assert nutrition.to_grams("2", "個", "卵", tables) == (100.0, "")
    assert nutrition.to_grams("1", "片", "にんにく", tables) == (5.0, "")
    assert nutrition.to_grams("1", "本", "長ねぎ", tables) == (100.0, "")
    # 目安重量を持たない食品の「個」は換算できない（未解決。数えるのは分母だけ）。
    assert nutrition.to_grams("1", "個", "マンゴスチン", tables) == (None, nutrition.REASON_NO_PIECE)


def test_pinch_is_fixed_for_salt_and_pepper_and_zero_otherwise(tables: nutrition.UnitTables) -> None:
    """`[units.pinch]`: 塩ひとつまみ 1g・塩少々 0.5g・胡椒少々 0.2g。他は 0（数えない）。"""
    assert nutrition.to_grams("", "ひとつまみ", "塩", tables) == (1.0, "")
    assert nutrition.to_grams("", "少々", "塩", tables) == (0.5, "")
    assert nutrition.to_grams("", "少々", "胡椒", tables) == (0.2, "")
    assert nutrition.to_grams("", "少々", "レモン", tables) == (0.0, nutrition.NOT_COUNTED)
    assert nutrition.to_grams("", "適量", "水", tables) == (0.0, nutrition.NOT_COUNTED)


def test_number_parsing_handles_fractions_and_ranges() -> None:
    assert nutrition.parse_number("2") == 2.0
    assert nutrition.parse_number("1/2") == 0.5
    assert nutrition.parse_number("2と1/2") == 2.5
    assert nutrition.parse_number("２") == 2.0
    assert nutrition.parse_number("2〜3") == 2.5  # 範囲は平均
    assert nutrition.parse_number("たっぷり") is None


def test_unknown_unit_is_unresolved(tables: nutrition.UnitTables) -> None:
    assert nutrition.to_grams("3", "ヤード", "長ねぎ", tables) == (None, nutrition.REASON_UNKNOWN_UNIT)
    assert nutrition.to_grams("20", "㎝分", "長ねぎ", tables)[0] == pytest.approx(50.0)  # ㎝→cm・「分」は落とす
    assert nutrition.to_grams("", "", "長ねぎ", tables) == (None, nutrition.REASON_NO_AMOUNT)


# --- D2 名寄せの4段 --------------------------------------------------------------------


def test_alias_exact_match_wins(index: nutrition.FoodIndex, tables: nutrition.UnitTables) -> None:
    """① `chef_food_alias` の完全一致は、部分一致より先に効く。"""
    aliases = {nutrition.normalize_name("ひき肉", tables): "11221"}
    found = nutrition.resolve_food("ひき肉", index, aliases, tables)
    assert found is not None
    assert (found["food_code"], found["_stage"]) == ("11221", nutrition.STAGE_ALIAS)


def test_normalized_name_exact_match(index: nutrition.FoodIndex, tables: nutrition.UnitTables) -> None:
    """② 正規化して成分表の食品名と完全一致（「サラダ油」→ 同義語で「調合油」）。"""
    found = nutrition.resolve_food("サラダ油", index, {}, tables)
    assert found is not None
    assert (found["food_code"], found["_stage"]) == ("14006", nutrition.STAGE_NAME)


def test_partial_match_after_normalization(index: nutrition.FoodIndex, tables: nutrition.UnitTables) -> None:
    """③ 部分一致。かなの揺れ（たまねぎ/玉ねぎ・ぶた/豚）は `[food_normalize]` が寄せる。"""
    for name, code in (("玉ねぎ", "06153"), ("豚ひき肉", "11221"), ("卵", "12004"), ("しょうゆ", "17007")):
        found = nutrition.resolve_food(name, index, {}, tables)
        assert found is not None, name
        assert found["food_code"] == code, name
        assert found["_stage"] == nutrition.STAGE_PARTIAL, name


def test_partial_match_prefers_raw_over_boiled(tables: nutrition.UnitTables) -> None:
    """③ 複数当たったら「生」を優先する（ADR-019 D2 ③）。"""
    foods = [
        _food("06087", "こまつな 葉 ゆで", kcal=14.0),
        _food("06086", "こまつな 葉 生", kcal=13.0),
    ]
    index = nutrition.build_index(foods, tables)
    found = nutrition.resolve_food("小松菜", index, {}, tables)
    assert found is not None
    assert found["food_code"] == "06086"


def test_unresolved_when_nothing_matches(index: nutrition.FoodIndex, tables: nutrition.UnitTables) -> None:
    """④ 当たらなければ未解決（`None`）。"""
    assert nutrition.resolve_food("ナンプラー", index, {}, tables) is None


def test_normalization_drops_prep_words_and_parentheses(tables: nutrition.UnitTables) -> None:
    assert nutrition.normalize_name("玉ねぎ（中）", tables) == "玉ねぎ"
    assert nutrition.normalize_name("にんじんの薄切り", tables) == "にんじんの"
    assert nutrition.normalize_name("(A) しょうゆ", tables) == "醤油"
    # 同義語は二重に当てない（「長ねぎ」が「長長ねぎ」にならない）。
    assert nutrition.normalize_name("長ねぎ", tables) == "長ねぎ"
    assert nutrition.normalize_name("玉ねぎ", tables) == "玉ねぎ"


# --- D4 推定（合算・廃棄率・1 人前化・coverage） --------------------------------------


def test_estimate_sums_and_divides_by_servings(index: nutrition.FoodIndex, tables: nutrition.UnitTables) -> None:
    """`グラム × 成分/100` を足し、廃棄率を掛け、`servings` で割る。"""
    recipe = _recipe(
        [
            {"name": "じゃがいも", "qty": "300", "unit": "g"},   # 廃棄 1% → 297g
            {"name": "玉ねぎ", "qty": "1", "unit": "個"},         # 200g・廃棄 6% → 188g
            {"name": "豚ひき肉", "qty": "150", "unit": "g"},
            {"name": "醤油", "qty": "2", "unit": "大さじ"},        # 30ml × 1.15 = 34.5g
            {"name": "サラダ油", "qty": "1", "unit": "大さじ"},    # 15ml × 0.92 = 13.8g
        ],
        servings=2,
    )
    est = nutrition.estimate_nutrition(recipe, index, {}, tables)
    expected_kcal = (297 * 0.59 + 188 * 0.33 + 150 * 2.09 + 34.5 * 0.76 + 13.8 * 8.86) / 2
    assert est.nutrition["kcal"] == pytest.approx(round(expected_kcal, 1), abs=0.15)
    # 塩分は醤油と挽肉から（34.5 × 14.5/100 + 150 × 0.1/100）/ 2。
    assert est.nutrition["salt_g"] == pytest.approx((34.5 * 0.145 + 150 * 0.001) / 2, abs=0.1)
    assert est.coverage == 1.0
    assert est.unresolved == []
    assert est.servings == 2
    assert len(est.resolved) == 5


def test_estimate_without_servings_is_one_serving(index: nutrition.FoodIndex, tables: nutrition.UnitTables) -> None:
    recipe = _recipe([{"name": "豚ひき肉", "qty": "100", "unit": "g"}], servings=None)
    est = nutrition.estimate_nutrition(recipe, index, {}, tables)
    assert est.servings == 1
    assert est.nutrition["kcal"] == pytest.approx(209.0)


def test_coverage_is_weight_ratio_and_unknown_units_count_only_in_denominator(
    index: nutrition.FoodIndex, tables: nutrition.UnitTables
) -> None:
    recipe = _recipe(
        [
            {"name": "豚ひき肉", "qty": "100", "unit": "g"},   # 解決（100g）
            {"name": "ナンプラー", "qty": "100", "unit": "g"},  # 分量は分かるが成分表に無い
            {"name": "青じそ", "qty": "3", "unit": "ヤード"},   # 換算できない（目安重量で分母へ）
            {"name": "水", "qty": "", "unit": "適量"},          # 数えない（分母にも入らない）
        ],
        servings=1,
    )
    est = nutrition.estimate_nutrition(recipe, index, {}, tables)
    assert est.coverage == pytest.approx(100 / (100 + 100 + tables.unresolved_grams), abs=1e-4)
    reasons = {u["name"]: u["reason"] for u in est.unresolved}
    assert reasons == {"ナンプラー": nutrition.REASON_NO_FOOD, "青じそ": nutrition.REASON_UNKNOWN_UNIT}


def test_estimate_is_partial_below_threshold(index: nutrition.FoodIndex, tables: nutrition.UnitTables) -> None:
    recipe = _recipe([
        {"name": "豚ひき肉", "qty": "100", "unit": "g"},
        {"name": "ナンプラー", "qty": "100", "unit": "g"},
    ], servings=1)
    est = nutrition.estimate_nutrition(recipe, index, {}, tables)
    assert est.coverage == pytest.approx(0.5)
    assert est.is_partial(0.8) is True
    assert est.is_partial(0.4) is False


def test_estimate_with_no_match_is_not_ok(index: nutrition.FoodIndex, tables: nutrition.UnitTables) -> None:
    est = nutrition.estimate_nutrition(
        _recipe([{"name": "ナンプラー", "qty": "100", "unit": "g"}], servings=1), index, {}, tables
    )
    assert est.ok is False
    assert est.coverage == 0.0


# --- D1 取り込み（5 行の偽 CSV。列は見出し名で探す） ---------------------------------


def test_import_reads_columns_by_header_name(conn) -> None:
    """列の位置を決め打ちしない。kJ ではなく kcal の列、素の「たんぱく質」の列を採る。"""
    result = nutrition.import_food_table(conn, FIXTURE_CSV)
    assert result["rows"] == 5
    assert result["added"] == 5
    row = conn.execute("SELECT * FROM chef_food WHERE food_code = '11221'").fetchone()
    assert row["name"] == "ぶた ひき肉 生"
    assert row["kcal"] == 209.0          # kJ の 874 ではない
    assert row["protein_g"] == 17.7      # アミノ酸組成による 15.6 ではない
    assert row["fat_g"] == 17.2          # トリアシルグリセロール当量 16.1 ではない
    assert row["carb_g"] == 0.1          # 利用可能炭水化物（`-`）ではない
    assert row["per"] == nutrition.PER_100G
    assert row["source_version"] == nutrition.DEFAULT_SOURCE_VERSION
    # `Tr`（微量）と `-`（未測定）は 0 として読む。
    assert conn.execute("SELECT salt_g FROM chef_food WHERE food_code='02017'").fetchone()["salt_g"] == 0.0
    # 廃棄率・食品群も拾う（食品群が無ければ食品番号の先頭2桁）。
    assert conn.execute("SELECT refuse_pct, food_group FROM chef_food WHERE food_code='12004'").fetchone()["refuse_pct"] == 14.0


def test_import_is_idempotent(conn) -> None:
    nutrition.import_food_table(conn, FIXTURE_CSV)
    second = nutrition.import_food_table(conn, FIXTURE_CSV)
    assert second["added"] == 0
    assert second["updated"] == 5
    assert int(conn.execute("SELECT COUNT(*) AS n FROM chef_food").fetchone()["n"]) == 5


def test_import_rejects_a_file_without_the_header(conn, tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    with pytest.raises(ManorError) as exc:
        nutrition.import_food_table(conn, bad)
    assert exc.value.key == nutrition.ERR_FOOD_IMPORT_FAILED


def test_search_matches_by_name_substring(conn) -> None:
    nutrition.import_food_table(conn, FIXTURE_CSV)
    hits = nutrition.search_foods(conn, "ひき肉")
    assert [h["food_code"] for h in hits] == ["11221"]
    assert nutrition.search_foods(conn, "") == []


# --- D2 画面からの `manual` の上書き ----------------------------------------------------


def test_set_alias_normalizes_and_is_idempotent(conn) -> None:
    nutrition.import_food_table(conn, FIXTURE_CSV)
    saved = nutrition.set_alias(conn, "合いびき肉（うちの）", "11221")
    assert saved["alias"] == nutrition.normalize_name("合いびき肉")
    assert saved["confidence"] == "manual"
    nutrition.set_alias(conn, "合いびき肉", "11221", confidence="rule")
    rows = nutrition.list_aliases(conn)
    assert len(rows) == 1
    assert rows[0]["confidence"] == "rule"
    assert rows[0]["food_name"] == "ぶた ひき肉 生"


def test_set_alias_rejects_an_unknown_food(conn) -> None:
    nutrition.import_food_table(conn, FIXTURE_CSV)
    with pytest.raises(ManorError) as exc:
        nutrition.set_alias(conn, "なにか", "99999")
    assert exc.value.key == nutrition.ERR_FOOD_NOT_FOUND


def test_alias_makes_an_unresolved_ingredient_resolve(conn) -> None:
    nutrition.import_food_table(conn, FIXTURE_CSV)
    recipe_id = recipes.add(conn, _full_recipe([{"name": "合いびき肉", "qty": "200", "unit": "g"}]))
    before = nutrition.estimate_for_recipe(conn, recipe_id)
    assert before.ok is False
    nutrition.set_alias(conn, "合いびき肉", "11221")
    after = nutrition.estimate_for_recipe(conn, recipe_id)
    assert after.ok is True
    assert after.coverage == 1.0
    assert after.resolved[0]["stage"] == nutrition.STAGE_ALIAS


# --- D4 `chef_recipe_meta` への書き込み（rebuild） -------------------------------------


def _full_recipe(ingredients: list[dict[str, str]], *, servings: int = 2, title: str = "試験用の一品") -> dict:
    """`recipes.validate()` を通る最小のレシピ（材料だけ差し替える）。"""
    return {
        "title": title,
        "servings": servings,
        "total_minutes": 10,
        "ingredients": ingredients,
        "tools": ["鍋"],
        "phases": [{"id": "cook", "title": "煮る"}],
        "steps": [
            {"index": 1, "phase": "cook", "title": "煮る", "instruction": "煮る。", "completion": "manual"}
        ],
    }


def test_rebuild_writes_estimated_and_coverage(conn) -> None:
    nutrition.import_food_table(conn, FIXTURE_CSV)
    recipe_id = recipes.add(conn, _full_recipe([{"name": "豚ひき肉", "qty": "200", "unit": "g"}]))
    result = nutrition.rebuild(conn)
    assert result["updated"] == 1
    meta = recipes.get(conn, recipe_id)["meta"]
    assert meta["nutrition_source"] == "estimated"
    assert meta["kcal"] == pytest.approx(209.0)  # 200g / 2 人前 = 100g 相当
    coverage = conn.execute(
        "SELECT nutrition_coverage FROM chef_recipe_meta WHERE recipe_id = ?", (recipe_id,)
    ).fetchone()["nutrition_coverage"]
    assert coverage == 1.0


def test_rebuild_does_not_overwrite_site_or_manual(conn) -> None:
    nutrition.import_food_table(conn, FIXTURE_CSV)
    ingredients = [{"name": "豚ひき肉", "qty": "200", "unit": "g"}]
    site_id = recipes.add(conn, _full_recipe(ingredients, title="出典の値がある一品"))
    manual_id = recipes.add(conn, _full_recipe(ingredients, title="手で入れた一品"))
    recipes.set_meta(conn, site_id, nutrition_source="site", kcal=111.0)
    recipes.set_meta(conn, manual_id, kcal=222.0)  # 数値を渡すと自動で manual

    result = nutrition.rebuild(conn)
    assert result["updated"] == 0
    assert result["skipped"] == 2
    assert recipes.get(conn, site_id)["meta"]["kcal"] == 111.0
    assert recipes.get(conn, site_id)["meta"]["nutrition_source"] == "site"
    assert recipes.get(conn, manual_id)["meta"]["kcal"] == 222.0
    assert recipes.get(conn, manual_id)["meta"]["nutrition_source"] == "manual"


def test_rebuild_overwrites_a_previous_estimate(conn) -> None:
    nutrition.import_food_table(conn, FIXTURE_CSV)
    recipe_id = recipes.add(conn, _full_recipe([{"name": "豚ひき肉", "qty": "200", "unit": "g"}]))
    nutrition.rebuild(conn, recipe_id=recipe_id)
    recipes.update(conn, recipe_id, _full_recipe([{"name": "豚ひき肉", "qty": "400", "unit": "g"}]))
    nutrition.rebuild(conn, recipe_id=recipe_id)
    assert recipes.get(conn, recipe_id)["meta"]["kcal"] == pytest.approx(418.0)


def test_rebuild_skips_a_recipe_with_no_match(conn) -> None:
    nutrition.import_food_table(conn, FIXTURE_CSV)
    recipe_id = recipes.add(conn, _full_recipe([{"name": "ナンプラー", "qty": "10", "unit": "g"}]))
    result = nutrition.rebuild(conn)
    assert result["updated"] == 0
    # 0 を書かない（「栄養値がある」ことにしてしまわない）。
    assert recipes.get(conn, recipe_id)["meta"]["kcal"] is None
    assert recipes.get(conn, recipe_id)["meta"]["nutrition_source"] == ""


def test_refresh_is_quiet_without_the_food_table(conn) -> None:
    """成分表の表を入れていない home でも、登録の経路は失敗しない（ADR-019 D4）。"""
    conn.execute("DROP TABLE chef_food")
    recipe_id = recipes.add(conn, _full_recipe([{"name": "豚ひき肉", "qty": "200", "unit": "g"}]))
    result = nutrition.refresh(conn, recipe_id)
    assert result["available"] is False
    assert result["updated"] == 0


def test_unresolved_summary_groups_by_name(conn) -> None:
    nutrition.import_food_table(conn, FIXTURE_CSV)
    ingredients = [{"name": "ナンプラー", "qty": "10", "unit": "g"}]
    recipes.add(conn, _full_recipe(ingredients, title="一品目"))
    recipes.add(conn, _full_recipe(ingredients, title="二品目"))
    summary = nutrition.unresolved_summary(conn)
    assert summary["total"] == 1
    item = summary["items"][0]
    assert item["count"] == 2
    assert item["names"] == ["ナンプラー"]
    assert {r["title"] for r in item["recipes"]} == {"一品目", "二品目"}
