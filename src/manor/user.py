"""利用者（ADR-014「利用者の識別と切り替え」D1・D2・D6）。

**認証ではない。** 「誰として見ているか（誰の机か）」を持たせるだけの表——passcode は
家の鍵1つのまま変えない（ADR-014 D1「やらないこと」）。

- `principal`（主人。1人）: 執事の裁定を受ける人。`profile.master.callname` と同じ人
- `member`（同居の相手。増やせる）
- `butler`（共有用の AI 執事ユーザー。1つ）: 執事自身の件（X 系・意見箱・夜勤が起票する
  もの）の持ち主。サーバ PC で開くときの既定

種（`master`/`butler`）は `seed_defaults` が「表が空のときだけ」入れる（`task_kind.
seed_defaults` と同じ約束——一度畳んだものを復活させない）。既存 DB の `task`/`project`
行の「誰の件か」への一回きりの埋めは `backfill_user_ids`（`meta` 表の印で冪等）。

CLI（`manor user list|add|set|archive`）と Web（段B）の両方がここの関数を呼ぶ
（`task_kind.py`/`rule.py` と同じ流儀）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3

from . import i18n, util
from .errors import ManorError

#: `role` の語彙（core.sql の CHECK と一致させること）。
VALID_ROLES: tuple[str, ...] = ("principal", "member", "butler")

#: 種の固定 id（ADR-014 D1）。
PRINCIPAL_ID = "master"
BUTLER_ID = "butler"

#: CLI の文脈の利用者（ADR-014 D2 の③・D6）。夜勤は `butler` を立てて起動する。
ENV_USER = "MANOR_USER"

#: id の形式。`project.code` と同じ質の制約（小文字英数字と `-`/`_`、先頭は英字、32字以内）。
_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def _profile_name(conn: sqlite3.Connection, key: str, default_ja: str) -> str:
    """`profile.<key>` があればそれを、無ければ既定の日本語名を返す。

    **既定値は i18n を通さない**——`profile.summary_line` が「執事」を既定にしているのと
    同じ扱いで、DB の中身（種の名前の初期値）であって画面の文言ではない。
    """
    row = conn.execute("SELECT value FROM profile WHERE key = ?", (key,)).fetchone()
    value = str(row["value"]).strip() if row is not None else ""
    return value or default_ja


def seed_defaults(conn: sqlite3.Connection) -> None:
    """`user` が空のときだけ `master`（principal）と `butler`（butler）を入れる。

    **既に1件でもあれば何もしない**——主人が畳んだものを再挿入・復活させない
    （`task_kind.seed_defaults` と同じ約束）。`db.init`/`db.migrate_core` から呼ぶ。
    """
    row = conn.execute("SELECT 1 FROM user LIMIT 1").fetchone()
    if row is not None:
        return
    now = util.now()
    master_name = _profile_name(conn, "master.callname", "主人")
    butler_name = _profile_name(conn, "butler.callname", "執事")
    conn.execute(
        "INSERT INTO user (id, name, role, created_at, archived_at) VALUES (?, ?, 'principal', ?, NULL)",
        (PRINCIPAL_ID, master_name, now),
    )
    conn.execute(
        "INSERT INTO user (id, name, role, created_at, archived_at) VALUES (?, ?, 'butler', ?, NULL)",
        (BUTLER_ID, butler_name, now),
    )


#: `meta` 表の一回きりの埋めの印（ADR-006 D21 の `_backfill_authorized_by` と同じ流儀）。
_BACKFILL_META_KEY = "user_backfill_done"


def backfill_user_ids(conn: sqlite3.Connection) -> dict[str, int]:
    """既存の行（列を足した直後、既定値 `'master'` のまま残っている行）を一回きりで埋める
    （ADR-014 D2）。**印が無いときだけ走り、走ったら `meta` に印を置く**——以後は
    `manor task set --user` / `manor project set --user` で動かせる（勝手に戻さない）。

    規則:
      - project: `kind == '執事'` → `butler`、他は `master`（既定のまま）
      - task: `project_id` があればそのプロジェクトの `user_id`。無ければ
        「`owner = 'master'` かつ `source != 'idea'`」→ `master`（既定のまま）、
        それ以外（owner が butler・部下名、または意見箱）→ `butler`

    戻り値は `{"projects": n, "tasks": n}`（変更した行数。既に済みなら両方 0）。
    """
    already = conn.execute(
        "SELECT value FROM meta WHERE key = ?", (_BACKFILL_META_KEY,)
    ).fetchone()
    if already is not None:
        return {"projects": 0, "tasks": 0}

    from . import project as project_mod  # 循環 import を避けるためここで

    # project: 執事 kind のプロジェクトだけ butler へ（他は既定 master のまま）。
    cur = conn.execute(
        "UPDATE project SET user_id = ? WHERE kind = ? AND user_id = ?",
        (BUTLER_ID, project_mod.BUTLER_PROJECT_KIND, PRINCIPAL_ID),
    )
    projects_changed = cur.rowcount

    # task（プロジェクトがある）: そのプロジェクトの user_id へ（上の更新の後に読むので
    # 執事 kind のプロジェクト配下は butler が伝播する）。
    cur = conn.execute(
        "UPDATE task SET user_id = ("
        "  SELECT p.user_id FROM project p WHERE p.id = task.project_id"
        ") WHERE project_id IS NOT NULL AND user_id = ?",
        (PRINCIPAL_ID,),
    )
    tasks_changed = cur.rowcount

    # task（プロジェクト無し）: owner=master かつ source!=idea だけ master のまま。
    # それ以外（owner が butler・部下名、または意見箱）は butler へ。
    cur = conn.execute(
        "UPDATE task SET user_id = ? WHERE project_id IS NULL AND user_id = ?"
        " AND (owner != 'master' OR source = 'idea')",
        (BUTLER_ID, PRINCIPAL_ID),
    )
    tasks_changed += cur.rowcount

    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, '1')"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (_BACKFILL_META_KEY,),
    )
    return {"projects": projects_changed, "tasks": tasks_changed}


def list_users(conn: sqlite3.Connection, *, include_archived: bool = False) -> list[dict[str, object]]:
    """一覧。並び: `principal` → `member`（`created_at` 順）→ `butler`。"""
    sql = "SELECT * FROM user WHERE 1=1"
    if not include_archived:
        sql += " AND archived_at IS NULL"
    sql += (
        " ORDER BY CASE role WHEN 'principal' THEN 0 WHEN 'member' THEN 1"
        " WHEN 'butler' THEN 2 ELSE 3 END, created_at, id"
    )
    return [dict(r) for r in conn.execute(sql).fetchall()]


def _row(conn: sqlite3.Connection, user_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM user WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise ManorError(
            f"user が見つかりません: {user_id}",
            code=2,
            key="error.user.not_found",
            params={"user_id": user_id},
        )
    return row


def get(conn: sqlite3.Connection, user_id: str) -> dict[str, object]:
    return dict(_row(conn, user_id))


def exists_active(conn: sqlite3.Connection, user_id: str) -> bool:
    """あって畳まれていない。"""
    row = conn.execute(
        "SELECT 1 FROM user WHERE id = ? AND archived_at IS NULL", (user_id,)
    ).fetchone()
    return row is not None


def principal_id(conn: sqlite3.Connection) -> str:
    """`role='principal'` の id（無ければ `PRINCIPAL_ID`＝`"master"`）。"""
    row = conn.execute(
        "SELECT id FROM user WHERE role = 'principal' ORDER BY id LIMIT 1"
    ).fetchone()
    return str(row["id"]) if row is not None else PRINCIPAL_ID


def _next_auto_id(conn: sqlite3.Connection) -> str:
    """`u2`, `u3`, … のうち既存の id（畳んだものも含む）と衝突しない最小の n。"""
    existing = {str(r["id"]) for r in conn.execute("SELECT id FROM user").fetchall()}
    n = 2
    while f"u{n}" in existing:
        n += 1
    return f"u{n}"


def add(
    conn: sqlite3.Connection,
    name: str,
    *,
    user_id: str | None = None,
    role: str = "member",
) -> str:
    """利用者を1人足す。`role` は `member` しか作れない（`principal`/`butler` は種でしか
    作らない。ADR-014 D1）。`user_id` を省略すれば `u2`, `u3`… と機械が振る
    （ADR-014 §4「聞きすぎない」）。
    """
    name = (name or "").strip()
    if not name:
        raise ManorError("name が空です", code=2, key="error.user.name_empty")
    if role != "member":
        raise ManorError(
            f"role はここでは member しか作れません: {role!r}"
            "（principal/butler は種でしか作らない。ADR-014 D1）",
            code=2,
            key="error.user.role_not_creatable",
            params={"role": repr(role)},
        )

    if user_id is None:
        resolved_id = _next_auto_id(conn)
    else:
        resolved_id = user_id.strip()
        if not _ID_RE.match(resolved_id):
            raise ManorError(
                f"id の形式が不正です: {resolved_id!r}"
                "（小文字英数字と-_、先頭は英字、32字以内）",
                code=2,
                key="error.user.id_format_invalid",
                params={"user_id": repr(resolved_id)},
            )
        existing = conn.execute("SELECT 1 FROM user WHERE id = ?", (resolved_id,)).fetchone()
        if existing is not None:
            raise ManorError(
                f"id が重複しています: {resolved_id}",
                code=2,
                key="error.user.id_duplicate",
                params={"user_id": resolved_id},
            )

    now = util.now()
    conn.execute(
        "INSERT INTO user (id, name, role, created_at, archived_at) VALUES (?, ?, 'member', ?, NULL)",
        (resolved_id, name, now),
    )
    return resolved_id


def set(conn: sqlite3.Connection, user_id: str, *, name: str | None = None) -> str:
    """名前を書き換える（記号 `id` は変わらない）。"""
    _row(conn, user_id)
    if name is not None:
        name = name.strip()
        if not name:
            raise ManorError("name が空です", code=2, key="error.user.name_empty")
        conn.execute("UPDATE user SET name = ? WHERE id = ?", (name, user_id))
    return user_id


def archive(conn: sqlite3.Connection, user_id: str) -> str:
    """畳む（論理削除。記録が指しているので消さない）。`principal`/`butler` は畳めない
    （ADR-014 D1）。既に畳んでいれば何もしない（冪等）。
    """
    row = _row(conn, user_id)
    if str(row["role"]) in ("principal", "butler"):
        raise ManorError(
            f"{row['role']} は畳めません（ADR-014 D1）",
            code=2,
            key="error.user.protected_archive",
            params={"role": str(row["role"])},
        )
    if row["archived_at"] is not None:
        return user_id
    conn.execute("UPDATE user SET archived_at = ? WHERE id = ?", (util.now(), user_id))
    return user_id


def resolve_default(
    conn: sqlite3.Connection,
    *,
    explicit: str | None = None,
    project_user_id: str | None = None,
    owner: str = "butler",
) -> str:
    """「誰の件か」の既定を決める（ADR-014 D2）。この順で見る:

    1. 明示の `explicit`
    2. プロジェクトの `project_user_id`
    3. 文脈の利用者（環境変数 `MANOR_USER`。畳んでいなければ）
    4. それも無ければ `owner == 'master'` なら `principal_id(conn)`、他は `BUTLER_ID`

    `explicit` / `MANOR_USER` が知らない・畳んだ利用者を指していれば `ManorError(code=2)`。
    """
    if explicit:
        if not exists_active(conn, explicit):
            raise ManorError(
                f"user が見つからない、または畳まれています: {explicit}",
                code=2,
                key="error.user.unknown_or_archived",
                params={"user_id": explicit},
            )
        return explicit

    if project_user_id:
        return project_user_id

    env_user = os.environ.get(ENV_USER, "").strip()
    if env_user:
        if not exists_active(conn, env_user):
            raise ManorError(
                f"MANOR_USER が指す利用者が見つからない、または畳まれています: {env_user}",
                code=2,
                key="error.user.env_unknown_or_archived",
                params={"user_id": env_user},
            )
        return env_user

    if owner == "master":
        return principal_id(conn)
    return BUTLER_ID


# --- CLI（`manor user ...`。ADR-014 D6） -----------------------------------------------


def _print_json(obj: object) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_user_list(conn: sqlite3.Connection, home, args: argparse.Namespace) -> object:
    rows = list_users(conn, include_archived=args.all)
    if args.json:
        return rows
    if not rows:
        return i18n.t("common.none")
    lines = []
    for r in rows:
        archived = i18n.t("user.archived_tag") if r["archived_at"] else ""
        lines.append(
            i18n.t("user.list.line", id=r["id"], name=r["name"], role=r["role"], archived=archived)
        )
    return "\n".join(lines)


def cmd_user_add(conn: sqlite3.Connection, home, args: argparse.Namespace) -> object:
    user_id = add(conn, args.name, user_id=args.id)
    if args.json:
        return {"id": user_id}
    return i18n.t("common.created", id=user_id)


def cmd_user_set(conn: sqlite3.Connection, home, args: argparse.Namespace) -> object:
    user_id = set(conn, args.id, name=args.name)
    if args.json:
        return {"id": user_id}
    return i18n.t("common.updated", id=user_id)


def cmd_user_archive(conn: sqlite3.Connection, home, args: argparse.Namespace) -> object:
    user_id = archive(conn, args.id)
    if args.json:
        return {"id": user_id}
    return i18n.t("user.archive.done", id=user_id)


def register(subparsers: "argparse._SubParsersAction") -> None:
    """`manor user list|add|set|archive` を足す。core の `build_parser` が呼ぶ。"""
    user_p = subparsers.add_parser("user", help=i18n.t("cli.user.help"))
    user_sub = user_p.add_subparsers(dest="verb")

    p = user_sub.add_parser("list")
    p.add_argument("--all", action="store_true", help=i18n.t("cli.user.list.all.help"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_user_list, is_write=False)

    p = user_sub.add_parser("add")
    p.add_argument("name")
    p.add_argument("--id", help=i18n.t("cli.user.add.id.help"))
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_user_add, is_write=True)

    p = user_sub.add_parser("set")
    p.add_argument("id")
    p.add_argument("--name")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_user_set, is_write=True)

    p = user_sub.add_parser("archive")
    p.add_argument("id")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_user_archive, is_write=True)
