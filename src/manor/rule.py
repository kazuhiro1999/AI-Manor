"""家庭のルール（ADR-005 §2「rules」・ADR-004 D5）。

タスクでも判断待ちでもない「知識」を持つための表——`rule`（`node` には紐づかない
独立の表。ADR-005 §2 のスキーマそのまま）。担当（LLM）は判断の前に
`manor rule list --tag <領域>` で引いてから動く（`.claude/agents/*.md` への配線は次の段）。

CLI（`manor rule ...`）と Web（`GET/POST/PUT/DELETE /api/v1/rules`）の両方が
ここの関数を呼ぶ（ADR-005 §2「CLI と API の両方から同じ関数」）。
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3

from . import i18n, util
from .errors import ManorError

VALID_SCOPES: frozenset[str] = frozenset({"family", "adults", "kids", "guests", "staff"})

#: タグの区切り。**読点（、）とカンマ（, ／ ，）の両方**を受ける（ADR-005 §7「読点・カンマ
#: 両方で区切る」の裁定）。日本語の入力は読点、コピペした英語圏の CSV はカンマになりがち
#: なので、両対応にしておかないと検索（`--tag`）が静かに外れる。
_TAG_SPLIT_RE = re.compile(r"[、,，]")


def split_tags(text: str | None) -> list[str]:
    """タグ文字列を個々のタグへ割る。前後の空白は落とし、空要素は捨てる。"""
    if not text:
        return []
    return [t.strip() for t in _TAG_SPLIT_RE.split(text) if t.strip()]


def _row(conn: sqlite3.Connection, rule_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM rule WHERE id = ?", (rule_id,)).fetchone()
    if row is None:
        raise ManorError(
            f"rule が見つかりません: {rule_id}",
            code=2,
            key="error.rule.not_found",
            params={"rule_id": rule_id},
        )
    return row


def _check_scope(scope: str) -> None:
    if scope not in VALID_SCOPES:
        known = " / ".join(sorted(VALID_SCOPES))
        raise ManorError(
            f"語彙外の scope です: {scope!r}（使えるのは {known}）",
            code=2,
            key="error.rule.scope_unknown",
            params={"scope": repr(scope), "known": known},
        )


def add(
    conn: sqlite3.Connection,
    title: str,
    *,
    body: str = "",
    scope: str = "family",
    tags: str = "",
    effective_from: str | None = None,
    effective_to: str | None = None,
) -> int:
    """ルールを1件作る。`scope` は既定 `family`。"""
    if not title.strip():
        raise ManorError("title が空です", code=2, key="error.rule.title_empty")
    _check_scope(scope)
    now = util.now()
    cur = conn.execute(
        "INSERT INTO rule (title, body, scope, tags, effective_from, effective_to,"
        " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (title, body, scope, tags, effective_from, effective_to, now, now),
    )
    return int(cur.lastrowid or 0)


def get(conn: sqlite3.Connection, rule_id: int) -> dict[str, object]:
    row = _row(conn, rule_id)
    out = dict(row)
    out["tag_list"] = split_tags(str(row["tags"]))
    return out


def set(
    conn: sqlite3.Connection,
    rule_id: int,
    *,
    title: str | None = None,
    body: str | None = None,
    scope: str | None = None,
    tags: str | None = None,
    effective_from: str | None = None,
    effective_to: str | None = None,
) -> int:
    """既存のルールを部分更新する。渡さなかった項目は変えない（`task.set` と同じ流儀）。"""
    _row(conn, rule_id)
    if scope is not None:
        _check_scope(scope)

    fields: dict[str, object] = {}
    if title is not None:
        if not title.strip():
            raise ManorError("title が空です", code=2, key="error.rule.title_empty")
        fields["title"] = title
    if body is not None:
        fields["body"] = body
    if scope is not None:
        fields["scope"] = scope
    if tags is not None:
        fields["tags"] = tags
    if effective_from is not None:
        fields["effective_from"] = effective_from
    if effective_to is not None:
        fields["effective_to"] = effective_to
    if fields:
        fields["updated_at"] = util.now()
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE rule SET {sets} WHERE id = ?", (*fields.values(), rule_id))
    return rule_id


def archive(conn: sqlite3.Connection, rule_id: int) -> dict[str, object]:
    """アーカイブする（論理削除。物理削除はしない——過去の裁定の根拠として残す）。"""
    _row(conn, rule_id)
    now = util.now()
    conn.execute("UPDATE rule SET archived_at = ?, updated_at = ? WHERE id = ?", (now, now, rule_id))
    return {"id": rule_id, "archived_at": now}


def list_rules(
    conn: sqlite3.Connection, *, tag: str | None = None, include_archived: bool = False
) -> list[dict[str, object]]:
    """一覧。既定はアーカイブ済みを除く。`tag` を渡すと `split_tags` で割った上で一致検索する
    （部分文字列一致ではなく、タグそのものの一致——「家」で「家事」まで拾わないため）。
    """
    sql = "SELECT * FROM rule WHERE 1=1"
    if not include_archived:
        sql += " AND archived_at IS NULL"
    sql += " ORDER BY id"
    rows = [dict(r) for r in conn.execute(sql).fetchall()]
    for r in rows:
        r["tag_list"] = split_tags(str(r["tags"]))
    if tag:
        rows = [r for r in rows if tag in r["tag_list"]]
    return rows


# --- CLI（`manor rule ...`。ADR-005 §2） -----------------------------------------------


def _print_json(obj: object) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_rule_add(conn: sqlite3.Connection, home, args: argparse.Namespace) -> object:
    rule_id = add(
        conn, args.title, body=args.body, scope=args.scope, tags=args.tags,
        effective_from=args.effective_from, effective_to=args.effective_to,
    )
    if args.json:
        return {"id": rule_id}
    return i18n.t("common.created", id=f"rule#{rule_id}")


def cmd_rule_list(conn: sqlite3.Connection, home, args: argparse.Namespace) -> object:
    rows = list_rules(conn, tag=args.tag, include_archived=args.all)
    if args.json:
        return rows
    if not rows:
        return i18n.t("common.none")
    lines = []
    for r in rows:
        archived = i18n.t("rule.archived_tag") if r["archived_at"] else ""
        tags = "・".join(r["tag_list"]) if r["tag_list"] else i18n.t("common.dash")
        lines.append(
            i18n.t(
                "rule.list.line",
                id=r["id"], scope=r["scope"], title=r["title"], tags=tags, archived=archived,
            )
        )
    return "\n".join(lines)


def cmd_rule_show(conn: sqlite3.Connection, home, args: argparse.Namespace) -> object:
    data = get(conn, args.id)
    if args.json:
        return data
    lines = [
        i18n.t("rule.show.header", id=data["id"], scope=data["scope"], title=data["title"]),
        i18n.t("rule.show.tags", tags="・".join(data["tag_list"]) or i18n.t("common.dash")),
        i18n.t(
            "rule.show.period",
            from_=data["effective_from"] or i18n.t("common.dash"),
            to=data["effective_to"] or i18n.t("common.dash"),
        ),
    ]
    if data["archived_at"]:
        lines.append(i18n.t("rule.show.archived_at", archived_at=data["archived_at"]))
    if data["body"]:
        lines.append("")
        lines.append(str(data["body"]))
    return "\n".join(lines)


def cmd_rule_set(conn: sqlite3.Connection, home, args: argparse.Namespace) -> object:
    rule_id = set(
        conn, args.id, title=args.title, body=args.body, scope=args.scope, tags=args.tags,
        effective_from=args.effective_from, effective_to=args.effective_to,
    )
    if args.json:
        return {"id": rule_id}
    return i18n.t("common.updated", id=f"rule#{rule_id}")


def cmd_rule_archive(conn: sqlite3.Connection, home, args: argparse.Namespace) -> object:
    result = archive(conn, args.id)
    if args.json:
        return result
    return i18n.t("rule.archive.done", id=args.id)


def register(subparsers: "argparse._SubParsersAction") -> None:
    """`manor rule add|list|show|set|archive` を足す。core の `build_parser` が呼ぶ。"""
    rule_p = subparsers.add_parser("rule", help=i18n.t("cli.rule.help"))
    rule_sub = rule_p.add_subparsers(dest="verb")

    p = rule_sub.add_parser("add")
    p.add_argument("title")
    p.add_argument("--body", default="")
    p.add_argument("--scope", default="family", help=i18n.t("cli.rule.add.scope.help"))
    p.add_argument("--tags", default="", help=i18n.t("cli.rule.add.tags.help"))
    p.add_argument("--effective-from", dest="effective_from")
    p.add_argument("--effective-to", dest="effective_to")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_rule_add, is_write=True)

    p = rule_sub.add_parser("list")
    p.add_argument("--tag")
    p.add_argument("--all", action="store_true", help=i18n.t("cli.rule.list.all.help"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_rule_list, is_write=False)

    p = rule_sub.add_parser("show")
    p.add_argument("id", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_rule_show, is_write=False)

    p = rule_sub.add_parser("set")
    p.add_argument("id", type=int)
    p.add_argument("--title")
    p.add_argument("--body")
    p.add_argument("--scope")
    p.add_argument("--tags")
    p.add_argument("--effective-from", dest="effective_from")
    p.add_argument("--effective-to", dest="effective_to")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_rule_set, is_write=True)

    p = rule_sub.add_parser("archive")
    p.add_argument("id", type=int)
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_rule_archive, is_write=True)
