"""FastAPI アプリ本体（ADR-005 §2・§4）。

`/api/v1/...` を D2 の構成で配線し、`web/dist`（フロント担当の成果物）があればそれを配る。
無ければ 500 にせず「`manor web build` を先に」と案内する。**board（`/api/...`・
`src/manor/board/**`）はそのまま別に動く**——この app はそれを壊さない・触らない。
"""

from __future__ import annotations

import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from .. import branding
from .. import util
from ..errors import ManorError
from . import auth as auth_mod
from . import net as net_mod
from ._common import COOKIE_NAME, WebContext, make_context, open_conn

#: フロントエンドのビルド成果物。**リポジトリ直下の `web/`**（`src/manor/web/` とは別物。
#: フロント担当のプロジェクト。ADR-004 D2）。
DIST_DIR = util.repo_root() / "web" / "dist"

#: `.py` の mtime を見る範囲。board の `_runtime_stale` と同じ判定（ADR-005 §2「/api/v1/health
#: の stale は board と同じ判定」）——`src/manor/` 全体（web 自身も含む）。
_SOURCE_ROOT = Path(__file__).resolve().parent.parent
_STARTED_AT = datetime.now()

_NO_CACHE_HEADERS = {"Cache-Control": "no-cache, must-revalidate"}

#: 認証なしで叩ける `/api/v1/...` の経路（ログイン前でも見えないと詰む）。
#: ADR-017 D2 のペアリングの口（`devices/pair/*`）もここに入る——端末はまだ鍵を持って
#: いないので認証できない。**主人が Web で「許可」しない限り何も起きない**口なので、
#: 認証なしで開けても増える権限が無い（速度制限は handler 側）。
_PUBLIC_API_PATHS = frozenset(
    {
        "/api/v1/meta",
        "/api/v1/health",
        "/api/v1/auth/login",
        "/api/v1/auth/me",
        *net_mod.PAIRING_PATHS,
    }
)


def runtime_stale() -> bool:
    """配信しているコードが、いま動いているプロセスより新しくないか（board と同じ理由・同じ判定）。"""
    try:
        latest = max(
            (p.stat().st_mtime for p in _SOURCE_ROOT.rglob("*.py") if "__pycache__" not in p.parts),
            default=0.0,
        )
    except OSError:
        latest = 0.0
    started = _STARTED_AT.timestamp()
    return bool(latest and latest > started + 60)


def _is_guarded_path(path: str) -> bool:
    """認証が要る経路か。`/api/v1/...`（`_PUBLIC_API_PATHS` を除く）と**姿の実体**（ADR-008 D2）。

    **守るのは `/face/model.vrm`（＝`home/face/*.vrm`。②の主人の資産）であって、`/face` の器では
    ない。** `tailscale serve` はループバックへ流すので `require_passcode` が唯一の門になり、姿を
    素通りさせると tailnet の誰でも取れてしまう。一方 `/face` 自体は担当の日本語名しか持たない
    ——ここまで止めると、cookie の切れた小窓が JSON のエラー塊になって主人の画面の隅に居座る。
    器は出し、姿が 401 なら輪郭と「ログインしてください」に落とす（`face.html`）。
    `/face-static/` は three.js 等の第三者の JS だけなので素通りでよい。
    """
    if path.startswith("/face/"):
        return True
    return path.startswith("/api/v1/") and path not in _PUBLIC_API_PATHS


def _authenticate_device(ctx: WebContext, token: str) -> dict | None:
    """端末鍵（ADR-017 D1）を照合し、通れば `last_seen_at` を間引いて更新する。

    **門の中で DB を触る**（この middleware は非同期だが sqlite は同期）——既存の
    `verify_session_cookie` も毎回 `home/web-secret` を読んでいるので、門が I/O を
    するのはこの app では初めてではない。pbkdf2 の重さは `device._TokenCache` が
    2度目以降を省く（`web/device.py` の docstring 参照）。
    """
    from . import device as device_mod

    with open_conn(ctx) as conn:
        row = device_mod.authenticate(conn, token)
        if row is not None and not ctx.read_only:
            if device_mod.touch_last_seen(conn, str(row["id"])):
                conn.commit()
    return row


