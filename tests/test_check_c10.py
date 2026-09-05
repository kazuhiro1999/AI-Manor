"""check.py の C10 — 追記ファイルの肥大化（ADR-006 §4 D14）。

`CHANGELOG.md` / `butler/GROWTH.md` は①層でリポジトリ直下にあり `home` の外なので、
`extra_paths` で本物のリポジトリファイルの代わりに合成ファイルを渡す（本物を太らせない）。
"""

from __future__ import annotations

from pathlib import Path

from manor import check as check_mod
from manor import render as render_mod
from manor import task as task_mod

OVER = 40 * 1024 + 1
UNDER = 40 * 1024 - 1


def test_c10_fires_over_40kb(tmp_path: Path, home: Path):
    changelog = tmp_path / "CHANGELOG.md"
    growth = tmp_path / "GROWTH.md"
    changelog.write_text("x" * OVER, encoding="utf-8")
    growth.write_text("y" * 100, encoding="utf-8")
    (home / "LOG.md").write_text("z" * OVER, encoding="utf-8")

    results = check_mod.check_c10(
        home, extra_paths={"CHANGELOG.md": changelog, "butler/GROWTH.md": growth}
    )
    flagged = {item["path"]: item["bytes"] for item in results}

    assert "CHANGELOG.md" in flagged
    assert flagged["CHANGELOG.md"] == OVER
    assert "home/LOG.md" in flagged
    assert "butler/GROWTH.md" not in flagged


def test_c10_silent_under_40kb(tmp_path: Path, home: Path):
    changelog = tmp_path / "CHANGELOG.md"
    growth = tmp_path / "GROWTH.md"
    changelog.write_text("x" * UNDER, encoding="utf-8")
    growth.write_text("y" * UNDER, encoding="utf-8")
    (home / "LOG.md").write_text("z" * UNDER, encoding="utf-8")

    results = check_mod.check_c10(
        home, extra_paths={"CHANGELOG.md": changelog, "butler/GROWTH.md": growth}
    )
    assert results == []


def test_c10_missing_files_are_silent(tmp_path: Path, home: Path):
    # ファイルが無い（stat が OSError）場合は誤検出にしない
    results = check_mod.check_c10(
        home,
        extra_paths={
            "CHANGELOG.md": tmp_path / "nope-1.md",
            "butler/GROWTH.md": tmp_path / "nope-2.md",
        },
    )
    assert results == []


def test_c10_does_not_affect_check_exit_verdict(conn, home: Path):
    task_mod.add(conn, "設計を書く")
    render_mod.render(conn, home)
    results = check_mod.run(conn, home)
    assert check_mod.ok(results)  # 本物の home/LOG.md は空・小さいので通常は鳴らない

    # 強制的に C10 を鳴らした状態にしても、判定（ok）は変わらない（警告扱い）
    results["C10"] = [{"path": "CHANGELOG.md", "bytes": 999_999}]
    assert check_mod.ok(results)


def test_c10_label_registered():
    assert "C10" in check_mod.CHECK_LABELS
    assert "C10" in check_mod.WARNING_ONLY_CHECKS
