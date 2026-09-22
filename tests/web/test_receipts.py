"""`money/receipts`（ADR-020 D9）の試験。**合成データのみ**。OCR・Claude・簡易チェックは stub、
背景の作業列は同期に差し替える。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from manor import db as db_mod
from manor.staff.steward import receipt_checks as rc
from manor.staff.steward import receipts
from manor.web import app as web_app_mod
from manor.web.api_v1 import receipts as api_receipts

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def make_client(home: Path, *, read_only: bool = False) -> TestClient:
    return TestClient(web_app_mod.create_app(home, read_only=read_only))


def _draft(**over: object) -> dict:
    base = {
        "store": {"name": "テスト商店", "registration_number": "T1234567890123"},
        "purchased_at": "2026-09-20T18:05",
        "tax_mode": "exclusive",
        "items": [
            {"name": "ギュウニュウ 1L", "qty": 1, "amount": 300, "tax_rate": 8},
            {"name": "センザイ", "qty": 1, "amount": 700, "tax_rate": 10},
        ],
        "subtotal": 1000,
        "taxes": [{"rate": 8, "amount": 24}, {"rate": 10, "amount": 70}],
        "total": 1094,
        "item_count": 2,
    }
    base.update(over)
    return base


@pytest.fixture
def stubbed(monkeypatch: pytest.MonkeyPatch, home: Path):
    """読み取りを stub し、作業列を同期にする。"""
    state = {"draft": _draft(), "quick": {"ok": True, "issues": [], "paper": True, "corners_inside": True, "sharpness": 100.0, "found": {"total": True, "date": True}}}

    def fake_read(path: Path, *, cfg: dict, claude_bin: str | None = None, allow_claude: bool | None = None) -> dict:
        d = rc.derive_missing(rc.normalize_draft(state["draft"]))
        checks = rc.run_checks(d)
        return {"draft": d, "checks": checks, "review": rc.review_from_checks(d, checks), "method": "ocr", "attempts": [], "reason": "", "ocr_text": ""}

    def fake_quick(data: bytes, home_: Path) -> dict:
        return dict(state["quick"])

    class SyncWorker:
        def __init__(self, home_: Path) -> None:
            self.home = home_

        def enqueue(self, rid: int) -> None:
            conn = db_mod.connect(self.home)
            try:
                receipts.process(conn, self.home, rid)
            finally:
                conn.close()

    monkeypatch.setattr(receipts, "read_image", fake_read)
    monkeypatch.setattr(receipts, "quick_check", fake_quick)
    monkeypatch.setattr(api_receipts, "worker_for", lambda home_: SyncWorker(home_))
    return state


def _upload(client: TestClient, *, force: bool = False):
    data = {"force": "1"} if force else {}
    return client.post("/api/v1/money/receipts", data=data, files={"file": ("r.png", _PNG, "image/png")})


def test_upload_then_list_and_detail(home: Path, stubbed) -> None:
    client = make_client(home)
    res = _upload(client)
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "reading" and body["id"] == 1 and body["quick"]["ok"] is True

    listing = client.get("/api/v1/money/receipts").json()
    assert listing["today"]["count"] == 1 and listing["today"]["limit"] == 30
    assert "ocr" in listing and set(listing["ocr"]) == {"available", "device", "model"}
    row = listing["items"][0]
    assert row["status"] == "committed" and row["review"] == "ok" and row["total"] == 1094 and row["item_count"] == 2

    d = client.get("/api/v1/money/receipts/1").json()
    assert d["store"]["name"] == "テスト商店" and len(d["items"]) == 2 and len(d["expenses"]) == 2
    assert d["image_url"] == "/api/v1/money/receipts/1/image"
    img = client.get("/api/v1/money/receipts/1/image")
    assert img.status_code == 200 and img.headers["content-type"].startswith("image/png")


def test_upload_rejected_by_quick_check_unless_forced(home: Path, stubbed) -> None:
    stubbed["quick"] = {"ok": False, "issues": ["blurry"], "paper": True, "corners_inside": True, "sharpness": 3.0, "found": {"total": None, "date": None}}
    client = make_client(home)
    res = _upload(client).json()
    assert res["status"] == "rejected" and res["id"] is None and res["quick"]["issues"] == ["blurry"]
    assert client.get("/api/v1/money/receipts").json()["items"] == []
    res = _upload(client, force=True).json()
    assert res["status"] == "reading" and res["id"] == 1


def test_upload_rejects_non_image_and_daily_limit(home: Path, stubbed) -> None:
    client = make_client(home)
    res = client.post("/api/v1/money/receipts", files={"file": ("x.txt", b"hello", "text/plain")})
    assert res.status_code == 400
    (home / "config.toml").write_text("[receipt]\ndaily_limit = 1\n", encoding="utf-8")
    assert _upload(client).status_code == 200
    assert _upload(client).status_code == 429


def test_duplicate_second_photo(home: Path, stubbed) -> None:
    client = make_client(home)
    _upload(client)
    _upload(client)
    rows = client.get("/api/v1/money/receipts").json()["items"]
    assert [r["status"] for r in rows] == ["discarded", "committed"]
    assert rows[0]["reason"] == "duplicate_of:1"


def test_put_updates_items_and_expenses(home: Path, stubbed) -> None:
    client = make_client(home)
    _upload(client)
    res = client.put(
        "/api/v1/money/receipts/1",
        json={
            "store_name": "テスト商店 本店",
            "items": [
                {"name": "ギュウニュウ 1L", "qty": 1, "amount": 300, "tax_rate": 8, "category": "食費", "subcategory": "食料品", "item_kind": "乳製品"},
                {"name": "センザイ", "qty": 1, "amount": 700, "tax_rate": 10, "category": "日用品", "subcategory": "日用品", "item_kind": "日用雑貨"},
            ],
            "learn_aliases": True,
        },
    )
    assert res.status_code == 200
    d = res.json()
    assert d["review"] == "fixed" and d["store"]["name"] == "テスト商店 本店"
    assert {e["category"] for e in d["expenses"]} == {"食費", "日用品"}
    assert sum(e["amount"] for e in d["expenses"]) == 1094
    assert client.get("/api/v1/money/categories").json()["categories"][0]["name"] == "食費"


def test_reread_and_delete(home: Path, stubbed) -> None:
    client = make_client(home)
    _upload(client)
    res = client.post("/api/v1/money/receipts/1/reread")
    assert res.status_code == 200 and res.json()["status"] == "reading"
    assert client.get("/api/v1/money/receipts/1").json()["reads"] == 2
    assert client.delete("/api/v1/money/receipts/1").json() == {"ok": True}
    d = client.get("/api/v1/money/receipts/1").json()
    assert d["status"] == "discarded" and d["expenses"] == []
    assert client.get("/api/v1/money/receipts/99").status_code == 404
    assert client.delete("/api/v1/money/receipts/99").status_code == 404


def test_read_only_blocks_writes(home: Path, stubbed) -> None:
    client = make_client(home, read_only=True)
    assert _upload(client).status_code == 403
    assert client.get("/api/v1/money/receipts").status_code == 200