class _AuthMiddleware(BaseHTTPMiddleware):
    """`/api/v1/...` と小窓（`/face`）を守る門。3つの軸を**この順で**見る。

    1. **端末鍵**（ADR-017 D1。`Authorization: Bearer`）。通れば `/api/v1/kitchen/*` と
       `/api/v1/devices/me` だけ（範囲外は 403）、合わない・失効は 401
       ——端末は 401 を見て「ペアリングし直し」を出す約束（ADR-017 D6）。
    2. **LAN の規則**（ADR-017 D4。`web/net.py` に表がある）。ループバックに待ち受けて
       いる間は効かない（`ctx.lan_rules`）。
    3. **cookie**（ADR-005 D4。従来どおり）。ループバックは全部通し、それ以外は cookie を
       検算して未認証なら 401（`_PUBLIC_API_PATHS` は素通り）。

    `--read-only` の書き込み拒否は別枠（`_common.require_writable`。認証と読み取り専用は
    別の軸）。
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        from . import device as device_mod

        ctx: WebContext = request.app.state.web_ctx
        path = request.url.path
        zone = net_mod.classify_source(request)
        request.state.source_zone = zone

        # 1. 端末鍵（Bearer）。cookie と**並んで**受ける（ADR-017 D1）。
        token = device_mod.bearer_token(request)
        if token is not None:
            device = _authenticate_device(ctx, token)
            if device is None:
                # 失効・知らない鍵は 401（403 ではない）。端末はこれを見て取り直す。
                return JSONResponse({"detail": "端末の鍵が無効です"}, status_code=401)
            request.state.device = device
            request.state.authenticated = True
            if not net_mod.in_device_scope(path):
                return JSONResponse(
                    {"detail": "この口は端末の鍵では使えません"}, status_code=403
                )
            return await call_next(request)

        # 2. LAN の規則（D4）。
        if ctx.lan_rules and zone == net_mod.ZONE_LAN:
            if not net_mod.lan_allows(path, lan_passcode_login=ctx.lan_passcode_login):
                return JSONResponse(
                    {"detail": "この口は家庭内 LAN からは使えません"}, status_code=403
                )

        # 3. cookie（従来どおり）。
        cookie = request.cookies.get(COOKIE_NAME)
        authenticated = ctx.auth_mode == "loopback" or auth_mod.verify_session_cookie(ctx.home, cookie)
        request.state.authenticated = authenticated

        if ctx.auth_mode != "loopback" and not authenticated and _is_guarded_path(path):
            return JSONResponse({"detail": "認証が必要です"}, status_code=401)
        return await call_next(request)


class _OpLogMiddleware(BaseHTTPMiddleware):
    """操作ログ（ADR-024。`web/oplog.py`）。**門（`_AuthMiddleware`）の内側**に置く——
    門が返した 401/403 は残らないが、端末鍵の `last_seen_at` の更新（門の中の書き込み）を
    利用者の操作と取り違えない。ログインの成否は `/auth/login` が門を通るので残る。
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        from . import oplog as oplog_mod

        if not request.url.path.startswith("/api/v1/"):
            return await call_next(request)
        ctx: WebContext = request.app.state.web_ctx
        items, token = oplog_mod.begin()
        started = time.monotonic()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            oplog_mod.end(token)
            elapsed = int((time.monotonic() - started) * 1000)
            try:
                oplog_mod.record(request, ctx.home, status, elapsed, items)
            except Exception:  # noqa: BLE001 - ログの失敗で応答を落とさない
                pass


def _looks_like_a_file(path: str) -> bool:
    """`/assets/index-abc123.js` のような**ファイルを狙った経路**か。

    画面の行き先はハッシュ（`#/tasks`）で持つので、経路の最後に `.` が来るのは
    ファイル以外にない。ここが真なら、無いときは 404 を返す（`spa_fallback` 参照）。
    """
    last = path.rsplit("/", 1)[-1]
    return "." in last


def _serve_index() -> FileResponse:
    return FileResponse(
        DIST_DIR / "index.html", media_type="text/html; charset=utf-8", headers=_NO_CACHE_HEADERS
    )


def _missing_dist_page() -> HTMLResponse:
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{branding.APP_NAME}</title></head>"
        "<body style=\"font-family:system-ui,sans-serif;max-width:640px;margin:4rem auto;"
        "line-height:1.6\">"
        "<h1>まだビルドされていません</h1>"
        "<p><code>manor web build</code> を先に実行してください（<code>web/</code> で "
        "<code>npm ci &amp;&amp; npm run build</code> を行い、<code>web/dist</code> を作ります）。</p>"
        "</body></html>"
    )
    return HTMLResponse(html, status_code=200, headers=_NO_CACHE_HEADERS)


