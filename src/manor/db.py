"""SQLite への唯一の入口。

`connect(home)` は接続を返すだけ（PRAGMA 済み・行は `sqlite3.Row`）。
`init(home)` は core のスキーマ → 部下（staff）のスキーマの順に適用し、冪等（何度呼んでもよい）。

部下の自動読み込み（ADR-001 §11）: `manor.staff` パッケージの `__path__` を
`pkgutil.iter_modules` で列挙する。試験は `manor.staff.__path__` に一時ディレクトリを
足すだけで偽の部下を登録できる（`__path__` は素の list なので特別な細工は要らない）。
"""

from __future__ import annotations

import importlib
import pkgutil
import sqlite3
from pathlib import Path
from types import ModuleType

from . import util
from .errors import ManorError

CORE_SCHEMA_PATH = Path(__file__).resolve().parent / "schema" / "core.sql"

#: core が持つ表の名前。C9（部下の表名接頭検査）が「これ以外」を部下の表とみなす。
#: `rule`（ADR-005 §2「家庭のルール」）も core の表——node には紐づかない独立の表だが、
#: `<staff>_` 接頭の対象ではないので明示的にここへ加える。
CORE_TABLES: frozenset[str] = frozenset(
    {
        "meta", "node", "task", "task_event", "project", "decision", "milestone", "edge",
        "handoff", "rule", "run", "profile",
        # ADR-009 §3（Slack 拡張。5b）: 部下（staff）の接頭規則の対象ではない core の表。
        "slack_message", "slack_reply",
        # ADR-009 §7（Notion 拡張。5c）: 同上。
        "notion_page",
        # ADR-010 D2（タスクの種類）: 同上。node には紐づかない独立の表（`rule` と同じ流儀）。
        "task_kind",
    }
)

#: CLI の group 名（`manor <group> ...`）→ 部下パッケージ名（`NAME`。表の接頭でもある）。
#: `chef` はどちらも同じだが、`house`/`money`/`sec` は歴史的な理由で短縮されており
#: 一致しない（ADR-002 §3-6）。ADR-006 D16 の authorizer 配線が「どの部下か」を
#: 引くために使う。ここに無い group 名は `iter_staff_modules()` の名前をそのまま試す。
STAFF_GROUP_ALIASES: dict[str, str] = {
    "house": "housekeeper",
    "money": "steward",
    "sec": "secretary",
}

#: ADR-002 §4 の唯一の例外: housekeeper は chef の買い物リスト（`chef_shopping`）へ
#: 書いてよい（消耗品が少ないときに提案として積む）。ADR-006 D16 の `allow`。
STAFF_GUARD_ALLOW: dict[str, tuple[str, ...]] = {
    "housekeeper": ("chef_shopping",),
}


def staff_prefix_for_group(group: str | None) -> str | None:
    """CLI の `group`（例: `"house"`）が部下のコマンドなら、その部下名（表の接頭）を返す。

    部下でない group（`task` / `check` / `run` など）には `None` を返す。
    """
    if not group:
        return None
    name = STAFF_GROUP_ALIASES.get(group, group)
    if any(n == name for n, _ in iter_staff_modules()):
        return name
    return None


def connect(home: Path) -> sqlite3.Connection:
    """`home/manor.db` を開く。無ければ空ファイルとして開く（スキーマは当てない。`init` の仕事）。"""
    home = Path(home)
    db_path = home / "manor.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.DatabaseError:
        pass
    return conn


def iter_staff_modules() -> list[tuple[str, ModuleType]]:
    """`manor.staff` 配下のプラグインパッケージを名前順に列挙する。

    サブパッケージ（`__init__.py` を持つディレクトリ）だけを拾う。単一の `.py` は無視。
    """
    from . import staff as staff_pkg

    out: list[tuple[str, ModuleType]] = []
    infos = sorted(pkgutil.iter_modules(staff_pkg.__path__), key=lambda i: i.name)
    for info in infos:
        if not info.ispkg:
            continue
        module = importlib.import_module(f"{staff_pkg.__name__}.{info.name}")
        out.append((info.name, module))
    return out


