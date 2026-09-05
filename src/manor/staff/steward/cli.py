"""`manor money ...`（ADR-002 §5）。`register(subparsers)` を core の cli.py が拾う（ADR-001 §11）。

**支払い・送金・口座やカードへの接触は一切しない。** `paid` は主人が払ったと言ったときの
記録であって、支払いの実行ではない。認証情報（口座番号・カード番号・ログイン情報）は
求めない・受け取らない・保存しない（`schema.sql` にその列を作らない設計が根）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from manor import i18n
from manor import util
from manor.errors import ManorError

from . import importer, ops

VALID_RECURRING_KINDS: frozenset[str] = frozenset({"subscription", "bill", "income"})
VALID_IMPORT_FORMATS: frozenset[str] = frozenset({"generic", "zaim", "moneyforward"})


def _resolve_recurring(conn: sqlite3.Connection, ref: str) -> sqlite3.Row:
    """`<name|id>` のどちらでも受ける（ADR-002 §5 の `paid <name|id>` / `stop <name|id>`）。"""
    row: sqlite3.Row | None = None
    try:
        rid = int(ref)
    except ValueError:
        rid = None
    if rid is not None:
        row = conn.execute("SELECT * FROM steward_recurring WHERE id = ?", (rid,)).fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM steward_recurring WHERE name = ?", (ref,)).fetchone()
    if row is None:
        raise ManorError(
            f"定期支払いが見つかりません: {ref!r}",
            code=2,
            key="error.money.recurring_not_found",
            params={"ref": repr(ref)},
        )
    return row


# --- money log / month / due / trend ---------------------------------------------------


def cmd_log(conn: sqlite3.Connection, home, args) -> object:
    amount = ops.parse_amount(args.amount)
    date_ = args.date or util.today()
    ops.parse_date(date_)
    kind = "income" if args.income else "expense"
    cur = conn.execute(
        "INSERT INTO steward_expense (date, amount, kind, category, memo, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (date_, amount, kind, args.category, args.memo, util.now()),
    )
    row_id = cur.lastrowid
    if args.json:
        return {"id": row_id, "date": date_, "amount": amount, "kind": kind, "category": args.category}
    label = i18n.t("money.log.kind_income") if kind == "income" else i18n.t("money.log.kind_expense")
    return i18n.t("money.log.done", label=label, date=date_, category=args.category, amount=amount, id=row_id)


def cmd_month(conn: sqlite3.Connection, home, args) -> object:
    ym = args.ym or util.today()[:7]
    ym = ops.parse_ym(ym)
    rows = [
        dict(r)
        for r in conn.execute("SELECT date, amount, kind, category, memo FROM steward_expense")
    ]
    budgets = {
        str(r["category"]): int(r["monthly_limit"])
        for r in conn.execute("SELECT category, monthly_limit FROM steward_budget")
    }
    summary = ops.month_summary(rows, budgets, ym)
    if args.json:
        return summary
    if not summary["expenses"] and not summary["income"]:
        return i18n.t("money.month.no_records", ym=ym)
    lines = [i18n.t("money.month.header", ym=ym)]
    for e in summary["expenses"]:
        if e["budget"] is None:
            budget_part = i18n.t("money.month.budget_unset")
        else:
            budget_part = i18n.t("money.month.budget_line", budget=e["budget"], diff=f"{e['diff']:+d}")
        mark = i18n.t("money.month.over_mark") if e["over"] else ""
        lines.append(
            i18n.t("money.month.expense_line", mark=mark, category=e["category"], spent=e["spent"], budget_part=budget_part)
        )
    lines.append(i18n.t("money.month.total_expense", total=summary["total_expense"]))
    lines.append(i18n.t("money.month.income", income=summary["income"]))
    return "\n".join(lines)


def cmd_due(conn: sqlite3.Connection, home, args) -> object:
    rows = [dict(r) for r in conn.execute("SELECT * FROM steward_recurring")]
    items = ops.upcoming(rows, util.today(), args.days)
    if args.json:
        return items
    if not items:
        return i18n.t("common.none")
    lines = []
    for it in items:
        overdue = it["overdue_days"]
        if overdue > 0:
            when = i18n.t("money.due.overdue_days", days=overdue)
        elif overdue == 0:
            when = i18n.t("money.due.today")
        else:
            when = i18n.t("money.due.days_until", days=-overdue)
        lines.append(
            i18n.t(
                "money.due.line",
                next_due=it["next_due"], when=when, name=it["name"], amount=it["amount"], category=it["category"],
            )
        )
    return "\n".join(lines)


def cmd_trend(conn: sqlite3.Connection, home, args) -> object:
    rows = [dict(r) for r in conn.execute("SELECT date, amount, kind, category FROM steward_expense")]
    result = ops.trend(rows, args.months)
    if args.json:
        return result
    if not result:
        return i18n.t("money.trend.empty")
    lines = []
    for m in result:
        lines.append(i18n.t("money.trend.line", ym=m["ym"], expense=m["total_expense"], income=m["total_income"]))
    return "\n".join(lines)


# --- money import（ADR-005 §2「imports」） ---------------------------------------------


def existing_import_hashes(conn: sqlite3.Connection) -> frozenset[str]:
    """既に取り込み済みの行の指紋（`import_hash`）の集合。重複判定に使う。"""
    rows = conn.execute(
        "SELECT import_hash FROM steward_expense WHERE import_hash IS NOT NULL"
    ).fetchall()
    return frozenset(str(r["import_hash"]) for r in rows)


def insert_import_rows(conn: sqlite3.Connection, rows: list[importer.ImportRow]) -> int:
    """取り込んだ行を `steward_expense` へ INSERT する（`import_hash` つき）。

    CLI（`manor money import`）と Web（`POST /api/v1/imports/money/commit`）の両方が
    ここを呼ぶ——INSERT 文を2箇所に書き写さない（D3「SQL の INSERT/UPDATE を web 層に書かない」
    の徹底。web 層はこの関数を呼ぶだけで、SQL 自体は steward の中に閉じる）。
    """
    now = util.now()
    for r in rows:
        conn.execute(
            "INSERT INTO steward_expense (date, amount, kind, category, memo, created_at, import_hash)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (r.date, r.amount, r.kind, r.category, r.memo, now, r.import_hash),
        )
    return len(rows)


def preview_import(
    conn: sqlite3.Connection, text: str, *, fmt: str, map_spec: str | None
) -> importer.ImportResult:
    """CSV 本文から、書き込まずに `ImportResult`（rows/duplicates/unreadable/total）を作る。"""
    column_map = importer.resolve_map(fmt, map_spec)
    existing = existing_import_hashes(conn)
    return importer.parse_csv(text, column_map, existing_hashes=existing)


def cmd_money_import(conn: sqlite3.Connection, home, args) -> object:
    """`manor money import <csv> --format .. [--map ..] [--encoding ..] [--dry-run]`。

    プリセット（zaim/moneyforward）の列名は**推定**——実物の CSV と違えば `--map` で
    上書きする（ADR-005 §2）。`generic` は `--map` が必須。
    """
    if args.format not in VALID_IMPORT_FORMATS:
        known = " / ".join(sorted(VALID_IMPORT_FORMATS))
        raise ManorError(
            f"語彙外の --format です: {args.format!r}（使えるのは {known}）",
            code=2,
            key="error.money.import_format_unknown",
            params={"format": repr(args.format), "known": known},
        )
    encoding = args.encoding or "utf-8"
    try:
        raw_bytes = Path(args.csv).read_bytes()
    except OSError as exc:
        raise ManorError(
            f"CSV を読めません: {args.csv}（{exc}）",
            code=2,
            key="error.money.import_csv_unreadable",
            params={"csv": args.csv, "exc": str(exc)},
        ) from exc
    try:
        text = raw_bytes.decode(encoding)
    except (LookupError, UnicodeDecodeError) as exc:
        raise ManorError(
            f"CSV を {encoding} として読めません: {exc}",
            code=2,
            key="error.money.import_csv_decode_failed",
            params={"encoding": encoding, "exc": str(exc)},
        ) from exc

    result = preview_import(conn, text, fmt=args.format, map_spec=args.map)

    if args.dry_run:
        payload = {
            "dry_run": True,
            "total": result.total,
            "to_insert": len(result.rows),
            "duplicates": len(result.duplicates),
            "unreadable": len(result.unreadable),
            "rows": [r.to_dict() for r in result.rows],
            "duplicate_rows": [r.to_dict() for r in result.duplicates],
            "unreadable_rows": [r.to_dict() for r in result.unreadable],
        }
        if args.json:
            return payload
        lines = [
            i18n.t(
                "money.import.dry_run_header",
                total=result.total, to_insert=len(result.rows),
                duplicates=len(result.duplicates), unreadable=len(result.unreadable),
            )
        ]
        for r in result.unreadable:
            lines.append(i18n.t("money.import.unreadable_line", line=r.line, reason=r.reason))
        return "\n".join(lines)

    inserted = insert_import_rows(conn, result.rows)
    payload = {
        "inserted": inserted,
        "skipped": len(result.duplicates),
        "unreadable": len(result.unreadable),
        "total": result.total,
    }
    if args.json:
        return payload
    return i18n.t(
        "money.import.done",
        inserted=inserted, duplicates=len(result.duplicates), unreadable=len(result.unreadable),
    )


# --- money recurring --------------------------------------------------------------------


def cmd_recurring_list(conn: sqlite3.Connection, home, args) -> object:
    rows = [dict(r) for r in conn.execute("SELECT * FROM steward_recurring ORDER BY next_due")]
    if args.json:
        return rows
    if not rows:
        return i18n.t("common.none")
    lines = []
    for r in rows:
        state = i18n.t("money.recurring.stopped_mark") if not r["active"] else ""
        lines.append(
            i18n.t(
                "money.recurring.list_line",
                id=r["id"], name=r["name"], amount=r["amount"], cycle=r["cycle"], next_due=r["next_due"],
                state=state,
            )
        )
    return "\n".join(lines)


def cmd_recurring_add(conn: sqlite3.Connection, home, args) -> object:
    amount = ops.parse_amount(args.amount)
    if args.cycle not in ops.VALID_CYCLES:
        raise ManorError(
            f"語彙外の cycle です: {args.cycle!r}",
            code=2,
            key="error.money.cycle_unknown",
            params={"cycle": repr(args.cycle)},
        )
    ops.parse_date(args.next_due)
    if args.kind not in VALID_RECURRING_KINDS:
        raise ManorError(
            f"語彙外の kind です: {args.kind!r}",
            code=2,
            key="error.money.recurring_kind_unknown",
            params={"kind": repr(args.kind)},
        )
    try:
        cur = conn.execute(
            "INSERT INTO steward_recurring (name, amount, cycle, next_due, category, kind, note)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (args.name, amount, args.cycle, args.next_due, args.category, args.kind, args.note),
        )
    except sqlite3.IntegrityError as exc:
        raise ManorError(
            f"同名の定期支払いが既にあります: {args.name!r}",
            key="error.money.recurring_duplicate_name",
            params={"name": repr(args.name)},
        ) from exc
    row_id = cur.lastrowid
    if args.json:
        return {"id": row_id, "name": args.name, "next_due": args.next_due}
    return i18n.t("money.recurring.add_done", name=args.name, next_due=args.next_due, amount=amount, cycle=args.cycle)


def cmd_recurring_paid(conn: sqlite3.Connection, home, args) -> object:
    """支出に1行記録し `next_due` を1周期進める。

    **これは支払いの実行ではない。** 主人が払ったと言ったことの記録に過ぎない
    （ADR-002 §5。定義と docs に太字で書く約束）。
    """
    row = _resolve_recurring(conn, args.ref)
    on = args.on or util.today()
    ops.parse_date(on)
    kind = "income" if row["kind"] == "income" else "expense"
    conn.execute(
        "INSERT INTO steward_expense (date, amount, kind, category, memo, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (on, row["amount"], kind, row["category"], f"定期: {row['name']}", util.now()),
    )
    next_due = ops.advance(str(row["next_due"]), str(row["cycle"]))
    conn.execute("UPDATE steward_recurring SET next_due = ? WHERE id = ?", (next_due, row["id"]))
    if args.json:
        return {"id": row["id"], "name": row["name"], "paid_on": on, "next_due": next_due}
    return i18n.t("money.recurring.paid_done", name=row["name"], next_due=next_due)


def cmd_recurring_stop(conn: sqlite3.Connection, home, args) -> object:
    row = _resolve_recurring(conn, args.ref)
    conn.execute("UPDATE steward_recurring SET active = 0 WHERE id = ?", (row["id"],))
    if args.json:
        return {"id": row["id"], "name": row["name"], "active": 0}
    return i18n.t("money.recurring.stop_done", name=row["name"])


# --- money budget ------------------------------------------------------------------------


def cmd_budget_show(conn: sqlite3.Connection, home, args) -> object:
    rows = [
        dict(r)
        for r in conn.execute("SELECT category, monthly_limit FROM steward_budget ORDER BY category")
    ]
    if args.json:
        return rows
    if not rows:
        return i18n.t("common.none")
    return "\n".join(i18n.t("money.budget.show_line", category=r["category"], limit=r["monthly_limit"]) for r in rows)


def cmd_budget_set(conn: sqlite3.Connection, home, args) -> object:
    limit = ops.parse_amount(args.limit)
    conn.execute(
        "INSERT INTO steward_budget (category, monthly_limit) VALUES (?, ?)"
        " ON CONFLICT(category) DO UPDATE SET monthly_limit = excluded.monthly_limit",
        (args.category, limit),
    )
    if args.json:
        return {"category": args.category, "monthly_limit": limit}
    return i18n.t("money.budget.set_done", category=args.category, limit=limit)


# --- パーサ組み立て --------------------------------------------------------------------


def register(subparsers) -> None:
    money_p = subparsers.add_parser("money", help=i18n.t("cli.money.help"))
    money_sub = money_p.add_subparsers(dest="verb")

    p = money_sub.add_parser("log")
    p.add_argument("--amount", required=True)
    p.add_argument("--category", required=True)
    p.add_argument("--date")
    p.add_argument("--memo", default="")
    p.add_argument("--income", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_log, is_write=True)

    p = money_sub.add_parser("month")
    p.add_argument("--ym")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_month, is_write=False)

    p = money_sub.add_parser("due")
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_due, is_write=False)

    p = money_sub.add_parser("trend")
    p.add_argument("--months", type=int, default=6)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_trend, is_write=False)

    p = money_sub.add_parser("import", help=i18n.t("cli.money.import.help"))
    p.add_argument("csv", help=i18n.t("cli.money.import.csv.help"))
    p.add_argument("--format", default="generic", help=i18n.t("cli.money.import.format.help"))
    p.add_argument("--map", help=i18n.t("cli.money.import.map.help"))
    p.add_argument("--encoding", default="utf-8", help=i18n.t("cli.money.import.encoding.help"))
    p.add_argument("--dry-run", action="store_true", dest="dry_run")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_money_import, is_write=True)

    # --- recurring ---
    recurring_p = money_sub.add_parser("recurring")
    recurring_sub = recurring_p.add_subparsers(dest="subverb")

    p = recurring_sub.add_parser("list")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_recurring_list, is_write=False)

    p = recurring_sub.add_parser("add")
    p.add_argument("name")
    p.add_argument("--amount", required=True)
    p.add_argument("--cycle", required=True)
    p.add_argument("--next", required=True, dest="next_due")
    p.add_argument("--category", required=True)
    p.add_argument("--kind", default="bill")
    p.add_argument("--note", default="")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_recurring_add, is_write=True)

    p = recurring_sub.add_parser("paid")
    p.add_argument("ref")
    p.add_argument("--on")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_recurring_paid, is_write=True)

    p = recurring_sub.add_parser("stop")
    p.add_argument("ref")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_recurring_stop, is_write=True)

    # --- budget ---
    budget_p = money_sub.add_parser("budget")
    budget_sub = budget_p.add_subparsers(dest="subverb")

    p = budget_sub.add_parser("show")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_budget_show, is_write=False)

    p = budget_sub.add_parser("set")
    p.add_argument("category")
    p.add_argument("limit")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_budget_set, is_write=True)
