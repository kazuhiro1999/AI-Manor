"""secretary（秘書）のメタ情報（ADR-001 §11・ADR-002 §6）。

`manor.cli` はこの `NAME` / `LABEL` を見て `manor init` の報告（「部下: secretary」）などに使う。
"""

from __future__ import annotations

NAME = "secretary"
LABEL = "秘書"
DESCRIPTION = "予定・控え・inbox の仕分けを預かる。予定の外部登録・外部送信はしない。"
