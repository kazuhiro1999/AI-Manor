"""check.py の C13 — 夜勤が戻し損ねた消音（ADR-008 D12）。**警告のみ**。"""

from __future__ import annotations

from pathlib import Path

from manor import check as check_mod
from manor import task as task_mod
from manor import voice as voice_mod
from manor.night import runner as night_runner


def test_c13_fires_when_muted_by_night_and_no_lock(home_path: Path):
    voice_mod.mute(home_path, by_night=True)

    results = check_mod.check_c13(home_path)

    assert results == [{"muted_by_night": True, "lock": False}]


def test_c13_silent_when_lock_exists(home_path: Path):
    voice_mod.mute(home_path, by_night=True)
    night_runner.lock_path(home_path).parent.mkdir(parents=True, exist_ok=True)
    night_runner.lock_path(home_path).write_text("12345", encoding="utf-8")

    results = check_mod.check_c13(home_path)

    assert results == []


def test_c13_silent_when_not_muted_by_night(home_path: Path):
    assert check_mod.check_c13(home_path) == []

    voice_mod.mute(home_path)  # by_night=False（主人が自分で消した）
    assert check_mod.check_c13(home_path) == []


def test_c13_silent_when_muted_by_night_was_restored(home_path: Path):
    voice_mod.mute(home_path, by_night=True)
    voice_mod.restore(home_path)

    assert check_mod.check_c13(home_path) == []


def test_c13_does_not_affect_check_exit_verdict(conn, home: Path):
    task_mod.add(conn, "設計を書く")
    voice_mod.mute(home, by_night=True)  # C13 を鳴らす状態にする

    results = check_mod.run(conn, home)

    assert results["C13"]  # 鳴っている
    assert check_mod.ok(results)  # それでも判定（ok）は変わらない（警告扱い）


def test_c13_label_and_warning_registered():
    assert "C13" in check_mod.CHECK_LABELS
    assert "C13" in check_mod.WARNING_ONLY_CHECKS


def test_c13_included_in_run_output(conn, home: Path):
    results = check_mod.run(conn, home)
    assert "C13" in results
