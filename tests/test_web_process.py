"""`manor web stop`（T23）— `launch-manor.cmd` にあった「ポートで待ち受けているプロセスを
止める」手順を core へ持ち上げたもの。**合成データのみ**（実際の netstat/taskkill は呼ばない
——`subprocess.run` を差し替える）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from manor import web as web_mod
from manor.web import _process


def _netstat_output(*lines: str) -> str:
    header = "\n  プロトコル  ローカル アドレス      外部アドレス        状態           PID\n"
    return header + "\n".join(lines)


NETSTAT_LISTENING_8789 = _netstat_output(
    "  TCP    0.0.0.0:8789           0.0.0.0:0              LISTENING       4321",
    "  TCP    127.0.0.1:8789         0.0.0.0:0              LISTENING       4321",
    "  TCP    0.0.0.0:8788           0.0.0.0:0              LISTENING       9999",
)

NETSTAT_NO_MATCH = _netstat_output(
    "  TCP    0.0.0.0:8788           0.0.0.0:0              LISTENING       9999",
    "  TCP    127.0.0.1:8789         127.0.0.1:51000        ESTABLISHED     1111",
)


def test_find_listening_pids_returns_empty_on_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_process.sys, "platform", "linux")
    assert _process.find_listening_pids(8789) == []


def test_find_listening_pids_parses_pid_from_listening_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_process.sys, "platform", "win32")
    monkeypatch.setattr(
        _process.subprocess, "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=NETSTAT_LISTENING_8789),
    )
    assert _process.find_listening_pids(8789) == [4321]


def test_find_listening_pids_ignores_other_ports_and_non_listening(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_process.sys, "platform", "win32")
    monkeypatch.setattr(
        _process.subprocess, "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=NETSTAT_NO_MATCH),
    )
    assert _process.find_listening_pids(8789) == []


def test_find_listening_pids_silent_when_netstat_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_process.sys, "platform", "win32")

    def _raise(*a, **kw):
        raise OSError("no netstat")

    monkeypatch.setattr(_process.subprocess, "run", _raise)
    assert _process.find_listening_pids(8789) == []


def test_stop_is_ok_when_nothing_is_listening(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_process, "find_listening_pids", lambda port: [])
    result = _process.stop(port=8789)
    assert result == {"port": 8789, "found": [], "killed": [], "ok": True}


def test_stop_kills_found_pids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_process, "find_listening_pids", lambda port: [4321])
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(_process.subprocess, "run", fake_run)
    result = _process.stop(port=8789)
    assert result == {"port": 8789, "found": [4321], "killed": [4321], "ok": True}
    assert calls == [["taskkill", "/F", "/PID", "4321"]]


def test_stop_reports_not_ok_when_taskkill_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_process, "find_listening_pids", lambda port: [4321])
    monkeypatch.setattr(
        _process.subprocess, "run",
        lambda *a, **kw: SimpleNamespace(returncode=1, stdout="", stderr="拒否されました"),
    )
    result = _process.stop(port=8789)
    assert result["ok"] is False
    assert result["killed"] == []


# --- CLI 配線（`manor web stop` / `manor web restart`） -----------------------------


def test_cli_stop_calls_process_stop_with_port(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(
        _process, "stop",
        lambda *, port: (calls.append(port), {"port": port, "found": [], "killed": [], "ok": True})[1],
    )
    args = SimpleNamespace(port=8789, json=False)
    rc = web_mod._cmd_stop(args)
    assert calls == [8789]
    assert rc == 0


def test_cli_stop_exits_nonzero_when_kill_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _process, "stop", lambda *, port: {"port": port, "found": [4321], "killed": [], "ok": False}
    )
    args = SimpleNamespace(port=8789, json=False)
    assert web_mod._cmd_stop(args) == 1


def test_cli_restart_stops_then_builds_then_serves(monkeypatch: pytest.MonkeyPatch) -> None:
    """T23: 「一言で止める→ビルド→起動」の順序を確かめる。`serve` はブロックするので呼び出しだけ見る。"""
    order: list[str] = []
    monkeypatch.setattr(
        _process, "stop",
        lambda *, port: (order.append("stop"), {"port": port, "found": [], "killed": [], "ok": True})[1],
    )
    monkeypatch.setattr(web_mod, "_cmd_build", lambda args: (order.append("build"), 0)[1])
    monkeypatch.setattr(web_mod, "_cmd_serve", lambda conn, home, args: order.append("serve"))

    args = SimpleNamespace(
        port=8789, host=None, read_only=False, open_browser=False, no_discovery=False,
        skip_build=False, json=False,
    )
    web_mod._cmd_restart(object(), "home", args)
    assert order == ["stop", "build", "serve"]


def test_cli_restart_skips_serve_when_build_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    monkeypatch.setattr(_process, "stop", lambda *, port: {"port": port, "found": [], "killed": [], "ok": True})
    monkeypatch.setattr(web_mod, "_cmd_build", lambda args: (order.append("build"), 1)[1])
    monkeypatch.setattr(web_mod, "_cmd_serve", lambda conn, home, args: order.append("serve"))

    args = SimpleNamespace(
        port=8789, host=None, read_only=False, open_browser=False, no_discovery=False,
        skip_build=False, json=False,
    )
    with pytest.raises(SystemExit) as exc_info:
        web_mod._cmd_restart(object(), "home", args)
    assert exc_info.value.code == 1
    assert order == ["build"]


def test_cli_restart_skip_build_goes_straight_to_serve(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    monkeypatch.setattr(_process, "stop", lambda *, port: {"port": port, "found": [], "killed": [], "ok": True})
    monkeypatch.setattr(web_mod, "_cmd_build", lambda args: order.append("build") or 0)
    monkeypatch.setattr(web_mod, "_cmd_serve", lambda conn, home, args: order.append("serve"))

    args = SimpleNamespace(
        port=8789, host=None, read_only=False, open_browser=False, no_discovery=False,
        skip_build=True, json=False,
    )
    web_mod._cmd_restart(object(), "home", args)
    assert order == ["serve"]


def test_web_stop_and_restart_subcommands_are_registered() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="group")
    web_mod.register(sub)

    stop_args = parser.parse_args(["web", "stop", "--port", "9000"])
    assert stop_args.func is web_mod._cmd_stop
    assert stop_args.port == 9000

    restart_args = parser.parse_args(["web", "restart", "--skip-build"])
    assert restart_args.func is web_mod._cmd_restart
    assert restart_args.skip_build is True
