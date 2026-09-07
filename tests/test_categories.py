"""カテゴリの印が全部の試験に付いていること（主人のご質問 2026-09-07）。

`pytest -m core` のように束で回せるよう、`tests/conftest.py` の `TEST_CATEGORIES` が
パスから印を付けている。**この表は列挙式**なので完全性に全部を頼っている——B174 で
「列挙は完全性に全依存」と学んだ形そのもの。新しい試験ファイルが表から漏れると
**黙って無印になる**ので、ここで鳴らす。
"""

from __future__ import annotations

from pathlib import Path

import pytest

# conftest.py は pytest が読み込む（ はパッケージではないので import はできない）。
# 同じ表を2つ持つと必ずずれるので、conftest そのものを読み込んで使う。
from conftest import TEST_CATEGORIES, category_for  # type: ignore[import-not-found]

#: `pyproject.toml` の `[tool.pytest.ini_options] markers` に登録済みの名前。
KNOWN = {name for _prefix, name in TEST_CATEGORIES}


def test_every_test_file_has_a_category() -> None:
    """`tests/` の下の試験ファイルが、1つ残らず表に当たること。"""
    root = Path(__file__).resolve().parent
    homeless = [
        str(p.relative_to(root.parent))
        for p in sorted(root.rglob("test_*.py"))
        if category_for(str(p)) is None
    ]
    assert homeless == [], (
        "カテゴリの表に当たらない試験ファイルがあります。"
        f"tests/conftest.py の TEST_CATEGORIES に足してください: {homeless}"
    )


def test_every_category_is_declared_in_pyproject() -> None:
    """表にある印が、`pyproject.toml` に説明つきで登録されていること。

    未登録の印は `PytestUnknownMarkWarning` になるだけで**試験は通ってしまう**ので、
    ここで見る（警告は流れて誰も読まない）。
    """
    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    missing = [name for name in sorted(KNOWN) if f'"{name}: ' not in text]
    assert missing == [], f"pyproject.toml の markers に説明がありません: {missing}"


@pytest.mark.parametrize("marker", sorted(KNOWN))
def test_each_category_actually_matches_something(marker: str, pytestconfig) -> None:
    """空のカテゴリを作らない——**名前だけあって中身が無い束**は、`-m` で回した人に
    「そこは試験が無い」ではなく「通った」と読ませてしまう。
    """
    root = Path(__file__).resolve().parents[1]
    hit = any(
        category_for(str(p)) == marker
        for p in (root / "tests").rglob("test_*.py")
    )
    assert hit, f"カテゴリ {marker!r} に当たる試験ファイルが1つもありません"