def _staff_schema_path(module: ModuleType) -> Path | None:
    if not module.__file__:
        return None
    p = Path(module.__file__).resolve().parent / "schema.sql"
    return p if p.is_file() else None


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, coltype: str) -> None:
    """`table` が既にあり、`column` を持っていなければ `ALTER TABLE ... ADD COLUMN` で足す。

    冪等な移行の道具。**新規 DB では何もしない**（テーブルがまだ無ければ `schema.sql` 側の
    `CREATE TABLE` が最初から列を持つので、ここで作る必要が無い）。既存 DB にだけ効く。
    """
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    if exists is None:
        return
    cols = {str(r["name"]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def migrate_core(home: Path) -> None:
    """**中核の表・列だけ**を冪等に当てる（部下のスキーマには触れない）。

    CLI が毎回の起動で呼ぶ（`cli.main`）。`run` 表（2026-09-03）と `notion_page` 表
    （2026-09-04）で「更新後に `manor init` を忘れた既存の home」に生の traceback が出た
    ——新しい中核の表は黙って足されるべきである。

    **部下のスキーマを当ててはいけない。** 「部下が導入されているか」は表の有無で判定して
    いる（`web/_common.module_list`・`api` の `available`）ので、毎回すべて作ると
    **導入していない部下を表現できなくなる**（実測: `manor init` 相当を CLI に入れたら
    `test_supply_low_to_shopping_table_missing_does_not_crash` が落ちた）。部下を入れるのは
    `manor init` の仕事のまま。
    """
    home = Path(home)
    if not (home / "manor.db").is_file():
        return
    conn = connect(home)
    try:
        conn.executescript(CORE_SCHEMA_PATH.read_text(encoding="utf-8"))
        _add_column_if_missing(conn, "decision", "evidence", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "task_event", "authorized_by", "TEXT")
        # ADR-010 D2: 既存 DB の task に kind 列を冪等に足す。
        _add_column_if_missing(conn, "task", "kind", "TEXT NOT NULL DEFAULT ''")
        # 済んだ節目（2026-09-05）。既存 DB は全部 NULL＝「まだ」から始まる。
        _add_column_if_missing(conn, "milestone", "done_at", "TEXT")
        # **既定の8つもここで入れる**（執事の裁定 2026-09-04）。`manor init` だけに任せると、
        # 更新後に init を忘れた home は「表はあるが空」になり、種類を1つも選べない——
        # `run`／`notion_page` で2度踏んだのと**同じ穴**（GROWTH G5）。
        # 復活の心配は無い: seed は**表が完全に空のときだけ**入り、`other` は隠せないので、
        # 一度 seed した表が再び空になることはない。
        from . import task_kind as task_kind_mod

        task_kind_mod.seed_defaults(conn)
        _backfill_authorized_by(conn)
        conn.commit()
    finally:
        conn.close()


def init(home: Path) -> list[str]:
    """DB と `home/` の骨を作る。冪等。適用した部下の名前の一覧を返す。

    ADR-005 §7 の移行: `rule` 表は core.sql の `CREATE TABLE IF NOT EXISTS` が
    毎回の `init()` で再適用されるので、既存 DB にも自然に足される（追加のコードは要らない）。
    `steward_expense.import_hash` は既存の表に列を足す必要があるため（`CREATE TABLE IF NOT
    EXISTS` では既存の表の列は増えない）、部下のスキーマを当てる前に明示的に移行する。
    """
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    (home / "projections").mkdir(parents=True, exist_ok=True)
    (home / "handoffs").mkdir(parents=True, exist_ok=True)
    (home / "inbox").mkdir(parents=True, exist_ok=True)

    conn = connect(home)
    applied: list[str] = []
    try:
        try:
            conn.executescript(CORE_SCHEMA_PATH.read_text(encoding="utf-8"))
        except sqlite3.DatabaseError as exc:
            raise ManorError(
                f"core のスキーマを適用できません: {exc}",
                key="error.db.core_schema_failed",
                params={"exc": str(exc)},
            ) from exc

        # ADR-005 §2「imports」: 既存 DB の steward_expense に import_hash 列を冪等に足す。
        # staff のスキーマ（CREATE UNIQUE INDEX）より先に列を用意する必要がある。
        _add_column_if_missing(conn, "steward_expense", "import_hash", "TEXT")

        # ADR-006 §2「承認に証跡を付ける」(D5・D8): 既存 CREATE TABLE 文は変えず、
        # ここで冪等に列を足す。新規 DB でも core.sql の CREATE TABLE の直後にこの
        # 段を通るので、新規・既存どちらも同じ経路で列を持つ。
        _add_column_if_missing(conn, "decision", "evidence", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "task_event", "authorized_by", "TEXT")

        # ADR-010 D2: 既存 DB の task に kind 列を冪等に足す（新規 DB は core.sql の
        # CREATE TABLE が最初から持つ）。
        _add_column_if_missing(conn, "task", "kind", "TEXT NOT NULL DEFAULT ''")

        # ADR-006 §2/D21: 既存 DB の HG・done task で authorized_by が未設定のものを
        # decided_by の辺から一回だけ埋める（辺→事実への写し。以後は事実だけを見る）。
        _backfill_authorized_by(conn)

        # ADR-010 D2: task_kind が空のときだけ既定の8つを入れる（主人が隠した/増やしたものを
        # 再挿入・復活させない。`task_kind.seed_defaults` が空チェックを持つ）。
        from . import task_kind as task_kind_mod

        task_kind_mod.seed_defaults(conn)

        for name, module in iter_staff_modules():
            schema_path = _staff_schema_path(module)
            if schema_path is None:
                continue
            try:
                conn.executescript(schema_path.read_text(encoding="utf-8"))
            except sqlite3.DatabaseError as exc:
                raise ManorError(
                    f"部下 {name} のスキーマを適用できません: {exc}",
                    key="error.db.staff_schema_failed",
                    params={"name": name, "exc": str(exc)},
                ) from exc
            applied.append(name)

        if conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone() is None:
            conn.execute("INSERT INTO meta (key, value) VALUES ('schema_version', '1')")
        if conn.execute("SELECT value FROM meta WHERE key='created_at'").fetchone() is None:
            conn.execute("INSERT INTO meta (key, value) VALUES ('created_at', ?)", (util.now(),))
        conn.commit()
    finally:
        conn.close()
    return applied


def _backfill_authorized_by(conn: sqlite3.Connection) -> None:
    """ADR-006 D21: 辺（`decided_by`）→事実（`task_event.authorized_by`）への一回きりの写し。

    `level='HG'` かつ `status='done'` の task のうち、最新の `to_status='done'` の
    event の `authorized_by` が NULL/空のものだけを対象にする。`decided_by` の辺で
    approved/modified の decision に繋がっていれば、その decision id（最小のもの）を
    その event に埋める。**冪等**——一度埋まれば `authorized_by` は NULL/空でなくなるので、
    次回の `init()` では対象から外れる（以後は事実（authorized_by）だけを検査が見る。C5）。
    """
    tasks = conn.execute(
        "SELECT id FROM task WHERE level = 'HG' AND status = 'done' ORDER BY id"
    ).fetchall()
    for t in tasks:
        task_id = t["id"]
        ev = conn.execute(
            "SELECT id, authorized_by FROM task_event WHERE task_id = ? AND to_status = 'done'"
            " ORDER BY id DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        if ev is None:
            continue
        current = ev["authorized_by"]
        if current is not None and str(current).strip() != "":
            continue  # 既に事実が入っている（新しい経路 or 前回の移行済み）
        dec = conn.execute(
            "SELECT d.id AS id FROM edge e JOIN decision d ON d.id = e.dst"
            " WHERE e.src = ? AND e.rel = 'decided_by' AND d.status IN ('approved','modified')"
            " ORDER BY d.id LIMIT 1",
            (task_id,),
        ).fetchone()
        if dec is None:
            continue
        conn.execute("UPDATE task_event SET authorized_by = ? WHERE id = ?", (dec["id"], ev["id"]))


def require_db(home: Path) -> None:
    """DB が無ければ `manor init` を促す `ManorError` を投げる。"""
    if not (Path(home) / "manor.db").is_file():
        raise ManorError(
            "DB がまだありません。`manor init` を先に実行してください",
            key="error.db.not_initialized",
        )


def ensure_staff_schema(conn: sqlite3.Connection, name: str) -> bool:
    """部下 `name` の `schema.sql` が当たっていることを確かめる（無ければ適用する）。

    `manor init` は通常すべての部下へ順に適用するが、それより前に部下の表へ書きたい
    場面向け（ADR-007 §6 D9: ウィザードの kitchen 答えを `chef_taste` へ書く前）。
    `schema.sql` は `CREATE TABLE IF NOT EXISTS` なので、既に当たっていても再適用して
    安全（DDL をここで手書きしない——`iter_staff_modules` / `_staff_schema_path` と
    同じ経路を通す）。部下パッケージ `name` 自体が見当たらなければ False を返す
    （呼び出し側はその答えの書き込みを諦める）。
    """
    for mod_name, module in iter_staff_modules():
        if mod_name != name:
            continue
        schema_path = _staff_schema_path(module)
        if schema_path is None:
            return False
        conn.executescript(schema_path.read_text(encoding="utf-8"))
        return True
    return False


# --- 部下の表の隔離（ADR-006 §5・D16〜D18） ------------------------------------------
#
# SQLite の authorizer（`sqlite3.Connection.set_authorizer`）で、部下の CLI が
# 自分の表（`<prefix>_*`）以外へ `INSERT`/`UPDATE`/`DELETE` するのを機構で止める。
# `meta`・`sqlite_sequence` は例外（自動採番の内部表・共有の設定表）。読み（SELECT）は
# 制限しない。
#
# **残る限界**（ADR-006 D18。ここに明記する）:
#   - Python から `db.connect()` を直に呼んで生の `conn` を使えば、この authorizer は
#     掛からない（`cli.py` の dispatch を経由したときだけ掛かる）。担当の定義（プロンプト）
#     と `tests/staff/test_<name>.py` が守る領分——機構はここまで。
#   - Web の API（`src/manor/web/**`）は core の関数（`task.py`/`decision.py` 等）を
#     直接呼ぶ。core の表への書き込みは core 自身の役目なので、この隔離の対象外。
#   - `sqlite3` の authorizer コールバックは Python 例外をそのまま伝播しない（実測済み。
#     独自例外を送出しても `SQLITE_DENY` 相当に潰され、呼び出し側には
#     `sqlite3.DatabaseError`（"not authorized"）が届く）。そのため `ManorError` への
#     翻訳は「拒否された表名を憶えておいて、その `DatabaseError` を捕まえた側で組み立てる」
#     形にした（`staff_guard` が返す `denied` と `staff_denial_error`）。


def staff_guard(
    conn: sqlite3.Connection, prefix: str, *, allow: tuple[str, ...] = ()
) -> list[str]:
    """`conn` に authorizer を掛ける。`INSERT`/`UPDATE`/`DELETE` の対象表が
    `<prefix>_` で始まらず `allow` にも無ければ拒否する（ADR-006 D16）。

    戻り値は「拒否した表名」を追記していく list（可変・共有）。SQLite の authorizer は
    Python 例外を伝えないので、呼び出し側は `conn.execute(...)` が `sqlite3.DatabaseError`
    を投げたときにこの list の末尾を見て `ManorError` を組み立てる（`staff_denial_error`）。
    """
    denied: list[str] = []

    def authorizer(action, arg1, arg2, db_name, trigger_name):  # noqa: ANN001 - sqlite3 のコールバック署名
        if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
            table = arg1 or ""
            if table in ("meta", "sqlite_sequence"):
                return sqlite3.SQLITE_OK
            if table.startswith(f"{prefix}_") or table in allow:
                return sqlite3.SQLITE_OK
            denied.append(table)
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    conn.set_authorizer(authorizer)
    return denied


def staff_denial_error(prefix: str, table: str) -> ManorError:
    """`staff_guard` が拒否した表名から `ManorError` を組み立てる（ADR-006 D16 の文言）。"""
    return ManorError(f"部下 {prefix} は自分の表（{prefix}_*）にしか書けません: {table}")
