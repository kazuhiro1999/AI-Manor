"""`devices`（ADR-017 D1・D2。端末の鍵とペアリング）。

SQL は書かない——`web/device.py`（段A）の関数を呼び、`ManorError` を状態コードへ写す
だけ（`kitchen.py` と同じ流儀）。

## 口の一覧と、誰が叩くか

| 口 | 誰 | 認証 |
|---|---|---|
| `POST /api/v1/devices/pair/start` | 端末（XR） | 無し（LAN 可・速度制限つき） |
| `POST /api/v1/devices/pair/poll` | 端末（XR） | 無し（LAN 可） |
| `POST /api/v1/devices/pair/approve` | Web の画面・CLI | cookie（主人） |
| `GET /api/v1/devices` | Web の画面・CLI | cookie |
| `DELETE /api/v1/devices/{id}` | Web の画面・CLI | cookie |
| `GET /api/v1/devices/me` | 端末（XR） | 端末鍵（Bearer） |

**`/pair/*` が認証なしで開いているのは、端末がまだ鍵を持っていないから**である。
開いていても増える権限は無い——番号を積み上げられるだけで、主人が Web で「許可」しない
限り鍵は1つも出ない（ADR-017 D2-4）。速度制限（送信元ごとに1分10回）はその積み上げを
抑えるためだけのもの。
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from ... import user as user_mod
from ...errors import ManorError
from .. import device as device_mod
from .._common import (
    WebContext,
    manor_error_to_http,
    open_conn,
    require_writable,
)


class PairStartRequest(BaseModel):
    #: 端末が名乗る名前（「Quest 3」など）。空でも通す（`kind` か既定に落ちる）。
    name: str = ""
    kind: str = ""


class PairPollRequest(BaseModel):
    pair_id: str = Field(..., min_length=1)


class PairApproveRequest(BaseModel):
    code: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)


def _source_key(request: Request) -> str:
    """速度制限の鍵（送信元アドレス）。分からなければ1つの鍵に束ねる
    （偽れる値なので、分からない相手を全部まとめて制限する側に倒す）。
    """
    client = request.client
    return client.host if client is not None else "-"


def register(app: FastAPI, ctx: WebContext) -> None:
    # ⚠ `/devices/pair/...` と `/devices/me` は `/devices/{device_id}` より**前に**
    # 登録する（`users.py` の `/users/switch` と同じ理由。メソッドが違うので今は
    # 衝突しないが、後から GET を足したときに黙って食われるのを防ぐ）。

    @app.post("/api/v1/devices/pair/start")
    def pair_start(body: PairStartRequest, request: Request) -> dict[str, object]:
        """D2-1: 番号を発行する。**`--read-only` では断る**（DB に行を作るので）。"""
        require_writable(ctx)
        if not ctx.pair_limiter.allow(_source_key(request)):
            raise HTTPException(
                status_code=429, detail="ペアリングの試行が多すぎます。1分ほど待ってからお試しください"
            )
        with open_conn(ctx) as conn:
            result = device_mod.pair_start(conn, name=body.name, kind=body.kind)
            conn.commit()
        return result

    @app.post("/api/v1/devices/pair/poll")
    def pair_poll(body: PairPollRequest, request: Request) -> dict[str, object]:
        """D2-2: 結果を取りに来る。**鍵は一度しか返さない**（`device.pair_poll`）。

        `--read-only` でも `pending`/`expired` は返せるが、鍵の受け渡しは書き込みを
        伴う（渡した印を残す）ので断る——読み取り専用の manor で鍵を配ると、
        「渡した」ことが記録されないまま何度でも返してしまう。
        """
        require_writable(ctx)
        with open_conn(ctx) as conn:
            result = device_mod.pair_poll(conn, body.pair_id)
            conn.commit()
        return result

    @app.post("/api/v1/devices/pair/approve")
    def pair_approve(body: PairApproveRequest) -> dict[str, object]:
        """D2-3: 主人が番号を入れ、利用者を選んで「許可」。**鍵はここでは返さない。**"""
        require_writable(ctx)
        with open_conn(ctx) as conn:
            try:
                result = device_mod.pair_approve(conn, code=body.code, user_id=body.user_id)
            except ManorError as exc:
                conn.commit()  # 照合を外した回数は残す（`_count_attempt_and_drop`）
                raise manor_error_to_http(exc) from None
            conn.commit()
        return result

    @app.get("/api/v1/devices/me")
    def device_me(request: Request) -> dict[str, object]:
        """D1: 端末が「自分は誰として振る舞っているか」を確かめる口（Bearer 専用）。

        cookie で来たときは 403——「いまブラウザで見ている利用者」は
        `GET /api/v1/meta` の `user` が答える口で、ここは端末の鍵のための口である。
        """
        device = getattr(request.state, "device", None)
        if not isinstance(device, dict):
            raise HTTPException(status_code=403, detail="端末の鍵で叩いてください")
        with open_conn(ctx) as conn:
            try:
                row = user_mod.get(conn, str(device["user_id"])) if device.get("user_id") else None
            except ManorError:
                # 利用者が消えていても端末の身元は返す（`user` が null になるだけ）
                # ——端末側で「ペアリングし直し」の判断ができるように、500 にしない。
                row = None
        return {
            "device": {
                "id": device["id"],
                "name": device["name"],
                "kind": device["kind"],
                "user_id": device["user_id"],
                "created_at": device["created_at"],
                "last_seen_at": device["last_seen_at"],
            },
            "user": (
                {
                    "id": row["id"],
                    "name": row["name"],
                    "callname": row["callname"],
                    "role": row["role"],
                }
                if row is not None
                else None
            ),
        }

    @app.get("/api/v1/devices")
    def list_devices() -> dict[str, object]:
        with open_conn(ctx) as conn:
            return {"items": device_mod.list_devices(conn)}

    @app.delete("/api/v1/devices/{device_id}")
    def revoke_device(device_id: str) -> dict[str, object]:
        """失効（D5 の「失効」）。行は残し `revoked_at` を入れる——次の問い合わせから 401。"""
        require_writable(ctx)
        with open_conn(ctx) as conn:
            try:
                result = device_mod.revoke(conn, device_id)
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc) from None
            conn.commit()
        return {"id": result["id"], "revoked_at": result["revoked_at"]}
