"""`board/api_core.py` の `user_id` 絞り（ADR-014 D4）の試験。**合成データのみ**
（架空の家庭。人名は入らない）。

`get_board(conn, user_id=)` を直接呼ぶ（HTTP は経由しない。段Bが cookie から
`user_id` を渡す配線をする）。`user_id=None` は今までどおり全部、という既存の
契約は他の試験（`tests/board/`）が守っているので、ここでは絞りの規則だけを確かめる。
"""

from __future__ import annotations

from manor import decision as decision_mod
from manor import graph
from manor import project as project_mod
from manor import task as task_mod
from manor import user as user_mod
from manor.board import api_core


def _ask_unlinked(conn, title: str) -> str:
    """`decided_by` の辺で結ばれていない open decision を1件作る（`decision.ask` は必ず
    task を要求するので、ここでは直に組む）。ADR-014 D4「task に結ばれていない裁定
    （夜勤の点検が積むもの等）は主人のもの」を試すためのヘルパー。
    """
    from manor import util as util_mod

    decision_id = graph.create_node(conn, kind="decision", title=title, id_prefix="D")
    conn.execute(
        "INSERT INTO decision (id, status, recommendation, background, asked_at)"
        " VALUES (?, 'open', '承認する', '', ?)",
        (decision_id, util_mod.now()),
    )
    return decision_id


def test_master_desk_excludes_butler_project_tasks(conn) -> None:
    butler_project = project_mod.add(conn, "butlerproj", "執事プロジェクト", kind=project_mod.BUTLER_PROJECT_KIND)
    task_mod.add(conn, "執事の件", project=butler_project, owner="butler")
    master_task = task_mod.add(conn, "主人の件", owner="master")

    board = api_core.get_board(conn, user_id="master")
    ids = {t["id"] for t in board["tasks"]}
    assert master_task in ids
    assert not any(t["project_id"] == butler_project for t in board["tasks"])


def test_butler_desk_includes_butler_project_tasks(conn) -> None:
    butler_project = project_mod.add(conn, "butlerproj", "執事プロジェクト", kind=project_mod.BUTLER_PROJECT_KIND)
    butler_task = task_mod.add(conn, "執事の件", project=butler_project, owner="butler")
    task_mod.add(conn, "主人の件", owner="master")

    board = api_core.get_board(conn, user_id="butler")
    ids = {t["id"] for t in board["tasks"]}
    assert butler_task in ids
    assert not any(t["user_id"] == "master" for t in board["tasks"])


def test_pending_shows_everything_to_principal(conn) -> None:
    butler_task = task_mod.add(conn, "執事の件", owner="butler")
    decision_mod.ask(conn, "執事の裁定", task_id=butler_task, recommend="承認する", background="")
    # task に結ばれていない裁定（夜勤の点検が積むもの等）も主人のもの。
    _ask_unlinked(conn, "結ばれていない裁定")

    board = api_core.get_board(conn, user_id="master")
    assert len(board["pending"]) == 2


def test_pending_shows_only_own_tasks_decisions_to_others(conn) -> None:
    butler_task = task_mod.add(conn, "執事の件", owner="butler")
    master_task = task_mod.add(conn, "主人の件", owner="master")
    decision_mod.ask(conn, "執事の裁定", task_id=butler_task, recommend="承認する", background="")
    decision_mod.ask(conn, "主人の裁定", task_id=master_task, recommend="承認する", background="")
    _ask_unlinked(conn, "結ばれていない裁定")

    board = api_core.get_board(conn, user_id="butler")
    assert len(board["pending"]) == 1
    assert board["pending"][0]["title"] == "執事の裁定"


def test_pending_for_partner_only_their_own(conn) -> None:
    partner = user_mod.add(conn, "同居人", user_id="partner")
    partner_task = task_mod.add(conn, "相手の件", owner="master", user=partner)
    master_task = task_mod.add(conn, "主人の件", owner="master")
    decision_mod.ask(conn, "相手の裁定", task_id=partner_task, recommend="承認する", background="")
    decision_mod.ask(conn, "主人の裁定", task_id=master_task, recommend="承認する", background="")

    board = api_core.get_board(conn, user_id=partner)
    assert len(board["pending"]) == 1
    assert board["pending"][0]["title"] == "相手の裁定"


def test_user_id_none_matches_unfiltered_baseline(conn) -> None:
    butler_project = project_mod.add(conn, "butlerproj", "執事プロジェクト", kind=project_mod.BUTLER_PROJECT_KIND)
    task_mod.add(conn, "執事の件", project=butler_project, owner="butler")
    master_task = task_mod.add(conn, "主人の件", owner="master")
    decision_mod.ask(conn, "何かの裁定", task_id=master_task, recommend="承認する", background="")

    filtered_default = api_core.get_board(conn)
    filtered_explicit_none = api_core.get_board(conn, user_id=None)
    assert filtered_default["counts"] == filtered_explicit_none["counts"]
    assert len(filtered_default["tasks"]) == 2
    assert len(filtered_default["pending"]) == 1


def test_projects_and_milestones_filtered_by_user(conn) -> None:
    from manor import graph

    partner = user_mod.add(conn, "同居人", user_id="partner")
    master_project = project_mod.add(conn, "pm", "主人のプロジェクト")
    partner_project = project_mod.add(conn, "pp", "相手のプロジェクト", user=partner)
    graph.milestone_add(conn, "主人の節目", date="2026-12-01", project_id=master_project)
    graph.milestone_add(conn, "相手の節目", date="2026-12-01", project_id=partner_project)

    board = api_core.get_board(conn, user_id=partner)
    codes = {p["code"] for p in board["projects"]}
    assert codes == {"pp"}
    titles = {m["title"] for m in board["milestones"]}
    assert titles == {"相手の節目"}


def test_counts_reflect_the_filtered_view(conn) -> None:
    butler_project = project_mod.add(conn, "butlerproj", "執事プロジェクト", kind=project_mod.BUTLER_PROJECT_KIND)
    task_mod.add(conn, "執事の件", project=butler_project, owner="butler")
    task_mod.status(conn, "T1", "doing")
    task_mod.add(conn, "主人の件", owner="master")

    board_master = api_core.get_board(conn, user_id="master")
    board_butler = api_core.get_board(conn, user_id="butler")
    assert board_master["counts"]["doing"] == 0
    assert board_butler["counts"]["doing"] == 1
