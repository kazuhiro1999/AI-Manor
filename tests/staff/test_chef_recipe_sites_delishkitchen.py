"""DELISH KITCHEN（delishkitchen.tv）の取り込みの試験（ADR-015 D7・2026-09-13 追加）。

**外部へは一切繋がない**——`delishkitchen_min.html` は実物のページ（主人の実測 URL を
1回だけ取得）から構造だけを残した抜粋（工程は実物も2件）。

このサイトは JSON-LD が最も充実していて、アダプタが補うのは
①食塩相当量（`sodiumContent` ではなく本文の「塩分」表示から）
②英語の `recipeCategory`/`recipeCuisine` の読み替え、の2つだけ。
"""

from __future__ import annotations

from pathlib import Path

from manor.staff.chef import recipe_import, recipes
from manor.staff.chef.recipe_sites import delishkitchen

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "sites"
URL = "https://delishkitchen.tv/recipes/487319391726207399"


def _html() -> str:
    return (FIXTURES_DIR / "delishkitchen_min.html").read_text(encoding="utf-8")


def _extract() -> dict:
    return recipe_import.extract_auto(_html(), URL)


def test_delishkitchen_is_taken_through_the_jsonld_route() -> None:
    result = _extract()
    assert result["ok"] is True
    assert result["method"] == "jsonld"
    recipes.validate(result["recipe"])


def test_delishkitchen_title_servings_and_seconds_based_time() -> None:
    """`totalTime` が `"PT600S"`（**秒**）でも 10 分になる（`_iso8601_minutes` が秒を見る）。"""
    recipe = _extract()["recipe"]
    assert recipe["title"] == "箸が止まらない！ 塩昆布の無限キャベツ"
    assert recipe["servings"] == 2
    assert recipe["total_minutes"] == 10


def test_delishkitchen_counts_and_step_images() -> None:
    """工程写真は `HowToStep.image` から2枚とも入る（本文での穴埋めは要らない）。"""
    result = _extract()
    recipe = result["recipe"]
    assert len(recipe["ingredients"]) == 5
    assert len(recipe["steps"]) == 2
    assert [s["image"] for s in recipe["steps"]] == [
        "https://media.delishkitchen.tv/recipe/487319391726207399/steps/1.jpg?version=1715243327",
        "https://media.delishkitchen.tv/recipe/487319391726207399/steps/2.jpg?version=1715243327",
    ]
    assert not any("写真" in w for w in result["warnings"])


def test_delishkitchen_hero_image_comes_from_jsonld() -> None:
    result = _extract()
    assert result["recipe"]["hero_image"] == (
        "https://image.delishkitchen.tv/recipe/487319391726207399/1.jpg?version=1715871603&w=920"
    )
    assert result["warnings"] == []


def test_delishkitchen_nutrition_including_salt_from_the_page_display() -> None:
    """kcal/たんぱく質/脂質/炭水化物は JSON-LD から。**食塩相当量は本文の「塩分」表示**から
    ——JSON-LD の `sodiumContent` を食塩相当量として使わない全体の規則は変えていない
    （`delishkitchen.py` の docstring ①）。
    """
    meta = _extract()["recipe"]["meta"]
    assert meta["kcal"] == 65.0
    assert meta["protein_g"] == 2.3
    assert meta["fat_g"] == 4.0
    assert meta["carb_g"] == 7.2
    assert meta["salt_g"] == 0.8
    assert meta["nutrition_source"] == "site"


def test_delishkitchen_salt_comes_from_the_dom_not_from_sodium_content() -> None:
    """念のため経路を分けて確かめる: 本文の対（`nutrient-name`/`nutrient-amount`）から
    `salt_g` が取れる。ここが壊れたら `sodiumContent` に頼っていないことがすぐ分かる。
    """
    values = delishkitchen.extract_nutrition(_html())
    assert values["salt_g"] == 0.8
    assert values["kcal"] == 65.0
    assert "糖質" not in values  # 「糖質」は契約の鍵に無い（carb_g は炭水化物）


def test_delishkitchen_english_category_and_cuisine_are_mapped() -> None:
    """`"side dish"` → 副菜、`"Japanese"` → 和食。素材の軸は `keywords` の「キャベツ」から。
    `lexicon.toml` は触らず、アダプタ側で既存の語に寄せている。
    """
    meta = _extract()["recipe"]["meta"]
    assert meta["category"] == "副菜"
    assert meta["cuisine"] == "和食"
    assert meta["main_ingredient"] == "野菜"
    # 読み替えた語を先に、サイトの素の語（英語も含む）を後に並べる。
    assert meta["tags"][:2] == ["副菜", "和食"]
    assert "キャベツ" in meta["tags"]


def test_delishkitchen_adapter_reads_the_body_when_jsonld_is_gone() -> None:
    """経路②の保険。JSON-LD を落とした HTML でもアダプタが本文から拾える
    （実ページでは①が必ず勝つので、ここは JSON-LD が消えた回のための道）。
    """
    adapted = delishkitchen.extract(_html(), URL)
    assert adapted is not None
    assert adapted["title"] == "箸が止まらない！ 塩昆布の無限キャベツ"
    assert adapted["servings"] == 2
    assert len(adapted["ingredients"]) == 5
    assert len(adapted["raw_steps"]) == 2
    # 工程写真は `<video poster="…">`（`<img>` ではない）から。
    assert adapted["raw_steps"][0]["image"] == (
        "https://media.delishkitchen.tv/recipe/487319391726207399/steps/1.jpg?version=1715243327"
    )


def test_delishkitchen_steps_get_the_ingredients_they_use() -> None:
    """ADR-015 §3: 「全ての材料を入れ」は材料表ぜんぶへの参照として展開する。"""
    steps = recipe_import.extract_auto(_html(), URL)["recipe"]["steps"]
    assert steps[0]["ingredients_used"] == ["キャベツ"]
    assert steps[1]["ingredients_used"] == [
        "キャベツ", "塩昆布", "おろしにんにく", "白いりごま", "ごま油",
    ]
