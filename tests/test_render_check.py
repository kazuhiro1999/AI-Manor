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
