"""家政婦の CLI（ADR-002 §4）。`manor house ...` を足す。

各コマンドは `(conn, home, args) -> str | object`（ADR-001 §5 の約束どおり）。
ここは薄く保つ：並べ替え・期日計算・突き合わせは `ops.py`（純粋関数）に任せ、
ここでは SQL の読み書きと、`args` から `ops` を呼ぶ配線・表示の整形だけをする。

日付は CLI では `YYYY-MM-DD` だけ受ける（`ops.parse_date` が検算する）。
"""

from __future__ import annotations

import argparse
import sqlite3

from manor import i18n, util
from manor.errors import ManorError

from . import ops


# --- 小さな整形ヘルパー（判断はしない。表示のための文字列化だけ） -------------------------


def _fmt_num(value: object) -> str:
    if value is None:
        return i18n.t("house.common.unknown")
    f = float(value)  # type: ignore[arg-type]
    if f.is_integer():
        return str(int(f))
    return str(f)


def _fmt_qty(qty: object, unit: str) -> str:
    return f"{_fmt_num(qty)}{unit or ''}"


def _date_arg(value: str | None) -> str:
    """`--on` 等の任意日付引数。省略なら今日、指定があれば `YYYY-MM-DD` を検算する。"""
    if value is None:
        return util.today()
    ops.parse_date(value)
    return value


def _due_tag(overdue_days: int | None) -> str:
    if overdue_days is None:
        return i18n.t("house.due.never")
    if overdue_days >= 0:
        return i18n.t("house.due.overdue", days=overdue_days)
    return i18n.t("house.due.upcoming", days=-overdue_days)


# --- chore -----------------------------------------------------------------------


def _resolve_chore(conn: sqlite3.Connection, ref: str) -> sqlite3.Row:
    row = None
    if ref.isdigit():
        row = conn.execute("SELECT * FROM housekeeper_chore WHERE id = ?", (int(ref),)).fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM housekeeper_chore WHERE name = ?", (ref,)).fetchone()
    if row is None:
        raise ManorError(
            f"当番が見つかりません: {ref}",
            code=2,
            key="error.house.chore_not_found",
            params={"ref": ref},
        )
    return row


def cmd_house_chore_list(conn, home, args) -> object:
    rows = [dict(r) for r in conn.execute("SELECT * FROM housekeeper_chore ORDER BY name").fetchall()]
    if args.json:
        return rows
    if not rows:
        return i18n.t("common.none")
    lines = []
    for r in rows:
        last = r["last_done"] or i18n.t("house.common.unrecorded")
        lines.append(
            i18n.t(
                "house.chore.list.line",
                id=r["id"], name=r["name"], area=r["area"] or i18n.t("common.dash"),
                cadence_days=r["cadence_days"], last=last,
            )
        )
    return "\n".join(lines)


def cmd_house_chore_add(conn, home, args) -> object:
    if args.every <= 0:
        raise ManorError(
            "--every は正の整数で指定してください",
            key="error.house.every_must_be_positive",
        )
    now = util.now()
    try:
        conn.execute(
            "INSERT INTO housekeeper_chore (name, area, cadence_days, note, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (args.name, args.area or "", args.every, args.note or "", now),
        )
    except sqlite3.IntegrityError as exc:
        raise ManorError(
            f"当番はすでに登録されています: {args.name}",
            key="error.house.chore_duplicate",
            params={"name": args.name},
        ) from exc
    row = conn.execute("SELECT id FROM housekeeper_chore WHERE name = ?", (args.name,)).fetchone()
    if args.json:
        return {"id": row["id"], "name": args.name, "cadence_days": args.every}
    return i18n.t("house.chore.add.done", name=args.name, every=args.every)


def cmd_house_chore_done(conn, home, args) -> object:
    row = _resolve_chore(conn, args.ref)
    on = _date_arg(args.on)
    conn.execute(
        "INSERT INTO housekeeper_chore_log (chore_id, done_on, note) VALUES (?, ?, ?)",
        (row["id"], on, args.note or ""),
    )
    current_last = row["last_done"]
    new_last = on if (not current_last or on > current_last) else current_last
    conn.execute("UPDATE housekeeper_chore SET last_done = ? WHERE id = ?", (new_last, row["id"]))
    if args.json:
        return {"id": row["id"], "name": row["name"], "done_on": on, "last_done": new_last}
    return i18n.t("house.chore.done.done", name=row["name"], on=on)


