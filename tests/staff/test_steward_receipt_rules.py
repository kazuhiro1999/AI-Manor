"""レシートの規則（ADR-020 D4・D5）の試験。**合成データのみ**（架空の店「テスト商店」の OCR 箱と下書き）。

実物の写真は `home/`（②）にしか置かない。ここでは OCR の出力の形（四角＋文字）を手で組み、
規則の構造化と 6 本の検算が決定論的に動くことを見張る。
"""

from __future__ import annotations

import math

import pytest

from manor.staff.steward import receipt_checks as rc
from manor.staff.steward import receipt_classify as rcl
from manor.staff.steward import receipt_parse as rp

# --- 合成の OCR 箱 -------------------------------------------------------------------------


def _box(x: float, y: float, w: float, h: float, text: str, *, angle_deg: float = 0.0, score: float = 0.9) -> dict:
    """左上 (x, y)・幅 w・高さ h の四角。`angle_deg` で全体を回す（傾いた写真の真似）。"""
    pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    rot = [[px * c - py * s, px * s + py * c] for px, py in pts]
    return {"box": rot, "text": text, "score": score}


def synthetic_receipt(*, angle_deg: float = 0.0, misread: bool = False) -> list[dict]:
    """外税の架空レシート。明細 4 行（数量行・値引き行つき）。合計欄は全部揃う。
    小計 1,000 = 300 + 2×200 + 400 − 100 ／ 8% 対象 900 → 税 72 ／ 10% 対象 100 → 税 10 ／ 合計 1,082。
    """
    y = 100.0
    rows: list[dict] = []
    h = 30.0

    def line(cells: list[tuple[float, float, str]]) -> None:
        nonlocal y
        for x, w, text in cells:
            rows.append(_box(x, y, w, h, text, angle_deg=angle_deg))
        y += 44

    line([(300, 200, "テスト商店")])
    line([(300, 120, "登録番号T1234567890123")])
    line([(300, 260, "2026年9月20日(土)18:05 #000123")])
    line([(300, 100, "#000456"), (450, 80, "R1234"), (560, 60, "18:04")])
    price_x = 700
    line([(200, 40, "外8"), (260, 200, "ギュウニュウ 1L"), (price_x, 90, "¥300外" if not misread else "半300タト")])
    line([(200, 40, "外8"), (260, 200, "ショクパン 6マイ"), (price_x, 90, "¥400外")])
    line([(300, 120, "(2個 × @200)")])
    line([(200, 40, "外10"), (260, 200, "ビール 350ML"), (price_x, 90, "¥100外")])
    line([(260, 200, "ワリビキ"), (price_x, 90, "-100")])
    line([(260, 200, "ホウレンソウ"), (price_x, 90, "¥300外")])
    line([(200, 60, "小計"), (price_x, 90, "¥1,000")])
    line([(200, 80, "外税額8"), (500, 40, "8%"), (price_x, 90, "¥72")])
    line([(200, 80, "外税額10"), (500, 40, "10%"), (price_x, 90, "¥10")])
    line([(200, 80, "買上点数"), (price_x, 60, "5点")])
    line([(200, 80, "合計"), (price_x - 100, 190, "¥1,082")])
    line([(200, 80, "お預り"), (price_x - 100, 190, "¥2,000")])
    line([(200, 80, "お釣り"), (price_x - 100, 190, "¥918")])
    return rows


def _finish(raw: dict) -> tuple[dict, dict, str]:
    d = rc.derive_missing(rc.normalize_draft(raw))
    checks = rc.run_checks(d)
    return d, checks, rc.review_from_checks(d, checks)


@pytest.mark.parametrize("angle", [0.0, 2.5, -3.0])
def test_parse_synthetic_receipt_all_checks_pass(angle: float) -> None:
    raw = rp.parse_boxes(synthetic_receipt(angle_deg=angle))
    d, checks, review = _finish(raw)
    assert d["store"]["name"] == "テスト商店"
    assert d["store"]["registration_number"] == "T1234567890123"
    assert d["purchased_at"] == "2026-09-20T18:05"
    assert d["receipt_no"] == "000123"
    assert d["subtotal"] == 1000 and d["total"] == 1082 and d["item_count"] == 5
    assert d["tendered"] == 2000 and d["change"] == 918
    assert d["taxes"] == [{"rate": 8, "amount": 72}, {"rate": 10, "amount": 10}]
    assert d["tax_mode"] == "exclusive"
    names = [i["name"] for i in d["items"]]
    assert names == ["ギュウニュウ 1L", "ショクパン 6マイ", "ビール 350ML", "ワリビキ", "ホウレンソウ"]
    amounts = [i["amount"] for i in d["items"]]
    assert amounts == [300, 400, 100, -100, 300]
    assert d["items"][1]["qty"] == 2 and d["items"][1]["unit_price"] == 200
    assert d["items"][2]["tax_rate"] == 10 and d["items"][0]["tax_rate"] == 8
    assert d["items"][3]["is_discount"] is True
    assert all(c["ok"] for c in checks.values()), checks
    assert review == "ok"


def test_parse_absorbs_price_misreads() -> None:
    """`¥` が `半` に、`外` が `タト` に読めても価格として拾う。"""
    raw = rp.parse_boxes(synthetic_receipt(misread=True))
    d, checks, review = _finish(raw)
    assert d["items"][0]["amount"] == 300
    assert review == "ok"


