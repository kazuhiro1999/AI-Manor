"""manor のダッシュボード（Web UI）。`python -m manor.board` で起動する。

`register(subparsers)` は将来 `src/manor/cli.py` の `build_parser()` に配線するための
公開口（ADR-001 §5 の CLI 契約の形に合わせる。`cli.py` 自体は今回のスコープ外——
配線は執事が行う）。`manor board` を打てるようになったときの形は次のとおり:

    p = subparsers.add_parser("board", ...)
    p.set_defaults(func=cmd_board, is_write=False)

`cmd_board` は `cli.main()` が開いた `conn` を使わない（board はリクエストごとに
自分で接続を開く。理由は `_common.open_conn` の docstring）。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .. import i18n

NAME = "board"
LABEL = "ダッシュボード"


def register(subparsers: "argparse._SubParsersAction") -> None:
    """`manor board [--host] [--port] [--read-only] [--open]` を足す。"""
    p = subparsers.add_parser("board", help=i18n.t("cli.board.help"))
    p.add_argument("--host", default="127.0.0.1", help=i18n.t("cli.web.serve.host.help"))
    p.add_argument("--port", type=int, default=8788)
    p.add_argument("--read-only", action="store_true", dest="read_only")
    p.add_argument("--open", action="store_true", dest="open_browser", help=i18n.t("cli.web.serve.open.help"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_board, is_write=False)


def cmd_board(conn: object, home: Path, args: "argparse.Namespace") -> None:
    """`manor board` の本体。呼ぶとサーバが起動し、Ctrl+C まで戻らない。

    `conn`（`cli.main()` が開いた core 用の接続）は使わない——board はリクエストごとに
    別の接続を開くので、ここで受け取った1本を使い回すと FastAPI のスレッドプールと衝突する。
    """
    # ADR-005 §6-13（2026-09-03）: 同等性表を満たしたので `manor board` は Web アプリの別名。
    # 主人の習慣のポート 8788 はそのまま。素 JS の旧画面は `python -m manor.board` でだけ残す。
    from ..web.app import run_server as run_web

    print(i18n.t("cli.board.merged_notice"))
    run_web(
        home=Path(home),
        host=args.host,
        port=args.port,
        read_only=args.read_only,
        open_browser=args.open_browser,
    )
    return None
