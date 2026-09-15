"""`GET /api/v1/meta` `GET /api/v1/health`（ADR-005 §2「共通」）。"""

from __future__ import annotations

from fastapi import FastAPI, Request

from ... import policy, profile as profile_mod, task as task_mod, task_kind as task_kind_mod, user as user_mod, util
from .. import config as web_config
from .._common import WebContext, module_list, open_conn, viewing_user_id
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
            # ADR-014 D3: 「見ている利用者」と、切り替え先の選択肢（畳んでいないもの）。
            # meta は5秒おきに取っている経路への相乗りなので新しい通信は増えない。
            # 秘密は持たない（id・name・role だけ）。
            viewing_id = viewing_user_id(request, conn)
            users_rows = user_mod.list_users(conn)
            # ADR-014 D1'（追補）: 呼び名（callname）も併せて返す——利用者名（name）とは
            # 別の欄（画面は topbar の chip・切り替えメニューには name を使い続ける）。
            users_out = [
                {"id": u["id"], "name": u["name"], "callname": u["callname"], "role": u["role"]}
                for u in users_rows
            ]
            current_user = next((u for u in users_out if u["id"] == viewing_id), None)
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
            # T55（主人 2026-09-15「ボタン一つで進行よりドロップダウンで自分で設定したい」）:
            # 状態機械（ADR-001 §4）をそのまま返す。画面は「いまの状態から行ける先」だけを
            # 選択肢に出し、note が要る状態（waiting/withdrawn）では入力欄を出す。表を画面側に
            # 持たない——持つと状態機械を変えたときにずれる（B139「列挙は完全性に全依存」）。
            "task_transitions": {k: sorted(v) for k, v in task_mod.ALLOWED_TRANSITIONS.items()},
            "task_note_required": sorted(task_mod.NOTE_REQUIRED),
            "setup_done": setup_done,  # ADR-007 D4（フロントの /setup 誘導用）
            # home のフルパスは返さない（ADR-005 §2）。最後のフォルダ名だけ。
            "home_name": ctx.home.name,
            # ADR-012 §3 D11: `[manor] language`（auto/ja/en）。/meta は認証なしで読める
            # 唯一の経路なので、login・setup 画面もここから初期言語を得る
            # （GET/PUT /api/v1/settings は認証が要る——変更はログイン後の設定画面から）。
            "language": web_config.get_manor_language(ctx.home),
            # ADR-014 D3: 見ている利用者と、切り替え先の選択肢。
            "user": current_user,
            "users": users_out,
        }

    @app.get("/api/v1/health")
    def health() -> dict[str, object]:
        return {"ok": True, "started_at": _STARTED_AT.isoformat(timespec="seconds"), "stale": runtime_stale()}
