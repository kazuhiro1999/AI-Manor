"""ID の採番。`meta` のカウンタで単調増加させる（ADR-001 §3）。

欠番は許すが再利用はしない。カウンタの更新と、実際に行を作る INSERT は
同じトランザクション内で呼ぶこと（呼び手が commit するまでは両方ロールバックできる）。
"""

from __future__ import annotations

import sqlite3


def next_id(conn: sqlite3.Connection, prefix: str) -> str:
    """`meta` の `seq:<prefix>` を1つ進めて `<prefix><n>` を返す。

    `meta` に `seq:<prefix>` が無いとき（未初期化・v1取り込みが seq を進めずに
    node を直接作った、など）は 1 から始めず、既存の node の中で同じ prefix を
    持つ最大番号 + 1 から始める（T77: 2026-09-20 に `P1` と衝突した）。
    """
    key = f"seq:{prefix}"
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    if row is not None:
        n = int(row["value"]) + 1
        conn.execute("UPDATE meta SET value = ? WHERE key = ?", (str(n), key))
    else:
        n = _max_existing_number(conn, prefix) + 1
        conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (key, str(n)))
    return f"{prefix}{n}"


def _max_existing_number(conn: sqlite3.Connection, prefix: str) -> int:
    best = 0
    for row in conn.execute("SELECT id FROM node WHERE id LIKE ?", (f"{prefix}%",)):
        suffix = str(row[0])[len(prefix):]
        if suffix.isdigit():
            best = max(best, int(suffix))
    return best
