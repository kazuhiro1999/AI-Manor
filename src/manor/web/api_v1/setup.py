"""`setup`（初回セットアップ。ADR-007 D4）。CLI（`manor setup ...` `manor profile ...`）と
同じ `profile.py` を呼ぶ。
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel, Field

from ... import profile as profile_mod
from ...errors import ManorError
from ...staff.steward.importer import PRESET_MAPS
from .._common import WebContext, commit_and_render, manor_error_to_http, open_conn, require_writable
from .meta import _task_classes


def _money_apps() -> list[dict[str, object]]:
    """D9: `GET /api/v1/setup` の `money_apps`。先頭に「使っていない」、続けて
    `steward/importer.py` の `PRESET_MAPS` の id（ラベルを持たないので id をそのまま表示に使う）。
    """
    apps: list[dict[str, object]] = [{"id": "none", "label": "使っていない"}]
    for app_id, mapping in PRESET_MAPS.items():
        apps.append({"id": app_id, "label": mapping.get("label") or _MONEY_APP_LABELS.get(app_id, app_id)})
    return apps


#: 画面に出す家計簿アプリの表示名（`PRESET_MAPS` はラベルを持たないのでここで補う。無い id は id のまま）。
_MONEY_APP_LABELS: dict[str, str] = {"zaim": "Zaim", "moneyforward": "マネーフォワード ME"}


def _setup_task_classes() -> list[dict[str, object]]:
    """D4: `task_classes` は meta と同じ生成だが、`fixed` かつ `HG` のクラスは除く
    （外部送信・課金は最初の登録に出さない。ADR-007 D2）。
    """
    return [c for c in _task_classes() if not (c["fixed"] and c["default_level"] == "HG")]


class SetupProjectInput(BaseModel):
    """必須項目（`code`/`name`）も `str | None` にしておく——欠けを pydantic の 422 ではなく
    `profile.apply_setup` の項目名つき `ManorError`（→ 400）で拒否する（ADR-007 D4）。
    """

    code: str | None = None
    name: str | None = None
    due: str | None = None
    preset: str | None = None


class SetupTaskInput(BaseModel):
    title: str | None = None
    project_code: str | None = None
    #: ADR-010 D1: 後方互換のためフィールドは残すが、セットアップの画面はもう聞かない。
    #: 省略時は `profile.apply_setup` が既定 `general`（L2）で埋める。
    cls: str | None = None
    #: ADR-010 D2「タスクの種類」。任意——省略・空文字ならタグ無しで起票する。
    kind: str | None = None
    due: str | None = None


class SetupKitchenInput(BaseModel):
    """ADR-007 §6 D9: 台所の前提（`kitchen` 用途を選んだときだけ出る段）。"""

    household_size: int | None = None
    allergies: str | None = None
    dislikes: str | None = None


class SetupMoneyInput(BaseModel):
    """ADR-007 §6 D9: 家計の前提（`money` 用途を選んだときだけ出る段）。"""

    app: str | None = None
    currency: str | None = None


class SetupRequest(BaseModel):
    callname: str | None = None
    butler_name: str | None = None
    purposes: list[str] = Field(default_factory=list)
    note: str | None = None
    projects: list[SetupProjectInput] = Field(default_factory=list)
    tasks: list[SetupTaskInput] = Field(default_factory=list)
    kitchen: SetupKitchenInput | None = None
    money: SetupMoneyInput | None = None


class ProfileUpdateRequest(BaseModel):
    callname: str | None = None
    butler_name: str | None = None
    purposes: list[str] | None = None
    note: str | None = None


def register(app: FastAPI, ctx: WebContext) -> None:
    @app.get("/api/v1/setup")
    def get_setup() -> dict[str, object]:
        with open_conn(ctx) as conn:
            st = profile_mod.status(conn)
            return {
                "done": st["done"],
                "completed_at": st["completed_at"],
                "profile": st["profile"],
                "purposes": [{"id": k, "label": v} for k, v in profile_mod.PURPOSES.items()],
                "presets": [{"id": k, "label": v} for k, v in profile_mod.PRESETS.items()],
                "task_classes": _setup_task_classes(),
                "money_apps": _money_apps(),
            }

    @app.post("/api/v1/setup")
    def post_setup(body: SetupRequest) -> dict[str, object]:
        require_writable(ctx)
        answers = body.model_dump()
        with open_conn(ctx) as conn:
            try:
                result = profile_mod.apply_setup(conn, answers)
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result

    @app.put("/api/v1/setup/profile")
    def put_setup_profile(body: ProfileUpdateRequest) -> dict[str, object]:
        require_writable(ctx)
        values: dict[str, object] = {}
        if body.callname is not None:
            values["master.callname"] = body.callname
        if body.butler_name is not None:
            values["butler.callname"] = body.butler_name
        if body.purposes is not None:
            values["purposes"] = body.purposes
        if body.note is not None:
            values["purposes.note"] = body.note
        with open_conn(ctx) as conn:
            try:
                profile_mod.set_many(conn, values)
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return profile_mod.get_all(conn)
