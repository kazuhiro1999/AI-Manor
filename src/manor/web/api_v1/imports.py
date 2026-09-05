"""`imports`（外部データの取り込み。ADR-005 §2「新設」）。multipart: file, format, map, encoding。

正規化そのものは `staff/steward/importer.py`（純粋関数）。書き込みは `staff/steward/cli.py`
の `insert_import_rows`（`manor money import` と同じ関数）を呼ぶ。
"""

from __future__ import annotations

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from ...errors import ManorError
from .._common import WebContext, commit_and_render, manor_error_to_http, open_conn, require_writable, table_exists


def _decode(raw: bytes, encoding: str) -> str:
    try:
        return raw.decode(encoding)
    except (LookupError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"CSV を {encoding} として読めません: {exc}") from exc


def register(app: FastAPI, ctx: WebContext) -> None:
    @app.post("/api/v1/imports/money/preview")
    async def preview(
        file: UploadFile = File(...),
        format: str = Form("generic"),
        map: str | None = Form(None),
        encoding: str = Form("utf-8"),
    ) -> dict[str, object]:
        text = _decode(await file.read(), encoding)
        from ...staff.steward import cli as money_cli

        with open_conn(ctx) as conn:
            if not table_exists(conn, "steward_expense"):
                raise HTTPException(status_code=404, detail="家令（steward）が導入されていません")
            try:
                result = money_cli.preview_import(conn, text, fmt=format, map_spec=map)
            except ManorError as exc:
                raise manor_error_to_http(exc)
            return result.to_dict()

    @app.post("/api/v1/imports/money/commit")
    async def commit_import(
        file: UploadFile = File(...),
        format: str = Form("generic"),
        map: str | None = Form(None),
        encoding: str = Form("utf-8"),
    ) -> dict[str, object]:
        require_writable(ctx)
        text = _decode(await file.read(), encoding)
        from ...staff.steward import cli as money_cli

        with open_conn(ctx) as conn:
            if not table_exists(conn, "steward_expense"):
                raise HTTPException(status_code=404, detail="家令（steward）が導入されていません")
            try:
                result = money_cli.preview_import(conn, text, fmt=format, map_spec=map)
                inserted = money_cli.insert_import_rows(conn, result.rows)
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return {
                "inserted": inserted,
                "skipped": len(result.duplicates),
                "unreadable": len(result.unreadable),
                "total": result.total,
            }
