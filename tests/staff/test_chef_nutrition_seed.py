"""名寄せの種と、部分一致の順位付けの試験（ADR-019 §4）。

**ここだけは実物の成分表（八訂増補 2023）の抜粋を使う。**——名寄せの正しさは
「そらまめ しょうゆ豆」「わかめ 湯通し塩蔵わかめ 塩抜き 生」のような**実在の紛らわしい
行**があって初めて検算できる。合成データでそれらしい行を作ると、作った人の思い込みを
確かめるだけの試験になる（`test_chef_nutrition.py` の合成データは「仕組み」を見るもの、
こちらは「実物に効くか」を見るもの、と役割を分けている）。

抜粋は `tests/fixtures/food_composition_excerpt.csv`（149 行）。文部科学省が公表して
いる数値そのもので、主人の情報は1つも入っていない（`home/manor.db` には触れず、
一時 DB へ取り込んでから引く）。行は「試験する材料名の素朴な部分一致の上位」と
「種が指す食品」から機械的に選んだ——**旧実装が誤って選んでいた行が必ず入る**。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from manor.staff.chef import nutrition

EXCERPT_CSV = Path(__file__).resolve().parent.parent / "fixtures" / "food_composition_excerpt.csv"


@pytest.fixture
def tables() -> nutrition.UnitTables:
    return nutrition.load_unit_tables()


@pytest.fixture
def seeded(conn, tables: nutrition.UnitTables):
    """抜粋を取り込み、種を入れた状態の `(index, aliases)`（`manor chef food import` と同じ順）。"""
    nutrition.import_food_table(conn, EXCERPT_CSV)
    nutrition.seed_aliases(conn, tables=tables)
    return nutrition.build_index(nutrition.food_rows(conn), tables), nutrition.alias_map(conn)


def _resolve(name: str, seeded, tables: nutrition.UnitTables) -> str:
    index, aliases = seeded
    found = nutrition.resolve_food(name, index, aliases, tables)
    return "" if found is None else str(found["food_code"])


# --- 種そのもの（ファイルの健全性） ------------------------------------------------------


def test_seed_file_is_readable_and_large_enough(tables: nutrition.UnitTables) -> None:
    entries = nutrition.load_alias_seed(tables=tables)
    aliases = [a for e in entries for a in e["aliases"]]
    assert len(entries) >= 150          # 家庭料理の主要な食品
    assert len(aliases) >= 200          # 表記の揺れを入れた材料名
    assert all(len(e["food_code"]) >= 4 for e in entries)


def test_seed_has_no_duplicate_aliases(tables: nutrition.UnitTables) -> None:
    """同じ材料名を2つの食品へ結ばない。

    重複は**正規化した後**で起きる（「蒸し中華めん」は `drop_words` の「蒸し」が落ちて
    「中華めん」になり、別の行の「中華めん」とぶつかった）。だから正規化後の鍵で見る。
    """
    seen: dict[str, str] = {}
    clashes: list[str] = []
    for entry in nutrition.load_alias_seed(tables=tables):
        for alias in entry["aliases"]:
            if alias in seen and seen[alias] != entry["food_code"]:
                clashes.append(f"{alias}: {seen[alias]} / {entry['food_code']}")
            seen[alias] = entry["food_code"]
    assert clashes == []


# --- 表の材料名（実測で外れていたもの。ADR-019 §4 の一覧そのもの） ----------------------


@pytest.mark.parametrize(
    ("name", "food_code", "food_name"),
    [
        ("しょうゆ", "17007", "こいくちしょうゆ"),
        ("塩", "17012", "食塩"),
        ("長ねぎ", "06226", "根深ねぎ 葉 軟白 生"),
        ("豚バラ薄切り肉", "11129", "ぶた ばら 脂身つき 生"),
        ("ご飯", "01088", "こめ 水稲めし 精白米 うるち米"),
        ("鶏もも肉", "11221", "にわとり もも 皮つき 生"),
        ("片栗粉", "02034", "じゃがいもでん粉"),
        ("鶏ガラスープの素", "17093", "顆粒中華だし"),
        ("玉葱", "06153", "たまねぎ りん茎 生"),
        # もともと当たっていたもの（直しで壊していないことの確認）
        ("玉ねぎ", "06153", "たまねぎ りん茎 生"),
        ("にんにく", "06223", "にんにく りん茎 生"),
        ("卵", "12004", "鶏卵 全卵 生"),
        ("キャベツ", "06061", "キャベツ 結球葉 生"),
        ("ごま油", "14002", "ごま油"),
        ("サラダ油", "14006", "調合油"),
        ("塩昆布", "09022", "塩昆布"),
    ],
)
def test_reported_ingredients_resolve(
    name: str, food_code: str, food_name: str, seeded, tables: nutrition.UnitTables
) -> None:
    assert _resolve(name, seeded, tables) == food_code, food_name


# --- 種の代表 30 語 ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "food_code"),
    [
        ("砂糖", "03003"), ("みりん", "16025"), ("酒", "17138"), ("みそ", "17045"),
        ("酢", "17015"), ("ケチャップ", "17036"), ("マヨネーズ", "17042"),
        ("コンソメ", "17027"), ("豆板醤", "17004"), ("カレールー", "17051"),
        ("オリーブオイル", "14001"), ("バター", "14017"),
        ("食パン", "01026"), ("うどん", "01039"), ("そば", "01128"), ("パスタ", "01063"),
        ("豚こま", "11115"), ("鶏むね肉", "11219"), ("ささみ", "11227"),
        ("合いびき肉", "11163"), ("ベーコン", "11183"), ("ウインナー", "11186"),
        ("鮭", "10134"), ("さば", "10154"), ("ツナ缶", "10263"), ("えび", "10415"),
        ("にんじん", "06212"), ("じゃがいも", "02017"), ("白菜", "06233"),
        ("ほうれん草", "06267"), ("小松菜", "06086"), ("もやし", "06291"),
        ("豆腐", "04032"), ("油揚げ", "04040"), ("納豆", "04046"),
        ("牛乳", "13003"), ("チーズ", "13040"), ("わかめ", "09044"), ("のり", "09004"),
        ("ごま", "05018"), ("小麦粉", "01015"), ("パン粉", "01079"), ("しょうが", "06103"),
    ],
)
def test_seed_representatives_resolve(
    name: str, food_code: str, seeded, tables: nutrition.UnitTables
) -> None:
    assert _resolve(name, seeded, tables) == food_code


def test_prep_words_do_not_break_the_seed(seeded, tables: nutrition.UnitTables) -> None:
    """下ごしらえ語（「薄切り」「みじん切り」）が付いても同じ食品に当たる。

    落ちるのは `[food_normalize].drop_words` に載っている語と括弧書きだけ——
    「玉ねぎのみじん切り」の「の」は残る（`normalize_name` の既知の限界。
    `test_chef_nutrition.py::test_normalization_drops_prep_words_and_parentheses` 参照）
    ので、ここでは実際のレシピに出る書き方（括弧か、語だけ）で見る。
    """
    assert _resolve("豚バラ薄切り肉", seeded, tables) == "11129"
    assert _resolve("玉ねぎ（みじん切り）", seeded, tables) == "06153"
    assert _resolve("にんじん（乱切り）", seeded, tables) == "06212"
    assert _resolve("玉ねぎみじん切り", seeded, tables) == "06153"


# --- 部分一致の順位付け（種を入れずに、順位付けだけで見る） ------------------------------


@pytest.fixture
def index(conn, tables: nutrition.UnitTables) -> nutrition.FoodIndex:
    nutrition.import_food_table(conn, EXCERPT_CSV)
    return nutrition.build_index(nutrition.food_rows(conn), tables)


def test_ranking_prefers_the_head_of_a_word_over_a_sub_category(
    index: nutrition.FoodIndex, tables: nutrition.UnitTables
) -> None:
    """「しょうゆ」は**しょうゆ類**へ。「そらまめ しょうゆ豆」「きゅうり 漬物 しょうゆ漬」へは落ちない。

    どちらも名前に「しょうゆ」を含み、しかも**名前が短い**ので、旧実装（短い名前優先）は
    豆のほうを採っていた。語の主辞（後ろ）に当たったものを上に置くと逆転する。
    """
    found = nutrition.resolve_food("しょうゆ", index, {}, tables)
    assert found is not None
    assert found["food_code"] not in ("04076", "06067")
    assert str(found["name"]).startswith("<調味料類>")


def test_ranking_picks_plain_salt_over_salt_cured_seaweed(
    index: nutrition.FoodIndex, tables: nutrition.UnitTables
) -> None:
    """「塩」は「食塩」。「わかめ 湯通し塩蔵わかめ 塩抜き 生」（生を含むので旧実装が最優先した）ではない。"""
    found = nutrition.resolve_food("塩", index, {}, tables)
    assert found is not None
    assert found["food_code"] == "17012"


def test_ranking_still_prefers_raw_within_the_same_position(
    index: nutrition.FoodIndex, tables: nutrition.UnitTables
) -> None:
    """同じ当たり方なら「生」が勝つ（ADR-019 D2 ③。既存の約束を壊していない）。"""
    found = nutrition.resolve_food("小松菜", index, {}, tables)
    assert found is not None
    assert found["food_code"] == "06086"  # 06087 は「こまつな 葉 ゆで」


def test_match_tier_positions() -> None:
    """当たり方の判定そのもの（純粋関数）。"""
    assert nutrition._match_tier("にんじん", ("にんじん", "根", "生")) == nutrition.MATCH_WORD_EXACT
    assert nutrition._match_tier("醤油", ("こいくち醤油",)) == nutrition.MATCH_WORD_HEAD
    assert nutrition._match_tier("鶏ひき肉", ("鶏", "ひき肉", "生")) == nutrition.MATCH_SPAN
    assert nutrition._match_tier("醤油", ("そらまめ", "醤油豆")) == nutrition.MATCH_WORD_PREFIX
    assert nutrition._match_tier("塩", ("わかめ", "湯通し塩蔵わかめ")) == nutrition.MATCH_WORD_INSIDE
    assert nutrition._match_tier("", ("にんじん",)) == nutrition.MATCH_SPAN


# --- 検索（正規化と同義語を両側に掛ける。ADR-019 §4） -------------------------------------


def test_search_finds_kana_spelling_from_the_kanji_the_recipe_uses(conn) -> None:
    """`manor chef food search 玉ねぎ` が 0 件だった（成分表はかな書きの「たまねぎ」）。"""
    nutrition.import_food_table(conn, EXCERPT_CSV)
    codes = [r["food_code"] for r in nutrition.search_foods(conn, "玉ねぎ")]
    assert "06153" in codes
    assert "17007" in [r["food_code"] for r in nutrition.search_foods(conn, "醤油")]
    # 「豚」→「ぶた」、「鶏」→「にわとり」（同義語を逆向きに当てる）。
    # ⚠ 引くのは**1 語**まで——成分表の名前は語の間に空白や分類の括弧が入るので、
    #   「鶏もも」のような続けた書き方は LIKE では当たらない（名寄せの仕事）。
    assert "11129" in [r["food_code"] for r in nutrition.search_foods(conn, "豚", limit=100)]
    assert "11221" in [r["food_code"] for r in nutrition.search_foods(conn, "鶏", limit=100)]
    # 素の書き方（成分表と同じかな）も従来どおり引ける。
    assert "06153" in [r["food_code"] for r in nutrition.search_foods(conn, "たまねぎ")]
    assert nutrition.search_foods(conn, "   ") == []


# --- 種の入れ方（冪等・manual を尊重） ---------------------------------------------------


def test_seed_is_idempotent(conn, tables: nutrition.UnitTables) -> None:
    nutrition.import_food_table(conn, EXCERPT_CSV)
    first = nutrition.seed_aliases(conn, tables=tables)
    second = nutrition.seed_aliases(conn, tables=tables)
    assert first["added"] > 0
    assert second["added"] == 0
    assert second["updated"] == first["added"] + first["updated"]


def test_seed_does_not_overwrite_manual(conn, tables: nutrition.UnitTables) -> None:
    """主人が手で決めた名寄せは、種を入れ直しても動かない（ADR-019 §4）。"""
    nutrition.import_food_table(conn, EXCERPT_CSV)
    nutrition.set_alias(conn, "しょうゆ", "17011")  # うすくち等、主人の好みの1本
    result = nutrition.seed_aliases(conn, tables=tables)
    assert result["kept_manual"] >= 1
    assert nutrition.alias_map(conn)[nutrition.normalize_name("しょうゆ", tables)] == "17011"
    # `rule` の行は入れ直しで更新される（手入力だけが守られる）。
    rows = {r["alias"]: r["confidence"] for r in nutrition.list_aliases(conn)}
    assert rows[nutrition.normalize_name("しょうゆ", tables)] == "manual"
    assert rows[nutrition.normalize_name("片栗粉", tables)] == "rule"


def test_seed_skips_food_codes_that_are_not_in_the_table(conn, tables: nutrition.UnitTables) -> None:
    """成分表の抜粋には無い食品番号があっても、例外にせず飛ばす（数だけ返す）。"""
    nutrition.import_food_table(conn, EXCERPT_CSV)
    result = nutrition.seed_aliases(conn, tables=tables)
    assert result["missing"]  # 抜粋なので必ずいくつか欠ける
    assert all(code not in nutrition.alias_map(conn).values() for code in result["missing"])


def test_seeded_recipe_gets_full_coverage(conn) -> None:
    """種が入っていれば、ありふれた一品の材料が全部解決する（coverage 1.0）。"""
    nutrition.import_food_table(conn, EXCERPT_CSV)
    nutrition.seed_aliases(conn)
    tables = nutrition.load_unit_tables()
    index = nutrition.build_index(nutrition.food_rows(conn), tables)
    recipe = {
        "servings": 2,
        "ingredients": [
            {"name": "豚バラ薄切り肉", "qty": "200", "unit": "g"},
            {"name": "玉ねぎ", "qty": "1", "unit": "個"},
            {"name": "長ねぎ", "qty": "1", "unit": "本"},
            {"name": "しょうゆ", "qty": "2", "unit": "大さじ"},
            {"name": "ごま油", "qty": "1", "unit": "大さじ"},
            {"name": "片栗粉", "qty": "1", "unit": "小さじ"},
        ],
    }
    est = nutrition.estimate_nutrition(recipe, index, nutrition.alias_map(conn), tables)
    assert est.unresolved == []
    assert est.coverage == 1.0
    assert est.nutrition["kcal"] > 0
