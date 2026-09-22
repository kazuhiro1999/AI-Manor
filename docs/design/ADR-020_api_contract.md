# ADR-020 API 契約（`/api/v1/money/receipts*`。画面と裏側が同時に作るための取り決め）

認証は既存の合言葉 cookie（`/api/v1/*` と同じ）。書き込みは `require_writable`。

## POST /api/v1/money/receipts
multipart/form-data: `file`（image/jpeg か image/png。原寸のまま送る）、任意 `force`（"1" で簡易チェックの不合格を無視して受け付ける）。

- 受付: `200 {"id": 12, "status": "reading", "quick": Quick}`
- 簡易チェック不合格（画像は保存しない）: `200 {"id": null, "status": "rejected", "quick": Quick}`
- 同じレシートが登録済み: `200 {"id": null, "status": "duplicate", "duplicate_of": 7, "quick": Quick}`（背景ジョブの結果として起きることもある → その場合は `GET /{id}` の `status="discarded"`, `reason="duplicate_of:7"`）
- 1 日の上限超え: `429`

```
Quick = {"ok": bool, "issues": ["no_paper"|"corners_cut"|"blurry"|"too_dark"|"no_total"|"ocr_unavailable"],
         "paper": bool, "corners_inside": bool, "sharpness": number|null, "found": {"total": bool, "date": bool}}
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
- `status`: `reading` → `draft`（読めたが未登録。今の設計では通らないことが多い）→ `committed`（登録済み）／`failed`（読めなかった。reason にコード）／`discarded`
- `review`: `ok`（検算が全部一致）／`needs_review`（不一致や分類なしがある）／`fixed`（主人が直した）
- `method`: `ocr`／`claude`／`ocr+claude`／`manual`／`""`（未読）
- `reason` のコード: `no_total`（合計が読めない）、`duplicate_of:<id>`、`ocr_error`、`claude_error`、`limit`

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
- `items` を送ると明細を丸ごと置き換える（画面は全行を送る）。
- 保存後に検算をやり直し、`review` は検算が全部一致なら `fixed`、そうでなくても主人が保存したので `fixed`。
- `steward_expense` の該当行（`receipt_id`）を書き直す。
- `learn_aliases: true` なら、分類のある明細の `name_normalized → (category, subcategory, item_kind)` を `steward_item_alias` に覚える（次回から先に当たる）。
- 返り値は `ReceiptDetail`。

## POST /api/v1/money/receipts/{id}/reread → `{"id", "status": "reading"}`（背景でもう一度。直した明細は上書きされる）
## DELETE /api/v1/money/receipts/{id} → `{"ok": true}`（`discarded`。登録も外す。画像は残す）

## GET /api/v1/money/categories
```
{"categories": [{"name": "食費", "subcategories": ["食料品", "外食", ...]}, ...],
 "item_kinds": ["肉類", "魚介", "野菜", ...]}
```
