"""外部フォーマットとの互換層。

`manor.compat.v1` に v1（AI執事 v1 / butler-board）のパーサのコピーを置く
（ADR-003 D1・D2）。読む側の関数だけを残し、書き戻し・副作用は持たない。
"""

from __future__ import annotations
