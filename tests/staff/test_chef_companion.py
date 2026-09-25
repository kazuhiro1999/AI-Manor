"""お供の提案（ADR-021）の採点の試験。純粋関数だけ——DB も成分表も使わない。

物差しは本物の `lexicon.toml`（`[menu.floor]`・`[menu.band]`・`[companion.*]`）を読む。
主人の例（ADR-021 §1）を表にして固定する: 揚げ物にはキャベツ（生・ビタミンC）、
汁のある麺に汁物は出さない、塩分の超えるお供は下がる、コンロが塞がっていれば火を使わないもの。
"""

from __future__ import annotations

import pytest

from manor.staff.chef import companion, ops, recipes


@pytest.fixture(scope="module")
def rules() -> companion.Rules:
    return companion.load_rules()


def _nut(kcal=50.0, protein=2.0, fat=1.0, carb=6.0, salt=0.4) -> dict[str, float]:
    return {"kcal": kcal, "protein_g": protein, "fat_g": fat, "carb_g": carb, "salt_g": salt}


def _micro(fiber=0.5, k=100.0, ca=20.0, fe=0.3, vc=2.0, veg=10.0) -> dict[str, float]:
    return {"fiber_g": fiber, "potassium_mg": k, "calcium_mg": ca, "iron_mg": fe, "vitamin_c_mg": vc, "veg_g": veg}


def _main(title: str, rules: companion.Rules, *, category="主菜", minutes=20, nutrition=None, micro=None, known=True):
    return {
        "title": title,
        "category": category,
        "kind": companion.main_kind(title, category, rules),
        "minutes": minutes,
        "nutrition": nutrition or _nut(550, 29, 40, 15, 1.9),
        "micro": micro or _micro(fiber=0.1, vc=5.0, veg=4.0),
        "micro_known": known,
    }


def _cand(key: str, *, category="副菜", kind="生", heat="none", tags=(), minutes=10, nutrition=None, micro=None,
          known=True, source="catalog", ingredients=("キャベツ",)):
    return {
        "key": key,
        "source": source,
        "title": key,
        "category": category,
        "kind": kind,
        "heat": heat,
        "tags": list(tags),
        "minutes": minutes,
        "ingredients": list(ingredients),
        "nutrition": nutrition or _nut(),
        "micro": micro or _micro(),
        "micro_known": known,
        "favorite": False,
        "rating": None,
    }


def _codes(scored: companion.Scored) -> list[str]:
    return [str(r["code"]) for r in scored.reasons]


# --- 主菜の型 -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "category", "kind"),
    [
        ("醤油ラーメン", "麺", "汁麺"),
        ("きつねうどん", "麺", "汁麺"),
        ("ソース焼きそば", "麺", "汁なし麺"),
        ("塩唐揚げ", "主菜", "揚げ物"),
        ("パラパラ炒飯", "ご飯もの", "ご飯もの"),
        ("2色のそぼろ丼", "ご飯もの", "ご飯もの"),
        ("豚の生姜焼き", "主菜", "焼き物"),
        ("鶏むね肉の塩麹", "主菜", "主菜"),  # 型の手がかりが無ければ分類
    ],
)
def test_main_kind(title: str, category: str, kind: str, rules: companion.Rules) -> None:
    assert companion.main_kind(title, category, rules) == kind


def test_heat_and_tags_are_read_from_steps_and_ingredients(rules: companion.Rules) -> None:
    assert companion.detect_heat("キャベツを切る。ポリ袋でもむ。", rules) == companion.HEAT_NONE
    assert companion.detect_heat("耐熱皿に入れ、電子レンジで2分加熱する。", rules) == companion.HEAT_RANGE
    assert companion.detect_heat("鍋で湯を沸かし、茹でる。", rules) == companion.HEAT_STOVE
    tags = companion.derive_tags(["キャベツ", "塩昆布", "白いりごま"], companion.HEAT_NONE, rules)
    assert {"葉物", "海藻", "ごま", "生野菜"} <= set(tags)
    # 火を通すなら「生野菜」は付けない
    assert "生野菜" not in companion.derive_tags(["キャベツ"], companion.HEAT_STOVE, rules)


def test_side_without_a_kind_word_is_raw_when_no_fire(rules: companion.Rules) -> None:
    """「塩昆布の無限キャベツ」は題名に型の語が無いが、火を使わない副菜＝生として扱う。"""
    assert companion.companion_kind("塩昆布の無限キャベツ", "副菜", companion.HEAT_NONE, rules) == "生"
    assert companion.companion_kind("わかめの味噌汁", "汁物", companion.HEAT_STOVE, rules) == "汁物"


# --- 主人の例（ADR-021 §1） ----------------------------------------------------------------


def test_fried_main_prefers_raw_cabbage_that_fills_vitamin_c(rules: companion.Rules) -> None:
    main = _main("塩唐揚げ", rules)
    cabbage = _cand("キャベツの塩もみ", tags=["葉物", "生野菜"], micro=_micro(fiber=1.8, vc=35.0, veg=90.0))
    stir_fry = _cand("もやし炒め", kind="炒め物", heat="stove", micro=_micro(fiber=1.8, vc=35.0, veg=90.0))
    out = companion.score(main, [stir_fry, cabbage], rules)
    assert [s.key for s in out] == ["キャベツの塩もみ", "もやし炒め"]
    assert "fills_vitamin_c_mg" in _codes(out[0]) or "fills_veg_g" in _codes(out[0])
    # 炒め物は揚げ物に重い組み合わせとして理由に出る
    assert "heavy_pair" in _codes(out[1])


