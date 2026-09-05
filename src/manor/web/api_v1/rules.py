"""`rules`（家庭のルール。ADR-005 §2「新設」）。CLI（`manor rule ...`）と同じ `rule.py` を呼ぶ。"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from ... import rule as rule_mod
from ...errors import ManorError
from .._common import WebContext, commit_and_render, manor_error_to_http, open_conn, require_writable


class RuleCreateRequest(BaseModel):
    title: str
    body: str = ""
    scope: str = "family"
    tags: str = ""
    effective_from: str | None = None
    effective_to: str | None = None


class RuleUpdateRequest(BaseModel):
    title: str | None = None
    body: str | None = None
    scope: str | None = None
    tags: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None


def register(app: FastAPI, ctx: WebContext) -> None:
    @app.get("/api/v1/rules")
    def list_rules(tag: str | None = None, all: bool = False) -> list[dict[str, object]]:
        with open_conn(ctx) as conn:
            return rule_mod.list_rules(conn, tag=tag, include_archived=all)

    @app.post("/api/v1/rules")
    def create_rule(body: RuleCreateRequest) -> dict[str, object]:
        require_writable(ctx)
        with open_conn(ctx) as conn:
            try:
                rule_id = rule_mod.add(
                    conn, body.title, body=body.body, scope=body.scope, tags=body.tags,
                    effective_from=body.effective_from, effective_to=body.effective_to,
                )
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return rule_mod.get(conn, rule_id)

    @app.put("/api/v1/rules/{rule_id}")
    def update_rule(rule_id: int, body: RuleUpdateRequest) -> dict[str, object]:
        require_writable(ctx)
        with open_conn(ctx) as conn:
            try:
                rule_mod.set(
                    conn, rule_id, title=body.title, body=body.body, scope=body.scope, tags=body.tags,
                    effective_from=body.effective_from, effective_to=body.effective_to,
                )
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return rule_mod.get(conn, rule_id)

    @app.delete("/api/v1/rules/{rule_id}")
    def delete_rule(rule_id: int) -> dict[str, object]:
        require_writable(ctx)
        with open_conn(ctx) as conn:
            try:
                result = rule_mod.archive(conn, rule_id)
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result