def create_app(
    home: Path,
    *,
    host: str = "127.0.0.1",
    read_only: bool = False,
    port: int = 8789,
    discovery: bool | None = None,
) -> FastAPI:
    """FastAPI アプリを組み立てる。**ここでは起動時の認証チェック（D4 の拒否）はしない**
    ——試験が `host="0.0.0.0"` 相当で app を直接作り、認証の挙動（401→login→200）を
    見られるようにするため。拒否は `check_startup_auth`（`run_server` が呼ぶ）の役目。

    `port` は探索（ADR-017 D3）が返す `base_url` の port に使うだけ（待ち受けるのは
    uvicorn の仕事）。`discovery` は `None` で「設定に従う」、`False` で `--no-discovery`。
    """
    # 起動時に冪等な移行を当てる（`rule` 表や `import_hash` 列は後から足したもの。
    # 本番 home で `manor init` を回し忘れると `/api/v1/rules` が 500 になった——2026-09-03 実測）。
    from .. import db as db_mod

    if (Path(home) / "manor.db").is_file() and not read_only:
        db_mod.init(Path(home))

    # passcode を平文（config.toml）から秘密の置き場へ移す（2026-09-05）。**冪等**で、
    # 途中で落ちても平文は残る——移行の失敗で主人が締め出されないため
    # （`web/passcode.py` の `migrate()` 参照）。読み取り専用では触らない。
    if not read_only:
        from . import passcode as passcode_mod

        passcode_mod.migrate(Path(home))
    ctx = make_context(home, host=host, read_only=read_only)

    # ADR-017 D3: 探索（UDP 8791）は**アプリの寿命に合わせて**立てる（asyncio の
    # datagram endpoint なので、uvicorn が回すループの中でしか立てられない）。
    # ループバックの待ち受け・`[web] discovery = false`・`--no-discovery` では立てない
    # （`discovery.enabled_for`）。立て損ねても Web は止めない。
    from . import config as web_config
    from . import discovery as discovery_mod

    discovery_on = discovery_mod.enabled_for(
        host,
        config_value=web_config.get_web_section(Path(home)).get("discovery"),
        cli_disabled=discovery is False,
    )

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
        handle = await discovery_mod.start(port=port, host=host) if discovery_on else None
        _app.state.discovery = handle[1] if handle is not None else None
        try:
            yield
        finally:
            if handle is not None:
                handle[0].close()

    app = FastAPI(
        title="manor-web",
        description="manor の家庭用 Web アプリ（LLM API 不使用・外部送信なし）",
        lifespan=_lifespan,
    )
    app.state.web_ctx = ctx
    app.state.discovery = None
    # 後に足したものが外側（Starlette）。操作ログは門の内側（`_OpLogMiddleware` の docstring）。
    app.add_middleware(_OpLogMiddleware)
    app.add_middleware(_AuthMiddleware)

    from . import face as face_mod
    from .api_v1 import agents as api_agents
    from .api_v1 import auth as api_auth
    from .api_v1 import dashboard as api_dashboard
    from .api_v1 import devices as api_devices
    from .api_v1 import extensions as api_extensions
    from .api_v1 import face_models as api_face_models
    from .api_v1 import face_thumbnail as api_face_thumbnail
    from .api_v1 import face_talk as api_face_talk
    from .api_v1 import face_window as api_face_window
    from .api_v1 import house, ideas as api_ideas, imports as api_imports, kitchen, meta, money
    from .api_v1 import night as api_night
    from .api_v1 import receipts as api_receipts
    from .api_v1 import rules, runs as api_runs, secretary
    from .api_v1 import settings as api_settings
    from .api_v1 import setup as api_setup
    from .api_v1 import task_kinds as api_task_kinds
    from .api_v1 import tasks
    from .api_v1 import users as api_users

    meta.register(app, ctx)
    api_auth.register(app, ctx)
    api_dashboard.register(app, ctx)  # ADR-011 D2（総括ダッシュボード。読み取り専用）
    api_agents.register(app, ctx)  # ADR-011 D3（担当の一覧。読み取り専用）
    tasks.register(app, ctx)
    api_ideas.register(app, ctx)
    kitchen.register(app, ctx)
    house.register(app, ctx)
    money.register(app, ctx)
    api_receipts.register(app, ctx)  # ADR-020 D9（レシートの読み取り。背景の作業列）
    secretary.register(app, ctx)
    rules.register(app, ctx)
    api_task_kinds.register(app, ctx)  # ADR-010 D2（タスクの種類）
    api_imports.register(app, ctx)
    api_night.register(app, ctx)
    api_runs.register(app, ctx)  # ADR-006 §3 D11・§6 担当C（run 表の読み取り。稼働と費用）
    api_settings.register(app, ctx)
    api_setup.register(app, ctx)  # ADR-007 D4（初回セットアップ）
    api_extensions.register(app, ctx)  # ADR-009 D6（拡張機能）
    api_users.register(app, ctx)  # ADR-014 D1・D3（利用者の一覧・追加・改名・畳む・切り替え）
    api_devices.register(app, ctx)  # ADR-017 D1・D2（端末の鍵とペアリング）
    api_face_models.register(app, ctx)  # ADR-008 §7 D14（姿の出し入れ API。画面から差し替え・削除）
    # 一覧に VRM を読ませないための正面画像（2026-09-09 主人のご提案）
    api_face_thumbnail.register(app, ctx)
    api_face_talk.register(app, ctx)  # ADR-008 §8 D17〜D20（小窓の通話。claude -p を1回だけ呼ぶ経路）
    api_face_window.register(app, ctx)  # ADR-011 D5・D6・D7（開く・口の予定表・ピン留め）

    # ADR-008 D2: `/face` は SPA の1画面にしない独立ページ。**下の SPA キャッチオール
    # （`/{full_path:path}`）より前に登録する**——無いと `/face` も index.html へ落とされる。
    face_mod.register(app, ctx)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        """主人が用意した icon（2026-09-05）を配る。

        **この経路は SPA のキャッチオールより前に登録されている**ので、ここが 204 を
        返している限り `web/dist/favicon.ico` は永久に届かない——タブに絵が出ない
        理由がどこにも出ず、静かに失敗する形だった。ビルド前（dist が無い）や
        icon を置いていないときだけ、これまでどおり 204（404 のログを出さないため）。

        認証の内側に置かない: ログイン画面にも絵が要る（`_is_guarded_path` は
        `/api/v1/` と `/face/` だけを守る）。
        """
        icon = DIST_DIR / "favicon.ico"
        if icon.is_file():
            return FileResponse(icon, media_type="image/x-icon")
        return Response(status_code=204)

    has_dist = DIST_DIR.is_dir() and (DIST_DIR / "index.html").is_file()

    if has_dist:

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return _serve_index()

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa_fallback(full_path: str) -> Response:
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="not found")
            candidate = (DIST_DIR / full_path).resolve()
            try:
                candidate.relative_to(DIST_DIR.resolve())
            except ValueError:
                raise HTTPException(status_code=404, detail="not found") from None
            if candidate.is_file():
                return FileResponse(candidate, headers=_NO_CACHE_HEADERS)
            if _looks_like_a_file(full_path):
                # **無いファイルに index.html を返さない。** 返すと `<script src=...>` が
                # 200 の HTML を受け取り、ブラウザは型が違うとだけ判断して黙って止まる
                # ——画面は真っ白、原因はどこにも出ない（2026-09-05 実測。古い殻が
                # 指紋の変わった JS を指したときにこれが起きていた）。404 なら
                # 開発者ツールに理由が出る。
                raise HTTPException(status_code=404, detail="not found")
            return _serve_index()

    else:

        @app.get("/", include_in_schema=False)
        def index_missing() -> HTMLResponse:
            return _missing_dist_page()

    return app


