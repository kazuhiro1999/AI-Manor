"""ID の採番。`meta` のカウンタで単調増加させる（ADR-001 §3）。

欠番は許すが再利用はしない。カウンタの更新と、実際に行を作る INSERT は
同じトランザクション内で呼ぶこと（呼び手が commit するまでは両方ロールバックできる）。
"""

from __future__ import annotations

import sqlite3


def next_id(conn: sqlite3.Connection, prefix: str) -> str:
    """`meta` の `seq:<prefix>` を1つ進めて `<prefix><n>` を返す。"""
    key = f"seq:{prefix}"
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    n = int(row["value"]) + 1 if row is not None else 1
    if row is not None:
        conn.execute("UPDATE meta SET value = ? WHERE key = ?", (str(n), key))
    else:
        conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (key, str(n)))
    return f"{prefix}{n}"
