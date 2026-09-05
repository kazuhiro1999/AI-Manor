"""`manor chef ...`（ADR-002 §3）。

引数の組み立てと DB の読み書き。判断（何を勧めるか）はしない。
並べ替え・突き合わせ・集計・検証は `ops.py`（純粋関数）に委ねる。
core のパターン（`src/manor/cli.py`）に合わせ、各コマンドは
`(conn, home, args) -> str | object` を返す。
"""

from __future__ import annotations

import sqlite3

from manor import i18n, util
from manor.errors import ManorError

from . import ops

VALID_SLOTS: tuple[str, ...] = ("breakfast", "lunch", "dinner", "snack")
VALID_AISLES: tuple[str, ...] = ("野菜", "肉魚", "乳卵", "主食", "調味料", "その他")
VALID_TASTE_KEYS: tuple[str, ...] = (
    "allergies",
    "dislikes",
    "likes",
    "household_size",
    "cook_minutes",
    "equipment",
    "notes",
)


def _split_csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _pantry_items(conn: sqlite3.Connection) -> list[str]:
    return [str(r["item"]) for r in conn.execute("SELECT item FROM chef_pantry").fetchall()]


def _pantry_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM chef_pantry").fetchone()
    return int(row["n"])


def _require_pantry_not_empty(conn: sqlite3.Connection) -> None:
    """在庫が丸ごと空なら `ManorError(code=2)`。エラーではなく「申告を求めよ」の合図
    （ADR-002 §3・手順書）。空の pantry で expiring/missing を回しても意味のある答えが
    出せないので、担当（LLM）へ「在庫の申告を求める」判断を促す。
    """
    if _pantry_count(conn) == 0:
        raise ManorError(
            "在庫がまだ登録されていません。在庫の申告をお願いします"
            "（目に入ったものだけで結構です。`manor chef pantry add` で教えてください）",
            code=2,
            key="error.chef.pantry_empty",
        )


# --- pantry ---------------------------------------------------------------------


def cmd_pantry_list(conn, home, args) -> object:
    sql = "SELECT * FROM chef_pantry WHERE 1=1"
    params: list[object] = []
    if args.place:
        sql += " AND place = ?"
        params.append(args.place)
    sql += " ORDER BY item"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    if args.json:
        return rows
    if not rows:
        return i18n.t("chef.pantry.list.empty")
    return "\n".join(
        i18n.t(
            "chef.pantry.list.line",
            id=r["id"], item=r["item"], qty=r["qty"], unit=r["unit"],
            expires=r["expires"] or i18n.t("chef.common.unknown"), place=r["place"],
        )
        for r in rows
    )


def cmd_pantry_add(conn, home, args) -> object:
    expires = (
        ops.validate_date(args.expires, field="期限", field_key="chef.field.expires")
        if args.expires
        else None
    )
    now = util.now()
    cur = conn.execute(
        "INSERT INTO chef_pantry (item, qty, unit, expires, place, note, added_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (args.item, args.qty, args.unit, expires, args.place, args.note, now, now),
    )
    pantry_id = cur.lastrowid
    if args.json:
        return {"id": pantry_id, "item": args.item}
    return i18n.t("chef.pantry.add.done", item=args.item, id=pantry_id)


def _find_pantry_rows(conn: sqlite3.Connection, ref: str) -> list[sqlite3.Row]:
    if ref.isdigit():
        return list(conn.execute("SELECT * FROM chef_pantry WHERE id = ?", (int(ref),)).fetchall())
    return list(conn.execute("SELECT * FROM chef_pantry WHERE item = ?", (ref,)).fetchall())


def _resolve_single_pantry_row(conn: sqlite3.Connection, ref: str) -> sqlite3.Row:
    rows = _find_pantry_rows(conn, ref)
    if not rows:
        raise ManorError(
            f"在庫に見つかりません: {ref}",
            code=2,
            key="error.chef.pantry_not_found",
            params={"ref": ref},
        )
    if len(rows) > 1 and not ref.isdigit():
        ids = ", ".join(str(r["id"]) for r in rows)
        raise ManorError(
            f"「{ref}」は在庫に複数あります。id で指定してください: {ids}",
            code=2,
            key="error.chef.pantry_ambiguous",
            params={"ref": ref, "ids": ids},
        )
    return rows[0]


