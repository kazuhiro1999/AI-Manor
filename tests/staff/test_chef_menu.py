"""献立のおすすめ（ADR-018 D7-1。規則だけ）の試験。すべて合成データ・架空の家庭。

採点は**表で**見る——「どの因子が効いたか」を1件ずつ切り離し、合成の候補2つの順位が
入れ替わることで確かめる（点の絶対値は重みを主人が変えれば動くので検算しない）。
LLM は呼ばない段なので、外部への差し替え（monkeypatch）は要らない。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from manor.errors import ManorError
from manor.staff.chef import cli as chef_cli
from manor.staff.chef import menu, recipes

TODAY = "2026-09-13"

#: 帯（`[menu.band]` の既定値）。試験は lexicon の値をそのまま使う——ここを合成値にすると
#: 「主人が書き換えられる」という D2 の約束を試験が検算しなくなる。
BAND = menu.load_band()
WEIGHTS = menu.load_weights()
RULES = menu.load_rules()
MOODS = menu.load_moods()


def _nutrition(kcal, protein, fat, carb, salt) -> dict[str, float]:
    return {"kcal": kcal, "protein_g": protein, "fat_g": fat, "carb_g": carb, "salt_g": salt}


def _cand(recipe_id: int, title: str, **over) -> dict[str, object]:
    base: dict[str, object] = {
        "id": recipe_id,
        "title": title,
        "nutrition": _nutrition(80, 3, 2, 10, 0.6),
        # ADR-019 D4 で D1 の判定に出所が入った（`nutrition_source` が無い＝出どころを
        # 言えない値は候補にしない）。採点そのものを見る試験は `manual` を既定に置く。
        "nutrition_source": "manual",
        "category": "副菜",
        "main_ingredient": "野菜",
        "cuisine": "和食",
        "dish_type": "",
        "total_minutes": 20,
        "rating": None,
        "favorite": False,
        "times_cooked": 0,
        "last_cooked_at": None,
        "ingredients": ["きゅうり"],
    }
    base.update(over)
    return base


#: 主菜（豚の生姜焼き相当）。kcal と炭水化物が帯に足りていない状態を作る。
MAIN = {
    "id": 1,
    "title": "豚の生姜焼き",
    "nutrition": _nutrition(450, 24, 22, 30, 1.8),
    "nutrition_source": "manual",
    "main_ingredient": "肉",
    "cuisine": "和食",
    "dish_type": "焼き物",
}


def _score(candidates, **kwargs) -> list[menu.Scored]:
    params: dict[str, object] = {
        "slot_kind": "side",
        "today": TODAY,
        "rules": RULES,
    }
    params.update(kwargs)
    main = params.pop("main", MAIN)
    return menu.score_candidates(
        main,
        candidates,
        params.pop("pantry_items", []),
        params.pop("recent_meals", []),
        params.pop("taste", {}),
        params.pop("mood_conditions", {}),
        BAND,
        WEIGHTS,
        **params,  # type: ignore[arg-type]
    )


def _codes(scored: menu.Scored) -> list[str]:
    return [str(r["code"]) for r in scored.reasons]


def _by_id(scored: list[menu.Scored]) -> dict[int, menu.Scored]:
    return {s.recipe_id: s for s in scored}


# --- lexicon（数値の出どころは lexicon.toml だけ。ADR-018 D2） ------------------------


def test_band_comes_from_lexicon_and_covers_five_nutrients() -> None:
    assert BAND["kcal"] == (600.0, 900.0)
    assert BAND["protein_g"] == (20.0, 35.0)
    assert BAND["fat_g"] == (15.0, 30.0)
    assert BAND["carb_g"] == (75.0, 130.0)
    assert BAND["salt_g"][1] == 2.5
    assert set(BAND) == set(menu.NUTRIENTS)


def test_band_is_editable_by_the_master(tmp_path: Path) -> None:
    """D2「`lexicon.toml` に置き、ユーザーが書き換えられる」——差し替えたファイルを
    読ませれば帯が変わることを機構で示す（`menu.py` に数値を持たせていない証拠）。"""
    lex = tmp_path / "lexicon.toml"
    lex.write_text(
        "[menu.band]\nkcal = [400, 500]\nprotein_g = [10, 20]\n"
        "fat_g = [5, 10]\ncarb_g = [40, 60]\nsalt_g = [0, 1.5]\n",
        encoding="utf-8",
    )
    assert menu.load_band(lex)["kcal"] == (400.0, 500.0)


def test_slot_categories_map_to_recipe_category_vocabulary() -> None:
    """枠 → `category` の対応も lexicon が持つ（`menu.py` に日本語をべた書きしない）。"""
    slots = menu.load_slot_categories()
    assert set(slots) == set(menu.SLOT_KINDS)
    from manor.staff.chef import ops

    for value in slots.values():
        assert value in ops.recipe_category_values()


# --- 帯への寄与・band_check ---------------------------------------------------------


def test_band_check_marks_under_in_over() -> None:
    checks = menu.band_check(_nutrition(500, 25, 20, 140, 3.0), BAND)
    assert checks["kcal"]["status"] == menu.BAND_UNDER
    assert checks["protein_g"]["status"] == menu.BAND_IN
    assert checks["carb_g"]["status"] == menu.BAND_OVER
    assert checks["salt_g"]["status"] == menu.BAND_OVER


def test_band_check_never_calls_salt_under() -> None:
    """塩分は下限を持たない（少ないほどよい）——0 g でも「不足」にはしない。"""
    assert menu.band_check(_nutrition(700, 25, 20, 100, 0.0), BAND)["salt_g"]["status"] == menu.BAND_IN


def test_candidate_that_closes_the_band_gap_wins() -> None:
    """帯への寄与: 足りない kcal・炭水化物を埋める候補が、埋めない候補より上に来る。"""
    filling = _cand(2, "かぼちゃの煮物", nutrition=_nutrition(180, 4, 2, 40, 0.7))
    thin = _cand(3, "きゅうりの浅漬け", nutrition=_nutrition(15, 1, 0.1, 3, 0.6))
    ranked = _score([filling, thin])
    assert [s.recipe_id for s in ranked] == [2, 3]
    assert "fills_kcal" in _codes(_by_id(ranked)[2]) or "fills_carb_g" in _codes(_by_id(ranked)[2])


def test_salt_overflow_is_penalized_strongly_and_always_reported() -> None:
    """塩分の超過は強く減点し、**理由からは絞られて消えない**（D4・D3）。"""
    salty = _cand(2, "塩辛い副菜", nutrition=_nutrition(180, 5, 4, 30, 1.2))
    mild = _cand(3, "ほうれん草のおひたし", nutrition=_nutrition(170, 5, 4, 30, 0.4))
    ranked = _score([salty, mild])
    assert [s.recipe_id for s in ranked] == [3, 2]
    assert "salt_over" in _codes(_by_id(ranked)[2])


def test_low_salt_candidate_earns_the_salt_reason() -> None:
    """ADR-018 §1 の例「海藻類で塩分摂取を抑制」に当たる理由が出る。"""
    ranked = _score([_cand(2, "きゅうりとわかめの酢の物", nutrition=_nutrition(40, 2, 0.5, 6, 0.3))])
    assert "keeps_salt_low" in _codes(ranked[0])


# --- 多様性（主菜と被らない） ---------------------------------------------------------


def test_different_main_ingredient_beats_the_same_one() -> None:
    veg = _cand(2, "小松菜のごま和え", main_ingredient="野菜")
    meat = _cand(3, "ハムのサラダ", main_ingredient="肉")
    ranked = _score([veg, meat])
    assert [s.recipe_id for s in ranked] == [2, 3]
    assert "main_ingredient_differs" in _codes(_by_id(ranked)[2])
    assert "main_ingredient_differs" not in _codes(_by_id(ranked)[3])


def test_different_cooking_method_beats_the_same_one() -> None:
    """調理法は**題名から** `[dish_types]` の語彙で暫定推定する（D4）。"""
    boiled = _cand(2, "大根の煮物")
    grilled = _cand(3, "野菜の焼き浸し")
    ranked = _score([boiled, grilled])
    assert menu.estimate_dish_type("大根の煮物") == "煮物"
    assert menu.estimate_dish_type("野菜の焼き浸し") == "焼き物"
    assert [s.recipe_id for s in ranked] == [2, 3]  # 主菜が「焼き物」なので煮物が上
    assert "dish_type_differs" in _codes(_by_id(ranked)[2])


def test_no_diversity_terms_when_the_main_is_not_decided() -> None:
    """主菜の枠を採点するときは比べる相手がいない——多様性の項は出ない（ADR-018 §4）。"""
    ranked = _score([_cand(2, "鶏の照り焼き", category="主菜", main_ingredient="肉")], main=None, slot_kind="main")
    assert "main_ingredient_differs" not in _codes(ranked[0])
    assert "dish_type_differs" not in _codes(ranked[0])


# --- 在庫（加点のみ。ADR-018 D4） -----------------------------------------------------


def test_pantry_items_only_add_points() -> None:
    """在庫にある材料を使う候補は上がる。**無い候補は減点されない**（同じ条件なら同点）。"""
    with_stock = _cand(2, "ほうれん草のおひたし", ingredients=["ほうれん草", "醤油"])
    without = _cand(3, "小松菜のおひたし", ingredients=["小松菜", "醤油"])
    ranked = _score([with_stock, without], pantry_items=["ほうれん草"])
    assert [s.recipe_id for s in ranked] == [2, 3]
    assert "pantry_uses" in _codes(_by_id(ranked)[2])
    # 在庫が空なら点は揃う（無いことで減点していない）。
    flat = _score([with_stock, without], pantry_items=[])
    assert flat[0].score == pytest.approx(flat[1].score)


def test_staples_do_not_count_as_pantry_hits() -> None:
    """基礎調味料（`[basics]`）は在庫に数えない——醤油だけ在庫にあっても加点しない。"""
    ranked = _score([_cand(2, "小松菜のおひたし", ingredients=["小松菜", "醤油"])], pantry_items=["醤油"])
    assert "pantry_uses" not in _codes(ranked[0])


def test_expiring_stock_adds_more_than_plain_stock() -> None:
    plain = _score([_cand(2, "ほうれん草のおひたし", ingredients=["ほうれん草"])], pantry_items=[{"item": "ほうれん草", "expires": "2026-10-31"}])
    soon = _score([_cand(2, "ほうれん草のおひたし", ingredients=["ほうれん草"])], pantry_items=[{"item": "ほうれん草", "expires": "2026-09-14"}])
    assert soon[0].score > plain[0].score
    assert "pantry_expiring" in _codes(soon[0])


# --- 履歴（直近に出たものは減点） -----------------------------------------------------


def test_recent_meal_is_penalized() -> None:
    recent = [{"date": "2026-09-11", "dish": "ほうれん草のおひたし"}]
    ranked = _score(
        [_cand(2, "ほうれん草のおひたし"), _cand(3, "小松菜のごま和え")], recent_meals=recent
    )
    assert [s.recipe_id for s in ranked] == [3, 2]
    assert "cooked_recently" in _codes(_by_id(ranked)[2])


def test_meal_older_than_the_window_is_not_penalized() -> None:
    old = [{"date": "2026-08-01", "dish": "ほうれん草のおひたし"}]
    ranked = _score([_cand(2, "ほうれん草のおひたし")], recent_meals=old)
    assert "cooked_recently" not in _codes(ranked[0])


def test_last_cooked_at_also_counts_as_history() -> None:
    """`chef_meal` に記録が無くても `last_cooked_at` が近ければ減点する。"""
    fresh = _cand(2, "きんぴらごぼう", last_cooked_at="2026-09-12T19:00:00")
    stale = _cand(3, "ひじきの煮物", last_cooked_at="2026-05-01T19:00:00")
    ranked = _score([fresh, stale])
    assert [s.recipe_id for s in ranked] == [3, 2]
    assert "cooked_recently" in _codes(_by_id(ranked)[2])
    # 最近作っていない側は小さな加点（理由の並びでは上位 3 件に入らないこともある）。
    all_reasons = _score([stale], rules={**RULES, "reasons_max": 99})
    assert "not_cooked_recently" in _codes(all_reasons[0])


# --- 栄養値なし・好み（候補から外す2つ） -----------------------------------------------


def test_recipe_without_nutrition_is_dropped() -> None:
    """D1: 栄養値の揃っていないレシピは候補に出さない。"""
    partial = _cand(3, "栄養値の一部だけある副菜", nutrition={"kcal": 100, "protein_g": None, "fat_g": 1, "carb_g": 10, "salt_g": 0.2})
    empty = _cand(4, "栄養値なしの副菜", nutrition={})
    ranked = _score([_cand(2, "ふつうの副菜"), partial, empty])
    assert [s.recipe_id for s in ranked] == [2]


def test_recipe_without_a_nutrition_source_is_dropped() -> None:
    """D1（ADR-019 D4 で足した分）: 数字があっても出どころが言えない値は候補にしない。"""
    ranked = _score([_cand(2, "ふつうの副菜"), _cand(3, "出所不明の副菜", nutrition_source="")])
    assert [s.recipe_id for s in ranked] == [2]


def test_estimated_recipe_below_the_coverage_floor_is_dropped() -> None:
    """ADR-019 D4: 推定の解決率が `nutrition_coverage_min` に届かないものは `partial`。"""
    good = _cand(2, "解決できた副菜", nutrition_source="estimated", nutrition_coverage=0.95)
    poor = _cand(3, "半分しか名寄せできていない副菜", nutrition_source="estimated", nutrition_coverage=0.4)
    ranked = _score([good, poor])
    assert [s.recipe_id for s in ranked] == [2]
    assert menu.nutrition_status(poor, 0.8) == menu.NUTRITION_PARTIAL
    # 解決率を持たない古い推定（ADR-019 より前の行）は落とさない——今より悪くしない。
    legacy = _cand(4, "解決率の無い古い推定", nutrition_source="estimated", nutrition_coverage=None)
    assert menu.nutrition_status(legacy, 0.8) == menu.NUTRITION_OK


def test_recommend_counts_partial_separately(conn: sqlite3.Connection, stocked: dict[str, int]) -> None:
    """画面が「名寄せへ」を出せるよう、`partial` で外した件数を別に数える。"""
    poor = _add_recipe(
        conn, "名寄せの足りない副菜", category="副菜", nutrition=_nutrition(70, 2, 1, 8, 0.4),
    )
    recipes.set_meta(conn, poor, nutrition_source="estimated")
    conn.execute("UPDATE chef_recipe_meta SET nutrition_coverage = 0.3 WHERE recipe_id = ?", (poor,))
    payload = menu.recommend(conn, main_recipe_id=stocked["main"])
    assert payload["excluded_partial"] == 1
    assert payload["coverage_min"] == 0.8
    assert poor not in [row["recipe_id"] for row in payload["slots"]["side"]]


def test_disliked_and_allergic_ingredients_are_excluded() -> None:
    ranked = _score(
        [_cand(2, "なすの煮物", ingredients=["なす"]), _cand(3, "きゅうりの酢の物", ingredients=["きゅうり"])],
        taste={"dislikes": "なす", "allergies": ""},
    )
    assert [s.recipe_id for s in ranked] == [3]


def test_liked_ingredients_add_points() -> None:
    ranked = _score(
        [_cand(2, "わかめの酢の物", ingredients=["わかめ"]), _cand(3, "きゅうりの酢の物", ingredients=["きゅうり"])],
        taste={"likes": "わかめ"},
    )
    assert [s.recipe_id for s in ranked] == [2, 3]
    assert "taste_like" in _codes(_by_id(ranked)[2])


# --- 時間・評価 ---------------------------------------------------------------------


def test_quick_side_dish_is_rewarded_but_not_the_main_slot() -> None:
    quick = _cand(2, "冷奴", total_minutes=5)
    slow = _cand(3, "ふろふき大根", total_minutes=60)
    ranked = _score([quick, slow])
    assert [s.recipe_id for s in ranked] == [2, 3]
    assert "quick" in _codes(_by_id(ranked)[2])
    # 主菜の枠では時間の加点をしない（D4「副菜・汁物では加点」）。
    as_main = _score([_cand(2, "手早い主菜", category="主菜", total_minutes=5)], main=None, slot_kind="main")
    assert "quick" not in _codes(as_main[0])


def test_rating_and_times_cooked_lift_the_familiar_one() -> None:
    familiar = _cand(2, "きんぴらごぼう", rating=5, times_cooked=8)
    unknown = _cand(3, "ひじきの煮物")
    ranked = _score([familiar, unknown])
    assert [s.recipe_id for s in ranked] == [2, 3]
    assert "well_rated" in _codes(_by_id(ranked)[2]) or "often_cooked" in _codes(_by_id(ranked)[2])


# --- 気分（第一段は語彙の一致だけ。ADR-018 D5） ----------------------------------------


def test_mood_vocabulary_matches_keywords_and_merges_conditions() -> None:
    matched = menu.match_mood("さっぱりしたものを早く", MOODS)
    assert set(matched["matched"]) == {"さっぱり", "早く"}
    assert matched["conditions"]["cuisine"] == ["和食"]
    assert matched["conditions"]["max_minutes"] == 15.0
    assert "揚げ物" in matched["conditions"]["exclude_dish_types"]


def test_mood_synonyms_hit_the_same_conditions() -> None:
    assert menu.match_mood("あっさりで", MOODS)["matched"] == ["さっぱり"]
    assert menu.match_mood("がっつり食べたい", MOODS)["conditions"]["main_ingredient"] == ["肉"]


def test_mood_with_no_keyword_is_ignored() -> None:
    matched = menu.match_mood("なんでもいい", MOODS)
    assert matched["matched"] == [] and matched["conditions"] == {}


def test_mood_lifts_matching_candidates_and_lowers_excluded_methods() -> None:
    conditions = menu.match_mood("さっぱり", MOODS)["conditions"] | {"labels": ["さっぱり"]}
    fresh = _cand(2, "きゅうりとわかめの酢の物", dish_type="生", cuisine="和食")
    fried = _cand(3, "野菜の天ぷら", dish_type="揚げ物", cuisine="和食")
    ranked = _score([fresh, fried], mood_conditions=conditions)
    assert [s.recipe_id for s in ranked] == [2, 3]
    assert "mood_match" in _codes(_by_id(ranked)[2])
    assert "mood_mismatch" in _codes(_by_id(ranked)[3])


def test_mood_time_limit_rewards_quick_recipes() -> None:
    conditions = menu.match_mood("早く", MOODS)["conditions"] | {"labels": ["早く"]}
    quick = _cand(2, "わかめスープ", total_minutes=8)
    slow = _cand(3, "けんちん汁", total_minutes=45)
    ranked = _score([quick, slow], mood_conditions=conditions)
    assert [s.recipe_id for s in ranked] == [2, 3]
    assert "mood_quick" in _codes(_by_id(ranked)[2])


# --- 理由の形（定型文の符牒。ADR-018 §4） ---------------------------------------------


def test_reasons_are_codes_with_params_and_capped() -> None:
    """理由は `{"code","params"}`（表示側が文にする）。件数は `reasons_max` まで。"""
    ranked = _score(
        [_cand(2, "ほうれん草のおひたし", ingredients=["ほうれん草"], rating=5, times_cooked=9, total_minutes=8, favorite=True)],
        pantry_items=["ほうれん草"],
    )
    reasons = ranked[0].reasons
    assert 1 <= len(reasons) <= int(RULES["reasons_max"])
    assert all(set(r) == {"code", "params"} for r in reasons)


def test_every_reason_code_has_a_translation(conn: sqlite3.Connection) -> None:
    """符牒 → 文の対応が ja/en の両方にあること（訳し忘れの検算）。"""
    from manor import i18n

    codes = {
        "band_fits", "cooked_recently", "cuisine_match", "dish_type_differs", "favorite",
        "fills_kcal", "fills_protein_g", "fills_fat_g", "fills_carb_g", "fills_salt_g",
        "keeps_salt_low", "main_ingredient_differs", "mood_match", "mood_mismatch",
        "mood_quick", "not_cooked_recently", "often_cooked", "over_kcal", "over_protein_g",
        "over_fat_g", "over_carb_g", "pantry_expiring", "pantry_uses", "quick", "salt_over",
        "taste_like", "well_rated",
    }
    for lang in ("ja", "en"):
        available = set(i18n.all_keys(lang))
        missing = [c for c in codes if f"chef.menu.reason.{c}" not in available]
        assert missing == [], f"{lang}: {missing}"


# --- DB（候補の取り出し・combo・plan） -------------------------------------------------


def _add_recipe(conn: sqlite3.Connection, title: str, *, category: str, nutrition, **meta) -> int:
    recipe_id = recipes.add(
        conn,
        {
            "title": title,
            "servings": 1,
            "total_minutes": meta.pop("total_minutes", 15),
            "ingredients": [{"name": name, "qty": "1", "unit": "個"} for name in meta.pop("ingredients", ["水"])],
            "tools": ["鍋"],
            "phases": [{"id": "cook", "title": "作る"}],
            "steps": [{"index": 1, "phase": "cook", "title": "作る", "instruction": "作る。", "completion": "manual"}],
        },
    )
    fields = dict(nutrition or {})
    if fields:
        fields["nutrition_source"] = "manual"
    recipes.set_meta(conn, recipe_id, category=category, **fields, **meta)
    return recipe_id


@pytest.fixture
def stocked(conn: sqlite3.Connection) -> dict[str, int]:
    """主菜1・副菜2・汁物1 と、栄養値の無い副菜1（候補から外れる側）。"""
    ids = {
        "main": _add_recipe(conn, "豚の生姜焼き", category="主菜", nutrition=_nutrition(450, 24, 22, 30, 1.8), main_ingredient="肉", cuisine="和食", ingredients=["豚肉", "生姜"]),
        "side_veg": _add_recipe(conn, "ほうれん草のおひたし", category="副菜", nutrition=_nutrition(60, 3, 1, 6, 0.5), main_ingredient="野菜", cuisine="和食", total_minutes=10, ingredients=["ほうれん草"]),
        "side_meat": _add_recipe(conn, "ハムのサラダ", category="副菜", nutrition=_nutrition(200, 8, 16, 5, 1.4), main_ingredient="肉", cuisine="洋食", total_minutes=8, ingredients=["ハム", "レタス"]),
        "soup": _add_recipe(conn, "わかめの味噌汁", category="汁物", nutrition=_nutrition(40, 3, 1, 4, 1.2), main_ingredient="野菜", cuisine="和食", total_minutes=10, ingredients=["わかめ", "味噌"]),
        "no_nutrition": _add_recipe(conn, "栄養値のない小鉢", category="副菜", nutrition=None, main_ingredient="野菜"),
    }
    conn.commit()
    return ids


def test_recommend_with_a_given_main(conn: sqlite3.Connection, stocked: dict[str, int]) -> None:
    payload = menu.recommend(conn, main_recipe_id=stocked["main"], today=TODAY)
    assert payload["main"]["recipe_id"] == stocked["main"]
    assert payload["slots"]["main"] == []  # 決まっているので枠は空
    side_ids = [row["recipe_id"] for row in payload["slots"]["side"]]
    assert stocked["side_veg"] in side_ids
    assert stocked["no_nutrition"] not in side_ids  # D1
    assert payload["excluded_no_nutrition"] >= 1
    # combo は 主菜＋副菜1位＋汁物1位。
    assert len(payload["combo"]["recipe_ids"]) == 3
    assert set(payload["combo"]["band_check"]) == set(menu.NUTRIENTS)


def test_recommend_without_a_main_also_ranks_main_dishes(conn: sqlite3.Connection, stocked: dict[str, int]) -> None:
    payload = menu.recommend(conn, today=TODAY)
    assert payload["main"] is None
    assert [row["recipe_id"] for row in payload["slots"]["main"]] == [stocked["main"]]
    assert payload["combo"]["recipe_ids"][0] == stocked["main"]


def test_recommend_honours_exclude(conn: sqlite3.Connection, stocked: dict[str, int]) -> None:
    payload = menu.recommend(conn, main_recipe_id=stocked["main"], exclude=[stocked["side_veg"]], today=TODAY)
    assert stocked["side_veg"] not in [row["recipe_id"] for row in payload["slots"]["side"]]


def test_recommend_caps_each_slot_at_top_n(conn: sqlite3.Connection, stocked: dict[str, int]) -> None:
    for i in range(8):
        _add_recipe(conn, f"副菜その{i}", category="副菜", nutrition=_nutrition(50, 2, 1, 5, 0.3))
    conn.commit()
    payload = menu.recommend(conn, main_recipe_id=stocked["main"], today=TODAY)
    assert len(payload["slots"]["side"]) == int(RULES["top_n"])


def test_recommend_rejects_unknown_main_and_bad_slot(conn: sqlite3.Connection, stocked: dict[str, int]) -> None:
    with pytest.raises(ManorError) as exc:
        menu.recommend(conn, main_recipe_id=9999, today=TODAY)
    assert exc.value.key == menu.ERR_RECIPE_NOT_FOUND
    with pytest.raises(ManorError) as exc:
        menu.recommend(conn, slot="brunch", today=TODAY)
    assert exc.value.key == menu.ERR_BAD_REQUEST


def test_recommend_applies_mood_from_free_text(conn: sqlite3.Connection, stocked: dict[str, int]) -> None:
    payload = menu.recommend(conn, main_recipe_id=stocked["main"], mood="さっぱりしたもの", today=TODAY)
    assert payload["applied_mood"]["matched"] == ["さっぱり"]
    assert payload["applied_mood"]["conditions"]["cuisine"] == ["和食"]


def test_plan_writes_planned_rows_into_chef_meal(conn: sqlite3.Connection, stocked: dict[str, int]) -> None:
    result = menu.plan(conn, date=TODAY, slot="dinner", recipe_ids=[stocked["main"], stocked["side_veg"]])
    assert len(result["items"]) == 2
    rows = conn.execute("SELECT dish, planned, date, slot, ingredients FROM chef_meal ORDER BY id").fetchall()
    assert [r["planned"] for r in rows] == [1, 1]
    assert [r["dish"] for r in rows] == ["豚の生姜焼き", "ほうれん草のおひたし"]
    assert rows[0]["date"] == TODAY and rows[0]["slot"] == "dinner"
    assert "豚肉" in rows[0]["ingredients"]


def test_planned_rows_feed_the_history_penalty(conn: sqlite3.Connection, stocked: dict[str, int]) -> None:
    """「この献立にする」で書いた行が、そのまま履歴の減点に効く（D6 の狙い）。"""
    before = menu.recommend(conn, main_recipe_id=stocked["main"], today=TODAY)
    top_before = before["slots"]["side"][0]["recipe_id"]
    menu.plan(conn, date=TODAY, slot="dinner", recipe_ids=[top_before])
    after = menu.recommend(conn, main_recipe_id=stocked["main"], today=TODAY)
    scores = {row["recipe_id"]: row["score"] for row in after["slots"]["side"]}
    assert scores[top_before] < next(row["score"] for row in before["slots"]["side"] if row["recipe_id"] == top_before)


def test_plan_rejects_empty_ids_and_unknown_recipe(conn: sqlite3.Connection, stocked: dict[str, int]) -> None:
    with pytest.raises(ManorError) as exc:
        menu.plan(conn, date=TODAY, slot="dinner", recipe_ids=[])
    assert exc.value.key == menu.ERR_BAD_REQUEST
    with pytest.raises(ManorError) as exc:
        menu.plan(conn, date=TODAY, slot="dinner", recipe_ids=[9999])
    assert exc.value.key == menu.ERR_RECIPE_NOT_FOUND


# --- CLI（確認用。ADR-018 D7-1） ------------------------------------------------------


def _args(**over):
    from types import SimpleNamespace

    base = {"main": None, "people": 2, "mood": "", "slot": "dinner", "exclude": "", "json": False}
    base.update(over)
    return SimpleNamespace(**base)


def test_cli_menu_text_lists_each_slot(conn: sqlite3.Connection, home: Path, stocked: dict[str, int]) -> None:
    out = chef_cli.cmd_menu(conn, home, _args(main=stocked["main"]))
    assert isinstance(out, str)
    assert "豚の生姜焼き" in out
    assert "ほうれん草のおひたし" in out
    assert "副菜" in out and "汁物" in out


def test_cli_menu_json_returns_the_api_payload(conn: sqlite3.Connection, home: Path, stocked: dict[str, int]) -> None:
    out = chef_cli.cmd_menu(conn, home, _args(main=stocked["main"], json=True))
    assert isinstance(out, dict)
    assert set(out["slots"]) == set(menu.SLOT_KINDS)
    assert out["band"]["salt_g"]["max"] == 2.5