def test_parse_rotated_90_degrees() -> None:
    """横向きの写真（箱が 90° 回っている）でも座標を戻して同じ結果になる。"""
    raw = rp.parse_boxes(synthetic_receipt(angle_deg=-90))
    d, checks, review = _finish(raw)
    assert d["total"] == 1082
    assert [i["amount"] for i in d["items"]] == [300, 400, 100, -100, 300]
    assert review == "ok"


def test_parse_empty() -> None:
    raw = rp.parse_boxes([])
    d, checks, review = _finish(raw)
    assert d["items"] == [] and d["total"] is None and review == "needs_review"


# --- 検算 ----------------------------------------------------------------------------------


def _draft(**over: object) -> dict:
    base = {
        "store": {"name": "テスト商店", "registration_number": "T1234567890123"},
        "purchased_at": "2026-09-20T18:05",
        "tax_mode": "exclusive",
        "items": [
            {"name": "A", "qty": 1, "amount": 300, "tax_rate": 8},
            {"name": "B", "qty": 2, "unit_price": 200, "amount": 400, "tax_rate": 8},
            {"name": "C", "qty": 1, "amount": 100, "tax_rate": 10},
            {"name": "値引き", "qty": 1, "amount": -100, "is_discount": True},
            {"name": "D", "qty": 1, "amount": 300, "tax_rate": 8},
        ],
        "subtotal": 1000,
        "taxes": [{"rate": 8, "amount": 72}, {"rate": 10, "amount": 10}],
        "total": 1082,
        "item_count": 5,
        "tendered": 2000,
        "change": 918,
    }
    base.update(over)
    return base


def test_checks_pass_and_discount_tax_bucket_is_flexible() -> None:
    d, checks, review = _finish(_draft())
    assert review == "ok"
    assert checks["tax_8"]["ok"] is True and checks["tax_10"]["ok"] is True


def test_checks_catch_row_shift_and_digit_error() -> None:
    items = _draft()["items"]
    items[0]["amount"] = 3000  # 桁の読み違い
    d, checks, review = _finish(_draft(items=items))
    assert checks["items_sum"]["ok"] is False
    assert checks["items_sum"]["actual"] - checks["items_sum"]["expected"] == 2700
    assert review == "needs_review"
    assert "items_sum:+2700" in rc.describe_mismatch(checks)


def test_derive_missing_tax_and_mode() -> None:
    d = rc.normalize_draft(_draft(taxes=[{"rate": 10, "amount": 10}], tax_mode="unknown"))
    d = rc.derive_missing(d)
    assert rc.tax_amount(d, 8) == 72
    assert d["tax_mode"] == "exclusive"


def test_derive_overrides_inconsistent_tax8() -> None:
    """合計欄の内訳の数字を 8% 税額として拾ってしまったとき、合計−小計−10% 税 に直す。"""
    d = rc.normalize_draft(_draft(taxes=[{"rate": 8, "amount": 6}, {"rate": 10, "amount": 10}]))
    d = rc.derive_missing(d)
    assert rc.tax_amount(d, 8) == 72
    assert "tax8_derived" in d["notes"]


def test_inclusive_receipt_uses_total_for_items_sum() -> None:
    d, checks, review = _finish({
        "store": {"name": "内税の店"}, "purchased_at": "2026-09-20", "tax_mode": "inclusive",
        "items": [{"name": "A", "amount": 500}, {"name": "B", "amount": 700}], "total": 1200,
    })
    assert checks["items_sum"]["ok"] is True
    assert review == "ok"


def test_fingerprint_and_normalize_name() -> None:
    a = rc.fingerprint(_draft())
    b = rc.fingerprint(_draft(items=[]))
    assert a == b and len(a) == 16
    assert rc.fingerprint(_draft(total=None)) is None
    assert rc.normalize_name("ｽｷﾞﾓﾄ ﾜｷﾞｭｳ ｺﾛｯｹ") == "スギモトワギュウコロッケ".lower()
    assert rc.normalize_name("*アサヒ (350ML)") == "アサヒ350ml"


def test_normalize_draft_tolerates_garbage() -> None:
    d = rc.normalize_draft({"items": [{"name": "", "amount": None}, "x", {"name": "A", "amount": "1,200"}], "total": "¥3,000", "taxes": [{"rate": "8", "amount": "10"}, {"rate": 5, "amount": 1}], "tax_mode": "weird", "purchased_at": "2026/9/5 12:34"})
    assert [i["amount"] for i in d["items"]] == [1200]
    assert d["total"] == 3000 and d["taxes"] == [{"rate": 8, "amount": 10}]
    assert d["tax_mode"] == "unknown" and d["purchased_at"] == "2026-09-05T12:34"


# --- 分類（語彙） ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,kind",
    [
        ("ギュウニュウ 1L", "乳製品"),
        ("ホウレンソウ", "野菜"),
        ("コクサンブタロース", "肉類"),
        ("スカイリーフ ジャスミンチャ", "飲料"),
        ("アジノモト ピュアセレクトマヨネーズ", "調味料"),  # 「アジ」で魚介に誤爆しない
        ("ナゾノシナ", ""),
    ],
)
def test_kind_by_keyword(name: str, kind: str) -> None:
    assert rcl._kind_by_keyword(name) == kind


def test_lexicon_vocabulary_is_consistent() -> None:
    cats = rcl.categories()
    kinds = rcl.item_kinds()
    assert "食費" in cats and "食料品" in cats["食費"]
    for kind in kinds:
        cat, sub = rcl.kind_default(kind)
        assert cat in cats, kind
        assert sub in cats[cat], kind
    for kind in (rp.lexicon().get("item_kind_keywords") or {}):
        assert kind in kinds, kind
