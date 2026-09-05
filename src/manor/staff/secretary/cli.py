"""`manor sec ...`（ADR-002 §6）。

引数の組み立てと DB の読み書き。判断（何を勧めるか・inbox をどこへ回すか）はしない。
日付式の解決・検証・並べ替えは `ops.py`（純粋関数）に委ねる。core の表（milestone/task）は
**読むだけ**（`manor.graph` / `manor.task` の読み取り API を使う。直接 INSERT しない）。
core のパターン（`src/manor/cli.py`）に合わせ、各コマンドは `(conn, home, args) -> str | object` を返す。
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from manor import graph
from manor import i18n
from manor import task as task_mod
from manor import util
from manor.errors import ManorError

from . import ops


def _kind_label(kind: str) -> str:
    return i18n.t(f"sec.agenda.kind.{kind}")


def _require_non_negative_days(days: int) -> None:
    if days < 0:
        raise ManorError(
            "--days は0以上で指定してください",
            code=2,
            key="error.sec.days_negative",
        )


# --- remind ----------------------------------------------------------------------


def cmd_remind_add(conn, home, args) -> object:
    text = args.text.strip()
    if not text:
        raise ManorError("控えの本文が空です", code=2, key="error.sec.remind_text_empty")
    today = date.fromisoformat(util.today())
    on_date = ops.resolve_date(args.on, today)
    at_time = ops.validate_time(args.at, field="--at") if args.at else None
    now = util.now()
    cur = conn.execute(
        "INSERT INTO secretary_reminder (on_date, at_time, text, source, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (on_date.isoformat(), at_time, text, args.source, now),
    )
    reminder_id = cur.lastrowid
    # 控えは復唱する（登録した日付と文をそのまま返す。ADR-002 §6）。
    if args.json:
        return {
            "id": reminder_id,
            "on_date": on_date.isoformat(),
            "at_time": at_time,
            "text": text,
            "source": args.source,
        }
    return i18n.t("sec.remind.add.done", date=on_date.isoformat(), text=text, id=reminder_id)


def cmd_remind_due(conn, home, args) -> object:
    _require_non_negative_days(args.days)
    today = util.today()
    end = (date.fromisoformat(today) + timedelta(days=args.days)).isoformat()
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM secretary_reminder WHERE done_at IS NULL AND on_date <= ?"
            " ORDER BY on_date, id",
            (end,),
        ).fetchall()
    ]
    for r in rows:
        r["overdue"] = str(r["on_date"]) < today
    if args.json:
        return rows
    if not rows:
        return i18n.t("common.none")
    lines = []
    for r in rows:
        mark = i18n.t("sec.remind.due.overdue_mark") if r["overdue"] else ""
        at = f" {r['at_time']}" if r["at_time"] else ""
        lines.append(
            i18n.t("sec.remind.due.line", mark=mark, on_date=r["on_date"], at=at, text=r["text"], id=r["id"])
        )
    return "\n".join(lines)


def cmd_remind_done(conn, home, args) -> object:
    row = conn.execute("SELECT id FROM secretary_reminder WHERE id = ?", (args.id,)).fetchone()
    if row is None:
        raise ManorError(
            f"控えが見つかりません: {args.id}",
            code=2,
            key="error.sec.remind_not_found",
            params={"id": args.id},
        )
    now = util.now()
    conn.execute("UPDATE secretary_reminder SET done_at = ? WHERE id = ?", (now, args.id))
    if args.json:
        return {"id": args.id, "done_at": now}
    return i18n.t("sec.remind.done.done", id=args.id)


def cmd_remind_list(conn, home, args) -> object:
    sql = "SELECT * FROM secretary_reminder"
    if not args.all:
        sql += " WHERE done_at IS NULL"
    sql += " ORDER BY on_date, id"
    rows = [dict(r) for r in conn.execute(sql).fetchall()]
    if args.json:
        return rows
    if not rows:
        return i18n.t("common.none")
    lines = []
    for r in rows:
        state = i18n.t("sec.remind.list.state_done") if r["done_at"] else i18n.t("sec.remind.list.state_pending")
        at = f" {r['at_time']}" if r["at_time"] else ""
        lines.append(
            i18n.t("sec.remind.list.line", state=state, on_date=r["on_date"], at=at, text=r["text"], id=r["id"])
        )
    return "\n".join(lines)


# --- event -----------------------------------------------------------------------


def cmd_event_add(conn, home, args) -> object:
    title = args.title.strip()
    if not title:
        raise ManorError("予定の題名が空です", code=2, key="error.sec.event_title_empty")
    start = ops.validate_datetime(args.start, field="--start")
    end = ops.validate_datetime(args.end, field="--end") if args.end else None
    now = util.now()
    cur = conn.execute(
        'INSERT INTO secretary_event (start, "end", title, place, note, source, created_at)'
        " VALUES (?, ?, ?, ?, ?, 'manual', ?)",
        (start, end, title, args.place, args.note, now),
    )
    event_id = cur.lastrowid
    if args.json:
        return {"id": event_id, "start": start, "end": end, "title": title}
    return i18n.t("sec.event.add.done", start=start, title=title, id=event_id)


def cmd_event_list(conn, home, args) -> object:
    _require_non_negative_days(args.days)
    today = util.today()
    end = (date.fromisoformat(today) + timedelta(days=args.days)).isoformat()
    rows = [
        dict(r)
        for r in conn.execute(
            'SELECT * FROM secretary_event WHERE substr(start, 1, 10) BETWEEN ? AND ?'
            " ORDER BY start",
            (today, end),
        ).fetchall()
    ]
    if args.json:
        return rows
    if not rows:
        return i18n.t("common.none")
    lines = []
    for r in rows:
        place = f" @{r['place']}" if r["place"] else ""
        lines.append(i18n.t("sec.event.list.line", start=r["start"], title=r["title"], place=place, id=r["id"]))
    return "\n".join(lines)


def cmd_event_remove(conn, home, args) -> object:
    cur = conn.execute("DELETE FROM secretary_event WHERE id = ?", (args.id,))
    if cur.rowcount == 0:
        raise ManorError(
            f"予定が見つかりません: {args.id}",
            code=2,
            key="error.sec.event_not_found",
            params={"id": args.id},
        )
    if args.json:
        return {"id": args.id, "removed": True}
    return i18n.t("sec.event.remove.done", id=args.id)


# --- agenda ------------------------------------------------------------------------


def cmd_agenda(conn, home, args) -> object:
    _require_non_negative_days(args.days)
    today_s = util.today()
    today = date.fromisoformat(today_s)
    end_s = (today + timedelta(days=args.days)).isoformat()

    events = [
        dict(r)
        for r in conn.execute(
            'SELECT id, start, "end", title, place, note FROM secretary_event'
            " WHERE substr(start, 1, 10) BETWEEN ? AND ? ORDER BY start",
            (today_s, end_s),
        ).fetchall()
    ]
    reminders = [
        dict(r)
        for r in conn.execute(
            "SELECT id, on_date, at_time, text FROM secretary_reminder"
            " WHERE done_at IS NULL AND on_date <= ? ORDER BY on_date, id",
            (end_s,),
        ).fetchall()
    ]
    # core（milestone/task）は読むだけ。core の読み取り API を使い、直接 INSERT/UPDATE しない。
    milestones = [m for m in graph.milestone_list(conn) if today_s <= str(m["date"]) <= end_s]
    tasks = [
        t
        for t in task_mod.list_tasks(conn, include_settled=False)
        if t.get("due") and today_s <= str(t["due"])[:10] <= end_s
    ]

    items = ops.merge_agenda(
        events=events, reminders=reminders, milestones=milestones, tasks=tasks, today=today
    )
    if args.json:
        return items
    if not items:
        return i18n.t("common.none")

    lines: list[str] = []
    overdue = [it for it in items if it["overdue"]]
    rest = [it for it in items if not it["overdue"]]
    for it in overdue:
        lines.append(
            i18n.t(
                "sec.agenda.overdue_line",
                date=it["date"], kind=_kind_label(str(it["kind"])), title=it["title"], id=it["id"],
            )
        )
    last_date: str | None = None
    for it in rest:
        if it["date"] != last_date:
            lines.append(str(it["date"]))
            last_date = str(it["date"])
        detail = f" {it['detail']}" if it.get("detail") else ""
        lines.append(
            i18n.t(
                "sec.agenda.item_line",
                kind=_kind_label(str(it["kind"])), title=it["title"], detail=detail, id=it["id"],
            )
        )
    return "\n".join(lines)


# --- inbox -----------------------------------------------------------------------


def cmd_inbox_scan(conn, home, args) -> object:
    existing = {
        str(r["ref"])
        for r in conn.execute("SELECT ref FROM secretary_inbox WHERE source = 'inbox'").fetchall()
    }
    found = ops.scan_inbox_files(Path(home) / "inbox", existing)
    for f in found:
        conn.execute(
            "INSERT INTO secretary_inbox (received_at, source, ref, summary) VALUES (?, 'inbox', ?, '')",
            (f["received_at"], f["ref"]),
        )
    if args.json:
        return {"added": found}
    if not found:
        return i18n.t("sec.inbox.scan.none")
    return "\n".join(i18n.t("sec.inbox.scan.line", ref=f["ref"], received_at=f["received_at"]) for f in found)


def cmd_inbox_list(conn, home, args) -> object:
    sql = "SELECT * FROM secretary_inbox"
    if not args.all:
        sql += " WHERE routed_to IS NULL"
    sql += " ORDER BY received_at, id"
    rows = [dict(r) for r in conn.execute(sql).fetchall()]
    if args.json:
        return rows
    if not rows:
        return i18n.t("common.none")
    lines = []
    for r in rows:
        routed = f" -> {r['routed_to']}" if r["routed_to"] else ""
        lines.append(
            i18n.t("sec.inbox.list.line", id=r["id"], ref=r["ref"], received_at=r["received_at"], routed=routed)
        )
    return "\n".join(lines)


def cmd_inbox_route(conn, home, args) -> object:
    to = args.to.strip()
    if not to:
        raise ManorError("--to が空です", code=2, key="error.sec.inbox_to_empty")
    row = conn.execute("SELECT id FROM secretary_inbox WHERE id = ?", (args.id,)).fetchone()
    if row is None:
        raise ManorError(
            f"inbox の項目が見つかりません: {args.id}",
            code=2,
            key="error.sec.inbox_not_found",
            params={"id": args.id},
        )
    now = util.now()
    if args.summary is not None:
        conn.execute(
            "UPDATE secretary_inbox SET routed_to = ?, routed_at = ?, summary = ? WHERE id = ?",
            (to, now, args.summary, args.id),
        )
    else:
        conn.execute(
            "UPDATE secretary_inbox SET routed_to = ?, routed_at = ? WHERE id = ?",
            (to, now, args.id),
        )
    if args.json:
        return {"id": args.id, "routed_to": to, "routed_at": now}
    return i18n.t("sec.inbox.route.done", id=args.id, to=to)


# --- resolve-date --------------------------------------------------------------------


def cmd_resolve_date(conn, home, args) -> object:
    if args.today:
        try:
            today = date.fromisoformat(args.today)
        except ValueError as exc:
            raise ManorError(
                f"--today の形式が不正です: {args.today!r}",
                code=2,
                key="error.sec.today_format",
                params={"today": repr(args.today)},
            ) from exc
    else:
        today = date.fromisoformat(util.today())
    resolved = ops.resolve_date(args.expr, today)
    if args.json:
        return {"expr": args.expr, "today": today.isoformat(), "resolved": resolved.isoformat()}
    return resolved.isoformat()


# --- パーサ組み立て -----------------------------------------------------------------


def register(subparsers) -> None:
    """`manor sec ...` を足す（ADR-001 §11）。core の `build_parser` が呼ぶ。"""
    sec_p = subparsers.add_parser("sec", help=i18n.t("cli.sec.help"))
    sec_sub = sec_p.add_subparsers(dest="verb")

    # --- remind ---
    remind_p = sec_sub.add_parser("remind", help=i18n.t("cli.sec.remind.help"))
    remind_sub = remind_p.add_subparsers(dest="remind_verb")

    p = remind_sub.add_parser("add")
    p.add_argument("text")
    p.add_argument("--on", required=True)
    p.add_argument("--at")
    p.add_argument("--source", default="butler")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_remind_add, is_write=True)

    p = remind_sub.add_parser("due")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_remind_due, is_write=False)

    p = remind_sub.add_parser("done")
    p.add_argument("id", type=int)
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_remind_done, is_write=True)

    p = remind_sub.add_parser("list")
    p.add_argument("--all", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_remind_list, is_write=False)

    # --- event ---
    event_p = sec_sub.add_parser("event", help=i18n.t("cli.sec.event.help"))
    event_sub = event_p.add_subparsers(dest="event_verb")

    p = event_sub.add_parser("add")
    p.add_argument("title")
    p.add_argument("--start", required=True)
    p.add_argument("--end")
    p.add_argument("--place", default="")
    p.add_argument("--note", default="")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_event_add, is_write=True)

    p = event_sub.add_parser("list")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_event_list, is_write=False)

    p = event_sub.add_parser("remove")
    p.add_argument("id", type=int)
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_event_remove, is_write=True)

    # --- agenda ---
    p = sec_sub.add_parser("agenda", help=i18n.t("cli.sec.agenda.help"))
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_agenda, is_write=False)

    # --- inbox ---
    inbox_p = sec_sub.add_parser("inbox", help=i18n.t("cli.sec.inbox.help"))
    inbox_sub = inbox_p.add_subparsers(dest="inbox_verb")

    p = inbox_sub.add_parser("scan")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_inbox_scan, is_write=True)

    p = inbox_sub.add_parser("list")
    p.add_argument("--all", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_inbox_list, is_write=False)

    p = inbox_sub.add_parser("route")
    p.add_argument("id", type=int)
    p.add_argument("--to", required=True)
    p.add_argument("--summary")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_inbox_route, is_write=True)

    # --- resolve-date ---
    p = sec_sub.add_parser("resolve-date", help=i18n.t("cli.sec.resolve_date.help"))
    p.add_argument("expr")
    p.add_argument("--today")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_resolve_date, is_write=False)
