"""render → sha256 記録 → 手で書き換え → check の C7 が見つける（ADR-001 §5・§9）。"""

from __future__ import annotations

from pathlib import Path

from manor import check as check_mod
from manor import project as project_mod
from manor import render as render_mod
from manor import task as task_mod


def test_render_writes_projection_files_and_records_sha(conn, home: Path):
    written = render_mod.render(conn, home)
    assert set(written) == {
        "projections/QUEUE.md", "projections/PROJECTS.md", "STATE.md", "projections/PROFILE.md",
    }
    for rel in written:
        path = home / rel
        assert path.is_file()
        text = path.read_text(encoding="utf-8")
        assert "自動生成。編集しないでください" in text
        row = conn.execute(
            "SELECT value FROM meta WHERE key = ?", (f"render_sha256:{rel}",)
        ).fetchone()
        assert row is not None


def test_render_reflects_current_db_state(conn, home: Path):
    project_mod.add(conn, "demo", "デモ計画")
    tid = task_mod.add(conn, "設計を書く", project="demo")
    render_mod.render(conn, home)
    queue_text = (home / "projections" / "QUEUE.md").read_text(encoding="utf-8")
    assert tid in queue_text
    projects_text = (home / "projections" / "PROJECTS.md").read_text(encoding="utf-8")
    assert "demo" in projects_text


def test_active_text_shows_night_pause_first(conn, home: Path):
    """N8: 夜勤の一時停止が、起動時の射影（`manor active`）の先頭に出ること。"""
    from manor.night import runner as night_runner

    night_runner.pause(home, until="2026-09-20", reason="主人のご指示")
    text = render_mod.active_text(conn)
    assert text.splitlines()[0] == "夜勤: 停止中（〜2026-09-20・主人のご指示）"


def test_active_text_has_no_night_pause_line_when_not_paused(conn, home: Path):
    text = render_mod.active_text(conn)
    assert "夜勤: 停止中" not in text


def test_check_passes_right_after_render(conn, home: Path):
    task_mod.add(conn, "設計を書く")
    render_mod.render(conn, home)
    results = check_mod.run(conn, home)
    assert check_mod.ok(results)


def test_check_c7_detects_hand_edit(conn, home: Path):
    task_mod.add(conn, "設計を書く")
    render_mod.render(conn, home)
    queue_path = home / "projections" / "QUEUE.md"
    # 「手で書いたら C7 が見つける」。射影ファイルを直接書き換える。
    queue_path.write_text(queue_path.read_text(encoding="utf-8") + "\n手で足した行\n", encoding="utf-8")

    results = check_mod.run(conn, home)
    assert not check_mod.ok(results)
    assert results["C7"], "C7 が手編集を検出しなければならない"
    files_flagged = {item["file"] for item in results["C7"]}
    assert "projections/QUEUE.md" in files_flagged


def test_check_c7_silent_before_first_render(conn, home: Path):
    # まだ render していない DB を検査しても誤検出にしない
    results = check_mod.run(conn, home)
    assert results["C7"] == []


def test_active_text_shows_idea_waiting_in_section_a(conn, home: Path):
    """N10: 意見箱（source=idea）の waiting が、A. 主人待ちへ確認待ちとして出る。"""
    tid = task_mod.add(conn, "タスク一覧の状態をドロップダウンで選べるように", source="idea")
    task_mod.status(conn, tid, "waiting", note="タスク一覧の各行に状態のドロップダウンが付きました")
    text = render_mod.active_text(conn)
    assert f"意見箱のご確認待ち: {tid}" in text
    assert "タスク一覧の各行に状態のドロップダウンが付きました" in text


def test_active_text_no_idea_waiting_line_when_absent(conn, home: Path):
    text = render_mod.active_text(conn)
    assert "意見箱のご確認待ち" not in text


def test_active_text_ignores_non_idea_waiting(conn, home: Path):
    """source=idea でない waiting は意見箱の行として出さない。"""
    tid = task_mod.add(conn, "普通のタスク")
    task_mod.status(conn, tid, "waiting", note="何かを待っている")
    text = render_mod.active_text(conn)
    assert "意見箱のご確認待ち" not in text
