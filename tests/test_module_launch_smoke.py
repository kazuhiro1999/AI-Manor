"""無人で動く便を「その形のまま」動かす煙試験（T95）。

`tests/test_main_guard_is_last.py` は AST で**定義の並び**だけを見る静的な検査で、
実際に `python -m <module>` として起動はしない（`register()` 経由の import では
再現しなかった穴が `python -m manor.slack` の実起動でだけ出た——2026-09-06〜09-23、
`__main__` ガードの後ろに定義が置かれていて `NameError` で落ちていた）。

ここでは同じ列挙（`__main__` ガードを持つ全モジュール）を、実際に
`python -m <module> --help` として1回ずつ起動し、**exit 0 かつ Traceback 無し**まで見る。
「主要な便だけ手で挙げる」形（数え上げ）は挙げ漏れに気づけないので、
2026-09-24 の「すべての経路を見る」方針（顔の声の経路の検査と同じ）に倣い、
列挙は `test_main_guard_is_last.py` と共有する。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from test_main_guard_is_last import _modules_with_main_guard

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(SRC).with_suffix("").parts)
    if parts[-1] == "__main__":
        parts = parts[:-1]
    return ".".join(parts)


def _all_module_names() -> list[str]:
    # 同じファイルが複数の __main__ ガードを持つことは無い前提——集合にして重複だけ削る。
    return sorted({_module_name(p) for p, _tree, _guard_end in _modules_with_main_guard()})


@pytest.mark.parametrize("module", _all_module_names())
def test_module_launches_with_help_flag(module: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANOR_HOME", str(tmp_path / "home"))
    result = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",  # Windows の既定（cp932）は argparse の日本語ヘルプを読めない
        stdin=subprocess.DEVNULL,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"python -m {module} --help が exit {result.returncode} で終わりました:\n{result.stderr}"
    )
    assert "Traceback" not in result.stderr, f"python -m {module} --help が例外で落ちました:\n{result.stderr}"
