"""拡張機能の秘密の置き場（ADR-009 D4）。

`~/.manor/secrets/<id>.json`（`~/.manor/git-leak-terms.txt` と同じ置き場——**リポジトリの外・
`home/` の外**）。試験は `MANOR_SECRETS_DIR` 環境変数で向き先を差し替える（本物の `~/.manor/` に
触れない。`tests/conftest.py` の `home_path` と同じ流儀）。

**読み出しの口を作らない。** ここには `get()` があるが、これは送受信の実装（例:
`extensions/slack.py` がトークンを実際に使う場所）だけが呼ぶ想定で、**web の API 層・CLI の
`show`/`list` からは絶対に呼ばない**——公開してよいのは `has()` の真偽だけ（D4「API は
`has_<key>: bool` しか返さない」）。`manor ctx` にも射影にも出さない。
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

#: 向き先を差し替える環境変数（試験用。本物の `~/.manor/secrets/` に触れないため）。
ENV_OVERRIDE = "MANOR_SECRETS_DIR"


def secrets_dir() -> Path:
    """`~/.manor/secrets/`。`MANOR_SECRETS_DIR` があればそちら（試験・隔離用）。"""
    override = os.environ.get(ENV_OVERRIDE, "").strip()
    if override:
        return Path(override)
    return Path.home() / ".manor" / "secrets"


def _path(id_: str) -> Path:
    return secrets_dir() / f"{id_}.json"


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """一時ファイル + `os.replace`。書き込み後に POSIX では 0600 を試みる（D4）。
    Windows には POSIX パーミッションが無いので `os.chmod` は試さない
    （`os.name == "nt"` で分岐。失敗しても飲み込む——書き込み自体は成功させる）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp_name, path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        return
    if os.name != "nt":
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except OSError:
            pass


def _read_all(id_: str) -> dict[str, str]:
    """`<id>.json` の全体（`{key: value}`）。無い・壊れているときは空辞書。"""
    path = _path(id_)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}


def _write_all(id_: str, data: dict[str, str]) -> None:
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    _atomic_write_bytes(_path(id_), payload)


def has(id_: str, key: str) -> bool:
    """web の API・CLI の `show` が呼んでよいのはこれだけ（値そのものは返さない）。"""
    value = _read_all(id_).get(key)
    return bool(value)


def get(id_: str, key: str) -> str | None:
    """値そのもの。**送受信の実装だけが呼ぶ**（`manor ctx` にも API 応答にも出さない）。"""
    return _read_all(id_).get(key)


def set(id_: str, key: str, value: str) -> None:
    data = _read_all(id_)
    data[key] = value
    _write_all(id_, data)


def delete(id_: str, key: str | None = None) -> None:
    """`key` を省略すると `<id>.json` を丸ごと消す（`DELETE /extensions/<id>` の全消去用）。"""
    if key is None:
        try:
            _path(id_).unlink()
        except OSError:
            pass
        return
    data = _read_all(id_)
    if key in data:
        del data[key]
        _write_all(id_, data)
