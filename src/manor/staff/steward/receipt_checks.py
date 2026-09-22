"""レシートの下書き（共通スキーマ）と検算（ADR-020 D5）。純粋関数だけ。

下書きの形（`draft`）は店舗・読み取り手段に依存しない共通の辞書。OCR＋規則でも Claude でも
手入力でもこの形に揃え、同じ検算にかける（ADR-020 D1「同じスキーマ・同じ検算・同じ画面」）。

```
{"store": {"name","branch","tel","registration_number"},
 "purchased_at": "YYYY-MM-DDTHH:MM"|"YYYY-MM-DD"|None, "receipt_no": str|None,
 "tax_mode": "exclusive"|"inclusive"|"unknown",
 "items": [{"name","qty","unit_price","amount","tax_rate","is_discount",
            "category","subcategory","item_kind","source"}],
 "subtotal": int|None, "taxes": [{"rate": 8|10, "amount": int}], "total": int|None,
 "item_count": int|None, "tendered": int|None, "change": int|None,
 "payment_method": "cash"|"credit"|"qr"|"ic"|"unknown", "notes": [str]}
```

**検算は 6 本**（①Σ明細＝小計 ②Σ数量＝買上点数 ③④税率別 対象額×率≒税額 ⑤小計＋税＝合計
⑥お預り−合計＝お釣り）。材料が無い検算は `ok: None`（判定不能）で、落ちたことにはしない。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

TAX_TOLERANCE = 2  # 円。切り捨て／四捨五入が店で違うぶん

VALID_TAX_MODES: tuple[str, ...] = ("exclusive", "inclusive", "unknown")
VALID_PAYMENTS: tuple[str, ...] = ("cash", "credit", "qr", "ic", "unknown")
VALID_SOURCES: tuple[str, ...] = ("ocr", "claude", "rule", "alias", "manual")

_WS_RE = re.compile(r"[\s　]+")
_NAME_STRIP_RE = re.compile(r"[\"'`´・.,、。()（）\[\]【】*＊※#＃!！?？:：;；/／\\|_\-—–ー〜~]+")


def normalize_name(name: object) -> str:
    """品名の名寄せ用の正規化。NFKC（半角カナ→全角カナ・全角英数→半角）→ 空白と記号を除く →
    小文字。`steward_item_alias` の鍵と、語彙の当たり判定に使う。表示用ではない。"""
    text = unicodedata.normalize("NFKC", str(name or ""))
    text = _WS_RE.sub("", text)
    text = _NAME_STRIP_RE.sub("", text)
    return text.lower()


def _to_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    text = unicodedata.normalize("NFKC", str(value)).replace(",", "").replace("¥", "").strip()
    text = text.replace("−", "-").replace("ー", "-")
    if re.fullmatch(r"-?\d+(\.\d+)?", text):
        return int(round(float(text)))
    return None


def empty_draft() -> dict[str, object]:
    return {
        "store": {"name": "", "branch": None, "tel": None, "registration_number": None},
        "purchased_at": None,
        "receipt_no": None,
        "tax_mode": "unknown",
        "items": [],
        "subtotal": None,
        "taxes": [],
        "total": None,
        "item_count": None,
        "tendered": None,
        "change": None,
        "payment_method": "unknown",
        "notes": [],
    }


def normalize_item(raw: object, line_no: int) -> dict[str, object] | None:
    """明細 1 行を共通の形に。品名も金額も無い行は捨てる（None）。"""
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    amount = _to_int(raw.get("amount"))
    if not name and amount is None:
        return None
    qty = _to_int(raw.get("qty"))
    if qty is None or qty <= 0:
        qty = 1
    unit_price = _to_int(raw.get("unit_price"))
    if unit_price is None and qty == 1 and amount is not None and amount > 0:
        unit_price = amount  # 1 個なら単価＝金額（数量行が無い行。表示と集計を揃える）
    tax_rate = _to_int(raw.get("tax_rate"))
    if tax_rate not in (8, 10):
        tax_rate = None
    is_discount = bool(raw.get("is_discount")) or (amount is not None and amount < 0)
    source = str(raw.get("source") or "ocr")
    if source not in VALID_SOURCES:
        source = "ocr"
    return {
        "line_no": line_no,
        "name": name,
        "name_normalized": normalize_name(name),
        "qty": qty,
        "unit_price": unit_price,
        "amount": amount,
        "tax_rate": tax_rate,
        "is_discount": is_discount,
        "category": str(raw.get("category") or ""),
        "subcategory": str(raw.get("subcategory") or ""),
        "item_kind": str(raw.get("item_kind") or ""),
        "source": source,
    }


def normalize_draft(raw: object) -> dict[str, object]:
    """どんな形で来ても共通の形に揃える（鍵の欠け・型の揺れ・語彙外を吸収）。例外は投げない。"""
    d = empty_draft()
    if not isinstance(raw, dict):
        return d
    store = raw.get("store") if isinstance(raw.get("store"), dict) else {}
    reg = store.get("registration_number")
    reg_text = re.sub(r"[^0-9T]", "", unicodedata.normalize("NFKC", str(reg or "")).upper())
    if re.fullmatch(r"T\d{13}", reg_text):
        reg_norm: str | None = reg_text
    elif re.fullmatch(r"\d{13}", reg_text):
        reg_norm = "T" + reg_text
    else:
        reg_norm = None
    d["store"] = {
        "name": str(store.get("name") or "").strip(),
        "branch": (str(store["branch"]).strip() or None) if store.get("branch") else None,
        "tel": (str(store["tel"]).strip() or None) if store.get("tel") else None,
        "registration_number": reg_norm,
    }
    purchased = str(raw.get("purchased_at") or "").strip() or None
    if purchased is not None:
        m = re.match(r"^(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?(?:[T\s(（].*?(\d{1,2}):(\d{2}))?", purchased)
        if m:
            y, mo, da = int(m.group(1)), int(m.group(2)), int(m.group(3))
            purchased = f"{y:04d}-{mo:02d}-{da:02d}"
            if m.group(4) is not None:
                purchased += f"T{int(m.group(4)):02d}:{m.group(5)}"
        else:
            purchased = None
    d["purchased_at"] = purchased
    d["receipt_no"] = (str(raw["receipt_no"]).strip() or None) if raw.get("receipt_no") else None
    tax_mode = str(raw.get("tax_mode") or "unknown")
    d["tax_mode"] = tax_mode if tax_mode in VALID_TAX_MODES else "unknown"
    items: list[dict[str, object]] = []
    for i, item in enumerate(raw.get("items") or [], start=1):
        norm = normalize_item(item, len(items) + 1)
        if norm is not None:
            items.append(norm)
    d["items"] = items
    for key in ("subtotal", "total", "item_count", "tendered", "change"):
        d[key] = _to_int(raw.get(key))
    taxes: list[dict[str, int]] = []
    for t in raw.get("taxes") or []:
        if not isinstance(t, dict):
            continue
        rate = _to_int(t.get("rate"))
        amount = _to_int(t.get("amount"))
        if rate in (8, 10) and amount is not None and not any(x["rate"] == rate for x in taxes):
            taxes.append({"rate": rate, "amount": amount})
    d["taxes"] = sorted(taxes, key=lambda x: x["rate"])
    payment = str(raw.get("payment_method") or "unknown")
    d["payment_method"] = payment if payment in VALID_PAYMENTS else "unknown"
    d["notes"] = [str(n) for n in (raw.get("notes") or []) if str(n).strip()][:20]
    return d


def tax_amount(d: dict[str, object], rate: int) -> int | None:
    for t in d.get("taxes") or []:  # type: ignore[union-attr]
        if t["rate"] == rate:  # type: ignore[index]
            return int(t["amount"])  # type: ignore[index]
    return None


def derive_missing(d: dict[str, object]) -> dict[str, object]:
    """読めなかった値を、他の値から機械的に導けるときだけ埋める（ADR-020 D5）。
    - 外税で 小計・合計・片方の税額 が揃っていれば、もう片方の税額 = 合計 − 小計 − 既知の税額
    - 税方式が unknown なら、小計＋Σ税＝合計 なら exclusive、小計＝合計 なら inclusive
    """
    subtotal, total = d.get("subtotal"), d.get("total")
    taxes: list[dict[str, int]] = list(d.get("taxes") or [])  # type: ignore[arg-type]
    t8, t10 = tax_amount(d, 8), tax_amount(d, 10)
    items: list[dict[str, object]] = list(d.get("items") or [])  # type: ignore[arg-type]
    has8 = any(i.get("tax_rate") == 8 for i in items)
    has10 = any(i.get("tax_rate") == 10 for i in items)
    # 内税で税率が 1 つだけ宣言され、明細に税率が無いレシート（飲食店など）は、その税率を明細に当てる
    # ——こうすると「対象額 × 率/(100+率) ≒ 税額」の検算が働く（外れたら needs_review で主人に回る）。
    taxes_declared = [t for t in taxes if isinstance(t.get("amount"), int)]
    if d.get("tax_mode") == "inclusive" and len(taxes_declared) == 1 and not has8 and not has10 and items:
        rate = int(taxes_declared[0]["rate"])
        for item in items:
            item["tax_rate"] = rate
        has8, has10 = rate == 8, rate == 10
    if isinstance(subtotal, int) and isinstance(total, int) and total >= subtotal and d.get("tax_mode") != "inclusive":
        # 読めた税額が 小計＋税＝合計 と食い違うとき、片方だけ直せば合うならその値に置き換える
        # （合計欄の内訳行の数字を拾った誤読。合計・小計・もう片方の税額のほうが信頼できる）。
        if t8 is not None and t10 is not None and subtotal + t8 + t10 != total:
            notes = d.setdefault("notes", [])
            # 8% の税額は 8% 対象額の 8% 前後のはず。そこから遠い方を疑う（両方遠ければ 8% を直す）。
            base8 = sum(int(i["amount"]) for i in items if i.get("tax_rate") == 8 and i.get("amount") is not None)  # type: ignore[arg-type]
            far8 = abs(int(base8 * 0.08) - t8) > TAX_TOLERANCE if base8 else True
            fixed8 = total - subtotal - t10
            fixed10 = total - subtotal - t8
            if far8 and 0 <= fixed8 <= subtotal:
                taxes = [x for x in taxes if x["rate"] != 8] + [{"rate": 8, "amount": fixed8}]
                t8 = fixed8
                if isinstance(notes, list):
                    notes.append("tax8_derived")
            elif 0 <= fixed10 <= subtotal:
                taxes = [x for x in taxes if x["rate"] != 10] + [{"rate": 10, "amount": fixed10}]
                t10 = fixed10
                if isinstance(notes, list):
                    notes.append("tax10_derived")
        if t8 is None and t10 is not None and has8:
            taxes.append({"rate": 8, "amount": total - subtotal - t10})
        elif t10 is None and t8 is not None and has10:
            taxes.append({"rate": 10, "amount": total - subtotal - t8})
        elif t8 is None and t10 is None and has8 and not has10 and total > subtotal:
            taxes.append({"rate": 8, "amount": total - subtotal})
        elif t8 is None and t10 is None and has10 and not has8 and total > subtotal:
            taxes.append({"rate": 10, "amount": total - subtotal})
        d["taxes"] = sorted(taxes, key=lambda x: x["rate"])
        if d.get("tax_mode") == "unknown":
            tax_sum = sum(t["amount"] for t in d["taxes"])  # type: ignore[index]
            if total == subtotal:
                d["tax_mode"] = "inclusive"
            elif total == subtotal + tax_sum and tax_sum > 0:
                d["tax_mode"] = "exclusive"
    return d


def _check(expected: int | None, actual: int | None, tolerance: int = 0) -> dict[str, object]:
    if expected is None or actual is None:
        return {"ok": None, "expected": expected, "actual": actual}
    return {"ok": abs(expected - actual) <= tolerance, "expected": expected, "actual": actual}


def run_checks(d: dict[str, object]) -> dict[str, dict[str, object]]:
    """6 本の検算。`expected` はレシートに印字された値、`actual` は明細から計算した値。"""
    items: list[dict[str, object]] = [i for i in (d.get("items") or []) if i.get("amount") is not None]  # type: ignore[union-attr]
    subtotal = d.get("subtotal") if isinstance(d.get("subtotal"), int) else None
    total = d.get("total") if isinstance(d.get("total"), int) else None
    items_sum = sum(int(i["amount"]) for i in items) if items else None  # type: ignore[arg-type]
    qty_sum = sum(int(i["qty"]) for i in items if not i.get("is_discount")) if items else None  # type: ignore[arg-type]
    t8, t10 = tax_amount(d, 8), tax_amount(d, 10)

    # 税率別の対象額。税区分の無い値引き行は、両方の検算が通る側に寄せる（紙の上でも曖昧）。
    base8 = sum(int(i["amount"]) for i in items if i.get("tax_rate") == 8)  # type: ignore[arg-type]
    base10 = sum(int(i["amount"]) for i in items if i.get("tax_rate") == 10)  # type: ignore[arg-type]
    untagged = sum(int(i["amount"]) for i in items if i.get("tax_rate") not in (8, 10))  # type: ignore[arg-type]
    has8 = any(i.get("tax_rate") == 8 for i in items)
    has10 = any(i.get("tax_rate") == 10 for i in items)

    # 内税（税込の明細）では 税額 = 対象額 × 率/(100+率)。外税では 対象額 × 率/100。
    inclusive = d.get("tax_mode") == "inclusive"

    def _expected_tax(base: int, rate: int) -> int:
        return int(base * rate / (100 + rate)) if inclusive else int(base * rate / 100)

    def _tax_ok(b8: int, b10: int) -> tuple[bool | None, bool | None]:
        ok8 = None if t8 is None or not has8 else abs(_expected_tax(b8, 8) - t8) <= TAX_TOLERANCE
        ok10 = None if t10 is None or not has10 else abs(_expected_tax(b10, 10) - t10) <= TAX_TOLERANCE
        return ok8, ok10

    best = (base8 + untagged, base10)
    if untagged:
        candidates = [(base8 + untagged, base10), (base8, base10 + untagged)]
        for cand in candidates:
            ok8, ok10 = _tax_ok(*cand)
            if ok8 is not False and ok10 is not False:
                best = cand
                break
    ok8, ok10 = _tax_ok(*best)

    tax_sum = sum(t["amount"] for t in (d.get("taxes") or []))  # type: ignore[index, union-attr]
    if d.get("tax_mode") == "inclusive":
        total_expected = subtotal
    elif d.get("tax_mode") == "exclusive":
        total_expected = (subtotal + tax_sum) if subtotal is not None else None
    else:
        total_expected = None
        if subtotal is not None and total is not None:
            total_expected = subtotal + tax_sum if total != subtotal else subtotal

    checks: dict[str, dict[str, object]] = {
        "items_sum": _check(subtotal, items_sum),
        "item_count": _check(d.get("item_count") if isinstance(d.get("item_count"), int) else None, qty_sum),
        "tax_8": {"ok": ok8, "expected": t8, "actual": _expected_tax(best[0], 8) if has8 else None},
        "tax_10": {"ok": ok10, "expected": t10, "actual": _expected_tax(best[1], 10) if has10 else None},
        "total": _check(total, total_expected),
        "change": _check(
            d.get("change") if isinstance(d.get("change"), int) else None,
            (int(d["tendered"]) - total) if isinstance(d.get("tendered"), int) and total is not None else None,  # type: ignore[arg-type]
        ),
    }
    # 小計が無いレシート（内税で合計しか無い）は Σ明細＝合計 で代用する。
    if subtotal is None and total is not None and items_sum is not None and d.get("tax_mode") != "exclusive":
        checks["items_sum"] = _check(total, items_sum)
    return checks


#: review を決めるときに「揃っていること」を求める検算（ADR-020 D5）。
CORE_CHECKS: tuple[str, ...] = ("items_sum", "total", "change")


def review_from_checks(d: dict[str, object], checks: dict[str, dict[str, object]]) -> str:
    """`ok`＝核の検算（あるものだけ）が全部一致し、明細があり、金額の無い明細が無い。それ以外は `needs_review`。"""
    items: list[dict[str, object]] = list(d.get("items") or [])  # type: ignore[arg-type]
    if not items or d.get("total") is None:
        return "needs_review"
    if any(i.get("amount") is None for i in items):
        return "needs_review"
    if checks["items_sum"]["ok"] is None:
        return "needs_review"
    for key in CORE_CHECKS:
        if checks.get(key, {}).get("ok") is False:
            return "needs_review"
    if checks["tax_8"]["ok"] is False or checks["tax_10"]["ok"] is False:
        return "needs_review"
    return "ok"


def fingerprint(d: dict[str, object]) -> str | None:
    """同じレシートの指紋（ADR-020 D8）: 登録番号（無ければ店名）｜日時｜合計。合計か日時が無ければ None。"""
    total, purchased = d.get("total"), d.get("purchased_at")
    if total is None or not purchased:
        return None
    store = d.get("store") if isinstance(d.get("store"), dict) else {}
    key = store.get("registration_number") or normalize_name(store.get("name"))
    raw = f"{key}|{purchased}|{total}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def describe_mismatch(checks: dict[str, dict[str, object]]) -> list[str]:
    """不一致の説明（機械が出す短い符号。画面が訳す）。例: `items_sum:+1013`。"""
    out: list[str] = []
    for key, c in checks.items():
        if c.get("ok") is False and isinstance(c.get("expected"), int) and isinstance(c.get("actual"), int):
            out.append(f"{key}:{int(c['actual']) - int(c['expected']):+d}")  # type: ignore[arg-type]
        elif c.get("ok") is False:
            out.append(key)
    return out