def cmd_house_chore_due(conn, home, args) -> object:
    rows = [dict(r) for r in conn.execute("SELECT * FROM housekeeper_chore").fetchall()]
    due = ops.due_chores(rows, util.today(), days=args.days)
    if args.json:
        return due
    if not due:
        return i18n.t("common.none")
    lines = []
    for r in due:
        lines.append(
            i18n.t(
                "house.chore.due.line",
                name=r["name"], area=r["area"] or i18n.t("common.dash"), due_tag=_due_tag(r["overdue_days"]),
            )
        )
    return "\n".join(lines)


# --- supply ------------------------------------------------------------------------


def cmd_house_supply_list(conn, home, args) -> object:
    rows = [dict(r) for r in conn.execute("SELECT * FROM housekeeper_supply ORDER BY item").fetchall()]
    if args.json:
        return rows
    if not rows:
        return i18n.t("common.none")
    lines = []
    for r in rows:
        th = (
            i18n.t("house.supply.threshold_suffix", threshold=_fmt_num(r["threshold"]))
            if r["threshold"] is not None
            else ""
        )
        lines.append(i18n.t("house.supply.list.line", item=r["item"], qty=_fmt_qty(r["qty"], r["unit"]), threshold_suffix=th).rstrip())
    return "\n".join(lines)


def cmd_house_supply_add(conn, home, args) -> object:
    now = util.now()
    try:
        conn.execute(
            "INSERT INTO housekeeper_supply (item, qty, unit, threshold, place, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (args.item, args.qty, args.unit or "", args.threshold, args.place or "", now),
        )
    except sqlite3.IntegrityError as exc:
        raise ManorError(
            f"消耗品はすでに登録されています: {args.item}",
            key="error.house.supply_duplicate",
            params={"item": args.item},
        ) from exc
    if args.json:
        return {"item": args.item, "qty": args.qty, "threshold": args.threshold}
    return i18n.t("house.supply.add.done", item=args.item)


def cmd_house_supply_set(conn, home, args) -> object:
    row = conn.execute("SELECT * FROM housekeeper_supply WHERE item = ?", (args.item,)).fetchone()
    if row is None:
        raise ManorError(
            f"消耗品が見つかりません: {args.item}",
            code=2,
            key="error.house.supply_not_found",
            params={"item": args.item},
        )
    conn.execute(
        "UPDATE housekeeper_supply SET qty = ?, updated_at = ? WHERE item = ?",
        (args.qty, util.now(), args.item),
    )
    if args.json:
        return {"item": args.item, "qty": args.qty}
    return i18n.t("house.supply.set.done", item=args.item, qty=_fmt_num(args.qty))


def cmd_house_supply_low(conn, home, args) -> object:
    rows = [dict(r) for r in conn.execute("SELECT * FROM housekeeper_supply").fetchall()]
    low = ops.low_supplies(rows)

    added: list[str] = []
    skipped: list[str] = []
    table_missing = False

    if args.to_shopping:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'chef_shopping'"
        ).fetchone()
        if exists is None:
            table_missing = True
        else:
            now = util.now()
            for item in low:
                dup = conn.execute(
                    "SELECT id FROM chef_shopping WHERE item = ? AND bought_at IS NULL",
                    (item["item"],),
                ).fetchone()
                if dup is not None:
                    skipped.append(str(item["item"]))
                    continue
                # chef_shopping.reason は買い物リストに永続する記録(主人が入れたデータと
                # 同じ扱い。decision.py の ruling・task.py の task_event.note と同じ判断)。
                # CLI の言語設定によらず日本語のまま書く——訳すと、書いた時の言語次第で
                # 同じ買い物リストに日本語と英語の理由が混ざってしまう。
                reason = f"家政婦: 残量 {_fmt_qty(item['qty'], item['unit'])}"
                conn.execute(
                    "INSERT INTO chef_shopping (item, reason, aisle, added_at, bought_at) "
                    "VALUES (?, ?, 'その他', ?, NULL)",
                    (item["item"], reason, now),
                )
                added.append(str(item["item"]))

    if args.json:
        result: dict[str, object] = {"low": low}
        if args.to_shopping:
            result["to_shopping"] = {
                "added": added,
                "skipped": skipped,
                "table_missing": table_missing,
            }
        return result

    lines = []
    if not low:
        lines.append(i18n.t("house.supply.low.empty"))
    else:
        for r in low:
            lines.append(
                i18n.t(
                    "house.supply.low.line",
                    item=r["item"], qty=_fmt_num(r["qty"]), unit=r["unit"], threshold=_fmt_num(r["threshold"]),
                )
            )
    if args.to_shopping:
        if table_missing:
            lines.append(i18n.t("house.supply.low.chef_missing"))
        else:
            if added:
                lines.append(i18n.t("house.supply.low.added_line", items=", ".join(added)))
            if skipped:
                lines.append(i18n.t("house.supply.low.skipped_line", items=", ".join(skipped)))
    return "\n".join(lines)


