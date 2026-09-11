"""`/api/v1/kitchen/recipes/import` `/api/v1/kitchen/recipes/{id}/estimate-nutrition`
（ADR-015 R2）の試験。`tests/web/test_kitchen_recipes.py` と同じ流儀（`TestClient` を
直に叩く）。**`claude -p` は呼ばない**——`manor.staff.chef.recipe_import` の関数を差し替える
（`tests/staff/test_chef_recipe_import.py` が subprocess レベルで検算済みなので、
ここでは web 層の配線・エラーの写り先だけを見る）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from manor.errors import ManorError
from manor.staff.chef import recipe_import as recipe_import_mod
from manor.web import app as web_app_mod


def make_client(home: Path, *, read_only: bool = False) -> TestClient:
    return TestClient(web_app_mod.create_app(home, read_only=read_only))


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


def _draft_recipe() -> dict:
    recipe = _minimal_recipe(title="取り込み品")
    recipe["source_url"] = "https://example.com/recipe/1"
    recipe["source_site"] = "example.com"
    recipe["hero_image"] = "https://example.com/hero.jpg"
    return recipe


# --- import（保存しない） --------------------------------------------------------------


def test_import_returns_draft_without_saving(conn, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        recipe_import_mod, "import_from_url",
        lambda url, **kw: {"ok": True, "recipe": _draft_recipe(), "warnings": [], "reason": ""},
    )
    client = make_client(home)

    res = client.post("/api/v1/kitchen/recipes/import", json={"url": "https://example.com/recipe/1"})

    assert res.status_code == 200
    body = res.json()
    assert body["recipe"]["title"] == "取り込み品"
    assert body["warnings"] == []
    # 保存していない(一覧に出てこない)。
    assert client.get("/api/v1/kitchen/recipes").json() == {"items": []}


def test_import_top_level_meta_matches_recipe_meta(
    conn, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """画面は `result.meta` を読む（コーディネーターの指示・2026-09-12）——
    `recipe["meta"]` と同じ辞書がトップレベルにも返る（`recipe["meta"]` 自体は
    CLI・`--save` 向けに残す）。
    """
    recipe = _draft_recipe()
    recipe["meta"] = {
        "category": "主菜", "main_ingredient": "肉", "cuisine": "和食", "tags": ["主菜"],
        "kcal": 685.0, "protein_g": 20.5, "fat_g": 35.2, "carb_g": 66.5, "salt_g": 2.8,
        "nutrition_source": "site",
    }
    monkeypatch.setattr(
        recipe_import_mod, "import_from_url",
        lambda url, **kw: {"ok": True, "recipe": recipe, "warnings": [], "reason": ""},
    )
    client = make_client(home)

    res = client.post("/api/v1/kitchen/recipes/import", json={"url": "https://example.com/recipe/1"})

    assert res.status_code == 200
    body = res.json()
    assert body["meta"] == body["recipe"]["meta"]
    assert body["meta"]["nutrition_source"] == "site"


def test_import_defaults_to_auto_mode(conn, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen_modes: list[str] = []

    def fake_import(url, *, mode="auto", **kw):
        seen_modes.append(mode)
        return {"ok": True, "recipe": _draft_recipe(), "method": "generic", "warnings": [], "reason": ""}

    monkeypatch.setattr(recipe_import_mod, "import_from_url", fake_import)
    client = make_client(home)

    res = client.post("/api/v1/kitchen/recipes/import", json={"url": "https://example.com/recipe/1"})

    assert res.status_code == 200
    assert seen_modes == ["auto"]
    assert res.json()["method"] == "generic"


def test_import_can_request_claude_mode(conn, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen_modes: list[str] = []

    def fake_import(url, *, mode="auto", **kw):
        seen_modes.append(mode)
        return {"ok": True, "recipe": _draft_recipe(), "method": "claude", "warnings": [], "reason": ""}

    monkeypatch.setattr(recipe_import_mod, "import_from_url", fake_import)
    client = make_client(home)

    res = client.post(
        "/api/v1/kitchen/recipes/import", json={"url": "https://example.com/recipe/1", "mode": "claude"}
    )

    assert res.status_code == 200
    assert seen_modes == ["claude"]
    assert res.json()["method"] == "claude"


def test_import_rejects_unknown_mode_as_422(conn, home: Path) -> None:
    client = make_client(home)
    res = client.post(
        "/api/v1/kitchen/recipes/import", json={"url": "https://example.com/recipe/1", "mode": "magic"}
    )
    assert res.status_code == 422


def test_import_reports_warnings_from_overflowing_steps(
    conn, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipe = _draft_recipe()
    monkeypatch.setattr(
        recipe_import_mod, "import_from_url",
        lambda url, **kw: {
            "ok": True, "recipe": recipe,
            "warnings": ["steps[0].title は12文字以内にしてください（20文字）: 'x'"],
            "reason": "",
        },
    )
    client = make_client(home)

    res = client.post("/api/v1/kitchen/recipes/import", json={"url": "https://example.com/recipe/1"})

    assert res.status_code == 200
    assert len(res.json()["warnings"]) == 1


def test_import_rejects_blocked_url_as_404(conn, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`validate_url` の違反（`ManorError(code=2)`）は既存の慣習どおり 404 に写る
    （`web/_common.manor_error_to_http`。`test_kitchen_recipes.py` の同種の試験と同じ扱い）。
    """

    def boom(url, **kw):
        raise ManorError("この URL は取り込めません", code=2, key="error.chef.recipe_import_url_blocked")

    monkeypatch.setattr(recipe_import_mod, "import_from_url", boom)
    client = make_client(home)

    res = client.post("/api/v1/kitchen/recipes/import", json={"url": "http://127.0.0.1/x"})

    assert res.status_code == 404


