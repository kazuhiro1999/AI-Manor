"""`run` 表への記録（ADR-006 §3）。トレースとコストの計測。

`start`/`finish` は DB へ書く。`from_claude_result` は `claude -p --output-format json`
（または stream-json の `result` イベント）の辞書から `finish` に渡せる形を作る**純粋関数**
（DB に触らない）——夜勤（`night/runner.py`）・振る舞い試験・関門（`gate.py`。担当B）の
どれからも同じ形で呼べる。

**書く側は誰か**（D10）: 夜勤・振る舞い試験・関門。**執事のセッション内の委譲（Agent ツール）は
CLI から測れない**——これは機構の限界として明記する（`handoff report` の報告に「概算コスト」欄を
任意で持たせる程度が現実的で、ここでは強制しない）。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from . import util
from .errors import ManorError

#: `run.kind`（core.sql の CHECK と一致させること）。
VALID_KINDS: frozenset[str] = frozenset({"night", "behavior", "gate", "talk", "other"})

#: `run.exit_reason`。空文字は「まだ終わっていない」（`start` 直後）。
VALID_EXIT_REASONS: frozenset[str] = frozenset({"", "done", "failed", "killed", "timeout", "limit"})

#: `usage` 辞書が持ちうるキー（`finish` に渡す形。`from_claude_result` が作る）。
_USAGE_KEYS: tuple[str, ...] = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
)


def start(conn: sqlite3.Connection, kind: str, ref: str = "", model: str = "") -> int:
    """1本の起動を記録する。返る id を `finish` に渡す。"""
    if kind not in VALID_KINDS:
        known = " / ".join(sorted(VALID_KINDS))
        raise ManorError(
            f"語彙外の run.kind です: {kind!r}（{known}）",
            code=2,
            key="error.runlog.kind_unknown",
            params={"kind": repr(kind), "known": known},
        )
    cur = conn.execute(
        "INSERT INTO run (kind, ref, started_at, model, exit_reason) VALUES (?, ?, ?, ?, '')",
        (kind, ref, util.now(), model),
    )
    return int(cur.lastrowid or 0)


def finish(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    usage: dict[str, Any] | None = None,
    cost: float | None = None,
    turns: int | None = None,
    exit_reason: str = "",
    note: str = "",
) -> None:
    """起動の終わりを記録する。

    `usage` は `input_tokens` / `output_tokens` / `cache_read_tokens` / `cache_write_tokens`
    のいずれかのキーを持つ辞書（`from_claude_result` の戻り値の `usage` をそのまま渡せる）。
    無い・None のキーは NULL のまま。
    """
    if exit_reason not in VALID_EXIT_REASONS:
        known = " / ".join(sorted(VALID_EXIT_REASONS - {""}))
        raise ManorError(
            f"語彙外の exit_reason です: {exit_reason!r}（{known}）",
            code=2,
            key="error.runlog.exit_reason_unknown",
            params={"exit_reason": repr(exit_reason), "known": known},
        )
    usage = usage or {}
    conn.execute(
        "UPDATE run SET ended_at = ?, input_tokens = ?, output_tokens = ?,"
        " cache_read_tokens = ?, cache_write_tokens = ?, cost_usd = ?, turns = ?,"
        " exit_reason = ?, note = ? WHERE id = ?",
        (
            util.now(),
            usage.get("input_tokens"),
            usage.get("output_tokens"),
            usage.get("cache_read_tokens"),
            usage.get("cache_write_tokens"),
            cost,
            turns,
            exit_reason,
            note,
            run_id,
        ),
    )


def from_claude_result(result: dict[str, Any]) -> dict[str, Any]:
    """`claude -p --output-format json`（または stream-json の `result` イベント）の
    辞書から `finish(...)` にそのまま渡せる形を作る**純粋関数**。DB に触らない。

    `usage`（`input_tokens`/`output_tokens`/`cache_creation_input_tokens`/
    `cache_read_input_tokens`）・`total_cost_usd`・`num_turns`・`is_error` を読む
    （Anthropic API の usage オブジェクトと同じキー名。claude CLI の JSON 出力の実測に基づく）。
    キーが無ければ該当する値は `None`（**推測で埋めない**）。
    """
    raw_usage = result.get("usage") or {}
    usage = {
        "input_tokens": raw_usage.get("input_tokens"),
        "output_tokens": raw_usage.get("output_tokens"),
        "cache_read_tokens": raw_usage.get("cache_read_input_tokens"),
        "cache_write_tokens": raw_usage.get("cache_creation_input_tokens"),
    }
    exit_reason = "failed" if result.get("is_error") else "done"
    return {
        "usage": usage,
        "cost": result.get("total_cost_usd"),
        "turns": result.get("num_turns"),
        "exit_reason": exit_reason,
    }


def list_runs(
    conn: sqlite3.Connection, *, kind: str | None = None, days: int | None = None
) -> list[dict[str, Any]]:
    """`manor run list [--kind] [--days]`。新しい順。"""
    if kind is not None and kind not in VALID_KINDS:
        known = " / ".join(sorted(VALID_KINDS))
        raise ManorError(
            f"語彙外の kind です: {kind!r}（{known}）",
            code=2,
            key="error.runlog.kind_unknown",
            params={"kind": repr(kind), "known": known},
        )
    sql = "SELECT * FROM run WHERE 1=1"
    params: list[object] = []
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    if days is not None:
        sql += " AND started_at >= datetime('now', 'localtime', ?)"
        params.append(f"-{int(days)} days")
    sql += " ORDER BY id DESC"
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):  # 移行前の DB。`manor init` で足される
            return []
        raise


def _parse_at(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def stats(conn: sqlite3.Connection, *, days: int = 30) -> list[dict[str, Any]]:
    """kind 別の件数・合計コスト・平均所要秒・失敗率・トークン合計（ADR-006 D11・D23）。

    **道具は判断しない**——並べ替え・集計まで。「安かったか」の判断は執事がこの数字を見て行う（D12）。

    D23: `count` は母数（bucket の全行数）であって「計測できた件数」ではない。
    `cost_measured` は `cost_usd IS NOT NULL` の行数。`tokens_measured` は
    `input_tokens`/`output_tokens` の**両方**が non-null な行数として数える
    （`cache_read_tokens`/`cache_write_tokens` はキャッシュ利用が無ければ元々 0 件でも
    構わない値で、`input`/`output` ほど「計測できたか」の芯ではない。§6 担当C
    `tests/web/test_runs.py` が `cache_*` 無し・`input`/`output` だけの合成データで
    集計を検算しており、4本すべて必須にすると母数が縮んでその試験と食い違う——
    「4本すべて」も仕様として成り立つ選択だが、ここでは「input/output の両方」を採る）。
    `cost_measured == 0` なら `cost_usd` は `None`、`tokens_measured == 0` なら4本の
    トークン合計は `None`（`avg_seconds` が `_duration_n == 0` のとき `None` になるのと
    同じ扱いに揃える）。
    """
    try:
        rows = conn.execute(
            "SELECT kind, started_at, ended_at, cost_usd, exit_reason,"
            " input_tokens, output_tokens, cache_read_tokens, cache_write_tokens"
            " FROM run WHERE started_at >= datetime('now', 'localtime', ?)",
            (f"-{int(days)} days",),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        # 移行前の DB（`run` 表が無い）。`manor init` を回せば足される。集計は「記録なし」として返す
        if "no such table" in str(exc):
            return []
        raise

    buckets: dict[str, dict[str, Any]] = {}
    for r in rows:
        k = str(r["kind"])
        b = buckets.setdefault(
            k,
            {
                "kind": k,
                "count": 0,
                "cost_usd": 0.0,
                "cost_measured": 0,
                "failed": 0,
                "_duration_sum": 0.0,
                "_duration_n": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "tokens_measured": 0,
            },
        )
        b["count"] += 1
        if r["cost_usd"] is not None:
            b["cost_usd"] += float(r["cost_usd"])
            b["cost_measured"] += 1
        if str(r["exit_reason"] or "") in ("failed", "killed", "timeout", "limit"):
            b["failed"] += 1
        started = _parse_at(r["started_at"])
        ended = _parse_at(r["ended_at"])
        if started is not None and ended is not None:
            b["_duration_sum"] += (ended - started).total_seconds()
            b["_duration_n"] += 1
        token_keys = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
        if r["input_tokens"] is not None and r["output_tokens"] is not None:
            b["tokens_measured"] += 1
            for key in token_keys:
                if r[key] is not None:
                    b[key] += int(r[key])

    out: list[dict[str, Any]] = []
    for k in sorted(buckets):
        b = buckets[k]
        avg = b["_duration_sum"] / b["_duration_n"] if b["_duration_n"] else None
        has_tokens = b["tokens_measured"] > 0
        out.append(
            {
                "kind": k,
                "count": b["count"],
                "cost_usd": round(b["cost_usd"], 4) if b["cost_measured"] else None,
                "cost_measured": b["cost_measured"],
                "avg_seconds": round(avg, 1) if avg is not None else None,
                "fail_rate": round(b["failed"] / b["count"], 3) if b["count"] else 0.0,
                "input_tokens": b["input_tokens"] if has_tokens else None,
                "output_tokens": b["output_tokens"] if has_tokens else None,
                "cache_read_tokens": b["cache_read_tokens"] if has_tokens else None,
                "cache_write_tokens": b["cache_write_tokens"] if has_tokens else None,
                "tokens_measured": b["tokens_measured"],
            }
        )
    return out
