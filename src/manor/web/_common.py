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

from fastapi import HTTPException, Request

from .. import db as db_mod
from .. import render as render_mod
from .. import user as user_mod
from ..errors import ManorError
from . import oplog as oplog_mod
from .auth import KeyedRateLimiter, RateLimiter, auth_mode_for_host, is_loopback

COOKIE_NAME = "manor_session"

#: ADR-014 D3:「見ている利用者」の cookie。**認証の cookie（`COOKIE_NAME`）とは別物**
#: ——署名しない。値は `user.id`。1年・httponly・samesite=lax。
USER_COOKIE_NAME = "manor_user"
USER_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365


@dataclass
class WebContext:
    """web が丸ごと持ち回る設定。`app.state.web_ctx` に1つだけ置く（board の `BoardContext` に相当）。"""

    home: Path
    read_only: bool
    host: str = "127.0.0.1"
    auth_mode: str = "loopback"
    login_limiter: RateLimiter = field(default_factory=RateLimiter)
    #: ADR-017 D4: `[web] lan_passcode_login`。**起動時に1回だけ読む**（`auth_mode` と
    #: 同じ約束——待ち受け方に関わる設定は、途中で変わると門の判断がぶれる）。
    lan_passcode_login: bool = False
    #: ADR-017 D2-4: `pair/start` の送信元ごとの速度制限（1分10回）。
    pair_limiter: KeyedRateLimiter = field(default_factory=KeyedRateLimiter)

    def __post_init__(self) -> None:
        self.home = Path(self.home)

    @property
    def lan_rules(self) -> bool:
        """LAN の規則（ADR-017 D4）を効かせるか。**ループバックに待ち受けている間は効かない**
        ——その口には LAN からそもそも届かないので、区分を分ける意味が無い。
        `tailscale serve` 経由（127.0.0.1 待ち受け＋`require_passcode`）の既存の使い方は、
        ここが `False` になることで丸ごと従来どおりに動く。
        """
        return not is_loopback(self.host)


def make_context(home: Path, *, host: str = "127.0.0.1", read_only: bool = False) -> WebContext:
    from . import config as web_config
    from .auth import auth_mode as _auth_mode

    return WebContext(
        home=Path(home),
        read_only=read_only,
        host=host,
        auth_mode=_auth_mode(Path(home), host),
        lan_passcode_login=web_config.get_web_section(Path(home)).get("lan_passcode_login") is True,
    )


@contextmanager
def open_conn(ctx: WebContext) -> Iterator[sqlite3.Connection]:
    """リクエストごとに新しい接続を開く（理由は board `_common.open_conn` と同じ:
    `sqlite3.Connection` はスレッド間で共有しない約束・FastAPI はスレッドプールで動く）。
    """
    conn = db_mod.connect(ctx.home)
    # ADR-024: 要求のあいだに書いた表の名前を操作ログへ（値は拾わない。`web/oplog.py`）。
    oplog_mod.trace_writes(conn)
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


def viewing_user_id(request: Request, conn: sqlite3.Connection) -> str:
    """「見ている利用者」（ADR-014 D3）。cookie `manor_user` の値が有効（存在し、畳んで
    いない）ならそれ、無い・知らない・畳んでいれば主人（`user.principal_id`）に落ちる。

    **端末鍵（ADR-017 D1）で来た問い合わせは端末の利用者になる**——cookie は見ない。
    XR は `manor_user` を持たない（持たせると「端末の持ち主」と「見ている人」が二重に
    なり、どちらが正かが場所によって変わる）。畳まれた利用者の端末でも**その利用者の
    ままにする**——主人へ落とすと、畳んだはずの端末が主人の机を覗くことになる。
    """
    device = getattr(request.state, "device", None)
    if isinstance(device, dict):
        device_user = str(device.get("user_id") or "").strip()
        if device_user:
            return device_user

    cookie = request.cookies.get(USER_COOKIE_NAME)
    if cookie and user_mod.exists_active(conn, cookie):
        return cookie
    return user_mod.principal_id(conn)


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
    # ADR-010 系「意見箱」（夜勤 N6・主人のご要望 2026-09-08）。tasks の次に置く。
    {"id": "ideas", "title": "意見箱", "icon": "💡", "order": 4, "table": None},
    {"id": "kitchen", "title": "台所", "icon": "🍳", "order": 5, "table": "chef_pantry"},
    {"id": "house", "title": "家事", "icon": "🧹", "order": 6, "table": "housekeeper_chore"},
    {"id": "money", "title": "家計", "icon": "💰", "order": 7, "table": "steward_expense"},
    {"id": "secretary", "title": "秘書", "icon": "🗓", "order": 8, "table": "secretary_reminder"},
    {"id": "rules", "title": "ルール", "icon": "📜", "order": 9, "table": None},
    {"id": "imports", "title": "取り込み", "icon": "📥", "order": 10, "table": None},
    {"id": "night", "title": "夜勤", "icon": "🌙", "order": 11, "table": None},
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
