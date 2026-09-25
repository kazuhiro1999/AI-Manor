"""名寄せ・換算の未解決を背景で Claude に調べてもらう作業係（ADR-022 D4）。

`receipts.py` の作業列と同じ考え方で、**1 本のスレッド**が `food_resolve.resolve()` を回す。
頼まれたときに走っていれば「もう1回」の印だけ立て、終わったらもう1周する——レシピを続けて
何本登録しても、Claude を呼ぶのは多くて2回（走っている回＋その後の1回）で済む。

自動で頼むのはレシピの登録・編集の直後（`[nutrition.claude_resolve].auto`）。環境変数
`MANOR_CLAUDE_RESOLVE=off` で自動を止められる（試験は必ず止める——本物の claude を呼ばない）。
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

from ... import db as db_mod
from ... import render as render_mod
from ...staff.chef import food_resolve
from .. import oplog

ENV_SWITCH = "MANOR_CLAUDE_RESOLVE"


def auto_enabled() -> bool:
    if os.environ.get(ENV_SWITCH, "").strip().lower() in ("off", "0", "false", "no"):
        return False
    try:
        return bool(food_resolve.load_settings()["auto"])
    except Exception:  # noqa: BLE001 - 設定が読めなくても登録は止めない
        return False


class ResolveWorker:
    def __init__(self, home: Path) -> None:
        self.home = home
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._again = False
        self.status: dict[str, Any] = {"running": False, "last": None}

    def request(self) -> dict[str, Any]:
        """調べてもらう。走っていれば「もう1回」の印だけ立てる。"""
        oplog.note("bg:food_resolve")  # ADR-024: 起こした要求の行に添える
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._again = True
            else:
                self._again = False
                self.status["running"] = True
                self._thread = threading.Thread(target=self._run, name="manor-food-resolve", daemon=True)
                self._thread.start()
            return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {"running": bool(self.status["running"]), "last": self.status["last"]}

    def _run(self) -> None:
        while True:
            started = time.monotonic()
            conn = db_mod.connect(self.home)
            try:
                result = food_resolve.resolve(conn)
                conn.commit()
                if result.get("resolved"):
                    try:
                        render_mod.render(conn, self.home)
                    except Exception:  # noqa: BLE001 - 射影の失敗で調べた結果を落とさない
                        pass
            except Exception as exc:  # noqa: BLE001 - スレッドを死なせない
                conn.rollback()
                result = {"asked": 0, "resolved": 0, "unresolved": 0, "failed": True, "reason": f"error: {exc}", "items": []}
            finally:
                conn.close()
            result["finished_at"] = food_resolve.util.now()
            if result.get("asked"):
                oplog.write(self.home, (
                    f"bg food_resolve asked={result['asked']} resolved={result.get('resolved', 0)}"
                    f" cost=${float(result.get('cost') or 0):.4f} {int(time.monotonic() - started)}s"
                    + (f" failed={result.get('reason')}" if result.get("failed") else "")
                ))
            with self._lock:
                self.status["last"] = result
                if not self._again:
                    self.status["running"] = False
                    return
                self._again = False


_workers: dict[str, ResolveWorker] = {}
_workers_lock = threading.Lock()


def worker_for(home: Path) -> ResolveWorker:
    key = str(Path(home).resolve())
    with _workers_lock:
        w = _workers.get(key)
        if w is None:
            w = ResolveWorker(Path(home))
            _workers[key] = w
        return w


def request_if_auto(home: Path) -> None:
    """レシピの登録・編集の後に呼ぶ。自動が切れていれば何もしない（未解決の有無は背景で見る）。"""
    if auto_enabled():
        worker_for(home).request()
