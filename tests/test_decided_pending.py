"""主人の裁定（approved/modified）が下りたのに、decided_by 先の task が todo のまま
着手されていない件——`manor active` の射影と Slack ブリーフィングの両方に出ること
（T46・外部視点E31。`check.py` の C17 と同じ条件。試験は `tests/test_check_c17.py`）。
"""

from __future__ import annotations

from pathlib import Path

from manor import decision as decision_mod
from manor import render as render_mod
from manor import slack as slack_mod
from manor import task as task_mod


def _decided_todo_task(conn, title: str, *, verdict: str = "approved") -> tuple[str, str]:
    task_id = task_mod.add(conn, title)
    decision_id = decision_mod.ask(
        conn, f"{title}の裁定", task_id=task_id, recommend="案", background="背景"
    )
    ruling = "こう直して" if verdict == "modified" else ""
    decision_mod.rule(conn, decision_id, verdict, ruling=ruling)
    return task_id, decision_id


def test_active_data_carries_decided_pending(conn, home: Path) -> None:
    task_id, decision_id = _decided_todo_task(conn, "板のタスクA")

    data = render_mod.active_data(conn)

    ids = {t["id"] for t in data["decided_pending"]}
    assert task_id in ids


def test_format_active_shows_the_section(conn, home: Path) -> None:
    task_id, decision_id = _decided_todo_task(conn, "板のタスクB")

    text = render_mod.format_active(render_mod.active_data(conn))

    assert "主人の裁定（未着手）" in text
    assert task_id in text
    assert decision_id in text


def test_format_active_silent_without_pending_decisions(conn, home: Path) -> None:
    task_mod.add(conn, "普通のタスク")

    text = render_mod.format_active(render_mod.active_data(conn))

    assert "主人の裁定（未着手）" not in text


def test_slack_brief_shows_decided_pending(conn, home: Path) -> None:
    task_id, decision_id = _decided_todo_task(conn, "板のタスクC")

    text = slack_mod.format_mechanical_brief(slack_mod.brief_data(conn, home))

    assert "裁定済み・未着手" in text
    assert task_id in text


def test_slack_brief_silent_without_pending_decisions(conn, home: Path) -> None:
    task_mod.add(conn, "普通のタスク2")

    text = slack_mod.format_mechanical_brief(slack_mod.brief_data(conn, home))

    assert "裁定済み・未着手" not in text