def test_import_claude_failure_is_502(conn, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        recipe_import_mod, "import_from_url",
        lambda url, **kw: {"ok": False, "recipe": None, "warnings": [], "reason": "claude が見つかりません"},
    )
    client = make_client(home)

    res = client.post("/api/v1/kitchen/recipes/import", json={"url": "https://example.com/recipe/1"})

    assert res.status_code == 502
    assert "claude" in res.json()["detail"]


# --- estimate-nutrition（保存する。require_writable） ------------------------------------


def test_estimate_nutrition_saves_meta(conn, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(home)
    recipe_id = client.post("/api/v1/kitchen/recipes", json=_minimal_recipe()).json()["id"]

    monkeypatch.setattr(
        recipe_import_mod, "estimate_nutrition",
        lambda recipe, **kw: {
            "ok": True,
            "nutrition": {"kcal": 500.0, "protein_g": 20.0, "fat_g": 15.0, "carb_g": 60.0, "salt_g": 2.5},
            "reason": "",
        },
    )

    res = client.post(f"/api/v1/kitchen/recipes/{recipe_id}/estimate-nutrition")

    assert res.status_code == 200
    meta = res.json()
    assert meta["kcal"] == 500.0
    assert meta["nutrition_source"] == "estimated"

    got = client.get(f"/api/v1/kitchen/recipes/{recipe_id}").json()
    assert got["meta"]["nutrition_source"] == "estimated"
    assert got["meta"]["kcal"] == 500.0


def test_estimate_nutrition_claude_failure_is_502(
    conn, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = make_client(home)
    recipe_id = client.post("/api/v1/kitchen/recipes", json=_minimal_recipe()).json()["id"]

    monkeypatch.setattr(
        recipe_import_mod, "estimate_nutrition",
        lambda recipe, **kw: {"ok": False, "nutrition": None, "reason": "claude が見つかりません"},
    )

    res = client.post(f"/api/v1/kitchen/recipes/{recipe_id}/estimate-nutrition")

    assert res.status_code == 502
    assert "claude" in res.json()["detail"]

    # 失敗したので meta は変わっていない(estimated に上書きされていない)。
    got = client.get(f"/api/v1/kitchen/recipes/{recipe_id}").json()
    assert got["meta"]["nutrition_source"] == ""


def test_estimate_nutrition_missing_recipe_is_404(conn, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(home)
    res = client.post("/api/v1/kitchen/recipes/999/estimate-nutrition")
    assert res.status_code == 404


def test_estimate_nutrition_requires_writable(conn, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(home, read_only=True)
    recipe_id_client = make_client(home)
    recipe_id = recipe_id_client.post("/api/v1/kitchen/recipes", json=_minimal_recipe()).json()["id"]

    res = client.post(f"/api/v1/kitchen/recipes/{recipe_id}/estimate-nutrition")
    assert res.status_code == 403


# --- refine（D7-2。保存しない） ------------------------------------------------------------


def test_refine_returns_tightened_draft(conn, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    refined = _draft_recipe()
    monkeypatch.setattr(
        recipe_import_mod, "refine_with_claude",
        lambda recipe, **kw: {"ok": True, "recipe": refined, "warnings": [], "reason": ""},
    )
    client = make_client(home)

    res = client.post("/api/v1/kitchen/recipes/refine", json={"recipe": _draft_recipe()})

    assert res.status_code == 200
    body = res.json()
    assert body["method"] == "claude"
    assert body["recipe"]["title"] == "取り込み品"
    # 保存していない(一覧に出てこない)。
    assert client.get("/api/v1/kitchen/recipes").json() == {"items": []}


def test_refine_top_level_meta_matches_recipe_meta(
    conn, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`import` と同じ約束（コーディネーターの指示・2026-09-12）: `refine` も
    トップレベルの `meta` を `recipe["meta"]` と同じ辞書で返す。
    """
    refined = _draft_recipe()
    refined["meta"] = {"category": "副菜", "main_ingredient": "", "cuisine": "", "tags": []}
    monkeypatch.setattr(
        recipe_import_mod, "refine_with_claude",
        lambda recipe, **kw: {"ok": True, "recipe": refined, "warnings": [], "reason": ""},
    )
    client = make_client(home)

    res = client.post("/api/v1/kitchen/recipes/refine", json={"recipe": _draft_recipe()})

    assert res.status_code == 200
    body = res.json()
    assert body["meta"] == body["recipe"]["meta"]


def test_refine_claude_missing_is_502(conn, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        recipe_import_mod, "refine_with_claude",
        lambda recipe, **kw: {"ok": False, "recipe": None, "warnings": [], "reason": "claude が見つかりません"},
    )
    client = make_client(home)

    res = client.post("/api/v1/kitchen/recipes/refine", json={"recipe": _draft_recipe()})

    assert res.status_code == 502
    assert "claude" in res.json()["detail"]
