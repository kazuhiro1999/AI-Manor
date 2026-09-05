"""web 内部の共通部品（`src/manor/board/_common.py` と同じ役割・同じ理由でここに集約する）。

**書き込みは必ず manor 側の API 関数（`task.py` / `decision.py` / `rule.py` / 各 staff の
`cli.py` の `cmd_*`）を呼ぶ。** ここでは SQL を書かない。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from pathlib import Path
from typing import Iterator

from fastapi import HTTPException

from .. import db as db_mod
from .. import render as render_mod
from ..errors import ManorError
from .auth import RateLimiter, auth_mode_for_host

COOKIE_NAME = "manor_session"


@dataclass
class WebContext:
    """web が丸ごと持ち回る設定。`app.state.web_ctx` に1つだけ置く（board の `BoardContext` に相当）。"""

    home: Path
    read_only: bool
    host: str = "127.0.0.1"
    auth_mode: str = "loopback"
    login_limiter: RateLimiter = field(default_factory=RateLimiter)

    def __post_init__(self) -> None:
        self.home = Path(self.home)


def make_context(home: Path, *, host: str = "127.0.0.1", read_only: bool = False) -> WebContext:
    from .auth import auth_mode as _auth_mode

    return WebContext(home=Path(home), read_only=read_only, host=host, auth_mode=_auth_mode(Path(home), host))


@contextmanager
def open_conn(ctx: WebContext) -> Iterator[sqlite3.Connection]:
    """リクエストごとに新しい接続を開く（理由は board `_common.open_conn` と同じ:
    `sqlite3.Connection` はスレッド間で共有しない約束・FastAPI はスレッドプールで動く）。
    """
    conn = db_mod.connect(ctx.home)
    if ctx.read_only:
        conn.execute("PRAGMA query_only = ON")
    try:
        yield conn
    finally:
        conn.close()


def require_writable(ctx: WebContext) -> None:
    """`--read-only` のとき書き込み系ハンドラの先頭で呼ぶ（403）。"""
    if ctx.read_only:
        raise HTTPException(status_code=403, detail="読み取り専用モードです（--read-only）")


def commit_and_render(conn: sqlite3.Connection, ctx: WebContext) -> None:
    conn.commit()
    render_mod.render(conn, ctx.home)


def manor_error_to_http(exc: ManorError, *, conflict_code: int = 400) -> HTTPException:
    """`ManorError` を HTTP へ写す（board `_common.manor_error_to_http` と同じ規則）。"""
    if exc.code == 2:
        return HTTPException(status_code=404, detail=exc.message_ja)
    return HTTPException(status_code=conflict_code, detail=exc.message_ja)


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
        is not None
    )


def ns(**kwargs: object) -> SimpleNamespace:
    """`(conn, home, args)` 形の CLI 関数へ渡す軽い args 代用（board `_common.ns` と同じ）。"""
    kwargs.setdefault("json", True)
    return SimpleNamespace(**kwargs)


#: 執事の初期モジュール一覧（ADR-004 D4・ADR-011 D1）。`table` があればその表の有無で
#: `enabled` を決める（部下が導入されていなければ無効。tasks/rules/imports/night/settings は
#: core なので常に有効）。**並びは主人の指定どおり**（ADR-011 D1）: ダッシュボード → 担当 →
#: タスク → 台所 → 家事 → 家計 → 秘書 → ルール → 取り込み → 夜勤 → 拡張機能。
#: 設定はサイドバーから外れ、右上の歯車アイコンから開く（`web/src/app/App.tsx`）ので、
#: order はここでは大きい値のまま残す（ナビには出ないが meta.modules 自体は消さない
#: ——設定画面の「モジュールの並び」節が引き続き meta.modules を表示に使うため）。
MODULE_DEFS: tuple[dict[str, object], ...] = (
    {"id": "dashboard", "title": "ダッシュボード", "icon": "🏠", "order": 1, "table": None},
    {"id": "agents", "title": "担当", "icon": "🧑‍🤝‍🧑", "order": 2, "table": None},
    {"id": "tasks", "title": "タスク", "icon": "📋", "order": 3, "table": None},
    {"id": "kitchen", "title": "台所", "icon": "🍳", "order": 4, "table": "chef_pantry"},
    {"id": "house", "title": "家事", "icon": "🧹", "order": 5, "table": "housekeeper_chore"},
    {"id": "money", "title": "家計", "icon": "💰", "order": 6, "table": "steward_expense"},
    {"id": "secretary", "title": "秘書", "icon": "🗓", "order": 7, "table": "secretary_reminder"},
    {"id": "rules", "title": "ルール", "icon": "📜", "order": 8, "table": None},
    {"id": "imports", "title": "取り込み", "icon": "📥", "order": 9, "table": None},
    {"id": "night", "title": "夜勤", "icon": "🌙", "order": 10, "table": None},
    {"id": "settings", "title": "設定", "icon": "⚙", "order": 90, "table": None},
    # ADR-009 D7: サイドバー最下部。order を大きく取り、将来コアのモジュールが増えても
    # 常に最後に来るようにする（拡張は core ではないので並びの終端が定位置）。
    {"id": "extensions", "title": "拡張機能", "icon": "🧩", "order": 100, "table": None},
)


def module_list(conn: sqlite3.Connection) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for m in MODULE_DEFS:
        table = m["table"]
        enabled = True if table is None else table_exists(conn, str(table))
        out.append({"id": m["id"], "title": m["title"], "icon": m["icon"], "order": m["order"], "enabled": enabled})
    return out
