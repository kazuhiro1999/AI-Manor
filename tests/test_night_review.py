"""朝の点検（2026-09-08・主人のご要望）。

> 今は毎朝、私が夜間タスクの様子を執事に聞いて処理してもらってるので、
> その部分を自動化したいです。……保留になったら朝チェックで直すとか
> 許可道具を増やすとか、その辺まで自律実行できるようにしてほしいです。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from manor import cli
from manor import db
from manor.night import review as review_mod
from manor.night import runner

REPORT = """# 夜勤の作業報告 {date}

## N1 片付いた仕事

- **やったこと**: 直しました
- **どこまで**: 完了

## N2 片付かない仕事

- **やったこと**: 調べました
- **どこまで**: 保留。道具が足りません
"""


def _setup(home: Path, date: str, *, denials: list | None = None) -> None:
    rd = runner.reports_dir(home)
    rd.mkdir(parents=True, exist_ok=True)
    (rd / f"{date}.md").write_text(REPORT.format(date=date), encoding="utf-8")
    last = {
        "status": "done",
        "started_at": f"{date}T02:00:00",
        "exit_code": 0,
        "killed": False,
        "attempts": 1,
    }
    if denials:
        last["permission_denials"] = denials
    runner.last_run_path(home).write_text(json.dumps(last, ensure_ascii=False), encoding="utf-8")


def test_review_picks_up_only_the_pending_sections(home_path: Path) -> None:
    """「どこまで」の行だけを読む。片付いた節は拾わない。"""
    assert cli.main(["init"]) == 0
    today = datetime.now().date().isoformat()
    _setup(home_path, today)

    result = runner.review(home_path, date=today, record=False)
    headings = [p["heading"] for p in result["items"]["pending"]]
    assert headings == ["N2 片付かない仕事"]
    assert result["items"]["states"] == 2, "状態行を2つとも読めていない"


def test_review_counts_the_streak_across_nights(home_path: Path) -> None:
    """同じ保留が何晩続いたかを数える。**1晩ずつしか増えない**（同じ日に2度回しても増えない）。"""
    assert cli.main(["init"]) == 0
    for n, date in enumerate(("2026-09-06", "2026-09-07", "2026-09-08"), start=1):
        _setup(home_path, date)
        result = runner.review(home_path, date=date)
        assert result["streaks"]["N2 片付かない仕事"] == n

    again = runner.review(home_path, date="2026-09-08")
    assert again["streaks"]["N2 片付かない仕事"] == 3, "同じ日に2度回して連続が増えている"


def test_review_asks_only_after_three_nights(home_path: Path) -> None:
    """1晩・2晩では黙る。3晩目に初めて主人へ伺う（B100「0件のときは黙る」）。"""
    assert cli.main(["init"]) == 0
    conn = db.connect(home_path)

    for date in ("2026-09-06", "2026-09-07"):
        _setup(home_path, date)
        assert review_mod.run(conn, home_path, date=date)["asked"] == []

    _setup(home_path, "2026-09-08")
    third = review_mod.run(conn, home_path, date="2026-09-08")
    assert third["asked"], "3晩続いたのに伺いを立てていない"
    conn.commit()

    row = conn.execute(
        "SELECT status FROM decision WHERE id = ?", (third["asked"][0],)
    ).fetchone()
    assert row["status"] == "open"


def test_review_does_not_ask_twice(home_path: Path) -> None:
    """伺いが open のまま残っているうちは、毎朝立て直さない。"""
    assert cli.main(["init"]) == 0
    conn = db.connect(home_path)
    for date in ("2026-09-06", "2026-09-07", "2026-09-08"):
        _setup(home_path, date)
        review_mod.run(conn, home_path, date=date)
    conn.commit()

    _setup(home_path, "2026-09-09")
    assert review_mod.run(conn, home_path, date="2026-09-09")["asked"] == []
    count = conn.execute("SELECT COUNT(*) AS n FROM decision").fetchone()["n"]
    assert count == 1, f"伺いが {count} 件に増えている"


def test_review_carries_the_denied_tools_into_the_evidence(home_path: Path) -> None:
    """拒まれた道具を根拠に載せる——「何の許可が要るか」が主人の画面で分かるように。"""
    assert cli.main(["init"]) == 0
    conn = db.connect(home_path)
    denials = [{"tool_name": "Bash", "tool_input": {"command": "ls -1"}}]
    for date in ("2026-09-06", "2026-09-07", "2026-09-08"):
        _setup(home_path, date, denials=denials)
        result = review_mod.run(conn, home_path, date=date)
    conn.commit()

    row = conn.execute(
        "SELECT evidence FROM decision WHERE id = ?", (result["asked"][0],)
    ).fetchone()
    assert "Bash" in str(row["evidence"])


def test_review_notices_a_changed_report_format(home_path: Path) -> None:
    """節はあるのに「どこまで」が1つも無い＝書式が変わった（B188 の再発防止）。

    語で判定する仕組みは、語が変わった日に黙る。**黙ったことに気づける**ようにする。
    """
    assert cli.main(["init"]) == 0
    today = datetime.now().date().isoformat()
    rd = runner.reports_dir(home_path)
    rd.mkdir(parents=True, exist_ok=True)
    (rd / f"{today}.md").write_text("# 報告\n\n## N1 なにか\n\n- 状態: 完了\n", encoding="utf-8")
    runner.last_run_path(home_path).write_text(
        json.dumps({"status": "done", "started_at": f"{today}T02:00:00"}), encoding="utf-8"
    )

    result = runner.review(home_path, date=today, record=False)
    assert result["health"]["ok"] is False
    assert any("どこまで" in r for r in result["health"]["reasons"])
