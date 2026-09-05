"""`manor init --demo`（合成データ）の試験。ROADMAP §8 6f・ADR-004 D10。

すべて `tests/conftest.py` の `home_path` fixture（一時ディレクトリへ向けた `MANOR_HOME`）
の中で完結する。本物の `home/` には一切触れない。
"""

from __future__ import annotations

import json
from pathlib import Path

from manor import cli
from manor import db as db_mod
from manor import demo as demo_mod

EXPECTED_COUNTS = {
    "project": 3,
    "task": 12,
    "milestone": 2,
    "chef_pantry": 6,
    "chef_shopping": 3,
    "chef_meal": 4,
    "housekeeper_chore": 3,
    "housekeeper_supply": 3,
    "housekeeper_waste": 2,
    "steward_expense": 6,
    "steward_recurring": 2,
    "steward_budget": 2,
    "secretary_reminder": 2,
    "secretary_event": 2,
}


def test_seed_on_empty_home_inserts_synthetic_data(home_path: Path):
    db_mod.init(home_path)
    conn = db_mod.connect(home_path)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM task").fetchone()["n"] == 0
    finally:
        conn.close()

    counts = demo_mod.seed(home_path)
    assert counts == EXPECTED_COUNTS

    conn = db_mod.connect(home_path)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM task").fetchone()["n"] == 12
        statuses = {r["status"] for r in conn.execute("SELECT DISTINCT status FROM task").fetchall()}
        assert {"todo", "doing", "waiting", "hold", "resident", "done"} <= statuses

        # 1件は HG＋open decision で「要対応」が1件出る
        hg_open = conn.execute(
            "SELECT COUNT(*) AS n FROM task WHERE level = 'HG'"
        ).fetchone()["n"]
        assert hg_open == 1
        open_decisions = conn.execute(
            "SELECT COUNT(*) AS n FROM decision WHERE status = 'open'"
        ).fetchone()["n"]
        assert open_decisions == 1

        # depends_on と duplicates の辺
        assert (
            conn.execute("SELECT COUNT(*) AS n FROM edge WHERE rel = 'depends_on'").fetchone()["n"] >= 1
        )
        assert (
            conn.execute("SELECT COUNT(*) AS n FROM edge WHERE rel = 'duplicates'").fetchone()["n"] >= 1
        )
        # duplicates の src は withdrawn（C6 が拾う不整合を自分でも踏まない）
        dup_src_status = conn.execute(
            "SELECT t.status FROM edge e JOIN task t ON t.id = e.src WHERE e.rel = 'duplicates'"
        ).fetchone()["status"]
        assert dup_src_status == "withdrawn"

        # 主人にも owner の作業がある（board の doing_master のデモ）
        assert (
            conn.execute(
                "SELECT COUNT(*) AS n FROM task WHERE status = 'doing' AND owner = 'master'"
            ).fetchone()["n"]
            == 1
        )

        for table, expected in EXPECTED_COUNTS.items():
            if table in ("project", "task", "milestone"):
                continue
            n = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            assert n == expected, f"{table}: {n} != {expected}"
    finally:
        conn.close()


def test_seed_is_idempotent_on_nonempty_home(home_path: Path):
    db_mod.init(home_path)
    first = demo_mod.seed(home_path)
    assert first  # 空の home では何かを入れる

    second = demo_mod.seed(home_path)
    assert second == {}  # 2回目は「空の home でだけ使えます」相当。何も足さない

    conn = db_mod.connect(home_path)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM task").fetchone()["n"] == 12
        assert conn.execute("SELECT COUNT(*) AS n FROM project").fetchone()["n"] == 3
    finally:
        conn.close()


def test_seed_skips_when_home_already_has_tasks(home_path: Path):
    """デモ以外の経緯で既にタスクが入っている home にも `--demo` は書き足さない。"""
    db_mod.init(home_path)
    from manor import project as project_mod
    from manor import task as task_mod

    conn = db_mod.connect(home_path)
    try:
        project_mod.add(conn, "X", "既存のプロジェクト")
        task_mod.add(conn, "既存のタスク", project="X")
        conn.commit()
    finally:
        conn.close()

    counts = demo_mod.seed(home_path)
    assert counts == {}

    conn = db_mod.connect(home_path)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM task").fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM project").fetchone()["n"] == 1
    finally:
        conn.close()


def test_init_demo_cli_reports_counts_and_second_run_is_noop(home_path: Path, capsys):
    assert cli.main(["init", "--demo", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["demo_skipped"] is False
    assert out["demo"] == EXPECTED_COUNTS

    assert cli.main(["init", "--demo", "--json"]) == 0
    out2 = json.loads(capsys.readouterr().out)
    assert out2["demo_skipped"] is True
    assert out2["demo"] == {}


def test_init_without_demo_does_not_seed(home_path: Path, capsys):
    assert cli.main(["init", "--json"]) == 0
    capsys.readouterr()
    conn = db_mod.connect(home_path)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM task").fetchone()["n"] == 0
    finally:
        conn.close()


def test_manor_check_passes_after_demo(home_path: Path, capsys):
    assert cli.main(["init", "--demo"]) == 0
    capsys.readouterr()
    assert cli.main(["check", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True, result["results"]


def test_manor_active_runs_after_demo(home_path: Path, capsys):
    assert cli.main(["init", "--demo"]) == 0
    capsys.readouterr()
    assert cli.main(["active"]) == 0
    text = capsys.readouterr().out
    assert text.strip() != ""


def test_board_api_ok_after_demo(home_path: Path, capsys):
    from fastapi.testclient import TestClient

    from manor.board import app as board_app_mod

    assert cli.main(["init", "--demo"]) == 0
    capsys.readouterr()

    app = board_app_mod.create_app(home_path)
    client = TestClient(app)
    res = client.get("/api/board")
    assert res.status_code == 200
    body = res.json()
    assert body["counts"]["pending"] == 1
    assert body["counts"]["done_total"] == 2
    assert len(body["pending"]) == 1
    assert len(body["projects"]) == 3