def check_startup_auth(home: Path, host: str) -> None:
    """`--host` がループバック以外なのに passcode が無ければ起動を拒む（D4）。

    起動そのものを止めるのはここ（`run_server`／CLI 経由）だけの役目。`create_app` は
    拒まない（試験が非ループバック相当の app を直接作れるようにするため）。
    """
    from . import config as web_config

    if auth_mod.auth_mode(home, host) == "loopback":
        return
    if not web_config.has_passcode(home):
        raise ManorError(
            "ループバック以外へ待ち受ける（または [web] require_passcode = true）には"
            " home/config.toml の [web] passcode が必要です"
            "（`manor web serve` は既定の 127.0.0.1 のままなら passcode 無しで使えます）。"
        )


def run_server(
    *,
    home: Path,
    host: str = "127.0.0.1",
    port: int = 8789,
    read_only: bool = False,
    open_browser: bool = False,
    discovery: bool | None = None,
) -> None:
    """`manor web serve` の本体。呼ぶとブロックする（board `run_server` と同じ形）。

    `discovery=False` は `--no-discovery`（ADR-017 D3）。`None` なら `[web] discovery`
    （既定 true）に従う——どちらでも、ループバックの待ち受けでは立てない。
    """
    import uvicorn

    check_startup_auth(home, host)
    app = create_app(home, host=host, read_only=read_only, port=port, discovery=discovery)

    if open_browser:
        url = f"http://{host}:{port}/"

        def _open() -> None:
            time.sleep(1.0)
            try:
                webbrowser.open(url)
            except Exception:
                pass

        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run(app, host=host, port=port, log_level="info")
