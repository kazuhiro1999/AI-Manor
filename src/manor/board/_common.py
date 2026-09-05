"""board 内部の共通部品。

なぜ分けるか: `app.py`（起動・静的配信）・`api_core.py`（task/project/decision/handoff/ctx）・
`api_staff.py`（部下4名）の3つが、DB 接続の作法とエラーの HTTP への変換を共有する。
コピーして3箇所に書くと、直したときに1箇所だけ直り忘れる（manor 本体が避けている罠と同じ）。

**書き込みは必ず manor 側の API 関数（`task.py` / `decision.py` / `handoff.py` / 各 staff の
`cli.py` の `cmd_*`）を呼ぶ。** ここでは SQL を書かない（読み取りの SELECT は api_*.py 側で行う）。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

from fastapi import HTTPException

from .. import db as db_mod
from .. import render as render_mod
from ..errors import ManorError


@dataclass
class BoardContext:
    """board が丸ごと持ち回る設定。`app.state.board_ctx` に1つだけ置く。"""

    home: Path
    read_only: bool


@contextmanager
def open_conn(ctx: BoardContext) -> Iterator[sqlite3.Connection]:
    """リクエストごとに新しい接続を開く。

    `sqlite3.Connection` はスレッド間で共有しない約束（既定 `check_same_thread=True`）。
    FastAPI の同期ハンドラはスレッドプールで動くため、`cli.py` の main() のように
    接続を1つだけ使い回すことはできない。**接続はリクエスト単位**にする。
    `--read-only` のときは `PRAGMA query_only` も立てる（API 層の 403 チェックに加えた
    二重の保険。GET しか通らないはずの経路でも、万一 POST が素通りしたら DB 側が拒む）。
    """
    conn = db_mod.connect(ctx.home)
    if ctx.read_only:
        conn.execute("PRAGMA query_only = ON")
    try:
        yield conn
    finally:
        conn.close()


def require_writable(ctx: BoardContext) -> None:
    """`--read-only` のとき POST を 403 で止める。書き込み系ハンドラの先頭で呼ぶ。"""
    if ctx.read_only:
        raise HTTPException(status_code=403, detail="読み取り専用モードです（--read-only）")


def commit_and_render(conn: sqlite3.Connection, ctx: BoardContext) -> None:
    """書き込み成功後の約束: commit してから射影を更新する（CLI の書き込み系と同じ）。"""
    conn.commit()
    render_mod.render(conn, ctx.home)


def manor_error_to_http(exc: ManorError, *, conflict_code: int = 400) -> HTTPException:
    """`ManorError` を HTTP へ写す。

    `code=2`（見つからない・語彙外）は 404。それ以外は呼び出し側が指定する
    `conflict_code`（既定 400。task の状態機械の拒否だけ 409 を渡す約束——ADR-001 §4 の
    状態機械は「業務規則」であって「壊れている」ではないため、409 Conflict が実情に合う）。
    """
    if exc.code == 2:
        return HTTPException(status_code=404, detail=exc.message_ja)
    return HTTPException(status_code=conflict_code, detail=exc.message_ja)


def ns(**kwargs: object) -> SimpleNamespace:
    """`(conn, home, args)` 形の CLI 関数へ渡す軽い args 代用。

    manor の CLI コマンド関数（`cmd_*`）は `args.json` 等の属性アクセスしかしないので、
    `argparse.Namespace` を素通りできる `SimpleNamespace` で足りる。board はこれで
    staff の `cli.py` の関数（`cmd_house_today` 等）をそのまま呼び、SQL を書き直さない。
    """
    kwargs.setdefault("json", True)
    return SimpleNamespace(**kwargs)
