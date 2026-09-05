"""②④（主人の情報・環境固有）が git に載らないことを機械で検算する（ADR-001 D5）。

v1 は ② が 6 箇所に散り、.gitignore が 15 行になっていた。manor は `home/*` の 1 行に集約した。
「集約した」だけでは守れていない——**実際に git が無視するか**を `git check-ignore` で確かめる。
リーク語彙の検査は、語彙リスト自体が個人情報なので**リポジトリの外**（環境変数 `MANOR_LEAK_TERMS`）
から読む。無ければその部分だけ skip する（pre-commit と同じ約束）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
GITIGNORE = REPO / ".gitignore"

#: ② と ④ の代表。ここが ignore されなければ境界は破れている。
MUST_BE_IGNORED = (
    "home/manor.db",
    "home/manor.db-wal",
    "home/USER.md",
    "home/ENV.md",
    "home/LOG.md",
    "home/STATE.md",
    "home/projections/QUEUE.md",
    "home/inbox/何かの書類.pdf",
    "home/handoffs/H1_T1_chef.md",
    ".claude/settings.local.json",
)

#: ① と ③ の代表。ここが ignore されたら公開物が消える。
MUST_BE_TRACKED = (
    "home/README.md",
    "CLAUDE.md",
    "butler/policy.toml",
    "src/manor/cli.py",
    "docs/design/ADR-001_core.md",
)

#: 追跡対象の走査で読まない場所。
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "home", "node_modules", "dist", "shots"}


def test_gitignore_collapses_private_data_into_home() -> None:
    lines = [ln.strip() for ln in GITIGNORE.read_text(encoding="utf-8").splitlines()]
    assert "home/*" in lines
    assert "!home/README.md" in lines
    assert ".claude/settings.local.json" in lines


@pytest.mark.skipif(shutil.which("git") is None, reason="git が無い環境")
def test_git_actually_ignores_home_and_keeps_readme(tmp_path: Path) -> None:
    """`.gitignore` を本物の git に読ませて、②④が落ち ①③が残ることを確かめる。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy(GITIGNORE, repo / ".gitignore")
    for rel in MUST_BE_IGNORED + MUST_BE_TRACKED:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(tmp_path)}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)

    def ignored(rel: str) -> bool:
        r = subprocess.run(
            ["git", "check-ignore", "-q", rel], cwd=repo, env=env, capture_output=True
        )
        return r.returncode == 0

    for rel in MUST_BE_IGNORED:
        assert ignored(rel), f"git に載ってしまう: {rel}"
    for rel in MUST_BE_TRACKED:
        assert not ignored(rel), f"公開物が落ちる: {rel}"


def _looks_binary(path: Path) -> bool:
    """`grep -I` と同じ判定（先頭に NUL があればバイナリ）。読めなければバイナリ扱い。"""
    try:
        return bytes((0,)) in path.open("rb").read(8192)
    except OSError:
        return True


def _tracked_candidates() -> list[Path]:
    out: list[Path] = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            p = Path(root) / f
            if p.suffix.lower() in {".db", ".pyc", ".png", ".jpg"}:
                continue
            if _looks_binary(p):
                # `.githooks/pre-commit` は `grep -I` でバイナリを飛ばす。試験も同じ判定に
                # 揃える——揃えないと、同梱の VRM（18MB）のような塊を毎回テキストとして
                # 読み、遅いうえに偶然の一致で誤検知する（2026-09-05 に同梱して気づいた）。
                continue
            out.append(p)
    return out


@pytest.mark.skipif(not os.environ.get("MANOR_LEAK_TERMS"), reason="MANOR_LEAK_TERMS が未設定")
def test_no_leak_terms_in_tracked_files() -> None:
    """語彙リスト（1行1語・# はコメント・大文字小文字無視）が追跡候補に無いこと。"""
    terms_path = Path(os.environ["MANOR_LEAK_TERMS"])
    raw = terms_path.read_text(encoding="utf-8-sig")
    terms = [t.strip() for t in raw.splitlines() if t.strip() and not t.startswith("#")]
    hits: list[str] = []
    for p in _tracked_candidates():
        try:
            text = p.read_text(encoding="utf-8").lower()
        except UnicodeDecodeError:
            continue
        for t in terms:
            if t.lower() in text:
                hits.append(f"{p.relative_to(REPO)}: {t!r}")
    assert not hits, "個人情報の疑い:\n" + "\n".join(hits)


def test_extension_secrets_dir_default_is_outside_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-009 D4: 拡張の秘密（`~/.manor/secrets/<id>.json`）はリポジトリの外に置く。

    `home_path`/`home` フィクスチャは試験全体で `MANOR_SECRETS_DIR` を一時ディレクトリへ
    向けてしまう（本物の `~/.manor/secrets/` を試験が書き換えないため）ので、ここだけ明示的に
    環境変数を外し、**既定の置き場**が `_tracked_candidates()`（この試験ファイルが git 追跡候補として
    走査する範囲）に絶対に入らないことを機械で確かめる（ADR-009 §5「~/.manor/secrets/ を
    追跡候補に含めない」）。
    """
    from manor import secrets as secrets_mod

    monkeypatch.delenv(secrets_mod.ENV_OVERRIDE, raising=False)
    default_dir = secrets_mod.secrets_dir().resolve()
    try:
        default_dir.relative_to(REPO)
    except ValueError:
        pass  # 期待どおり: リポジトリの外
    else:
        pytest.fail(f"拡張の秘密の既定の置き場がリポジトリの中です: {default_dir}")


def test_no_windows_user_paths_in_tracked_files() -> None:
    """`C:\\Users\\<誰か>` の形は ④ の印。①③ に混ざっていたら移植も公開も壊れる。"""
    import re

    pat = re.compile(r"[A-Za-z]:\\\\?Users\\\\?[A-Za-z0-9_]+", re.IGNORECASE)
    hits: list[str] = []
    for p in _tracked_candidates():
        if p.name == "test_privacy_boundary.py":
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for m in pat.finditer(text):
            # 雛形の `<ユーザー名>` 表記は許す
            if "<" in m.group(0):
                continue
            hits.append(f"{p.relative_to(REPO)}: {m.group(0)}")
    assert not hits, "ユーザー名入りのパス:\n" + "\n".join(hits)
