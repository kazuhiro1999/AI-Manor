"""サイト別アダプタの登録簿（ADR-015 D7）。ホスト名 → アダプタモジュール。

1サイト1ファイル（`nadia.py`・`cookpad.py`・`kurashiru.py`・`sirogohan.py`・
`delishkitchen.py`）で足しやすくする。**アダプタは壊れる前提**——サイトの markup は
予告なく変わる（CSS Modules のハッシュ付きクラス名など）ので、呼び出し側
（`recipe_import.extract_auto`）は例外はもちろん、`None` を返された場合も
汎用抽出（`generic.py`）へ静かに落とす。ここでは対応表を持つだけで、抽出そのものの
判断はしない。

## アダプタの口（この3つ。`extract` だけが必須）

```python
def extract(html: str, url: str) -> dict | None
```
経路②（JSON-LD が無いページ）の本文抽出。`{"title","servings","total_minutes",
"ingredients","tools","raw_steps","hero_image","site_tags"}` を返す。材料・工程の
どちらかでも拾えなければ `None`（汎用へ落とす合図）。

```python
def extract_nutrition(html: str) -> dict[str, float]   # 任意
```
栄養価の**表示値**（1人分）を `{"kcal","protein_g","fat_g","carb_g","salt_g"}` の
取れた分だけ返す。JSON-LD で埋まらなかった鍵だけが使われる
（`recipe_import._merge_nutrition`）。

```python
def extract_hints(html, url, *, ld=None, source=None) -> dict   # 任意
```
**JSON-LD 経路でも効く補い**（2026-09-13 に追加）。`{"hero_image": str,
"site_tags": list[str], "step_images": list[str]}` の取れた分だけ返す。`ld` は
`recipe_import._normalize_recipe_ld` が均した JSON-LD（`site_tags` を含む）、
`source` は `extract_text()` の戻り（`og_image`・`images`・`title`）——どちらも
`None` があり得るので参照する側で守る。**なぜ要るか**は
`recipe_import._adapter_hints` の節に実測とともに書いてある。
"""

from __future__ import annotations

from types import ModuleType

from . import cookpad, delishkitchen, kurashiru, nadia, sirogohan

#: (ホスト名, method名, モジュール)。ホスト名は完全一致かサブドメイン一致で引く
#: （`www.example.com` は `example.com` のアダプタに当たる）。
_ADAPTERS: tuple[tuple[str, str, ModuleType], ...] = (
    ("oceans-nadia.com", "nadia", nadia),
    ("cookpad.com", "cookpad", cookpad),
    ("kurashiru.com", "kurashiru", kurashiru),
    ("sirogohan.com", "sirogohan", sirogohan),
    ("delishkitchen.tv", "delishkitchen", delishkitchen),
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
