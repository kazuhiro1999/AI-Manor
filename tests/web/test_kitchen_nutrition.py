"""`/api/v1/kitchen/recipes/{id}/nutrition` と `/api/v1/kitchen/food/*`（ADR-019 D5）の試験。

成分表は 5 行の偽データ（`tests/fixtures/food_composition_sample.csv`）を取り込む
——**実データは試験に入れない**（ADR-019 D6）。`tests/web/test_kitchen_recipes.py` と
同じ流儀（`TestClient` を直に叩く）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from manor.staff.chef import nutrition as chef_nutrition
from manor.web import app as web_app_mod

FIXTURE_CSV = Path(__file__).resolve().parent.parent / "fixtures" / "food_composition_sample.csv"


def make_client(home: Path, *, read_only: bool = False) -> TestClient:
    return TestClient(web_app_mod.create_app(home, read_only=read_only))


def _recipe(ingredients: list[dict[str, str]], *, title: str = "試験用の一品", servings: int = 2) -> dict:
    return {
        "title": title,
        "servings": servings,
        "total_minutes": 10,
        "ingredients": ingredients,
        "tools": ["鍋"],
        "phases": [{"id": "cook", "title": "煮る"}],
        "steps": [
            {"index": 1, "phase": "cook", "title": "煮る", "instruction": "煮る。", "completion": "manual"}
        ],
    }


@pytest.fixture
def stocked_foods(conn) -> None:
    chef_nutrition.import_food_table(conn, FIXTURE_CSV)
    conn.commit()


# --- GET /recipes/{id}/nutrition -------------------------------------------------------


def test_nutrition_adds_source_coverage_and_unresolved(home: Path, stocked_foods: None) -> None:
    client = make_client(home)
    created = client.post(
        "/api/v1/kitchen/recipes",
        json=_recipe([
            {"name": "豚ひき肉", "qty": "200", "unit": "g"},
            {"name": "ナンプラー", "qty": "100", "unit": "g"},
        ]),
    ).json()
    body = client.get(f"/api/v1/kitchen/recipes/{created['id']}/nutrition").json()
    # 既存の5項目（XR が読む形）はそのまま。
    for key in ("kcal", "protein_g", "fat_g", "carb_g", "salt_g"):
        assert key in body
    assert body["source"] == "estimated"
    assert body["coverage"] == pytest.approx(2 / 3, abs=0.01)
    assert body["partial"] is True
    assert [u["name"] for u in body["unresolved"]] == ["ナンプラー"]
    assert body["food_table_available"] is True


def test_nutrition_says_nothing_when_the_table_is_empty(home: Path) -> None:
    client = make_client(home)
    created = client.post(
        "/api/v1/kitchen/recipes", json=_recipe([{"name": "豚ひき肉", "qty": "200", "unit": "g"}])
    ).json()
    body = client.get(f"/api/v1/kitchen/recipes/{created['id']}/nutrition").json()
    assert body["source"] == ""
    assert body["coverage"] is None
    assert body["unresolved"] == []
    assert body["food_table_available"] is False


def test_nutrition_keeps_site_values(home: Path, stocked_foods: None) -> None:
    """出典サイトの値は推定に塗り替えられない（ADR-019 D4）。"""
    client = make_client(home)
    created = client.post(
        "/api/v1/kitchen/recipes", json=_recipe([{"name": "豚ひき肉", "qty": "200", "unit": "g"}])
    ).json()
    client.put(
        f"/api/v1/kitchen/recipes/{created['id']}/meta",
        json={"kcal": 111.0, "protein_g": 1.0, "fat_g": 1.0, "carb_g": 1.0, "salt_g": 1.0,
              "nutrition_source": "site"},
    )
    client.post(f"/api/v1/kitchen/recipes/{created['id']}/rebuild-nutrition")
    body = client.get(f"/api/v1/kitchen/recipes/{created['id']}/nutrition").json()
    assert body["source"] == "site"
    assert body["kcal"] == 111.0
    assert body["partial"] is False


def test_nutrition_404_for_a_missing_recipe(home: Path, stocked_foods: None) -> None:
    client = make_client(home)
    assert client.get("/api/v1/kitchen/recipes/999/nutrition").status_code == 404


# --- 登録・編集が推定の契機になる（ADR-019 D4） ----------------------------------------


def test_registering_a_recipe_estimates_it(home: Path, stocked_foods: None) -> None:
    client = make_client(home)
    created = client.post(
        "/api/v1/kitchen/recipes", json=_recipe([{"name": "豚ひき肉", "qty": "200", "unit": "g"}])
    ).json()
    assert created["meta"]["nutrition_source"] == "estimated"
    assert created["meta"]["kcal"] == pytest.approx(209.0)


def test_editing_the_ingredients_re_estimates(home: Path, stocked_foods: None) -> None:
    client = make_client(home)
    created = client.post(
        "/api/v1/kitchen/recipes", json=_recipe([{"name": "豚ひき肉", "qty": "200", "unit": "g"}])
    ).json()
    updated = client.put(
        f"/api/v1/kitchen/recipes/{created['id']}",
        json=_recipe([{"name": "豚ひき肉", "qty": "400", "unit": "g"}]),
    ).json()
    assert updated["meta"]["kcal"] == pytest.approx(418.0)


# --- 名寄せ（GET/POST /food/aliases・GET /food/search） --------------------------------


def test_food_search_matches_by_substring(home: Path, stocked_foods: None) -> None:
    client = make_client(home)
    items = client.get("/api/v1/kitchen/food/search", params={"q": "ひき肉"}).json()["items"]
    assert [i["food_code"] for i in items] == ["11221"]


def test_food_aliases_lists_unresolved_then_resolves_it(home: Path, stocked_foods: None) -> None:
    client = make_client(home)
    created = client.post(
        "/api/v1/kitchen/recipes",
        json=_recipe([{"name": "合いびき肉", "qty": "200", "unit": "g"}], title="ハンバーグ"),
    ).json()

    listed = client.get("/api/v1/kitchen/food/aliases").json()
    assert listed["unresolved_total"] == 1
    assert listed["unresolved"][0]["names"] == ["合いびき肉"]
    assert listed["aliases"] == []

    res = client.post(
        "/api/v1/kitchen/food/aliases", json={"alias": "合いびき肉", "food_code": "11221"}
    )
    assert res.status_code == 200
    assert res.json()["alias"]["food_name"] == "ぶた ひき肉 生"
    # 名寄せの更新は**その場で再計算の契機になる**（ADR-019 D4）。
    assert res.json()["rebuilt"]["updated"] == 1

    after = client.get(f"/api/v1/kitchen/recipes/{created['id']}/nutrition").json()
    assert after["source"] == "estimated"
    assert after["coverage"] == 1.0
    assert after["unresolved"] == []

    again = client.get("/api/v1/kitchen/food/aliases").json()
    assert again["unresolved_total"] == 0
    assert again["aliases"][0]["alias"] == chef_nutrition.normalize_name("合いびき肉")


def test_food_alias_rejects_an_unknown_food_with_400(home: Path, stocked_foods: None) -> None:
    client = make_client(home)
    res = client.post("/api/v1/kitchen/food/aliases", json={"alias": "なにか", "food_code": "99999"})
    assert res.status_code == 400


def test_food_alias_can_be_removed(home: Path, stocked_foods: None) -> None:
    client = make_client(home)
    client.post("/api/v1/kitchen/food/aliases", json={"alias": "合いびき肉", "food_code": "11221"})
    alias = chef_nutrition.normalize_name("合いびき肉")
    assert client.delete(f"/api/v1/kitchen/food/aliases/{alias}").status_code == 200
    assert client.get("/api/v1/kitchen/food/aliases").json()["aliases"] == []


def test_food_endpoints_are_read_only_safe(home: Path, stocked_foods: None) -> None:
    """読み取り専用（ADR-005）の web では書き込みの口が 403。"""
    client = make_client(home, read_only=True)
    res = client.post("/api/v1/kitchen/food/aliases", json={"alias": "x", "food_code": "11221"})
    assert res.status_code == 403
    assert client.get("/api/v1/kitchen/food/search", params={"q": "ひき肉"}).status_code == 200
