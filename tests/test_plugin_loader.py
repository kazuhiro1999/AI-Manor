"""部下（staff）のプラグイン自動読み込み（ADR-001 §11）。

偽の部下パッケージ `fake` を `manor.staff.__path__` に一時登録し、
(1) `manor init` 相当のスキーマ適用が拾うこと、(2) `manor fake ...` が CLI として通ること、
(3) C9（表名接頭検査）が正しく効くことを確かめる。
"""

from __future__ import annotations

from pathlib import Path

from manor import check as check_mod
from manor import cli
from manor import db as db_mod


def test_iter_staff_modules_discovers_fake(home_path: Path, fake_staff: str):
    names = [name for name, _ in db_mod.iter_staff_modules()]
    assert "fake" in names


def test_init_applies_fake_schema(home_path: Path, fake_staff: str):
    applied = db_mod.init(home_path)
    assert "fake" in applied
    conn = db_mod.connect(home_path)
    try:
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "fake_note" in tables
    finally:
        conn.close()


def test_manor_fake_ping_runs_through_cli(home_path: Path, fake_staff: str, capsys):
    db_mod.init(home_path)
    code = cli.main(["fake", "ping"])
    assert code == 0
    out = capsys.readouterr().out
    assert "pong" in out

    conn = db_mod.connect(home_path)
    try:
        row = conn.execute("SELECT text FROM fake_note").fetchone()
        assert row["text"] == "ping"
    finally:
        conn.close()


def test_c9_passes_when_table_correctly_prefixed(home_path: Path, fake_staff: str):
    db_mod.init(home_path)
    conn = db_mod.connect(home_path)
    try:
        violations = check_mod.check_c9(conn)
        assert "fake_note" not in violations
    finally:
        conn.close()


def test_c9_flags_table_without_staff_prefix(home_path: Path, fake_staff: str):
    db_mod.init(home_path)
    conn = db_mod.connect(home_path)
    try:
        conn.execute("CREATE TABLE stray_table (id INTEGER PRIMARY KEY)")
        conn.commit()
        violations = check_mod.check_c9(conn)
        assert "stray_table" in violations
    finally:
        conn.close()


def test_fake_not_registered_without_fixture(home_path: Path):
    # fake_staff を使わない試験では、'fake' が見えてはいけない（後始末の検算）。
    names = [name for name, _ in db_mod.iter_staff_modules()]
    assert "fake" not in names
