"""節目（milestone）の試験。**合成データのみ**。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manor import check
from manor import cli
from manor import db
from manor import graph
from manor import project as project_mod


# --- 節目の「済んだ」（執事の裁定 2026-09-05）---------------------------------------


def test_milestone_done_does_not_touch_the_date(home_path: Path) -> None:
    """済みにしても**日付は書き換えない**。

    主人「予備審査の発表練習は終わったので完了に」——節目に状態が無かったので、C8 を
    黙らせる唯一の道が「日付を書き換える」（履歴を偽る）だった。表せない事実があるなら
    機構のほうを直す、という判断でこの列を足した。
    """
    assert cli.main(["init"]) == 0
    conn = db.connect(home_path)
    mid = graph.milestone_add(conn, "発表練習", date="2026-09-03")
    conn.commit()

    assert graph.milestone_done(conn, mid) is True
    conn.commit()

    row = conn.execute("SELECT date, done_at FROM milestone WHERE id = ?", (mid,)).fetchone()
    assert row["date"] == "2026-09-03", "日付が書き換わっている"
    assert row["done_at"], "済んだ日時が入っていない"


def test_milestone_done_is_idempotent_and_undoable(home_path: Path) -> None:
    """二度押しても変わらず、取り消せる（押し間違いの戻し道が無いと主人が直せない）。"""
    assert cli.main(["init"]) == 0
    conn = db.connect(home_path)
    mid = graph.milestone_add(conn, "発表練習", date="2026-09-03")
    conn.commit()

    assert graph.milestone_done(conn, mid) is True
    assert graph.milestone_done(conn, mid) is False, "二度目で変わってしまっている"
    assert graph.milestone_undone(conn, mid) is True
    assert graph.milestone_undone(conn, mid) is False
    conn.commit()
    assert conn.execute("SELECT done_at FROM milestone WHERE id = ?", (mid,)).fetchone()["done_at"] is None


def test_milestone_done_on_a_missing_id_is_not_an_error(home_path: Path) -> None:
    """存在しない id でも例外にしない（画面から二重に押した後などに落ちない）。"""
    assert cli.main(["init"]) == 0
    conn = db.connect(home_path)
    assert graph.milestone_done(conn, "M999") is False
    assert graph.milestone_undone(conn, "M999") is False


def test_c8_stops_complaining_once_the_milestone_is_done(home_path: Path) -> None:
    """C8 は「済んでいない」過去の節目だけを鳴らす。"""
    assert cli.main(["init"]) == 0
    conn = db.connect(home_path)
    pid = project_mod.add(conn, "research", "研究")
    mid = graph.milestone_add(conn, "発表練習", date="2026-09-03", project_id=pid)
    conn.commit()

    assert [r["id"] for r in check.check_c8(conn)] == [mid], "過ぎた節目を鳴らしていない"

    graph.milestone_done(conn, mid)
    conn.commit()
    assert check.check_c8(conn) == [], "済んだのに鳴り続けている"


def test_cli_milestone_done_and_list_shows_it(
    home_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """`manor milestone done <id>` と、一覧に「済」が出ること。"""
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    assert cli.main(["milestone", "add", "発表練習", "--date", "2026-09-03", "--json"]) == 0
    mid = json.loads(capsys.readouterr().out)["id"]

    assert cli.main(["milestone", "done", mid]) == 0
    assert "済み" in capsys.readouterr().out

    assert cli.main(["milestone", "list"]) == 0
    assert "済 " in capsys.readouterr().out
