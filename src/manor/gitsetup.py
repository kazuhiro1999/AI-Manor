"""git の pre-commit を配線する（`manor init` から呼ぶ）。

`.githooks/pre-commit` は置いてあるだけでは動かない——git は既定で `.git/hooks/` しか見ない
（QA 指摘 2026-09-02）。「規則は機構で守る」を名乗るなら、配線も機構がやる。
リポジトリでない・git が無い・失敗した、のどれでも**黙って空文字**を返す（init を止めない）。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import i18n

HOOKS_DIR = ".githooks"


def ensure_git_hooks(repo: Path) -> str:
    """`core.hooksPath` を `.githooks` に向ける。冪等。何をしたかの1行を返す（何もしなければ空）。"""
    if not (repo / ".git").is_dir() or not (repo / HOOKS_DIR / "pre-commit").is_file():
        return ""
    git = shutil.which("git")
    if git is None:
        return ""
    try:
        cur = subprocess.run(
            [git, "-C", str(repo), "config", "--get", "core.hooksPath"],
            capture_output=True, text=True, encoding="utf-8", timeout=10,
        )
        if cur.returncode == 0 and cur.stdout.strip() == HOOKS_DIR:
            return i18n.t("gitsetup.already_set", hooks_dir=HOOKS_DIR)
        subprocess.run(
            [git, "-C", str(repo), "config", "core.hooksPath", HOOKS_DIR],
            capture_output=True, text=True, encoding="utf-8", timeout=10, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return i18n.t("gitsetup.set", hooks_dir=HOOKS_DIR)