def test_noodle_soup_main_never_gets_a_soup(rules: companion.Rules) -> None:
    main = _main("醤油ラーメン", rules, category="麺", nutrition=_nut(500, 20, 15, 70, 6.0))
    soup = _cand("わかめスープ", category="汁物", kind="汁物", heat="stove", micro=_micro(fiber=3.0, veg=60.0))
    seaweed = _cand("海藻サラダ", tags=["海藻"], micro=_micro(fiber=3.0, k=400.0, veg=60.0), nutrition=_nut(salt=0.3))
    out = companion.score(main, [soup, seaweed], rules)
    assert [s.key for s in out] == ["海藻サラダ"]


def test_salty_side_ranks_below_a_light_one_and_says_why(rules: companion.Rules) -> None:
    main = _main("塩唐揚げ", rules)
    salty = _cand("浅漬け（濃いめ）", nutrition=_nut(salt=2.0), micro=_micro(vc=20.0, veg=60.0))
    light = _cand("浅漬け（薄め）", nutrition=_nut(salt=0.3), micro=_micro(vc=20.0, veg=60.0))
    out = companion.score(main, [salty, light], rules)
    assert out[0].key == "浅漬け（薄め）"
    assert _codes(out[1])[0] == "salt_over"  # 超過は必ず先頭に見せる


def test_busy_main_prefers_a_no_fire_side(rules: companion.Rules) -> None:
    """揚げ物はコンロを塞ぐ——同じ栄養なら火を使わないお供が上（同時に作る）。"""
    main = _main("塩唐揚げ", rules)
    stove = _cand("おひたし", heat="stove")
    no_fire = _cand("和え物", heat="none")
    out = companion.score(main, [stove, no_fire], rules)
    assert out[0].key == "和え物"
    assert "easy_while_busy" in _codes(out[0])


def test_unknown_micro_on_main_skips_the_floor_entirely(rules: companion.Rules) -> None:
    main = _main("塩唐揚げ", rules, known=False)
    out = companion.score(main, [_cand("キャベツ", micro=_micro(vc=40.0, veg=100.0))], rules)
    assert not any(code.startswith("fills_") for code in _codes(out[0]))


def test_unknown_micro_on_candidate_does_not_claim_to_fill(rules: companion.Rules) -> None:
    main = _main("塩唐揚げ", rules)
    known = _cand("A", micro=_micro(vc=30.0, veg=80.0))
    unknown = _cand("B", micro=_micro(vc=30.0, veg=80.0), known=False)
    out = companion.score(main, [unknown, known], rules)
    assert out[0].key == "A"
    assert not any(code.startswith("fills_") for code in _codes(out[1]))


def test_own_recipe_wins_a_tie_with_the_catalog(rules: companion.Rules) -> None:
    main = _main("塩唐揚げ", rules)
    out = companion.score(main, [_cand("定番"), _cand("うちの", source="recipe")], rules)
    assert out[0].key == "うちの"
    assert "own_recipe" in _codes(out[0])


def test_disliked_ingredient_is_excluded(rules: companion.Rules) -> None:
    main = _main("塩唐揚げ", rules)
    out = companion.score(main, [_cand("トマトサラダ", ingredients=("トマト",))], rules, taste={"dislikes": "トマト"})
    assert out == []


def test_candidate_without_macro_nutrition_is_excluded(rules: companion.Rules) -> None:
    main = _main("塩唐揚げ", rules)
    cand = _cand("栄養値なし")
    cand["nutrition"] = {}
    assert companion.score(main, [cand], rules) == []


def test_shortfalls_are_ordered_by_how_short(rules: companion.Rules) -> None:
    main = _main("塩唐揚げ", rules, micro=_micro(fiber=0.1, k=850.0, ca=200.0, fe=2.4, vc=5.0, veg=4.0))
    out = companion.shortfalls(main, rules)
    assert out["under"][:2] == ["fiber_g", "veg_g"]
    assert out["over"] == ["fat_g"]


# --- 定番の一覧（同梱の companions.toml） --------------------------------------------------


def test_bundled_catalog_is_well_formed() -> None:
    dishes = companion.load_catalog()
    assert len(dishes) >= 30
    keys = [d["key"] for d in dishes]
    assert len(keys) == len(set(keys))
    kinds = set(ops.dish_types()) | {"蒸し物"}
    for d in dishes:
        assert d["category"] in ("副菜", "汁物"), d["key"]
        assert d["kind"] in kinds, d["key"]
        assert d["heat"] in companion.HEATS, d["key"]
        assert d["cuisine"] in ops.recipe_cuisine_values(), d["key"]
        assert d["ingredients"] and d["steps"], d["key"]
        # レシピ帳へ昇格できる形になる（ADR-015 §3 の検算を通る）
        recipes.validate(companion.catalog_recipe(d))


def test_bundled_catalog_covers_the_owners_examples() -> None:
    titles = " ".join(d["title"] for d in companion.load_catalog())
    assert "わかめ" in titles and "キャベツ" in titles
