"""oceans-nadia.com 向けアダプタ（ADR-015 D7）。

**壊れる前提**——Nadia は CSS Modules のハッシュ付きクラス名
（`IngredientsList_list__0Zyys` のような）を使っており、ビルドのたびに末尾のハッシュが
変わる。クラス名は**先頭一致**（`IngredientsList_list` まで）で緩く拾い、末尾の
ハッシュは無視する。材料・工程が1つも拾えなければ `None` を返し、呼び出し側
（`recipe_import.extract_auto`）が JSON-LD／汎用へ落とす。

実物のページ（2026-09-12・主人の実測 URL）を1回だけ取得して構造を確認した
（`docs/design/ADR-015_recipe_book.md` §6 D8 の報告を参照）。試験には要点だけを
切り詰めた合成 HTML（`tests/fixtures/sites/nadia_min.html`）を使い、本文は
リポジトリに入れない。
"""

from __future__ import annotations

import re

from .. import recipe_shaping as shaping

NAME = "nadia"

_TITLE_RE = re.compile(r'<h1 class="RecipeTitle[^"]*">(.*?)</h1>', re.S)
_HERO_RE = re.compile(
    r'class="RecipeMainImage_modalImage[^"]*"[\s\S]{0,40}?<img\b([^>]*)>', re.S
)
_YIELD_RE = re.compile(r'class="RecipeHeading_bunryoYield[^"]*">([^<]*)</span>')
_CATEGORY_BADGE_RE = re.compile(r'class="RecipeTypeBadge[^"]*">([^<]*)</span>')

_INGREDIENT_LI_RE = re.compile(
    r'<div class="IngredientsList_group[^"]*">(.*?)</div>\s*'
    r'<div class="IngredientsList_ingredient[^"]*">(.*?)</div>\s*'
    r'<div class="IngredientsList_amount[^"]*">(.*?)</div>',
    re.S,
)

_STEP_RE = re.compile(
    r'<div class="CookingProcess_group[^"]*"><span>(\d+)</span></div>\s*'
    r'<div class="CookingProcess_textBox[^"]*">'
    r'<p class="CookingProcess_text[^"]*"><span>(.*?)</span></p>'
    r'(?:\s*<div class="CookingProcess_processImage[^"]*">(.*?)</div>)?',
    re.S,
)

_IMG_TAG_RE = re.compile(r"<img\b([^>]*)>")

#: 栄養価の表示（ADR-015 §6 追補。主人の指摘: 「Nadia AI の自動計算による推定値」欄）。
#: `<span class="RecipeInfo_head...">ラベル（SVGアイコン混じり）</span>`
#: `<span class="RecipeInfo_value...">数値<span class="RecipeInfo_unit...">単位</span></span>`
#: の並び（2026-09-12・主人の実測 URL を1回だけ取得して確認した構造）。
_NUTRITION_ITEM_RE = re.compile(
    r'class="RecipeInfo_head[^"]*">(.*?)</span>\s*'
    r'<span class="RecipeInfo_value[^"]*">\s*([\d.]+)',
    re.S,
)

#: ラベル語 → 契約の栄養キー（ADR-015 §3 `meta`）。**「糖質」は見ない**——契約の
#: `carb_g` は「炭水化物」に対応し、糖質・食物繊維の内訳までは持たない設計のため。
_NUTRITION_LABELS: tuple[tuple[str, str], ...] = (
    ("エネルギー", "kcal"),
    ("たんぱく質", "protein_g"),
    ("脂質", "fat_g"),
    ("炭水化物", "carb_g"),
    ("食塩相当量", "salt_g"),
)


def extract_nutrition(html: str) -> dict[str, float]:
    """栄養価の表示値（1人分）をラベル語の隣の数値から拾う（ADR-015 §6 追補）。

    JSON-LD の `nutrition`（`recipe_import._normalize_recipe_ld`）が優先だが、
    **食塩相当量（`salt_g`）は JSON-LD に無いことが多い**——Nadia の JSON-LD は
    `sodiumContent`（ナトリウム。食塩相当量とは別物で換算しない）しか持たないため、
    ここが実質唯一の出どころ。取れた分だけを返す（`recipe_import._merge_nutrition`
    が JSON-LD で埋まらなかった分だけをここから補う）。
    """
    text = shaping.strip_html_comments(html or "")
    out: dict[str, float] = {}
    for label_html, value in _NUTRITION_ITEM_RE.findall(text):
        label = _text_only(label_html)
        for word, key in _NUTRITION_LABELS:
            if word in label and key not in out:
                try:
                    out[key] = float(value)
                except ValueError:
                    pass
                break
    return out


#: 共通の小道具は `recipe_shaping` へ出してある（2026-09-13。アダプタが5つになり、
#: 同じ3関数を各ファイルへ写していたのをやめた——`data-src`/`srcset` の癖を直すときに
#: 1か所で済む）。名前はこのファイルの読み筋を変えないよう別名で受ける。
_text_only = shaping.text_only
_image_url_from_img_tag = shaping.image_url_from_img_tag


def extract(html: str, url: str) -> dict | None:
    html = shaping.strip_html_comments(html or "")
    ingredient_matches = _INGREDIENT_LI_RE.findall(html)
    step_matches = list(_STEP_RE.finditer(html))
    if not ingredient_matches or not step_matches:
        return None

    title_m = _TITLE_RE.search(html)
    title = _text_only(title_m.group(1)) if title_m else ""

    servings = None
    yield_m = _YIELD_RE.search(html)
    if yield_m:
        num = re.search(r"\d+", yield_m.group(1))
        if num:
            servings = int(num.group())

    ingredients = []
    for group_html, name_html, amount_html in ingredient_matches:
        name = _text_only(name_html)
        if not name:
            continue
        amount = _text_only(amount_html)
        group = _text_only(group_html)
        # 主人の実測（2026-09-12）: 「塩、にんにくチューブ」＋「各小さじ1/2」のように
        # 名前と量は DOM で既に分かれていても、1つの `<li>` が複数の材料を表すことがある
        # ——`shaping.split_grouped_ingredient` へ委ねる（ADR-015 §6 追補）。
        ingredients.extend(shaping.split_grouped_ingredient(name, amount, group=group))

    raw_steps = []
    for _order, instruction_html, image_html in (m.groups() for m in step_matches):
        instruction = _text_only(instruction_html)
        if not instruction:
            continue
        image = ""
        if image_html:
            img_tag = _IMG_TAG_RE.search(image_html)
            if img_tag:
                image = _image_url_from_img_tag(img_tag.group(1), url)
        raw_steps.append({"instruction": instruction, "image": image or None})

    if not raw_steps:
        return None

    hero_image = ""
    hero_m = _HERO_RE.search(html)
    if hero_m:
        hero_image = _image_url_from_img_tag(hero_m.group(1), url)

    site_tags = []
    badge_m = _CATEGORY_BADGE_RE.search(html)
    if badge_m:
        badge = _text_only(badge_m.group(1))
        if badge:
            site_tags.append(badge)

    return {
        "title": title,
        "servings": servings,
        "total_minutes": None,
        "ingredients": ingredients,
        "tools": [],
        "raw_steps": raw_steps,
        "hero_image": hero_image,
        "site_tags": site_tags,
    }
