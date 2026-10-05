"""夜勤が朝に何も残さない仕組み（主人 2026-10-06）。

> 朝に夜勤が出した判断待ちが並ぶが、意味があるか分からないもの・関心の無いものに
> 思考を割かれるのが苦。……許可が無い等で夜勤中に終えられなかったり、中途半端な
> ままずっと残っているのが気になっていた。

1. 夜勤の伺いは3日返事が無ければ見送りにする（`review.expire_night_decisions`）
2. 前夜の自分の未コミットを、翌晩の指示の先頭に差し込む（`runner.own_leftovers`）
3. 「完了（…保留ではない）」を片付かなかった件と読み違えない
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from manor import cli
from manor import db
from manor import decision as decision_mod
from manor import task as task_mod
from manor.night import review as review_mod
from manor.night import runner


# --- 1. 夜勤の伺いは3日で見送り ------------------------------------------------------


def _night_decision(conn, *, asked_at: str, asked_by: str = "night") -> tuple[str, str]:
    task_id = task_mod.add(conn, "夜勤が積んだ伺いの試験")
    did = decision_mod.ask(
        conn, "夜勤が積んだ伺いの試験", task_id=task_id, recommend="そうする", background="背景",
        asked_by=asked_by,
    )
    conn.execute("UPDATE decision SET asked_at = ? WHERE id = ?", (asked_at, did))
    conn.commit()
    return did, task_id


def test_night_decisions_lapse_after_three_days(home_path: Path) -> None:
    assert cli.main(["init"]) == 0
    conn = db.connect(home_path)
    now = datetime(2026, 10, 10, 7, 30)
    old, old_task = _night_decision(conn, asked_at=(now - timedelta(days=3, minutes=1)).isoformat())
    fresh, _ = _night_decision(conn, asked_at=(now - timedelta(days=2)).isoformat())

    assert review_mod.expire_night_decisions(conn, now=now) == [old]

    row = conn.execute("SELECT status, actor FROM decision WHERE id = ?", (old,)).fetchone()
    assert (row["status"], row["actor"]) == ("rejected", "timeout")
    status = conn.execute("SELECT status FROM task WHERE id = ?", (old_task,)).fetchone()["status"]
    assert status == "hold", "todo のままだと翌晩の夜勤がまた拾って積み直す"
    still = conn.execute("SELECT status FROM decision WHERE id = ?", (fresh,)).fetchone()["status"]
    assert still == "open"


def test_decisions_from_the_master_or_daytime_butler_never_lapse(home_path: Path) -> None:
    assert cli.main(["init"]) == 0
    conn = db.connect(home_path)
    now = datetime(2026, 10, 10, 7, 30)
    for who in ("cli", "butler", "web", ""):
        _night_decision(conn, asked_at=(now - timedelta(days=30)).isoformat(), asked_by=who)

    assert review_mod.expire_night_decisions(conn, now=now) == []


def test_dry_run_counts_without_writing(home_path: Path) -> None:
    assert cli.main(["init"]) == 0
    conn = db.connect(home_path)
    now = datetime(2026, 10, 10, 7, 30)
    did, _ = _night_decision(conn, asked_at=(now - timedelta(days=5)).isoformat())

    assert review_mod.expire_night_decisions(conn, now=now, record=False) == [did]
    assert conn.execute("SELECT status FROM decision WHERE id = ?", (did,)).fetchone()["status"] == "open"


def test_decision_ask_takes_the_night_mark_from_the_environment(home_path: Path, monkeypatch) -> None:
    """runner は子プロセスへ `MANOR_ACTOR=night` を渡す。CLI はそれを起票者として残す。"""
    assert cli.main(["init"]) == 0
    conn = db.connect(home_path)
    task_id = task_mod.add(conn, "環境変数の試験")
    conn.commit()
    monkeypatch.setenv("MANOR_ACTOR", "night")

    assert cli.main([
        "decision", "ask", "環境変数の試験", "--task", task_id,
        "--recommend", "そうする", "--background", "背景",
    ]) == 0

    conn = db.connect(home_path)
    row = conn.execute("SELECT asked_by FROM decision ORDER BY rowid DESC LIMIT 1").fetchone()
    assert row["asked_by"] == "night"


# --- 2. 前夜の自分の残り ------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo_with_change(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "src" / "b.py").write_text("y = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    (repo / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
    (repo / "src" / "b.py").write_text("y = 2\n", encoding="utf-8")
    return repo


def _write_prev_run(home: Path, *, ended_at: datetime, uncommitted: list[str]) -> None:
    last = {"status": "done", "ended_at": ended_at.isoformat(), "gate": {"uncommitted": uncommitted}}
    runner.last_run_path(home).parent.mkdir(parents=True, exist_ok=True)
    runner.last_run_path(home).write_text(json.dumps(last, ensure_ascii=False), encoding="utf-8")


def test_own_leftovers_are_files_nobody_touched_since_last_night(home_path: Path, tmp_path: Path) -> None:
    repo = _repo_with_change(tmp_path)
    ended = datetime.now()
    _write_prev_run(home_path, ended_at=ended, uncommitted=[" M src/a.py", " M src/b.py", " M docs/x.md"])
    # b.py は昼に主人か執事が触った（前夜の終わりより後）
    later = (ended + timedelta(hours=5)).timestamp()
    os.utime(repo / "src" / "b.py", (later, later))
    past = (ended - timedelta(minutes=5)).timestamp()
    os.utime(repo / "src" / "a.py", (past, past))

    assert runner.own_leftovers(home_path, repo) == ["src/a.py"]


def test_committed_leftovers_are_not_reported(home_path: Path, tmp_path: Path) -> None:
    repo = _repo_with_change(tmp_path)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "daytime commit")
    _write_prev_run(home_path, ended_at=datetime.now() + timedelta(minutes=5), uncommitted=[" M src/a.py"])

    assert runner.own_leftovers(home_path, repo) == []


def test_no_previous_run_means_no_leftovers(home_path: Path, tmp_path: Path) -> None:
    repo = _repo_with_change(tmp_path)
    assert runner.own_leftovers(home_path, repo) == []


def test_leftover_block_names_the_files() -> None:
    block = runner.build_leftover_block(["src/a.py"])
    assert "前夜のあなたの残り" in block
    assert "`src/a.py`" in block


# --- 3. 「完了」の行を片付かなかった件と読まない ---------------------------------------


def test_done_line_mentioning_hold_is_not_pending(home_path: Path) -> None:
    assert cli.main(["init"]) == 0
    date = "2026-10-05"
    rd = runner.reports_dir(home_path)
    rd.mkdir(parents=True, exist_ok=True)
    (rd / f"{date}.md").write_text(
        "# 夜勤の作業報告\n\n## N1 見張り\n\n- **どこまで**: 完了（触れない理由を記録済み。保留ではない）。\n",
        encoding="utf-8",
    )
    assert runner.pending_items(home_path, date)["pending"] == []
