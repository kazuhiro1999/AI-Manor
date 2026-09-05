"""タスクの種類（ADR-010 D2「タスクの種類を新設する（人に意味がある分類）」）。

主人が見る分類——並べ替え・絞り込み・振り返りのための札であって、`level`（自律の
度合い）を決める機構ではない（D1 の「行動クラス」＝ `policy.py` とは別物。混ぜない。
D3「やらないこと」）。語彙は `task_kind` 表に持ち、**Web から足す・名前を変える・隠す
ができる**（ADR-010 指摘6の後半）。

CLI（`manor kind ...`）と Web（`GET/POST/PUT/DELETE /api/v1/task-kinds`）の両方が
ここの関数を呼ぶ（`rule.py` と同じ流儀。ADR-005 §2「CLI と API の両方から同じ関数」）。
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3

from . import i18n, util
from .errors import ManorError

#: id の形式。小文字英数字とアンダースコア、先頭は英字（`manor kind add <id>` を執事が
#: CLI から打つ運用を想定し、既存の語彙 id と同じ質の制約を掛ける）。
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")

#: `other` は消せない（D2「分類できないものの受け皿。消せると『種類が無い』状態が
#: 表現できなくなる」）。archive はもちろん、rename（label を書き換えて別物にする）も
#: 同じ理由で拒む——id はそのままでも「その他」という受け皿の意味を主人が壊せてしまう。
PROTECTED_ID = "other"

#: 既定の8つ（ADR-010 D2 の表そのまま。**多すぎないこと**を主人が指定）。
#: `db.init` が `task_kind` の空チェックをした上でここから流し込む（`seed_defaults`）。
#: 増減・改名は主人が Web/CLI から行う——ここを書き換えて既定を増やさない（D3「やらないこと」）。
DEFAULTS: tuple[tuple[str, str], ...] = (
    ("research", "調査・情報収集"),
    ("design", "検討・設計"),
    ("build", "作成・実装"),
    ("fix", "修正・改善"),
    ("write", "資料・文章の作成"),
    ("contact", "連絡・調整"),
    ("admin", "手続き・事務"),
    ("other", "その他"),
)


def seed_defaults(conn: sqlite3.Connection) -> None:
    """`task_kind` が空のときだけ既定の8つを入れる。

    **既に1件でもあれば何もしない**——主人が隠した（archived_at を付けた）ものを
    再挿入したり、消したはずの id を復活させたりしない（ADR-010 D2）。`db.init` から
    呼ぶ（`migrate_core` は表・列は当てるが seed はしない。`db.py` の docstring 参照）。
    """
    row = conn.execute("SELECT 1 FROM task_kind LIMIT 1").fetchone()
    if row is not None:
        return
    for i, (kind_id, label) in enumerate(DEFAULTS):
        conn.execute(
            "INSERT INTO task_kind (id, label, sort, archived_at) VALUES (?, ?, ?, NULL)",
            (kind_id, label, i),
        )


def _row(conn: sqlite3.Connection, kind_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM task_kind WHERE id = ?", (kind_id,)).fetchone()
    if row is None:
        raise ManorError(
            f"task_kind が見つかりません: {kind_id}",
            code=2,
            key="error.task_kind.not_found",
            params={"kind_id": kind_id},
        )
    return row


def _check_id_format(kind_id: str) -> None:
    if not _ID_RE.match(kind_id):
        raise ManorError(
            f"id の形式が不正です: {kind_id!r}（小文字英数字とアンダースコア、先頭は英字）",
            code=2,
            key="error.task_kind.id_format_invalid",
            params={"kind_id": repr(kind_id)},
        )


def list_kinds(conn: sqlite3.Connection, *, include_archived: bool = False) -> list[dict[str, object]]:
    """一覧。既定はアーカイブ済みを除く。表示順は `sort`（同着は id）。"""
    sql = "SELECT * FROM task_kind WHERE 1=1"
    if not include_archived:
        sql += " AND archived_at IS NULL"
    sql += " ORDER BY sort, id"
    return [dict(r) for r in conn.execute(sql).fetchall()]


def add(conn: sqlite3.Connection, kind_id: str, label: str) -> dict[str, object]:
    """種類を1件足す。`id` は語彙の形式（`_ID_RE`）に沿い、既存と重複しないこと。"""
    kind_id = (kind_id or "").strip()
    label = (label or "").strip()
    _check_id_format(kind_id)
    if not label:
        raise ManorError("label が空です", code=2, key="error.task_kind.label_empty")
    existing = conn.execute("SELECT 1 FROM task_kind WHERE id = ?", (kind_id,)).fetchone()
    if existing is not None:
        raise ManorError(
            f"id が重複しています: {kind_id}",
            code=2,
            key="error.task_kind.id_duplicate",
            params={"kind_id": kind_id},
        )
    next_sort_row = conn.execute("SELECT COALESCE(MAX(sort), -1) + 1 AS n FROM task_kind").fetchone()
    next_sort = int(next_sort_row["n"])
    conn.execute(
        "INSERT INTO task_kind (id, label, sort, archived_at) VALUES (?, ?, ?, NULL)",
        (kind_id, label, next_sort),
    )
    return dict(_row(conn, kind_id))


def rename(conn: sqlite3.Connection, kind_id: str, label: str) -> dict[str, object]:
    """`label` を書き換える（id はそのまま——素の改名。`other` は拒む）。"""
    _row(conn, kind_id)
    if kind_id == PROTECTED_ID:
        raise ManorError(
            f"{PROTECTED_ID!r} は改名できません（分類できないものの受け皿。ADR-010 D2）",
            code=2,
            key="error.task_kind.protected_rename",
            params={"protected_id": repr(PROTECTED_ID)},
        )
    label = (label or "").strip()
    if not label:
        raise ManorError("label が空です", code=2, key="error.task_kind.label_empty")
    conn.execute("UPDATE task_kind SET label = ? WHERE id = ?", (label, kind_id))
    return dict(_row(conn, kind_id))


def archive(conn: sqlite3.Connection, kind_id: str) -> dict[str, object]:
    """隠す（論理削除。物理削除はしない）。**既にその種類が付いた task はそのまま
    残る**——ここでは `task.kind` を一切書き換えない（D2「過去の記録を書き換えない」）。
    `other` は拒む。
    """
    _row(conn, kind_id)
    if kind_id == PROTECTED_ID:
        raise ManorError(
            f"{PROTECTED_ID!r} はアーカイブできません（分類できないものの受け皿。ADR-010 D2）",
            code=2,
            key="error.task_kind.protected_archive",
            params={"protected_id": repr(PROTECTED_ID)},
        )
    now = util.now()
    conn.execute("UPDATE task_kind SET archived_at = ? WHERE id = ?", (now, kind_id))
    return dict(_row(conn, kind_id))


def unarchive(conn: sqlite3.Connection, kind_id: str) -> dict[str, object]:
    """隠したのを戻す。"""
    _row(conn, kind_id)
    conn.execute("UPDATE task_kind SET archived_at = NULL WHERE id = ?", (kind_id,))
    return dict(_row(conn, kind_id))


def is_valid(conn: sqlite3.Connection, kind_id: str) -> bool:
    """`task.kind` として新しく使ってよいか。**空文字はここに来ない**——`task.py` が
    「種類は必須ではない」（D2）を先に通し、非空の id だけをここへ渡す。アーカイブ済みの
    id は使えない（隠した語彙を新しい起票には使わせない）。**既存 task の `kind` 列は
    この検査を経由しない**——`archive` は task 側の行を一切書き換えないので、隠した
    あとも過去の task はそのままその id を持ち続ける（D2）。
    """
    row = conn.execute(
        "SELECT 1 FROM task_kind WHERE id = ? AND archived_at IS NULL", (kind_id,)
    ).fetchone()
    return row is not None


# --- CLI（`manor kind ...`。ADR-010 D2） -----------------------------------------------


def _print_json(obj: object) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_kind_list(conn: sqlite3.Connection, home, args: argparse.Namespace) -> object:
    rows = list_kinds(conn, include_archived=args.all)
    if args.json:
        return rows
    if not rows:
        return i18n.t("common.none")
    lines = []
    for r in rows:
        archived = i18n.t("kind.archived_tag") if r["archived_at"] else ""
        lines.append(i18n.t("kind.list.line", id=r["id"], label=r["label"], archived=archived))
    return "\n".join(lines)


def cmd_kind_add(conn: sqlite3.Connection, home, args: argparse.Namespace) -> object:
    result = add(conn, args.id, args.label)
    if args.json:
        return result
    return i18n.t("common.created", id=f"task_kind {result['id']}")


def cmd_kind_rename(conn: sqlite3.Connection, home, args: argparse.Namespace) -> object:
    result = rename(conn, args.id, args.label)
    if args.json:
        return result
    return i18n.t("kind.rename.done", id=result["id"])


def cmd_kind_archive(conn: sqlite3.Connection, home, args: argparse.Namespace) -> object:
    result = archive(conn, args.id)
    if args.json:
        return result
    return i18n.t("kind.archive.done", id=args.id)


def cmd_kind_unarchive(conn: sqlite3.Connection, home, args: argparse.Namespace) -> object:
    result = unarchive(conn, args.id)
    if args.json:
        return result
    return i18n.t("kind.unarchive.done", id=args.id)


def register(subparsers: "argparse._SubParsersAction") -> None:
    """`manor kind list|add|rename|archive|unarchive` を足す。core の `build_parser` が呼ぶ。"""
    kind_p = subparsers.add_parser("kind", help=i18n.t("cli.kind.help"))
    kind_sub = kind_p.add_subparsers(dest="verb")

    p = kind_sub.add_parser("list")
    p.add_argument("--all", action="store_true", help=i18n.t("cli.kind.list.all.help"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_kind_list, is_write=False)

    p = kind_sub.add_parser("add")
    p.add_argument("id")
    p.add_argument("label")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_kind_add, is_write=True)

    p = kind_sub.add_parser("rename")
    p.add_argument("id")
    p.add_argument("label")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_kind_rename, is_write=True)

    p = kind_sub.add_parser("archive")
    p.add_argument("id")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_kind_archive, is_write=True)

    p = kind_sub.add_parser("unarchive")
    p.add_argument("id")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_kind_unarchive, is_write=True)