# --- maint ---------------------------------------------------------------------------


def cmd_house_maint_list(conn, home, args) -> object:
    rows = [dict(r) for r in conn.execute("SELECT * FROM housekeeper_maintenance ORDER BY item, what").fetchall()]
    if args.json:
        return rows
    if not rows:
        return i18n.t("common.none")
    lines = []
    for r in rows:
        last = r["last_done"] or i18n.t("house.common.unrecorded")
        lines.append(
            i18n.t(
                "house.maint.list.line",
                id=r["id"], item=r["item"], what=r["what"], cadence_days=r["cadence_days"], last=last,
            )
        )
    return "\n".join(lines)


def cmd_house_maint_add(conn, home, args) -> object:
    if args.every <= 0:
        raise ManorError(
            "--every は正の整数で指定してください",
            key="error.house.every_must_be_positive",
        )
    now = util.now()
    conn.execute(
        "INSERT INTO housekeeper_maintenance (item, what, cadence_days, note, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (args.item, args.what, args.every, args.note or "", now),
    )
    row = conn.execute(
        "SELECT id FROM housekeeper_maintenance WHERE item = ? AND what = ? ORDER BY id DESC LIMIT 1",
        (args.item, args.what),
    ).fetchone()
    if args.json:
        return {"id": row["id"], "item": args.item, "what": args.what, "cadence_days": args.every}
    return i18n.t("house.maint.add.done", item=args.item, what=args.what, every=args.every)


def cmd_house_maint_done(conn, home, args) -> object:
    row = conn.execute("SELECT * FROM housekeeper_maintenance WHERE id = ?", (args.id,)).fetchone()
    if row is None:
        raise ManorError(
            f"手入れの記録が見つかりません: {args.id}",
            code=2,
            key="error.house.maint_not_found",
            params={"id": args.id},
        )
    on = _date_arg(args.on)
    current_last = row["last_done"]
    new_last = on if (not current_last or on > current_last) else current_last
    conn.execute("UPDATE housekeeper_maintenance SET last_done = ? WHERE id = ?", (new_last, row["id"]))
    if args.json:
        return {"id": row["id"], "item": row["item"], "what": row["what"], "done_on": on, "last_done": new_last}
    return i18n.t("house.maint.done.done", item=row["item"], what=row["what"], on=on)


def cmd_house_maint_due(conn, home, args) -> object:
    rows = [dict(r) for r in conn.execute("SELECT * FROM housekeeper_maintenance").fetchall()]
    due = ops.due_chores(rows, util.today(), days=args.days)
    if args.json:
        return due
    if not due:
        return i18n.t("common.none")
    lines = []
    for r in due:
        lines.append(i18n.t("house.maint.due.line", item=r["item"], what=r["what"], due_tag=_due_tag(r["overdue_days"])))
    return "\n".join(lines)


# --- waste -------------------------------------------------------------------------


def cmd_house_waste_list(conn, home, args) -> object:
    rows = [dict(r) for r in conn.execute("SELECT * FROM housekeeper_waste ORDER BY kind").fetchall()]
    if args.json:
        return rows
    if not rows:
        return i18n.t("common.none")
    return "\n".join(i18n.t("house.waste.list.line", kind=r["kind"], rule=r["rule"]) for r in rows)


