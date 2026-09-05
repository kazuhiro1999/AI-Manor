"""`manor talk <name>` — 担当との直接対話の起動口（ROADMAP §6 5e）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from manor import talk
from manor.errors import ManorError


def _repo(tmp_path: Path, *names: str) -> Path:
    d = tmp_path / "repo" / ".claude" / "agents"
    d.mkdir(parents=True)
    for n in names:
        (d / f"{n}.md").write_text("---\nname: x\n---\n", encoding="utf-8")
    return tmp_path / "repo"


def test_available_agents_lists_butler_first(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "chef", "secretary")
    assert talk.available_agents(repo) == ["butler", "chef", "secretary"]


def test_build_command_for_staff_uses_agent_flag(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "chef")
    cmd = talk.build_command("chef", claude_bin="claude", repo=repo)
    assert cmd == ["claude", "--agent", "chef"]


def test_build_command_for_butler_is_plain_claude(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "chef")
    assert talk.build_command("butler", claude_bin="claude", repo=repo) == ["claude"]


def test_extra_args_pass_through(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "chef")
    cmd = talk.build_command("chef", extra=["--model", "sonnet"], claude_bin="claude", repo=repo)
    assert cmd[-2:] == ["--model", "sonnet"]


def test_unknown_agent_is_rejected(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "chef")
    with pytest.raises(ManorError) as exc:
        talk.build_command("gardener", claude_bin="claude", repo=repo)
    assert "gardener" in exc.value.message_ja
