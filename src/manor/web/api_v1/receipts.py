"""`money/receipts`（レシートの読み取り。ADR-020 D9）。

読み取りは**背景の作業列**（1 本のスレッドが順に処理する。OCR は CPU で 30 秒かかることがあり、
同時に走らせると互いに遅くなる）。API は受付だけして即 id を返し、画面は polling で追う。
段取りそのもの（前処理・OCR・規則・検算・Claude・分類・登録）は `staff/steward/receipts.py`。
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ... import db as db_mod
from ... import render as render_mod
from ...staff.steward import receipt_classify, receipt_ocr, receipts
from .._common import WebContext, open_conn, require_writable, table_exists, viewing_user_id

MAX_UPLOAD_BYTES = 25 * 1024 * 1024


class _Worker:
    """背景の作業列。`enqueue(id)` → 1 本のスレッドが `receipts.process` を順に回す。"""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.queue: queue.Queue[int] = queue.Queue()
        self.pending: set[int] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def enqueue(self, receipt_id: int) -> None:
        from .. import oplog

        oplog.note(f"bg:receipt_read#{receipt_id}")  # ADR-024: 起こした要求の行に添える
        with self._lock:
            if receipt_id in self.pending:
                return
            self.pending.add(receipt_id)
            self.queue.put(receipt_id)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="manor-receipts", daemon=True)
                self._thread.start()

    def _run(self) -> None:
        while True:
            try:
                receipt_id = self.queue.get(timeout=60)
            except queue.Empty:
                return
            conn = db_mod.connect(self.home)
            try:
                receipts.process(conn, self.home, receipt_id)
                try:
                    render_mod.render(conn, self.home)
                except Exception:  # noqa: BLE001 - 射影の失敗で読み取りを落とさない
                    pass
            except Exception:  # noqa: BLE001 - スレッドを死なせない（行には failed が残る）
                try:
                    conn.execute("UPDATE steward_receipt SET status='failed', reason='error' WHERE id = ? AND status='reading'", (receipt_id,))
                    conn.commit()
                except Exception:  # noqa: BLE001
                    pass
            finally:
                conn.close()
                with self._lock:
                    self.pending.discard(receipt_id)


_workers: dict[str, _Worker] = {}
_workers_lock = threading.Lock()


def worker_for(home: Path) -> _Worker:
    key = str(Path(home).resolve())
    with _workers_lock:
        w = _workers.get(key)
        if w is None:
            w = _Worker(Path(home))
            _workers[key] = w
        return w


class ReceiptTax(BaseModel):
    rate: int
    amount: int


class ReceiptItemIn(BaseModel):
    line_no: int | None = None
    name: str = ""
    qty: int = 1
    unit_price: int | None = None
    amount: int | None = None
    tax_rate: int | None = None
    is_discount: bool = False
    category: str = ""
    subcategory: str = ""
    item_kind: str = ""


class ReceiptUpdate(BaseModel):
    store_name: str | None = None
    purchased_at: str | None = None
    total: int | None = None
    subtotal: int | None = None
    tax_mode: str | None = None
    payment_method: str | None = None
    taxes: list[ReceiptTax] | None = None
    items: list[ReceiptItemIn] | None = None
    learn_aliases: bool = False


def _require_steward(conn) -> None:  # type: ignore[no-untyped-def]
    if not table_exists(conn, "steward_receipt"):
        raise HTTPException(status_code=404, detail="家令（steward）が導入されていません")


def register(app: FastAPI, ctx: WebContext) -> None:
    @app.get("/api/v1/money/categories")
    def categories() -> dict[str, object]:
        cats = receipt_classify.categories()
        return {
            "categories": [{"name": k, "subcategories": v} for k, v in cats.items()],
            "item_kinds": receipt_classify.item_kinds(),
        }

    @app.get("/api/v1/money/receipts")
    def list_receipts(status: str | None = None, limit: int = 50) -> dict[str, object]:
        with open_conn(ctx) as conn:
            _require_steward(conn)
            st = receipt_ocr.status()
            cfg = receipts.settings(ctx.home)
            return {
                "items": receipts.list_rows(conn, status=status, limit=limit),
                "ocr": {"available": st["available"], "device": receipt_ocr.resolve_device(str(cfg["ocr_device"])) if st["available"] else None, "model": st["model"]},
                "today": {"count": receipts.today_count(conn), "limit": int(cfg["daily_limit"])},
            }

    @app.post("/api/v1/money/receipts")
    async def upload(request: Request, file: UploadFile = File(...), force: str | None = Form(None)) -> dict[str, object]:
        require_writable(ctx)
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="画像が空です")
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="画像が大きすぎます（上限 25MB）")
        is_png = data[:8] == b"\x89PNG\r\n\x1a\n"
        is_jpeg = data[:3] == b"\xff\xd8\xff"
        if not (is_png or is_jpeg):
            raise HTTPException(status_code=400, detail="JPEG か PNG の画像を送ってください")
        with open_conn(ctx) as conn:
            _require_steward(conn)
            cfg = receipts.settings(ctx.home)
            if receipts.today_count(conn) >= int(cfg["daily_limit"]):
                raise HTTPException(status_code=429, detail="今日の上限に達しました")
            quick = receipts.quick_check(data, ctx.home)
            if not quick["ok"] and not (force or "").strip():
                return {"id": None, "status": "rejected", "quick": quick}
            rel = receipts.store_image(ctx.home, data, ext="png" if is_png else "jpg")
            user = viewing_user_id(request, conn)
            rid = receipts.create(conn, rel, created_by=user)
            conn.commit()
        worker_for(ctx.home).enqueue(rid)
        return {"id": rid, "status": "reading", "quick": quick}

    @app.get("/api/v1/money/receipts/{receipt_id}")
    def get_receipt(receipt_id: int) -> dict[str, object]:
        with open_conn(ctx) as conn:
            _require_steward(conn)
            d = receipts.detail(conn, receipt_id)
            if d is None:
                raise HTTPException(status_code=404, detail="レシートが見つかりません")
            return d

    @app.get("/api/v1/money/receipts/{receipt_id}/image")
    def get_image(receipt_id: int) -> FileResponse:
        with open_conn(ctx) as conn:
            _require_steward(conn)
            row = receipts.get_row(conn, receipt_id)
            if row is None or not row["image_path"]:
                raise HTTPException(status_code=404, detail="画像がありません")
            path = receipts.image_path(ctx.home, row["image_path"])
        if not path.is_file():
            raise HTTPException(status_code=404, detail="画像がありません")
        media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        return FileResponse(str(path), media_type=media)

    @app.put("/api/v1/money/receipts/{receipt_id}")
    def update_receipt(receipt_id: int, body: ReceiptUpdate) -> dict[str, object]:
        require_writable(ctx)
        payload: dict[str, Any] = body.model_dump(exclude_unset=True)
        if "taxes" in payload and payload["taxes"] is not None:
            payload["taxes"] = [dict(t) for t in payload["taxes"]]
        if "items" in payload and payload["items"] is not None:
            payload["items"] = [dict(i) for i in payload["items"]]
        with open_conn(ctx) as conn:
            _require_steward(conn)
            d = receipts.update(conn, receipt_id, payload)
            if d is None:
                raise HTTPException(status_code=404, detail="レシートが見つかりません")
            try:
                render_mod.render(conn, ctx.home)
            except Exception:  # noqa: BLE001
                pass
            return d

    @app.post("/api/v1/money/receipts/{receipt_id}/reread")
    def reread(receipt_id: int) -> dict[str, object]:
        require_writable(ctx)
        with open_conn(ctx) as conn:
            _require_steward(conn)
            if not receipts.mark_reading(conn, receipt_id):
                raise HTTPException(status_code=404, detail="レシートが見つかりません")
        worker_for(ctx.home).enqueue(receipt_id)
        return {"id": receipt_id, "status": "reading"}

    @app.delete("/api/v1/money/receipts/{receipt_id}")
    def delete_receipt(receipt_id: int) -> dict[str, object]:
        require_writable(ctx)
        with open_conn(ctx) as conn:
            _require_steward(conn)
            if not receipts.discard(conn, receipt_id):
                raise HTTPException(status_code=404, detail="レシートが見つかりません")
            try:
                render_mod.render(conn, ctx.home)
            except Exception:  # noqa: BLE001
                pass
            return {"ok": True}
