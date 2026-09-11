"""既存の行への一回きりの埋め（ADR-014 D2「既存の行の一回きりの埋め方」）の試験。
**合成データのみ**（架空の家庭。人名は入らない）。

`user.backfill_user_ids` を直接呼ぶ（`db.init`/`db.migrate_core` が内部で呼ぶのと同じ
関数）。規則そのものは ADR-014 D2 の表:

  - project: `kind = '執事'` → `butler`、他 → `master`
  - task: `project_id` があればそのプロジェクトの `user_id`、無ければ
    「`owner = 'master'` かつ `source != 'idea'`」→ `master`、それ以外 → `butler`

「2回目は0件・印を消さない限り再実行で書き換わらない」（G2: 移す・畳む道具は
2回目＋間に追記で試す）もここで確かめる。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from manor import db as db_mod
from manor import project as project_mod
from manor import task as task_mod
from manor import user as user_mod


def _make_pre_user_db(home: Path) -> None:
    """「`user` 表も `user_id` 列も無い、ADR-014 より前の DB」を手作りする
    （`tests/test_db_migration.py::_make_old_task_db` と同じ流儀）。
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
            CREATE TABLE project (
              id TEXT PRIMARY KEY, code TEXT NOT NULL UNIQUE, kind TEXT NOT NULL DEFAULT '',
              priority INTEGER NOT NULL DEFAULT 3, preset TEXT NOT NULL DEFAULT 'standard',
              status TEXT NOT NULL DEFAULT 'active', next_action TEXT NOT NULL DEFAULT '', due TEXT
            );
            CREATE TABLE task (
              id TEXT PRIMARY KEY, project_id TEXT, status TEXT NOT NULL DEFAULT 'todo',
              status_note TEXT NOT NULL DEFAULT '', owner TEXT NOT NULL DEFAULT 'butler',
              level TEXT NOT NULL DEFAULT 'L2', section TEXT NOT NULL DEFAULT 'B',
              goal TEXT NOT NULL DEFAULT '', now TEXT NOT NULL DEFAULT '', next TEXT NOT NULL DEFAULT '',
              recommendation TEXT NOT NULL DEFAULT '', risk TEXT NOT NULL DEFAULT '',
              due TEXT, start TEXT, "end" TEXT, done_at TEXT,
              kind TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT ''
            );
            """
        )
        now = "2026-01-01T00:00:00"

        # P1: 執事 kind のプロジェクト（配下の T1 は butler になるはず）
        conn.execute(
            "INSERT INTO node (id, kind, title, created_at, updated_at) VALUES"
            " ('P1', 'project', '執事プロジェクト', ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO project (id, code, kind) VALUES ('P1', 'butlerproj', '執事')"
        )
        conn.execute(
            "INSERT INTO node (id, kind, title, created_at, updated_at) VALUES"
            " ('T1', 'task', '執事配下のタスク', ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO task (id, project_id, owner, source) VALUES ('T1', 'P1', 'butler', '')"
        )

        # P3: 主人のプロジェクト（他の kind。配下の T19 は master のまま）
        conn.execute(
            "INSERT INTO node (id, kind, title, created_at, updated_at) VALUES"
            " ('P3', 'project', '研究プロジェクト', ?, ?)",
            (now, now),
        )
        conn.execute("INSERT INTO project (id, code, kind) VALUES ('P3', 'paper', '研究')")
        conn.execute(
            "INSERT INTO node (id, kind, title, created_at, updated_at) VALUES"
            " ('T19', 'task', '研究の一部', ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO task (id, project_id, owner, source) VALUES ('T19', 'P3', 'master', '')"
        )

        # 無所属・owner=master・意見箱でない → master のまま
        conn.execute(
            "INSERT INTO node (id, kind, title, created_at, updated_at) VALUES"
            " ('T2', 'task', '無所属の主人のタスク', ?, ?)",
            (now, now),
        )
        conn.execute("INSERT INTO task (id, owner, source) VALUES ('T2', 'master', '')")

        # 無所属・owner=master・意見箱 → butler（意見箱は執事の件）
        conn.execute(
            "INSERT INTO node (id, kind, title, created_at, updated_at) VALUES"
            " ('T3', 'task', '意見箱の起票', ?, ?)",
            (now, now),
        )
        conn.execute("INSERT INTO task (id, owner, source) VALUES ('T3', 'master', 'idea')")

        # 無所属・owner=部下名 → butler
        conn.execute(
            "INSERT INTO node (id, kind, title, created_at, updated_at) VALUES"
            " ('T4', 'task', '部下へ委譲中のタスク', ?, ?)",
            (now, now),
        )
        conn.execute("INSERT INTO task (id, owner, source) VALUES ('T4', 'chef', '')")

        conn.commit()
    finally:
        conn.close()


def test_backfill_applies_adr014_rules(home_path: Path) -> None:
    _make_pre_user_db(home_path)
    db_mod.init(home_path)  # user 表・user_id 列を足し、backfill まで一気に通す

    conn = db_mod.connect(home_path)
    try:
        assert project_mod.resolve(conn, "P1")["user_id"] == "butler"
        assert project_mod.resolve(conn, "P3")["user_id"] == "master"

        by_id = {t["id"]: t for t in task_mod.list_tasks(conn, include_settled=True)}
        assert by_id["T1"]["user_id"] == "butler"  # 執事 kind のプロジェクト配下
        assert by_id["T19"]["user_id"] == "master"  # 他 kind のプロジェクト配下
        assert by_id["T2"]["user_id"] == "master"  # 無所属・owner=master・非意見箱
        assert by_id["T3"]["user_id"] == "butler"  # 無所属・意見箱
        assert by_id["T4"]["user_id"] == "butler"  # 無所属・owner=部下名
    finally:
        conn.close()


def test_backfill_second_call_changes_nothing(home_path: Path) -> None:
    _make_pre_user_db(home_path)
    db_mod.init(home_path)

    conn = db_mod.connect(home_path)
    try:
        result = user_mod.backfill_user_ids(conn)
        assert result == {"projects": 0, "tasks": 0}
    finally:
        conn.close()


def test_backfill_does_not_undo_manual_reassignment(home_path: Path) -> None:
    """埋めたあとに `manor task set --user` で動かした結果を、再実行で戻さない
    （G2: 移す・畳む道具は2回目＋間に追記で試す）。
    """
    _make_pre_user_db(home_path)
    db_mod.init(home_path)

    conn = db_mod.connect(home_path)
    try:
        partner = user_mod.add(conn, "同居人", user_id="partner")
        task_mod.set(conn, "T2", user=partner)  # 手で動かす
        conn.commit()
    finally:
        conn.close()

    db_mod.init(home_path)  # 間に追記した状態で再実行

    conn = db_mod.connect(home_path)
    try:
        assert task_mod.show(conn, "T2")["user_id"] == "partner"  # 戻っていない
    finally:
        conn.close()


def test_migrate_core_also_runs_backfill_on_old_db(home_path: Path) -> None:
    """`manor init` を通さず `migrate_core`（CLI が毎回当てる軽い経路）だけでも埋まる。"""
    _make_pre_user_db(home_path)
    db_mod.migrate_core(home_path)

    conn = db_mod.connect(home_path)
    try:
        assert project_mod.resolve(conn, "P1")["user_id"] == "butler"
        row = conn.execute("SELECT user_id FROM task WHERE id = 'T3'").fetchone()
        assert row["user_id"] == "butler"
    finally:
        conn.close()
