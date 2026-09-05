"""`GET /api/v1/meta` `GET /api/v1/health`（ADR-005 §2「共通」）。"""

from __future__ import annotations

from fastapi import FastAPI, Request

from ... import policy, profile as profile_mod, task_kind as task_kind_mod, util
from .. import config as web_config
from .._common import WebContext, module_list, open_conn
from ..app import _STARTED_AT, runtime_stale


def _task_classes() -> list[dict[str, object]]:
    """執事の裁定1: `meta.task_classes` を `policy.classes()` から起こす
    （`[{id, label, default_level, fixed}]`）。フロントの起票フォームはこれがあれば
    使い、無ければ固定一覧にフォールバックする（`web/src/modules/tasks/TaskForm.tsx`）。
    """
    out: list[dict[str, object]] = []
    for cls_id, entry in policy.classes().items():
        out.append(
            {
                "id": cls_id,
                "label": entry.get("label", cls_id),
                "default_level": entry.get("default"),
                "fixed": bool(entry.get("fixed", False)),
            }
        )
    return out


def register(app: FastAPI, ctx: WebContext) -> None:
    @app.get("/api/v1/meta")
    def meta(request: Request) -> dict[str, object]:
        with open_conn(ctx) as conn:
            modules = module_list(conn)
            setup_done = profile_mod.is_setup_done(conn)
            # ADR-010 D2: フォームが「もう1往復」せずに済むよう、meta にも並べて出す
            # （表示用途なのでアーカイブ済みは除く。`GET /api/v1/task-kinds` が全件・管理用）。
            task_kinds = task_kind_mod.list_kinds(conn, include_archived=False)
        authenticated = bool(getattr(request.state, "authenticated", ctx.auth_mode == "loopback"))
        return {
            "version": "0.1.0",
            "today": util.today(),
            "read_only": ctx.read_only,
            "stale": runtime_stale(),
            "auth": {"mode": ctx.auth_mode, "authenticated": authenticated},
            "modules": modules,
            "task_classes": _task_classes(),
            "task_kinds": task_kinds,  # ADR-010 D2（タスクの種類）
            "setup_done": setup_done,  # ADR-007 D4（フロントの /setup 誘導用）
            # home のフルパスは返さない（ADR-005 §2）。最後のフォルダ名だけ。
            "home_name": ctx.home.name,
            # ADR-012 §3 D11: `[manor] language`（auto/ja/en）。/meta は認証なしで読める
            # 唯一の経路なので、login・setup 画面もここから初期言語を得る
            # （GET/PUT /api/v1/settings は認証が要る——変更はログイン後の設定画面から）。
            "language": web_config.get_manor_language(ctx.home),
        }

    @app.get("/api/v1/health")
    def health() -> dict[str, object]:
        return {"ok": True, "started_at": _STARTED_AT.isoformat(timespec="seconds"), "stale": runtime_stale()}
