"""chef（料理長）のメタ情報（ADR-001 §11・ADR-002 §3）。

`manor.cli` はこの `NAME` / `LABEL` を見て `manor init` の報告（「部下: chef」）などに使う。
"""

from __future__ import annotations

NAME = "chef"
LABEL = "料理長"
DESCRIPTION = "在庫・食事の記録・買い物リスト・好みを預かり、献立に要る材料を揃える"