def cmd_pantry_use(conn, home, args) -> object:
    row = _resolve_single_pantry_row(conn, args.item)

    if args.all:
        conn.execute("DELETE FROM chef_pantry WHERE id = ?", (row["id"],))
        if args.json:
            return {"id": row["id"], "item": row["item"], "removed": True}
        return i18n.t("chef.pantry.use.done_all", item=row["item"])

    if not args.qty:
        raise ManorError(
            "--qty か --all のどちらかを指定してください",
            code=2,
            key="error.chef.pantry_use_missing_arg",
        )

    remaining = ops.subtract_qty(str(row["qty"]), args.qty)
    if remaining is None:
        raise ManorError(
            f"{row['item']} の数量（{row['qty']}）が不明なため差し引けません。"
            "`--all` で使い切りを記録してください",
            code=2,
            key="error.chef.pantry_qty_unknown",
            params={"item": row["item"], "qty": row["qty"]},
        )
    if remaining == "":
        conn.execute("DELETE FROM chef_pantry WHERE id = ?", (row["id"],))
        if args.json:
            return {"id": row["id"], "item": row["item"], "removed": True}
        return i18n.t("chef.pantry.use.done_all", item=row["item"])

    conn.execute(
        "UPDATE chef_pantry SET qty = ?, updated_at = ? WHERE id = ?",
        (remaining, util.now(), row["id"]),
    )
    if args.json:
        return {"id": row["id"], "item": row["item"], "qty": remaining}
    return i18n.t("chef.pantry.use.done_partial", item=row["item"], remaining=remaining)


def cmd_pantry_remove(conn, home, args) -> object:
    row = _resolve_single_pantry_row(conn, args.ref)
    conn.execute("DELETE FROM chef_pantry WHERE id = ?", (row["id"],))
    if args.json:
        return {"id": row["id"], "item": row["item"], "removed": True}
    return i18n.t("chef.pantry.remove.done", item=row["item"])


def cmd_pantry_expiring(conn, home, args) -> object:
    _require_pantry_not_empty(conn)
    today = util.today()
    rows = [dict(r) for r in conn.execute("SELECT * FROM chef_pantry").fetchall()]
    picked = [r for r in rows if ops.is_expiring(r["expires"], today, args.days)]
    ordered = ops.sort_by_expiry(picked)
    if args.json:
        return ordered
    if not ordered:
        return i18n.t("chef.pantry.expiring.empty")
    return "\n".join(
        i18n.t(
            "chef.pantry.expiring.line",
            item=r["item"], expires=r["expires"] or i18n.t("chef.common.unknown"),
        )
        for r in ordered
    )


def cmd_pantry_missing(conn, home, args) -> object:
    _require_pantry_not_empty(conn)
    requested = _split_csv(args.items)
    pantry_items = _pantry_items(conn)
    staples = ops.basics()
    result = ops.check_missing(requested, pantry_items, staples)
    if args.json:
        return result
    if not result:
        return i18n.t("chef.pantry.missing.empty")
    lines = []
    for r in result:
        mark = i18n.t("chef.pantry.missing.found") if r["found"] else i18n.t("chef.pantry.missing.not_found")
        matched = "/".join(r["matched"]) if r["matched"] else ""
        line = i18n.t("chef.pantry.missing.line", item=r["item"], mark=mark)
        if matched:
            line += i18n.t("chef.pantry.missing.matched_suffix", matched=matched)
        lines.append(line)
    return "\n".join(lines)


# --- meal -------------------------------------------------------------------------


def cmd_meal_log(conn, home, args) -> object:
    date_ = ops.validate_date(args.date, field="日付")
    ops.validate_choice(args.slot, VALID_SLOTS, field="slot")
    now = util.now()
    cur = conn.execute(
        "INSERT INTO chef_meal (date, slot, dish, ingredients, note, planned, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (date_, args.slot, args.dish, args.ingredients, args.note, 1 if args.planned else 0, now),
    )
    meal_id = cur.lastrowid
    if args.json:
        return {"id": meal_id}
    return i18n.t("chef.meal.log.done", id=meal_id)


def cmd_meal_confirm(conn, home, args) -> object:
    row = conn.execute("SELECT id FROM chef_meal WHERE id = ?", (args.id,)).fetchone()
    if row is None:
        raise ManorError(
            f"meal が見つかりません: {args.id}",
            code=2,
            key="error.chef.meal_not_found",
            params={"id": args.id},
        )
    conn.execute("UPDATE chef_meal SET planned = 0 WHERE id = ?", (args.id,))
    if args.json:
        return {"id": args.id, "planned": False}
    return i18n.t("chef.meal.confirm.done", id=args.id)


