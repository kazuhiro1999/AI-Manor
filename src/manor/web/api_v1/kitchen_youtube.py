"""YouTube の再生リストの同期を背景で回す作業係（ADR-023 D1）。

`kitchen_resolve.py` と同じ形: 1 本のスレッドが `youtube.sync()` を回し、走っている間の依頼は
「もう1周」の印にまとめる。レシピ帳の一覧（`GET /kitchen/videos`）を開いたとき、前の同期から
`[youtube].sync_hours` 以上たっていれば自動で頼む。環境変数 `MANOR_YOUTUBE_SYNC=off` で自動を止める
（試験は必ず止める——本物の API を呼ばない）。
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

from ... import db as db_mod
from ...staff.chef import youtube
from .. import oplog

ENV_SWITCH = "MANOR_YOUTUBE_SYNC"


def auto_enabled() -> bool:
    return os.environ.get(ENV_SWITCH, "").strip().lower() not in ("off", "0", "false", "no")


class SyncWorker:
    def __init__(self, home: Path) -> None:
        self.home = home
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._again = False
        self.status: dict[str, Any] = {"running": False, "last": None}

    def request(self) -> dict[str, Any]:
        oplog.note("bg:youtube_sync")  # ADR-024: 起こした要求の行に添える
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._again = True
            else:
                self._again = False
                self.status["running"] = True
                self._thread = threading.Thread(target=self._run, name="manor-youtube-sync", daemon=True)
                self._thread.start()
            return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {"running": bool(self.status["running"]), "last": self.status["last"]}

    def _run(self) -> None:
        while True:
            started = time.monotonic()
            conn = db_mod.connect(self.home)
            try:
                result: dict[str, Any] = youtube.sync(conn, self.home)
                conn.commit()
            except Exception as exc:  # noqa: BLE001 - スレッドを死なせない
                conn.rollback()
                result = {"units": 0, "playlists": [], "videos": 0, "failed": True, "reason": f"error: {type(exc).__name__}"}
            finally:
                conn.close()
            oplog.write(self.home, (
                f"bg youtube_sync videos={result.get('videos', 0)} units={result.get('units', 0)}"
                f" {int(time.monotonic() - started)}s" + (f" failed={result.get('reason')}" if result.get("failed") else "")
            ))
            with self._lock:
                self.status["last"] = result
                if not self._again:
                    self.status["running"] = False
                    return
                self._again = False


_workers: dict[str, SyncWorker] = {}
_workers_lock = threading.Lock()


def worker_for(home: Path) -> SyncWorker:
    key = str(Path(home).resolve())
    with _workers_lock:
        w = _workers.get(key)
        if w is None:
            w = SyncWorker(Path(home))
            _workers[key] = w
        return w
