"""`task.list_tasks(..., night=True)`（N9）。夜勤の板の未着手を拾う歯止めを1コマンドに
束ねる機構の試験。B139: 列挙式（2つのコマンドを目で確かめる形）は完全性に全依存する
——歯止め漏れの実例（owner=butler が意見箱の件を弾いていた）を機構で潰す。
"""

from __future__ import annotations

from pathlib import Path

from manor import task as task_mod
from manor import user as user_mod


def test_night_only_shows_user_butler(conn) -> None:
    partner = user_mod.add(conn, "同居人", user_id="partner")
    task_mod.add(conn, "執事の件", user="butler")
    task_mod.add(conn, "主人の件", user="master")
    task_mod.add(conn, "同居人の件", user=partner)
    rows = task_mod.list_tasks(conn, night=True)
    assert {r["user_id"] for r in rows} == {"butler"}


def test_night_only_shows_status_todo(conn) -> None:
    a = task_mod.add(conn, "未着手", user="butler")
    b = task_mod.add(conn, "対応中", user="butler")
    task_mod.status(conn, b, "doing")
    rows = task_mod.list_tasks(conn, night=True)
    ids = {r["id"] for r in rows}
    assert a in ids
    assert b not in ids


def test_night_excludes_hg_and_l1(conn) -> None:
    l1 = task_mod.add(conn, "L1の件", user="butler", level="L1")
    hg = task_mod.add(conn, "HGの件", user="butler", level="HG", recommendation="既定案")
    l2 = task_mod.add(conn, "L2の件", user="butler", level="L2")
    rows = task_mod.list_tasks(conn, night=True)
    ids = {r["id"] for r in rows}
    assert l1 not in ids
    assert hg not in ids
    assert l2 in ids


def test_night_puts_idea_source_first(conn) -> None:
    normal = task_mod.add(conn, "通常起票の件", user="butler")
    idea = task_mod.add(conn, "意見箱の件", user="butler", source="idea")
    rows = task_mod.list_tasks(conn, night=True)
    ids = [r["id"] for r in rows]
    assert ids.index(idea) < ids.index(normal)


def test_night_ignores_owner(conn) -> None:
    """owner は「誰の件か」ではなく「誰が手を動かすか」——夜勤の歯止めでは見ない。"""
    task_id = task_mod.add(conn, "主人の指示・執事が実装", owner="master", user="butler")
    rows = task_mod.list_tasks(conn, night=True)
    assert task_id in {r["id"] for r in rows}
