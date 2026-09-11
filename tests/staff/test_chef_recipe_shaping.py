"""材料の分割（`recipe_shaping.parse_ingredient_line`/`split_grouped_ingredient`）の試験
（ADR-015 §6 追補・2026-09-12。主人が実ページ oceans-nadia.com の炒飯を試した実測から）。

主人の実測: 「粗挽き黒胡椒 小さじ1/4」のように**数字で始まらない**量が材料名に混ざって
残る、「塩、にんにくチューブ 各小さじ1/2」のように**複数の材料が1行にまとまり量に
『各』が付く**のに分けられない、の2つ。実際の JSON-LD（`recipeIngredient`）はこの
「名前 空白 量」の1本の文字列で来る——`parse_ingredient_line()` はこの形を対象にする。
"""

from __future__ import annotations

import pytest

from manor.staff.chef import recipe_shaping as shaping


# --- parse_ingredient_line（8行以上の表。主人の実測3例を含む） ----------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        # 主人の実測1: 数字で始まらない量（今までは「小さじ1/4」が名前に混ざった）。
        (
            "粗挽き黒胡椒 小さじ1/4",
            [{"name": "粗挽き黒胡椒", "qty": "1/4", "unit": "小さじ", "group": ""}],
        ),
        # 主人の実測2: 複数材料＋「各」→ 個別の行へ分け、同じ量をそれぞれに。
        (
            "塩、にんにくチューブ 各小さじ1/2",
            [
                {"name": "塩", "qty": "1/2", "unit": "小さじ", "group": ""},
                {"name": "にんにくチューブ", "qty": "1/2", "unit": "小さじ", "group": ""},
            ],
        ),
        # 主人の実測3: グループ記号 `(A)` は group へ。
        (
            "(A) 鶏ガラスープの素 小さじ1",
            [{"name": "鶏ガラスープの素", "qty": "1", "unit": "小さじ", "group": "A"}],
        ),
        # 数字＋g（従来から通っていた基本形。壊していないことの確認）。
        ("ご飯 300g", [{"name": "ご飯", "qty": "300", "unit": "g", "group": ""}]),
        # 「大さじ」＋帯分数。
        (
            "サラダ油 大さじ2と1/2",
            [{"name": "サラダ油", "qty": "2と1/2", "unit": "大さじ", "group": ""}],
        ),
        # 数字を伴わない量の語（少々）。
        ("塩 少々", [{"name": "塩", "qty": "", "unit": "少々", "group": ""}]),
        # 数字を伴わない量の語（ふたつまみ）。
        ("砂糖 ふたつまみ", [{"name": "砂糖", "qty": "", "unit": "ふたつまみ", "group": ""}]),
        # 分数＋助数詞。
        ("キャベツ 1/4個", [{"name": "キャベツ", "qty": "1/4", "unit": "個", "group": ""}]),
        # 「各」が無くても読点区切りが2つ以上あれば分ける——量は最後の1つにだけ付く。
        (
            "みりん、酒 大さじ1",
            [
                {"name": "みりん", "qty": "", "unit": "", "group": ""},
                {"name": "酒", "qty": "1", "unit": "大さじ", "group": ""},
            ],
        ),
        # グループ記号 `【A】`。
        ("【A】醤油 大さじ1", [{"name": "醤油", "qty": "1", "unit": "大さじ", "group": "A"}]),
        # グループ記号 `★`（それ自体が記号で、A のような文字を伴わない）。
        ("★ 塩 適量", [{"name": "塩", "qty": "", "unit": "適量", "group": "★"}]),
        # グループ記号「素の1文字」（括弧なし）。
        ("A 醤油 大さじ1", [{"name": "醤油", "qty": "1", "unit": "大さじ", "group": "A"}]),
        # 「お好みで」は末尾に付く（材料名の後ろ）。
        ("パクチー お好みで", [{"name": "パクチー", "qty": "", "unit": "お好みで", "group": ""}]),
        # 認識できない行は今より悪くしない——全文を name に、qty は空文字のまま。
        ("隠し味", [{"name": "隠し味", "qty": "", "unit": "", "group": ""}]),
        # 空行は材料を生まない（呼び出し側は for line in lines for ing in parse_ingredient_line(line) で展開する）。
        ("", []),
    ],
)
def test_parse_ingredient_line_table(line: str, expected: list[dict[str, str]]) -> None:
    assert shaping.parse_ingredient_line(line) == expected


def test_parse_ingredient_line_accepts_full_width_digits() -> None:
    assert shaping.parse_ingredient_line("卵 ３個") == [
        {"name": "卵", "qty": "３", "unit": "個", "group": ""}
    ]


def test_parse_ingredient_line_does_not_worsen_unrecognized_amount() -> None:
    """量の語彙に当たらなければ、今までどおり全文を name に入れる（悪化させない）。"""
    result = shaping.parse_ingredient_line("バジル ちょっと多めに")
    assert result == [{"name": "バジル ちょっと多めに", "qty": "", "unit": "", "group": ""}]


# --- split_grouped_ingredient（DOM で名前・量が既に分かれているサイト別アダプタ向け） -------


def test_split_grouped_ingredient_splits_on_each() -> None:
    result = shaping.split_grouped_ingredient("塩、にんにくチューブ", "各小さじ1/2", group="A")
    assert result == [
        {"name": "塩", "qty": "1/2", "unit": "小さじ", "group": "A"},
        {"name": "にんにくチューブ", "qty": "1/2", "unit": "小さじ", "group": "A"},
    ]


def test_split_grouped_ingredient_without_each_only_last_gets_amount() -> None:
    result = shaping.split_grouped_ingredient("醤油・みりん", "大さじ1")
    assert result == [
        {"name": "醤油", "qty": "", "unit": "", "group": ""},
        {"name": "みりん", "qty": "1", "unit": "大さじ", "group": ""},
    ]


def test_split_grouped_ingredient_single_name_is_unaffected() -> None:
    result = shaping.split_grouped_ingredient("豚バラ肉", "200g", group="")
    assert result == [{"name": "豚バラ肉", "qty": "200", "unit": "g", "group": ""}]


def test_split_grouped_ingredient_empty_name_yields_nothing() -> None:
    assert shaping.split_grouped_ingredient("", "200g") == []
