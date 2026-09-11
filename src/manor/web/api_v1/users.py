"""`users`（ADR-014 D1・D3。「利用者」の一覧・追加・改名・畳む・切り替え）。

**認証ではない。** `GET /api/v1/users` は「誰の机を見るか」の選択肢を返すだけで、
秘密は持たない（利用者名・呼び名・記号だけ——`GET /api/v1/meta` の `users` と同じ形。
ADR-014 D1'）。
切り替え（`POST /api/v1/users/switch`）は cookie `manor_user` を置くだけなので、
`--read-only` でも通す（見る利用者を変えるのは DB への書き込みではない）。
"""

from __future__ import annotations

from fastapi import FastAPI, Response
from pydantic import BaseModel, Field

from ... import user as user_mod
from ...errors import ManorError
from .._common import (
    USER_COOKIE_MAX_AGE_SECONDS,
    USER_COOKIE_NAME,
    WebContext,
    commit_and_render,
    manor_error_to_http,
    open_conn,
    require_writable,
)


class UserAddRequest(BaseModel):
    name: str = Field(..., min_length=1)
    #: 省略すれば `u2`, `u3`… と機械が振る（ADR-014 §4「聞きすぎない」）。
    id: str | None = None
    #: 呼び名（ADR-014 D1'）。任意——省略・空文字は「利用者名で呼ぶ」。
    callname: str = ""


class UserSetRequest(BaseModel):
    #: `name`（利用者名）・`callname`（呼び名）はどちらも任意——渡された方だけ更新する
    #: （ADR-014 D1'）。
    name: str | None = None
    callname: str | None = None


class UserSwitchRequest(BaseModel):
    id: str = Field(..., min_length=1)


def register(app: FastAPI, ctx: WebContext) -> None:
    @app.get("/api/v1/users")
    def list_users_route() -> dict[str, object]:
        with open_conn(ctx) as conn:
            return {"items": user_mod.list_users(conn)}

    @app.post("/api/v1/users")
    def add_user(body: UserAddRequest) -> dict[str, object]:
        require_writable(ctx)
        with open_conn(ctx) as conn:
            try:
                user_id = user_mod.add(conn, body.name, user_id=body.id, callname=body.callname)
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return {"id": user_id}

    # ⚠ `/users/switch` は `/users/{user_id}` より**前に**登録する。両方とも POST の
    # 2段パスなので、後にすると "switch" が user_id として食われてしまう。
    @app.post("/api/v1/users/switch")
    def switch_user(body: UserSwitchRequest, response: Response) -> dict[str, object]:
        with open_conn(ctx) as conn:
            if not user_mod.exists_active(conn, body.id):
                # 段A（user.py）が既に持つ語彙（`error.user.unknown_or_archived`）を
                # そのまま使う——ここで新しい文言を増やさない。
                raise manor_error_to_http(
                    ManorError(
                        f"user が見つからない、または畳まれています: {body.id}",
                        code=2,
                        key="error.user.unknown_or_archived",
                        params={"user_id": body.id},
                    )
                )
            row = user_mod.get(conn, body.id)
        response.set_cookie(
            USER_COOKIE_NAME,
            body.id,
            httponly=True,
            samesite="lax",
            max_age=USER_COOKIE_MAX_AGE_SECONDS,
        )
        return {"user": {"id": row["id"], "name": row["name"], "role": row["role"]}}

    @app.post("/api/v1/users/{user_id}")
    def set_user(user_id: str, body: UserSetRequest) -> dict[str, object]:
        require_writable(ctx)
        with open_conn(ctx) as conn:
            try:
                result_id = user_mod.set(conn, user_id, name=body.name, callname=body.callname)
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return {"id": result_id}

    @app.post("/api/v1/users/{user_id}/archive")
    def archive_user(user_id: str) -> dict[str, object]:
        require_writable(ctx)
        with open_conn(ctx) as conn:
            try:
                result_id = user_mod.archive(conn, user_id)
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return {"id": result_id}
