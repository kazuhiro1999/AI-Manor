"""board の中心 API: `/api/board` `/api/timeline` `/api/log` `/api/ctx/{id}` と
task / decision / handoff の書き込み口。

読み取りは `SELECT` してよい約束（ROADMAP 委譲文）。書き込みは必ず manor の API
関数（`task.status` / `decision.rule` / `handoff.accept` / `handoff.reject`）を呼び、
成功したら `render.render` で射影を更新する（`_common.commit_and_render`）。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .. import check as check_mod
from .. import ctx as ctx_mod
from .. import decision as decision_mod
from .. import graph
from .. import handoff as handoff_mod
from .. import project as project_mod
from .. import task as task_mod
from .. import user as user_mod
from .. import util
from ..errors import ManorError
from ._common import BoardContext, commit_and_render, manor_error_to_http, open_conn, require_writable

#: `/api/board` の task 一覧に持たせる列（ADR-001 §3 の task 表そのまま＋ title/body）。
#: `n.body` は「要対応」の詳細表示（v1 の D セクション相当＝ decision.background と
#: task.body の両方）で使う。
_TASK_COLUMNS = (
    "t.id, t.project_id, t.status, t.status_note, t.owner, t.level, t.section,"
    " t.goal, t.now, t.next, t.recommendation, t.risk, t.due, t.start, t.\"end\","
    " t.done_at, t.user_id AS user_id, n.title AS title, n.body AS body"
)


def _days_left(value: object, today: date) -> int | None:
    """`due` / `date` 列から本日基準の残日数を出す。読めなければ `None`（推測しない）。"""
    if not value:
        return None
    try:
        d = date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
    return (d - today).days


#: 常に関心順の最下部へ落とす（主人の裁定「3のプロジェクト別」）。定数の定義は
#: `project.BUTLER_PROJECT_KIND` に一本化（T26）。
_BUTLER_PROJECT_KIND = project_mod.BUTLER_PROJECT_KIND


def _project_interest(
    conn: sqlite3.Connection, projects: list[dict[str, object]], today: date
) -> list[dict[str, object]]:
    """「AIの進行中 → プロジェクト別」の並び順（主人の裁定 §3）を計算し、各 project に
    `interest`（`nearest_date` / `doing` / `last_event_at` / `rank`）を付けて返す。

    **並べ替えの規則（この順で比較。上ほど強い）:**

    1. `kind == '執事'`（v1 の X 系）は常に最下部（その中は優先度→code）
    2. それ以外は次の順:
       a. 直近の期日が近い順
          （`min(project.due, milestone.date で今日以降のもの, 未完了 task.due)`。無ければ末尾）
       b. `doing` の件数が多い順（owner は問わない。主人の作業も含む）
       c. 最後に動いた時刻（そのプロジェクトの task の `task_event.at` の最大）が新しい順
       d. 優先度（`project.priority`。1 が最高）
       e. `code`

    `board.projects` 自体の並びは変えない（計画画面など他の使い方に影響させないため）。
    ここで作る `interest` は「AIの進行中」画面がツリーの並べ替えに使う専用の付加情報。
    """
    doing_by_project: dict[str, int] = {
        str(r["project_id"]): int(r["n"])
        for r in conn.execute(
            "SELECT project_id, COUNT(*) AS n FROM task"
            " WHERE status = 'doing' AND project_id IS NOT NULL GROUP BY project_id"
        ).fetchall()
        if r["project_id"] is not None
    }
    last_event_by_project: dict[str, str] = {
        str(r["project_id"]): str(r["last_at"])
        for r in conn.execute(
            "SELECT t.project_id AS project_id, MAX(ev.at) AS last_at FROM task_event ev"
            " JOIN task t ON t.id = ev.task_id WHERE t.project_id IS NOT NULL GROUP BY t.project_id"
        ).fetchall()
        if r["project_id"] is not None and r["last_at"] is not None
    }
    task_due_by_project: dict[str, str] = {
        str(r["project_id"]): str(r["due"])
        for r in conn.execute(
            "SELECT project_id, MIN(due) AS due FROM task"
            " WHERE due IS NOT NULL AND status NOT IN ('done', 'withdrawn')"
            "   AND project_id IS NOT NULL GROUP BY project_id"
        ).fetchall()
        if r["project_id"] is not None and r["due"] is not None
    }
    milestone_by_project: dict[str, str] = {
        str(r["project_id"]): str(r["date"])
        for r in conn.execute(
            "SELECT project_id, MIN(date) AS date FROM milestone"
            " WHERE project_id IS NOT NULL AND done_at IS NULL AND date >= ? GROUP BY project_id",
            (today.isoformat(),),
        ).fetchall()
        if r["project_id"] is not None and r["date"] is not None
    }

    def nearest_date_for(p: dict[str, object]) -> str | None:
        pid = str(p["id"])
        candidates = [p.get("due"), milestone_by_project.get(pid), task_due_by_project.get(pid)]
        dates = [str(c)[:10] for c in candidates if c]
        return min(dates) if dates else None

    def sort_key(p: dict[str, object]) -> tuple[object, ...]:
        pid = str(p["id"])
        is_butler = 1 if p.get("kind") == _BUTLER_PROJECT_KIND else 0
        nearest = p["_interest_nearest_date"]
        if nearest:
            try:
                key_a: float = date.fromisoformat(nearest).toordinal()
            except ValueError:
                key_a = float("inf")
        else:
            key_a = float("inf")
        key_b = -int(p["_interest_doing"])
        last_event = p["_interest_last_event_at"]
        if last_event:
            try:
                key_c: float = -datetime.fromisoformat(str(last_event)[:19]).timestamp()
            except ValueError:
                key_c = float("inf")
        else:
            key_c = float("inf")
        key_d = p.get("priority", 3)
        key_e = p.get("code", pid)
        return (is_butler, key_a, key_b, key_c, key_d, key_e)

    enriched = []
    for p in projects:
        p2 = dict(p)
        p2["_interest_nearest_date"] = nearest_date_for(p)
        p2["_interest_doing"] = doing_by_project.get(str(p["id"]), 0)
        p2["_interest_last_event_at"] = last_event_by_project.get(str(p["id"]))
        enriched.append(p2)

    ranked = sorted(enriched, key=sort_key)
    rank_by_id = {str(p["id"]): i for i, p in enumerate(ranked, start=1)}

    out = []
    for p in projects:
        p2 = dict(p)
        pid = str(p["id"])
        matched = next(x for x in enriched if str(x["id"]) == pid)
        p2["interest"] = {
            "nearest_date": matched["_interest_nearest_date"],
            "doing": matched["_interest_doing"],
            "last_event_at": matched["_interest_last_event_at"],
            "rank": rank_by_id[pid],
        }
        out.append(p2)
    return out


#: risk の重さ（ソートだけに使う。表示は risk 文字列のまま）。
_RISK_ORDER = "CASE d.risk WHEN 'high' THEN 3 WHEN 'medium' THEN 2 WHEN 'low' THEN 1 ELSE 0 END"


def _open_decisions_with_context(conn: sqlite3.Connection, today: date) -> list[dict[str, object]]:
    """要対応（open decision）を **滞留日数 → risk の順** で並べる（v1 README §2「要対応」）。

    以前は decision の ID 順（起票順）で、滞留が長い・risk が高いものが埋もれていた。
    """
    rows = conn.execute(
        "SELECT d.*, n.title AS title,"
        " CAST(julianday('now','localtime') - julianday(d.asked_at) AS INTEGER) AS days"
        " FROM decision d JOIN node n ON n.id = d.id WHERE d.status = 'open'"
        f" ORDER BY days DESC, {_RISK_ORDER} DESC, CAST(substr(d.id, 2) AS INTEGER)"
    ).fetchall()
    out: list[dict[str, object]] = []
    for row in rows:
        d = dict(row)
        tasks = [
            dict(t)
            for t in conn.execute(
                f"SELECT {_TASK_COLUMNS} FROM edge e JOIN task t ON t.id = e.src"
                " JOIN node n ON n.id = t.id WHERE e.dst = ? AND e.rel = 'decided_by'"
                " ORDER BY t.id",
                (d["id"],),
            ).fetchall()
        ]
        d["tasks"] = tasks
        d["project_id"] = tasks[0]["project_id"] if tasks else None
        d["stale"] = int(d["days"]) >= 3
        out.append(d)
    return out


def get_board(conn: sqlite3.Connection, user_id: str | None = None) -> dict[str, object]:
    """`user_id`（ADR-014 D4）: 渡すと「誰の机か」で絞る。`None`（既定）は今までどおり
    全部（CLI・射影・起動時の注入・既存の試験はこちらを使い続ける）。
    """
    today = date.fromisoformat(util.today())
    user_filter = " AND t.user_id = ?" if user_id is not None else ""
    user_params: tuple[object, ...] = (user_id,) if user_id is not None else ()

    pending = _open_decisions_with_context(conn, today)
    if user_id is not None:
        principal = user_mod.principal_id(conn)
        if user_id != principal:
            # 相手・執事の机では、自分のタスクに結ばれた裁定だけ（結ばれていない裁定は
            # 主人のもの——ADR-014 D4）。
            pending = [
                d for d in pending
                if any(str(t.get("user_id")) == user_id for t in d["tasks"])
            ]

    tasks = [
        dict(r)
        for r in conn.execute(
            f"SELECT {_TASK_COLUMNS} FROM task t JOIN node n ON n.id = t.id"
            " WHERE (t.status NOT IN ('done', 'withdrawn')"
            "    OR (t.status = 'done' AND t.done_at IS NOT NULL"
            "        AND julianday('now', 'localtime') - julianday(t.done_at) <= 7))"
            f"{user_filter}"
            " ORDER BY CAST(substr(t.id, 2) AS INTEGER)",
            user_params,
        ).fetchall()
    ]

    delegated_rows = [
        dict(r)
        for r in conn.execute(
            f"SELECT {_TASK_COLUMNS} FROM task t JOIN node n ON n.id = t.id"
            " WHERE t.status = 'doing' AND t.owner NOT IN ('butler', 'master')"
            f"{user_filter}"
            " ORDER BY CAST(substr(t.id, 2) AS INTEGER)",
            user_params,
        ).fetchall()
    ]
    delegated: list[dict[str, object]] = []
    for t in delegated_rows:
        hrow = conn.execute(
            "SELECT * FROM handoff WHERE task_id = ? ORDER BY id DESC LIMIT 1", (t["id"],)
        ).fetchone()
        t2 = dict(t)
        t2["handoff"] = dict(hrow) if hrow is not None else None
        delegated.append(t2)

    projects = []
    for p in project_mod.list_projects(conn, user_id=user_id):
        p2 = dict(p)
        # 畳んだ project（done）に「超過N日」を出さない——発表（P3）が終わったあとも
        # 俯瞰で「超過11日」と目立っていた（主人 2026-09-20）。期限は残す（記録）、残日数だけ空。
        p2["days_left"] = None if p.get("status") == "done" else _days_left(p.get("due"), today)
        projects.append(p2)
    projects = _project_interest(conn, projects, today)

    milestones = []
    for m in graph.milestone_list(conn, user_id=user_id):
        m2 = dict(m)
        m2["days_left"] = _days_left(m.get("date"), today)
        milestones.append(m2)

    recent_done = [
        dict(r)
        for r in conn.execute(
            f"SELECT {_TASK_COLUMNS} FROM task t JOIN node n ON n.id = t.id"
            f" WHERE t.status = 'done'{user_filter} ORDER BY t.done_at DESC LIMIT 20",
            user_params,
        ).fetchall()
    ]

    # 「AIの進行中」画面の取り下げブロック用。`tasks` は spec どおり
    # 「未完了＋直近7日の完了」に絞っているため、withdrawn（`done_at` を持たない終端）は
    # 別に持つ——直近の遷移（task_event）を見て「直近7日に取り下げたもの」を返す。
    withdrawn_recent = [
        dict(r)
        for r in conn.execute(
            f"SELECT {_TASK_COLUMNS}, ev.last_at AS withdrawn_at FROM task t"
            " JOIN node n ON n.id = t.id"
            " JOIN (SELECT task_id, MAX(at) AS last_at FROM task_event"
            "       WHERE to_status = 'withdrawn' GROUP BY task_id) ev ON ev.task_id = t.id"
            " WHERE t.status = 'withdrawn'"
            "   AND julianday('now', 'localtime') - julianday(ev.last_at) <= 7"
            f"{user_filter}"
            " ORDER BY ev.last_at DESC",
            user_params,
        ).fetchall()
    ]

    doing_sql = "SELECT COUNT(*) AS n FROM task t WHERE t.status = 'doing'" + user_filter
    doing_n = conn.execute(doing_sql, user_params).fetchone()["n"]
    # **執事のぶんだけ数える。** 主人の作業（owner='master'）は別に添える（v1 README §2-1
    # 「主人の作業は別のブロック」）。② のタイルに主人の作業まで混ぜると、「AI が今なにを
    # しているか」の質問に「私（AI）は 3 件動いている」という嘘の答えを返すことになる。
    doing_butler_sql = "SELECT COUNT(*) AS n FROM task t WHERE t.status = 'doing' AND t.owner != 'master'" + user_filter
    doing_butler_n = conn.execute(doing_butler_sql, user_params).fetchone()["n"]
    doing_master_n = doing_n - doing_butler_n
    resident_sql = "SELECT COUNT(*) AS n FROM task t WHERE t.status = 'resident'" + user_filter
    resident_n = conn.execute(resident_sql, user_params).fetchone()["n"]
    if user_id is not None:
        blocked_ready_n = conn.execute(
            "SELECT COUNT(*) AS n FROM v_blocked_ready v JOIN task t ON t.id = v.id"
            " WHERE t.user_id = ?",
            (user_id,),
        ).fetchone()["n"]
        stale_n = conn.execute(
            "SELECT COUNT(*) AS n FROM v_stale_doing v JOIN task t ON t.id = v.id"
            " WHERE t.user_id = ?",
            (user_id,),
        ).fetchone()["n"]
    else:
        blocked_ready_n = conn.execute("SELECT COUNT(*) AS n FROM v_blocked_ready").fetchone()["n"]
        stale_n = conn.execute("SELECT COUNT(*) AS n FROM v_stale_doing").fetchone()["n"]
    # ① 直近で完了タイル（README §2-1）は「累計」も添え書きする。`recent_done` は
    # 直近20件に絞ってあるので、累計はここで別に数える（絞った先で数えると嘘になる）。
    done_total_sql = "SELECT COUNT(*) AS n FROM task t WHERE t.status = 'done'" + user_filter
    done_total_n = conn.execute(done_total_sql, user_params).fetchone()["n"]

    counts = {
        "pending": len(pending),
        "doing": int(doing_n),
        "doing_butler": int(doing_butler_n),
        "doing_master": int(doing_master_n),
        "resident": int(resident_n),
        "blocked_ready": int(blocked_ready_n),
        "stale": int(stale_n),
        "done_total": int(done_total_n),
    }

    # 伝達キュー（v1 PROJECTS.md「伝達キュー」＝ manor では kind='note'。`about` 辺で
    # プロジェクトに紐づく。import_v1 が v1 の relay 行をここへ写している）。
    # ADR-014 D4: 結び先（`about`）のプロジェクトの利用者で絞る。結び先が無い、または
    # プロジェクトでない何かに結ばれているメモは全員に出す（判定できないものは絞らない側）。
    notes_sql = (
        "SELECT n.id, n.title, n.body, e.dst AS project_id"
        " FROM node n LEFT JOIN edge e ON e.src = n.id AND e.rel = 'about'"
        " LEFT JOIN project p ON p.id = e.dst"
        " WHERE n.kind = 'note'"
    )
    notes_params: tuple[object, ...] = ()
    if user_id is not None:
        notes_sql += " AND (p.id IS NULL OR p.user_id = ?)"
        notes_params = (user_id,)
    notes_sql += " ORDER BY n.id"
    notes = [dict(r) for r in conn.execute(notes_sql, notes_params).fetchall()]

    board = {
        "today": today.isoformat(),
        "pending": pending,
        "tasks": tasks,
        "delegated": delegated,
        "projects": projects,
        "milestones": milestones,
        "recent_done": recent_done,
        "withdrawn_recent": withdrawn_recent,
        "notes": notes,
        "counts": counts,
    }
    # **外部の更新を反映したことを画面に知らせるための指紋**（v1 README §2 のポーリング
    # 挙動を踏襲）。裁定・状態変更など、画面に見える中身が変われば必ず変わるように、
    # 表示に使う可変部分（pending の状態・task の状態・件数）だけをハッシュする。
    fp_src = json.dumps(
        {
            "pending": [(p["id"], p["status"], p.get("days")) for p in pending],
            "tasks": [(t["id"], t["status"], t.get("status_note")) for t in tasks],
            "delegated": [d["id"] for d in delegated],
            "withdrawn_recent": [w["id"] for w in withdrawn_recent],
            "notes": [n["id"] for n in notes],
            "counts": counts,
        },
        sort_keys=True,
        default=str,
        ensure_ascii=False,
    )
    board["fingerprint"] = hashlib.sha256(fp_src.encode("utf-8")).hexdigest()
    return board


#: v1 timeline.py の型に合わせる（本 ADR は§7で「画面はv1と同等」と定めている）。
_CLOSED_STATUSES = {"done", "withdrawn"}


def get_timeline(conn: sqlite3.Connection, days: int, user_id: str | None = None) -> dict[str, object]:
    """task の start/end/due・milestone・secretary_reminder（未済）・project.due から
    帯と点を組む（v1 `timeline.py` の型を踏襲。ADR §7「画面はv1と同等」）。

    **新しい真実は作らない。** 日付を持っている列をそのまま並べ替えるだけで、
    書いていないタスクは出さない（推測で期間を作ると、動いていないものが
    「今週やる予定」の顔をして並ぶ——v1 timeline.py の教訓をそのまま引き継ぐ）。

    `user_id`（ADR-014 D4）: 渡すと「誰の机か」でレーン・帯を絞る。秘書の予定は
    `NULL`（共通）も出す。控え（reminder）は共通のまま絞らない。`None`（既定）は
    今までどおり全部。
    """
    today = date.fromisoformat(util.today())
    horizon = today + timedelta(days=days)

    lanes: dict[str, dict[str, object]] = {}

    def lane_for(project_id: str | None) -> dict[str, object]:
        key = project_id or "__none__"
        lane = lanes.get(key)
        if lane is None:
            lane = {"id": key, "project_id": project_id, "name": "その他（プロジェクト未設定）",
                     "code": "", "priority": 999, "events": []}
            lanes[key] = lane
        return lane

    # レーンは有効な project ぶんすべて作る（予定が無くても「随時」として残す）。
    for p in project_mod.list_projects(conn, status="active", user_id=user_id):
        lanes[str(p["id"])] = {
            "id": p["id"], "project_id": p["id"], "name": p["title"],
            "code": p["code"], "priority": p["priority"], "events": [],
        }
    for p in project_mod.list_projects(conn, status="paused", user_id=user_id):
        lanes.setdefault(str(p["id"]), {
            "id": p["id"], "project_id": p["id"], "name": p["title"],
            "code": p["code"], "priority": p["priority"], "events": [],
        })

    def add_event(project_id: str | None, ev: dict[str, object]) -> None:
        lane_for(project_id)["events"].append(ev)  # type: ignore[union-attr]

    # --- milestone（点） ---
    # ⚠ 2026-09-07: ここは done を **常に False で書いていた**——`milestone.done_at` が
    # 入っていても帯が「まだ」の顔で残り、済んだ節目が先の予定として並んでいた
    # （主人のご指摘「完了済の…論文修正が残ったまま」）。済んだものは出さない。
    for m in graph.milestone_list(conn, user_id=user_id):
        if m.get("done_at"):
            continue
        d = date.fromisoformat(str(m["date"])[:10])
        if d < today or d > horizon:
            continue
        add_event(
            m.get("project_id"),
            {
                "kind": "milestone", "start": m["date"], "end": m["date"],
                "start_days": (d - today).days, "end_days": (d - today).days,
                "title": m["title"], "approximate": bool(m["approximate"]),
                "detail": m["title"], "ref": m["id"], "done": False, "overdue": False,
            },
        )

    # --- project の期限（今日→期限の帯） ---
    for p in project_mod.list_projects(conn, user_id=user_id):
        due = p.get("due")
        if not due:
            continue
        try:
            d = date.fromisoformat(str(due)[:10])
        except ValueError:
            continue
        if d < today or d > horizon:
            continue
        add_event(
            p["id"],
            {
                "kind": "deadline", "start": today.isoformat(), "end": str(due)[:10],
                "start_days": 0, "end_days": (d - today).days,
                "title": f"期限: {due}", "approximate": False,
                "detail": f"{p['title']} の期限", "ref": p["id"], "done": False, "overdue": False,
            },
        )

    # --- task（start/end/due のどれかを持つものだけ。書いていないものは出さない） ---
    # ⚠ 2026-09-07: 済んだ課題も帯として残していた（取り消し線は引かれるが場所は取る）。
    # タイムラインは「この先どうなるか」を見る画面なので、終わったものは出さない
    # ——済んだ仕事は「最近の動き」と日誌が持っている（主人のご指摘）。
    task_sql = (
        "SELECT t.id, t.project_id, t.status, t.due, t.start, t.\"end\", n.title AS title"
        " FROM task t JOIN node n ON n.id = t.id"
        " WHERE (t.due IS NOT NULL OR t.start IS NOT NULL OR t.\"end\" IS NOT NULL)"
        f" AND t.status NOT IN ({', '.join('?' for _ in _CLOSED_STATUSES)})"
    )
    task_params: list[object] = list(sorted(_CLOSED_STATUSES))
    if user_id is not None:
        task_sql += " AND t.user_id = ?"
        task_params.append(user_id)
    task_rows = conn.execute(task_sql, task_params).fetchall()
    for t in task_rows:
        start_raw = t["start"]
        end_raw = t["end"] or t["due"]
        if not start_raw and not end_raw:
            continue
        start_s = str(start_raw)[:10] if start_raw else today.isoformat()
        end_s = str(end_raw)[:10] if end_raw else start_s
        try:
            sd = date.fromisoformat(start_s)
            ed = date.fromisoformat(end_s)
        except ValueError:
            continue
        if ed < sd:
            sd, ed = ed, sd
        if ed < today or sd > horizon:
            continue
        clipped_start = max(sd, today)
        clipped_end = min(ed, horizon)
        add_event(
            t["project_id"],
            {
                "kind": "task",
                "start": clipped_start.isoformat(), "end": clipped_end.isoformat(),
                "start_days": (clipped_start - today).days, "end_days": (clipped_end - today).days,
                "title": t["title"], "approximate": False,
                "done": t["status"] in _CLOSED_STATUSES,
                "ref": t["id"], "detail": f"{t['title']}\n状態: {t['status']}", "overdue": False,
            },
        )

    # --- 優先タスク（T83・D24。期限は無いが早くやりたい印を付けたもの） ---
    # 上の task 帯は due/start/end を持つものだけを見る（「書いていないタスクは出さない」）
    # ので、期限の無いタスクはこのままだとタイムライン画面のどこにも出ない。`pinned` を
    # 立てたものだけ、帯とは別に「優先」欄として返す（日付を推測して帯に混ぜない）。
    priority_sql = (
        "SELECT t.id, t.project_id, t.status, n.title AS title"
        " FROM task t JOIN node n ON n.id = t.id"
        " WHERE t.pinned = 1"
        " AND t.due IS NULL AND t.start IS NULL AND t.\"end\" IS NULL"
        f" AND t.status NOT IN ({', '.join('?' for _ in _CLOSED_STATUSES)})"
    )
    priority_params: list[object] = list(sorted(_CLOSED_STATUSES))
    if user_id is not None:
        priority_sql += " AND t.user_id = ?"
        priority_params.append(user_id)
    priority_sql += " ORDER BY t.id"
    priority_tasks = [
        {"id": r["id"], "project_id": r["project_id"], "title": r["title"], "status": r["status"]}
        for r in conn.execute(priority_sql, priority_params).fetchall()
    ]

    # --- secretary_event（予定。secretary が居なければ黙って空） ---
    # ⚠ 2026-09-07: タイムラインは **カレンダーを1行も読んでいなかった**——9/8 の
    # 打ち合わせが画面に出ないと主人からご指摘。節目・期限・課題・控えだけを見ていて、
    # 実際に主人が出向く予定が抜けていた（朝のブリーフィングと同じ穴。同日に両方直した）。
    event_table = conn.execute(
        "SELECT * FROM sqlite_master WHERE type = 'table' AND name = 'secretary_event'"
    ).fetchone()
    if event_table is not None:
        event_sql = "SELECT * FROM secretary_event"
        event_params: tuple[object, ...] = ()
        if user_id is not None:
            has_user_col = any(
                str(r["name"]) == "user_id"
                for r in conn.execute("PRAGMA table_info(secretary_event)").fetchall()
            )
            if has_user_col:
                event_sql += " WHERE (user_id IS NULL OR user_id = ?)"
                event_params = (user_id,)
        event_sql += " ORDER BY start"
        for ev in conn.execute(event_sql, event_params).fetchall():
            sd_s = str(ev["start"])[:10]
            ed_s = str(ev["end"] or ev["start"])[:10]
            try:
                sd = date.fromisoformat(sd_s)
                ed = date.fromisoformat(ed_s)
            except ValueError:
                continue
            if ed < sd:
                sd, ed = ed, sd
            if ed < today or sd > horizon:
                continue
            place = str(ev["place"] or "").strip()
            # 結ばれていれば、その計画のレーンへ（2026-09-07 主人のご指摘——NEDO の
            # 打ち合わせが「その他」に落ちて、当の P10 が「予定の無い計画」に見えていた）。
            add_event(
                (ev["project_id"] if "project_id" in ev.keys() else None) or None,
                {
                    "kind": "event",
                    "start": max(sd, today).isoformat(), "end": min(ed, horizon).isoformat(),
                    "start_days": (max(sd, today) - today).days,
                    "end_days": (min(ed, horizon) - today).days,
                    "title": str(ev["title"]), "approximate": False, "done": False,
                    "overdue": False, "ref": ev["id"],
                    "detail": f"{ev['start']}\n{ev['title']}" + (f"\n場所: {place}" if place else ""),
                },
            )

    # --- secretary_reminder（未済。過ぎたものも出す。secretary が居なければ黙って空） ---
    remind_rows = conn.execute(
        "SELECT * FROM sqlite_master WHERE type = 'table' AND name = 'secretary_reminder'"
    ).fetchone()
    if remind_rows is not None:
        for r in conn.execute(
            "SELECT * FROM secretary_reminder WHERE done_at IS NULL ORDER BY on_date"
        ).fetchall():
            d = date.fromisoformat(str(r["on_date"])[:10])
            if d > horizon:
                continue
            add_event(
                None,
                {
                    "kind": "remind", "start": r["on_date"], "end": r["on_date"],
                    "start_days": (d - today).days, "end_days": (d - today).days,
                    "title": r["text"], "approximate": False, "done": False,
                    "overdue": d < today, "ref": r["id"], "detail": r["text"],
                },
            )

    ordered = sorted(
        lanes.values(),
        key=lambda ln: (0 if ln["events"] else 1, ln["priority"], str(ln["id"])),  # type: ignore[arg-type]
    )
    for lane in ordered:
        lane["events"] = sorted(lane["events"], key=lambda e: (e["start"], e["end"]))  # type: ignore[arg-type,index]
        lane["scheduled"] = bool(lane["events"])

    return {
        "today": today.isoformat(),
        "horizon_days": days,
        "horizon": horizon.isoformat(),
        "lanes": ordered,
        "priority_tasks": priority_tasks,
    }


def _decision_task_user_ids(conn: sqlite3.Connection, decision_id: str) -> list[str]:
    return [
        str(r["user_id"])
        for r in conn.execute(
            "SELECT t.user_id AS user_id FROM edge e JOIN task t ON t.id = e.src"
            " WHERE e.dst = ? AND e.rel = 'decided_by'",
            (decision_id,),
        ).fetchall()
    ]


def get_log(conn: sqlite3.Connection, home: Path, user_id: str | None = None) -> dict[str, object]:
    """`user_id`（ADR-014 D4）: 渡すと台帳の行を「誰の机か」で絞る（裁定は §D4 の
    「task に結ばれていない裁定は主人のもの」の規則。委譲・履歴は結ばれた task の
    利用者で絞る）。`None`（既定）は今までどおり全部。
    """
    state_path = Path(home) / "STATE.md"
    state_text = state_path.read_text(encoding="utf-8") if state_path.is_file() else ""
    decided = [d for d in decision_mod.list_decisions(conn) if d["status"] != "open"]
    handoffs = handoff_mod.list_handoffs(conn)
    results = check_mod.run(conn, home)
    events = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM task_event ORDER BY id DESC LIMIT 50"
        ).fetchall()
    ]

    if user_id is not None:
        principal = user_mod.principal_id(conn)
        if user_id != principal:
            decided = [
                d for d in decided
                if user_id in _decision_task_user_ids(conn, str(d["id"]))
            ]
        task_user_by_id: dict[str, str] = {
            str(r["id"]): str(r["user_id"])
            for r in conn.execute("SELECT id, user_id FROM task").fetchall()
        }
        handoffs = [h for h in handoffs if task_user_by_id.get(str(h["task_id"])) == user_id]
        events = [e for e in events if task_user_by_id.get(str(e["task_id"])) == user_id]

    return {
        "state": state_text,
        "decided": decided,
        "handoffs": handoffs,
        "check": {"ok": check_mod.ok(results), "results": results, "labels": check_mod.CHECK_LABELS},
        "events": events,
    }


class DecisionRuleRequest(BaseModel):
    # 主人の指摘（2026-09-02・2巡目）: 「一言なしで承認／却下できるように」。core の
    # `decision.rule` が既に空文を許すよう直っている（承認/却下は既定の一言を入れ、
    # 修正だけ文を必須にする）ので、API 層で `min_length=1` を課して弾く必要は無い。
    # ここで弾くと「空なら承認ボタンが押せない／400になる」が再現してしまう——
    # 空でもそのまま core（`decision_mod.rule`）へ渡し、判断は core に任せる。
    status: Literal["approved", "rejected", "modified"]
    ruling: str = Field("", max_length=2000)


class TaskStatusRequest(BaseModel):
    status: str = Field(..., min_length=1)
    note: str = Field("", max_length=2000)


class HandoffNoteRequest(BaseModel):
    note: str = Field("", max_length=2000)


def register(app: FastAPI, ctx: BoardContext) -> None:
    @app.get("/api/board")
    def api_board() -> dict[str, object]:
        with open_conn(ctx) as conn:
            return get_board(conn)

    @app.get("/api/timeline")
    def api_timeline(days: int = 70) -> dict[str, object]:
        with open_conn(ctx) as conn:
            return get_timeline(conn, days)

    @app.get("/api/log")
    def api_log() -> dict[str, object]:
        with open_conn(ctx) as conn:
            return get_log(conn, ctx.home)

    @app.get("/api/ctx/{node_id}")
    def api_ctx(node_id: str, depth: int = 2, budget: int = 2000) -> dict[str, object]:
        with open_conn(ctx) as conn:
            try:
                data = ctx_mod.build(conn, node_id, depth=depth, budget=budget)
            except ManorError as exc:
                raise manor_error_to_http(exc)
            return {"id": node_id, "markdown": ctx_mod.render_markdown(data)}

    @app.get("/api/handoff/{handoff_id}")
    def api_handoff_get(handoff_id: int) -> dict[str, object]:
        """委譲の指示書（brief）と報告（report）の本文。`/api/log` の一覧は列挙のみなので、
        本文はここで id 指定して読む（重いので毎回のポーリングには載せない）。
        """
        with open_conn(ctx) as conn:
            row = conn.execute("SELECT * FROM handoff WHERE id = ?", (handoff_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail=f"handoff が見つかりません: H{handoff_id}")
            return dict(row)

    @app.post("/api/decision/{decision_id}/rule")
    def api_decision_rule(decision_id: str, body: DecisionRuleRequest) -> dict[str, object]:
        require_writable(ctx)
        with open_conn(ctx) as conn:
            try:
                result = decision_mod.rule(conn, decision_id, body.status, ruling=body.ruling)
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result

    @app.post("/api/task/{task_id}/status")
    def api_task_status(task_id: str, body: TaskStatusRequest) -> dict[str, object]:
        require_writable(ctx)
        with open_conn(ctx) as conn:
            try:
                result = task_mod.status(conn, task_id, body.status, note=body.note, actor="board")
            except ManorError as exc:
                conn.rollback()
                # 状態機械の拒否（ADR-001 §4）は 409 で理由を返す約束。
                raise manor_error_to_http(exc, conflict_code=409)
            commit_and_render(conn, ctx)
            return result

    @app.post("/api/handoff/{handoff_id}/accept")
    def api_handoff_accept(handoff_id: int, body: HandoffNoteRequest) -> dict[str, object]:
        require_writable(ctx)
        with open_conn(ctx) as conn:
            try:
                result = handoff_mod.accept(conn, handoff_id, note=body.note)
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result

    @app.post("/api/handoff/{handoff_id}/reject")
    def api_handoff_reject(handoff_id: int, body: HandoffNoteRequest) -> dict[str, object]:
        require_writable(ctx)
        with open_conn(ctx) as conn:
            try:
                result = handoff_mod.reject(conn, handoff_id, note=body.note)
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result
