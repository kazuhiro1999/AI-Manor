"""調理の途中で捨てる塩（茹で湯の塩・塩もみ。ADR-019 §6）の試験。

主人の実例（2026-09-25）を表にして固定する: おひたしの「沸騰したお湯に塩を加える→ザルにあける」、
酢の物の「塩をふって揉み、流水で洗う」。逆に**減らしてはいけない**もの（スープの塩・仕上げの塩・
袋の上から揉むだけの和え物）も固定する。成分表は合成の数行。物差しは本物の `lexicon.toml`。
"""

from __future__ import annotations

import pytest

from manor.staff.chef import nutrition


@pytest.fixture(scope="module")
def tables() -> nutrition.UnitTables:
    return nutrition.load_unit_tables()


def _food(code: str, name: str, **values) -> dict[str, object]:
    row: dict[str, object] = {
        "food_code": code, "food_group": code[:2], "name": name,
        "kcal": 0.0, "protein_g": 0.0, "fat_g": 0.0, "carb_g": 0.0, "salt_g": 0.0, "refuse_pct": 0.0,
    }
    row.update(values)
    return row


@pytest.fixture(scope="module")
def index(tables) -> nutrition.FoodIndex:
    return nutrition.build_index(
        [
            _food("17012", "<調味料類> (食塩類) 食塩", salt_g=100.0),
            _food("06267", "ほうれんそう 葉 通年平均 生", kcal=18.0),
            _food("06065", "きゅうり 果実 生", kcal=13.0),
            _food("01063", "マカロニ・スパゲッティ 乾", kcal=347.0),
            _food("17007", "こいくちしょうゆ", salt_g=14.5),
        ],
        tables,
    )


def _recipe(ingredients, steps, *, servings=1):
    return {
        "title": "試験",
        "servings": servings,
        "ingredients": [{"name": n, "qty": q, "unit": u} for n, q, u in ingredients],
        "steps": [
            {"title": "", "instruction": text, "ingredients_used": list(used)} for text, used in steps
        ],
    }


def _salt(recipe, index, tables) -> float:
    return nutrition.estimate_nutrition(recipe, index, {"塩": "17012"}, tables).nutrition["salt_g"]


def test_boiling_salt_poured_into_water_that_is_drained_counts_only_a_little(index, tables) -> None:
    """クラシルのおひたしの書き方（「茹で」の語が無い）: 塩 6 g・湯 1000 g・ほうれん草 200 g。"""
    recipe = _recipe(
        [("ほうれん草", "200", "g"), ("お湯", "1000", "ml"), ("塩", "6", "g"), ("しょうゆ", "10", "g")],
        [
            ("沸騰したお湯に塩小さじ1を加えます", ["お湯", "塩"]),
            ("根から入れて1〜2分加熱します", ["ほうれん草"]),
            ("ザルにあけて水分を切ります", []),
            ("醤油をかけて完成です", ["しょうゆ"]),
        ],
    )
    est = nutrition.estimate_nutrition(recipe, index, {"塩": "17012"}, tables)
    # 茹で塩は 6 × (200 × 0.1) / 1000 = 0.12 g だけ。醤油 1.45 g と合わせて 1.6 g 弱
    assert est.nutrition["salt_g"] == pytest.approx(0.12 + 1.45, abs=0.1)
    adj = [a for a in est.adjustments if a["kind"] == nutrition.ADJUST_SALT_DISCARD]
    assert adj and adj[0]["method"] == nutrition.SALT_BOIL and adj[0]["salt_g"] < -5.5


def test_soup_salt_is_not_reduced(index, tables) -> None:
    """湯を捨てない（スープ）なら、沸いた湯に入れた塩も全部口に入る。"""
    recipe = _recipe(
        [("お湯", "400", "ml"), ("塩", "2", "g")],
        [("鍋に湯を沸かし、塩を入れて味を調える", ["お湯", "塩"]), ("器に盛る", [])],
    )
    assert _salt(recipe, index, tables) == pytest.approx(2.0)


def test_pasta_absorbs_more_than_vegetables(index, tables) -> None:
    """乾麺（食品群 01）は移る率 0.64: 2 L・塩 20 g・乾麺 200 g → 20 × 200 × 0.64 / 2000 = 1.28 g。"""
    recipe = _recipe(
        [("スパゲッティ", "200", "g"), ("水", "2000", "ml"), ("塩", "20", "g")],
        [("湯を沸かして塩を入れ、スパゲッティを茹でる", ["水", "塩", "スパゲッティ"]), ("湯を切る", [])],
    )
    est = nutrition.estimate_nutrition(recipe, index, {"塩": "17012", "スパゲッティ": "01063"}, tables)
    assert est.nutrition["salt_g"] == pytest.approx(1.28, abs=0.05)


def test_salt_rub_then_rinse_keeps_5_percent_and_the_finishing_salt_stays(index, tables) -> None:
    """酢の物の書き方: 塩もみの塩（先に出る行）は洗い流し、仕上げの塩（後の行）は全部数える。"""
    recipe = _recipe(
        [("きゅうり", "100", "g"), ("塩", "1.5", "g"), ("塩", "1.5", "g")],
        [
            ("きゅうりは薄切りにし、塩をふって揉み、10分程置いて流水で洗い、水気を絞ります", ["きゅうり", "塩"]),
            ("塩で味を調えます", ["塩"]),
        ],
    )
    assert _salt(recipe, index, tables) == pytest.approx(1.5 * 0.05 + 1.5, abs=0.05)  # 0.1 g 単位に丸める


def test_salt_rub_then_squeeze_keeps_10_percent(index, tables) -> None:
    recipe = _recipe(
        [("きゅうり", "100", "g"), ("塩", "2", "g")],
        [("きゅうりに塩をまぶしてしばらく置く", ["きゅうり", "塩"]), ("水気をしっかり絞る", ["きゅうり"])],
    )
    assert _salt(recipe, index, tables) == pytest.approx(0.2, abs=0.02)


def test_rubbing_without_squeezing_or_rinsing_keeps_all(index, tables) -> None:
    """無限キャベツの書き方（袋の上から全部もむ）は捨てないので減らさない。"""
    recipe = _recipe(
        [("きゅうり", "100", "g"), ("塩", "1", "g")],
        [("ポリ袋に全材料を入れ、袋の上からもむ。5分ほどおく", ["きゅうり", "塩"])],
    )
    assert _salt(recipe, index, tables) == pytest.approx(1.0)


def test_the_step_quantity_picks_which_salt_row_was_boiled(index, tables) -> None:
    """「塩 少々」（仕上げ）が先に並んでいても、工程に「塩小さじ1」とあればそちらを茹で塩とみなす。"""
    recipe = _recipe(
        [("塩", "1", "g"), ("ほうれん草", "200", "g"), ("お湯", "1000", "ml"), ("塩", "1", "小さじ")],
        [
            ("湯を沸かし、塩小さじ1を入れる", ["お湯", "塩"]),
            ("ほうれん草を茹でる", ["ほうれん草"]),
            ("冷水にとって絞る", []),
            ("塩で味を調える", ["塩"]),
        ],
    )
    # 小さじ1（6 g）は茹で塩で 0.12 g、仕上げの 1 g は全部
    assert _salt(recipe, index, tables) == pytest.approx(1.0 + 0.12, abs=0.05)
