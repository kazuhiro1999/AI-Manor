"""射影の生成（ADR-001 §5・§7）。

`home/projections/QUEUE.md` `home/projections/PROJECTS.md` `home/STATE.md` を
DB から生成する。**真実は DB。これらは読むためだけの生成物**——先頭に必ず
「自動生成。編集しないでください」を置き、sha256 を `meta` に記録する
（`check.py` の C7 が、記録と実際のファイルのずれ＝手編集の疑いを検出する）。

`manor active`（起動時の射影）もここが作る。hooks の SessionStart はこの
`active_text` をそのまま注入する。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import graph, profile as profile_mod, project as project_mod, util
from .errors import ManorError

DISCLAIMER = "自動生成。編集しないでください。書くときは `manor task …` / `manor project …`。"

_STATUS_LABEL = {
    "doing": "進行中",
    "waiting": "待ち",
    "hold": "保留",
    "todo": "未着手",
    "resident": "常駐",
}


def _clip(text: object, limit: int = 88) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _rows(conn: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> list[dict[str, object]]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


# --- active（起動時の射影） ----------------------------------------------------


def active_data(conn: sqlite3.Connection) -> dict[str, object]:
    """出すのは: A（open decision と section A のタスク）／ B の未完了／ resident／
    委譲中／ v_blocked_ready／ v_stale_doing／ 直近7日の milestone。完了済みは出さない。
    """
    open_decisions = _rows(
        conn,
        "SELECT d.id, n.title AS title, d.asked_at,"
        " CAST(julianday('now','localtime') - julianday(d.asked_at) AS INTEGER) AS days"
        " FROM decision d JOIN node n ON n.id = d.id WHERE d.status = 'open'"
        " ORDER BY CAST(substr(d.id, 2) AS INTEGER)",
    )
    section_a = _rows(
        conn,
        "SELECT t.*, n.title AS title FROM task t JOIN node n ON n.id = t.id"
        " WHERE t.section = 'A' AND t.status NOT IN ('done','withdrawn')"
        " ORDER BY CAST(substr(t.id, 2) AS INTEGER)",
    )
    section_b = _rows(
        conn,
        "SELECT t.*, n.title AS title FROM task t JOIN node n ON n.id = t.id"
        " WHERE t.section = 'B' AND t.status IN ('todo','doing','waiting','hold')"
        " ORDER BY CAST(substr(t.id, 2) AS INTEGER)",
    )
    resident = _rows(
        conn,
        "SELECT t.*, n.title AS title FROM task t JOIN node n ON n.id = t.id"
        " WHERE t.status = 'resident' ORDER BY CAST(substr(t.id, 2) AS INTEGER)",
    )
    delegated = _rows(
        conn,
        "SELECT t.*, n.title AS title FROM task t JOIN node n ON n.id = t.id"
        " WHERE t.status = 'doing' AND t.owner NOT IN ('butler','master')"
        " ORDER BY CAST(substr(t.id, 2) AS INTEGER)",
    )
    blocked_ready = _rows(conn, "SELECT id FROM v_blocked_ready ORDER BY CAST(substr(id, 2) AS INTEGER)")
    stale_doing = _rows(
        conn, "SELECT id, last_at FROM v_stale_doing ORDER BY CAST(substr(id, 2) AS INTEGER)"
    )
    milestones = _rows(
        conn,
        "SELECT m.id, n.title AS title, m.date, m.approximate, m.project_id FROM milestone m"
        " JOIN node n ON n.id = m.id"
        " WHERE date(m.date) BETWEEN date('now','localtime') AND date('now','localtime','+7 days')"
        " ORDER BY m.date",
    )
    return {
        "open_decisions": open_decisions,
        "section_a": section_a,
        "section_b": section_b,
        "resident": resident,
        "delegated": delegated,
        "blocked_ready": blocked_ready,
        "stale_doing": stale_doing,
        "milestones": milestones,
    }


def format_active(data: dict[str, object], *, width: int = 88) -> str:
    out: list[str] = []
    open_decisions = list(data["open_decisions"])  # type: ignore[arg-type]
    section_a = list(data["section_a"])  # type: ignore[arg-type]
    section_b = list(data["section_b"])  # type: ignore[arg-type]
    resident = list(data["resident"])  # type: ignore[arg-type]
    delegated = list(data["delegated"])  # type: ignore[arg-type]
    blocked_ready = list(data["blocked_ready"])  # type: ignore[arg-type]
    stale_doing = list(data["stale_doing"])  # type: ignore[arg-type]
    milestones = list(data["milestones"])  # type: ignore[arg-type]

    out.append(f"■ A. 主人待ち: {len(section_a)}件（判断待ち {len(open_decisions)}件）")
    for d in open_decisions:
        out.append(f"  {d['id']}（{d['days']}日）{_clip(d['title'], width)}")
    for t in section_a:
        out.append(f"  {t['id']} [{t['project_id'] or '—'}] {_clip(t['title'], width)}")
        rec = _clip(t["recommendation"], width - 8)
        if rec:
            out.append(f"      推奨: {rec}（risk {t['risk'] or '—'}）")
    if not open_decisions and not section_a:
        out.append("  （なし）")

    by_status: dict[str, list[dict[str, object]]] = {}
    for t in section_b:
        by_status.setdefault(str(t["status"]), []).append(t)
    tally = "／".join(
        f"{_STATUS_LABEL[c]} {len(by_status.get(c, []))}"
        for c in ("doing", "waiting", "hold", "todo")
        if by_status.get(c)
    )
    out.append("")
    out.append(f"■ B. 未完了: {len(section_b)}件（{tally or 'なし'}）")
    for code in ("doing", "waiting", "hold", "todo"):
        rows = by_status.get(code, [])
        if not rows:
            continue
        out.append(f"  --- {_STATUS_LABEL[code]} ---")
        for t in rows:
            out.append(f"  {t['id']} [{t['project_id'] or '—'}] {_clip(t['title'], width)}")
            if code in ("waiting", "hold") and t["status_note"]:
                out.append(f"      → {_clip(t['status_note'], width)}")

    out.append("")
    if resident:
        out.append(f"■ 常駐: {len(resident)}件")
        for t in resident:
            out.append(f"  {t['id']} {_clip(t['title'], width)}")
    else:
        out.append("■ 常駐: なし")

    out.append("")
    if delegated:
        out.append(f"■ 委譲中: {len(delegated)}件")
        for t in delegated:
            out.append(f"  {t['id']} -> {t['owner']} {_clip(t['title'], width)}")
    else:
        out.append("■ 委譲中: なし")

    if blocked_ready:
        out.append("")
        out.append(f"■ 止まる理由が消えている: {len(blocked_ready)}件")
        for r in blocked_ready:
            out.append(f"  {r['id']}")

    if stale_doing:
        out.append("")
        out.append(f"■ 3日以上動きの無い進行中: {len(stale_doing)}件")
        for r in stale_doing:
            out.append(f"  {r['id']}（最終 {r['last_at']}）")

    if milestones:
        out.append("")
        out.append(f"■ 直近7日のマイルストーン: {len(milestones)}件")
        for m in milestones:
            approx = "頃" if m["approximate"] else ""
            out.append(f"  {m['date']}{approx} {_clip(m['title'], width)} [{m['project_id'] or '—'}]")

    return "\n".join(out)


def active_text(conn: sqlite3.Connection) -> str:
    return format_active(active_data(conn))


# --- 射影ファイル ---------------------------------------------------------------


def queue_markdown(conn: sqlite3.Connection) -> str:
    data = active_data(conn)
    lines = ["# QUEUE", "", DISCLAIMER, ""]

    lines.append(
        f"## A. 主人待ち（タスク {len(data['section_a'])}件 / 判断待ち {len(data['open_decisions'])}件）"
    )
    if data["open_decisions"]:
        lines.append("")
        lines.append("### 判断待ち")
        for d in data["open_decisions"]:
            lines.append(f"- {d['id']}（{d['days']}日）{_clip(d['title'], 80)}")
    if data["section_a"]:
        lines.append("")
        lines.append("### タスク")
        for t in data["section_a"]:
            lines.append(f"- {t['id']} [{t['project_id'] or '—'}] {_clip(t['title'], 80)}")
            if t["recommendation"]:
                lines.append(f"    推奨: {_clip(t['recommendation'], 80)}（risk {t['risk'] or '—'}）")
    if not data["open_decisions"] and not data["section_a"]:
        lines.append("")
        lines.append("（なし）")

    lines.append("")
    lines.append(f"## B. 自走（未完了 {len(data['section_b'])}件）")
    by_status: dict[str, list[dict[str, object]]] = {}
    for t in data["section_b"]:  # type: ignore[union-attr]
        by_status.setdefault(str(t["status"]), []).append(t)
    for code in ("doing", "waiting", "hold", "todo"):
        rows = by_status.get(code, [])
        if not rows:
            continue
        lines.append("")
        lines.append(f"### {_STATUS_LABEL[code]}")
        for t in rows:
            lines.append(f"- {t['id']} [{t['project_id'] or '—'}] {_clip(t['title'], 80)}")
            if code in ("waiting", "hold") and t["status_note"]:
                lines.append(f"    → {_clip(t['status_note'], 80)}")

    if data["resident"]:
        lines.append("")
        lines.append(f"## 常駐（{len(data['resident'])}件）")
        for t in data["resident"]:  # type: ignore[union-attr]
            lines.append(f"- {t['id']} {_clip(t['title'], 80)}")

    return "\n".join(lines) + "\n"


def projects_markdown(conn: sqlite3.Connection) -> str:
    rows = project_mod.list_projects(conn)
    lines = ["# PROJECTS", "", DISCLAIMER, "", "## プロジェクト"]
    if rows:
        lines.append("")
        lines.append("| code | 名前 | 種別 | 優先度 | preset | status | 次の一手 | 期限 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for p in rows:
            lines.append(
                f"| {p['code']} | {_clip(p['title'], 30)} | {p['kind'] or '—'} | {p['priority']} |"
                f" {p['preset']} | {p['status']} | {_clip(p['next_action'], 30)} | {p['due'] or '—'} |"
            )
    else:
        lines.append("")
        lines.append("（なし）")

    lines.append("")
    lines.append("## マイルストーン")
    ms = graph.milestone_list(conn)
    if ms:
        lines.append("")
        lines.append("| 日付 | 件名 | project |")
        lines.append("|---|---|---|")
        for m in ms:
            approx = "頃" if m["approximate"] else ""
            lines.append(f"| {m['date']}{approx} | {_clip(m['title'], 40)} | {m['project_id'] or '—'} |")
    else:
        lines.append("")
        lines.append("（なし）")

    return "\n".join(lines) + "\n"


def state_markdown(conn: sqlite3.Connection) -> str:
    lines = ["# STATE", "", DISCLAIMER, "", "`manor active` と同じ内容です。", ""]
    lines.append(active_text(conn))
    return "\n".join(lines) + "\n"


def profile_markdown(conn: sqlite3.Connection) -> str:
    """ADR-007 D3: 呼び名・執事の呼び名・用途・自由記述・完了日時。"""
    data = profile_mod.get_all(conn)
    callname = data.get("master.callname", "").strip()
    butler_name = data.get("butler.callname", "").strip() or "執事"
    labels = [profile_mod.PURPOSES[i] for i in profile_mod.purposes_of(data)]
    note = data.get("purposes.note", "").strip()
    completed_at = data.get("setup.completed_at", "").strip()

    lines = ["# PROFILE", "", DISCLAIMER, ""]
    lines.append(f"- 主人の呼び名: {callname or '（未設定）'}")
    lines.append(f"- 執事の呼び名: {butler_name}")
    lines.append(f"- 用途: {'、'.join(labels) if labels else '（未設定）'}")
    lines.append(f"- 自由記述: {note or '（なし）'}")
    lines.append(f"- セットアップ完了: {completed_at or '（未完了）'}")
    return "\n".join(lines) + "\n"


#: render 対象。key は CLI の `--only` に使う名前、値は (home からの相対パス, 生成関数)。
FILES: dict[str, tuple[str, object]] = {
    "QUEUE": ("projections/QUEUE.md", queue_markdown),
    "PROJECTS": ("projections/PROJECTS.md", projects_markdown),
    "STATE": ("STATE.md", state_markdown),
    "PROFILE": ("projections/PROFILE.md", profile_markdown),
}


def render(conn: sqlite3.Connection, home: Path, *, only: str | None = None) -> list[str]:
    """射影ファイルを書き出し、sha256 を `meta` に記録する。書いた相対パスの一覧を返す。"""
    home = Path(home)
    targets = [only] if only else list(FILES.keys())
    written: list[str] = []
    for key in targets:
        if key not in FILES:
            raise ManorError(
                f"未知の render 対象です: {key}",
                code=2,
                key="error.render.target_unknown",
                params={"target": key},
            )
        rel, builder = FILES[key]
        path = home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        text = builder(conn)  # type: ignore[operator]
        data = text.encode("utf-8")
        # `write_text` は Windows で改行を \r\n に変換してしまい、書いた文字列から
        # 計算した sha256 とファイル実体の sha256 がずれる。バイト列を直接書いて揃える。
        path.write_bytes(data)
        sha = util.sha256_text(text)
        meta_key = f"render_sha256:{rel}"
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (meta_key, sha),
        )
        written.append(rel)
    conn.commit()
    return written
