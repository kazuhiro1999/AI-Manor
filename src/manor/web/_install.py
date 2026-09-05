"""`manor web install|uninstall|status`（ADR-005 §4）。night の `install`（`src/manor/night/
runner.py`）と同じ型: **組んで見せるだけ**。`--yes` を渡さない限り実際には登録しない。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from .. import util

DEFAULT_TASK_NAME = "ManorWeb"


def build_install_command(
    *, host: str = "127.0.0.1", port: int = 8789, repo_root: Path | None = None,
    task_name: str = DEFAULT_TASK_NAME,
) -> str:
    """Windows: ログオン時に起動（`/SC ONLOGON`）。ADR-005 §4「常時起動」。"""
    repo = Path(repo_root) if repo_root else util.repo_root()
    if sys.platform.startswith("win"):
        python_exe = repo / ".venv" / "Scripts" / "python.exe"
        inner = f'"{python_exe}" -m manor.web serve --host {host} --port {port}'
        tr = f'cmd /c cd /d "{repo}" && {inner}'
        return f'schtasks /Create /SC ONLOGON /TN "{task_name}" /TR "{tr}" /F'
    python_exe = repo / ".venv" / "bin" / "python"
    return (
        "# launchd/cron 雛形（macOS/Linux。schtasks に相当する自動登録は無い。手で組み込む）\n"
        f'@reboot cd "{repo}" && "{python_exe}" -m manor.web serve --host {host} --port {port}'
        f"  # {task_name}"
    )


def build_uninstall_command(*, task_name: str = DEFAULT_TASK_NAME) -> str:
    if sys.platform.startswith("win"):
        return f'schtasks /Delete /TN "{task_name}" /F'
    return f'crontab -l | grep -v "{task_name}" | crontab -   # launchd は unload の上 plist を rm'


def install(
    *, host: str = "127.0.0.1", port: int = 8789, execute: bool = False,
    repo_root: Path | None = None, task_name: str = DEFAULT_TASK_NAME,
) -> dict[str, Any]:
    cmd = build_install_command(host=host, port=port, repo_root=repo_root, task_name=task_name)
    result: dict[str, Any] = {"command": cmd, "executed": False}
    if execute:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)  # noqa: S602
        result.update(executed=True, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
    return result


def uninstall(*, execute: bool = False, task_name: str = DEFAULT_TASK_NAME) -> dict[str, Any]:
    cmd = build_uninstall_command(task_name=task_name)
    result: dict[str, Any] = {"command": cmd, "executed": False}
    if execute:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)  # noqa: S602
        result.update(executed=True, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
    return result


def _query_scheduled_task(task_name: str) -> dict[str, Any]:
    if sys.platform.startswith("win"):
        try:
            proc = subprocess.run(
                ["schtasks", "/Query", "/TN", task_name], capture_output=True, text=True, timeout=10
            )
        except Exception:
            return {"platform": "windows", "registered": None, "detail": "schtasks を呼べませんでした"}
        registered = proc.returncode == 0
        detail = (proc.stdout or proc.stderr or "").strip()[:400]
        return {"platform": "windows", "registered": registered, "detail": detail}
    return {"platform": sys.platform, "registered": None, "detail": "このOSでは自動確認していません"}


def status(*, task_name: str = DEFAULT_TASK_NAME) -> dict[str, Any]:
    return {"scheduled": _query_scheduled_task(task_name)}
