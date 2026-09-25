"""Web の操作ログ（ADR-024）。**いつ・だれが・何をしたか**をテキストで残す。

置き場は `home/logs/web-YYYY-MM.log`（月ごと・1行1件・追記だけ）。DB には何も足さない。
見るのは運用側（`manor web log`）で、画面には出さない。

1行の形（空白区切り。先頭は時刻）:

    2026-09-25T17:12:03 user=master(主人) ip=#a3f91c zone=tailnet POST /api/v1/kitchen/recipes 201 120ms +chef_recipe ~chef_recipe_meta
    2026-09-25T17:40:00 user=u2(はなこ) ip=#77c2e0 zone=lan visit
    2026-09-25T17:12:45 bg food_resolve asked=2 resolved=2 cost=$0.0812 41s

- **だれ**: 画面で選んでいる人（ADR-014 D3。自己申告——本人確認はしない。主人の裁定）。
  端末鍵（ADR-017）で来たら `device=` も添える。
- **ip**: 生の IP は残さない。`home/web-secret` を鍵にした HMAC の先頭6桁（同じ端末なら同じ印、
  元の IP へは戻せない）。サーバ機そのもの（ループバック・転送なし）は `local`。
- **何を書き換えたか**: 要求のあいだに実行された INSERT／UPDATE／DELETE の**表の名前だけ**を
  SQLite の trace で拾う（`trace_writes`）。値は捨てる。`+` 追加・`~` 更新・`-` 削除。
- **残す要求**: GET 以外の全部。GET でも DB を書いた・背景の仕事を起こしたものは残す（一覧を開くと
  同期が走る、など）。ふだんの GET は残さない代わりに、同じ人・同じ端末が 30 分あいて来たら `visit`。
- 本文・クエリ文字列・会話の中身・秘密の値は**残さない**。
- 書けなくても本来の操作は止めない（ログの失敗は飲み込む）。12 か月より古い月のファイルは消す。
"""

from __future__ import annotations

import hashlib
import hmac
import re
import sqlite3
import threading
import time
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import util

if TYPE_CHECKING:  # pragma: no cover
    from fastapi import Request

LOG_DIR_NAME = "logs"
FILE_PREFIX = "web-"
KEEP_MONTHS = 12
VISIT_GAP_SECONDS = 30 * 60

#: 残さない要求（ペアリング中の端末が数秒おきに叩く）。
SKIP_PATHS: frozenset[str] = frozenset({"/api/v1/devices/pair/poll"})

_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_WRITE_RE = re.compile(
    r"^\s*(INSERT|REPLACE|UPDATE|DELETE)\b(?:\s+OR\s+\w+)?\s+(?:INTO\s+|FROM\s+)?[\"`\[]?(\w+)",
    re.IGNORECASE,
)
_OP_MARK = {"INSERT": "+", "REPLACE": "+", "UPDATE": "~", "DELETE": "-"}
_FILE_RE = re.compile(r"^web-(\d{4}-\d{2})\.log$")

#: いまの要求で拾ったもの（書いた表・背景の仕事）。要求の外では `None`。
_collector: ContextVar[list[str] | None] = ContextVar("manor_oplog_collector", default=None)


# --- 拾う ---------------------------------------------------------------------------


def _trace(statement: str) -> None:
    items = _collector.get()
    if items is None:
        return
    m = _WRITE_RE.match(statement)
    if m is None:
        return
    mark = _OP_MARK[m.group(1).upper()] + m.group(2)
    if mark not in items:
        items.append(mark)


def trace_writes(conn: sqlite3.Connection) -> None:
    """接続に trace を付ける（`_common.open_conn`）。要求の外で実行された文は拾わない。"""
    conn.set_trace_callback(_trace)


def note(text: str) -> None:
    """いまの要求の行に一言添える（背景の仕事を起こした、など）。要求の外なら何もしない。"""
    items = _collector.get()
    if items is not None and text not in items:
        items.append(text)


def begin() -> tuple[list[str], Any]:
    items: list[str] = []
    return items, _collector.set(items)


def end(token: Any) -> None:
    _collector.reset(token)


# --- 書く ---------------------------------------------------------------------------


def log_dir(home: Path) -> Path:
    return Path(home) / LOG_DIR_NAME


def log_path(home: Path, month: str) -> Path:
    return log_dir(home) / f"{FILE_PREFIX}{month}.log"


def _month_index(month: str) -> int:
    y, m = month.split("-")
    return int(y) * 12 + int(m) - 1


def prune(home: Path, month: str, *, keep: int = KEEP_MONTHS) -> list[Path]:
    """`month` を含めて `keep` か月より古いファイルを消す。消したものを返す。"""
    removed: list[Path] = []
    folder = log_dir(home)
    if not folder.is_dir():
        return removed
    oldest = _month_index(month) - keep + 1
    for path in folder.iterdir():
        m = _FILE_RE.match(path.name)
        if m and _month_index(m.group(1)) < oldest:
            try:
                path.unlink()
                removed.append(path)
            except OSError:
                pass
    return removed


