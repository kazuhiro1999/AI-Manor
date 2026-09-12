"""sirogohan.com（白ごはん.com）向けアダプタ（ADR-015 D7・2026-09-13 追加）。

**このサイトには JSON-LD が無い**——実ページ（主人の実測 URL）を1回だけ取得して
確かめたところ、`application/ld+json` の script が1つも無かった。`itemscope
itemtype="http://schema.org/Recipe"` と `itemprop` は付いているが、材料・工程には
付いていない（題名・完成画像・説明だけ）。つまり**本文を直接読むアダプタが要る**
——ここが唯一の取り込み口で、経路②（アダプタ）が本命。

## 拾える構造（2026-09-13 の実測）

素の HTML で、class 名も `material`・`howto-block` のように**意味のある語**（ビルドの
ハッシュではない）。Nadia・クラシルより壊れにくいと見込めるが、約束は同じ「壊れる前提」
——1つでも欠ければ拾えた分だけ返し、材料・工程が空なら `None`。

| もの | どこ |
|---|---|
| 題名 | `<h1 id="recipe-name" itemprop="name">` |
| 分量 | 材料の見出し `<h2 class="material-ttl">…<span>(２人分)</span>`（**全角数字**） |
| 調理時間 | `<p id="cooking-time">調理時間：30分` |
| 材料 | `section.material` の `<ul class="disc-list">`（無印）と `<ul class="a-list">`（A） |
| 工程 | `section.howto` の `<div class="howto-block">` 内の `<p>` を1工程ずつ |
| 工程写真 | 工程の直後の `<ul class="howto-imglist-col1">` の先頭の `<img>` |
| 完成画像 | `<p id="recipe-main"><img itemprop="image">` |
| 3軸の手掛かり | `<ul class="recipe-category">` と `<dl class="recipe-keyword">` の語 |

**栄養価は無い**（表示自体が無く、HTML に `kcal`・「塩分」の語も現れない）。

## 1工程＝1段落にした理由

`howto-block` は「準備と味付け」「揚げ方」の2つしかなく、中に段落が4〜5本と写真が
並ぶ。ブロックを1工程にすると本文が数百字になり、料理中に読める単位から外れる
——段落ごとに切ると、写真の付き方（段落の直後に写真が来る）とも素直に合う。
補足の段落（`class="note-text"` や「※」で始まるもの）は工程にしない。
"""

from __future__ import annotations

import re

from .. import recipe_shaping as shaping

NAME = "sirogohan"

_TITLE_RE = re.compile(r'<h1[^>]*id="recipe-name"[^>]*>(.*?)</h1>', re.S)
_HERO_RE = re.compile(r'<p[^>]*id="recipe-main"[^>]*>\s*<img\b([^>]*)>', re.S)
_COOKING_TIME_RE = re.compile(r'id="cooking-time"[^>]*>([^<]*)')
_CATEGORY_RE = re.compile(r'<ul class="recipe-category">(.*?)</ul>', re.S)
_KEYWORD_RE = re.compile(r'<dl class="recipe-keyword">(.*?)</dl>', re.S)
_LINK_TEXT_RE = re.compile(r"<a\b[^>]*>(.*?)</a>", re.S)

#: 材料の節（見出し＋`<ul>` の並び）と工程の節。`section` は入れ子にならない作り。
_MATERIAL_SECTION_RE = re.compile(r'<section class="material[^"]*">(.*?)</section>', re.S)
_HOWTO_SECTION_RE = re.compile(r'<section class="howto[^"]*">(.*?)</section>', re.S)

_MATERIAL_HEADING_RE = re.compile(r'<h2 class="material-ttl[^"]*">(.*?)</h2>', re.S)
_MATERIAL_LIST_RE = re.compile(r'<ul class="([^"]*)"[^>]*>(.*?)</ul>', re.S)
_LI_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.S)

#: 分量（`(２人分)`）・調理時間（`調理時間：30分`）。全角数字も拾う
#: （`int("２")` は 2 になるので、数字部分はそのまま `int()` へ渡してよい）。
_SERVINGS_RE = re.compile(r"([\d０-９]+)\s*人分")
_MINUTES_RE = re.compile(r"([\d０-９]+)\s*分")

#: 材料1行の「名前」と「量」の区切り。実測は全角スペース＋三点リーダ＋全角スペース
#: （`鶏もも肉　…　1枚（約350ｇ）`）。`…` が無い行は全部を名前として扱う。
_AMOUNT_SEP_RE = re.compile(r"[\s　]*[…‥][\s　]*")

