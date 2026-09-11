"""料理長のレシピ帳の試験（ADR-015 §3・D3）。すべて合成データ・架空の家庭。

見本は `tests/fixtures/chahan.recipe.json`（`kitchen-xr/Docs/samples/chahan.recipe.json`
のコピー。主人がよく作る炒飯——9工程あるので、セッションの試験に都合がよい）。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from manor import user as user_mod
from manor.errors import ManorError
from manor.staff.chef import recipes

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "chahan.recipe.json"


@pytest.fixture
def sample_recipe() -> dict:
    with FIXTURE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _minimal_recipe(**overrides) -> dict:
    base = {
        "title": "試験用の一品",
        "servings": 1,
        "total_minutes": 5,
        "ingredients": [{"name": "水", "qty": "1", "unit": "L"}],
        "tools": ["鍋"],
        "phases": [{"id": "cook", "title": "煮る"}],
        "steps": [
            {
                "index": 1,
                "phase": "cook",
                "title": "沸かす",
                "instruction": "水を沸かす。",
                "completion": "manual",
            }
        ],
    }
    base.update(overrides)
    return base


# --- validate / add / get（見本の往復） ------------------------------------------------


def test_add_then_get_roundtrip_matches_sample(conn, home: Path, sample_recipe: dict) -> None:
    recipe_id = recipes.add(conn, sample_recipe)
    got = recipes.get(conn, recipe_id)

    assert got["id"] == recipe_id
    assert got["title"] == sample_recipe["title"]
    assert got["source_url"] == sample_recipe["source_url"]
    assert got["hero_image"] == ""  # null → 空文字に正規化
    assert got["servings"] == sample_recipe["servings"]
    assert got["total_minutes"] == sample_recipe["total_minutes"]
    assert got["tools"] == sample_recipe["tools"]
    assert [p["id"] for p in got["phases"]] == [p["id"] for p in sample_recipe["phases"]]
    assert len(got["ingredients"]) == len(sample_recipe["ingredients"])
    assert len(got["steps"]) == len(sample_recipe["steps"]) == 9
    for got_step, src_step in zip(got["steps"], sample_recipe["steps"]):
        assert got_step["index"] == src_step["index"]
        assert got_step["title"] == src_step["title"]
        assert got_step["instruction"] == src_step["instruction"]
        assert got_step["completion"] == src_step["completion"]

    # 本体と分けて持つ「うちの値」は空で初期化される（ADR-015 D1）。
    assert got["meta"]["nutrition_source"] == ""
    assert got["meta"]["tags"] == []
    assert got["meta"]["favorite"] is False
    assert got["meta"]["times_cooked"] == 0


def test_get_missing_recipe_raises_code2(conn, home: Path) -> None:
    with pytest.raises(ManorError) as exc_info:
        recipes.get(conn, 999)
    assert exc_info.value.code == 2


# --- 検算の拒否 ------------------------------------------------------------------------


def test_validate_rejects_step_title_over_12_chars(conn, home: Path) -> None:
    recipe = _minimal_recipe()
    recipe["steps"][0]["title"] = "あ" * 13
    with pytest.raises(ManorError) as exc_info:
        recipes.add(conn, recipe)
    assert exc_info.value.code == 2


def test_validate_accepts_step_title_at_12_chars(conn, home: Path) -> None:
    recipe = _minimal_recipe()
    recipe["steps"][0]["title"] = "あ" * 12
    recipe_id = recipes.add(conn, recipe)
    assert recipes.get(conn, recipe_id)["steps"][0]["title"] == "あ" * 12


def test_validate_rejects_instruction_over_60_chars(conn, home: Path) -> None:
    recipe = _minimal_recipe()
    recipe["steps"][0]["instruction"] = "あ" * 61
    with pytest.raises(ManorError) as exc_info:
        recipes.add(conn, recipe)
    assert exc_info.value.code == 2


def test_validate_rejects_completion_outside_vocabulary(conn, home: Path) -> None:
    recipe = _minimal_recipe()
    recipe["steps"][0]["completion"] = "instant"
    with pytest.raises(ManorError) as exc_info:
        recipes.add(conn, recipe)
    assert exc_info.value.code == 2


def test_validate_rejects_phase_not_declared(conn, home: Path) -> None:
    recipe = _minimal_recipe()
    recipe["steps"][0]["phase"] = "存在しない工程"
    with pytest.raises(ManorError) as exc_info:
        recipes.add(conn, recipe)
    assert exc_info.value.code == 2


def test_validate_rejects_missing_step_index(conn, home: Path) -> None:
    """1..n の連番でない（欠番）例。"""
    recipe = _minimal_recipe(
        steps=[
            {"index": 1, "phase": "cook", "title": "下ごしらえ", "instruction": "準備する。", "completion": "manual"},
            {"index": 3, "phase": "cook", "title": "仕上げ", "instruction": "仕上げる。", "completion": "manual"},
        ]
    )
    with pytest.raises(ManorError) as exc_info:
        recipes.add(conn, recipe)
    assert exc_info.value.code == 2


def test_validate_rejects_duplicate_step_index(conn, home: Path) -> None:
    recipe = _minimal_recipe(
        steps=[
            {"index": 1, "phase": "cook", "title": "下ごしらえ", "instruction": "準備する。", "completion": "manual"},
            {"index": 1, "phase": "cook", "title": "仕上げ", "instruction": "仕上げる。", "completion": "manual"},
        ]
    )
    with pytest.raises(ManorError) as exc_info:
        recipes.add(conn, recipe)
    assert exc_info.value.code == 2


def test_validate_rejects_missing_title(conn, home: Path) -> None:
    recipe = _minimal_recipe(title="")
    with pytest.raises(ManorError) as exc_info:
        recipes.add(conn, recipe)
    assert exc_info.value.code == 2


# --- update（本体の丸ごと差し替え。meta は触らない） -------------------------------------


def test_update_replaces_body_but_keeps_meta(conn, home: Path) -> None:
    recipe_id = recipes.add(conn, _minimal_recipe())
    recipes.set_meta(conn, recipe_id, kcal=250, tags=["定番"])

    new_body = _minimal_recipe(title="改訂版")
    updated = recipes.update(conn, recipe_id, new_body)

    assert updated["title"] == "改訂版"
    assert updated["meta"]["kcal"] == 250  # 本体を差し替えても meta は残る
    assert updated["meta"]["tags"] == ["定番"]


def test_update_missing_recipe_raises_code2(conn, home: Path) -> None:
    with pytest.raises(ManorError) as exc_info:
        recipes.update(conn, 999, _minimal_recipe())
    assert exc_info.value.code == 2


# --- set_meta（部分更新・manual の印） --------------------------------------------------


def test_set_meta_partial_update_only_touches_given_fields(conn, home: Path) -> None:
    recipe_id = recipes.add(conn, _minimal_recipe())
    recipes.set_meta(conn, recipe_id, kcal=500, memo="最初のメモ")

    result = recipes.set_meta(conn, recipe_id, memo="うちは油少なめ")
    assert result["meta"]["memo"] == "うちは油少なめ"
    assert result["meta"]["kcal"] == 500  # 渡さなかった欄は据え置き


def test_set_meta_numeric_without_source_marks_manual(conn, home: Path) -> None:
    recipe_id = recipes.add(conn, _minimal_recipe())
    result = recipes.set_meta(conn, recipe_id, kcal=480, protein_g=20)
    assert result["meta"]["nutrition_source"] == "manual"


def test_set_meta_explicit_source_overrides_auto_manual(conn, home: Path) -> None:
    recipe_id = recipes.add(conn, _minimal_recipe())
    result = recipes.set_meta(conn, recipe_id, kcal=480, nutrition_source="estimated")
    assert result["meta"]["nutrition_source"] == "estimated"


def test_set_meta_rejects_invalid_nutrition_source(conn, home: Path) -> None:
    recipe_id = recipes.add(conn, _minimal_recipe())
    with pytest.raises(ManorError) as exc_info:
        recipes.set_meta(conn, recipe_id, nutrition_source="guessed")
    assert exc_info.value.code == 2


def test_set_meta_rejects_rating_out_of_range(conn, home: Path) -> None:
    recipe_id = recipes.add(conn, _minimal_recipe())
    with pytest.raises(ManorError):
        recipes.set_meta(conn, recipe_id, rating=6)


def test_set_meta_favorite_and_tags(conn, home: Path) -> None:
    recipe_id = recipes.add(conn, _minimal_recipe())
    result = recipes.set_meta(conn, recipe_id, favorite=True, tags=["和食", "定番"])
    assert result["meta"]["favorite"] is True
    assert result["meta"]["tags"] == ["和食", "定番"]


# --- list_recipes（絞り） ---------------------------------------------------------------


def test_list_recipes_filters(conn, home: Path) -> None:
    wa_id = recipes.add(conn, _minimal_recipe(title="肉じゃが"))
    yo_id = recipes.add(conn, _minimal_recipe(title="オムライス"))
    recipes.set_meta(conn, wa_id, tags=["和食"], favorite=True)
    recipes.set_meta(conn, yo_id, tags=["洋食"])

    by_q = recipes.list_recipes(conn, q="じゃが")
    assert [r["id"] for r in by_q] == [wa_id]

    by_tag = recipes.list_recipes(conn, tag="洋食")
    assert [r["id"] for r in by_tag] == [yo_id]

    by_favorite = recipes.list_recipes(conn, favorite=True)
    assert [r["id"] for r in by_favorite] == [wa_id]


def test_list_recipes_excludes_archived_by_default(conn, home: Path) -> None:
    keep_id = recipes.add(conn, _minimal_recipe(title="残す料理"))
    gone_id = recipes.add(conn, _minimal_recipe(title="畳む料理"))
    recipes.archive(conn, gone_id)

    default_list = recipes.list_recipes(conn)
    assert gone_id not in [r["id"] for r in default_list]
    assert keep_id in [r["id"] for r in default_list]

    with_archived = recipes.list_recipes(conn, include_archived=True)
    assert gone_id in [r["id"] for r in with_archived]


def test_archive_is_idempotent(conn, home: Path) -> None:
    recipe_id = recipes.add(conn, _minimal_recipe())
    first = recipes.archive(conn, recipe_id)
    second = recipes.archive(conn, recipe_id)
    assert first["id"] == second["id"]


# --- 調理セッション ----------------------------------------------------------------------


def test_session_next_nine_times_then_end_counts_once(conn, home: Path, sample_recipe: dict) -> None:
    recipe_id = recipes.add(conn, sample_recipe)
    session = recipes.start_session(conn, recipe_id, user_id="master")
    assert session["current"] == 1

    progress = None
    for _ in range(9):
        progress = recipes.apply_event(conn, session["id"], "next")
    assert progress["current"] == 9  # 9工程なので n で止まる

    recipes.end_session(conn, session["id"])
    got = recipes.get(conn, recipe_id)
    assert got["meta"]["times_cooked"] == 1
    assert got["meta"]["last_cooked_at"] is not None


def test_session_prev_stops_at_one(conn, home: Path, sample_recipe: dict) -> None:
    recipe_id = recipes.add(conn, sample_recipe)
    session = recipes.start_session(conn, recipe_id, user_id="master")
    result = recipes.apply_event(conn, session["id"], "prev")
    assert result["current"] == 1


def test_session_next_at_last_step_is_not_same_as_done(conn, home: Path) -> None:
    """`next` で最終工程（n）に達しても、`done` イベントと同じ状態にはならない
    （ADR-015 D3）——`current` を動かすだけの `next` と、工程の完了を報告する `done` は
    別のイベントとして記録される。
    """
    recipe = _minimal_recipe(
        steps=[
            {"index": 1, "phase": "cook", "title": "工程1", "instruction": "1つ目。", "completion": "manual"},
            {"index": 2, "phase": "cook", "title": "工程2", "instruction": "2つ目。", "completion": "manual"},
        ]
    )
    recipe_id = recipes.add(conn, recipe)
    session = recipes.start_session(conn, recipe_id, user_id="master")
    recipes.apply_event(conn, session["id"], "next")  # current=2 (n)
    recipes.apply_event(conn, session["id"], "next")  # n で止まる

    events = [
        dict(r)
        for r in conn.execute(
            "SELECT type FROM chef_cook_event WHERE session_id = ? ORDER BY id", (session["id"],)
        ).fetchall()
    ]
    assert [e["type"] for e in events] == ["next", "next"]
    assert "done" not in [e["type"] for e in events]

    current = recipes.current_session(conn, user_id="master")
    assert current["current"] == 2  # n に達しているが、まだ終了していない
    assert current["id"] == session["id"]


def test_apply_event_rejects_unknown_type(conn, home: Path) -> None:
    recipe_id = recipes.add(conn, _minimal_recipe())
    session = recipes.start_session(conn, recipe_id, user_id="master")
    with pytest.raises(ManorError) as exc_info:
        recipes.apply_event(conn, session["id"], "teleport")
    assert exc_info.value.code == 2


def test_start_session_resumes_unended_session_for_same_user(conn, home: Path) -> None:
    recipe1 = recipes.add(conn, _minimal_recipe(title="一品目"))
    recipe2 = recipes.add(conn, _minimal_recipe(title="二品目"))

    first = recipes.start_session(conn, recipe1, user_id="master")
    again = recipes.start_session(conn, recipe2, user_id="master")
    assert again["id"] == first["id"]  # 未終了セッションがあればそれを返す


def test_current_session_is_per_user(conn, home: Path) -> None:
    user_mod.add(conn, "同居人", user_id="u2")
    recipe1 = recipes.add(conn, _minimal_recipe(title="master の一品"))
    recipe2 = recipes.add(conn, _minimal_recipe(title="u2 の一品"))

    session_master = recipes.start_session(conn, recipe1, user_id="master")
    session_u2 = recipes.start_session(conn, recipe2, user_id="u2")

    assert recipes.current_session(conn, user_id="master")["id"] == session_master["id"]
    assert recipes.current_session(conn, user_id="u2")["id"] == session_u2["id"]


def test_current_session_none_when_no_session(conn, home: Path) -> None:
    assert recipes.current_session(conn, user_id="master") is None


def test_end_session_is_idempotent_for_times_cooked(conn, home: Path) -> None:
    recipe_id = recipes.add(conn, _minimal_recipe())
    session = recipes.start_session(conn, recipe_id, user_id="master")
    recipes.end_session(conn, session["id"])
    recipes.end_session(conn, session["id"])  # 2回目は数えない
    assert recipes.get(conn, recipe_id)["meta"]["times_cooked"] == 1


def test_apply_event_after_end_raises_code2(conn, home: Path) -> None:
    recipe_id = recipes.add(conn, _minimal_recipe())
    session = recipes.start_session(conn, recipe_id, user_id="master")
    recipes.end_session(conn, session["id"])
    with pytest.raises(ManorError) as exc_info:
        recipes.apply_event(conn, session["id"], "next")
    assert exc_info.value.code == 2


def test_validate_does_not_mutate_input(conn, home: Path, sample_recipe: dict) -> None:
    """`validate`/`add` は渡した dict を書き換えない（呼び出し側の JSON を壊さない）。"""
    original = copy.deepcopy(sample_recipe)
    recipes.add(conn, sample_recipe)
    assert sample_recipe == original