class OpLog:
    """1つの home の操作ログ。スレッドから同時に呼ばれてよい。"""

    def __init__(self, home: Path) -> None:
        self.home = Path(home)
        self._lock = threading.Lock()
        self._month = ""
        self._key: bytes | None = None
        self._last_seen: dict[tuple[str, str], float] = {}

    def write(self, text: str, *, at: str | None = None) -> None:
        stamp = at or util.now()
        month = stamp[:7]
        line = f"{stamp} {text}".replace("\r", " ").replace("\n", " ") + "\n"
        try:
            with self._lock:
                if month != self._month:
                    self._month = month
                    prune(self.home, month)
                path = log_path(self.home, month)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as f:
                    f.write(line)
        except Exception:  # noqa: BLE001 - ログが書けなくても操作は止めない
            pass

    # --- 送信元 ---

    def ip_mark(self, request: "Request") -> str:
        from . import net as net_mod

        client = request.client.host if request.client is not None else ""
        forwarded = request.headers.get("x-forwarded-for") or ""
        ip = client
        if net_mod.classify_host(client, forwarded) == net_mod.ZONE_TAILNET and forwarded:
            ip = forwarded.split(",")[0].strip()
        elif net_mod.classify_host(client) == net_mod.ZONE_LOOPBACK:
            return "local"
        if self._key is None:
            from . import auth as auth_mod

            self._key = auth_mod.ensure_secret(self.home)
        digest = hmac.new(self._key, b"oplog-ip\0" + ip.encode("utf-8"), hashlib.sha256).hexdigest()
        return "#" + digest[:6]

    # --- 来訪 ---

    def is_visit(self, who_key: str, ip: str, *, now: float | None = None) -> bool:
        """同じ人・同じ端末から 30 分あいて来た最初の要求か（覚えるのはこのプロセスの間だけ）。"""
        t = time.time() if now is None else now
        key = (who_key, ip)
        with self._lock:
            last = self._last_seen.get(key)
            self._last_seen[key] = t
        return last is None or t - last >= VISIT_GAP_SECONDS


_logs: dict[str, OpLog] = {}
_logs_lock = threading.Lock()


def for_home(home: Path) -> OpLog:
    key = str(Path(home).resolve())
    with _logs_lock:
        log = _logs.get(key)
        if log is None:
            log = OpLog(Path(home))
            _logs[key] = log
        return log


def write(home: Path, text: str) -> None:
    """背景の仕事などが1行書く（`bg …`）。"""
    for_home(home).write(text)


# --- 要求の1行 --------------------------------------------------------------------


def _who(request: "Request", home: Path) -> str:
    """`user=<id>(<呼び名>)`（端末鍵なら `device=<id>(<名前>)` も）。DB が無ければ `user=?`。"""
    from . import _common
    from .. import db as db_mod
    from .. import user as user_mod

    parts: list[str] = []
    device = getattr(request.state, "device", None)
    if isinstance(device, dict):
        parts.append(f"device={device.get('id')}({device.get('name') or ''})")
    if not (Path(home) / "manor.db").is_file():
        return " ".join(parts + ["user=?"])
    try:
        conn = db_mod.connect(Path(home))
        try:
            uid = _common.viewing_user_id(request, conn)
            try:
                name = user_mod.display_callname(user_mod.get(conn, uid))
            except Exception:  # noqa: BLE001
                name = ""
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return " ".join(parts + ["user=?"])
    return " ".join(parts + [f"user={uid}({name})"])


def _who_key(request: "Request") -> str:
    """来訪の判定に使う「だれ」（DB を引かない）。端末鍵なら端末、無ければ cookie の利用者。"""
    from ._common import USER_COOKIE_NAME

    device = getattr(request.state, "device", None)
    if isinstance(device, dict):
        return f"device:{device.get('id')}"
    return "user:" + (request.cookies.get(USER_COOKIE_NAME) or "")


def record(request: "Request", home: Path, status: int, elapsed_ms: int, items: list[str]) -> None:
    """要求が終わったあとに呼ぶ（`app._OpLogMiddleware`）。残すものだけ書く。"""
    path = request.url.path
    if not path.startswith("/api/v1/") or path in SKIP_PATHS:
        return
    log = for_home(home)
    try:
        ip = log.ip_mark(request)
    except Exception:  # noqa: BLE001
        ip = "?"
    visit = bool(getattr(request.state, "authenticated", False)) and log.is_visit(_who_key(request), ip)
    act = request.method.upper() not in _READ_METHODS or bool(items)
    if not (visit or act):
        return
    zone = getattr(request.state, "source_zone", "") or "?"
    head = f"{_who(request, home)} ip={ip} zone={zone}"
    if visit:
        log.write(f"{head} visit")
    if act:
        tail = (" " + " ".join(items)) if items else ""
        log.write(f"{head} {request.method.upper()} {path} {status} {elapsed_ms}ms{tail}")


# --- 読む（`manor web log`） --------------------------------------------------------


def months(home: Path) -> list[str]:
    folder = log_dir(home)
    if not folder.is_dir():
        return []
    return sorted(m.group(1) for p in folder.iterdir() if (m := _FILE_RE.match(p.name)))


def read_lines(
    home: Path,
    *,
    month: str | None = None,
    user: str | None = None,
    grep: str | None = None,
    tail: int | None = 50,
) -> list[str]:
    """ある月（既定は今月）の行。`user` は `user=<id>(` で、`grep` は部分一致で絞る。"""
    target = month or util.now()[:7]
    path = log_path(home, target)
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    if user:
        needle = f"user={user}("
        lines = [ln for ln in lines if needle in ln]
    if grep:
        lines = [ln for ln in lines if grep in ln]
    if tail is not None and tail > 0:
        lines = lines[-tail:]
    return lines
