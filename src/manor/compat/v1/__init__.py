"""v1 butler-board からのコピー。読む側だけ。

出所: `apps/butler-board/src/butler_board/{mdtable,queue_doc,projects_doc}.py`
（AI執事 v1）。ADR-003 D1・D2 に従い、`parse_queue` / `parse_projects` と
それが依存する関数だけを残し、書き戻し（`apply_decision` 等）・バックアップ・
`os` / `shutil` を使う副作用のある関数は削ってある。manor はここへ書き戻さない。
"""

from __future__ import annotations
