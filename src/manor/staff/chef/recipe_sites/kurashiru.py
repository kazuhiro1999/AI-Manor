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

## 取れないもの（実測。ADR-015 §6 の表も参照）

- **工程写真**: ページに1枚も無い（工程は動画で見せる作り。`<img>` はロゴと
  カテゴリのサムネイルだけだった）
- **栄養価**: 表示が無い（HTML に `kcal` の語も現れない）
- **材料のグループ**: 本文の DOM には「卵そぼろ」「肉そぼろ」の小見出しと `(A)`〜`(C)` の
  印があるが、JSON-LD の `recipeIngredient` には入っていない（上記の理由で本文は読まない）
"""

from __future__ import annotations

NAME = "kurashiru"


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
    """
    return {"hero_image": str((source or {}).get("og_image") or "")}
