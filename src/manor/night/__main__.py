"""`uv run python -m manor.night <verb> ...`。

`manor night` の配線（`src/manor/cli.py`）とは別の起動口
（`src/manor/board/__main__.py` と同じ形）。**引数の定義は書き写さない。**
`__init__.py` の `_add_*()` をそのまま呼ぶ。

以前はここに同じ引数を並べ直していた（「同じ dest 名を使う」と書き添えて）。
**2026-09-06 に食い違った**——`--sleep-back` を `register()` 側だけに足したところ、
`python -m manor.night run --sleep-back` が `unrecognized arguments` で落ちるように
なった。**スケジューラに登録されているのはこの起動口のほう**なので、夜勤が丸ごと
動かない朝になるところだった。書き写しは、書き写した瞬間から腐り始める。
"""

from __future__ import annotations

import argparse
import json
import sys

from .. import i18n
from . import _add_install, _add_report, _add_review, _add_run, _add_status, _add_uninstall


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m manor.night", description=i18n.t("cli.night.help"))
    sub = parser.add_subparsers(dest="verb")
    _add_run(sub)
    _add_status(sub)
    _add_install(sub)
    _add_uninstall(sub)
    _add_report(sub)
    _add_review(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 2
    # ⚠ DB が要る口（`review`）は `cli.py` と同じ3引数で呼ぶ。**能力を起動口ごとに
    # 変えない**——`--sleep-back` を片方だけに足して夜勤が丸ごと落ちかけた前例がある
    # （2026-09-06・G15）。
    if getattr(args, "needs_db", False):
        from .. import db as db_mod
        from .. import util as util_mod

        home = util_mod.manor_home()
        conn = db_mod.connect(home)
        try:
            out = func(conn, home, args)
            if getattr(args, "is_write", False):
                conn.commit()
        finally:
            conn.close()
        if out is not None and not isinstance(out, int):
            print(out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, indent=2, default=str))
            return 0
        return int(out or 0)
    return int(func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
