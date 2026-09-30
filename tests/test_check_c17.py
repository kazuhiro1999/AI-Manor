"""check.py の C17 — decision が approved/modified なのに、decided_by 先の task が
todo のまま着手されていないもの（T46・外部視点E31。2026-09-11実測: D11・D12・D16に
紐づく T36・T39・T15 が2日 todo のまま放置され、誰も気づかなかった）。

C1（`v_blocked_ready`）は waiting/hold が対象で todo は見ない——C17 が見るのはその隙間。
"""

from __future__ import annotations

from pathlib import Path

from manor import check as check_mod
from manor import decision as decision_mod
from manor import task as task_mod


def test_c17_fires_when_decision_approved_but_task_still_todo(conn, home: Path) -> None:
    task_id = task_mod.add(conn, "板のタスク")
    decision_id = decision_mod.ask(
        conn, "裁定タイトル", task_id=task_id, recommend="案", background="背景"
    )
    decision_mod.rule(conn, decision_id, "approved", ruling="承認します")

    results = check_mod.run(conn, home)

    assert task_id in {r["id"] for r in results["C17"]}
    assert check_mod.ok(results) is False


def test_c17_fires_when_decision_modified(conn, home: Path) -> None:
    task_id = task_mod.add(conn, "板のタスク2")
    decision_id = decision_mod.ask(
        conn, "裁定タイトル2", task_id=task_id, recommend="案", background="背景"
    )
    decision_mod.rule(conn, decision_id, "modified", ruling="こう直して")

    results = check_mod.run(conn, home)

    assert task_id in {r["id"] for r in results["C17"]}


def test_c17_silent_while_decision_is_still_open(conn, home: Path) -> None:
    task_id = task_mod.add(conn, "板のタスク3")
    decision_mod.ask(conn, "裁定タイトル3", task_id=task_id, recommend="案", background="背景")

    results = check_mod.run(conn, home)

    assert task_id not in {r["id"] for r in results["C17"]}


def test_c17_silent_once_task_moves_past_todo(conn, home: Path) -> None:
    task_id = task_mod.add(conn, "板のタスク4")
    decision_id = decision_mod.ask(
        conn, "裁定タイトル4", task_id=task_id, recommend="案", background="背景"
    )
    decision_mod.rule(conn, decision_id, "approved", ruling="承認します")
    task_mod.status(conn, task_id, "doing")

    results = check_mod.run(conn, home)

    assert task_id not in {r["id"] for r in results["C17"]}


def test_c17_silent_when_decision_rejected(conn, home: Path) -> None:
    """却下は「やらない」という裁定——todo のままでも C17 は鳴らない。"""
    task_id = task_mod.add(conn, "板のタスク5")
    decision_id = decision_mod.ask(
        conn, "裁定タイトル5", task_id=task_id, recommend="案", background="背景"
    )
    decision_mod.rule(conn, decision_id, "rejected", ruling="やらない")

    results = check_mod.run(conn, home)

    assert task_id not in {r["id"] for r in results["C17"]}
