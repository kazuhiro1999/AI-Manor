"""レシートの段取りと DB（ADR-020 D7・D8）の試験。**合成データのみ**。OCR と Claude は stub。

読み取り器（`receipts.read_image`）を差し替え、登録・分割・重複・修正・辞書の学習が
決定論的に動くことを見張る。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manor.staff.steward import receipt_checks as rc
from manor.staff.steward import receipts

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _draft(**over: object) -> dict:
    base = {
        "store": {"name": "テスト商店", "registration_number": "T1234567890123"},
        "purchased_at": "2026-09-20T18:05",
        "tax_mode": "exclusive",
        "items": [
            {"name": "ギュウニュウ 1L", "qty": 1, "amount": 300, "tax_rate": 8},
            {"name": "ショクパン", "qty": 2, "unit_price": 200, "amount": 400, "tax_rate": 8},
            {"name": "センザイ", "qty": 1, "amount": 300, "tax_rate": 10},
        ],
        "subtotal": 1000,
        "taxes": [{"rate": 8, "amount": 56}, {"rate": 10, "amount": 30}],
        "total": 1086,
        "item_count": 4,
        "tendered": 2000,
        "change": 914,
    }
    base.update(over)
    return base


def _stub_read(monkeypatch: pytest.MonkeyPatch, raw: dict, *, method: str = "ocr") -> None:
    def fake(path: Path, *, cfg: dict, claude_bin: str | None = None, allow_claude: bool | None = None) -> dict:
        d = rc.derive_missing(rc.normalize_draft(raw))
        checks = rc.run_checks(d)
        return {"draft": d, "checks": checks, "review": rc.review_from_checks(d, checks), "method": method, "attempts": [], "reason": "", "ocr_text": ""}

    monkeypatch.setattr(receipts, "read_image", fake)


def _ingest(conn, home: Path) -> int:
    rel = receipts.store_image(home, _PNG, ext="png")
    assert (receipts.images_dir(home) / rel).is_file()
    rid = receipts.create(conn, rel, created_by="master")
    conn.commit()
    return rid


def test_process_registers_and_splits_by_category(conn, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_read(monkeypatch, _draft())
    rid = _ingest(conn, home)
    d = receipts.process(conn, home, rid)
    assert d["status"] == "committed" and d["review"] == "ok"
    assert d["total"] == 1086 and d["store"]["name"] == "テスト商店"
    kinds = {i["name"]: i["item_kind"] for i in d["items"]}
    assert kinds["ギュウニュウ 1L"] == "乳製品" and kinds["ショクパン"] == "パン" and kinds["センザイ"] == "日用雑貨"
    cats = {e["category"]: e["amount"] for e in d["expenses"]}
    # 食費 700 : 日用品 300 の比で合計 1,086 を按分（端数は最大剰余法）
    assert sum(cats.values()) == 1086
    assert cats["食費"] == 760 and cats["日用品"] == 326
    assert all(e["date"] == "2026-09-20" for e in d["expenses"])
    row = conn.execute("SELECT fingerprint, method, reads FROM steward_receipt WHERE id = ?", (rid,)).fetchone()
    assert row["fingerprint"] and row["method"] == "ocr" and row["reads"] == 1


def test_process_marks_duplicate(conn, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_read(monkeypatch, _draft())
    first = _ingest(conn, home)
    receipts.process(conn, home, first)
    second = _ingest(conn, home)
    d = receipts.process(conn, home, second)
    assert d["status"] == "discarded" and d["reason"] == f"duplicate_of:{first}"
    assert d["expenses"] == []
    assert conn.execute("SELECT COUNT(*) FROM steward_expense").fetchone()[0] == 2  # 1 枚目の 2 行だけ


def test_process_needs_review_still_registers(conn, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    items = _draft()["items"]
    items[0]["amount"] = 3000  # 検算に落ちる
    _stub_read(monkeypatch, _draft(items=items))
    rid = _ingest(conn, home)
    d = receipts.process(conn, home, rid)
    assert d["status"] == "committed" and d["review"] == "needs_review"
    assert d["checks"]["items_sum"]["ok"] is False
    assert sum(e["amount"] for e in d["expenses"]) == 1086  # 合計は読めているので登録はされる


def test_process_without_total_fails(conn, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_read(monkeypatch, _draft(total=None, tendered=None, change=None))
    rid = _ingest(conn, home)
    d = receipts.process(conn, home, rid)
    assert d["status"] == "failed" and d["reason"] == "no_total"
    assert d["expenses"] == []


def test_update_rewrites_expenses_and_learns_aliases(conn, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_read(monkeypatch, _draft())
    rid = _ingest(conn, home)
    receipts.process(conn, home, rid)
    d = receipts.update(
        conn, rid,
        {
            "items": [
                {"name": "ギュウニュウ 1L", "qty": 1, "amount": 300, "tax_rate": 8, "category": "食費", "subcategory": "食料品", "item_kind": "乳製品"},
                {"name": "ショクパン", "qty": 2, "unit_price": 200, "amount": 400, "tax_rate": 8, "category": "食費", "subcategory": "食料品", "item_kind": "パン"},
                {"name": "センザイ", "qty": 1, "amount": 300, "tax_rate": 10, "category": "趣味・娯楽", "subcategory": "本", "item_kind": "書籍・文具"},
            ],
            "learn_aliases": True,
        },
    )
    assert d is not None and d["review"] == "fixed" and d["status"] == "committed"
    assert all(i["source"] == "manual" for i in d["items"])
    cats = {e["category"]: e["amount"] for e in d["expenses"]}
    assert set(cats) == {"食費", "趣味・娯楽"} and sum(cats.values()) == 1086
    alias = conn.execute("SELECT category, item_kind FROM steward_item_alias WHERE name_normalized = ?", (rc.normalize_name("センザイ"),)).fetchone()
    assert alias["category"] == "趣味・娯楽" and alias["item_kind"] == "書籍・文具"

    # 次のレシートでは辞書が先に当たる
    _stub_read(monkeypatch, _draft(purchased_at="2026-09-21T10:00", store={"name": "テスト商店"}))
    rid2 = _ingest(conn, home)
    d2 = receipts.process(conn, home, rid2)
    by_name = {i["name"]: i for i in d2["items"]}
    assert by_name["センザイ"]["category"] == "趣味・娯楽" and by_name["センザイ"]["source"] == "alias"


def test_discard_removes_expenses(conn, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_read(monkeypatch, _draft())
    rid = _ingest(conn, home)
    receipts.process(conn, home, rid)
    assert receipts.discard(conn, rid)
    assert conn.execute("SELECT COUNT(*) FROM steward_expense WHERE receipt_id = ?", (rid,)).fetchone()[0] == 0
    assert receipts.get_row(conn, rid)["status"] == "discarded"
    assert not receipts.discard(conn, 999)


def test_split_by_category_rounding() -> None:
    d = rc.normalize_draft({
        "items": [{"name": "a", "amount": 1, "category": "食費"}, {"name": "b", "amount": 1, "category": "日用品"}, {"name": "c", "amount": 1, "category": "その他"}],
        "total": 100, "purchased_at": "2026-09-20",
    })
    parts = dict(receipts.split_by_category(d))
    assert sum(parts.values()) == 100 and set(parts) == {"食費", "日用品", "その他"}
    d2 = rc.normalize_draft({"items": [], "total": 500, "purchased_at": "2026-09-20", "store": {"name": "x"}})
    assert receipts.split_by_category(d2) == [("未分類", 500)]


def test_settings_and_daily_count(conn, home: Path) -> None:
    cfg = receipts.settings(home)
    assert cfg["daily_limit"] == 30 and cfg["ocr_model"] == "full"
    (home / "config.toml").write_text('[receipt]\ndaily_limit = 3\nocr_device = "cpu"\n', encoding="utf-8")
    cfg = receipts.settings(home)
    assert cfg["daily_limit"] == 3 and cfg["ocr_device"] == "cpu"
    assert receipts.today_count(conn) == 0
    _ingest(conn, home)
    assert receipts.today_count(conn) == 1


def test_detail_shape(conn, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_read(monkeypatch, _draft())
    rid = _ingest(conn, home)
    d = receipts.process(conn, home, rid)
    for key in ("id", "status", "review", "method", "reads", "reason", "store", "purchased_at", "tax_mode", "payment_method",
                "subtotal", "taxes", "total", "item_count_declared", "tendered", "change", "items", "checks", "notes", "image_url", "expenses"):
        assert key in d, key
    assert d["image_url"] == f"/api/v1/money/receipts/{rid}/image"
    assert set(d["checks"]) == {"items_sum", "item_count", "tax_8", "tax_10", "total", "change"}
    assert json.dumps(d, ensure_ascii=False)  # JSON に落ちる
