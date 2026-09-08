"""昨夜ちゃんと走ったかを、朝の便が自分で見ること（2026-09-08）。

主人のご質問「夜間タスクのチェックは朝に自動実行するようになってますか？」。
定例4本（夜勤 02:00 / 日誌 06:45 / ブリーフィング 07:30 / 取り込み 5分ごと）は
動いていたが、ブリーフィングの【昨夜の作業】は**報告ファイルがあれば読む**形で、
無い場合の1行は「起動しなかった / 落ちた / 指示が空だった」を区別しなかった。
9/5 に夜勤がプロセスごと消えた晩、気づいたのは**翌々日の夜勤自身**。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from manor import cli
from manor import db
from manor import slack as slack_mod
from manor.night import runner


def _write_last_run(home: Path, **fields: object) -> None:
    base = {
        "status": "done",
        "started_at": datetime.now().isoformat(),
        "exit_code": 0,
        "killed": False,
        "attempts": 1,
    }
    base.update(fields)
    path = runner.last_run_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")


def _write_report(home: Path, date: str) -> None:
    rd = runner.reports_dir(home)
    rd.mkdir(parents=True, exist_ok=True)
    (rd / f"{date}.md").write_text("# 夜勤の作業報告\n\n## N1 なにか\n\n- やりました", encoding="utf-8")


def test_health_is_quiet_when_the_night_went_well(home_path: Path) -> None:
    """正常な晩は黙る（B100「0件のときは黙る」——毎朝鳴ると読まれなくなる）。"""
    assert cli.main(["init"]) == 0
    today = datetime.now().date().isoformat()
    _write_last_run(home_path, started_at=f"{today}T02:00:00")
    _write_report(home_path, today)

    result = runner.health(home_path)
    assert result["ok"] is True
    assert result["reasons"] == []


def test_health_notices_a_missing_record(home_path: Path) -> None:
    """記録そのものが無い＝起動していない可能性（台帳 E1: 不在を信号にする）。"""
    assert cli.main(["init"]) == 0
    result = runner.health(home_path)
    assert result["ok"] is False
    assert any("記録がありません" in r for r in result["reasons"])


def test_health_notices_a_stale_run(home_path: Path) -> None:
    """記録はあるが古い＝昨夜は動いていない。"""
    assert cli.main(["init"]) == 0
    old = (datetime.now() - timedelta(days=3)).isoformat()
    _write_last_run(home_path, started_at=old)

    result = runner.health(home_path)
    assert any("昨夜は動いていません" in r for r in result["reasons"])


def test_health_notices_killed_and_bad_status(home_path: Path) -> None:
    """打ち切り・異常終了・再起動を数える。"""
    assert cli.main(["init"]) == 0
    _write_last_run(home_path, status="killed", killed=True, exit_code=3, attempts=2)

    reasons = " / ".join(runner.health(home_path)["reasons"])
    assert "killed" in reasons
    assert "打ち切" in reasons
    assert "3" in reasons
    assert "2 回目" in reasons


def test_health_notices_done_without_a_report(home_path: Path) -> None:
    """「完了」と言っているのに成果物が無い（status と実物の食い違い）。"""
    assert cli.main(["init"]) == 0
    today = datetime.now().date().isoformat()
    _write_last_run(home_path, started_at=f"{today}T02:00:00")

    reasons = " / ".join(runner.health(home_path)["reasons"])
    assert "作業報告がありません" in reasons


def test_brief_carries_the_night_health(home_path: Path) -> None:
    """朝の便に異常が1行出ること。**報告の中身より先に**置く。"""
    assert cli.main(["init"]) == 0
    conn = db.connect(home_path)
    _write_last_run(home_path, status="killed", killed=True)

    text = slack_mod.format_mechanical_brief(slack_mod.brief_data(conn, home_path))

    assert "【昨夜の作業】" in text
    assert "注意:" in text
    assert "打ち切" in text
