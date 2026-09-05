"""`uv run python -m manor.board [--host] [--port] [--read-only] [--open]`。

`manor board` 配線（`src/manor/cli.py`）は執事が別途行うので、それまでの起動口は
これ。`MANOR_HOME` を尊重し、DB が無ければ `manor init` を促して終了コード1で戻る
（`src/manor/cli.py` の `main()` と同じ約束）。
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

    parser = argparse.ArgumentParser(
        prog="python -m manor.board", description="manor board -- ダッシュボード（Web UI）"
    )
    parser.add_argument("--host", default="127.0.0.1", help="待ち受けアドレス（既定 127.0.0.1）")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--read-only", action="store_true", dest="read_only")
    parser.add_argument("--open", action="store_true", dest="open_browser", help="起動後にブラウザを開く")
    args = parser.parse_args(argv)

    home = util.manor_home()
    try:
        require_db(home)
    except ManorError as exc:
        print(exc.message_ja)
        return exc.code

    from .app import run_server

    run_server(
        home=home,
        host=args.host,
        port=args.port,
        read_only=args.read_only,
        open_browser=args.open_browser,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
