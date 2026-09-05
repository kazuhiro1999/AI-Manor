"""証跡（ADR-006 §2）: `decision.evidence`／`task_event.authorized_by`／
`decision show` の逆引き／C11（evidence の無い open decision。警告）。
"""

from __future__ import annotations

import json
from pathlib import Path

from manor import cli
from manor import check as check_mod
from manor import decision as decision_mod
from manor import handoff as handoff_mod
from manor import task as task_mod


# --- decision.ask に evidence ------------------------------------------------------


def test_ask_stores_evidence(conn):
    tid = task_mod.add(conn, "GPU購入", recommendation="買う")
    did = decision_mod.ask(
        conn, "GPUを買うか", task_id=tid, recommend="買う", background="実験が遅い",
        evidence="- 見た: 在庫ページ https://example.test/gpu\n- 見た: 予算 残り12万円",
    )
    row = conn.execute("SELECT evidence FROM decision WHERE id = ?", (did,)).fetchone()
    assert "在庫ページ" in row["evidence"]


def test_ask_evidence_defaults_empty_and_is_allowed(conn):
    """空でも通す（ADR-006 D5）。"""
    tid = task_mod.add(conn, "何か", recommendation="やる")
    did = decision_mod.ask(conn, "やってよいか", task_id=tid, recommend="やる", background="")
    row = conn.execute("SELECT evidence FROM decision WHERE id = ?", (did,)).fetchone()
    assert row["evidence"] == ""


# --- task_event.authorized_by（HG の門） ---------------------------------------------


def test_hg_done_auto_fills_authorized_by_with_decision_id(conn):
    tid = task_mod.add(conn, "重要な変更", level="HG", section="A", recommendation="やる")
    task_mod.status(conn, tid, "doing")
    did = conn.execute(
        "SELECT dst FROM edge WHERE src = ? AND rel = 'decided_by'", (tid,)
    ).fetchone()["dst"]
    decision_mod.rule(conn, did, "approved", ruling="よし")

    result = task_mod.status(conn, tid, "done")
    assert result["authorized_by"] == did

    row = conn.execute(
        "SELECT authorized_by FROM task_event WHERE task_id = ? AND to_status = 'done'", (tid,)
    ).fetchone()
    assert row["authorized_by"] == did


def test_non_hg_done_leaves_authorized_by_null(conn):
    tid = task_mod.add(conn, "普通のタスク")
    task_mod.status(conn, tid, "doing")
    result = task_mod.status(conn, tid, "done")
    assert result["authorized_by"] is None
    row = conn.execute(
        "SELECT authorized_by FROM task_event WHERE task_id = ? AND to_status = 'done'", (tid,)
    ).fetchone()
    assert row["authorized_by"] is None


def test_explicit_authorized_by_is_kept_over_auto_fill(conn):
    """明示した decision id は auto-fill（HG の門を通した decision）より優先される。

    ADR-006 D21 補足（3周目レビュー）: `done` への遷移の `authorized_by` は実在する
    decision の id に限る（`H<n>` は不可、存在しない id も不可）ようになったため、
    「明示値が勝つ」ことを確かめるにも**実在する別の decision**を使う必要がある
    （auto-fill が指す decision とは別物であることを示せば「明示が優先された」と言える）。
    """
    tid = task_mod.add(conn, "何か", level="HG", section="A", recommendation="やる")
    task_mod.status(conn, tid, "doing")
    did = conn.execute(
        "SELECT dst FROM edge WHERE src = ? AND rel = 'decided_by'", (tid,)
    ).fetchone()["dst"]
    decision_mod.rule(conn, did, "approved", ruling="よし")

    # auto-fill が使うはずの did とは別に、承認済みの decision をもう1つ用意して明示する
    other_did = decision_mod.ask(
        conn, "別件の裁定", task_id=tid, recommend="別案", background=""
    )
    decision_mod.rule(conn, other_did, "approved", ruling="こちらで")

    result = task_mod.status(conn, tid, "done", authorized_by=other_did)
    assert result["authorized_by"] == other_did
    assert other_did != did


# --- handoff.accept/reject が H<n> を authorized_by に残す --------------------------


def test_handoff_accept_sets_authorized_by_to_handoff_id(conn, home: Path):
    tid = task_mod.add(conn, "献立を考える")
    result = handoff_mod.new(conn, home, tid, "chef")
    row = conn.execute(
        "SELECT authorized_by FROM task_event WHERE task_id = ? ORDER BY id DESC LIMIT 1", (tid,)
    ).fetchone()
    assert row["authorized_by"] is None  # 委譲そのものは authorized_by を持たない

    handoff_mod.accept(conn, result["id"], note="良い")
    row = conn.execute(
        "SELECT authorized_by FROM task_event WHERE task_id = ? ORDER BY id DESC LIMIT 1", (tid,)
    ).fetchone()
    assert row["authorized_by"] == f"H{result['id']}"


def test_handoff_reject_sets_authorized_by_to_handoff_id(conn, home: Path):
    tid = task_mod.add(conn, "献立を考える")
    result = handoff_mod.new(conn, home, tid, "chef")
    handoff_mod.reject(conn, result["id"], note="証跡が無い")
    row = conn.execute(
        "SELECT authorized_by FROM task_event WHERE task_id = ? ORDER BY id DESC LIMIT 1", (tid,)
    ).fetchone()
    assert row["authorized_by"] == f"H{result['id']}"


