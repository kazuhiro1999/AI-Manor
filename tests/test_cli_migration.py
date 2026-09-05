"""CLI は起動時に冪等な移行を当てる（2026-09-04・実測で2度踏んだ事故の機構化）。

`run` 表（2026-09-03）と `notion_page` 表（2026-09-04）で、「更新したあと `manor init` を
忘れた既存の home」に生の traceback が出た。**`needs_db=False` のコマンドでも当てる**
——`manor slack` / `manor notion` は `needs_db=False` でありながら自分で接続を開く。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from manor import cli as cli_mod


def _old_db(home: Path) -> None:
    """新しい表がまだ無い、古い形の home を作る。"""
    home.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(home / "manor.db")
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.commit()
    conn.close()


def _has_table(home: Path, name: str) -> bool:
    conn = sqlite3.connect(home / "manor.db")
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (name,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def test_cli_applies_migration_for_needs_db_false_command(home_path: Path, capsys) -> None:
    """`manor notion diary --dry-run` は `needs_db=False` だが `notion_page` を読む。"""
    _old_db(home_path)
    assert _has_table(home_path, "notion_page") is False
    rc = cli_mod.main(["notion", "diary", "--dry-run"])
    assert rc == 0
    assert _has_table(home_path, "notion_page") is True
    assert "Traceback" not in capsys.readouterr().out


def test_cli_migration_does_not_create_db_when_absent(home_path: Path) -> None:
    """DB が無いときは作らない（`manor init` の役目を奪わない）。"""
    cli_mod.main(["task", "list"])
    assert not (home_path / "manor.db").is_file()