def cmd_house_waste_add(conn, home, args) -> object:
    ops.parse_rule(args.rule)  # 読めない文法は登録時に拒否する（ADR-002 §4）
    try:
        conn.execute(
            "INSERT INTO housekeeper_waste (kind, rule, note) VALUES (?, ?, '')",
            (args.kind, args.rule),
        )
    except sqlite3.IntegrityError as exc:
        raise ManorError(
            f"ゴミの種類はすでに登録されています: {args.kind}",
            key="error.house.waste_duplicate",
            params={"kind": args.kind},
        ) from exc
    if args.json:
        return {"kind": args.kind, "rule": args.rule}
    return i18n.t("house.waste.add.done", kind=args.kind, rule=args.rule)


def cmd_house_waste_next(conn, home, args) -> object:
    rows = [dict(r) for r in conn.execute("SELECT * FROM housekeeper_waste ORDER BY kind").fetchall()]
    today = util.today()
    entries: list[dict[str, str]] = []
    for r in rows:
        for d in ops.next_dates(str(r["rule"]), today, args.days):
            entries.append({"date": d, "kind": str(r["kind"])})
    entries.sort(key=lambda e: (e["date"], e["kind"]))
    if args.json:
        return entries
    if not entries:
        return i18n.t("common.none")
    return "\n".join(i18n.t("house.waste.next.line", date=e["date"], kind=e["kind"]) for e in entries)


# --- today ---------------------------------------------------------------------------


def cmd_house_today(conn, home, args) -> object:
    today = util.today()
    tomorrow = ops.add_days(today, 1)

    chore_rows = [dict(r) for r in conn.execute("SELECT * FROM housekeeper_chore").fetchall()]
    chore_annotated = ops.due_chores(chore_rows, today)
    overdue_chores = [
        r for r in chore_annotated if r["overdue_days"] is None or r["overdue_days"] > 0  # type: ignore[operator]
    ]
    week_chores = [
        r
        for r in chore_annotated
        if r["overdue_days"] is not None and -6 <= r["overdue_days"] <= 0  # type: ignore[operator]
    ]

    waste_rows = [dict(r) for r in conn.execute("SELECT * FROM housekeeper_waste ORDER BY kind").fetchall()]
    tomorrow_waste = [str(r["kind"]) for r in waste_rows if ops.next_dates(str(r["rule"]), tomorrow, 0)]

    supply_rows = [dict(r) for r in conn.execute("SELECT * FROM housekeeper_supply").fetchall()]
    low = ops.low_supplies(supply_rows)

    maint_rows = [dict(r) for r in conn.execute("SELECT * FROM housekeeper_maintenance").fetchall()]
    maint_due = ops.due_chores(maint_rows, today, days=7)

    sections: list[tuple[str, list[object]]] = []
    if overdue_chores:
        sections.append(("当番の期限切れ", overdue_chores))  # type: ignore[arg-type]
    if week_chores:
        sections.append(("今週の当番", week_chores))  # type: ignore[arg-type]
    if tomorrow_waste:
        sections.append(("明日のゴミ", tomorrow_waste))  # type: ignore[arg-type]
    if low:
        sections.append(("少ない消耗品", low))  # type: ignore[arg-type]
    if maint_due:
        sections.append(("手入れの期日", maint_due))  # type: ignore[arg-type]

    if args.json:
        return dict(sections)

    if not sections:
        return i18n.t("house.today.empty")

    # `label` はここでは JSON 出力のキーにもなるデータ側の値（日本語のまま。上の sections
    # 組み立てと分岐 `label in (...)` を参照）。表示用の見出しだけ別に翻訳キーへ差し替える。
    section_header_keys = {
        "当番の期限切れ": "house.today.section.chore_overdue",
        "今週の当番": "house.today.section.chore_week",
        "明日のゴミ": "house.today.section.waste_tomorrow",
        "少ない消耗品": "house.today.section.supply_low",
        "手入れの期日": "house.today.section.maint_due",
    }

    lines: list[str] = []
    for label, data in sections:
        lines.append(i18n.t("house.today.header", label=i18n.t(section_header_keys[label])))
        if label in ("当番の期限切れ", "今週の当番"):
            for r in data:  # type: ignore[assignment]
                lines.append(
                    i18n.t(
                        "house.today.chore_line",
                        name=r["name"], area=r["area"] or i18n.t("common.dash"), due_tag=_due_tag(r["overdue_days"]),
                    )
                )
        elif label == "明日のゴミ":
            for kind in data:
                lines.append(i18n.t("house.today.waste_line", kind=kind))
        elif label == "少ない消耗品":
            for r in data:  # type: ignore[assignment]
                lines.append(
                    i18n.t(
                        "house.today.supply_line",
                        item=r["item"], qty=_fmt_num(r["qty"]), unit=r["unit"], threshold=_fmt_num(r["threshold"]),
                    )
                )
        elif label == "手入れの期日":
            for r in data:  # type: ignore[assignment]
                lines.append(i18n.t("house.today.maint_line", item=r["item"], what=r["what"], due_tag=_due_tag(r["overdue_days"])))
    return "\n".join(lines)


