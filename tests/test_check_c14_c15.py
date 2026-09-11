"""check.py の C14/C15 — 利用者の整合（ADR-014 D7）。**合成データのみ**。

C14: `task.user_id` / `project.user_id` が `user` 表に無い・畳んだ利用者を指している行が
無いこと。C15: `role='principal'` がちょうど1件、`role='butler'` がちょうど1件（畳んで
いないもの）であること。
"""

from __future__ import annotations

from pathlib import Path

from manor import check as check_mod
from manor import project as project_mod
from manor import task as task_mod
from manor import user as user_mod


# --- C14 --------------------------------------------------------------------------


def test_c14_silent_on_fresh_db(conn) -> None:
    task_mod.add(conn, "普通のタスク")
    project_mod.add(conn, "p1", "普通のプロジェクト")
    assert check_mod.check_c14(conn) == []


def test_c14_fires_when_task_points_to_unknown_user(conn) -> None:
    task_id = task_mod.add(conn, "壊れたタスク")
    conn.execute("UPDATE task SET user_id = 'no-such-user' WHERE id = ?", (task_id,))

    results = check_mod.check_c14(conn)
    assert any(r["id"] == task_id for r in results)


def test_c14_fires_when_project_points_to_archived_user(conn) -> None:
    partner = user_mod.add(conn, "同居人", user_id="partner")
    project_id = project_mod.add(conn, "p2", "相手のプロジェクト", user=partner)
    user_mod.archive(conn, partner)

    results = check_mod.check_c14(conn)
    assert any(r["id"] == project_id for r in results)


def test_c14_included_in_run_output(conn, home: Path) -> None:
    results = check_mod.run(conn, home)
    assert "C14" in results


# --- C15 --------------------------------------------------------------------------


def test_c15_silent_on_fresh_db(conn) -> None:
    assert check_mod.check_c15(conn) == []


def test_c15_fires_when_principal_archived(conn) -> None:
    conn.execute("UPDATE user SET archived_at = ? WHERE id = 'master'", ("2026-01-01T00:00:00",))

    results = check_mod.check_c15(conn)
    assert {r["role"] for r in results} == {"principal"}


def test_c15_fires_when_butler_archived(conn) -> None:
    conn.execute("UPDATE user SET archived_at = ? WHERE id = 'butler'", ("2026-01-01T00:00:00",))

    results = check_mod.check_c15(conn)
    assert {r["role"] for r in results} == {"butler"}


def test_c15_fires_when_second_principal_exists(conn) -> None:
    """`role` は表の CHECK で語彙は守るが「1人だけ」は機構で強制していない——
    直接 INSERT すれば2人目の principal を作れてしまう。C15 がここを拾う。
    """
    conn.execute(
        "INSERT INTO user (id, name, role, created_at) VALUES ('u2', '二人目の主人', 'principal', ?)",
        ("2026-01-01T00:00:00",),
    )

    results = check_mod.check_c15(conn)
    assert any(r["role"] == "principal" and r["count"] == 2 for r in results)


def test_c15_included_in_run_output(conn, home: Path) -> None:
    results = check_mod.run(conn, home)
    assert "C15" in results


def test_c14_c15_labels_registered() -> None:
    assert "C14" in check_mod.CHECK_LABELS
    assert "C15" in check_mod.CHECK_LABELS
    assert "C14" not in check_mod.WARNING_ONLY_CHECKS
    assert "C15" not in check_mod.WARNING_ONLY_CHECKS
