"""check.py の C16 — 外部視点の台帳の取り込みが頻度を超えて空いていないか（v1 の O1。T30）。

**警告のみ**。ファイル・「頻度」行・晩の見出しのいずれかが読めなければ判定せず `[]`。
"""

from __future__ import annotations

from pathlib import Path

from manor import check as check_mod


def _write_ledger(path: Path, *, frequency: str, rounds: list[str]) -> None:
    headings = "\n".join(f"## {d}（{i + 1}晩目）\n\n本文\n" for i, d in enumerate(rounds))
    path.write_text(f"**頻度: {frequency}**（判定の記録）\n\n{headings}", encoding="utf-8")


def test_c16_silent_when_file_missing(tmp_path: Path) -> None:
    results = check_mod.check_c16(today="2026-09-15", ledger_path=tmp_path / "nope.md")
    assert results == []


def test_c16_silent_when_frequency_line_missing(tmp_path: Path) -> None:
    path = tmp_path / "ledger.md"
    path.write_text("## 2026-08-28（1晩目）\n\n本文\n", encoding="utf-8")
    results = check_mod.check_c16(today="2026-09-15", ledger_path=path)
    assert results == []


def test_c16_silent_when_no_round_headings(tmp_path: Path) -> None:
    path = tmp_path / "ledger.md"
    path.write_text("**頻度: 毎晩**（判定の記録）\n\n本文だけ\n", encoding="utf-8")
    results = check_mod.check_c16(today="2026-09-15", ledger_path=path)
    assert results == []


def test_c16_silent_when_ended(tmp_path: Path) -> None:
    path = tmp_path / "ledger.md"
    _write_ledger(path, frequency="終了", rounds=["2026-01-01"])
    results = check_mod.check_c16(today="2026-09-15", ledger_path=path)
    assert results == []


def test_c16_silent_within_daily_threshold(tmp_path: Path) -> None:
    path = tmp_path / "ledger.md"
    _write_ledger(path, frequency="毎晩", rounds=["2026-09-13"])
    results = check_mod.check_c16(today="2026-09-15", ledger_path=path)
    assert results == []


def test_c16_fires_past_daily_threshold(tmp_path: Path) -> None:
    path = tmp_path / "ledger.md"
    _write_ledger(path, frequency="毎晩", rounds=["2026-09-10"])
    results = check_mod.check_c16(today="2026-09-15", ledger_path=path)
    assert len(results) == 1
    assert results[0]["frequency"] == "毎晩"
    assert results[0]["last_round"] == "2026-09-10"
    assert results[0]["elapsed_days"] == 5


def test_c16_silent_within_weekly_threshold(tmp_path: Path) -> None:
    path = tmp_path / "ledger.md"
    _write_ledger(path, frequency="週1回", rounds=["2026-09-11"])
    results = check_mod.check_c16(today="2026-09-15", ledger_path=path)
    assert results == []


def test_c16_fires_past_weekly_threshold(tmp_path: Path) -> None:
    path = tmp_path / "ledger.md"
    _write_ledger(path, frequency="週1回", rounds=["2026-09-01"])
    results = check_mod.check_c16(today="2026-09-15", ledger_path=path)
    assert len(results) == 1
    assert results[0]["elapsed_days"] == 14


def test_c16_uses_latest_round(tmp_path: Path) -> None:
    path = tmp_path / "ledger.md"
    _write_ledger(path, frequency="毎晩", rounds=["2026-09-01", "2026-09-14"])
    results = check_mod.check_c16(today="2026-09-15", ledger_path=path)
    assert results == []


def test_c16_included_in_run_output(conn, home: Path) -> None:
    results = check_mod.run(conn, home)
    assert "C16" in results


def test_c16_label_registered() -> None:
    assert "C16" in check_mod.CHECK_LABELS
    assert "C16" in check_mod.WARNING_ONLY_CHECKS
