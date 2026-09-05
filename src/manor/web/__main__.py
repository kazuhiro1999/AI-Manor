"""`uv run python -m manor.web serve [--host] [--port] [--read-only] [--open]`。

`manor web` 配線（`src/manor/cli.py`）が無い環境でもこの経路から起動できる
（`src/manor/board/__main__.py` と同じ形）。`MANOR_HOME` を尊重する。
"""

from __future__ import annotations

import argparse
import sys

from .. import util
from ..db import require_db
from ..errors import ManorError


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass

    parser = argparse.ArgumentParser(prog="python -m manor.web", description="manor web -- 家庭用 Web アプリ")
    sub = parser.add_subparsers(dest="verb")
    serve_p = sub.add_parser("serve")
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--port", type=int, default=8789)
    serve_p.add_argument("--read-only", action="store_true", dest="read_only")
    serve_p.add_argument("--open", action="store_true", dest="open_browser")
    args = parser.parse_args(argv)

    if args.verb != "serve":
        parser.print_help()
        return 2

    home = util.manor_home()
    try:
        require_db(home)
    except ManorError as exc:
        print(exc.message_ja)
        return exc.code

    from .app import run_server

    try:
        run_server(
            home=home, host=args.host, port=args.port, read_only=args.read_only,
            open_browser=args.open_browser,
        )
    except ManorError as exc:
        print(exc.message_ja)
        return exc.code
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
