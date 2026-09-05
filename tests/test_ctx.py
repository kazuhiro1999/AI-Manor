"""文脈パック（ADR-001 §10）。1ホップ・2ホップの depends_on 連鎖・budget での落とし順。"""

from __future__ import annotations

from manor import ctx
from manor import decision as decision_mod
from manor import graph
from manor import task as task_mod


def test_build_core_fields(conn):
    tid = task_mod.add(conn, "設計", goal="動くもの", now="下書き中", next_="実装")
    data = ctx.build(conn, tid)
    assert data["core"]["id"] == tid
    assert data["core"]["title"] == "設計"
    assert data["core"]["goal"] == "動くもの"
    assert data["core"]["status"] == "todo"


def test_onehop_depends_on_carries_status(conn):
    blocker = task_mod.add(conn, "先にやること")
    tid = task_mod.add(conn, "本題", depends_on=[blocker])
    task_mod.status(conn, blocker, "doing")
    data = ctx.build(conn, tid)
    dep_entries = data["onehop"]["depends_on"]
    assert len(dep_entries) == 1
    assert dep_entries[0]["id"] == blocker
    assert dep_entries[0]["status"] == "doing"


def test_reverse_depends_on_shows_blocking(conn):
    blocker = task_mod.add(conn, "先にやること")
    tid = task_mod.add(conn, "本題", depends_on=[blocker])
    data = ctx.build(conn, blocker)
    blocking_ids = [b["id"] for b in data["blocking"]]
    assert tid in blocking_ids


def test_decided_by_carries_decision_status_and_ruling(conn):
    tid = task_mod.add(conn, "承認要る", section="A", recommendation="やる")
    did = decision_mod.ask(conn, "やってよいか", task_id=tid, recommend="やる", background="")
    decision_mod.rule(conn, did, "approved", ruling="よし")
    data = ctx.build(conn, tid)
    entries = data["onehop"]["decided_by"]
    assert entries[0]["id"] == did
    assert entries[0]["status"] == "approved"
    assert entries[0]["ruling"] == "よし"


def test_twohop_follows_depends_on_chain_only(conn):
    root = task_mod.add(conn, "根")
    middle = task_mod.add(conn, "中間", depends_on=[root])
    tip = task_mod.add(conn, "先端", depends_on=[middle])

    data = ctx.build(conn, tip, depth=2)
    onehop_ids = [e["id"] for e in data["onehop"]["depends_on"]]
    assert onehop_ids == [middle]
    twohop_ids = [e["id"] for e in data["twohop"]]
    assert root in twohop_ids


def test_twohop_does_not_follow_relates_to(conn):
    a = task_mod.add(conn, "A")
    b = task_mod.add(conn, "B")
    c = task_mod.add(conn, "C")
    graph.link(conn, a, "relates_to", b)
    graph.link(conn, b, "depends_on", c)
    data = ctx.build(conn, a, depth=2)
    # a の 2ホップは a --relates_to--> b の depends_on を辿らない
    # （2ホップは depends_on の連鎖だけを辿る、という ADR-001 §10 の約束）
    twohop_ids = [e["id"] for e in data["twohop"]]
    assert c not in twohop_ids


def test_budget_drops_relates_then_twohop_then_events(conn):
    root = task_mod.add(conn, "根", body="x" * 50)
    middle = task_mod.add(conn, "中間" * 20, depends_on=[root])
    tip = task_mod.add(conn, "先端" * 20, depends_on=[middle])
    other = task_mod.add(conn, "弱い関連" * 20)
    graph.link(conn, tip, "relates_to", other)
    for i in range(5):
        task_mod.set(conn, tip, now=f"更新{i}")

    data = ctx.build(conn, tip, depth=2, budget=1)  # 極端に小さい budget で全部落とす
    text = ctx.render_markdown(data)
    assert "relates_to" in text or "2ホップ" in text or "直近イベント" in text
    json_out = ctx.to_json(data)
    assert json_out["dropped"] == ["relates_to", "twohop", "events"]
    assert "relates_to" not in json_out["onehop"]
    assert json_out["twohop"] == []
    assert json_out["events"] == []


def test_budget_generous_keeps_everything(conn):
    root = task_mod.add(conn, "根")
    tip = task_mod.add(conn, "先端", depends_on=[root])
    other = task_mod.add(conn, "弱い関連")
    graph.link(conn, tip, "relates_to", other)

    data = ctx.build(conn, tip, depth=2, budget=100_000)
    json_out = ctx.to_json(data)
    assert json_out["dropped"] == []
    assert "relates_to" in json_out["onehop"]
