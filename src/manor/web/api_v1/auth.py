"""`POST /api/v1/auth/login` `POST /api/v1/auth/logout` `GET /api/v1/auth/me`（ADR-005 §2 D4）。"""

from __future__ import annotations

from fastapi import FastAPI, Request, Response
from pydantic import BaseModel, Field

from .. import auth as auth_mod
from .._common import COOKIE_NAME, WebContext


class LoginRequest(BaseModel):
    passcode: str = Field(..., min_length=1)


def register(app: FastAPI, ctx: WebContext) -> None:
    @app.post("/api/v1/auth/login")
    def login(body: LoginRequest, response: Response) -> dict[str, object]:
        if ctx.auth_mode == "loopback":
            return {"ok": True, "mode": "loopback"}
        if not ctx.login_limiter.allow():
            from fastapi import HTTPException

            raise HTTPException(status_code=429, detail="試行が多すぎます。しばらくしてからお試しください")
        if not auth_mod.verify_passcode(ctx.home, body.passcode):
            from fastapi import HTTPException

            raise HTTPException(status_code=401, detail="passcode が違います")
        cookie_value = auth_mod.make_session_cookie(ctx.home)
        response.set_cookie(
            COOKIE_NAME, cookie_value, httponly=True, samesite="lax",
            max_age=auth_mod.SESSION_TTL_SECONDS,
        )
        return {"ok": True, "mode": "passcode"}

    @app.post("/api/v1/auth/logout")
    def logout(response: Response) -> dict[str, object]:
        response.delete_cookie(COOKIE_NAME)
        return {"ok": True}

    @app.get("/api/v1/auth/me")
    def me(request: Request) -> dict[str, object]:
        authenticated = bool(getattr(request.state, "authenticated", ctx.auth_mode == "loopback"))
        return {"authenticated": authenticated, "mode": ctx.auth_mode}
