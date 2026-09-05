"""`manor init` が pre-commit を配線すること（QA 指摘 2026-09-02）。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from manor import gitsetup

REPO = Path(__file__).resolve().parent.parent


def _make_repo(tmp_path: Path, *, with_git: bool) -> Path:
    repo = tmp_path / "repo"
    (repo / ".githooks").mkdir(parents=True)
    shutil.copy(REPO / ".githooks" / "pre-commit", repo / ".githooks" / "pre-commit")
    if with_git:
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return repo


@pytest.mark.skipif(shutil.which("git") is None, reason="git が無い環境")
def test_sets_hooks_path_and_is_idempotent(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, with_git=True)
    first = gitsetup.ensure_git_hooks(repo)
    assert "設定しました" in first
    got = subprocess.run(
        ["git", "-C", str(repo), "config", "--get", "core.hooksPath"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert got == ".githooks"
    second = gitsetup.ensure_git_hooks(repo)
    assert "既に" in second


def test_silent_when_not_a_repo(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path, with_git=False)
    assert gitsetup.ensure_git_hooks(repo) == ""


def test_silent_when_no_hook_file(tmp_path: Path) -> None:
    repo = tmp_path / "bare"
    (repo / ".git").mkdir(parents=True)
    assert gitsetup.ensure_git_hooks(repo) == ""
