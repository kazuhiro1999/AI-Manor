"""分類3軸（ADR-015 D9）の試験: `recipes.classify()` の推定と `recipes.set_meta()` の
語彙検算。すべて合成データ。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from manor.errors import ManorError
from manor.staff.chef import recipes


def _minimal_recipe(**overrides) -> dict:
    base = {
        "title": "試験用の一品",
        "servings": 1,
        "total_minutes": 5,
        "ingredients": [{"name": "水", "qty": "1", "unit": "L"}],
        "tools": ["鍋"],
        "phases": [{"id": "cook", "title": "煮る"}],
        "steps": [
            {
                "index": 1, "phase": "cook", "title": "沸かす",
                "instruction": "水を沸かす。", "completion": "manual",
            }
        ],
    }
    base.update(overrides)
    return base


# --- classify（推定） --------------------------------------------------------------------


def test_classify_pork_fried_rice_gives_meat_chinese_rice_dish() -> None:
    """タスクの実例: 豚バラ＋炒飯 → 肉・中華・ご飯もの。"""
    recipe = _minimal_recipe(
        title="豚バラ炒飯",
        ingredients=[{"name": "豚バラ肉"}, {"name": "ご飯"}, {"name": "卵"}],
    )
    result = recipes.classify(recipe)
    assert result == {"category": "ご飯もの", "main_ingredient": "肉", "cuisine": "中華"}


def test_classify_returns_empty_strings_when_nothing_matches() -> None:
    recipe = _minimal_recipe(title="ふしぎな一品", ingredients=[{"name": "謎の粉"}])
    result = recipes.classify(recipe)
    assert result == {"category": "", "main_ingredient": "", "cuisine": ""}


def test_classify_uses_site_tags_as_extra_hints() -> None:
    recipe = _minimal_recipe(title="鮭のホイル焼き", ingredients=[{"name": "鮭"}])
    result = recipes.classify(recipe, site_tags=["和食"])
    assert result["main_ingredient"] == "魚介"
    assert result["cuisine"] == "和食"


def test_classify_uses_existing_meta_tags() -> None:
    recipe = _minimal_recipe(title="謎の一品", ingredients=[{"name": "謎の粉"}])
    recipe["meta"] = {"tags": ["キムチ"]}
    result = recipes.classify(recipe)
    assert result["cuisine"] == "韓国"


# --- set_meta（語彙の検算） ---------------------------------------------------------------


def test_set_meta_accepts_valid_category_main_ingredient_cuisine(conn, home: Path) -> None:
    recipe_id = recipes.add(conn, _minimal_recipe())
    result = recipes.set_meta(
        conn, recipe_id, category="ご飯もの", main_ingredient="肉", cuisine="中華"
    )
    assert result["meta"]["category"] == "ご飯もの"
    assert result["meta"]["main_ingredient"] == "肉"
    assert result["meta"]["cuisine"] == "中華"


def test_set_meta_rejects_category_outside_vocabulary(conn, home: Path) -> None:
    recipe_id = recipes.add(conn, _minimal_recipe())
    with pytest.raises(ManorError) as exc_info:
        recipes.set_meta(conn, recipe_id, category="謎の分類")
    assert exc_info.value.code == 2


def test_set_meta_rejects_main_ingredient_outside_vocabulary(conn, home: Path) -> None:
    recipe_id = recipes.add(conn, _minimal_recipe())
    with pytest.raises(ManorError) as exc_info:
        recipes.set_meta(conn, recipe_id, main_ingredient="宇宙食")
    assert exc_info.value.code == 2


def test_set_meta_rejects_cuisine_outside_vocabulary(conn, home: Path) -> None:
    recipe_id = recipes.add(conn, _minimal_recipe())
    with pytest.raises(ManorError) as exc_info:
        recipes.set_meta(conn, recipe_id, cuisine="宇宙料理")
    assert exc_info.value.code == 2


def test_set_meta_allows_clearing_classification_with_empty_string(conn, home: Path) -> None:
    recipe_id = recipes.add(conn, _minimal_recipe())
    recipes.set_meta(conn, recipe_id, category="主菜")
    result = recipes.set_meta(conn, recipe_id, category="")
    assert result["meta"]["category"] == ""


# --- add() が下書きの meta を拾う（recipe_import との橋渡し） ------------------------------


def test_add_seeds_classification_from_recipe_meta(conn, home: Path) -> None:
    recipe = _minimal_recipe(title="豚バラ炒飯", ingredients=[{"name": "豚バラ肉"}])
    recipe["meta"] = {"category": "ご飯もの", "main_ingredient": "肉", "cuisine": "中華", "tags": ["主食"]}
    recipe_id = recipes.add(conn, recipe)
    got = recipes.get(conn, recipe_id)
    assert got["meta"]["category"] == "ご飯もの"
    assert got["meta"]["main_ingredient"] == "肉"
    assert got["meta"]["cuisine"] == "中華"
    assert got["meta"]["tags"] == ["主食"]


def test_add_without_meta_key_still_works(conn, home: Path) -> None:
    """`meta` を持たない従来どおりの契約 JSON も引き続き登録できる（後方互換）。"""
    recipe_id = recipes.add(conn, _minimal_recipe())
    got = recipes.get(conn, recipe_id)
    assert got["meta"]["category"] == ""
