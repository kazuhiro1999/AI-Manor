"""kurashiru.com（クラシル）向けアダプタ（ADR-015 D7・2026-09-13 追加）。

**このサイトは JSON-LD が本命**——実ページ（主人の実測 URL）を1回だけ取得して確かめた
ところ、`Recipe` の JSON-LD に題名・分量（`recipeYield` が `"2 servings"`）・
調理時間（`totalTime` が `PT20M`）・材料12件・工程5件が揃っていた。だからここは
経路①（JSON-LD）の**補い**に徹し、本文からの抽出はしない（`extract()` は常に `None`）。

本文を読まない理由は2つある:

1. クラシルの class 名は vanilla-extract のハッシュ（`_8u4gzjm`・`_1ttze1q2` 等）で、
   ビルドのたびに変わる。Nadia のように「先頭一致」で緩く拾うこともできない
   （先頭からハッシュなので手掛かりが無い）
2. JSON-LD が常にあるので、本文を読む経路②は実際には呼ばれない

## 補うもの（`extract_hints`）

- **完成画像**: JSON-LD の `image` は**動画の小さな正方形サムネイル**
  （`video.kurashiru.com/.../compressed_thumbnail_square_normal.jpg`）で、同じ絵の
  大きい版が og:image（`..._square_large.jpg`・800×800）にある。og:image を先に指す
- **分類3軸**: `recipeCategory` が「ごはんもの,卵料理,肉,ひき肉」と日本語なので
  読み替えは要らない（`recipe_import._ld_site_tags` が素のまま渡す）
- **材料のグループ**（2026-09-13 追加）: 本文の材料一覧には「卵そぼろ」「肉そぼろ」の
  小見出しと `(A)`〜`(C)` の印があるが、JSON-LD の `recipeIngredient` には**どちらも
  入っていない**（実測）。DOM の**並び**が JSON-LD の並びと1対1なので、行の順序から
  グループを割り出して `ingredient_groups` として渡す（下の `extract_ingredient_groups`）。

## 取れないもの（実測。ADR-015 §6 の表も参照）

- **工程写真**: ページに1枚も無い（工程は動画で見せる作り。`<img>` はロゴと
  カテゴリのサムネイルだけだった）
- **栄養価**: 表示が無い（HTML に `kcal` の語も現れない）
"""

from __future__ import annotations

import re

from .. import recipe_shaping as shaping

NAME = "kurashiru"

#: 材料一覧の節。class 名はハッシュ（`_8u4gzj0`）だが、**`browsi_…` の札だけは素の語**で
#: 付いている（実測。おそらく解析用のマーカー）——ハッシュの隣にあるこの語を手掛かりにする。
#: 見つからなければ「材料」の見出しから次の `</section>` までを見る（保険）。
_INGREDIENT_SECTION_RE = re.compile(
    r'<section[^>]*\bbrowsi_videos_show_ingredients\b[^>]*>(.*?)</section>', re.S
)
_INGREDIENT_SECTION_FALLBACK_RE = re.compile(r"材料(.*?)</section>", re.S)

#: 材料一覧の1行。`<a>` を持つ行が材料、持たない行が小見出し（実測）——class 名は
#: ビルドのたびに変わるので**タグの形**だけで見分ける。
_LI_RE = re.compile(r"<li\b[^>]*>(.*?)</li>", re.S)
_ANCHOR_RE = re.compile(r"<a\b[^>]*>(.*?)</a>", re.S)


def extract(html: str, url: str) -> dict | None:
    """常に `None`（＝汎用へ落とす合図）。モジュール docstring の「本文を読まない理由」参照。"""
    return None


def extract_hints(
    html: str,
    url: str,
    *,
    ld: dict | None = None,
    source: dict | None = None,
) -> dict:
    """完成画像だけを補う（`recipe_sites/__init__.py` の口の説明を参照）。

    `source["og_image"]` は `recipe_import.extract_text()` が既に絶対 URL へ直した値。
    ここで指すと `_resolve_hero_image` の候補の**先頭**に来るので、JSON-LD の小さい
    サムネイルより優先される（og:image が無い回は空を返し、従来どおりの順に落ちる）。

    材料のグループ（`ingredient_groups`）も一緒に渡す——JSON-LD には無く、本文の
    並びからしか取れない（モジュール docstring 参照）。
    """
    return {
        "hero_image": str((source or {}).get("og_image") or ""),
        "ingredient_groups": extract_ingredient_groups(html),
    }


def extract_ingredient_groups(html: str) -> list[str]:
    """材料一覧の**並び**から、材料1件ごとのグループ名を返す（ADR-015 §7 追補）。

    クラシルの材料一覧は `<li>` の並びで、

    - 小見出しの行 … `<li><div>卵そぼろ</div></li>`（`<a>` が無い）
    - 材料の行 … `<li><a>(A)料理酒</a><span>大さじ1</span></li>`（`<a>` が名前）

    という形（実測。class 名は vanilla-extract のハッシュなので**タグの形と順序**だけを
    見る）。名前に付いた `(A)` と小見出しの両方があるので、優先の規則は
    `recipe_shaping.ingredient_groups_in_order()` に置いてある。

    戻り値の並びは**材料の行だけ**で、JSON-LD の `recipeIngredient` と同じ順・同じ件数に
    なる（実測で 12 件どちらも一致）。件数が合うかどうかは呼び出し側
    （`recipe_import.extract_auto`）が確かめる——**壊れる前提**なので、合わなければ
    当てずに `warnings` を1行返す。
    """
    m = _INGREDIENT_SECTION_RE.search(html or "") or _INGREDIENT_SECTION_FALLBACK_RE.search(
        html or ""
    )
    if not m:
        return []
    rows: list[tuple[str, str]] = []
    for li in _LI_RE.findall(m.group(1)):
        anchor = _ANCHOR_RE.search(li)
        if anchor is None:
            heading = shaping.text_only(li)
            if heading:
                rows.append((shaping.ROW_HEADING, heading))
            continue
        rows.append((shaping.ROW_INGREDIENT, shaping.text_only(anchor.group(1))))
    return shaping.ingredient_groups_in_order(rows)
