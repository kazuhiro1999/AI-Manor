"""`/api/v1/kitchen/food/units`・`/api/v1/kitchen/food/resolve`（ADR-022）の試験。

本物の claude は呼ばない——`food_resolve.call_claude` を差し替える。自動で走る経路は
`tests/conftest.py` が `MANOR_CLAUDE_RESOLVE=off` で止めている（ここでは止まっていることも確かめる）。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from manor.staff.chef import food_resolve, nutrition
from manor.web import app as web_app_mod
from manor.web.api_v1 import kitchen_resolve


def make_client(home: Path) -> TestClient:
    return TestClient(web_app_mod.create_app(home))


RECIPE = {
    "title": "ほうれん草のおひたし",
    "servings": 2,
    "ingredients": [{"name": "ほうれん草", "qty": "1", "unit": "袋"}, {"name": "醤油", "qty": "10", "unit": "g"}],
    "phases": [{"id": "p", "title": "作る"}],
    "steps": [{"index": 1, "phase": "p", "title": "茹でる", "instruction": "茹でる。", "completion": "manual"}],
}


@pytest.fixture
def stocked(conn, home: Path) -> int:
    nutrition.import_food_table(conn)
    nutrition.seed_aliases(conn)
    conn.commit()
    res = make_client(home).post("/api/v1/kitchen/recipes", json=RECIPE)
    assert res.status_code == 200, res.text
    return int(res.json()["id"])


def _wait(client: TestClient) -> dict:
    for _ in range(100):
        status = client.get("/api/v1/kitchen/food/resolve").json()
        if not status["running"]:
            return status
        time.sleep(0.05)
    raise AssertionError("調べ係が終わらない")


def test_registering_a_recipe_does_not_start_claude_when_switched_off(home: Path, stocked: int) -> None:
    status = make_client(home).get("/api/v1/kitchen/food/resolve").json()
    assert status == {"running": False, "last": None}


def test_unresolved_bag_is_shown_with_its_unit(home: Path, stocked: int) -> None:
    body = make_client(home).get("/api/v1/kitchen/food/aliases").json()
    spinach = next(i for i in body["unresolved"] if i["normalized"] == "ほうれん草")
    assert spinach["reason"] == "no_piece" and spinach["units"] == ["袋"]
    assert body["units"] == [] and body["resolve"]["running"] is False


def test_manual_unit_resolves_and_can_be_removed(home: Path, stocked: int) -> None:
    client = make_client(home)
    res = client.post("/api/v1/kitchen/food/units", json={"name": "ほうれん草", "unit": "袋", "grams": 200})
    assert res.status_code == 200, res.text
    body = client.get("/api/v1/kitchen/food/aliases").json()
    assert not any(i["normalized"] == "ほうれん草" for i in body["unresolved"])
    assert body["units"][0]["confidence"] == "manual"
    assert client.get(f"/api/v1/kitchen/recipes/{stocked}/nutrition").json()["coverage"] == 1.0

    res = client.delete("/api/v1/kitchen/food/units", params={"name": "ほうれん草", "unit": "袋"})
    assert res.status_code == 200
    body = client.get("/api/v1/kitchen/food/aliases").json()
    assert any(i["normalized"] == "ほうれん草" for i in body["unresolved"])


def test_bad_unit_is_400(home: Path, stocked: int) -> None:
    res = make_client(home).post("/api/v1/kitchen/food/units", json={"name": "ほうれん草", "unit": "袋", "grams": 0})
    assert res.status_code == 400


def test_resolve_runs_in_the_background_with_one_claude_call(home: Path, stocked: int, monkeypatch) -> None:
    calls: list[str] = []

    def fake_claude(prompt: str, settings) -> dict:
        calls.append(prompt)
        return {"ok": True, "data": {"answers": [
            {"id": "u1", "grams": 200, "source_url": "https://example.com/spinach", "note": "1袋200g"},
        ]}, "reason": ""}

    monkeypatch.setattr(food_resolve, "call_claude", fake_claude)
    client = make_client(home)
    started = client.post("/api/v1/kitchen/food/resolve").json()
    assert "running" in started
    status = _wait(client)
    assert len(calls) == 1
    assert status["last"]["resolved"] == 1 and status["last"]["failed"] is False
    units = client.get("/api/v1/kitchen/food/aliases").json()["units"]
    assert units[0]["source_url"] == "https://example.com/spinach" and units[0]["confidence"] == "llm"


def test_auto_switch_reads_the_environment(monkeypatch) -> None:
    monkeypatch.setenv(kitchen_resolve.ENV_SWITCH, "off")
    assert kitchen_resolve.auto_enabled() is False
    monkeypatch.setenv(kitchen_resolve.ENV_SWITCH, "")
    assert kitchen_resolve.auto_enabled() is True  # lexicon の既定は auto = true
