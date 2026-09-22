"""月の内訳（2026-09-22 主人「何にお金を一番使ったかが分かるように」）。**数えるだけ**。

- 支出の全体と大項目は `steward_expense`（手入力・CSV・レシート由来を全部含む）
- 中項目・品目・店・よく買ったものは登録済みレシートの明細（`steward_receipt_item`）。明細の金額は
  税抜のことがあるので、**レシートの合計 ÷ 明細の合計 の比で税込に按分**して、大項目の額と桁を揃える
"""

from __future__ import annotations

import calendar
import sqlite3
from datetime import date
from typing import Any

from . import ops

TOP_N = 8
TOP_ITEMS = 10
MONTHS = 6


def prev_ym(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def _share(amount: int, total: int) -> float:
    return round(amount / total, 4) if total > 0 else 0.0


def _ranked(buckets: dict[str, int], total: int, *, extra: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    rows = [{"name": k, "amount": v, "share": _share(v, total), **(extra or {}).get(k, {})} for k, v in buckets.items() if v != 0]
    rows.sort(key=lambda r: (-r["amount"], r["name"]))
    return rows


def _receipt_factors(conn: sqlite3.Connection, ym: str) -> dict[int, tuple[float, str]]:
    """登録済みレシート id → (税込への按分係数, 店名)。明細の合計が 0 か、合計と 3 倍以上ずれていれば 1.0。"""
    rows = conn.execute(
        "SELECT r.id, r.total, r.store_name, "
        "(SELECT COALESCE(SUM(i.amount), 0) FROM steward_receipt_item i WHERE i.receipt_id = r.id) AS items_sum "
        "FROM steward_receipt r WHERE r.status = 'committed' AND substr(r.purchased_at, 1, 7) = ?",
        (ym,),
    ).fetchall()
    out: dict[int, tuple[float, str]] = {}
    for r in rows:
        total = int(r["total"] or 0)
        items_sum = int(r["items_sum"] or 0)
        factor = 1.0
        if total > 0 and items_sum > 0 and 1 / 3 <= total / items_sum <= 3:
            factor = total / items_sum
        out[int(r["id"])] = (factor, str(r["store_name"] or ""))
    return out


def month_breakdown(conn: sqlite3.Connection, ym: str, *, today: date | None = None) -> dict[str, Any]:
    ym = ops.parse_ym(ym)
    today = today or date.today()
    expense_rows = [dict(r) for r in conn.execute("SELECT date, amount, kind, category FROM steward_expense").fetchall()]
    budgets = {r["category"]: int(r["monthly_limit"]) for r in conn.execute("SELECT category, monthly_limit FROM steward_budget").fetchall()}

    this_month = [r for r in expense_rows if str(r["date"])[:7] == ym]
    last_month = [r for r in expense_rows if str(r["date"])[:7] == prev_ym(ym)]
    expense_total = sum(int(r["amount"]) for r in this_month if r["kind"] != "income")
    income_total = sum(int(r["amount"]) for r in this_month if r["kind"] == "income")
    prev_expense: int | None = sum(int(r["amount"]) for r in last_month if r["kind"] != "income") if last_month else None

    by_cat: dict[str, int] = {}
    for r in this_month:
        if r["kind"] != "income":
            by_cat[r["category"]] = by_cat.get(r["category"], 0) + int(r["amount"])
    prev_by_cat: dict[str, int] = {}
    for r in last_month:
        if r["kind"] != "income":
            prev_by_cat[r["category"]] = prev_by_cat.get(r["category"], 0) + int(r["amount"])
    by_category = _ranked(
        by_cat, expense_total,
        extra={k: {"prev_amount": prev_by_cat.get(k) if last_month else None, "budget": budgets.get(k)} for k in by_cat},
    )

    # 日別
    days_in_month = calendar.monthrange(int(ym[:4]), int(ym[5:7]))[1]
    daily_map: dict[str, int] = {}
    for r in this_month:
        if r["kind"] != "income":
            daily_map[str(r["date"])[:10]] = daily_map.get(str(r["date"])[:10], 0) + int(r["amount"])
    daily = [{"date": f"{ym}-{d:02d}", "amount": daily_map.get(f"{ym}-{d:02d}", 0)} for d in range(1, days_in_month + 1)]

    # レシートの明細から（税込に按分）
    factors = _receipt_factors(conn, ym)
    by_sub: dict[tuple[str, str], float] = {}
    by_kind: dict[str, float] = {}
    kind_items: dict[str, int] = {}
    by_store: dict[str, float] = {}
    store_receipts: dict[str, int] = {}
    items_agg: dict[str, dict[str, Any]] = {}
    for rid, (factor, store) in factors.items():
        store_key = store or "（店名なし）"
        store_receipts[store_key] = store_receipts.get(store_key, 0) + 1
        rows = conn.execute(
            "SELECT name, name_normalized, qty, amount, is_discount, category, subcategory, item_kind FROM steward_receipt_item WHERE receipt_id = ?",
            (rid,),
        ).fetchall()
        for it in rows:
            amt = float(it["amount"] or 0) * factor
            by_store[store_key] = by_store.get(store_key, 0.0) + amt
            cat = str(it["category"] or "未分類")
            sub = str(it["subcategory"] or "未分類")
            by_sub[(cat, sub)] = by_sub.get((cat, sub), 0.0) + amt
            kind = str(it["item_kind"] or "その他")
            by_kind[kind] = by_kind.get(kind, 0.0) + amt
            if not it["is_discount"]:
                kind_items[kind] = kind_items.get(kind, 0) + 1
                key = str(it["name_normalized"] or it["name"])
                agg = items_agg.setdefault(key, {"name": str(it["name"]), "amount": 0.0, "qty": 0, "count": 0, "store": store_key, "item_kind": kind})
                agg["amount"] += amt
                agg["qty"] += int(it["qty"] or 1)
                agg["count"] += 1

    by_subcategory = [
        {"category": cat, "name": sub, "amount": int(round(v)), "share": _share(int(round(v)), expense_total)}
        for (cat, sub), v in by_sub.items() if round(v) != 0
    ]
    by_subcategory.sort(key=lambda r: (-r["amount"], r["category"], r["name"]))
    by_item_kind = _ranked({k: int(round(v)) for k, v in by_kind.items()}, expense_total, extra={k: {"items": kind_items.get(k, 0)} for k in by_kind})
    by_store_rows = _ranked({k: int(round(v)) for k, v in by_store.items()}, expense_total, extra={k: {"receipts": store_receipts.get(k, 0)} for k in by_store})
    top_items = sorted(
        ({**a, "amount": int(round(a["amount"]))} for a in items_agg.values() if round(a["amount"]) > 0),
        key=lambda a: (-a["amount"], a["name"]),
    )[:TOP_ITEMS]

    # 月の推移（データのある月だけ。今月を含めて直近 6 か月）
    trend_rows = ops.trend(expense_rows, MONTHS * 4)
    months = [{"ym": m["ym"], "expense": m["total_expense"], "income": m["total_income"]} for m in trend_rows if m["ym"] <= ym][-MONTHS:]

    return {
        "ym": ym,
        "prev_ym": prev_ym(ym),
        "months": months,
        "summary": {
            "expense": expense_total,
            "income": income_total,
            "prev_expense": prev_expense,
            "diff": (expense_total - prev_expense) if prev_expense is not None else None,
            "receipts": len(factors),
            "days_with_spending": sum(1 for d in daily if d["amount"] > 0),
        },
        "by_category": by_category,
        "by_subcategory": by_subcategory,
        "by_item_kind": by_item_kind,
        "by_store": by_store_rows,
        "top_items": top_items,
        "daily": daily,
        "top_category": {k: by_category[0][k] for k in ("name", "amount", "share")} if by_category else None,
        "top_item_kind": {k: by_item_kind[0][k] for k in ("name", "amount", "share")} if by_item_kind else None,
    }
