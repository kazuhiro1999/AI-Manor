"""`/api/v1/kitchen/recipes/{id}/companions*`・`/api/v1/kitchen/companions/*`（ADR-021 D5）の試験。

`test_kitchen_menu.py` と同じ流儀（`TestClient` を直に叩く）。成分表は同梱 CSV を取り込む
（定番の栄養値は材料から推定するので、表が無いと定番が1品も出ない）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from manor.staff.chef import nutrition, recipes as chef_recipes
from manor.web import app as web_app_mod


def make_client(home: Path, *, read_only: bool = False) -> TestClient:
    return TestClient(web_app_mod.create_app(home, read_only=read_only))


def _add(conn, title: str, *, category: str, ingredients, steps=("切る。",)) -> int:
    recipe_id = chef_recipes.add(
        conn,
        {
            "title": title,
            "servings": 2,
            "total_minutes": 20,
            "ingredients": [{"name": n, "qty": q, "unit": u} for n, q, u in ingredients],
            "phases": [{"id": "cook", "title": "作る"}],
            "steps": [
                {"index": i, "phase": "cook", "title": "作る", "instruction": text, "completion": "manual"}
                for i, text in enumerate(steps, start=1)
            ],
        },
    )
    chef_recipes.set_meta(conn, recipe_id, category=category)
    return recipe_id


@pytest.fixture
def stocked(conn) -> dict[str, int]:
    nutrition.import_food_table(conn)
    nutrition.seed_aliases(conn)
    ids = {
        "main": _add(
            conn, "塩唐揚げ", category="主菜",
            ingredients=[("鶏もも肉", "300", "g"), ("塩", "1", "小さじ"), ("片栗粉", "2", "大さじ")],
            steps=("鶏肉に塩をもみ込む。", "片栗粉をまぶして油で揚げる。"),
        ),
        "side": _add(
            conn, "塩昆布の無限キャベツ", category="副菜",
            ingredients=[("キャベツ", "200", "g"), ("塩昆布", "5", "g"), ("ごま油", "1/2", "大さじ")],
            steps=("キャベツを切る。", "ポリ袋で全部もむ。"),
        ),
    }
    nutrition.rebuild(conn)
    conn.commit()
    return ids


def test_companions_for_a_fried_main(home: Path, stocked: dict[str, int]) -> None:
    client = make_client(home)
    res = client.get(f"/api/v1/kitchen/recipes/{stocked['main']}/companions")
    assert res.status_code == 200
    body = res.json()
    assert body["eligible"] is True
    assert body["main"]["kind"] == "揚げ物"
    assert "vitamin_c_mg" in body["main"]["under"]
    assert 1 <= len(body["items"]) <= 3
    top = body["items"][0]
    assert set(top) >= {"key", "source", "title", "kind", "heat", "reasons", "nutrition", "micro"}
    assert all(set(r) == {"code", "params"} for r in top["reasons"])
    # うちのキャベツ（火を使わない・揚げ物に合う・ビタミンC）が並ぶ
    assert any(i["source"] == "recipe" and i["recipe_id"] == stocked["side"] for i in body["items"])
    # 揚げ物に合わせたいのは生・和え物の類（炒め物・揚げ物は上位に来ない）
    assert all(i["kind"] not in ("炒め物", "揚げ物") for i in body["items"])
    assert body["catalog_size"] >= 30


def test_companions_for_a_side_dish_are_not_offered(home: Path, stocked: dict[str, int]) -> None:
    body = make_client(home).get(f"/api/v1/kitchen/recipes/{stocked['side']}/companions").json()
    assert body["eligible"] is False and body["items"] == []


def test_companions_unknown_recipe_is_404(home: Path, stocked: dict[str, int]) -> None:
    assert make_client(home).get("/api/v1/kitchen/recipes/9999/companions").status_code == 404


def test_catalog_detail_and_404(home: Path, stocked: dict[str, int]) -> None:
    client = make_client(home)
    items = client.get(f"/api/v1/kitchen/recipes/{stocked['main']}/companions").json()["items"]
    catalog = [i for i in items if i["source"] == "catalog"]
    assert catalog, "定番が1品も並ばない"
    key = catalog[0]["catalog_key"]
    detail = client.get(f"/api/v1/kitchen/companions/{key}").json()
    assert detail["title"] == catalog[0]["title"]
    assert detail["ingredients"] and detail["steps"]
    assert client.get("/api/v1/kitchen/companions/no_such_dish").status_code == 404


def test_plan_together_writes_both_to_meal_history(conn, home: Path, stocked: dict[str, int]) -> None:
    client = make_client(home)
    res = client.post(
        f"/api/v1/kitchen/recipes/{stocked['main']}/companions/plan",
        json={"date": "2026-09-25", "companion_recipe_id": stocked["side"]},
    )
    assert res.status_code == 200, res.text
    dishes = [r["dish"] for r in conn.execute("SELECT dish FROM chef_meal WHERE planned = 1 ORDER BY id")]
    assert dishes == ["塩唐揚げ", "塩昆布の無限キャベツ"]


def test_plan_together_with_a_catalog_dish_does_not_add_a_recipe(conn, home: Path, stocked: dict[str, int]) -> None:
    client = make_client(home)
    before = conn.execute("SELECT COUNT(*) AS n FROM chef_recipe").fetchone()["n"]
    key = next(i["catalog_key"] for i in client.get(f"/api/v1/kitchen/recipes/{stocked['main']}/companions").json()["items"] if i["source"] == "catalog")
    res = client.post(
        f"/api/v1/kitchen/recipes/{stocked['main']}/companions/plan",
        json={"date": "2026-09-25", "catalog_key": key},
    )
    assert res.status_code == 200, res.text
    assert conn.execute("SELECT COUNT(*) AS n FROM chef_meal WHERE planned = 1").fetchone()["n"] == 2
    assert conn.execute("SELECT COUNT(*) AS n FROM chef_recipe").fetchone()["n"] == before


def test_plan_together_needs_exactly_one_companion(home: Path, stocked: dict[str, int]) -> None:
    client = make_client(home)
    url = f"/api/v1/kitchen/recipes/{stocked['main']}/companions/plan"
    assert client.post(url, json={"date": "2026-09-25"}).status_code == 400
    assert client.post(url, json={"date": "2026-09-25", "companion_recipe_id": stocked["side"], "catalog_key": "x"}).status_code == 400


def test_adopt_promotes_a_catalog_dish_once(conn, home: Path, stocked: dict[str, int]) -> None:
    client = make_client(home)
    key = next(i["catalog_key"] for i in client.get(f"/api/v1/kitchen/recipes/{stocked['main']}/companions").json()["items"] if i["source"] == "catalog")
    first = client.post(f"/api/v1/kitchen/companions/{key}/adopt").json()
    assert first["created"] is True
    recipe = client.get(f"/api/v1/kitchen/recipes/{first['recipe_id']}").json()
    assert recipe["meta"]["category"] in ("副菜", "汁物")
    assert recipe["meta"]["nutrition_source"] == "estimated"
    second = client.post(f"/api/v1/kitchen/companions/{key}/adopt").json()
    assert second == {"recipe_id": first["recipe_id"], "created": False}
    # 昇格した後は、定番ではなくうちのレシピとして並ぶ（同じ題名の定番は出さない）
    items = client.get(f"/api/v1/kitchen/recipes/{stocked['main']}/companions").json()["items"]
    assert not any(i["source"] == "catalog" and i["catalog_key"] == key for i in items)


def test_writes_are_refused_when_read_only(home: Path, stocked: dict[str, int]) -> None:
    client = make_client(home, read_only=True)
    res = client.post(
        f"/api/v1/kitchen/recipes/{stocked['main']}/companions/plan",
        json={"date": "2026-09-25", "companion_recipe_id": stocked["side"]},
    )
    assert res.status_code in (403, 405, 409)
