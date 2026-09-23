"""`if __name__ == "__main__"` ガードより後ろに定義を置かない（2026-09-23）。

**踏んだ事故**: `src/manor/slack.py` のガードの後ろに `_find_existing_event` /
`extract_task` などが置かれていた。`python -m manor.slack`（定例の便が使う形）は
そのモジュールを `__main__` として実行するので、ガードが `main()` を呼んで
`SystemExit` する時点で**後ろの def はまだ評価されていない**。結果、Slack の
`#task` に日時が入っていると毎回 `NameError` で落ち、予定がカレンダーへ押し出されず、
取り込みの印も残らないので同じ予定が重複して増えた（2026-09-06〜09-23）。

`uv run manor slack ...`（CLI 経由＝モジュールを import してから呼ぶ）では再現せず、
試験も import して呼ぶので緑のままだった——**実際の起動の形でしか出ない穴**なので、
ここで静的に押さえる。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "manor"

#: 定義とみなすもの（定数の代入も含む——`INTAKE_EVENT_SOURCE` で実際に踏んだ）。
_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign)


def _modules_with_main_guard() -> list[tuple[Path, ast.Module, int]]:
    out: list[tuple[Path, ast.Module, int]] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.If) and ast.unparse(node.test).startswith("__name__ =="):
                out.append((path, tree, int(node.end_lineno or node.lineno)))
    return out


def test_at_least_one_module_has_a_main_guard() -> None:
    """この試験自体が空振りしていないことを確かめる（列挙の当てが外れたら気づけない）。"""
    assert _modules_with_main_guard(), "__main__ ガードを持つモジュールが1つも見つからない"


@pytest.mark.parametrize(
    ("path", "tree", "guard_end"),
    [pytest.param(p, t, g, id=str(p.name)) for p, t, g in _modules_with_main_guard()],
)
def test_nothing_is_defined_after_the_main_guard(path: Path, tree: ast.Module, guard_end: int) -> None:
    stranded = [
        f"{path.name}:{node.lineno} {ast.unparse(node).splitlines()[0][:60]}"
        for node in tree.body
        if node.lineno > guard_end and isinstance(node, _DEFINITIONS)
    ]
    assert not stranded, (
        "`if __name__ == \"__main__\"` の後ろに定義があります。"
        "`python -m` で動かすと読まれず NameError になります（ガードは末尾へ）:\n"
        + "\n".join(stranded)
    )