def cmd_meal_week(conn, home, args) -> object:
    today = util.today()
    start, end = ops.week_range(today, args.days)
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM chef_meal WHERE date BETWEEN ? AND ? ORDER BY date, slot",
            (start, end),
        ).fetchall()
    ]
    result = ops.aggregate_week(rows, start, end, ops.dish_types(), ops.ingredient_categories())
    if args.json:
        return result
    lines = [
        i18n.t(
            "chef.meal.week.header",
            start=result["start"], end=result["end"], coverage=f"{result['coverage_rate']:.0%}",
        )
    ]
    lines.append(i18n.t("chef.meal.week.planned_count", count=result["planned_count"]))
    if result["missing_slots"]:
        lines.append(i18n.t("chef.meal.week.missing_header"))
        for m in result["missing_slots"]:  # type: ignore[union-attr]
            lines.append(i18n.t("chef.meal.week.missing_line", date=m["date"], slot=m["slot"]))
    return "\n".join(lines)


# --- shopping -----------------------------------------------------------------------


def cmd_shopping_list(conn, home, args) -> object:
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM chef_shopping WHERE bought_at IS NULL ORDER BY aisle, item"
        ).fetchall()
    ]
    if args.json:
        return rows
    if not rows:
        return i18n.t("chef.shopping.list.empty")
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        grouped.setdefault(str(r["aisle"]), []).append(r)
    lines = []
    for aisle, items in grouped.items():
        lines.append(i18n.t("chef.shopping.list.aisle_header", aisle=aisle))
        for it in items:
            if it["reason"]:
                lines.append(i18n.t("chef.shopping.list.item_line_with_reason", item=it["item"], reason=it["reason"]))
            else:
                lines.append(i18n.t("chef.shopping.list.item_line", item=it["item"]))
    return "\n".join(lines)


def cmd_shopping_add(conn, home, args) -> object:
    ops.validate_choice(args.aisle, VALID_AISLES, field="aisle")
    now = util.now()
    cur = conn.execute(
        "INSERT INTO chef_shopping (item, reason, aisle, added_at, bought_at)"
        " VALUES (?, ?, ?, ?, NULL)",
        (args.item, args.reason, args.aisle, now),
    )
    shopping_id = cur.lastrowid
    if args.json:
        return {"id": shopping_id}
    return i18n.t("chef.shopping.add.done", item=args.item)


def cmd_shopping_bought(conn, home, args) -> object:
    items = _split_csv(args.items)
    if not items:
        raise ManorError(
            "品目が指定されていません",
            code=2,
            key="error.chef.shopping_items_missing",
        )
    expires = (
        ops.validate_date(args.expires, field="期限", field_key="chef.field.expires")
        if args.expires
        else None
    )
    now = util.now()

    pantry_items = _pantry_items(conn)
    open_shopping = [
        dict(r)
        for r in conn.execute("SELECT * FROM chef_shopping WHERE bought_at IS NULL").fetchall()
    ]

    results: list[dict[str, object]] = []
    for item in items:
        crossed_off: list[str] = []
        for row in open_shopping:
            if row["bought_at"] is None and ops.item_match(item, str(row["item"])):
                conn.execute("UPDATE chef_shopping SET bought_at = ? WHERE id = ?", (now, row["id"]))
                row["bought_at"] = now
                crossed_off.append(str(row["item"]))

        already_in_pantry = [p for p in pantry_items if ops.item_match(item, p)]
        added_to_pantry = False
        if not already_in_pantry:
            conn.execute(
                "INSERT INTO chef_pantry (item, qty, unit, expires, place, note, added_at, updated_at)"
                " VALUES (?, ?, '', ?, ?, '', ?, ?)",
                (item, args.qty or "不明", expires, args.place or "不明", now, now),
            )
            pantry_items.append(item)
            added_to_pantry = True

        results.append(
            {
                "item": item,
                "crossed_off": crossed_off,
                "added_to_pantry": added_to_pantry,
                "already_in_pantry": already_in_pantry,
            }
        )

    if args.json:
        return results
    lines = []
    for r in results:
        note = (
            i18n.t("chef.shopping.bought.added")
            if r["added_to_pantry"]
            else i18n.t("chef.shopping.bought.already_in_pantry")
        )
        lines.append(i18n.t("chef.shopping.bought.line", item=r["item"], note=note))
    return "\n".join(lines)