# --- パーサ組み立て -------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> None:
    house_p = subparsers.add_parser("house", help=i18n.t("cli.house.help"))
    house_sub = house_p.add_subparsers(dest="entity")

    # --- chore ---
    chore_p = house_sub.add_parser("chore", help=i18n.t("cli.house.chore.help"))
    chore_sub = chore_p.add_subparsers(dest="verb")

    p = chore_sub.add_parser("list")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_house_chore_list, is_write=False)

    p = chore_sub.add_parser("add")
    p.add_argument("name")
    p.add_argument("--every", type=int, required=True)
    p.add_argument("--area", default="")
    p.add_argument("--note", default="")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_house_chore_add, is_write=True)

    p = chore_sub.add_parser("done")
    p.add_argument("ref")
    p.add_argument("--on")
    p.add_argument("--note", default="")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_house_chore_done, is_write=True)

    p = chore_sub.add_parser("due")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_house_chore_due, is_write=False)

    # --- supply ---
    supply_p = house_sub.add_parser("supply", help=i18n.t("cli.house.supply.help"))
    supply_sub = supply_p.add_subparsers(dest="verb")

    p = supply_sub.add_parser("list")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_house_supply_list, is_write=False)

    p = supply_sub.add_parser("add")
    p.add_argument("item")
    p.add_argument("--qty", type=float)
    p.add_argument("--unit", default="")
    p.add_argument("--threshold", type=float)
    p.add_argument("--place", default="")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_house_supply_add, is_write=True)

    p = supply_sub.add_parser("set")
    p.add_argument("item")
    p.add_argument("--qty", type=float, required=True)
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_house_supply_set, is_write=True)

    p = supply_sub.add_parser("low")
    p.add_argument("--to-shopping", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_house_supply_low, is_write=True)

    # --- maint ---
    maint_p = house_sub.add_parser("maint", help=i18n.t("cli.house.maint.help"))
    maint_sub = maint_p.add_subparsers(dest="verb")

    p = maint_sub.add_parser("list")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_house_maint_list, is_write=False)

    p = maint_sub.add_parser("add")
    p.add_argument("item")
    p.add_argument("--what", required=True)
    p.add_argument("--every", type=int, required=True)
    p.add_argument("--note", default="")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_house_maint_add, is_write=True)

    p = maint_sub.add_parser("done")
    p.add_argument("id", type=int)
    p.add_argument("--on")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_house_maint_done, is_write=True)

    p = maint_sub.add_parser("due")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_house_maint_due, is_write=False)

    # --- waste ---
    waste_p = house_sub.add_parser("waste", help=i18n.t("cli.house.waste.help"))
    waste_sub = waste_p.add_subparsers(dest="verb")

    p = waste_sub.add_parser("list")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_house_waste_list, is_write=False)

    p = waste_sub.add_parser("add")
    p.add_argument("kind")
    p.add_argument("--rule", required=True)
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_house_waste_add, is_write=True)

    p = waste_sub.add_parser("next")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_house_waste_next, is_write=False)

    # --- today ---
    p = house_sub.add_parser("today")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_house_today, is_write=False)
