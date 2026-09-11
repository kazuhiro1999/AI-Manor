"""`/api/v1/kitchen/recipes*` `/api/v1/kitchen/cook-sessions*`（ADR-015 D3）の試験。

すべて合成データ（見本の炒飯レシピ＝架空の家庭の記録として扱う）。
`tests/web/test_staff.py` と同じ流儀（`TestClient` を直に叩く）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from manor.web import app as web_app_mod
from manor.web._common import USER_COOKIE_NAME

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "chahan.recipe.json"


def make_client(home: Path, *, read_only: bool = False) -> TestClient:
    return TestClient(web_app_mod.create_app(home, read_only=read_only))


@pytest.fixture
def sample_recipe() -> dict:
    with FIXTURE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _minimal_recipe(**overrides) -> dict:
    base = {
        "title": "試験用の一品",
        "ingredients": [{"name": "水"}],
        "tools": [],
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


# --- recipes CRUD ------------------------------------------------------------------


def test_recipe_add_then_get(conn, home: Path, sample_recipe: dict) -> None:
    client = make_client(home)
    res = client.post("/api/v1/kitchen/recipes", json=sample_recipe)
    assert res.status_code == 200
    body = res.json()
    recipe_id = body["id"]
    assert body["title"] == sample_recipe["title"]
    assert len(body["steps"]) == 9

    got = client.get(f"/api/v1/kitchen/recipes/{recipe_id}").json()
    assert got["id"] == recipe_id
    assert got["title"] == sample_recipe["title"]


def test_recipe_add_rejects_invalid_step_title(conn, home: Path) -> None:
    """`ManorError(code=2)` は web 層で 404 に写る（`web/_common.manor_error_to_http` の
    既存の規則——`error.chef.pantry_not_found` 等の「見つからない」と「語彙外・検算違反」を
    区別せず、どちらも code=2 として同じ経路を通る。この Web 層の慣習は ADR-015 の対象外
    なので変えない）。
    """
    client = make_client(home)
    recipe = _minimal_recipe()
    recipe["steps"][0]["title"] = "あ" * 13
    res = client.post("/api/v1/kitchen/recipes", json=recipe)
    assert res.status_code == 404


def test_recipe_get_missing_is_404(conn, home: Path) -> None:
    client = make_client(home)
    res = client.get("/api/v1/kitchen/recipes/999")
    assert res.status_code == 404


def test_recipe_list_shape(conn, home: Path) -> None:
    """ADR-015 D9追補: 返り値は `{"items":[...]}`（既存の各行の形は保つ）。"""
    client = make_client(home)
    client.post("/api/v1/kitchen/recipes", json=_minimal_recipe(title="一覧確認用"))
    body = client.get("/api/v1/kitchen/recipes").json()
    items = body["items"]
    assert any(r["title"] == "一覧確認用" for r in items)
    row = next(r for r in items if r["title"] == "一覧確認用")
    assert set(row.keys()) >= {
        "id", "title", "hero_image", "total_minutes", "servings", "tags", "favorite",
        "times_cooked", "last_cooked_at", "kcal", "category", "main_ingredient", "cuisine",
        "updated_at",
    }


def test_recipe_list_supports_classification_filters_and_sort(conn, home: Path) -> None:
    client = make_client(home)
    recipe_id = client.post("/api/v1/kitchen/recipes", json=_minimal_recipe(title="絞り込み確認用")).json()["id"]
    client.put(f"/api/v1/kitchen/recipes/{recipe_id}/meta", json={"category": "主菜", "cuisine": "和食"})

    hit = client.get("/api/v1/kitchen/recipes", params={"category": "主菜"}).json()["items"]
    assert recipe_id in [r["id"] for r in hit]

    miss = client.get("/api/v1/kitchen/recipes", params={"category": "デザート"}).json()["items"]
    assert recipe_id not in [r["id"] for r in miss]

    sorted_by_title = client.get("/api/v1/kitchen/recipes", params={"sort": "title"}).json()["items"]
    titles = [r["title"] for r in sorted_by_title]
    assert titles == sorted(titles)


def test_recipe_facets_excludes_archived(conn, home: Path) -> None:
    client = make_client(home)
    kept_id = client.post("/api/v1/kitchen/recipes", json=_minimal_recipe(title="facets残す")).json()["id"]
    gone_id = client.post("/api/v1/kitchen/recipes", json=_minimal_recipe(title="facets畳む")).json()["id"]
    client.put(f"/api/v1/kitchen/recipes/{kept_id}/meta", json={"category": "主菜"})
    client.put(f"/api/v1/kitchen/recipes/{gone_id}/meta", json={"category": "主菜"})
    client.post(f"/api/v1/kitchen/recipes/{gone_id}/archive")

    facets = client.get("/api/v1/kitchen/recipes/facets").json()
    assert set(facets.keys()) == {"category", "main_ingredient", "cuisine", "tags"}
    assert facets["category"] == [{"value": "主菜", "count": 1}]


def test_recipe_update_replaces_body(conn, home: Path) -> None:
    client = make_client(home)
    recipe_id = client.post("/api/v1/kitchen/recipes", json=_minimal_recipe()).json()["id"]
    client.put(f"/api/v1/kitchen/recipes/{recipe_id}/meta", json={"kcal": 300})

    res = client.put(f"/api/v1/kitchen/recipes/{recipe_id}", json=_minimal_recipe(title="改訂"))
    assert res.status_code == 200
    body = res.json()
    assert body["title"] == "改訂"
    assert body["meta"]["kcal"] == 300  # 本体を差し替えても meta は残る


def test_recipe_set_meta_partial_and_manual_mark(conn, home: Path) -> None:
    client = make_client(home)
    recipe_id = client.post("/api/v1/kitchen/recipes", json=_minimal_recipe()).json()["id"]

    res = client.put(f"/api/v1/kitchen/recipes/{recipe_id}/meta", json={"kcal": 400})
    assert res.status_code == 200
    assert res.json()["meta"]["nutrition_source"] == "manual"

    res2 = client.put(f"/api/v1/kitchen/recipes/{recipe_id}/meta", json={"memo": "うちは薄味"})
    body2 = res2.json()
    assert body2["meta"]["memo"] == "うちは薄味"
    assert body2["meta"]["kcal"] == 400  # 渡さなかった欄は据え置き


def test_recipe_archive(conn, home: Path) -> None:
    client = make_client(home)
    recipe_id = client.post("/api/v1/kitchen/recipes", json=_minimal_recipe()).json()["id"]
    res = client.post(f"/api/v1/kitchen/recipes/{recipe_id}/archive")
    assert res.status_code == 200
    listed = client.get("/api/v1/kitchen/recipes").json()["items"]
    assert recipe_id not in [r["id"] for r in listed]


# --- 読み取り専用モード -----------------------------------------------------------------


def test_recipe_writes_forbidden_when_read_only(conn, home: Path) -> None:
    recipe_id = None
    writable_client = make_client(home)
    recipe_id = writable_client.post("/api/v1/kitchen/recipes", json=_minimal_recipe()).json()["id"]

    client = make_client(home, read_only=True)
    assert client.post("/api/v1/kitchen/recipes", json=_minimal_recipe()).status_code == 403
    assert client.put(f"/api/v1/kitchen/recipes/{recipe_id}", json=_minimal_recipe()).status_code == 403
    assert client.put(f"/api/v1/kitchen/recipes/{recipe_id}/meta", json={"favorite": True}).status_code == 403
    assert client.post(f"/api/v1/kitchen/recipes/{recipe_id}/archive").status_code == 403
    assert client.post("/api/v1/kitchen/cook-sessions", json={"recipe_id": recipe_id}).status_code == 403


# --- 表が無い home ----------------------------------------------------------------------


def test_recipes_not_available_when_table_missing(conn, home: Path) -> None:
    """更新前に chef を導入した既存 home の想定——`chef_recipe` が無ければ 404
    （500 の生の traceback にしない。ADR-015 の指示どおり）。
    """
    client = make_client(home)
    conn.execute("DROP TABLE chef_cook_event")
    conn.execute("DROP TABLE chef_cook_session")
    conn.execute("DROP TABLE chef_recipe_meta")
    conn.execute("DROP TABLE chef_recipe")
    conn.commit()

    assert client.get("/api/v1/kitchen/recipes").status_code == 404
    assert client.get("/api/v1/kitchen/recipes/facets").status_code == 404
    assert client.get("/api/v1/kitchen/recipes/1").status_code == 404
    assert client.post("/api/v1/kitchen/recipes", json=_minimal_recipe()).status_code == 404
    assert client.get("/api/v1/kitchen/cook-sessions/current").status_code == 404
    assert client.post("/api/v1/kitchen/cook-sessions", json={"recipe_id": 1}).status_code == 404


# --- cook-sessions -----------------------------------------------------------------


def test_cook_session_flow_increments_times_cooked(conn, home: Path, sample_recipe: dict) -> None:
    client = make_client(home)
    recipe_id = client.post("/api/v1/kitchen/recipes", json=sample_recipe).json()["id"]

    started = client.post("/api/v1/kitchen/cook-sessions", json={"recipe_id": recipe_id}).json()
    session_id = started["id"]
    assert started["current"] == 1

    progress = None
    for _ in range(9):
        res = client.post(f"/api/v1/kitchen/cook-sessions/{session_id}/events", json={"type": "next"})
        assert res.status_code == 200
        progress = res.json()
    assert progress["current"] == 9

    end_res = client.post(f"/api/v1/kitchen/cook-sessions/{session_id}/end")
    assert end_res.status_code == 200

    got = client.get(f"/api/v1/kitchen/recipes/{recipe_id}").json()
    assert got["meta"]["times_cooked"] == 1


def test_cook_session_current_is_per_viewing_user(conn, home: Path) -> None:
    from manor import user as user_mod

    user_mod.add(conn, "同居人", user_id="u2")
    conn.commit()

    client_master = make_client(home)
    client_u2 = make_client(home)
    client_u2.cookies.set(USER_COOKIE_NAME, "u2")

    recipe1 = client_master.post("/api/v1/kitchen/recipes", json=_minimal_recipe(title="master の一品")).json()["id"]
    recipe2 = client_master.post("/api/v1/kitchen/recipes", json=_minimal_recipe(title="u2 の一品")).json()["id"]

    session_master = client_master.post("/api/v1/kitchen/cook-sessions", json={"recipe_id": recipe1}).json()
    session_u2 = client_u2.post("/api/v1/kitchen/cook-sessions", json={"recipe_id": recipe2}).json()

    current_master = client_master.get("/api/v1/kitchen/cook-sessions/current").json()
    current_u2 = client_u2.get("/api/v1/kitchen/cook-sessions/current").json()

    assert current_master["id"] == session_master["id"]
    assert current_u2["id"] == session_u2["id"]
    assert current_master["id"] != current_u2["id"]


def test_cook_session_current_none_shape_when_no_session(conn, home: Path) -> None:
    client = make_client(home)
    body = client.get("/api/v1/kitchen/cook-sessions/current").json()
    assert body["id"] is None
