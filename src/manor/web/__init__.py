"""manor の家庭用 Web アプリ（ADR-004・ADR-005）。`python -m manor.web` で起動する。

`register(subparsers)` は `src/manor/cli.py` の `build_parser()` に配線するための公開口
（`src/manor/board/__init__.py` と同じ形）。`manor web serve|build|install|uninstall|status`
を足す。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from .. import i18n, util

NAME = "web"
LABEL = "Web アプリ"


def register(subparsers: "argparse._SubParsersAction") -> None:
    p = subparsers.add_parser("web", help=i18n.t("cli.web.help"))
    sub = p.add_subparsers(dest="verb")
    _add_serve(sub)
    _add_build(sub)
    _add_install(sub)
    _add_uninstall(sub)
    _add_status(sub)


# --- serve -----------------------------------------------------------------------


def _add_serve(sub: "argparse._SubParsersAction") -> None:
    p = sub.add_parser("serve", help=i18n.t("cli.web.serve.help"))
    p.add_argument("--host", default="127.0.0.1", help=i18n.t("cli.web.serve.host.help"))
    p.add_argument("--port", type=int, default=8789)
    p.add_argument("--read-only", action="store_true", dest="read_only")
    p.add_argument("--open", action="store_true", dest="open_browser", help=i18n.t("cli.web.serve.open.help"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_serve, is_write=False)


def _cmd_serve(conn: object, home: Path, args: "argparse.Namespace") -> None:
    """`manor web serve` の本体。呼ぶとサーバが起動し、Ctrl+C まで戻らない。

    `conn`（`cli.main()` が開いた core 用の接続）は使わない——web はリクエストごとに
    別の接続を開く（board の `cmd_board` と同じ理由）。起動前に `check_startup_auth`
    （D4 の拒否）が走る——非ループバックで passcode が無ければ `ManorError` を投げ、
    `cli.main()` がメッセージを出して終了コード1で戻る。
    """
    from .app import run_server

    run_server(
        home=Path(home), host=args.host, port=args.port, read_only=args.read_only,
        open_browser=args.open_browser,
    )
    return None


# --- build -----------------------------------------------------------------------


def _add_build(sub: "argparse._SubParsersAction") -> None:
    p = sub.add_parser("build", help=i18n.t("cli.web.build.help"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_build, is_write=False, needs_db=False)


def _cmd_build(args: "argparse.Namespace") -> int:
    """`web/` で `npm ci`（`package-lock.json` が無ければ `npm install`）→ `npm run build`。

    node/npm が無ければそう言って終了コード1（ADR-005 §4）。DB は要らない
    （`needs_db=False`。night の `install`/`status` と同じ扱い）。
    """
    web_dir = util.repo_root() / "web"
    npm = shutil.which("npm")

    def _emit(payload: dict[str, object], text: str) -> None:
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(text)

    if npm is None:
        msg = i18n.t("web.build.npm_not_found")
        _emit({"ok": False, "error": msg}, msg)
        return 1
    if not web_dir.is_dir():
        msg = i18n.t("web.build.dir_not_found", web_dir=web_dir)
        _emit({"ok": False, "error": msg}, msg)
        return 1

    lock = web_dir / "package-lock.json"
    install_cmd = [npm, "ci"] if lock.is_file() else [npm, "install"]
    for step in (install_cmd, [npm, "run", "build"]):
        try:
            proc = subprocess.run(
                step, cwd=str(web_dir), capture_output=True, text=True, timeout=600,
                encoding="utf-8", errors="replace",
            )
        except OSError as exc:
            msg = i18n.t("web.build.step_failed_to_run", step=" ".join(step), exc=exc)
            _emit({"ok": False, "error": msg}, msg)
            return 1
        if proc.returncode != 0:
            msg = i18n.t(
                "web.build.step_failed",
                step=" ".join(step), code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr,
            )
            _emit({"ok": False, "error": msg, "stdout": proc.stdout, "stderr": proc.stderr}, msg)
            return 1

    dist = web_dir / "dist"
    _emit({"ok": True, "dist": str(dist)}, i18n.t("web.build.done", dist=dist))
    return 0


# --- install / uninstall / status --------------------------------------------------


def _add_install(sub: "argparse._SubParsersAction") -> None:
    p = sub.add_parser(
        "install", help=i18n.t("cli.web.install.help")
    )
    p.add_argument("--at", default="boot", help=i18n.t("cli.web.install.at.help"))
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8789)
    p.add_argument("--yes", action="store_true", help=i18n.t("cli.web.install.yes.help"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_install, is_write=False, needs_db=False)


def _cmd_install(args: "argparse.Namespace") -> int:
    from . import _install

    result = _install.install(host=args.host, port=args.port, execute=bool(args.yes))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["command"])
        print(i18n.t("web.install.registered") if result["executed"] else i18n.t("web.install.preview_only"))
    return 0


def _add_uninstall(sub: "argparse._SubParsersAction") -> None:
    p = sub.add_parser("uninstall", help=i18n.t("cli.web.uninstall.help"))
    p.add_argument("--yes", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_uninstall, is_write=False, needs_db=False)


def _cmd_uninstall(args: "argparse.Namespace") -> int:
    from . import _install

    result = _install.uninstall(execute=bool(args.yes))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["command"])
        print(i18n.t("web.uninstall.removed") if result["executed"] else i18n.t("web.uninstall.preview_only"))
    return 0


def _add_status(sub: "argparse._SubParsersAction") -> None:
    p = sub.add_parser("status", help=i18n.t("cli.web.status.help"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_status, is_write=False, needs_db=False)


def _cmd_status(args: "argparse.Namespace") -> int:
    from . import _install

    data = _install.status()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        sched = data.get("scheduled", {})
        if sched.get("registered") is True:
            print(i18n.t("web.status.registered"))
        elif sched.get("registered") is False:
            print(i18n.t("web.status.not_registered"))
        else:
            print(i18n.t("web.status.unknown", detail=sched.get("detail", "")))
    return 0
