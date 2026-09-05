"""task の状態機械（ADR-001 §4）。許される遷移・拒否される遷移・門を確かめる。"""

from __future__ import annotations

import pytest

from manor import decision as decision_mod
from manor import task as task_mod
from manor.errors import ManorError


def test_allowed_transition_todo_to_doing(conn):
    tid = task_mod.add(conn, "設計")
    result = task_mod.status(conn, tid, "doing", owner="butler")
    assert result["status"] == "doing"
    assert result["owner"] == "butler"


def test_disallowed_transition_done_to_doing(conn):
    tid = task_mod.add(conn, "設計")
    task_mod.status(conn, tid, "doing")
    task_mod.status(conn, tid, "done")
    with pytest.raises(ManorError):
        task_mod.status(conn, tid, "doing")


def test_disallowed_transition_withdrawn_to_anything(conn):
    tid = task_mod.add(conn, "設計")
    task_mod.status(conn, tid, "withdrawn", note="不要になった")
    with pytest.raises(ManorError):
        task_mod.status(conn, tid, "todo")


def test_waiting_requires_note(conn):
    tid = task_mod.add(conn, "設計")
    with pytest.raises(ManorError):
        task_mod.status(conn, tid, "waiting")
    # note を渡せば通る
    result = task_mod.status(conn, tid, "waiting", note="レビュー待ち")
    assert result["status"] == "waiting"


def test_withdrawn_requires_note(conn):
    tid = task_mod.add(conn, "設計")
    with pytest.raises(ManorError):
        task_mod.status(conn, tid, "withdrawn")
    result = task_mod.status(conn, tid, "withdrawn", note="不要になった")
    assert result["status"] == "withdrawn"


def test_same_state_reset_logs_event(conn):
    tid = task_mod.add(conn, "設計")
    task_mod.status(conn, tid, "waiting", note="A待ち")
    task_mod.status(conn, tid, "waiting", note="B待ちに変わった")
    row = conn.execute("SELECT status_note FROM task WHERE id = ?", (tid,)).fetchone()
    assert row["status_note"] == "B待ちに変わった"
    events = conn.execute(
        "SELECT COUNT(*) AS n FROM task_event WHERE task_id = ?", (tid,)
    ).fetchone()
    # 作成時の1件 + waiting x2 = 3件。同じ状態への更新も必ず1行残る
    assert events["n"] == 3


def test_hg_gate_blocks_done_without_approved_decision(conn):
    tid = task_mod.add(conn, "重要な変更", level="HG", section="A", recommendation="やる")
    task_mod.status(conn, tid, "doing")
    with pytest.raises(ManorError):
        task_mod.status(conn, tid, "done")


def test_hg_gate_allows_done_with_approved_decision(conn):
    tid = task_mod.add(conn, "重要な変更", level="HG", section="A", recommendation="やる")
    task_mod.status(conn, tid, "doing")
    decision_mod.ask(
        conn, "やってよいか", task_id=tid, recommend="やる", background="必要だから"
    )
    did = conn.execute(
        "SELECT dst FROM edge WHERE src = ? AND rel = 'decided_by'", (tid,)
    ).fetchone()["dst"]
    decision_mod.rule(conn, did, "approved", ruling="よし")
    result = task_mod.status(conn, tid, "done")
    assert result["status"] == "done"


def test_hg_gate_modified_also_passes(conn):
    tid = task_mod.add(conn, "重要な変更2", level="HG", section="A", recommendation="やる")
    task_mod.status(conn, tid, "doing")
    decision_mod.ask(conn, "やってよいか2", task_id=tid, recommend="やる", background="")
    did = conn.execute(
        "SELECT dst FROM edge WHERE src = ? AND rel = 'decided_by'", (tid,)
    ).fetchone()["dst"]
    decision_mod.rule(conn, did, "modified", ruling="条件付きでよし")
    result = task_mod.status(conn, tid, "done")
    assert result["status"] == "done"


def test_resident_cannot_become_done(conn):
    tid = task_mod.add(conn, "見張り")
    task_mod.status(conn, tid, "resident")
    with pytest.raises(ManorError):
        task_mod.status(conn, tid, "done")


def test_resident_can_become_withdrawn(conn):
    tid = task_mod.add(conn, "見張り")
    task_mod.status(conn, tid, "resident")
    result = task_mod.status(conn, tid, "withdrawn", note="もう要らない")
    assert result["status"] == "withdrawn"


def test_depends_on_unresolved_warns_but_does_not_block(conn):
    blocker = task_mod.add(conn, "先にやること")
    tid = task_mod.add(conn, "本題", depends_on=[blocker])
    task_mod.status(conn, tid, "doing")
    result = task_mod.status(conn, tid, "done")
    assert result["status"] == "done"
    assert result["warnings"], "depends_on が未完了のままなら警告が出るはず"


def test_depends_on_resolved_no_warning(conn):
    blocker = task_mod.add(conn, "先にやること")
    tid = task_mod.add(conn, "本題", depends_on=[blocker])
    task_mod.status(conn, blocker, "doing")
    task_mod.status(conn, blocker, "done")
    task_mod.status(conn, tid, "doing")
    result = task_mod.status(conn, tid, "done")
    assert result["warnings"] == []


