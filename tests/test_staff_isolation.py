"""部下の表の隔離（ADR-006 §5・D16〜D18）。静的検算＋実行時検算の両方。"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from manor import cli
from manor import db as db_mod
from manor.errors import ManorError

REPO_ROOT = Path(__file__).resolve().parents[1]

#: `INSERT INTO <table>` / `UPDATE <table> SET` / `DELETE FROM <table>`。
#: `ON CONFLICT(...) DO UPDATE SET ...` は対象表を伴わないので、直前が `DO ` の
#: `UPDATE` は除外する（`(?<!DO )` の否定後読み）。
_WRITE_RE = re.compile(
    r"INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)"
    r"|(?<!DO )UPDATE\s+([A-Za-z_][A-Za-z0-9_]*)\s+SET"
    r"|DELETE\s+FROM\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)

_EXEMPT_TABLES = {"meta", "sqlite_sequence"}


# --- 静的検算: src/manor/staff/<name>/*.py の SQL 文字列 ------------------------------


def test_staff_source_only_writes_own_tables():
    """各部下の `.py` から `INSERT/UPDATE/DELETE` の対象表を拾い、`<name>_` 接頭か
    `db.STAFF_GUARD_ALLOW` の例外に合うことを検算する（ADR-006 D17）。
    """
    violations: list[str] = []
    checked_any = False
    for name, module in db_mod.iter_staff_modules():
        assert module.__file__ is not None
        staff_dir = Path(module.__file__).resolve().parent
        allow = db_mod.STAFF_GUARD_ALLOW.get(name, ())
        for py_file in sorted(staff_dir.glob("*.py")):
            text = py_file.read_text(encoding="utf-8")
            for m in _WRITE_RE.finditer(text):
                table = next(g for g in m.groups() if g)
                checked_any = True
                if table in _EXEMPT_TABLES:
                    continue
                if table.startswith(f"{name}_") or table in allow:
                    continue
                violations.append(f"{py_file.relative_to(REPO_ROOT)}: {table} (owner={name})")
    assert checked_any, "検算対象の SQL 文が1件も見つかりませんでした（正規表現の見直しが要る）"
    assert not violations, "部下が自分の表以外へ書いています:\n" + "\n".join(violations)


def test_staff_guard_allow_only_lists_housekeeper_to_chef_shopping():
    """ADR-002 §4 の例外は housekeeper→chef_shopping の1件だけ。増えていたら意図の確認が要る。"""
    assert db_mod.STAFF_GUARD_ALLOW == {"housekeeper": ("chef_shopping",)}


# --- 実行時検算: guard 付き接続 --------------------------------------------------------


def test_runtime_guard_denies_write_to_core_table(home: Path):
    conn = db_mod.connect(home)
    try:
        denied = db_mod.staff_guard(conn, "housekeeper")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(
                "INSERT INTO node (id, kind, title, body, created_at, updated_at)"
                " VALUES ('T999', 'task', 'x', '', '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
            )
        assert denied == ["node"]
        err = db_mod.staff_denial_error("housekeeper", denied[-1])
        assert isinstance(err, ManorError)
        assert "housekeeper" in err.message_ja and "node" in err.message_ja
    finally:
        conn.close()


def test_runtime_guard_allows_own_table_write(home: Path):
    conn = db_mod.connect(home)
    try:
        db_mod.staff_guard(conn, "housekeeper")
        conn.execute(
            "INSERT INTO housekeeper_chore (name, area, cadence_days, created_at)"
            " VALUES (?, ?, ?, ?)",
            ("窓拭き", "全体", 30, "2026-01-01T00:00:00"),
        )
        row = conn.execute(
            "SELECT name FROM housekeeper_chore WHERE name = '窓拭き'"
        ).fetchone()
        assert row is not None
    finally:
        conn.close()


def test_runtime_guard_allows_housekeeper_write_to_chef_shopping(home: Path):
    """ADR-002 §4 の唯一の例外: housekeeper は chef_shopping へ書いてよい。"""
    conn = db_mod.connect(home)
    try:
        db_mod.staff_guard(conn, "housekeeper", allow=db_mod.STAFF_GUARD_ALLOW["housekeeper"])
        conn.execute(
            "INSERT INTO chef_shopping (item, reason, aisle, added_at) VALUES (?, ?, ?, ?)",
            ("洗剤", "家政婦", "その他", "2026-01-01T00:00:00"),
        )
        row = conn.execute("SELECT item FROM chef_shopping WHERE item = '洗剤'").fetchone()
        assert row is not None
    finally:
        conn.close()


def test_runtime_guard_without_allow_still_denies_other_staff_table(home: Path):
    """`allow` を渡さなければ housekeeper でも chef_shopping には書けない。"""
    conn = db_mod.connect(home)
    try:
        denied = db_mod.staff_guard(conn, "housekeeper")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(
                "INSERT INTO chef_shopping (item, reason, aisle, added_at) VALUES (?, ?, ?, ?)",
                ("洗剤", "家政婦", "その他", "2026-01-01T00:00:00"),
            )
        assert denied == ["chef_shopping"]
    finally:
        conn.close()


def test_runtime_guard_allows_select_on_core_tables(home: Path):
    """読み（SELECT）は常に許す。"""
    conn = db_mod.connect(home)
    try:
        db_mod.staff_guard(conn, "housekeeper")
        conn.execute("SELECT * FROM task").fetchall()
        conn.execute("SELECT * FROM node").fetchall()
    finally:
        conn.close()


def test_staff_prefix_for_group_resolves_aliases():
    assert db_mod.staff_prefix_for_group("house") == "housekeeper"
    assert db_mod.staff_prefix_for_group("money") == "steward"
    assert db_mod.staff_prefix_for_group("sec") == "secretary"
    assert db_mod.staff_prefix_for_group("chef") == "chef"
    assert db_mod.staff_prefix_for_group("task") is None
    assert db_mod.staff_prefix_for_group(None) is None


# --- CLI 経由: 部下のコマンドが自分の表へは書ける（回帰） ------------------------------


def test_cli_chef_pantry_add_still_works_under_guard(home_path: Path, capsys):
    cli.main(["init"])
    capsys.readouterr()
    code = cli.main(["chef", "pantry", "add", "卵", "--qty", "6", "--unit", "個", "--json"])
    assert code == 0
    out = capsys.readouterr().out
    assert '"item"' in out or "卵" in out


def test_cli_housekeeper_chore_add_still_works_under_guard(home_path: Path, capsys):
    cli.main(["init"])
    capsys.readouterr()
    code = cli.main(["house", "chore", "add", "窓拭き", "--every", "30", "--json"])
    assert code == 0