#: 工程の本文（`<p>`）と工程写真の並び（`<ul class="howto-imglist…">`）を**出てくる順に**拾う。
_HOWTO_TOKEN_RE = re.compile(
    r'<p\b([^>]*)>(.*?)</p>|<ul class="howto-imglist[^"]*"[^>]*>(.*?)</ul>', re.S
)
_IMG_TAG_RE = re.compile(r"<img\b([^>]*)>")

#: 工程にしない段落（補足・注意書き）。`class="note-text"` が付く形と、付かずに
#: 「※」で始まる形の両方が実測で出てきた。
_NOTE_CLASS_WORDS: tuple[str, ...] = ("note-text",)
_NOTE_PREFIXES: tuple[str, ...] = ("※", "◆")


def _first_number(text: str, pattern: re.Pattern[str]) -> int | None:
    m = pattern.search(text or "")
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _tags(html: str) -> list[str]:
    """カテゴリ（「肉のおかず」）とキーワード（「から揚げ」「メイン料理」等）を
    出典のタグとして集める。`classify` は**最初に当たった語**で決まるので、
    カテゴリ（サイトが付けた分類そのもの）を先に置く。
    """
    out: list[str] = []
    for block_re in (_CATEGORY_RE, _KEYWORD_RE):
        block = block_re.search(html)
        if not block:
            continue
        for link in _LINK_TEXT_RE.findall(block.group(1)):
            tag = shaping.text_only(link)
            if tag and tag not in out:
                out.append(tag)
    return out


def _ingredients(section_html: str) -> list[dict[str, str]]:
    """`<ul class="disc-list">`（無印）と `<ul class="a-list">`（グループ A）の
    `<li>` を材料へ。名前と量は `…` で分かれている（DOM では分かれていない）。
    """
    out: list[dict[str, str]] = []
    for class_attr, list_html in _MATERIAL_LIST_RE.findall(section_html):
        names = [c for c in class_attr.split() if c.endswith("-list")]
        if not names:
            continue  # 材料の並びではない `<ul>`（節に他の飾りが入った回の保険）
        # `a-list` → グループ `A`、`b-list` → `B`。`disc-list` のような語はグループ無し。
        prefix = names[0].split("-")[0]
        group = prefix.upper() if len(prefix) == 1 else ""
        for li in _LI_RE.findall(list_html):
            text = shaping.text_only(li)
            if not text:
                continue
            parts = _AMOUNT_SEP_RE.split(text, maxsplit=1)
            name = parts[0].strip()
            amount = parts[1].strip() if len(parts) > 1 else ""
            out.extend(shaping.split_grouped_ingredient(name, amount, group=group))
    return out


def _is_note_paragraph(attrs: str, text: str) -> bool:
    cls = shaping.tag_attr(attrs, "class")
    if any(w in cls for w in _NOTE_CLASS_WORDS):
        return True
    return text.startswith(_NOTE_PREFIXES)


def _steps(section_html: str, url: str) -> list[dict[str, object]]:
    steps: list[dict[str, object]] = []
    for m in _HOWTO_TOKEN_RE.finditer(section_html):
        attrs, paragraph, imglist = m.group(1), m.group(2), m.group(3)
        if imglist is not None:
            # 直前の工程に写真が無ければ、この並びの先頭の1枚を当てる。
            img = _IMG_TAG_RE.search(imglist)
            if img and steps and not steps[-1].get("image"):
                steps[-1]["image"] = shaping.image_url_from_img_tag(img.group(1), url) or None
            continue
        text = shaping.text_only(paragraph or "")
        if not text or _is_note_paragraph(attrs or "", text):
            continue
        steps.append({"instruction": text, "image": None})
    return steps


def extract(html: str, url: str) -> dict | None:
    html = shaping.strip_html_comments(html or "")

    material = _MATERIAL_SECTION_RE.search(html)
    howto = _HOWTO_SECTION_RE.search(html)
    if not material or not howto:
        return None

    ingredients = _ingredients(material.group(1))
    raw_steps = _steps(howto.group(1), url)
    if not ingredients or not raw_steps:
        return None

    title_m = _TITLE_RE.search(html)
    heading_m = _MATERIAL_HEADING_RE.search(material.group(1))
    time_m = _COOKING_TIME_RE.search(html)

    hero_image = ""
    hero_m = _HERO_RE.search(html)
    if hero_m:
        hero_image = shaping.image_url_from_img_tag(hero_m.group(1), url)

    return {
        "title": shaping.text_only(title_m.group(1)) if title_m else "",
        "servings": _first_number(shaping.text_only(heading_m.group(1)) if heading_m else "", _SERVINGS_RE),
        "total_minutes": _first_number(time_m.group(1) if time_m else "", _MINUTES_RE),
        "ingredients": ingredients,
        "tools": [],
        "raw_steps": raw_steps,
        "hero_image": hero_image,
        "site_tags": _tags(html),
    }