# --- decision.show（逆引き） ---------------------------------------------------------


def test_decision_show_reverse_lookup_lists_authorized_events(conn):
    tid = task_mod.add(conn, "重要な変更", level="HG", section="A", recommendation="やる")
    task_mod.status(conn, tid, "doing")
    did = conn.execute(
        "SELECT dst FROM edge WHERE src = ? AND rel = 'decided_by'", (tid,)
    ).fetchone()["dst"]
    decision_mod.rule(conn, did, "approved", ruling="よし")
    task_mod.status(conn, tid, "done")

    data = decision_mod.show(conn, did)
    assert data["id"] == did
    assert tid in data["tasks"]
    assert any(e["task_id"] == tid and e["to_status"] == "done" for e in data["authorized_events"])


def test_decision_show_unknown_id_raises(conn):
    import pytest

    from manor.errors import ManorError

    with pytest.raises(ManorError):
        decision_mod.show(conn, "D999")


# --- C11: evidence の無い open decision（警告。終了コードを変えない） -----------------


def test_check_c11_flags_open_decision_without_evidence(conn):
    tid = task_mod.add(conn, "何か", recommendation="やる")
    decision_mod.ask(conn, "やってよいか", task_id=tid, recommend="やる", background="")
    violations = check_mod.check_c11(conn)
    assert len(violations) == 1


def test_check_c11_does_not_flag_decision_with_evidence(conn):
    tid = task_mod.add(conn, "何か", recommendation="やる")
    decision_mod.ask(
        conn, "やってよいか", task_id=tid, recommend="やる", background="", evidence="- 見た: X"
    )
    assert check_mod.check_c11(conn) == []


def test_check_c11_does_not_flag_decided_decision(conn):
    """裁定済み（open でない）decision は対象外。"""
    tid = task_mod.add(conn, "何か", recommendation="やる")
    did = decision_mod.ask(conn, "やってよいか", task_id=tid, recommend="やる", background="")
    decision_mod.rule(conn, did, "approved", ruling="よし")
    assert check_mod.check_c11(conn) == []


def test_check_c11_is_warning_only_does_not_change_exit_code(conn, home: Path):
    """C11 が引っかかっても `check.ok()` は True のまま（終了コードを変えない）。"""
    tid = task_mod.add(conn, "何か", recommendation="やる")
    decision_mod.ask(conn, "やってよいか", task_id=tid, recommend="やる", background="")
    results = check_mod.run(conn, home)
    assert results["C11"]  # 引っかかっている
    assert check_mod.ok(results) is True


# --- CLI 経由の一連（`manor decision ask --evidence` / `decision show` / HG の一連） -----


def test_cli_decision_ask_with_evidence_and_show_round_trip(home_path: Path, capsys):
    cli.main(["init"])
    capsys.readouterr()
    cli.main(["task", "add", "GPU購入", "--recommendation", "買う", "--json"])
    tid = json.loads(capsys.readouterr().out)["id"]

    code = cli.main(
        [
            "decision", "ask", "GPUを買うか", "--task", tid, "--recommend", "買う",
            "--background", "実験が遅い", "--evidence", "- 見た: 在庫ページ", "--json",
        ]
    )
    assert code == 0
    did = json.loads(capsys.readouterr().out)["id"]

    assert cli.main(["decision", "show", did, "--json"]) == 0
    show_out = json.loads(capsys.readouterr().out)
    assert "在庫ページ" in show_out["evidence"]

    assert cli.main(["decision", "rule", did, "approved", "--ruling", "よし"]) == 0
    capsys.readouterr()

    assert cli.main(["task", "status", tid, "doing"]) == 0
    capsys.readouterr()


def test_cli_hg_task_done_records_authorized_by_and_shows_in_task_show(home_path: Path, capsys):
    cli.main(["init"])
    capsys.readouterr()

    code = cli.main(
        ["task", "add", "取引先へメール", "--class", "external_send",
         "--recommendation", "下書きを見てから送る", "--json"]
    )
    assert code == 0
    tid = json.loads(capsys.readouterr().out)["id"]

    assert cli.main(["task", "status", tid, "doing", "--json"]) == 0
    capsys.readouterr()

    assert cli.main(["decision", "list", "--open", "--json"]) == 0
    capsys.readouterr()

    from manor import db as db_mod

    conn = db_mod.connect(home_path)
    try:
        did = conn.execute(
            "SELECT dst FROM edge WHERE src = ? AND rel = 'decided_by'", (tid,)
        ).fetchone()["dst"]
    finally:
        conn.close()

    assert cli.main(["decision", "rule", did, "approved", "--ruling", "よし"]) == 0
    capsys.readouterr()

    assert cli.main(["task", "done", tid, "--json"]) == 0
    capsys.readouterr()

    assert cli.main(["task", "show", tid, "--json"]) == 0
    show_out = json.loads(capsys.readouterr().out)
    done_events = [e for e in show_out["events"] if e["to_status"] == "done"]
    assert done_events and done_events[0]["authorized_by"] == did

    assert cli.main(["decision", "show", did, "--json"]) == 0
    decision_out = json.loads(capsys.readouterr().out)
    assert any(
        e["task_id"] == tid and e["to_status"] == "done" for e in decision_out["authorized_events"]
    )
