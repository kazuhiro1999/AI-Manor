"""月の内訳（`manor money breakdown`／`GET /money/breakdown`）の試験。**合成データのみ**。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from manor.staff.steward import breakdown as bd
from manor.web import app as web_app_mod


def _seed(conn) -> None:
    now = "2026-09-22T10:00:00"
    rows = [
        ("2026-09-03", 3000, "expense", "食費", "手入力", now, None),
        ("2026-09-10", 2000, "expense", "日用品", "手入力", now, None),
        ("2026-09-15", 50000, "income", "給与", "", now, None),
        ("2026-08-20", 4000, "expense", "食費", "先月", now, None),
    ]
    conn.executemany("INSERT INTO steward_expense(date, amount, kind, category, memo, created_at, receipt_id) VALUES (?,?,?,?,?,?,?)", rows)
    # 登録済みレシート: 合計 1,100（外税。明細の合計 1,000 → 係数 1.1）
    conn.execute(
        "INSERT INTO steward_receipt(id, status, review, store_name, purchased_at, total, draft_json, checks_json, image_path, created_at) "
        "VALUES (1, 'committed', 'ok', 'テスト商店', '2026-09-05T12:00', 1100, '{}', '{}', 'x.jpg', ?)",
        (now,),
    )
    conn.execute("INSERT INTO steward_expense(date, amount, kind, category, memo, created_at, receipt_id) VALUES ('2026-09-05', 1100, 'expense', '食費', 'テスト商店', ?, 1)", (now,))
    items = [
        (1, 1, "ギュウニュウ", "ギュウニュウ", 1, None, 300, 8, 0, "食費", "食料品", "乳製品", "ocr"),
        (1, 2, "ブタロース", "ブタロース", 2, 250, 500, 8, 0, "食費", "食料品", "肉類", "ocr"),
        (1, 3, "ワリビキ", "ワリビキ", 1, None, -100, 8, 1, "食費", "食料品", "肉類", "ocr"),
        (1, 4, "ギュウニュウ", "ギュウニュウ", 1, None, 300, 8, 0, "食費", "食料品", "乳製品", "ocr"),
    ]
    conn.executemany(
        "INSERT INTO steward_receipt_item(receipt_id, line_no, name, name_normalized, qty, unit_price, amount, tax_rate, is_discount, category, subcategory, item_kind, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        items,
    )
    conn.execute("INSERT INTO steward_budget(category, monthly_limit) VALUES ('食費', 5000)")
    conn.commit()


def test_month_breakdown_counts(conn, home: Path) -> None:
    _seed(conn)
    d = bd.month_breakdown(conn, "2026-09", today=date(2026, 9, 22))
    s = d["summary"]
    assert s["expense"] == 6100 and s["income"] == 50000 and s["prev_expense"] == 4000 and s["diff"] == 2100
    assert s["receipts"] == 1 and s["days_with_spending"] == 3
    assert d["prev_ym"] == "2026-08"
    cats = {r["name"]: r for r in d["by_category"]}
    assert cats["食費"]["amount"] == 4100 and cats["食費"]["budget"] == 5000 and cats["食費"]["prev_amount"] == 4000
    assert cats["日用品"]["amount"] == 2000 and cats["日用品"]["prev_amount"] is None
    assert d["by_category"][0]["name"] == "食費" and abs(cats["食費"]["share"] - 4100 / 6100) < 1e-3
    # 明細は 1.1 倍に按分: 乳製品 600→660、肉類 400→440
    kinds = {r["name"]: r for r in d["by_item_kind"]}
    assert kinds["乳製品"]["amount"] == 660 and kinds["肉類"]["amount"] == 440 and kinds["乳製品"]["items"] == 2
    assert d["by_subcategory"][0] == {"category": "食費", "name": "食料品", "amount": 1100, "share": round(1100 / 6100, 4)}
    assert d["by_store"] == [{"name": "テスト商店", "amount": 1100, "share": round(1100 / 6100, 4), "receipts": 1}]
    assert d["top_items"][0]["name"] == "ギュウニュウ" and d["top_items"][0]["amount"] == 660 and d["top_items"][0]["count"] == 2
    assert len(d["daily"]) == 30 and d["daily"][4] == {"date": "2026-09-05", "amount": 1100}
    assert [m["ym"] for m in d["months"]] == ["2026-08", "2026-09"]
    assert d["top_category"]["name"] == "食費" and d["top_item_kind"]["name"] == "乳製品"


def test_month_breakdown_empty_month(conn, home: Path) -> None:
    _seed(conn)
    d = bd.month_breakdown(conn, "2026-07", today=date(2026, 9, 22))
    assert d["summary"]["expense"] == 0 and d["summary"]["prev_expense"] is None and d["by_category"] == []
    assert d["top_category"] is None and d["months"] == []


def test_breakdown_api(conn, home: Path) -> None:
    _seed(conn)
    client = TestClient(web_app_mod.create_app(home))
    res = client.get("/api/v1/money/breakdown?ym=2026-09")
    assert res.status_code == 200
    body = res.json()
    assert body["summary"]["expense"] == 6100 and body["by_category"][0]["name"] == "食費"
    assert client.get("/api/v1/money/breakdown?ym=2026-13").status_code == 404
    assert client.get("/api/v1/money/breakdown").status_code == 200
