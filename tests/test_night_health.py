"""昨夜ちゃんと走ったかを、朝の便が自分で見ること（2026-09-08）。

主人のご質問「夜間タスクのチェックは朝に自動実行するようになってますか？」。
定例4本（夜勤 02:00 / 日誌 06:45 / ブリーフィング 07:30 / 取り込み 5分ごと）は
動いていたが、ブリーフィングの【昨夜の作業】は**報告ファイルがあれば読む**形で、
無い場合の1行は「起動しなかった / 落ちた / 指示が空だった」を区別しなかった。
9/5 に夜勤がプロセスごと消えた晩、気づいたのは**翌々日の夜勤自身**。
"""

from __future__ import annotations

import json
import pytest
from datetime import datetime, timedelta
from pathlib import Path

from manor import cli
from manor import db
from manor import slack as slack_mod
from manor.night import runner


@pytest.fixture(autouse=True)
def _scheduler_is_enabled(monkeypatch):
    """⚠ `health()` は本物のスケジューラを見る（登録が無効なら鳴る。2026-09-12）。
    試験は**その機械の登録状態に依らず**通らなければならない（G13「試験は私の機械に
    たまたま在るものを読んでいる」）——主人が今夜の夜勤を止めた日に、無関係な試験が
    赤くなった。既定は「有効」にし、無効の振る舞いは別の試験で明示的に作る。"""
    monkeypatch.setattr(
        runner, "_query_scheduled_task",
        lambda task_name: {"platform": "test", "registered": True, "enabled": True, "detail": ""},
    )


def test_health_notices_a_disabled_registration(home_path: Path, monkeypatch) -> None:
    """止めたまま戻し忘れたら、朝に鳴ること（2026-09-12 主人「今夜は一旦なし」）。"""
    monkeypatch.setattr(
        runner, "_query_scheduled_task",
        lambda task_name: {"platform": "test", "registered": True, "enabled": False, "detail": ""},
    )
    today = datetime.now().date().isoformat()
    _write_last_run(home_path, started_at=f"{today}T02:00:00")
    _write_report(home_path, today)

    reasons = " / ".join(runner.health(home_path)["reasons"])
    assert "無効" in reasons


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
    """打ち切り・異常終了・再開を数える。

    ⚠ 「再開した」の印は `attempts`（＝席の数）から `resumed` へ移した（2026-09-11 の
    作り直し）。席が複数あるのは**普通の晩**なので、席の数で鳴らすと毎晩鳴る。
    """
    assert cli.main(["init"]) == 0
    _write_last_run(
        home_path, status="killed", killed=True, exit_code=3,
        resumed=True, resumed_from="2026-09-11T03:40:00",
    )

    reasons = " / ".join(runner.health(home_path)["reasons"])
    assert "killed" in reasons
    assert "打ち切" in reasons
    assert "3" in reasons
    assert "03:40" in reasons


def test_health_stays_quiet_about_the_number_of_sittings(home_path: Path) -> None:
    """**席が2回あったこと自体は異常ではない。** 作り直し以降、これが普通の晩。"""
    assert cli.main(["init"]) == 0
    today = datetime.now().date().isoformat()
    _write_last_run(
        home_path, started_at=f"{today}T02:00:00", attempts=3, sittings=3,
        items=["N1"], progress={"done": ["N1"], "stuck": [], "doing": [], "sittings": 3},
    )
    _write_report(home_path, today)

    assert runner.health(home_path)["ok"] is True


def test_health_notices_that_nothing_got_done(home_path: Path) -> None:
    """`status` は `done` でも、**台帳が空なら片付いていない**。"""
    assert cli.main(["init"]) == 0
    today = datetime.now().date().isoformat()
    _write_last_run(
        home_path, started_at=f"{today}T02:00:00",
        items=["N1", "N2"], progress={"done": [], "stuck": ["N1"], "doing": [], "sittings": 2},
    )
    _write_report(home_path, today)

    reasons = " / ".join(runner.health(home_path)["reasons"])
    assert "進められなかった" in reasons
    assert "1本も片付いていません" in reasons


def test_health_notices_done_without_a_report(home_path: Path) -> None:
    """「完了」と言っているのに成果物が無い（status と実物の食い違い）。"""
    assert cli.main(["init"]) == 0
    today = datetime.now().date().isoformat()
    _write_last_run(home_path, started_at=f"{today}T02:00:00")

    reasons = " / ".join(runner.health(home_path)["reasons"])
    assert "作業報告がありません" in reasons


def test_health_notices_uncommitted_changes_left_by_the_gate(home_path: Path) -> None:
    """歯止め「1タスク1コミット」が破られたまま朝を迎えたら鳴る（T40）。"""
    assert cli.main(["init"]) == 0
    today = datetime.now().date().isoformat()
    _write_last_run(
        home_path, started_at=f"{today}T02:00:00",
        gate={"uncommitted": ["M src/manor/night/runner.py"], "tests": None},
    )
    _write_report(home_path, today)

    reasons = " / ".join(runner.health(home_path)["reasons"])
    assert "未コミット" in reasons


def test_health_notices_a_red_test_run_from_the_gate(home_path: Path) -> None:
    """歯止め「テストを通してから終わる」が破られたまま朝を迎えたら鳴る（T40）。"""
    assert cli.main(["init"]) == 0
    today = datetime.now().date().isoformat()
    _write_last_run(
        home_path, started_at=f"{today}T02:00:00",
        gate={"uncommitted": ["M src/x.py"], "tests": {"exit_code": 1, "tail": "1 failed"}},
    )
    _write_report(home_path, today)

    reasons = " / ".join(runner.health(home_path)["reasons"])
    assert "試験が赤" in reasons


def test_health_stays_quiet_when_the_gate_is_clean(home_path: Path) -> None:
    """歯止めが守られていれば、gate があっても鳴らない。"""
    assert cli.main(["init"]) == 0
    today = datetime.now().date().isoformat()
    _write_last_run(
        home_path, started_at=f"{today}T02:00:00",
        gate={"uncommitted": [], "tests": None},
    )
    _write_report(home_path, today)

    assert runner.health(home_path)["ok"] is True


def test_brief_carries_the_night_health(home_path: Path) -> None:
    """朝の便に異常が1行出ること。**報告の中身より先に**置く。"""
    assert cli.main(["init"]) == 0
    conn = db.connect(home_path)
    _write_last_run(home_path, status="killed", killed=True)

    text = slack_mod.format_mechanical_brief(slack_mod.brief_data(conn, home_path))

    assert "【昨夜の作業】" in text
    assert "注意:" in text
    assert "打ち切" in text
