"""他のPCのセッション（ADR-025 §6・§7）。中継から取り込んだイベントを畳み、紐づけ表を組む。

ここは DB を読み書きするだけの段で、HTTP のことは知らない（`relay.py` が中継と話す）。

## 活動の状態は合図から機械的に決める（§4.2）

Claude の申告（見出し・段階・進捗）は古くなり得るが、「作業中か・主人の番か」は hook の合図
（`prompt` / `stop` / `session_end`）だけで決まる。報告が古くても、ここは常に正しい。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from .. import util
from .client import manor_report as protocol

#: 合図がこの分数より古ければ「休止」。
IDLE_MINUTES = 30
#: 終了したセッションを一覧に残す時間。
ENDED_KEEP_HOURS = 24
#: 合図が来なくなったまま、この時間を過ぎたセッションは一覧から外す（終了の合図が来ないこともある）。
STALE_HOURS = 72

ACTIVITY_ORDER = {"your_turn": 0, "working": 1, "idle": 2, "ended": 3}

PHASES = protocol.PHASES
PHASE_LABELS_JA = protocol.PHASE_LABELS_JA


def _parse(ts: str | None) -> datetime | None:
    """時差つき・時差なし（manor の util.now）のどちらも、比べられる形にそろえる。"""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()  # ローカル時刻とみなす
    return dt


def _now() -> datetime:
    return _parse(util.now()) or datetime.now().astimezone()


# --- 取り込み ------------------------------------------------------------------------------


def ingest(conn: sqlite3.Connection, events: list[dict[str, Any]]) -> dict[str, Any]:
    """イベントを `remote_event` へ入れ（重複は捨てる）、触れたセッションを畳み直す。

    戻り値: `{"accepted", "duplicates", "sessions", "phase_changes"}`。`phase_changes` は
    タスクへの反映（§7.3）の材料: `[(session_id, task_id, 前の段階, 今の段階)]`。
    """
    received = util.now()
    accepted = 0
    touched: list[str] = []
    for ev in events:
        event_id = str(ev.get("event_id") or "")
        session_id = str(ev.get("session_id") or "")
        if not event_id or not session_id:
            continue
        cur = conn.execute(
            "INSERT OR IGNORE INTO remote_event(event_id, seq, kind, at, machine, session_id, payload, received_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (event_id, ev.get("seq"), str(ev.get("kind") or ""), str(ev.get("at") or received),
             str(ev.get("machine") or ""), session_id,
             json.dumps({k: v for k, v in ev.items() if k not in ("seq", "received_at")}, ensure_ascii=False),
             received),
        )
        if cur.rowcount:
            accepted += 1
            if session_id not in touched:
                touched.append(session_id)
    changes = []
    for sid in touched:
        change = fold_session(conn, sid)
        if change:
            changes.append(change)
    return {"accepted": accepted, "duplicates": len(events) - accepted, "sessions": touched,
            "phase_changes": changes}


def fold_session(conn: sqlite3.Connection, session_id: str) -> tuple[str, str, str, str] | None:
    """そのセッションのイベントを時刻順に畳み直して `remote_session` を書く。

    畳み直しは毎回全部から（届く順が前後しても、結果が時刻順の畳みと同じになる）。
    戻り値: 段階がタスク付きで変わったときだけ `(session_id, task_id, 前, 今)`。
    """
    before = conn.execute("SELECT phase, task_id FROM remote_session WHERE session_id=?",
                          (session_id,)).fetchone()
    rows = conn.execute(
        "SELECT payload FROM remote_event WHERE session_id=? ORDER BY at, seq", (session_id,)
    ).fetchall()
    s: dict[str, Any] = {
        "machine": "", "cwd": "", "repo_key": None, "repo_remote": None, "branch": None,
        "project_id": None, "task_id": None, "title": "", "phase": "", "progress": None,
        "human_next": "", "note": "", "last_kind": "", "started_at": None, "last_event_at": None,
        "last_report_at": None, "ended_at": None,
    }
    for (raw,) in rows:
        try:
            ev = json.loads(raw)
        except ValueError:
            continue
        kind = ev.get("kind")
        at = ev.get("at")
        repo = ev.get("repo") or {}
        s["machine"] = ev.get("machine") or s["machine"]
        s["cwd"] = ev.get("cwd") or s["cwd"]
        s["repo_key"] = repo.get("key") or s["repo_key"]
        s["repo_remote"] = repo.get("remote") or s["repo_remote"]
        s["branch"] = repo.get("branch") or s["branch"]
        s["started_at"] = s["started_at"] or at
        s["last_event_at"] = at
        if kind == "progress":
            r = ev.get("report") or {}
            for key, col in (("title", "title"), ("phase", "phase"), ("project", "project_id"), ("task", "task_id")):
                if r.get(key):
                    s[col] = r[key]
            if r.get("progress") is not None:
                s["progress"] = int(r["progress"])
            if "human_next" in r:
                s["human_next"] = r.get("human_next") or ""
            if "note" in r:
                s["note"] = r.get("note") or ""
            s["last_report_at"] = at
        else:
            s["last_kind"] = kind or s["last_kind"]
            if kind == "session_end":
                s["ended_at"] = at
            elif kind == "session_start":
                s["ended_at"] = None
    s["project_id"] = _normalize_project(conn, s["project_id"])
    s["task_id"] = (s["task_id"] or "").upper() or None
    cols = ["session_id", *s.keys()]
    conn.execute(
        f"INSERT OR REPLACE INTO remote_session({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
        (session_id, *s.values()),
    )
    prev_phase = before["phase"] if before else ""
    if s["task_id"] and s["phase"] and s["phase"] != prev_phase:
        return (session_id, s["task_id"], prev_phase or "", s["phase"])
    return None


def _normalize_project(conn: sqlite3.Connection, value: str | None) -> str | None:
    """申告の `p4` / `P4` / プロジェクトの code を、project の id にそろえる。知らなければそのまま。"""
    if not value:
        return None
    row = conn.execute(
        "SELECT id FROM project WHERE id=? COLLATE NOCASE OR code=? COLLATE NOCASE", (value, value)
    ).fetchone()
    return row["id"] if row else value


# --- 紐づけ --------------------------------------------------------------------------------


def link(conn: sqlite3.Connection, repo_key: str, project: str) -> dict[str, str]:
    pid = _normalize_project(conn, project)
    if not pid or not conn.execute("SELECT 1 FROM project WHERE id=?", (pid,)).fetchone():
        raise ValueError(f"知らないプロジェクトです: {project}")
    key = protocol.normalize_remote(repo_key) or repo_key.strip().lower()
    conn.execute("INSERT OR IGNORE INTO remote_repo_link(repo_key, project_id, created_at) VALUES (?,?,?)",
                 (key, pid, util.now()))
    return {"repo_key": key, "project_id": pid}


def unlink(conn: sqlite3.Connection, repo_key: str, project: str | None = None) -> int:
    key = protocol.normalize_remote(repo_key) or repo_key.strip().lower()
    if project:
        pid = _normalize_project(conn, project)
        cur = conn.execute("DELETE FROM remote_repo_link WHERE repo_key=? AND project_id=?", (key, pid))
    else:
        cur = conn.execute("DELETE FROM remote_repo_link WHERE repo_key=?", (key,))
    return cur.rowcount


def links(conn: sqlite3.Connection) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for r in conn.execute("SELECT repo_key, project_id FROM remote_repo_link ORDER BY repo_key, project_id"):
        out.setdefault(r["repo_key"], []).append(r["project_id"])
    return out


def build_directory(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """中継の `directory` に置く表: リポジトリの鍵 → プロジェクトと未完了タスク（§6）。"""
    entries: dict[str, dict[str, Any]] = {}
    for key, pids in links(conn).items():
        projects = []
        tasks = []
        for pid in pids:
            p = conn.execute(
                "SELECT p.id, p.code, n.title FROM project p JOIN node n ON n.id=p.id WHERE p.id=?", (pid,)
            ).fetchone()
            if not p:
                continue
            projects.append({"id": p["id"], "code": p["code"], "name": p["title"]})
            for t in conn.execute(
                "SELECT t.id, n.title, t.status, t.now FROM task t JOIN node n ON n.id=t.id"
                " WHERE t.project_id=? AND t.status IN ('todo','doing','waiting','hold')"
                " ORDER BY CASE t.status WHEN 'doing' THEN 0 WHEN 'waiting' THEN 1 WHEN 'todo' THEN 2 ELSE 3 END,"
                " n.updated_at DESC LIMIT 20",
                (pid,),
            ):
                tasks.append({"id": t["id"], "title": t["title"], "status": t["status"],
                              "now": _clip(t["now"], 80)})
        if projects:
            entries[key] = {"projects": projects, "tasks": tasks}
    return entries


def _clip(text: object, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


# --- 読む ----------------------------------------------------------------------------------


def activity(row: dict[str, Any], now: datetime | None = None) -> str:
    now = now or _now()
    if row.get("ended_at"):
        return "ended"
    last = _parse(row.get("last_event_at"))
    if last is None or now - last > timedelta(minutes=IDLE_MINUTES):
        return "idle"
    return "working" if row.get("last_kind") in ("prompt", "session_start") else "your_turn"


def list_sessions(conn: sqlite3.Connection, *, include_ended: bool = False,
                  now: datetime | None = None) -> list[dict[str, Any]]:
    """ダッシュボードの並び（§10）: あなたの番 → 作業中 → 休止 → 終了。同じ状態の中は新しい順。"""
    now = now or _now()
    linked = links(conn)
    titles = {r["id"]: r["title"] for r in conn.execute(
        "SELECT p.id, n.title FROM project p JOIN node n ON n.id=p.id")}
    out = []
    for r in conn.execute("SELECT * FROM remote_session"):
        row = dict(r)
        act = activity(row, now)
        last = _parse(row.get("last_event_at"))
        if act == "ended":
            ended = _parse(row.get("ended_at"))
            if not include_ended and (ended is None or now - ended > timedelta(hours=ENDED_KEEP_HOURS)):
                continue
        elif last is None or now - last > timedelta(hours=STALE_HOURS):
            if not include_ended:
                continue
        project_id = row.get("project_id")
        candidates = linked.get(row.get("repo_key") or "", [])
        if not project_id and len(candidates) == 1:
            project_id = candidates[0]
        task_title = None
        if row.get("task_id"):
            t = conn.execute("SELECT title FROM node WHERE id=?", (row["task_id"],)).fetchone()
            task_title = t["title"] if t else None
        phase = row.get("phase") or ""
        progress = row.get("progress")
        if progress is None and phase:
            progress = PHASES.get(phase)
        out.append({
            "session_id": row["session_id"],
            "machine": row["machine"],
            "activity": act,
            "title": row.get("title") or "",
            "phase": phase,
            "phase_label": PHASE_LABELS_JA.get(phase, phase),
            "progress": progress,
            "human_next": row.get("human_next") or "",
            "note": row.get("note") or "",
            "project_id": project_id,
            "project_title": titles.get(project_id or ""),
            "linked": bool(project_id),
            "task_id": row.get("task_id"),
            "task_title": task_title,
            "repo_key": row.get("repo_key"),
            "repo_remote": row.get("repo_remote"),
            "repo_name": _repo_name(row),
            "branch": row.get("branch"),
            "cwd": row.get("cwd"),
            "reported": bool(row.get("last_report_at")),
            "started_at": row.get("started_at"),
            "last_event_at": row.get("last_event_at"),
            "last_report_at": row.get("last_report_at"),
            "ended_at": row.get("ended_at"),
        })
    out.sort(key=lambda x: (ACTIVITY_ORDER.get(x["activity"], 9), _neg_ts(x["last_event_at"])))
    return out


def _neg_ts(ts: str | None) -> float:
    dt = _parse(ts)
    return -dt.timestamp() if dt else 0.0


def _repo_name(row: dict[str, Any]) -> str:
    key = row.get("repo_remote") or row.get("repo_key") or ""
    if key.startswith("dir:"):
        return key[4:]
    if key:
        return key.rsplit("/", 1)[-1]
    cwd = row.get("cwd") or ""
    return cwd.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def unlinked_repos(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """紐づけを伺うべきリポジトリ（直近に動いたが、申告にも紐づけにもプロジェクトが無い）。"""
    linked = links(conn)
    seen: dict[str, dict[str, Any]] = {}
    for s in list_sessions(conn):
        key = s.get("repo_key")
        if not key or key in linked or s.get("project_id"):
            continue
        seen.setdefault(key, {"repo_key": key, "machine": s["machine"], "cwd": s["cwd"]})
    return list(seen.values())


def format_active(conn: sqlite3.Connection) -> list[str]:
    """`manor active` の「■ 他のPCのセッション」の行。"""
    lines = []
    for s in list_sessions(conn):
        if s["activity"] == "ended":
            continue
        label = {"your_turn": "あなたの番", "working": "作業中", "idle": "休止"}[s["activity"]]
        head = s["title"] or f"{s['repo_name']}（報告待ち）"
        where = s["task_id"] or s["project_id"] or "未紐づけ"
        phase = s["phase_label"] or "—"
        pct = "" if s["progress"] is None else f" {s['progress']}%"
        nxt = f"｜主人の次: {s['human_next']}" if s["human_next"] else ""
        lines.append(f"  {s['machine']}｜{head}（{where}）｜{phase}{pct}｜{label}{nxt}")
    for r in unlinked_repos(conn):
        lines.append(f"  ⚠ 未紐づけのリポジトリ: {r['repo_key']}（{r['machine']}）— "
                     f"`manor remote link {r['repo_key']} <プロジェクト>` で結ぶ")
    return lines


def done_reports(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """完了と報告されたが、タスクはまだ閉じていないもの（執事が検分して閉じる。§7.3）。"""
    out = []
    for r in conn.execute(
        "SELECT s.session_id, s.machine, s.task_id, s.title FROM remote_session s"
        " JOIN task t ON t.id = s.task_id WHERE s.phase='done' AND t.status NOT IN ('done','withdrawn')"
    ):
        out.append(dict(r))
    return out
