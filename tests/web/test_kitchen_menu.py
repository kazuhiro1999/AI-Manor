"""`/api/v1/kitchen/menu/*`（ADR-018 D6）の試験。

`tests/web/test_kitchen_media.py` と同じ流儀（`TestClient` を直に叩く）。LLM を呼ばない段
（ADR-018 D7-1）なので外部の差し替えは要らない——規則だけで結果が決まる。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from manor import user as user_mod
from manor.staff.chef import recipes as chef_recipes
from manor.web import app as web_app_mod
from manor.web._common import USER_COOKIE_NAME

TODAY = "2026-09-13"


def make_client(home: Path, *, read_only: bool = False) -> TestClient:
    return TestClient(web_app_mod.create_app(home, read_only=read_only))


def _nutrition(kcal, protein, fat, carb, salt) -> dict[str, float]:
    return {"kcal": kcal, "protein_g": protein, "fat_g": fat, "carb_g": carb, "salt_g": salt}


def _add(conn, title: str, *, category: str, nutrition, ingredients=("水",), **meta) -> int:
    recipe_id = chef_recipes.add(
        conn,
        {
            "title": title,
            "servings": 1,
            "total_minutes": meta.pop("total_minutes", 15),
            "ingredients": [{"name": n, "qty": "1", "unit": "個"} for n in ingredients],
            "tools": ["鍋"],
            "phases": [{"id": "cook", "title": "作る"}],
            "steps": [{"index": 1, "phase": "cook", "title": "作る", "instruction": "作る。", "completion": "manual"}],
        },
    )
    fields = dict(nutrition or {})
    if fields:
        fields["nutrition_source"] = "manual"
    chef_recipes.set_meta(conn, recipe_id, category=category, **fields, **meta)
    return recipe_id


@pytest.fixture
def stocked(conn) -> dict[str, int]:
    ids = {
        "main": _add(conn, "豚の生姜焼き", category="主菜", nutrition=_nutrition(450, 24, 22, 30, 1.8), main_ingredient="肉", cuisine="和食", ingredients=("豚肉", "生姜")),
        "side_veg": _add(conn, "ほうれん草のおひたし", category="副菜", nutrition=_nutrition(60, 3, 1, 6, 0.5), main_ingredient="野菜", cuisine="和食", total_minutes=10, ingredients=("ほうれん草",)),
        "side_meat": _add(conn, "ハムのサラダ", category="副菜", nutrition=_nutrition(200, 8, 16, 5, 1.4), main_ingredient="肉", cuisine="洋食", total_minutes=8, ingredients=("ハム",)),
        "soup": _add(conn, "わかめの味噌汁", category="汁物", nutrition=_nutrition(40, 3, 1, 4, 1.2), main_ingredient="野菜", cuisine="和食", total_minutes=10, ingredients=("わかめ",)),
        "no_nutrition": _add(conn, "栄養値のない小鉢", category="副菜", nutrition=None, main_ingredient="野菜"),
    }
    conn.commit()
    return ids


# --- recommend（主菜あり／なし） ---------------------------------------------------


def test_recommend_with_main_returns_the_adr_shape(conn, home: Path, stocked: dict[str, int]) -> None:
    client = make_client(home)
    res = client.get("/api/v1/kitchen/menu/recommend", params={"main_recipe_id": stocked["main"]})
    assert res.status_code == 200
    body = res.json()
    assert set(body["slots"]) == {"main", "side", "soup"}
    assert body["main"]["recipe_id"] == stocked["main"]
    assert body["slots"]["main"] == []
    assert body["people"] == 2 and body["slot"] == "dinner"
    assert body["band"]["salt_g"] == {"min": 0.0, "max": 2.5}
    # 候補は題名・理由・栄養つき（ADR-018 D3）。
    top = body["slots"]["side"][0]
    assert set(top) >= {"recipe_id", "title", "score", "reasons", "nutrition", "hero_image"}
    assert top["reasons"] and all(set(r) == {"code", "params"} for r in top["reasons"])
    # combo は 主菜＋副菜1位＋汁物1位の合計と帯の比較。
    assert len(body["combo"]["recipe_ids"]) == 3
    assert body["combo"]["band_check"]["kcal"]["status"] in ("in", "under", "over")


def test_recommend_without_main_ranks_main_dishes_too(conn, home: Path, stocked: dict[str, int]) -> None:
    client = make_client(home)
    body = client.get("/api/v1/kitchen/menu/recommend").json()
    assert body["main"] is None
    assert [row["recipe_id"] for row in body["slots"]["main"]] == [stocked["main"]]


def test_recommend_drops_recipes_without_nutrition(conn, home: Path, stocked: dict[str, int]) -> None:
    client = make_client(home)
    body = client.get("/api/v1/kitchen/menu/recommend", params={"main_recipe_id": stocked["main"]}).json()
    assert stocked["no_nutrition"] not in [row["recipe_id"] for row in body["slots"]["side"]]
    assert body["excluded_no_nutrition"] >= 1


def test_recommend_honours_exclude_as_csv(conn, home: Path, stocked: dict[str, int]) -> None:
    client = make_client(home)
    body = client.get(
        "/api/v1/kitchen/menu/recommend",
        params={"main_recipe_id": stocked["main"], "exclude": f"{stocked['side_veg']},{stocked['soup']}"},
    ).json()
    assert stocked["side_veg"] not in [row["recipe_id"] for row in body["slots"]["side"]]
    assert body["slots"]["soup"] == []


def test_recommend_rejects_broken_exclude_with_400(conn, home: Path, stocked: dict[str, int]) -> None:
    client = make_client(home)
    res = client.get("/api/v1/kitchen/menu/recommend", params={"exclude": "abc"})
    assert res.status_code == 400


def test_recommend_applies_mood(conn, home: Path, stocked: dict[str, int]) -> None:
    client = make_client(home)
    body = client.get(
        "/api/v1/kitchen/menu/recommend", params={"main_recipe_id": stocked["main"], "mood": "さっぱり"}
    ).json()
    assert body["applied_mood"]["matched"] == ["さっぱり"]
    # 和食の副菜が洋食より上（`[menu.mood]` の cuisine 条件）。
    order = [row["recipe_id"] for row in body["slots"]["side"]]
    assert order.index(stocked["side_veg"]) < order.index(stocked["side_meat"])


def test_recommend_unknown_main_is_404(conn, home: Path, stocked: dict[str, int]) -> None:
    client = make_client(home)
    assert client.get("/api/v1/kitchen/menu/recommend", params={"main_recipe_id": 9999}).status_code == 404


def test_recommend_bad_slot_is_400(conn, home: Path, stocked: dict[str, int]) -> None:
    client = make_client(home)
    assert client.get("/api/v1/kitchen/menu/recommend", params={"slot": "brunch"}).status_code == 400


def test_recommend_is_not_available_when_recipe_table_missing(conn, home: Path) -> None:
    """更新前に chef を導入した既存 home の想定——500 ではなく 404 で案内する
    （`create_app` は起動時に `manor init` を通すので、client を作った**後**に落とす）。"""
    client = make_client(home)
    conn.execute("DROP TABLE chef_recipe_meta")
    conn.execute("DROP TABLE chef_recipe")
    conn.commit()
    assert client.get("/api/v1/kitchen/menu/recommend").status_code == 404
    assert client.post(
        "/api/v1/kitchen/menu/plan", json={"date": TODAY, "slot": "dinner", "recipe_ids": [1]}
    ).status_code == 404


# --- plan（この献立にする） --------------------------------------------------------


def test_plan_writes_planned_rows(conn, home: Path, stocked: dict[str, int]) -> None:
    client = make_client(home)
    res = client.post(
        "/api/v1/kitchen/menu/plan",
        json={"date": TODAY, "slot": "dinner", "recipe_ids": [stocked["main"], stocked["side_veg"], stocked["soup"]]},
    )
    assert res.status_code == 200
    assert len(res.json()["items"]) == 3
    rows = conn.execute("SELECT dish, planned, date FROM chef_meal ORDER BY id").fetchall()
    assert len(rows) == 3
    assert {r["planned"] for r in rows} == {1}
    assert {r["date"] for r in rows} == {TODAY}
    assert "豚の生姜焼き" in {r["dish"] for r in rows}


def test_plan_rejects_empty_recipe_ids_with_400(conn, home: Path, stocked: dict[str, int]) -> None:
    client = make_client(home)
    res = client.post("/api/v1/kitchen/menu/plan", json={"date": TODAY, "slot": "dinner", "recipe_ids": []})
    assert res.status_code == 400


def test_plan_unknown_recipe_is_404_and_writes_nothing(conn, home: Path, stocked: dict[str, int]) -> None:
    client = make_client(home)
    res = client.post(
        "/api/v1/kitchen/menu/plan",
        json={"date": TODAY, "slot": "dinner", "recipe_ids": [stocked["main"], 9999]},
    )
    assert res.status_code == 404
    assert conn.execute("SELECT COUNT(*) AS n FROM chef_meal").fetchone()["n"] == 0


def test_plan_rejects_bad_date(conn, home: Path, stocked: dict[str, int]) -> None:
    client = make_client(home)
    res = client.post(
        "/api/v1/kitchen/menu/plan", json={"date": "2026/09/13", "slot": "dinner", "recipe_ids": [stocked["main"]]}
    )
    assert res.status_code == 400


def test_plan_forbidden_when_read_only(conn, home: Path, stocked: dict[str, int]) -> None:
    client = make_client(home, read_only=True)
    res = client.post(
        "/api/v1/kitchen/menu/plan", json={"date": TODAY, "slot": "dinner", "recipe_ids": [stocked["main"]]}
    )
    assert res.status_code == 403


# --- 利用者（台所は共通。ADR-014 D4・ADR-018 §4） -----------------------------------


def test_menu_is_shared_across_viewing_users(conn, home: Path, stocked: dict[str, int]) -> None:
    """**動画リストと逆の判断**——献立は誰が開いても同じものが出る（食卓は1つ）。
    `viewing_user_id` は応答に載るが、候補の絞りには使わない。
    """
    other_id = user_mod.add(conn, name="同居人")
    conn.commit()
    client = make_client(home)

    mine = client.get("/api/v1/kitchen/menu/recommend", params={"main_recipe_id": stocked["main"]}).json()
    client.cookies.set(USER_COOKIE_NAME, other_id)
    theirs = client.get("/api/v1/kitchen/menu/recommend", params={"main_recipe_id": stocked["main"]}).json()

    assert theirs["viewing_user_id"] == other_id
    assert mine["viewing_user_id"] != theirs["viewing_user_id"]
    assert [r["recipe_id"] for r in mine["slots"]["side"]] == [r["recipe_id"] for r in theirs["slots"]["side"]]

    # 同居人が決めた献立も同じ食卓に乗る（履歴の減点は全員に効く）。
    client.post(
        "/api/v1/kitchen/menu/plan", json={"date": TODAY, "slot": "dinner", "recipe_ids": [stocked["side_veg"]]}
    )
    client.cookies.delete(USER_COOKIE_NAME)
    after = client.get("/api/v1/kitchen/menu/recommend", params={"main_recipe_id": stocked["main"]}).json()
    scores = {row["recipe_id"]: row["score"] for row in after["slots"]["side"]}
    before = {row["recipe_id"]: row["score"] for row in mine["slots"]["side"]}
    assert scores[stocked["side_veg"]] < before[stocked["side_veg"]]
