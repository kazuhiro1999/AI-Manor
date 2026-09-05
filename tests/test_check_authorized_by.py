"""ADR-006 D21: `task_event.authorized_by` が「何がこの遷移を認めたか」の唯一の真実。

C5 は事実（authorized_by）だけを見る（`decided_by` の辺は「関係」であって見ない）。
C12 は authorized_by が指す decision/handoff が実在することを検算する。
`db.init()` の一回きりの移行（辺→事実への写し）も併せて検算する。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from manor import check as check_mod
from manor import db as db_mod
from manor import decision as decision_mod
from manor import handoff as handoff_mod
from manor import task as task_mod
from manor.errors import ManorError


def _make_hg_done_task(conn, title: str = "HGタスク") -> tuple[str, str]:
    """level=HG のタスクを起票し、承認 → doing → done まで**普通の経路**で進める。"""
    task_id = task_mod.add(conn, title, level="HG", recommendation="既定案")
    row = conn.execute(
        "SELECT dst FROM edge WHERE src = ? AND rel = 'decided_by'", (task_id,)
    ).fetchone()
    decision_id = str(row["dst"])
    decision_mod.rule(conn, decision_id, "approved", ruling="")
    task_mod.status(conn, task_id, "doing")
    task_mod.status(conn, task_id, "done")
    return task_id, decision_id


# --- 普通の経路: 自動補完が働き、C5・C12 はどちらも空 ------------------------------------


def test_hg_done_normal_path_c5_and_c12_empty(conn, home: Path):
    _make_hg_done_task(conn)
    results = check_mod.run(conn, home)
    assert results["C5"] == []
    assert results["C12"] == []
    assert check_mod.ok(results) is True


# --- DB 直接改変: decided_by の辺が残っていても C5 は事実（authorized_by）だけを見る ----------


def test_hg_done_authorized_by_nulled_by_direct_sql_flagged_by_c5(conn, home: Path):
    task_id, decision_id = _make_hg_done_task(conn)

    # DB 直接改変を模す: done event の authorized_by だけを NULL に戻す
    conn.execute(
        "UPDATE task_event SET authorized_by = NULL WHERE task_id = ? AND to_status = 'done'",
        (task_id,),
    )

    results = check_mod.run(conn, home)
    assert task_id in {r["id"] for r in results["C5"]}

    # decided_by の辺は生きたまま（C5 が見なくなったのは辺ではなく事実の欠落）
    edge = conn.execute(
        "SELECT 1 FROM edge WHERE src = ? AND rel = 'decided_by' AND dst = ?",
        (task_id, decision_id),
    ).fetchone()
    assert edge is not None


# --- C12: authorized_by が指す先が実在しないものを列挙する --------------------------------
#
# `task.status()` は `done` への遷移だけ入り口で値域を狭める（下の節）ので、ここでの
# 「実在しない id を渡す」検算は done 以外の遷移（doing/hold）を使う——handoff.accept/reject
# と同じ形で、C12 が実在しない decision/handoff を事後に検算することを確かめる。


def test_c12_flags_dangling_handoff_id_on_non_done_transition(conn, home: Path):
    task_id = task_mod.add(conn, "委譲中のタスク")
    task_mod.status(conn, task_id, "doing", authorized_by="H999")

    results = check_mod.run(conn, home)
    assert any(r["authorized_by"] == "H999" for r in results["C12"])
    assert check_mod.ok(results) is False  # C12 は警告ではなくエラー


def test_c12_flags_dangling_decision_id_on_non_done_transition(conn, home: Path):
    task_id = task_mod.add(conn, "hold中のタスク")
    task_mod.status(conn, task_id, "doing")
    task_mod.status(conn, task_id, "hold", note="待ち", authorized_by="D999")

    results = check_mod.run(conn, home)
    assert any(r["authorized_by"] == "D999" for r in results["C12"])


def test_c12_empty_when_authorized_by_resolves(conn, home: Path):
    _make_hg_done_task(conn)
    results = check_mod.run(conn, home)
    assert results["C12"] == []


# --- ADR-006 D21 補足（3周目レビュー）: done の authorized_by は decision の id に限る -------
#
# C12 は「H<n> は done では違反」を事後に検算するが、`task.status()` 自体が入り口で
# 同じ規則を守る（`task.done(authorized_by="H7")` は公開 API から到達できてしまっていた）。


def test_done_rejects_handoff_id_as_authorized_by(conn, home: Path):
    task_id = task_mod.add(conn, "普通のタスク")
    task_mod.status(conn, task_id, "doing")
    with pytest.raises(ManorError):
        task_mod.status(conn, task_id, "done", authorized_by="H7")
    # 拒否されているので task_event に H7 は残っていない
    row = conn.execute(
        "SELECT 1 FROM task_event WHERE task_id = ? AND authorized_by = 'H7'", (task_id,)
    ).fetchone()
    assert row is None


def test_done_rejects_nonexistent_decision_id(conn, home: Path):
    task_id = task_mod.add(conn, "普通のタスク2")
    task_mod.status(conn, task_id, "doing")
    with pytest.raises(ManorError):
        task_mod.status(conn, task_id, "done", authorized_by="D999")


def test_done_accepts_existing_decision_id(conn, home: Path):
    task_id = task_mod.add(conn, "普通のタスク3")
    decision_id = decision_mod.ask(
        conn, "後付けの裁定", task_id=task_id, recommend="そのまま", background=""
    )
    decision_mod.rule(conn, decision_id, "approved", ruling="")
    task_mod.status(conn, task_id, "doing")
    result = task_mod.status(conn, task_id, "done", authorized_by=decision_id)
    assert result["authorized_by"] == decision_id


def test_doing_transition_with_handoff_id_still_allowed(conn, home: Path):
    """done だけを狭める——doing 等の遷移は H<n> を引き続き受け付ける（handoff.accept の経路）。"""
    task_id = task_mod.add(conn, "委譲される側のタスク")
    result = task_mod.status(conn, task_id, "doing", authorized_by="H1")
    assert result["authorized_by"] == "H1"


def test_direct_sql_handoff_id_on_done_event_flagged_by_both_c5_and_c12(conn, home: Path):
    """入り口（task.status）は塞いだが、DB を直接触った場合の検算（事後の網）も確かめる。
    HG task の done event の authorized_by を H<n> に書き換えると、C5（事実が decision を
    指していない）と C12（done では H<n> を許さない）の**両方**が同じ task を拾う。
    """
    task_id, _decision_id = _make_hg_done_task(conn, "後から書き換えられたタスク")
    other_task = task_mod.add(conn, "委譲先のタスク")
    handoff_id = handoff_mod.new(conn, home, other_task, "chef")["id"]

    conn.execute(
        "UPDATE task_event SET authorized_by = ? WHERE task_id = ? AND to_status = 'done'",
        (f"H{handoff_id}", task_id),
    )

    results = check_mod.run(conn, home)
    assert task_id in {r["id"] for r in results["C5"]}
    assert any(
        r["task_id"] == task_id and r["authorized_by"] == f"H{handoff_id}"
        for r in results["C12"]
    )
    assert check_mod.ok(results) is False


# --- 移行（backfill）: 辺→事実への一回きりの写し ------------------------------------------


def test_backfill_fills_authorized_by_from_decided_by_edge(conn, home: Path):
    task_id, decision_id = _make_hg_done_task(conn, "旧経路タスク")

    # 「旧 DB」の再現: authorized_by が入らなかった時代の状態を SQL で直接作る
    # （decided_by の辺と approved の decision は残ったまま、事実の列だけが欠けている）。
    conn.execute(
        "UPDATE task_event SET authorized_by = NULL WHERE task_id = ? AND to_status = 'done'",
        (task_id,),
    )
    conn.commit()

    results_before = check_mod.run(conn, home)
    assert task_id in {r["id"] for r in results_before["C5"]}

    db_mod.init(home)  # 移行をもう一度回す（冪等）

    conn2 = db_mod.connect(home)
    try:
        ev = conn2.execute(
            "SELECT authorized_by FROM task_event WHERE task_id = ? AND to_status = 'done'",
            (task_id,),
        ).fetchone()
        assert ev["authorized_by"] == decision_id

        results_after = check_mod.run(conn2, home)
        assert results_after["C5"] == []
        assert results_after["C12"] == []
    finally:
        conn2.close()


def test_backfill_is_idempotent_and_does_not_override_explicit_value(conn, home: Path):
    """既に事実（authorized_by）が入っていれば、`db.init` を何度回しても書き換えない。"""
    task_id, decision_id = _make_hg_done_task(conn, "別の decision に差し替え済み")
    other_id = decision_mod.ask(
        conn, "別の裁定", task_id=task_id, recommend="別案", background=""
    )
    decision_mod.rule(conn, other_id, "approved", ruling="")
    conn.execute(
        "UPDATE task_event SET authorized_by = ? WHERE task_id = ? AND to_status = 'done'",
        (other_id, task_id),
    )
    conn.commit()

    db_mod.init(home)

    conn2 = db_mod.connect(home)
    try:
        ev = conn2.execute(
            "SELECT authorized_by FROM task_event WHERE task_id = ? AND to_status = 'done'",
            (task_id,),
        ).fetchone()
        assert ev["authorized_by"] == other_id  # 最小の decision_id（decision_id）で上書きされない
    finally:
        conn2.close()


def test_empty_authorized_by_is_treated_as_unspecified(conn, home: Path):
    """空文字の authorized_by は「未指定」——HG の自動補完が働き、空文字が event に残らない。"""
    task_id = task_mod.add(conn, "HG 空文字", level="HG", recommendation="既定案")
    decision_id = str(
        conn.execute("SELECT dst FROM edge WHERE src = ? AND rel = 'decided_by'", (task_id,)).fetchone()["dst"]
    )
    decision_mod.rule(conn, decision_id, "approved", ruling="")
    task_mod.status(conn, task_id, "doing")
    result = task_mod.status(conn, task_id, "done", authorized_by="  ")
    assert result["authorized_by"] == decision_id
    assert check_mod.run(conn, home)["C5"] == []
    assert check_mod.run(conn, home)["C12"] == []
