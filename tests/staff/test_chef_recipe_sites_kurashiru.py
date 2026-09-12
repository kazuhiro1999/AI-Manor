"""クラシル（kurashiru.com）の取り込みの試験（ADR-015 D7・2026-09-13 追加）。

**外部へは一切繋がない**——`kurashiru_min.html` は実物のページ（主人の実測 URL を
1回だけ取得）から構造だけを残し、工程を2件に切り詰めた抜粋。
"""

from __future__ import annotations

from pathlib import Path

from manor.staff.chef import recipe_import, recipes

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "sites"
URL = "https://www.kurashiru.com/recipes/e80338d5"


def _extract() -> dict:
    html = (FIXTURES_DIR / "kurashiru_min.html").read_text(encoding="utf-8")
    return recipe_import.extract_auto(html, URL)


def test_kurashiru_is_taken_through_the_jsonld_route() -> None:
    """クラシルは JSON-LD が本命（アダプタは補いだけ。`kurashiru.py` の docstring 参照）。"""
    result = _extract()
    assert result["ok"] is True
    assert result["method"] == "jsonld"
    recipes.validate(result["recipe"])  # 契約の形を満たす


def test_kurashiru_title_servings_and_time() -> None:
    """`recipeYield` が `"2 servings"`（英語）でも分量が入り、題名はサイト名の尾が付かない。"""
    recipe = _extract()["recipe"]
    assert recipe["title"] == "合挽き肉でジューシー！2色のそぼろ丼"
    assert "クラシル" not in recipe["title"]
    assert recipe["servings"] == 2
    assert recipe["total_minutes"] == 20


def test_kurashiru_counts_ingredients_and_steps() -> None:
    recipe = _extract()["recipe"]
    assert len(recipe["ingredients"]) == 12
    assert len(recipe["steps"]) == 2  # 実物は5件。フィクスチャは2件に切り詰めてある
    by_name = {ing["name"]: ing for ing in recipe["ingredients"]}
    assert by_name["ごはん"]["qty"] == "400"
    assert by_name["ごはん"]["unit"] == "g"
    assert by_name["顆粒和風だし"]["unit"] == "小さじ"


def test_kurashiru_hero_image_comes_from_og_image_not_jsonld() -> None:
    """D8 の順の例外。JSON-LD の `image` は動画の小さな正方形サムネイル
    （`..._square_normal.jpg`）で、og:image に同じ絵の大きい版（`..._square_large.jpg`）が
    ある——アダプタの `extract_hints` が og:image を候補の先頭に置く。
    """
    result = _extract()
    assert result["recipe"]["hero_image"].endswith("compressed_thumbnail_square_large.jpg?1789223525")
    assert "_normal.jpg" not in result["recipe"]["hero_image"]
    assert result["warnings"] == []  # 完成画像が取れているので警告は出ない


def test_kurashiru_has_no_step_images() -> None:
    """実測: 工程写真はページに1枚も無い（工程は動画で見せる作り）。ロゴ・カテゴリの
    サムネイルを工程写真として当ててしまわないことを確かめる。
    """
    recipe = _extract()["recipe"]
    assert [s["image"] for s in recipe["steps"]] == [None, None]


def test_kurashiru_recipe_category_feeds_classification() -> None:
    """`recipeCategory`（「ごはんもの,卵料理,肉,ひき肉」）が3軸とタグへ渡る。"""
    meta = _extract()["recipe"]["meta"]
    assert meta["category"] == "ご飯もの"
    assert meta["main_ingredient"] == "肉"
    assert meta["cuisine"] == "和食"  # 材料の「顆粒和風だし」から
    assert meta["tags"] == ["ごはんもの", "卵料理", "肉", "ひき肉"]


def test_kurashiru_has_no_nutrition() -> None:
    """実測: 栄養価の表示が無い（HTML に `kcal` の語も現れない）。"""
    meta = _extract()["recipe"]["meta"]
    assert "kcal" not in meta
    assert "nutrition_source" not in meta
