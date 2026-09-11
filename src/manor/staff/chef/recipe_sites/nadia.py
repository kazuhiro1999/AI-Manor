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

import html as html_lib
import re
import urllib.parse

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


def _text_only(fragment: str) -> str:
    return html_lib.unescape(re.sub(r"<[^>]+>", "", fragment or "")).strip()


def _attr(attrs_str: str, name: str) -> str:
    m = re.search(rf'{name}="([^"]*)"', attrs_str, re.I)
    return html_lib.unescape(m.group(1)) if m else ""


def _image_url_from_img_tag(attrs_str: str, base_url: str) -> str:
    """`src` → `data-src` → `srcSet`/`srcset` の先頭候補、の順（D8「data-src/srcset も見る」）。"""
    src = _attr(attrs_str, "src") or _attr(attrs_str, "data-src")
    if not src:
        srcset = _attr(attrs_str, "srcSet") or _attr(attrs_str, "srcset") or _attr(attrs_str, "data-srcset")
        if srcset:
            first = srcset.split(",")[0].strip()
            src = first.split(" ")[0] if first else ""
    return urllib.parse.urljoin(base_url, src) if src else ""


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
        qty_m = re.match(r"([\d./~〜]+)\s*(.*)", amount)
        qty, unit = (qty_m.group(1), qty_m.group(2)) if qty_m else ("", amount)
        ingredients.append(
            {"name": name, "qty": qty, "unit": unit, "group": _text_only(group_html)}
        )

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
