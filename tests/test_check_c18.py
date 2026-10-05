"""check.py の C18 と `project.open_tasks_for` — project を `status=done` で畳んだのに、
紐づく task が done/withdrawn でないまま残っているもの（T85。2026-09-20 実測: P1・P3 を
畳んだとき、常駐タスク B27・B44 が放置され警告も出ず、執事が手で取り下げた）。
"""

from __future__ import annotations

import json
from pathlib import Path

from manor import check as check_mod
from manor import cli
from manor import project as project_mod
from manor import task as task_mod


def test_c18_fires_when_project_done_but_task_open(conn, home: Path) -> None:
    project_id = project_mod.add(conn, "PZ1", "畳むプロジェクト")
    task_id = task_mod.add(conn, "残ったタスク", project=project_id)
    project_mod.set(conn, project_id, status="done")

    results = check_mod.run(conn, home)

    assert task_id in {r["id"] for r in results["C18"]}
    assert check_mod.ok(results) is False


def test_c18_silent_once_tasks_are_closed(conn, home: Path) -> None:
    project_id = project_mod.add(conn, "PZ2", "畳むプロジェクト2")
    done_id = task_mod.add(conn, "済んだタスク", project=project_id)
    gone_id = task_mod.add(conn, "取り下げたタスク", project=project_id)
    task_mod.status(conn, done_id, "doing")
    task_mod.status(conn, done_id, "done")
    task_mod.status(conn, gone_id, "withdrawn", note="畳むので")
    project_mod.set(conn, project_id, status="done")

    results = check_mod.run(conn, home)

    assert not {done_id, gone_id} & {r["id"] for r in results["C18"]}


def test_c18_silent_while_project_active(conn, home: Path) -> None:
    project_id = project_mod.add(conn, "PZ3", "動いているプロジェクト")
    task_id = task_mod.add(conn, "途中のタスク", project=project_id)

    results = check_mod.run(conn, home)

    assert task_id not in {r["id"] for r in results["C18"]}


def test_open_tasks_for_lists_only_unclosed(conn) -> None:
    project_id = project_mod.add(conn, "PZ4", "数えるプロジェクト")
    open_id = task_mod.add(conn, "未完了", project=project_id)
    done_id = task_mod.add(conn, "完了", project=project_id)
    task_mod.status(conn, done_id, "doing")
    task_mod.status(conn, done_id, "done")

    rows = project_mod.open_tasks_for(conn, project_id)

    assert [r["id"] for r in rows] == [open_id]
    assert rows[0]["title"] == "未完了"


def test_cli_project_set_done_warns_about_open_tasks(conn, home: Path, capsys) -> None:
    """T85①: `manor project set --status done` が、未完了 task を列挙して警告する。"""
    project_id = project_mod.add(conn, "PZ5", "CLIで畳むプロジェクト")
    task_id = task_mod.add(conn, "残ったタスク", project=project_id)
    conn.commit()

    assert cli.main(["project", "set", "PZ5", "--status", "done", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [t["id"] for t in out["open_tasks"]] == [task_id]


def test_cli_project_set_done_silent_when_tasks_closed(conn, home: Path, capsys) -> None:
    project_id = project_mod.add(conn, "PZ6", "CLIで畳むプロジェクト2")
    task_id = task_mod.add(conn, "済んだタスク", project=project_id)
    task_mod.status(conn, task_id, "doing")
    task_mod.status(conn, task_id, "done")
    conn.commit()

    assert cli.main(["project", "set", "PZ6", "--status", "done", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "open_tasks" not in out
