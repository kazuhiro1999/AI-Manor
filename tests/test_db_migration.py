"""ADR-005 §7 の移行試験: 既存 DB（`rule` 表が無い・`steward_expense` に `import_hash` が
無い）に `db.init()` を再度通すと、冪等に両方が足される。**合成データのみ**。

`db.init()` 自体が「まっさらな DB を作る」だけでなく「既存 DB を移行する」役目も持つ
（`manor init` は既存の家でも安全に再実行できる約束——ADR-001 §5「manor init … 冪等」）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from manor import db as db_mod


def _make_old_db(home: Path) -> None:
    """`rule` 表を持たず、`steward_expense` に `import_hash` 列も無い「旧いDB」を手作りする
    （ADR-005 より前の状態を模す。実物の schema.sql を借りず、必要な表だけ最小限で作る）。
    """
    home.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(home / "manor.db"))
    try:
        conn.executescript(
            """
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE node (
              id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
              body TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE steward_expense (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              date TEXT NOT NULL, amount INTEGER NOT NULL,
              kind TEXT NOT NULL DEFAULT 'expense', category TEXT NOT NULL,
              memo TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT INTO steward_expense (date, amount, kind, category, memo, created_at)"
            " VALUES ('2026-01-01', 1000, 'expense', '食費', '旧データ', '2026-01-01T00:00:00')"
        )
        conn.commit()
    finally:
        conn.close()


def test_init_adds_rule_table_to_old_db(tmp_path: Path) -> None:
    home = tmp_path / "old_home_rule"
    _make_old_db(home)

    before = sqlite3.connect(str(home / "manor.db"))
    try:
        has_rule_before = before.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rule'"
        ).fetchone()
    finally:
        before.close()
    assert has_rule_before is None

    db_mod.init(home)

    conn = db_mod.connect(home)
    try:
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rule'"
            ).fetchone()
            is not None
        )
        # 使える状態（必要な列が揃っている）ことも確かめる
        conn.execute(
            "INSERT INTO rule (title, body, scope, tags, created_at, updated_at)"
            " VALUES ('雛形ルール', '', 'family', '', '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
        )
        conn.commit()
    finally:
        conn.close()


def test_init_adds_import_hash_column_to_old_steward_expense(tmp_path: Path) -> None:
    home = tmp_path / "old_home_hash"
    _make_old_db(home)

    db_mod.init(home)

    conn = db_mod.connect(home)
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(steward_expense)").fetchall()}
        assert "import_hash" in cols
        # 既存データは残る（列を足しただけ。行を消していない）
        row = conn.execute("SELECT * FROM steward_expense WHERE memo = '旧データ'").fetchone()
        assert row is not None
        assert row["import_hash"] is None  # 手入力（取り込みでない）行は NULL のまま
    finally:
        conn.close()


def test_init_is_idempotent_on_old_db_run_twice(tmp_path: Path) -> None:
    home = tmp_path / "old_home_twice"
    _make_old_db(home)

    db_mod.init(home)
    db_mod.init(home)  # 2回目もエラーにならない

    conn = db_mod.connect(home)
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(steward_expense)").fetchall()}
        assert "import_hash" in cols
        n = conn.execute("SELECT COUNT(*) AS n FROM steward_expense").fetchone()["n"]
        assert n == 1  # 二重に増えていない
    finally:
        conn.close()


def test_init_on_fresh_db_creates_rule_and_import_hash_directly(tmp_path: Path) -> None:
    """新規 DB は最初から `rule` 表と `steward_expense.import_hash` を持つ
    （移行コードを経由しない経路も確かめる）。
    """
    home = tmp_path / "fresh_home"
    db_mod.init(home)
    conn = db_mod.connect(home)
    try:
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rule'"
            ).fetchone()
            is not None
        )
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(steward_expense)").fetchall()}
        assert "import_hash" in cols
    finally:
        conn.close()


def test_check_c9_does_not_flag_rule_table(tmp_path: Path) -> None:
    """`rule` は core の表——C9（部下の表名接頭検査）の違反として拾われない
    （`db.CORE_TABLES` に加えたことの検算）。
    """
    from manor import check as check_mod

    home = tmp_path / "check_home"
    db_mod.init(home)
    conn = db_mod.connect(home)
    try:
        violations = check_mod.check_c9(conn)
    finally:
        conn.close()
    assert "rule" not in violations


def test_init_adds_run_table_to_old_db(tmp_path: Path) -> None:
    """ADR-006 §3: `run` 表は core.sql の `CREATE TABLE IF NOT EXISTS` で既存 DB にも足される。"""
    home = tmp_path / "old_home_run"
    _make_old_db(home)

    before = sqlite3.connect(str(home / "manor.db"))
    try:
        has_run_before = before.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='run'"
        ).fetchone()
    finally:
        before.close()
    assert has_run_before is None

    db_mod.init(home)

    conn = db_mod.connect(home)
    try:
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='run'"
            ).fetchone()
            is not None
        )
        conn.execute(
            "INSERT INTO run (kind, ref, started_at, exit_reason) VALUES ('night', '', '2026-01-01T00:00:00', '')"
        )
        conn.commit()
    finally:
        conn.close()


