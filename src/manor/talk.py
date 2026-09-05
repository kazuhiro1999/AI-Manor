"""担当と直接話す — `manor talk <name>`（ROADMAP §6 5e・§7-2）。

主人 → 執事 → 担当 の経路に加えて、**主人 → 担当** の直接対話の入口。
実体は `claude --agent <name>`（担当の定義 `.claude/agents/<name>.md` が
そのまま人格・道具・モデルになる。CLAUDE.md も読まれるので、CLAUDE.md 側に
「担当として起動されたとき」の一節がある）。`butler` は `--agent` なしの素の `claude`。

DB は執事と同じ `MANOR_HOME` を共有するので、料理長が書いた在庫は
ダッシュボードにも執事にもそのまま見える。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from . import i18n, util
from .errors import ManorError

BUTLER = "butler"


def available_agents(repo: Path | None = None) -> list[str]:
    root = repo or util.repo_root()
    agents_dir = root / ".claude" / "agents"
    if not agents_dir.is_dir():
        return [BUTLER]
    names = sorted(p.stem for p in agents_dir.glob("*.md"))
    return [BUTLER, *names]


def build_command(
    name: str, *, extra: list[str] | None = None, claude_bin: str | None = None,
    repo: Path | None = None,
) -> list[str]:
    """起動コマンドを組む。存在しない担当は `ManorError`（終了コード 2 相当）。"""
    name = name.strip()
    agents = available_agents(repo)
    if name not in agents:
        known = " / ".join(agents)
        raise ManorError(
            f"担当が見つかりません: {name!r}（使えるのは {known}）",
            code=2,
            key="error.talk.agent_not_found",
            params={"name": repr(name), "known": known},
        )
    exe = claude_bin or shutil.which("claude") or "claude"
    cmd = [exe]
    if name != BUTLER:
        cmd += ["--agent", name]
    cmd += list(extra or [])
    return cmd


def run(name: str, *, extra: list[str] | None = None, dry_run: bool = False) -> int:
    cmd = build_command(name, extra=extra)
    if dry_run:
        print(" ".join(cmd))
        return 0
    env = {**os.environ, "MANOR_HOME": str(util.manor_home())}
    try:
        return subprocess.call(cmd, cwd=str(util.repo_root()), env=env)
    except OSError as exc:
        raise ManorError(
            f"claude を起動できません: {exc}",
            key="error.talk.launch_failed",
            params={"exc": str(exc)},
        ) from exc


def register(subparsers) -> None:  # type: ignore[no-untyped-def]
    p = subparsers.add_parser(
        "talk",
        help=i18n.t("cli.talk.help"),
        description=i18n.t("cli.talk.description"),
    )
    p.add_argument("name", help=i18n.t("cli.talk.name.help"))
    p.add_argument("--dry-run", action="store_true", help=i18n.t("cli.talk.dry_run.help"))
    p.add_argument("extra", nargs="*", help=i18n.t("cli.talk.extra.help"))
    p.set_defaults(func=_cmd_talk, is_write=False, needs_db=False)


def _cmd_talk(args) -> int:  # type: ignore[no-untyped-def]
    return run(args.name, extra=list(args.extra or []), dry_run=bool(args.dry_run))
