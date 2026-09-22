# ADR-020 API 契約（`/api/v1/money/receipts*`・`/api/v1/money/breakdown`。画面と裏側が同時に作るための取り決め）

認証は既存の合言葉 cookie（`/api/v1/*` と同じ）。書き込みは `require_writable`。

## POST /api/v1/money/receipts
multipart/form-data: `file`（image/jpeg か image/png。原寸のまま送る）、任意 `force`（"1" で簡易チェックの不合格を無視して受け付ける）。

- 受付: `200 {"id": 12, "status": "reading", "quick": Quick}`
- 簡易チェック不合格（画像は保存しない）: `200 {"id": null, "status": "rejected", "quick": Quick}`
- 同じレシートが登録済み: 背景ジョブの結果として `GET /{id}` の `status="discarded"`, `reason="duplicate_of:7"`
- 1 日の上限超え: `429`

```
Quick = {"ok": bool, "issues": ["corners_cut"|"blurry"|"too_dark"|"no_total"|"ocr_unavailable"|"unreadable"],
         "paper": bool|null, "corners_inside": bool|null, "sharpness": number|null, "found": {"total": bool|null, "date": bool|null}}
```
`ocr_unavailable` は「OCR が入っていないので簡易チェックを飛ばした」の印（ok は true のまま）。

## GET /api/v1/money/receipts?status=&limit=50
```
{"items": [ReceiptSummary...],
 "ocr": {"available": bool, "device": "cpu"|"dml"|null, "model": "PP-OCRv6-small"|null},
 "today": {"count": 3, "limit": 30}}
ReceiptSummary = {"id", "status", "review", "store_name", "purchased_at", "total", "item_count",
                  "method", "reason", "created_at", "committed_at"}
```
- `status`: `reading` → `committed`（登録済み）／`failed`（読めなかった。reason にコード）／`discarded`
- `review`: `ok`（検算が全部一致）／`needs_review`（不一致や分類なしがある）／`fixed`（主人が直した）
- `method`: `ocr`／`claude`／`ocr+claude`／`manual`／`""`（未読）
- `reason` のコード: `no_total`、`duplicate_of:<id>`、`error`、`unreadable`、`discarded`

## GET /api/v1/money/receipts/{id}
```
ReceiptDetail = {
  "id", "status", "review", "method", "reads", "reason", "created_at", "committed_at", "created_by",
  "store": {"name": str, "branch": str|null, "tel": str|null, "registration_number": str|null},
  "purchased_at": "YYYY-MM-DDTHH:MM"|"YYYY-MM-DD"|null, "receipt_no": str|null,
  "tax_mode": "exclusive"|"inclusive"|"unknown", "payment_method": "cash"|"credit"|"qr"|"ic"|"unknown",
  "subtotal": int|null, "taxes": [{"rate": 8|10, "amount": int}], "total": int|null,
  "item_count_declared": int|null, "tendered": int|null, "change": int|null,
  "items": [ReceiptItem...],
  "checks": {"items_sum": Check, "item_count": Check, "tax_8": Check, "tax_10": Check, "total": Check, "change": Check},
  "notes": [str],
  "image_url": "/api/v1/money/receipts/12/image",
  "expenses": [{"id", "date", "amount", "category", "memo"}]
}
ReceiptItem = {"id", "line_no", "name", "name_normalized", "qty", "unit_price": int|null, "amount", "tax_rate": 8|10|null,
               "is_discount": bool, "category": str, "subcategory": str, "item_kind": str, "source": "ocr"|"claude"|"rule"|"alias"|"manual"}
Check = {"ok": bool|null, "expected": int|null, "actual": int|null}   # null = 判定できない（材料が無い）
```

## GET /api/v1/money/receipts/{id}/image → image/jpeg（原寸）

## PUT /api/v1/money/receipts/{id}
本文（全部任意。送った鍵だけ上書き）:
```
{"store_name", "purchased_at", "total", "subtotal", "tax_mode", "payment_method",
 "taxes": [{"rate", "amount"}],
 "items": [{"line_no", "name", "qty", "unit_price", "amount", "tax_rate", "is_discount", "category", "subcategory", "item_kind"}],
 "learn_aliases": true}
```
- `items` を送ると明細を丸ごと置き換える（画面は全行を送る）。保存後に検算をやり直し、`review` は `fixed`。
- `steward_expense` の該当行（`receipt_id`）を書き直す。`learn_aliases: true` なら `steward_item_alias` に覚える。
- 返り値は `ReceiptDetail`。

## POST /api/v1/money/receipts/{id}/reread → `{"id", "status": "reading"}`
## DELETE /api/v1/money/receipts/{id} → `{"ok": true}`（`discarded`。登録も外す。画像は残す）

## GET /api/v1/money/categories
```
{"categories": [{"name": "食費", "subcategories": ["食料品", "外食", ...]}, ...],
 "item_kinds": ["肉類", "魚介", "野菜", ...]}
```

---

## GET /api/v1/money/breakdown?ym=YYYY-MM（内訳。2026-09-22 追加。主人「何にお金を一番使ったかが分かるように」）

`ym` 省略時は今月。**数えるだけ**（支出は `steward_expense`、内訳は登録済みレシートの明細）。

```
{"ym": "2026-09",
 "prev_ym": "2026-08",
 "months": [{"ym": "2026-04", "expense": 123456, "income": 0}, ...],   # データのある月だけ、古い→新しい、直近 6 か月
 "summary": {"expense": 123456, "income": 0, "prev_expense": 110000|null, "diff": 13456|null,
             "receipts": 12, "days_with_spending": 9},
 "by_category":    [{"name": "食費", "amount": 80000, "share": 0.65, "prev_amount": 70000|null, "budget": 90000|null}, ...],  # 大項目。金額の降順
 "by_subcategory": [{"category": "食費", "name": "食料品", "amount": 60000, "share": 0.49}, ...],   # レシート明細から。降順
 "by_item_kind":   [{"name": "肉類", "amount": 20000, "share": 0.16, "items": 14}, ...],            # レシート明細から。降順
 "by_store":       [{"name": "ロピア", "amount": 45000, "share": 0.37, "receipts": 4}, ...],         # 登録済みレシートから。降順
 "top_items":      [{"name": "コクサンブタロース", "amount": 2296, "qty": 4, "store": "ロピア", "item_kind": "肉類", "count": 2}, ...],  # 同じ品名を足した上位 10
 "daily":          [{"date": "2026-09-01", "amount": 0}, ... 月の全日 ...],
 "top_category": {"name": "食費", "amount": 80000, "share": 0.65}|null,
 "top_item_kind": {"name": "肉類", "amount": 20000, "share": 0.16}|null
}
```
- `share` は当月の支出合計に対する割合（0〜1）。`by_subcategory`／`by_item_kind`／`by_store`／`top_items` の金額は
  **税込に按分**（レシートの合計 ÷ 明細の合計 の比を掛ける。明細が無い支出（手入力・CSV）は大項目にしか現れない）。
- `prev_amount`／`prev_expense` は前月に記録が無ければ `null`。
- 表示の要点（画面）: 今月の支出・前月比の帯 → 大項目の横棒（割合つき・予算があれば予算線）→ 品目トップ・店トップ・
  品目トップ10 → 日別の棒。棒は**単一の色**（系列は 1 つ。大項目の識別は文字で）。数値は文字で直接添える。
