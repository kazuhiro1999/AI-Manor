"""cookpad.com 向けアダプタ（ADR-015 D7）。

**壊れる前提**——実物のページ（`cookpad.com/jp/recipes/...`）を1回だけ取得して確認した
ところ、材料・手順はクライアント側（JS）で描画されており、静的 HTML には
JSON-LD（`extract_auto` の①）以外の構造化データがほぼ無かった（2026-09-12）。
つまり実際の Cookpad ページは大半が①（JSON-LD）で解決し、このアダプタが呼ばれるのは
JSON-LD を欠く変種（旧テンプレート・ミラーサイト等）だけという想定——**保険**として、
schema.org の `itemprop` 属性（`recipeIngredient`／`recipeInstructions`）と、
「作り方」「手順」の見出し直後の `<ol>/<ul>` だけを拾う。材料・手順のどちらかでも
拾えなければ `None`（呼び出し側が JSON-LD／汎用へ落とす）。
"""

from __future__ import annotations

import html as html_lib
import re
import urllib.parse

from .. import recipe_shaping as shaping

NAME = "cookpad"

_INGREDIENT_RE = re.compile(r'itemprop="recipeIngredient"[^>]*>(.*?)<', re.S)
_STEP_ITEMPROP_RE = re.compile(r'itemprop="recipeInstructions?"[\s\S]*?<li[^>]*>(.*?)</li>', re.S)
_STEP_HEADING_BLOCK_RE = re.compile(r'(?:作り方|手順)[\s\S]*?<(?:ol|ul)[^>]*>(.*?)</(?:ol|ul)>', re.S)
_LI_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.S)
_TITLE_RE = re.compile(r'itemprop="name"[^>]*>([^<]*)<')
_HERO_META_RE = re.compile(r'itemprop="image"[^>]*content="([^"]*)"')
_CATEGORY_LINK_RE = re.compile(r'href="[^"]*/categories/[^"]*"[^>]*>([^<]*)<')


#: 共通の小道具は `recipe_shaping` へ（2026-09-13。`nadia.py` と同じ理由）。
_text_only = shaping.text_only


def extract(html: str, url: str) -> dict | None:
    text = shaping.strip_html_comments(html or "")

    ingredients_raw = [t for t in (_text_only(m) for m in _INGREDIENT_RE.findall(text)) if t]

    step_items: list[str] = []
    heading_block = _STEP_HEADING_BLOCK_RE.search(text)
    if heading_block:
        step_items = _LI_RE.findall(heading_block.group(1))
    if not step_items:
        step_items = _STEP_ITEMPROP_RE.findall(text)
    steps_raw = [t for t in (_text_only(s) for s in step_items) if t]

    if not ingredients_raw or not steps_raw:
        return None

    title_m = _TITLE_RE.search(text)
    title = _text_only(title_m.group(1)) if title_m else ""

    hero_image = ""
    hero_m = _HERO_META_RE.search(text)
    if hero_m:
        hero_image = urllib.parse.urljoin(url, html_lib.unescape(hero_m.group(1)))

    site_tags = [t for t in (_text_only(m) for m in _CATEGORY_LINK_RE.findall(text)) if t]

    return {
        "title": title,
        "servings": None,
        "total_minutes": None,
        "ingredients": [ing for name in ingredients_raw for ing in shaping.parse_ingredient_line(name)],
        "tools": [],
        "raw_steps": [{"instruction": s, "image": None} for s in steps_raw],
        "hero_image": hero_image,
        "site_tags": site_tags,
    }
