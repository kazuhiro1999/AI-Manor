"""`i18n.t(...)` の戻り値に `.format(...)` を掛けていないかを機械で検算する（2026-09-06）。

`t()` は**差し込みをキーワード引数で受け取る**作りで、足りなければ `I18nError` を投げる
（`src/manor/i18n/__init__.py` の「抜けを塞ぐ」3つめ）。ところが

    i18n.t("night.install.failed").format(returncode=1, detail="...")

と書くと、外側の `.format` が走る前に **`t()` の中で引数ゼロの `format` が先に走り**、
テンプレートの `{returncode}` が解決できずに `I18nError` で落ちる。**辞書も呼び出し側も
一見正しいのに、その行だけが例外になる**——2026-09-06 の検分で `manor night install` の
失敗経路に実在した（失敗を隠さないために足した経路が、そこだけ落ちる形だった）。

読んで気づける類の間違いではないので、文章の規則ではなく試験で塞ぐ。
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "manor"


def _is_t_call(node: ast.AST) -> bool:
    """`t(...)` または `i18n.t(...)`（別名の `_t` なども拾う）。"""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in {"t", "_t"}
    if isinstance(func, ast.Attribute):
        return func.attr in {"t", "_t"}
    return False


def test_no_format_on_the_result_of_i18n_t() -> None:
    offenders: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "format" and _is_t_call(func.value):
                rel = path.relative_to(SRC_ROOT.parent.parent)
                offenders.append(f"{rel}:{node.lineno}")

    assert not offenders, (
        "i18n.t(...) の戻り値に .format(...) を掛けています（差し込みは t() の"
        f" キーワード引数で渡してください）: {offenders}"
    )
