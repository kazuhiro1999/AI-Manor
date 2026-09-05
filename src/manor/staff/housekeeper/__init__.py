"""家政婦（housekeeper）のプラグイン・メタ情報（ADR-002 §4）。

預かる推論: いつ何を手入れするか（当番の周期・消耗品の残量・設備・ゴミの日）。
預からないもの: 修理の実行、業者の手配（提案まで）。
"""

from __future__ import annotations

NAME = "housekeeper"
LABEL = "家政婦"
DESCRIPTION = "当番の周期・消耗品の残量・設備の手入れ周期・ゴミの日を預かる。"
