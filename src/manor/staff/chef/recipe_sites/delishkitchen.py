"""delishkitchen.tv（DELISH KITCHEN）向けアダプタ（ADR-015 D7・2026-09-13 追加）。

**JSON-LD が最も充実しているサイト**——実ページ（主人の実測 URL）を1回だけ取得して
確かめたところ、`@graph` の `Recipe` に題名・分量・材料・工程（`HowToStep` ごとに
`image` あり）・`nutrition`・`recipeCategory`・`recipeCuisine`・`keywords` が揃っていた。
経路①（JSON-LD）でほぼ完結するので、ここで補うのは**2つの読み替え**だけ。

## ① 食塩相当量（`salt_g`）

`nutrition.sodiumContent` は `"0.8g"`。全体の規則は「`sodiumContent`（ナトリウム）は
食塩相当量とは別物なので換算せずに無視」（`recipe_import._LD_NUTRITION_FIELD_MAP` の
コメント）だが、**このサイトでは食塩相当量そのもの**だと確かめた——同じページの本文に
「塩分 0.8g」と表示されており（`nutrient-name`/`nutrient-amount` の対。内部の名前は
`name:"塩分量", icon_type:"salt"`）、JSON-LD の値と一致する。ナトリウム 0.8g なら
食塩相当量は約2gで、栄養表示としても桁が合わない。

そこで**本文の表示の方から**読む（`extract_nutrition`）。全体の規則は変えない
——「サイト固有の事情はアダプタが持つ」（D7）に寄せ、JSON-LD の `sodiumContent` は
これまでどおり誰も食塩相当量として扱わない。本文の並びは kcal から塩分まで6項目
あるので、JSON-LD が欠けた回の保険として**全部**返す。

## ② 分類3軸（`recipeCategory`・`recipeCuisine` が英語）

`recipeCategory` が `"side dish"`、`recipeCuisine` が `"Japanese"` で、そのままでは
日本語の手がかり語に当たらない。読み替えの**対応表は `lexicon.toml` にある**
（`[recipe_category_cues]`・`[recipe_cuisine_cues]` の ASCII の手がかり語。
`ops.recipe_tag_translations()` が集める）——2026-09-13 に、ここに写していた自前の表を
そちらへ寄せた（ADR-019 §4）。語を足すときに直す場所を1つにするため。
`keywords` は日本語（「副菜, おつまみ, キャベツ, …」）なのでそのままで当たる
——素材の軸は「キャベツ」から「野菜」が付く。

## 取れないもの

- **本文の DOM にタグの一覧が無い**（`keywords` は JSON-LD だけ）。経路②に落ちた回は
  3軸が題名・材料名からの推定だけになる
- 調理時間は `totalTime` が `"PT600S"`（秒）。`recipe_import._iso8601_minutes` が秒を
  見るようにしたので10分として入る（本文の DOM には「約10分」の表示しか無い）
"""

from __future__ import annotations

import re

from .. import ops
from .. import recipe_shaping as shaping

NAME = "delishkitchen"

#: 栄養価の表示（1人分）。`<div class="nutrient-name"><p>塩分</p></div>` の直後に
#: `<div class="nutrient-amount"><p>0.8g</p></div>` が来る対の並び。
_NUTRIENT_PAIR_RE = re.compile(
    r'class="nutrient-name"[^>]*>\s*<p[^>]*>([^<]*)</p>\s*</div>\s*'
    r'<div class="nutrient-amount"[^>]*>\s*<p[^>]*>([^<]*)</p>',
    re.S,
)

#: 表示のラベル → 契約の栄養キー（ADR-015 §3 `meta`）。**「糖質」は見ない**
#: ——契約の `carb_g` は「炭水化物」に対応する（Nadia と同じ約束）。
_NUTRITION_LABELS: tuple[tuple[str, str], ...] = (
    ("カロリー", "kcal"),
    ("たんぱく質", "protein_g"),
    ("脂質", "fat_g"),
    ("炭水化物", "carb_g"),
    ("塩分", "salt_g"),
)

_NUMBER_RE = re.compile(r"[\d]+(?:\.[\d]+)?")

#: 本文（経路②で使う。`data-v-XXXXXXXX` は Vue の scoped 属性で、ビルドで変わるので見ない）。
_TITLE_LEAD_RE = re.compile(r'<h1[^>]*>(.*?)</h1>', re.S)
_SERVING_RE = re.compile(r'class="recipe-serving"[^>]*>(.*?)</span>\s*</h2>', re.S)
_SERVINGS_NUM_RE = re.compile(r"([\d０-９]+)\s*人分")
_INGREDIENT_LI_RE = re.compile(
    r'class="ingredient-name"[^>]*>(.*?)</(?:a|span)>\s*'
    r'<span[^>]*class="ingredient-serving"[^>]*>(.*?)</span>',
    re.S,
)
#: 工程は `<li class="step">` ごとに、写真（`<video poster="…">`）と本文（`p.step-desc`）。
_STEP_LI_RE = re.compile(r'<li class="step"[^>]*>(.*?)</li>', re.S)
_STEP_DESC_RE = re.compile(r'class="step-desc"[^>]*>(.*?)</p>', re.S)
_STEP_POSTER_RE = re.compile(r'<video\b([^>]*)>')