def test_section_a_requires_recommendation_on_add(conn):
    with pytest.raises(ManorError):
        task_mod.add(conn, "承認が要る", section="A")


def test_unknown_status_is_vocab_error(conn):
    tid = task_mod.add(conn, "設計")
    with pytest.raises(ManorError) as excinfo:
        task_mod.status(conn, tid, "not_a_status")
    assert excinfo.value.code == 2


# --- 振る舞い試験（2026-09-02）で見つかった2件を機構にした ------------------------------


def test_link_dependency_auto_waits_when_blocker_unfinished(conn):
    """S4: 依存を張ることと待つことは同じ意味。機械が waiting に揃える。"""
    blocker = task_mod.add(conn, "先にやること")
    waiter = task_mod.add(conn, "本題")
    result = task_mod.link_dependency(conn, waiter, blocker)
    assert result["auto_wait"] is True
    row = conn.execute("SELECT status, status_note FROM task WHERE id = ?", (waiter,)).fetchone()
    assert row["status"] == "waiting"
    assert blocker in row["status_note"]


def test_link_dependency_does_not_wait_when_blocker_done_or_src_doing(conn):
    blocker = task_mod.add(conn, "済んだこと")
    task_mod.status(conn, blocker, "doing")
    task_mod.status(conn, blocker, "done")
    waiter = task_mod.add(conn, "本題")
    assert task_mod.link_dependency(conn, waiter, blocker)["auto_wait"] is False
    assert conn.execute("SELECT status FROM task WHERE id = ?", (waiter,)).fetchone()["status"] == "todo"

    other = task_mod.add(conn, "まだのこと")
    running = task_mod.add(conn, "進行中の本題")
    task_mod.status(conn, running, "doing")
    assert task_mod.link_dependency(conn, running, other)["auto_wait"] is False
    assert conn.execute("SELECT status FROM task WHERE id = ?", (running,)).fetchone()["status"] == "doing"


def test_add_with_depends_on_starts_waiting(conn):
    blocker = task_mod.add(conn, "先にやること")
    waiter = task_mod.add(conn, "本題", depends_on=[blocker])
    assert conn.execute("SELECT status FROM task WHERE id = ?", (waiter,)).fetchone()["status"] == "waiting"


def test_hg_add_requires_recommendation_and_asks_decision(conn):
    """S6: HG は起票の時点で decision を積む。推奨が無ければ起票できない。"""
    with pytest.raises(ManorError):
        task_mod.add(conn, "取引先へメール", level="HG")
    tid = task_mod.add(conn, "取引先へメール", level="HG", recommendation="下書きを見てから送る")
    row = conn.execute("SELECT section, status FROM task WHERE id = ?", (tid,)).fetchone()
    assert row["section"] == "A" and row["status"] == "todo"
    d = conn.execute(
        "SELECT d.status, d.recommendation FROM edge e JOIN decision d ON d.id = e.dst"
        " WHERE e.src = ? AND e.rel = 'decided_by'", (tid,)
    ).fetchone()
    assert d is not None and d["status"] == "open" and "下書き" in d["recommendation"]


def test_class_decides_level_even_if_level_given(conn):
    """S6 再試験: `--level L1 --class external_send` のように食い違ったら、クラスが勝つ（HG 固定）。"""
    tid = task_mod.add(
        conn, "取引先へメール", cls="external_send", level="L1", recommendation="下書きを見てから送る"
    )
    row = conn.execute("SELECT level, section FROM task WHERE id = ?", (tid,)).fetchone()
    assert row["level"] == "HG" and row["section"] == "A"


def test_unknown_class_is_rejected_even_with_level(conn):
    with pytest.raises(ManorError) as exc:
        task_mod.add(conn, "何か", cls="human_gate", level="L1")
    assert "external_send" in exc.value.message_ja


# --- 裁定の一言（2026-09-02 主人の指摘: 入力せずに却下できないのは仕様か → 変えた） ------------


def test_rule_without_text_defaults_for_approve_and_reject(conn):
    tid = task_mod.add(conn, "何か", level="HG", recommendation="却下でよい")
    did = conn.execute(
        "SELECT dst FROM edge WHERE src = ? AND rel = 'decided_by'", (tid,)
    ).fetchone()["dst"]
    out = decision_mod.rule(conn, did, "rejected", ruling="")
    row = conn.execute("SELECT status, ruling FROM decision WHERE id = ?", (did,)).fetchone()
    assert row["status"] == "rejected" and row["ruling"] == "却下"
    assert out is not None


def test_rule_modified_requires_text(conn):
    tid = task_mod.add(conn, "何か", level="HG", recommendation="やる")
    did = conn.execute(
        "SELECT dst FROM edge WHERE src = ? AND rel = 'decided_by'", (tid,)
    ).fetchone()["dst"]
    with pytest.raises(ManorError):
        decision_mod.rule(conn, did, "modified", ruling="   ")
    decision_mod.rule(conn, did, "modified", ruling="期限を1週間延ばして")
    assert conn.execute("SELECT status FROM decision WHERE id = ?", (did,)).fetchone()["status"] == "modified"
