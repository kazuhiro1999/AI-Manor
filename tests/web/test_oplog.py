"""操作ログ（ADR-024。`web/oplog.py`）の試験。

Web の書き込み・来訪・背景の仕事が `home/logs/web-YYYY-MM.log` に1行ずつ残ること、
残さないもの（本文・生の IP・ふだんの GET）が残らないことを固定する。本物の claude は呼ばない。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from manor import cli as cli_mod
from manor.staff.chef import food_resolve, nutrition
from manor.web import app as web_app_mod
from manor.web import oplog

LOOPBACK = ("127.0.0.1", 50000)

RECIPE = {
    "title": "ほうれん草のおひたし",
    "servings": 2,
    "ingredients": [{"name": "ほうれん草", "qty": "1", "unit": "袋"}, {"name": "醤油", "qty": "10", "unit": "g"}],
    "phases": [{"id": "p", "title": "作る"}],
    "steps": [{"index": 1, "phase": "p", "title": "茹でる", "instruction": "ひみつの手順", "completion": "manual"}],
}


def make_client(home: Path, client=LOOPBACK) -> TestClient:
    return TestClient(web_app_mod.create_app(home), client=client)


def lines(home: Path) -> list[str]:
    return oplog.read_lines(home, tail=None)


def test_write_request_is_one_line_with_the_tables_it_touched(home: Path) -> None:
    client = make_client(home)
    assert client.post("/api/v1/kitchen/recipes", json=RECIPE).status_code == 200
    posted = [ln for ln in lines(home) if "POST /api/v1/kitchen/recipes " in ln]
    assert len(posted) == 1
    line = posted[0]
    assert "user=master(" in line and "ip=local" in line and "zone=loopback" in line
    assert " 200 " in line and "+chef_recipe" in line
    # 本文（レシピの中身）は残さない
    assert "ひみつの手順" not in "\n".join(lines(home))


def test_plain_gets_are_not_logged_but_the_first_one_is_a_visit(home: Path) -> None:
    client = make_client(home)
    client.get("/api/v1/users")
    client.get("/api/v1/users")
    client.get("/api/v1/kitchen/recipes")
    got = lines(home)
    assert len(got) == 1 and got[0].endswith(" visit")


def test_visit_again_after_30_minutes(home: Path) -> None:
    log = oplog.for_home(home)
    assert log.is_visit("user:", "#abc", now=1000.0) is True
    assert log.is_visit("user:", "#abc", now=1000.0 + 60) is False
    assert log.is_visit("user:", "#abc", now=1060.0 + oplog.VISIT_GAP_SECONDS) is True
    assert log.is_visit("user:u2", "#abc", now=1061.0 + oplog.VISIT_GAP_SECONDS) is True  # 別の人


def test_switched_user_is_who_did_it(home: Path) -> None:
    client = make_client(home)
    uid = client.post("/api/v1/users", json={"name": "同居人", "callname": "はなこ"}).json()["id"]
    assert client.post("/api/v1/users/switch", json={"id": uid}).status_code == 200
    client.post("/api/v1/kitchen/recipes", json=RECIPE)
    line = [ln for ln in lines(home) if "POST /api/v1/kitchen/recipes " in ln][0]
    assert f"user={uid}(はなこ)" in line
    assert oplog.read_lines(home, user=uid, tail=None) == [ln for ln in lines(home) if f"user={uid}(" in ln]


def test_ip_is_hashed_not_kept(home: Path) -> None:
    client = make_client(home, client=("127.0.0.1", 50000))
    client.post("/api/v1/users", json={"name": "A"}, headers={"x-forwarded-for": "100.101.102.103"})
    text = "\n".join(lines(home))
    assert "100.101.102.103" not in text
    assert "zone=tailnet" in text
    mark = [ln for ln in lines(home) if "POST /api/v1/users " in ln][0].split("ip=")[1].split(" ")[0]
    assert mark.startswith("#") and len(mark) == 7
    # 同じ IP は同じ印
    client.post("/api/v1/users", json={"name": "B"}, headers={"x-forwarded-for": "100.101.102.103"})
    marks = {ln.split("ip=")[1].split(" ")[0] for ln in lines(home) if "POST /api/v1/users " in ln}
    assert marks == {mark}


def test_background_work_is_noted_and_reports_when_done(conn, home: Path, monkeypatch) -> None:
    nutrition.import_food_table(conn)
    nutrition.seed_aliases(conn)
    conn.commit()
    client = make_client(home)
    client.post("/api/v1/kitchen/recipes", json=RECIPE)

    def fake_claude(prompt: str, settings) -> dict:
        return {"ok": True, "cost": 0.0123, "reason": "", "data": {"answers": [
            {"id": "u1", "grams": 200, "source_url": "https://example.com/spinach", "note": "1袋200g"},
        ]}}

    monkeypatch.setattr(food_resolve, "call_claude", fake_claude)
    client.post("/api/v1/kitchen/food/resolve")
    for _ in range(100):
        if not client.get("/api/v1/kitchen/food/resolve").json()["running"]:
            break
        time.sleep(0.05)
    got = lines(home)
    assert any("POST /api/v1/kitchen/food/resolve " in ln and "bg:food_resolve" in ln for ln in got)
    done = [ln for ln in got if " bg food_resolve " in ln]
    assert done and "resolved=1" in done[0] and "cost=$0.0123" in done[0]


def test_note_and_trace_only_inside_a_request() -> None:
    oplog.note("bg:x")  # 要求の外では何も起きない
    items, token = oplog.begin()
    try:
        oplog.note("bg:x")
        oplog.note("bg:x")
        oplog._trace("INSERT OR REPLACE INTO chef_food_unit (a) VALUES (1)")
        oplog._trace("UPDATE chef_recipe SET title = 'x'")
        oplog._trace("DELETE FROM chef_pantry WHERE id = 1")
        oplog._trace("SELECT * FROM chef_recipe")
    finally:
        oplog.end(token)
    assert items == ["bg:x", "+chef_food_unit", "~chef_recipe", "-chef_pantry"]


def test_old_months_are_pruned(home: Path) -> None:
    folder = oplog.log_dir(home)
    folder.mkdir(parents=True)
    for month in ("2025-09", "2025-10", "2026-08"):
        oplog.log_path(home, month).write_text("x\n", encoding="utf-8")
    log = oplog.OpLog(home)
    log.write("hello", at="2026-09-25T10:00:00")
    assert oplog.months(home) == ["2025-10", "2026-08", "2026-09"]


def test_cli_shows_the_tail(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = oplog.for_home(home)
    for i in range(5):
        log.write(f"user=master() ip=local zone=loopback POST /api/v1/x{i} 200 1ms")
    assert cli_mod.main(["web", "log", "-n", "2"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 2 and out[-1].endswith("POST /api/v1/x4 200 1ms")
    assert cli_mod.main(["web", "log", "--grep", "/x1 "]) == 0
    assert "/api/v1/x1 " in capsys.readouterr().out
