"""manor の夜勤 — v1 `night-shift` の仕組みの移植（ROADMAP 5i）。

**トリガーはしない。** 主人の要望（2026-09-02）は「仕組みは引き継ぎたいが、今は v1 が現役なので
manor 側からは起動しない」。このため `install` は OS のスケジューラへ登録するコマンドを
**組んで見せるだけ**（`--yes` を渡さない限り実行しない）。

`register(subparsers)` は将来 `src/manor/cli.py` の `build_parser()` に配線するための
公開口（`src/manor/board/__init__.py` と同じ形。`cli.py` 自体は今回のスコープ外——
配線は執事が行う）。`manor night` を打てるようになったときの形は次のとおり:

    from . import night as night_mod
    night_mod.register(subparsers)

それまでの起動口は `python -m manor.night <verb> ...`（`__main__.py`）。
実体は `runner.py`。ここは CLI の薄い配線層に留める。
"""

from __future__ import annotations

import argparse
import json

from .. import i18n, util
from . import runner

NAME = "night"
LABEL = "夜勤"


def register(subparsers: "argparse._SubParsersAction") -> None:
    """`manor night run|status|install|uninstall|report` を足す。"""
    p = subparsers.add_parser("night", help=i18n.t("cli.night.help"))
    sub = p.add_subparsers(dest="verb")
    _add_run(sub)
    _add_status(sub)
    _add_install(sub)
    _add_uninstall(sub)
    _add_report(sub)


def _add_run(sub: "argparse._SubParsersAction") -> None:
    p = sub.add_parser("run", help=i18n.t("cli.night.run.help"))
    p.add_argument(
        "--deadline", default=runner.DEFAULT_DEADLINE,
        help=i18n.t("cli.night.run.deadline.help", deadline=runner.DEFAULT_DEADLINE),
    )
    p.add_argument(
        "--min-minutes", type=int, default=runner.DEFAULT_MIN_MINUTES, dest="min_minutes",
        help=i18n.t("cli.night.run.min_minutes.help"),
    )
    p.add_argument(
        "--grace", type=int, default=runner.DEFAULT_GRACE_MINUTES, dest="grace_minutes",
        help=i18n.t("cli.night.run.grace.help"),
    )
    p.add_argument("--dry-run", action="store_true", dest="dry_run", help=i18n.t("cli.night.run.dry_run.help"))
    p.add_argument("--exec", dest="exec_cmd", help=i18n.t("cli.night.run.exec.help"))
    p.add_argument("--now", help=i18n.t("cli.night.run.now.help"))
    p.add_argument("--model", default=runner.DEFAULT_MODEL)
    p.add_argument("--max-turns", type=int, default=runner.DEFAULT_MAX_TURNS, dest="max_turns")
    p.add_argument("--no-resume", action="store_true", dest="no_resume", help=i18n.t("cli.night.run.no_resume.help"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_run, is_write=False, needs_db=False)


def _cmd_run(args: "argparse.Namespace") -> int:
    home = util.manor_home()
    result = runner.run(
        home,
        deadline=args.deadline,
        min_minutes=args.min_minutes,
        grace_minutes=args.grace_minutes,
        dry_run=args.dry_run,
        exec_cmd=args.exec_cmd,
        now=args.now,
        model=args.model,
        max_turns=args.max_turns,
        no_resume=args.no_resume,
        echo=not args.json,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.dry_run:
        print(i18n.t("night.run.preview_header"))
        for line in result.get("preview_lines", []):
            print(line)
        print(i18n.t("night.run.preview_footer"))
        print(i18n.t("night.run.command_line", command=" ".join(result.get("command", []))))
    return 0 if result.get("status") not in ("failed", "failed_no_time") else 1


def _add_status(sub: "argparse._SubParsersAction") -> None:
    p = sub.add_parser("status", help=i18n.t("cli.night.status.help"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_status, is_write=False, needs_db=False)


def _cmd_status(args: "argparse.Namespace") -> int:
    home = util.manor_home()
    data = runner.status(home)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(runner.format_status(data))
    return 0


def _add_install(sub: "argparse._SubParsersAction") -> None:
    p = sub.add_parser(
        "install", help=i18n.t("cli.night.install.help")
    )
    p.add_argument("--at", default="01:00")
    p.add_argument("--dry-run", action="store_true", dest="dry_run", help=i18n.t("cli.night.install.dry_run.help"))
    p.add_argument("--yes", action="store_true", help=i18n.t("cli.night.install.yes.help"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_install, is_write=False, needs_db=False)


def _cmd_install(args: "argparse.Namespace") -> int:
    execute = bool(args.yes) and not bool(args.dry_run)
    result = runner.install(at=args.at, execute=execute)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["command"])
        print(i18n.t("night.install.done") if result["executed"] else i18n.t("night.install.dry_run_note"))
    return 0


def _add_uninstall(sub: "argparse._SubParsersAction") -> None:
    p = sub.add_parser("uninstall", help=i18n.t("cli.night.uninstall.help"))
    p.add_argument("--yes", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_uninstall, is_write=False, needs_db=False)


def _cmd_uninstall(args: "argparse.Namespace") -> int:
    result = runner.uninstall(execute=bool(args.yes))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["command"])
        print(i18n.t("night.uninstall.done") if result["executed"] else i18n.t("night.uninstall.dry_run_note"))
    return 0


def _add_report(sub: "argparse._SubParsersAction") -> None:
    p = sub.add_parser("report", help=i18n.t("cli.night.report.help"))
    p.add_argument("date", nargs="?", help=i18n.t("cli.night.report.date.help"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_report, is_write=False, needs_db=False)


def _cmd_report(args: "argparse.Namespace") -> int:
    home = util.manor_home()
    result = runner.report(home, args.date)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["text"])
    return 0
