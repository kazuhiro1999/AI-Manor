"""名寄せ・換算の未解決を Claude にまとめて調べてもらう（ADR-022）の試験。

本物の claude は呼ばない——`resolve(runner=…)` に偽の返事を渡す。成分表も合成の数行。
主人の実例（「ほうれん草 1袋」が換算できず未解決に入った。2026-09-25）を表にして固定する。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any

import pytest

from manor.staff.chef import food_resolve, nutrition, recipes

NOW = datetime(2026, 9, 25, 15, 0, 0)


def _food(code: str, name: str, **values) -> tuple:
    row = {"kcal": 0.0, "protein_g": 0.0, "fat_g": 0.0, "carb_g": 0.0, "salt_g": 0.0, "refuse_pct": 0.0}
    row.update(values)
    return (code, code[:2], name, row["kcal"], row["protein_g"], row["fat_g"], row["carb_g"], row["salt_g"], row["refuse_pct"])


@pytest.fixture
def stocked(conn: sqlite3.Connection) -> dict[str, int]:
    for f in (
        _food("06267", "ほうれんそう 葉 通年平均 生", kcal=18.0, refuse_pct=10.0),
        _food("17007", "こいくちしょうゆ", kcal=76.0, salt_g=14.5),
        _food("10091", "かつお かつお節", kcal=332.0, protein_g=77.1),
        _food("10092", "かつお 削り節", kcal=327.0, protein_g=75.7),
    ):
        conn.execute(
            "INSERT INTO chef_food (food_code, food_group, name, kcal, protein_g, fat_g, carb_g, salt_g,"
            " refuse_pct, per, source_version, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '100g', 't', 't')",
            f,
        )
    ids = {
        "ohitashi": recipes.add(
            conn,
            {
                "title": "ほうれん草のおひたし",
                "servings": 2,
                "ingredients": [
                    {"name": "ほうれん草", "qty": "1", "unit": "袋"},
                    {"name": "醤油", "qty": "10", "unit": "g"},
                    {"name": "花かつお", "qty": "3", "unit": "g"},
                ],
                "phases": [{"id": "p", "title": "作る"}],
                "steps": [{"index": 1, "phase": "p", "title": "茹でる", "instruction": "茹でる。", "completion": "manual"}],
            },
        )
    }
    conn.commit()
    return ids


def _fake(answers: list[dict[str, Any]] | None, *, ok: bool = True, calls: list | None = None):
    def runner(prompt: str, settings) -> dict[str, Any]:
        if calls is not None:
            calls.append(prompt)
        if not ok:
            return {"ok": False, "data": None, "reason": "claude_timeout"}
        return {"ok": True, "data": {"answers": answers or []}, "reason": ""}

    return runner


def _ids(conn) -> dict[str, str]:
    """pending の各件に振られる id（プロンプトの並び順と同じ）。"""
    out = {}
    for i, item in enumerate(food_resolve.pending(conn, now=NOW), start=1):
        out[item["key"]] = f"{'u' if item['kind'] == 'unit' else 'f'}{i}"
    return out


def test_pending_lists_the_bag_of_spinach_and_the_unknown_food(conn, stocked) -> None:
    items = {i["key"]: i for i in food_resolve.pending(conn, now=NOW)}
    assert "unit|ほうれん草|袋" in items
    assert items["unit|ほうれん草|袋"]["example"] == "ほうれん草 1袋"
    foods = [i for i in items.values() if i["kind"] == "food"]
    assert [i["name"] for i in foods] == ["花かつお"]


def test_resolve_writes_the_unit_with_its_source_and_rebuilds(conn, stocked) -> None:
    ids = _ids(conn)
    runner = _fake([
        {"id": ids["unit|ほうれん草|袋"], "grams": 200, "source_url": "https://example.com/spinach", "note": "1袋200g前後"},
    ])
    result = food_resolve.resolve(conn, runner=runner, now=NOW)
    assert result["asked"] == 2 and result["resolved"] == 1 and result["unresolved"] == 1
    units = food_resolve.list_units(conn)
    assert units == [
        {**units[0], "name": "ほうれん草", "unit": "袋", "grams": 200.0, "confidence": "llm",
         "source_url": "https://example.com/spinach"}
    ]
    est = nutrition.estimate_for_recipe(conn, stocked["ohitashi"])
    assert not any(u["reason"] == nutrition.REASON_NO_PIECE for u in est.unresolved)


def test_food_answer_must_be_one_of_the_candidates(conn, stocked) -> None:
    calls: list[str] = []
    ids = _ids(conn)
    fid = ids["food|花かつお|"]
    # 候補に無い番号は捨てる（成分表の番号を作らせない）
    food_resolve.resolve(conn, runner=_fake([{"id": fid, "food_code": "99999"}], calls=calls), now=NOW)
    assert nutrition.alias_map(conn).get("花かつお") is None
    assert "10092" in calls[0] or "10091" in calls[0]  # 候補はプロンプトに載っている


def test_food_answer_from_candidates_becomes_an_llm_alias(conn, stocked) -> None:
    ids = _ids(conn)
    fid = ids["food|花かつお|"]
    candidates = food_resolve.candidate_foods(conn, "花かつお", 20)
    # 名前そのものでは当たらないが、名前の一部（かつお）で削り節まで拾える
    assert {"10091", "10092"} <= {str(c["food_code"]) for c in candidates}
    code = "10092"
    food_resolve.resolve(conn, runner=_fake([{"id": fid, "food_code": code}]), now=NOW)
    row = conn.execute("SELECT food_code, confidence FROM chef_food_alias WHERE alias = '花かつお'").fetchone()
    assert (row["food_code"], row["confidence"]) == (code, "llm")


def test_out_of_range_grams_are_dropped_and_not_asked_again_for_a_while(conn, stocked) -> None:
    ids = _ids(conn)
    food_resolve.resolve(conn, runner=_fake([{"id": ids["unit|ほうれん草|袋"], "grams": 99999}]), now=NOW)
    assert food_resolve.list_units(conn) == []
    # 分からなかったものは retry_days の間は聞き直さない
    assert "unit|ほうれん草|袋" not in {i["key"] for i in food_resolve.pending(conn, now=NOW + timedelta(days=1))}
    assert "unit|ほうれん草|袋" in {i["key"] for i in food_resolve.pending(conn, now=NOW + timedelta(days=8))}


def test_claude_failure_is_retried_next_time(conn, stocked) -> None:
    result = food_resolve.resolve(conn, runner=_fake(None, ok=False), now=NOW)
    assert result["failed"] and result["reason"] == "claude_timeout"
    assert "unit|ほうれん草|袋" in {i["key"] for i in food_resolve.pending(conn, now=NOW)}


def test_nothing_pending_does_not_call_claude(conn) -> None:
    calls: list[str] = []
    result = food_resolve.resolve(conn, runner=_fake([], calls=calls), now=NOW)
    assert result["asked"] == 0 and calls == []


def test_manual_unit_beats_lexicon_and_llm(conn, stocked) -> None:
    food_resolve.set_unit(conn, "もやし", "袋", 250, confidence="manual")  # lexicon は 200
    food_resolve.set_unit(conn, "ほうれん草", "袋", 180, confidence="manual")
    assert food_resolve.set_unit(conn, "ほうれん草", "袋", 200, confidence="llm") is None  # 上書きしない
    tables = nutrition.tables_for(conn)
    assert tables.piece["袋"]["もやし"] == 250.0
    assert tables.piece["袋"]["ほうれん草"] == 180.0


def test_lexicon_beats_llm(conn, stocked) -> None:
    food_resolve.set_unit(conn, "もやし", "袋", 999, confidence="llm")
    assert nutrition.tables_for(conn).piece["袋"]["もやし"] == 200.0


def test_removed_unit_is_not_immediately_refilled(conn, stocked) -> None:
    ids = _ids(conn)
    food_resolve.resolve(conn, runner=_fake([{"id": ids["unit|ほうれん草|袋"], "grams": 200}]), now=NOW)
    food_resolve.remove_unit(conn, "ほうれん草", "袋")
    assert "unit|ほうれん草|袋" not in {i["key"] for i in food_resolve.pending(conn)}


def test_check_answers_drops_non_http_urls() -> None:
    item = {"key": "unit|x|袋", "kind": "unit", "name": "x", "unit": "袋"}
    out = food_resolve.check_answers(
        {"answers": [{"id": "u1", "grams": 100, "source_url": "javascript:alert(1)"}]},
        {"u1": item}, {}, food_resolve.load_settings(),
    )
    assert out[0]["source_url"] == ""
