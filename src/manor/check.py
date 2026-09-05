"""整合検査（ADR-001 §9）。C1〜C9。確実に判定できるものだけ。誤検出は出さない。"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from . import db, render, util
from . import voice

CHECK_LABELS: dict[str, str] = {
    "C1": "ブロッカーが片付いたのに waiting/hold のまま（v_blocked_ready）",
    "C2": "3日動いていない doing（v_stale_doing）",
    "C3": "waiting なのに status_note が空",
    "C4": "section=A なのに recommendation が空、または decided_by の decision が無い",
    "C5": "level=HG で done なのに done への遷移の authorized_by が approved/modified の decision を指していない",
    "C6": "duplicates の src が withdrawn でない",
    "C7": "射影ファイルの sha256 が meta と違う（手編集の疑い）",
    "C8": "milestone の date が過去で project が active（済んでいないもののみ）",
    "C9": "部下の表名が `<name>_` で始まっていない",
    "C10": "追記ファイル（CHANGELOG.md・butler/GROWTH.md・home/LOG.md）が40KB超（manor archive を）",
    "C11": "evidence の無い open decision（警告。終了コードは変えない。ADR-006 D5）",
    "C12": "authorized_by が NULL でない task_event が、実在する decision/handoff を指していない（done は decision のみ許す。ADR-006 D21）",
    "C13": "muted_by_night が立ったまま夜勤が動いていない（home/night/night.lock が無い。ADR-008 D12）",
}

#: 警告のみの check（C10・C11・C13）。存在しても `manor check` の終了コードは変えない
#: （ADR-006 D5・D14・ADR-008 D12）。
WARNING_ONLY_CHECKS: frozenset[str] = frozenset({"C10", "C11", "C13"})


def _rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, object]]:
    return [dict(r) for r in conn.execute(sql).fetchall()]


def check_c1(conn: sqlite3.Connection) -> list[dict[str, object]]:
    return _rows(conn, "SELECT id FROM v_blocked_ready ORDER BY id")


def check_c2(conn: sqlite3.Connection) -> list[dict[str, object]]:
    return _rows(conn, "SELECT id, last_at FROM v_stale_doing ORDER BY id")


def check_c3(conn: sqlite3.Connection) -> list[dict[str, object]]:
    return _rows(
        conn,
        "SELECT id FROM task WHERE status = 'waiting' AND TRIM(status_note) = '' ORDER BY id",
    )


def check_c4(conn: sqlite3.Connection) -> list[dict[str, object]]:
    return _rows(
        conn,
        "SELECT t.id FROM task t WHERE t.section = 'A' AND ("
        " TRIM(t.recommendation) = '' OR NOT EXISTS ("
        "   SELECT 1 FROM edge e JOIN decision d ON d.id = e.dst"
        "   WHERE e.src = t.id AND e.rel = 'decided_by'"
        " )"
        ") ORDER BY t.id",
    )


def check_c5(conn: sqlite3.Connection) -> list[dict[str, object]]:
    """level=HG で done な task は、**最新**の to_status='done' の event の
    `authorized_by` が approved/modified の decision を指していなければならない
    （ADR-006 D21）。`decided_by` の辺（関係）はもう見ない——見るのは事実（authorized_by）。
    """
    return _rows(
        conn,
        "SELECT t.id FROM task t WHERE t.level = 'HG' AND t.status = 'done' AND NOT EXISTS ("
        "  SELECT 1 FROM task_event te WHERE te.task_id = t.id AND te.to_status = 'done'"
        "    AND te.id = ("
        "      SELECT MAX(te2.id) FROM task_event te2"
        "      WHERE te2.task_id = t.id AND te2.to_status = 'done'"
        "    )"
        "    AND te.authorized_by IS NOT NULL AND TRIM(te.authorized_by) != ''"
        "    AND EXISTS ("
        "      SELECT 1 FROM decision d WHERE d.id = te.authorized_by"
        "        AND d.status IN ('approved','modified')"
        "    )"
        ") ORDER BY t.id",
    )


def check_c6(conn: sqlite3.Connection) -> list[dict[str, object]]:
    return _rows(
        conn,
        "SELECT e.src AS id, e.dst FROM edge e JOIN task t ON t.id = e.src"
        " WHERE e.rel = 'duplicates' AND t.status != 'withdrawn' ORDER BY e.src",
    )


def check_c7(conn: sqlite3.Connection, home: Path) -> list[dict[str, object]]:
    home = Path(home)
    out: list[dict[str, object]] = []
    for rel, _builder in render.FILES.values():
        meta_key = f"render_sha256:{rel}"
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (meta_key,)).fetchone()
        recorded = str(row["value"]) if row is not None else None
        actual = util.sha256_file(home / rel)
        if recorded is None:
            continue  # まだ render していない（render 前の DB を検査しても誤検出にしない）
        if actual != recorded:
            out.append({"file": rel, "recorded": recorded, "actual": actual})
    return out


def check_c8(conn: sqlite3.Connection) -> list[dict[str, object]]:
    return _rows(
        conn,
        "SELECT m.id, m.date, m.project_id FROM milestone m JOIN project p ON p.id = m.project_id"
        " WHERE date(m.date) < date('now','localtime') AND p.status = 'active'"
        " AND m.done_at IS NULL ORDER BY m.id",
    )


def check_c9(conn: sqlite3.Connection) -> list[str]:
    staff_names = [name for name, _ in db.iter_staff_modules()]
    violations: list[str] = []
    for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
        name = str(r["name"])
        if name.startswith("sqlite_") or name in db.CORE_TABLES:
            continue
        if not any(name.startswith(f"{s}_") for s in staff_names):
            violations.append(name)
    return violations


def check_c10(home: Path, *, extra_paths: dict[str, Path] | None = None) -> list[dict[str, object]]:
    """追記ファイル（CHANGELOG.md・butler/GROWTH.md・home/LOG.md）が40KB超（ADR-006 §4 D14）。

    **警告扱い**（`ok()` の判定には数えない。`manor archive` を促すだけ）。
    `CHANGELOG.md`・`butler/GROWTH.md` は①層でリポジトリ直下にあり `home` の外なので、
    `util.repo_root()` から解決する。`extra_paths` は試験用の差し替え口
    （本物のリポジトリファイルを太らせずに済ませるため。`run()` からは渡さない）。
    """
    home = Path(home)
    root = util.repo_root()
    targets: dict[str, Path] = {
        "CHANGELOG.md": root / "CHANGELOG.md",
        "butler/GROWTH.md": root / "butler" / "GROWTH.md",
        "home/LOG.md": home / "LOG.md",
    }
    if extra_paths:
        targets.update(extra_paths)
    threshold = 40 * 1024
    out: list[dict[str, object]] = []
    for label, path in targets.items():
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > threshold:
            out.append({"path": label, "bytes": size})
    return out


def check_c11(conn: sqlite3.Connection) -> list[dict[str, object]]:
    """evidence の無い open decision（ADR-006 D5）。**警告**——`ok()` の判定には数えない。"""
    return _rows(
        conn,
        "SELECT id FROM decision WHERE status = 'open' AND TRIM(COALESCE(evidence, '')) = ''"
        " ORDER BY id",
    )


_HANDOFF_ID_RE = re.compile(r"^H(\d+)$")


def check_c12(conn: sqlite3.Connection) -> list[dict[str, object]]:
    """`authorized_by` が NULL でも空でもない task_event は、実在する decision か
    handoff を指していなければならない（ADR-006 D21）。**エラー**（`WARNING_ONLY_CHECKS`
    には入れない）。

    3周目レビューの補足（D21 補足）: 値域は遷移の種類で狭まる。**`to_status='done'` の
    event は decision の id しか許さない**（`H<n>` は委譲の裁定であって done を認める
    ものではない——`task.status()` が入り口で同じ規則を守るので通常はここに来ないが、
    DB を直接触った場合はここで拾う）。それ以外の遷移（doing/hold 等）は従来どおり
    `^H\\d+$` なら `handoff.id`、それ以外なら `decision.id` を見る。
    """
    rows = _rows(
        conn,
        "SELECT id, task_id, to_status, authorized_by FROM task_event"
        " WHERE authorized_by IS NOT NULL AND TRIM(authorized_by) != '' ORDER BY id",
    )
    out: list[dict[str, object]] = []
    for r in rows:
        value = str(r["authorized_by"])
        to_status = str(r["to_status"])
        m = _HANDOFF_ID_RE.match(value)

        if to_status == "done":
            if m:
                out.append({**r, "reason": "done の authorized_by が H<n>（委譲の裁定）です"})
                continue
            exists = conn.execute("SELECT 1 FROM decision WHERE id = ?", (value,)).fetchone()
            if exists is None:
                out.append({**r, "reason": "authorized_by が指す decision が見つかりません"})
            continue

        if m:
            exists = conn.execute(
                "SELECT 1 FROM handoff WHERE id = ?", (int(m.group(1)),)
            ).fetchone()
            if exists is None:
                out.append({**r, "reason": "authorized_by が指す handoff が見つかりません"})
        else:
            exists = conn.execute("SELECT 1 FROM decision WHERE id = ?", (value,)).fetchone()
            if exists is None:
                out.append({**r, "reason": "authorized_by が指す decision が見つかりません"})
    return out


def check_c13(home: Path) -> list[dict[str, object]]:
    """`muted_by_night` が true なのに夜勤が動いていない（`home/night/night.lock` が無い）とき
    警告する（ADR-008 D12）。**警告のみ**——`manor check` を夜勤の最中に回すこともあるし、
    止めるほどではない。

    夜勤の lock パスは `night/runner.py` から解決する（ここではハードコードしない）。
    """
    from .night import runner as night_runner

    home = Path(home)
    st = voice.state(home)
    if not st.get("muted_by_night"):
        return []
    if night_runner.lock_path(home).is_file():
        return []
    return [{"muted_by_night": True, "lock": False}]


def run(conn: sqlite3.Connection, home: Path) -> dict[str, list[object]]:
    return {
        "C1": check_c1(conn),
        "C2": check_c2(conn),
        "C3": check_c3(conn),
        "C4": check_c4(conn),
        "C5": check_c5(conn),
        "C6": check_c6(conn),
        "C7": check_c7(conn, home),
        "C8": check_c8(conn),
        "C9": check_c9(conn),
        "C10": check_c10(home),
        "C11": check_c11(conn),
        "C12": check_c12(conn),
        "C13": check_c13(home),
    }


def ok(results: dict[str, list[object]]) -> bool:
    return all(not v for k, v in results.items() if k not in WARNING_ONLY_CHECKS)