def extract_nutrition(html: str) -> dict[str, float]:
    """本文の栄養価の表示（1人分）をラベルの隣の数値から拾う。

    **`salt_g` がここの主役**——JSON-LD の `sodiumContent` を食塩相当量として使わない
    全体の規則を保ったまま、このサイトの「塩分」表示から入れる（モジュール docstring ①）。
    """
    out: dict[str, float] = {}
    for label_html, value in _NUTRIENT_PAIR_RE.findall(shaping.strip_html_comments(html or "")):
        label = shaping.text_only(label_html)
        for word, key in _NUTRITION_LABELS:
            if word in label and key not in out:
                num = _NUMBER_RE.search(value or "")
                if num:
                    try:
                        out[key] = float(num.group())
                    except ValueError:
                        pass
                break
    return out


def _translate_tags(tags: list[str]) -> list[str]:
    """英語の分類語を `lexicon.toml` の語へ寄せる。当たらない語は落とす
    （素の語は `recipe_import._ld_site_tags` が別に渡すので、ここで返す必要はない）。

    対応表の出どころは `lexicon.toml` だけ（`ops.recipe_tag_translations()`）。
    """
    translations = ops.recipe_tag_translations()
    out: list[str] = []
    for tag in tags:
        low = str(tag).strip().lower()
        for word, mapped in translations:
            if low == word and mapped not in out:
                out.append(mapped)
                break
    return out


def extract_hints(
    html: str,
    url: str,
    *,
    ld: dict | None = None,
    source: dict | None = None,
) -> dict:
    """英語の `recipeCategory`／`recipeCuisine` を日本語の手がかり語へ読み替える
    （モジュール docstring ②）。完成画像・工程写真は JSON-LD のままで良いので触らない。
    """
    ld_tags = list((ld or {}).get("site_tags") or [])
    return {"site_tags": _translate_tags([str(t) for t in ld_tags])}


def extract(html: str, url: str) -> dict | None:
    """経路②（JSON-LD を欠いた変種）用の保険。本文の `li.ingredient`／`li.step` を読む。

    実ページでは①が必ず勝つので、ここが使われるのは JSON-LD が消えた回だけ。
    """
    text = shaping.strip_html_comments(html or "")

    ingredients: list[dict[str, str]] = []
    for name_html, amount_html in _INGREDIENT_LI_RE.findall(text):
        name = shaping.text_only(name_html)
        if not name:
            continue
        ingredients.extend(
            shaping.split_grouped_ingredient(name, shaping.text_only(amount_html))
        )

    raw_steps: list[dict[str, object]] = []
    for li in _STEP_LI_RE.findall(text):
        desc_m = _STEP_DESC_RE.search(li)
        if not desc_m:
            continue
        instruction = shaping.text_only(desc_m.group(1))
        if not instruction:
            continue
        image = ""
        video_m = _STEP_POSTER_RE.search(li)
        if video_m:
            # 工程は動画で、静止画は `poster` 属性に入っている（`<img>` ではない）。
            poster = shaping.tag_attr(video_m.group(1), "poster")
            if poster:
                image = shaping.image_url_from_img_tag(f'src="{poster}"', url)
        raw_steps.append({"instruction": instruction, "image": image or None})

    if not ingredients or not raw_steps:
        return None

    title_m = _TITLE_LEAD_RE.search(text)
    # `<h1>` は `<span class="lead">箸が止まらない！</span><br><span class="title">…</span>`
    # の2段。`<br>` を空白に替えてから地の文にする（JSON-LD の `name` と同じ並びになる）。
    title = shaping.text_only(re.sub(r"<br\b[^>]*>", " ", title_m.group(1))) if title_m else ""

    servings = None
    serving_m = _SERVING_RE.search(text)
    if serving_m:
        num = _SERVINGS_NUM_RE.search(shaping.text_only(serving_m.group(1)))
        if num:
            try:
                servings = int(num.group(1))
            except ValueError:
                servings = None

    return {
        "title": re.sub(r"\s+", " ", title).strip(),
        "servings": servings,
        "total_minutes": None,  # 本文には「約10分」の表示しか無い（JSON-LD の totalTime が正）
        "ingredients": ingredients,
        "tools": [],
        "raw_steps": raw_steps,
        "hero_image": "",  # og:image が完成画像（`_resolve_hero_image` の3段目に任せる）
        "site_tags": [],  # 本文の DOM にタグの一覧が無い（docstring「取れないもの」）
    }
