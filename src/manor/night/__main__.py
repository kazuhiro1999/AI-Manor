"""`uv run python -m manor.night <verb> ...`。

`manor night` の配線（`src/manor/cli.py`）は執事が別途行うので、それまでの起動口はこれ
（`src/manor/board/__main__.py` と同じ形）。引数の定義は `__init__.py` の `register()` と
同じ dest 名を使い、実処理（`_cmd_*`）はそちらから再利用する——二重に書いて食い違わせない。
"""

from __future__ import annotations

import argparse
import sys

from .. import i18n
from . import _cmd_install, _cmd_report, _cmd_run, _cmd_status, _cmd_uninstall


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m manor.night", description=i18n.t("cli.night.help"))
    sub = parser.add_subparsers(dest="verb")

    from . import runner

    p = sub.add_parser("run", help=i18n.t("cli.night.run.help"))
    p.add_argument("--deadline", default=runner.DEFAULT_DEADLINE)
    p.add_argument("--min-minutes", type=int, default=runner.DEFAULT_MIN_MINUTES, dest="min_minutes")
    p.add_argument("--grace", type=int, default=runner.DEFAULT_GRACE_MINUTES, dest="grace_minutes")
    p.add_argument("--dry-run", action="store_true", dest="dry_run")
    p.add_argument("--exec", dest="exec_cmd")
    p.add_argument("--now")
    p.add_argument("--model", default=runner.DEFAULT_MODEL)
    p.add_argument("--max-turns", type=int, default=runner.DEFAULT_MAX_TURNS, dest="max_turns")
    p.add_argument("--no-resume", action="store_true", dest="no_resume")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("status", help=i18n.t("cli.night.status.help"))
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("install", help=i18n.t("cli.night.install.help"))
    p.add_argument("--at", default="01:00")
    p.add_argument("--dry-run", action="store_true", dest="dry_run")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("uninstall", help=i18n.t("cli.night.uninstall.help"))
    p.add_argument("--yes", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("report", help=i18n.t("cli.night.report.help"))
    p.add_argument("date", nargs="?")
    p.add_argument("--json", action="store_true")

    return parser


_DISPATCH = {
    "run": _cmd_run,
    "status": _cmd_status,
    "install": _cmd_install,
    "uninstall": _cmd_uninstall,
    "report": _cmd_report,
}


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    if not args.verb:
        parser.print_help()
        return 2
    return int(_DISPATCH[args.verb](args) or 0)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