def test_init_adds_evidence_and_authorized_by_columns_to_old_db(tmp_path: Path) -> None:
    """ADR-006 §2 D8: `decision.evidence`・`task_event.authorized_by` を既存 DB へ
    冪等に ADD COLUMN する。`_make_old_db` は `decision`/`task_event` 自体を持たないので、
    ここでは最小限のそれらの表も手作りして「列が無い既存 DB」を模す。
    """
    home = tmp_path / "old_home_evidence"
    home.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(home / "manor.db"))
    try:
        conn.executescript(
            """
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE node (
              id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
              body TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE task (
              id TEXT PRIMARY KEY, project_id TEXT, status TEXT NOT NULL DEFAULT 'todo',
              status_note TEXT NOT NULL DEFAULT '', owner TEXT NOT NULL DEFAULT 'butler',
              level TEXT NOT NULL DEFAULT 'L2', section TEXT NOT NULL DEFAULT 'B',
              goal TEXT NOT NULL DEFAULT '', now TEXT NOT NULL DEFAULT '', next TEXT NOT NULL DEFAULT '',
              recommendation TEXT NOT NULL DEFAULT '', risk TEXT NOT NULL DEFAULT '',
              due TEXT, start TEXT, "end" TEXT, done_at TEXT
            );
            CREATE TABLE task_event (
              id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
              at TEXT NOT NULL, from_status TEXT, to_status TEXT NOT NULL,
              note TEXT NOT NULL DEFAULT '', actor TEXT NOT NULL DEFAULT 'butler'
            );
            CREATE TABLE decision (
              id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'open',
              recommendation TEXT NOT NULL DEFAULT '', background TEXT NOT NULL DEFAULT '',
              risk TEXT NOT NULL DEFAULT '', ruling TEXT NOT NULL DEFAULT '',
              asked_at TEXT NOT NULL, decided_at TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO decision (id, status, asked_at) VALUES ('D1', 'open', '2026-01-01T00:00:00')"
        )
        conn.commit()
    finally:
        conn.close()

    db_mod.init(home)

    conn = db_mod.connect(home)
    try:
        decision_cols = {r["name"] for r in conn.execute("PRAGMA table_info(decision)").fetchall()}
        assert "evidence" in decision_cols
        row = conn.execute("SELECT evidence FROM decision WHERE id = 'D1'").fetchone()
        assert row["evidence"] == ""  # 既存行は NOT NULL DEFAULT '' で埋まる

        event_cols = {r["name"] for r in conn.execute("PRAGMA table_info(task_event)").fetchall()}
        assert "authorized_by" in event_cols
    finally:
        conn.close()


def test_web_app_migrates_old_db_on_startup(tmp_path: Path) -> None:
    """本番 home で `manor init` を回し忘れても、Web を立てれば `rule` 表が足される（2026-09-03 実測の 500 を再現させない）。"""
    from fastapi.testclient import TestClient

    from manor.web.app import create_app

    home = tmp_path / "home"
    _make_old_db(home)
    client = TestClient(create_app(home))
    assert client.get("/api/v1/rules").status_code == 200


def _make_old_task_db(home: Path) -> None:
    """`task.kind` 列も `task_kind` 表も無い「ADR-010 より前の DB」を手作りする
    （`test_init_adds_evidence_and_authorized_by_columns_to_old_db` と同じ流儀）。
    """
    home.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(home / "manor.db"))
    try:
        conn.executescript(
            """
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE node (
              id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
              body TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE task (
              id TEXT PRIMARY KEY, project_id TEXT, status TEXT NOT NULL DEFAULT 'todo',
              status_note TEXT NOT NULL DEFAULT '', owner TEXT NOT NULL DEFAULT 'butler',
              level TEXT NOT NULL DEFAULT 'L2', section TEXT NOT NULL DEFAULT 'B',
              goal TEXT NOT NULL DEFAULT '', now TEXT NOT NULL DEFAULT '', next TEXT NOT NULL DEFAULT '',
              recommendation TEXT NOT NULL DEFAULT '', risk TEXT NOT NULL DEFAULT '',
              due TEXT, start TEXT, "end" TEXT, done_at TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO node (id, kind, title, created_at, updated_at)"
            " VALUES ('T1', 'task', '旧いタスク', '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
        )
        conn.execute(
            "INSERT INTO task (id, status, goal) VALUES ('T1', 'todo', '旧データ')"
        )
        conn.commit()
    finally:
        conn.close()


def test_migrate_core_adds_task_kind_column_and_table_to_old_db(tmp_path: Path) -> None:
    """ADR-010 D2: `task.kind` 列と `task_kind` 表は `manor init` を回さなくても
    `migrate_core`（CLI が毎回当てる軽い経路）だけで既存 DB へ届く。"""
    home = tmp_path / "old_home_task_kind"
    _make_old_task_db(home)

    before = sqlite3.connect(str(home / "manor.db"))
    try:
        cols_before = {r[1] for r in before.execute("PRAGMA table_info(task)").fetchall()}
        assert "kind" not in cols_before
        has_table_before = before.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_kind'"
        ).fetchone()
    finally:
        before.close()
    assert has_table_before is None

    db_mod.migrate_core(home)

    conn = db_mod.connect(home)
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(task)").fetchall()}
        assert "kind" in cols
        row = conn.execute("SELECT kind FROM task WHERE id = 'T1'").fetchone()
        assert row["kind"] == ""  # 既存行は NOT NULL DEFAULT '' で埋まる（過去のタスクは消えない）
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_kind'"
            ).fetchone()
            is not None
        )
        # **既定の8つも `migrate_core` で入る**（執事の裁定 2026-09-04）。表だけ作って seed を
        # `manor init` に任せると、更新後に init を忘れた home は「表はあるが空」になり、
        # 種類を1つも選べない——`run`／`notion_page` と同じ穴（GROWTH G5）。
        n = conn.execute("SELECT COUNT(*) AS n FROM task_kind").fetchone()["n"]
        assert n == 8
    finally:
        conn.close()