# --- taste --------------------------------------------------------------------------


def cmd_taste_show(conn, home, args) -> object:
    rows = [dict(r) for r in conn.execute("SELECT * FROM chef_taste ORDER BY key").fetchall()]
    if args.json:
        return rows
    if not rows:
        return i18n.t("chef.taste.show.empty")
    return "\n".join(i18n.t("chef.taste.show.line", taste_key=r["key"], value=r["value"]) for r in rows)


def cmd_taste_set(conn, home, args) -> object:
    ops.validate_choice(args.key, VALID_TASTE_KEYS, field="key")
    now = util.now()
    conn.execute(
        "INSERT INTO chef_taste (key, value, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (args.key, args.value, now),
    )
    if args.json:
        return {"key": args.key, "value": args.value}
    return i18n.t("chef.taste.set.done", taste_key=args.key)


# --- パーサ組み立て -----------------------------------------------------------------


def register(subparsers) -> None:
    """`manor chef ...` を足す（ADR-001 §11）。core の `build_parser` が呼ぶ。"""
    chef_p = subparsers.add_parser("chef", help=i18n.t("cli.chef.help"))
    chef_sub = chef_p.add_subparsers(dest="verb")

    # --- pantry ---
    pantry_p = chef_sub.add_parser("pantry", help=i18n.t("cli.chef.pantry.help"))
    pantry_sub = pantry_p.add_subparsers(dest="pantry_verb")

    p = pantry_sub.add_parser("list")
    p.add_argument("--place")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_pantry_list, is_write=False)

    p = pantry_sub.add_parser("add")
    p.add_argument("item")
    p.add_argument("--qty", default="不明")
    p.add_argument("--unit", default="")
    p.add_argument("--expires")
    p.add_argument("--place", default="不明")
    p.add_argument("--note", default="")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_pantry_add, is_write=True)

    p = pantry_sub.add_parser("use")
    p.add_argument("item")
    p.add_argument("--qty")
    p.add_argument("--all", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_pantry_use, is_write=True)

    p = pantry_sub.add_parser("remove")
    p.add_argument("ref")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_pantry_remove, is_write=True)

    p = pantry_sub.add_parser("expiring")
    p.add_argument("--days", type=int, default=3)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_pantry_expiring, is_write=False)

    p = pantry_sub.add_parser("missing")
    p.add_argument("items")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_pantry_missing, is_write=False)

    # --- meal ---
    meal_p = chef_sub.add_parser("meal", help=i18n.t("cli.chef.meal.help"))
    meal_sub = meal_p.add_subparsers(dest="meal_verb")

    p = meal_sub.add_parser("log")
    p.add_argument("--date", required=True)
    p.add_argument("--slot", required=True)
    p.add_argument("--dish", required=True)
    p.add_argument("--ingredients", default="")
    p.add_argument("--planned", action="store_true")
    p.add_argument("--note", default="")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_meal_log, is_write=True)

    p = meal_sub.add_parser("confirm")
    p.add_argument("id", type=int)
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_meal_confirm, is_write=True)

    p = meal_sub.add_parser("week")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_meal_week, is_write=False)

    # --- shopping ---
    shopping_p = chef_sub.add_parser("shopping", help=i18n.t("cli.chef.shopping.help"))
    shopping_sub = shopping_p.add_subparsers(dest="shopping_verb")

    p = shopping_sub.add_parser("list")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_shopping_list, is_write=False)

    p = shopping_sub.add_parser("add")
    p.add_argument("item")
    p.add_argument("--reason", required=True)
    p.add_argument("--aisle", default="その他")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_shopping_add, is_write=True)

    p = shopping_sub.add_parser("bought")
    p.add_argument("items")
    p.add_argument("--qty")
    p.add_argument("--expires")
    p.add_argument("--place")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_shopping_bought, is_write=True)

    # --- taste ---
    taste_p = chef_sub.add_parser("taste", help=i18n.t("cli.chef.taste.help"))
    taste_sub = taste_p.add_subparsers(dest="taste_verb")

    p = taste_sub.add_parser("show")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_taste_show, is_write=False)

    p = taste_sub.add_parser("set")
    p.add_argument("key")
    p.add_argument("value")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_taste_set, is_write=True)
