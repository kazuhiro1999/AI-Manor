"""材料の分割（`recipe_shaping.parse_ingredient_line`/`split_grouped_ingredient`）の試験
（ADR-015 §6 追補・2026-09-12。主人が実ページ oceans-nadia.com の炒飯を試した実測から）。

主人の実測: 「粗挽き黒胡椒 小さじ1/4」のように**数字で始まらない**量が材料名に混ざって
残る、「塩、にんにくチューブ 各小さじ1/2」のように**複数の材料が1行にまとまり量に
『各』が付く**のに分けられない、の2つ。実際の JSON-LD（`recipeIngredient`）はこの
「名前 空白 量」の1本の文字列で来る——`parse_ingredient_line()` はこの形を対象にする。

末尾に `infer_ingredients_used`（工程が使う材料の推定。ADR-015 §3・2026-09-13）の節がある。
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


# --- infer_ingredients_used（工程が使う材料の推定。ADR-015 §3・2026-09-13） ---------------
#
# 主人「取り込んだレシピでハイライトが消えた」——XR の材料の板は
# `steps[].ingredients_used` を見て光らせるが、出典が明示することはまず無い。


def _ing(*pairs: tuple[str, str]) -> list[dict]:
    """`(名前, グループ)` の並び → 契約 §3 の材料表（照合に使う2欄だけ）。"""
    return [{"name": name, "group": group} for name, group in pairs]


@pytest.mark.parametrize(
    ("instruction", "ingredients", "expected"),
    [
        # --- 名前の一致 ---
        ("玉ねぎをみじん切りにする。", _ing(("玉ねぎ", ""), ("豚バラ肉", "")), ["玉ねぎ"]),
        # 戻り値は**材料表の並び**（文に出てくる順ではない）。
        (
            "ごま油としょうゆを加える。",
            _ing(("しょうゆ", ""), ("ごま油", "")),
            ["しょうゆ", "ごま油"],
        ),
        # 末尾の「類」の揺れ（きのこ類 ↔ きのこ）。
        ("きのこをほぐして入れる。", _ing(("きのこ類", "")), ["きのこ類"]),
        # 部位の書き分け（鶏もも肉 ↔ 鶏肉。白ごはん.com の実測）。
        ("鶏肉に火が通ったら器に盛る。", _ing(("鶏もも肉", "")), ["鶏もも肉"]),
        # --- グループ展開 ---
        (
            "ボウルに卵を入れて溶きほぐし、(A)を入れてかき混ぜます。",
            _ing(("卵", ""), ("顆粒和風だし", "A"), ("料理酒", "A"), ("しょうゆ", "C")),
            ["卵", "顆粒和風だし", "料理酒"],
        ),
        # 全角のグループ記号＋「…の材料」（NFKC で半角へ均してから照合する）。
        ("Ｂの材料を混ぜ合わせる。", _ing(("砂糖", "B"), ("水", "B"), ("塩", "A")), ["砂糖", "水"]),
        # 「調味料B」の言い回し。
        ("調味料Bを加えて煮からめる。", _ing(("砂糖", "B"), ("塩", "A")), ["砂糖"]),
        # グループ名が語のとき（「合わせ調味料」に「調味料」が当たる）。
        (
            "合わせ調味料を回し入れる。",
            _ing(("しょうゆ", "調味料"), ("みりん", "調味料"), ("卵", "")),
            ["しょうゆ", "みりん"],
        ),
        # 材料表ぜんぶへの参照（DELISH の実測「ポリ袋に全ての材料を入れ」）。
        (
            "ポリ袋に全ての材料を入れ、袋の上からもむ。",
            _ing(("キャベツ", ""), ("塩昆布", ""), ("ごま油", "")),
            ["キャベツ", "塩昆布", "ごま油"],
        ),
        # --- 短い語の誤爆なし ---
        # 「油」は「ごま油」の一部としては当たらない。
        ("ごま油で炒める。", _ing(("油", "")), []),
        ("フライパンにサラダ油をひく。", _ing(("油", "")), []),
        # 「油」は「油揚げ」の一部としても当たらない。
        ("油揚げを短冊切りにする。", _ing(("油", "")), []),
        # 「酒」が「料理酒」の一部として**二重に**当たらない（長い名前から照合する）。
        ("料理酒を加える。", _ing(("料理酒", ""), ("酒", "")), ["料理酒"]),
        # 「水」は「水気」に当たらない／「卵」は「卵焼き」に当たらない。
        ("ざるに上げて水気を切る。", _ing(("水", "")), []),
        ("付け合わせに卵焼きを添える。", _ing(("卵", "")), []),
        # 語として現れていれば短い名前も当たる（直後が量の語でもよい）。
        ("塩少々をふり、酒大さじ1を回しかける。", _ing(("塩", ""), ("酒", "")), ["塩", "酒"]),
        ("鍋に水を入れて沸かす。", _ing(("水", "")), ["水"]),
        # --- 空 ---
        ("", _ing(("塩", "")), []),
        ("中火で5分ほど煮る。", _ing(("塩", ""), ("しょうゆ", "")), []),
        ("材料を切る。", [], []),
    ],
)
def test_infer_ingredients_used_table(
    instruction: str, ingredients: list[dict], expected: list[str]
) -> None:
    assert shaping.infer_ingredients_used(instruction, ingredients) == expected


def test_infer_ingredients_used_has_no_duplicates() -> None:
    """同じ名前が材料表に2度並んでいても（グループ違い）、戻り値は1つだけ。"""
    ingredients = _ing(("料理酒", "A"), ("料理酒", "B"), ("みりん", "A"))
    assert shaping.infer_ingredients_used("料理酒を加える。", ingredients) == ["料理酒"]


@pytest.mark.parametrize(
    ("instruction", "ingredient", "expected"),
    [
        # 主人の炒飯の実測: 材料表は「豚バラ薄切り肉」、工程の文は「豚バラ肉」。
        ("豚バラ肉は粗みじん切りにする。", "豚バラ薄切り肉", ["豚バラ薄切り肉"]),
        ("フライパンで豚肉を炒める。", "豚バラ薄切り肉", ["豚バラ薄切り肉"]),
        # 主人の塩唐揚げの実測: 「鶏肉全体に片栗粉が絡んだら」——直後が漢字でも落とさない。
        ("鶏肉全体に片栗粉がまんべんなく絡んだら完了です。", "鶏もも肉", ["鶏もも肉"]),
        # 別の生き物の肉には当たらない（先頭の1文字が違う）。
        ("牛肉を炒める。", "豚バラ薄切り肉", []),
        # 助詞・読点をまたいで1語にはしない（「豚バラと鶏肉」を豚の肉と読まない）。
        ("豚バラと鶏肉を並べる。", "豚ロース肉", []),
        # 肉で終わらない材料名には働かない。
        ("肉厚のしいたけを焼く。", "しいたけ", ["しいたけ"]),
    ],
)
def test_infer_ingredients_used_absorbs_meat_cut_wording(
    instruction: str, ingredient: str, expected: list[str]
) -> None:
    """部位の書き分け（材料表は部位まで・工程の文は「豚肉」「鶏肉」）を吸収する。"""
    assert shaping.infer_ingredients_used(instruction, _ing((ingredient, ""))) == expected
