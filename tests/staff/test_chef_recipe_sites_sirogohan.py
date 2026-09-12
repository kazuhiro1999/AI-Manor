"""白ごはん.com（sirogohan.com）の取り込みの試験（ADR-015 D7・2026-09-13 追加）。

**外部へは一切繋がない**——`sirogohan_min.html` は実物のページ（主人の実測 URL を
1回だけ取得）から構造だけを残し、工程の段落を各ブロック2本に切り詰めた抜粋。

このサイトは **JSON-LD が無い**ので、アダプタが唯一の取り込み口になる。
"""

from __future__ import annotations

from pathlib import Path

from manor.staff.chef import recipe_import, recipes

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "sites"
URL = "https://www.sirogohan.com/recipe/siokaraage/"


def _extract() -> dict:
    html = (FIXTURES_DIR / "sirogohan_min.html").read_text(encoding="utf-8")
    return recipe_import.extract_auto(html, URL)


def test_sirogohan_is_taken_through_the_adapter_route() -> None:
    result = _extract()
    assert result["ok"] is True
    assert result["method"] == "adapter:sirogohan"
    recipes.validate(result["recipe"])


def test_sirogohan_title_servings_and_time() -> None:
    """分量は**全角数字**（`(２人分)`）、調理時間は本文の「調理時間：30分」から。"""
    recipe = _extract()["recipe"]
    assert recipe["title"] == "やみつきの旨さ！塩唐揚げのレシピ/作り方"
    assert recipe["servings"] == 2
    assert recipe["total_minutes"] == 30


def test_sirogohan_ingredients_split_on_the_ellipsis_and_keep_groups() -> None:
    """名前と量は全角スペース＋`…` で分かれる。`<ul class="a-list">` はグループ A。"""
    recipe = _extract()["recipe"]
    assert len(recipe["ingredients"]) == 10
    assert [ing["group"] for ing in recipe["ingredients"]] == [""] * 4 + ["A"] * 6

    by_name = {ing["name"]: ing for ing in recipe["ingredients"]}
    assert by_name["片栗粉"]["qty"] == "4"
    assert by_name["片栗粉"]["unit"] == "大さじ"
    assert by_name["塩"]["unit"] == "小さじ"
    assert by_name["塩"]["group"] == "A"
    # 量の語彙に無い書き方（「適量」「少々」）は unit にそのまま残す（D7「今より悪くしない」）。
    assert by_name["揚げ油"]["unit"] == "適量"
    assert by_name["こしょう"]["unit"] == "少々"


def test_sirogohan_steps_are_paragraphs_with_notes_skipped() -> None:
    """1工程＝1段落。`class="note-text"` と「※」で始まる補足は工程にしない
    （`sirogohan.py` の「1工程＝1段落にした理由」参照）。
    """
    recipe = _extract()["recipe"]
    assert len(recipe["steps"]) == 4
    assert [s["index"] for s in recipe["steps"]] == [1, 2, 3, 4]
    assert all(not s["instruction"].startswith("※") for s in recipe["steps"])
    assert "冷たいまま" not in " ".join(s["instruction"] for s in recipe["steps"])
    assert all(len(s["title"]) <= 12 for s in recipe["steps"])


def test_sirogohan_step_images_follow_the_paragraph() -> None:
    """工程写真は段落の**直後**の `<ul class="howto-imglist-col1">` の先頭の1枚。
    写真の無い段落は `None` のまま（前後の工程の写真を横取りしない）。
    """
    images = [s["image"] for s in _extract()["recipe"]["steps"]]
    assert images[0] is None
    assert images[1] == "https://www.sirogohan.com/_files/recipe/images/karaage/karaageb1.JPG"
    assert images[2] == "https://www.sirogohan.com/_files/recipe/images/karaage/siokara6.JPG"
    assert images[3] == "https://www.sirogohan.com/_files/recipe/images/karaage/siokara8.JPG"


def test_sirogohan_hero_image_comes_from_recipe_main() -> None:
    result = _extract()
    assert result["recipe"]["hero_image"] == (
        "https://www.sirogohan.com/_files/recipe/images/karaage/siokara4628.JPG"
    )
    assert result["warnings"] == []


def test_sirogohan_category_and_keywords_feed_classification() -> None:
    """`<ul class="recipe-category">`（「肉のおかず」）と `<dl class="recipe-keyword">`
    （「から揚げ」「メイン料理」等）が3軸とタグへ渡る。
    """
    meta = _extract()["recipe"]["meta"]
    assert meta["category"] == "主菜"
    assert meta["main_ingredient"] == "肉"
    assert meta["tags"][0] == "肉のおかず"  # サイトの分類を先に置く
    assert "メイン料理" in meta["tags"]


def test_sirogohan_has_no_nutrition() -> None:
    """実測: 栄養価の表示が無い（HTML に `kcal`・「塩分」の語も現れない）。"""
    meta = _extract()["recipe"]["meta"]
    assert "kcal" not in meta
    assert "nutrition_source" not in meta
