"""manor の家庭用 Web アプリ（ADR-004・ADR-005）。`python -m manor.web` で起動する。

`register(subparsers)` は `src/manor/cli.py` の `build_parser()` に配線するための公開口
（`src/manor/board/__init__.py` と同じ形）。`manor web serve|build|install|uninstall|status|device|log`
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
    _add_stop(sub)
    _add_restart(sub)
    _add_build(sub)
    _add_install(sub)
    _add_uninstall(sub)
    _add_status(sub)
    _add_device(sub)
    _add_log(sub)


# --- serve -----------------------------------------------------------------------


def _add_serve(sub: "argparse._SubParsersAction") -> None:
    p = sub.add_parser("serve", help=i18n.t("cli.web.serve.help"))
    # 既定は None＝`[web] host`（無ければ 127.0.0.1）。ADR-017 D4 の追補: デスクトップの
    # ショートカット（`manor shortcut create` が生成する launch-manor.cmd）は `--host` を渡さない
    # ので、LAN 向け（0.0.0.0）で立てたいときは設定に書く。ランチャーを手で直しても次の生成で
    # 戻ってしまう（2026-09-13 主人がショートカットで再起動→loopback に戻り、Quest から見えなくなった）。
    p.add_argument("--host", default=None, help=i18n.t("cli.web.serve.host.help"))
    p.add_argument("--port", type=int, default=8789)
    p.add_argument("--read-only", action="store_true", dest="read_only")
    p.add_argument("--open", action="store_true", dest="open_browser", help=i18n.t("cli.web.serve.open.help"))
    # ADR-017 D3: 探索（UDP 8791）を止める。既定は `[web] discovery`（true）に従う。
    p.add_argument(
        "--no-discovery", action="store_true", dest="no_discovery",
        help=i18n.t("cli.web.serve.no_discovery.help"),
    )
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
        home=Path(home), host=resolve_serve_host(Path(home), args.host), port=args.port,
        read_only=args.read_only,
        open_browser=args.open_browser,
        discovery=False if getattr(args, "no_discovery", False) else None,
    )
    return None


# --- stop / restart（T23。止めるには PID を殺すしかなかった） --------------------


def _add_stop(sub: "argparse._SubParsersAction") -> None:
    p = sub.add_parser("stop", help=i18n.t("cli.web.stop.help"))
    p.add_argument("--port", type=int, default=8789)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_stop, is_write=False, needs_db=False)


def _cmd_stop(args: "argparse.Namespace") -> int:
    """待ち受けているプロセスを `taskkill` で止める（`launch-manor.cmd` にあった手順を
    core へ持ち上げた。Windows 以外では「止めるものが無い」を返す）。
    """
    from . import _process

    result = _process.stop(port=args.port)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result["found"]:
        print(i18n.t("web.stop.killed", port=args.port, pids=", ".join(str(p) for p in result["killed"])))
    else:
        print(i18n.t("web.stop.nothing", port=args.port))
    return 0 if result["ok"] else 1


def _add_restart(sub: "argparse._SubParsersAction") -> None:
    p = sub.add_parser("restart", help=i18n.t("cli.web.restart.help"))
    p.add_argument("--host", default=None, help=i18n.t("cli.web.serve.host.help"))
    p.add_argument("--port", type=int, default=8789)
    p.add_argument("--read-only", action="store_true", dest="read_only")
    p.add_argument("--open", action="store_true", dest="open_browser", help=i18n.t("cli.web.serve.open.help"))
    p.add_argument(
        "--no-discovery", action="store_true", dest="no_discovery",
        help=i18n.t("cli.web.serve.no_discovery.help"),
    )
    p.add_argument("--skip-build", action="store_true", help=i18n.t("cli.web.restart.skip_build.help"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_restart, is_write=False)


def _cmd_restart(conn: object, home: Path, args: "argparse.Namespace") -> None:
    """`manor web restart` の本体: 止める → ビルド → 起動（`serve` と同じく Ctrl+C まで
    戻らない）。ビルドが失敗したら起動へは進まない（壊れたままの画面を出さない）。
    """
    from . import _process

    stop_result = _process.stop(port=args.port)
    if not args.json:
        if stop_result["found"]:
            print(i18n.t("web.stop.killed", port=args.port, pids=", ".join(str(p) for p in stop_result["killed"])))
        else:
            print(i18n.t("web.stop.nothing", port=args.port))

    if not args.skip_build:
        build_rc = _cmd_build(args)
        if build_rc != 0:
            raise SystemExit(build_rc)

    _cmd_serve(conn, home, args)
    return None


DEFAULT_SERVE_HOST = "127.0.0.1"


def resolve_serve_host(home: Path, explicit: "str | None") -> str:
    """`--host` が無ければ `[web] host`、それも無ければ loopback。

    `--host` を明示したときはそれが勝つ（設定に 0.0.0.0 があっても、試しに loopback で
    立てたいことはある）。
    """
    if explicit:
        return explicit
    from . import config as web_config

    value = web_config.get_web_section(home).get("host")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return DEFAULT_SERVE_HOST


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


def _outcome_line(result: dict, verb: str, *, done_key: str) -> str:
    """`install` / `uninstall` の結果1行。**返り値を見てから言う。**

    「実行を試みたか」（`executed`）ではなく「できたか」（`ok`）で言葉を選ぶ——
    その2つを同じ言葉で言っていたのが、`manor night install` が登録できていないのに
    「登録しました」と言った原因だった（butler/GROWTH.md G14）。
    """
    if not result["executed"]:
        return i18n.t(f"web.{verb}.preview_only")
    if result.get("ok"):
        return i18n.t(f"web.{verb}.{done_key}")
    detail = (result.get("stderr") or result.get("stdout") or "").strip()
    return i18n.t(f"web.{verb}.failed", returncode=result.get("returncode"), detail=detail)


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
        print(_outcome_line(result, "install", done_key="registered"))
    # **失敗を 0 で返さない**（night 側と同じ。2026-09-06 の検分で是正）。
    return 0 if result.get("ok", None) is not False else 1


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
        print(_outcome_line(result, "uninstall", done_key="removed"))
    return 0 if result.get("ok", None) is not False else 1


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


# --- log（ADR-024。操作ログを運用側で見る） -----------------------------------------


def _add_log(sub: "argparse._SubParsersAction") -> None:
    p = sub.add_parser("log", help=i18n.t("cli.web.log.help"))
    p.add_argument("--month", default=None, help=i18n.t("cli.web.log.month.help"))
    p.add_argument("--user", default=None, help=i18n.t("cli.web.log.user.help"))
    p.add_argument("--grep", default=None, help=i18n.t("cli.web.log.grep.help"))
    p.add_argument("-n", "--tail", type=int, default=50, help=i18n.t("cli.web.log.tail.help"))
    p.add_argument("--months", action="store_true", help=i18n.t("cli.web.log.months.help"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_log, is_write=False, needs_db=False)


def _cmd_log(args: "argparse.Namespace") -> int:
    """`manor web log`: `home/logs/web-YYYY-MM.log` の末尾を出す（`web/oplog.py`）。"""
    from . import oplog

    home = util.manor_home()
    if args.months:
        data: object = oplog.months(home)
        lines = [str(m) for m in data]  # type: ignore[attr-defined]
    else:
        lines = oplog.read_lines(home, month=args.month, user=args.user, grep=args.grep, tail=args.tail)
        data = lines
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif not lines:
        print(i18n.t("web.log.empty"))
    else:
        print("\n".join(lines))
    return 0


# --- device（ADR-017 D5。Web が使えないときの逃げ道） ---------------------------------


def _add_device(sub: "argparse._SubParsersAction") -> None:
    """`manor web device list|revoke <id>|pair <番号> [--user]`（ADR-017 D5）。

    **Web の画面と同じ段（`web/device.py`）を呼ぶ**——画面と CLI で判断が分かれないように、
    どちらも SQL を書かず同じ関数を通す。`manor web serve` が止まっているときでも
    （DB を直に開くので）端末の一覧と失効ができる。
    """
    p = sub.add_parser("device", help=i18n.t("cli.web.device.help"))
    dsub = p.add_subparsers(dest="device_verb")

    lp = dsub.add_parser("list", help=i18n.t("cli.web.device.list.help"))
    lp.add_argument(
        "--all", action="store_true", dest="include_revoked",
        help=i18n.t("cli.web.device.list.all.help"),
    )
    lp.add_argument("--json", action="store_true")
    lp.set_defaults(func=_cmd_device_list, is_write=False)

    rp = dsub.add_parser("revoke", help=i18n.t("cli.web.device.revoke.help"))
    rp.add_argument("device_id")
    rp.add_argument("--json", action="store_true")
    rp.set_defaults(func=_cmd_device_revoke, is_write=True)

    pp = dsub.add_parser("pair", help=i18n.t("cli.web.device.pair.help"))
    pp.add_argument("code", help=i18n.t("cli.web.device.pair.code.help"))
    pp.add_argument("--user", default=None, help=i18n.t("cli.web.device.pair.user.help"))
    pp.add_argument("--json", action="store_true")
    pp.set_defaults(func=_cmd_device_pair, is_write=True)


def _cmd_device_list(conn, home: Path, args: "argparse.Namespace") -> object:
    from . import device as device_mod

    items = device_mod.list_devices(conn, include_revoked=bool(args.include_revoked))
    if args.json:
        return {"items": items}
    if not items:
        return i18n.t("web.device.list.empty")
    lines = [
        i18n.t(
            "web.device.list.line",
            id=item["id"],
            name=item["name"],
            user=item.get("user_name") or item["user_id"],
            last_seen=item["last_seen_at"] or i18n.t("web.device.never"),
            state=i18n.t("web.device.revoked") if item["revoked_at"] else i18n.t("web.device.live"),
        )
        for item in items
    ]
    return "\n".join(lines)


def _cmd_device_revoke(conn, home: Path, args: "argparse.Namespace") -> object:
    from . import device as device_mod

    result = device_mod.revoke(conn, args.device_id)
    if args.json:
        return {"id": result["id"], "revoked_at": result["revoked_at"]}
    return i18n.t("web.device.revoked_line", id=result["id"], name=result["name"])


def _cmd_device_pair(conn, home: Path, args: "argparse.Namespace") -> object:
    """番号を許可して端末を作る。**鍵は端末が `pair/poll` で受け取る**ので、ここでは出さない
    （出せば端末の画面の外に平文が残ってしまう。ADR-017 D2-2）。
    """
    from .. import user as user_mod
    from . import device as device_mod

    user_id = (args.user or "").strip() or user_mod.principal_id(conn)
    result = device_mod.pair_approve(conn, code=args.code, user_id=user_id)
    if args.json:
        return result
    return i18n.t(
        "web.device.paired_line",
        name=result["name"], user=result["user_id"], id=result["device_id"],
    )