def test_init_seeds_task_kind_defaults_on_old_db(tmp_path: Path) -> None:
    """`manor init`（`db.init`）を通せば、既存 DB でも既定の8つが入る。"""
    home = tmp_path / "old_home_task_kind_init"
    _make_old_task_db(home)

    db_mod.init(home)

    conn = db_mod.connect(home)
    try:
        from manor import task_kind as task_kind_mod

        rows = task_kind_mod.list_kinds(conn, include_archived=True)
        assert {r["id"] for r in rows} == {k for k, _ in task_kind_mod.DEFAULTS}
        # 既存の task 行はそのまま・kind は空のまま
        row = conn.execute("SELECT kind FROM task WHERE id = 'T1'").fetchone()
        assert row["kind"] == ""
    finally:
        conn.close()


def test_cli_task_list_on_old_db_migrates_without_crashing(home_path: Path, capsys) -> None:
    """CLI コマンド（`manor task list`）を叩くだけで `migrate_core` が走り、
    列が無い旧い DB でも生の traceback を出さない（`test_cli_migration.py` と同じ趣旨）。
    `home_path` fixture が `MANOR_HOME` を向けてくれるので、DB だけ旧い形で用意する。
    """
    from manor import cli as cli_mod

    _make_old_task_db(home_path)

    rc = cli_mod.main(["task", "list", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Traceback" not in out

    conn = sqlite3.connect(str(home_path / "manor.db"))
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(task)").fetchall()}
        assert "kind" in cols
    finally:
        conn.close()


def test_board_app_migrates_old_db_on_startup(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from manor.board.app import create_app

    home = tmp_path / "home"
    _make_old_db(home)
    client = TestClient(create_app(home))
    assert client.get("/api/health").status_code == 200
    conn = sqlite3.connect(home / "manor.db")
    assert conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='rule'").fetchone()


def test_migrate_core_seeds_task_kinds_so_an_un_inited_home_can_choose(tmp_path, monkeypatch) -> None:
    """更新後に `manor init` を忘れた home でも種類が選べる（執事の裁定 2026-09-04）。

    表だけ作って seed を `manor init` に任せると「表はあるが空」になり、`--kind` が
    1つも通らない。`run`／`notion_page` で2度踏んだのと同じ穴（GROWTH G5）。
    """
    import sqlite3

    from manor import db as db_mod
    from manor import task_kind as task_kind_mod

    home = tmp_path / "old-home"
    home.mkdir()
    conn = sqlite3.connect(home / "manor.db")
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.commit()
    conn.close()

    db_mod.migrate_core(home)

    conn = db_mod.connect(home)
    try:
        kinds = task_kind_mod.list_kinds(conn)
        assert {k["id"] for k in kinds} == {kind_id for kind_id, _ in task_kind_mod.DEFAULTS}
    finally:
        conn.close()


def test_migrate_core_does_not_resurrect_archived_kinds(home, conn) -> None:
    """隠した種類を移行が復活させない（seed は「表が完全に空」のときだけ）。"""
    from manor import db as db_mod
    from manor import task_kind as task_kind_mod

    task_kind_mod.archive(conn, "admin")
    conn.commit()
    conn.close()

    db_mod.migrate_core(home)

    c2 = db_mod.connect(home)
    try:
        rows = {r["id"]: r for r in task_kind_mod.list_kinds(c2, include_archived=True)}
        assert rows["admin"]["archived_at"] is not None
    finally:
        c2.close()
