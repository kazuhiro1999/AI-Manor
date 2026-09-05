"""共通のユーティリティ: 時刻・パスの導出。

なぜ: 「パスは MANOR_HOME 以外を直書きしない」「時刻は util から取る」という
ADR-001 §12 の約束を1箇所に集約する。試験は MANOR_NOW / MANOR_TODAY で時刻を固定できる。
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime
from pathlib import Path


def repo_root() -> Path:
    """このパッケージが置かれているリポジトリのルート。

    `src/manor/util.py` から2つ上が `src/`、さらに1つ上がリポジトリルート。
    """
    return Path(__file__).resolve().parents[2]


def manor_home() -> Path:
    """`home/` の場所。既定は `<repo>/home`。環境変数 `MANOR_HOME` があればそちら。"""
    val = os.environ.get("MANOR_HOME", "").strip()
    if val:
        return Path(val)
    return repo_root() / "home"


def now() -> str:
    """いまの時刻（ISO 8601 ローカル、秒まで）。`MANOR_NOW` があれば固定できる（試験用）。"""
    override = os.environ.get("MANOR_NOW", "").strip()
    if override:
        return override
    return datetime.now().isoformat(timespec="seconds")


def today() -> str:
    """今日の日付（YYYY-MM-DD）。`MANOR_TODAY` があれば固定できる（試験用）。"""
    override = os.environ.get("MANOR_TODAY", "").strip()
    if override:
        return override
    return datetime.now().date().isoformat()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str | None:
    """ファイルの sha256。無ければ None（例外にしない。check.py の C7 が判定に使う）。"""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None
