"""サイト別アダプタの登録簿（ADR-015 D7）。ホスト名 → アダプタモジュール。

1サイト1ファイル（`nadia.py`・`cookpad.py`）で足しやすくする。**アダプタは壊れる前提**
——サイトの markup は予告なく変わる（CSS Modules のハッシュ付きクラス名など）ので、
呼び出し側（`recipe_import.extract_auto`）は例外はもちろん、`None` を返された場合も
汎用抽出（`generic.py`）へ静かに落とす。ここでは対応表を持つだけで、抽出そのものの
判断はしない。
"""

from __future__ import annotations

from types import ModuleType

from . import cookpad, nadia

#: (ホスト名, method名, モジュール)。ホスト名は完全一致かサブドメイン一致で引く
#: （`www.example.com` は `example.com` のアダプタに当たる）。
_ADAPTERS: tuple[tuple[str, str, ModuleType], ...] = (
    ("oceans-nadia.com", "nadia", nadia),
    ("cookpad.com", "cookpad", cookpad),
)


def match(host: str) -> tuple[str, ModuleType] | None:
    """`host` に対応するアダプタを `(名前, モジュール)` で返す。無ければ `None`。

    `名前` は `extract_auto` が組み立てる `method`（`f"adapter:{名前}"`）に使う。
    """
    h = (host or "").strip().lower()
    if not h:
        return None
    for domain, name, module in _ADAPTERS:
        if h == domain or h.endswith(f".{domain}"):
            return name, module
    return None
