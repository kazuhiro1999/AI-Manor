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


# --- 別の型のレシート（回転寿司。2026-09-22 主人の2枚目の実物から） ----------------------------
#
# 取りこぼしていた点: ①数量行が `(@132 × 2個)`（単価が先。`@`→`0`・`個`→`个` の誤読つき）
# ②登録番号が `T8-0600-0100-1562`（区切りつき） ③買上点数が「小計 27点 ¥4,620」の行の中
# ④内税で `(内税額 ¥420)` に税率が書かれていない ⑤店名が「一番大きい字」 ⑥`クレジット` の誤読


def synthetic_sushi_receipt() -> list[dict]:
    """内税・単価が先の数量行・合計欄に点数、の型。明細 5 行 = 1,639 円（内税額 149）。
    小計＝合計（内税）。点数は 2+2+1+3+2 = 10。"""
    rows: list[dict] = []
    y = 100.0

    def line(cells: list[tuple[float, float, str]], h: float = 30.0) -> None:
        nonlocal y
        for x, w, text in cells:
            rows.append(_box(x, y, w, h, text))
        y += h + 14

    line([(300, 280, "さかな亭")], h=90)          # 一番大きい字＝店名
    line([(300, 140, "各務原店")])
    line([(300, 200, "058-260-3211")])
    line([(300, 360, "岐阜県各務原市那加緑町1丁目22-1")])   # 店名より長いが小さい字
    line([(300, 320, "登録番号:T8-0600-0100-1562")])
    line([(300, 420, "2026年09月22日(火) 20:42 No.3869")])
    line([(300, 130, "担当:32"), (700, 180, "[0389-0002]")])
    line([(300, 150, "伝票:3128"), (520, 180, "テーブル:24-1"), (800, 70, "2名")])
    price_x = 760
    line([(300, 130, "金目鯛"), (price_x, 110, "¥264内")])
    line([(360, 200, "(@132×2个)")])              # `個`→`个`
    line([(300, 130, "えび"), (price_x, 110, "¥220内")])
    line([(360, 200, "(0110× 2个)")])             # `@`→`0`
    line([(300, 190, "あぶらかれい"), (price_x, 110, "¥110内")])
    line([(300, 190, "大切りまぐろ"), (price_x, 110, "¥495内")])
    line([(360, 200, "(@165×3個)")])
    line([(300, 190, "大粒いくら海苔包み"), (price_x, 110, "¥550内")])
    line([(360, 200, "(0275×2個)")])
    line([(300, 100, "小計"), (560, 110, "10点"), (price_x, 130, "¥1,639")])
    line([(300, 330, "(10%内税対象額"), (price_x, 130, "¥1,639)")])
    line([(430, 150, "内税額"), (price_x, 110, "¥149)")])
    line([(300, 140, "合計"), (price_x - 60, 190, "¥1,639")], h=40)
    line([(300, 250, "クルシット"), (price_x - 60, 190, "¥1,639")], h=40)   # クレジットの誤読
    line([(300, 150, "お釣り"), (price_x, 80, "¥0")], h=40)
    line([(300, 460, "◇は軽減税率対象商品です。")])
    return rows


def test_sushi_receipt_parses_fully() -> None:
    raw = rp.parse_boxes(synthetic_sushi_receipt())
    d, checks, review = _finish(raw)
    assert d["store"]["name"] == "さかな亭"          # ⑤一番大きい字
    assert d["store"]["branch"] == "各務原店"
    assert d["store"]["registration_number"] == "T8060001001562"   # ②区切りつき
    assert d["purchased_at"] == "2026-09-22T20:42"
    assert d["tax_mode"] == "inclusive"
    assert d["subtotal"] == 1639 and d["total"] == 1639
    assert d["item_count"] == 10                      # ③合計欄の行の中の「10点」
    assert d["taxes"] == [{"rate": 10, "amount": 149}]  # ④税率の指定が無い「内税額」
    assert d["payment_method"] == "credit"            # ⑥クルシット → クレジット
    names = [i["name"] for i in d["items"]]
    assert names == ["金目鯛", "えび", "あぶらかれい", "大切りまぐろ", "大粒いくら海苔包み"]
    assert [(i["qty"], i["unit_price"], i["amount"]) for i in d["items"]] == [
        (2, 132, 264), (2, 110, 220), (1, 110, 110), (3, 165, 495), (2, 275, 550),
    ]  # ①単価が先の数量行
    assert not raw["parse"]["notes"]                  # ⑦伝票・テーブル・2名が明細に混ざらない
    assert checks["items_sum"]["ok"] is True and checks["item_count"]["ok"] is True
    assert checks["tax_10"]["ok"] is True             # 内税: 1639 × 10/110 = 149
    assert review == "ok"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("(@132×2個)", (2, 132)),      # 単価が先
        ("(@132×2个)", (2, 132)),      # 「個」の誤読
        ("(0110× 2个)", (2, 110)),     # 「@」が「0」に
        ("(@165×3個)", (3, 165)),
        ("( 2個 × @219 )", (2, 219)),  # 数量が先（ロピアの型）
        ("(2個×@1,090)", (2, 1090)),   # 桁区切り
        ("(3点 1回", None),            # 値引きの内訳行
        ("ギュウニュウ", None),
        ("¥264内", None),
    ],
)
def test_parse_qty_row_table(text: str, expected: tuple[int, int] | None) -> None:
    assert rp.parse_qty_row(text) == expected


def test_inclusive_tax_check_uses_tax_included_formula() -> None:
    """内税では 税額 = 対象額 × 率/(100+率)。外税の式（×率/100）で落とさない。"""
    d = rc.normalize_draft({
        "store": {"name": "内税の店"}, "purchased_at": "2026-09-20", "tax_mode": "inclusive",
        "items": [{"name": "A", "amount": 1100, "tax_rate": 10}],
        "subtotal": 1100, "total": 1100, "taxes": [{"rate": 10, "amount": 100}],
    })
    checks = rc.run_checks(rc.derive_missing(d))
    assert checks["tax_10"] == {"ok": True, "expected": 100, "actual": 100}


def test_inclusive_single_rate_is_applied_to_items() -> None:
    """税率が 1 つだけ宣言され、明細に税率が無いとき（飲食店）は、その税率を明細に当てる。"""
    d = rc.derive_missing(rc.normalize_draft({
        "store": {"name": "内税の店"}, "purchased_at": "2026-09-20", "tax_mode": "inclusive",
        "items": [{"name": "A", "amount": 2200}, {"name": "B", "amount": 2420}],
        "subtotal": 4620, "total": 4620, "taxes": [{"rate": 10, "amount": 420}],
    }))
    assert [i["tax_rate"] for i in d["items"]] == [10, 10]
    assert rc.run_checks(d)["tax_10"]["ok"] is True


# --- さらに別の型（喫茶店。2026-09-23 主人の3枚目の実物から） --------------------------------
#
# 取りこぼしていた点: ⓐ明細の見出し「商品名 数量 合計」の `合計` を合計欄と取り違えて明細が 0 行
# ⓑ小計の**後**に引かれる値引き（`JAF5% -148`）が明細に入らない ⓒ検算が「Σ明細（値引き込み）＝小計」
# しか知らない（この店は 小計＝値引き前・合計＝小計−値引き） ⓓ「数量」の列の数字が品名に混ざる
# ⓔ `(内税 ¥256)` を税額として拾えない ⓕ店名が `卓番:28`（一番大きい字）になる


def synthetic_cafe_receipt() -> list[dict]:
    """内税・列の見出しあり・小計の後の値引き。1,595 + 1,375 = 2,970、JAF −148、合計 2,822（内税 256）。"""
    rows: list[dict] = []
    y = 100.0

    def line(cells: list[tuple[float, float, str]], h: float = 34.0) -> None:
        nonlocal y
        for x, w, text in cells:
            rows.append(_box(x, y, w, h, text))
        y += h + 16

    line([(300, 220, "ネコ珈琲店 各務原店")])
    line([(300, 260, "岐阜県各務原市那加緑町1丁目21-1")])
    line([(300, 230, "TEL.0583-71-2434")])
    line([(300, 330, "企業登録番号:T4011801013144")])
    line([(300, 150, "ご利用明細")])
    line([(300, 300, "毎度ありがとうございます。")])
    line([(300, 140, "卓番:28")], h=80)          # 一番大きい字だが店名ではない
    line([(300, 180, "人数:2名様")])
    line([(300, 330, "日時:2026/09/22 22:18")])
    line([(300, 190, "担当:管理者")])
    line([(300, 140, "商品名"), (640, 110, "数量"), (820, 110, "合計")])   # 列の見出し
    price_x = 820
    line([(300, 330, "モンブランパフェ"), (660, 40, "1"), (price_x, 130, "1,595")])
    line([(300, 260, "プリンパフェ"), (660, 40, "2"), (price_x, 130, "1,375")])
    line([(300, 120, "小計"), (price_x, 140, "2,970")])
    line([(300, 150, "JAF5%"), (price_x + 20, 120, "-148")])
    line([(320, 260, "税率10%対象"), (price_x, 140, "2,822")])
    line([(320, 200, "(税率10%"), (price_x + 20, 120, "256)")])
    line([(300, 140, "合計"), (price_x - 60, 200, "¥2,822")], h=44)
    line([(300, 130, "(内税"), (price_x + 10, 150, "¥256)")])
    line([(300, 150, "カード"), (price_x - 20, 180, "¥2,822")])
    line([(300, 160, "お預り"), (price_x - 20, 180, "¥2,822")])
    line([(300, 160, "お釣り"), (price_x + 60, 80, "¥0")])
    line([(300, 420, "No.26092200100112")])
    return rows


def test_cafe_receipt_with_discount_after_subtotal() -> None:
    raw = rp.parse_boxes(synthetic_cafe_receipt())
    d, checks, review = _finish(raw)
    assert d["store"]["name"] == "ネコ珈琲店 各務原店"     # ⓕ札・住所・挨拶は店名にしない
    assert d["store"]["registration_number"] == "T4011801013144"
    assert d["purchased_at"] == "2026-09-22T22:18"
    assert d["tax_mode"] == "inclusive"
    assert d["subtotal"] == 2970 and d["total"] == 2822   # ⓐ見出しの `合計` を取り違えない
    assert d["taxes"] == [{"rate": 10, "amount": 256}]    # ⓔ `(内税 ¥256)`
    assert d["payment_method"] == "credit"
    assert [(i["name"], i["qty"], i["amount"], i["is_discount"]) for i in d["items"]] == [
        ("モンブランパフェ", 1, 1595, False),
        ("プリンパフェ", 2, 1375, False),                  # ⓓ「数量」の列を数量として読む
        ("JAF5%", 1, -148, True),                          # ⓑ小計の後の値引き
    ]
    assert checks["items_sum"] == {"ok": True, "expected": 2970, "actual": 2970}   # ⓒ小計＝値引き前
    assert checks["total"] == {"ok": True, "expected": 2822, "actual": 2822}       #    合計＝小計−値引き
    assert checks["tax_10"]["ok"] is True and checks["change"]["ok"] is True
    assert review == "ok"


def test_checks_accept_both_discount_conventions() -> None:
    """値引きが明細の中にある店（小計＝値引き後）も、小計の後に引く店も、同じ検算で通る。"""
    inside = rc.derive_missing(rc.normalize_draft({
        "store": {"name": "店"}, "purchased_at": "2026-09-20", "tax_mode": "inclusive",
        "items": [{"name": "A", "amount": 1000}, {"name": "値引き", "amount": -100, "is_discount": True}],
        "subtotal": 900, "total": 900,
    }))
    after = rc.derive_missing(rc.normalize_draft({
        "store": {"name": "店"}, "purchased_at": "2026-09-20", "tax_mode": "inclusive",
        "items": [{"name": "A", "amount": 1000}, {"name": "値引き", "amount": -100, "is_discount": True}],
        "subtotal": 1000, "total": 900,
    }))
    for d in (inside, after):
        checks = rc.run_checks(d)
        assert checks["items_sum"]["ok"] is True, checks
        assert checks["total"]["ok"] is True, checks
        assert rc.review_from_checks(d, checks) == "ok"


def test_store_name_candidate_filters() -> None:
    assert rp._store_name_candidate("ネコ珈琲店 各務原店")
    assert not rp._store_name_candidate("卓番:28")              # 札
    assert not rp._store_name_candidate("岐阜県各務原市那加緑町")  # 住所
    assert not rp._store_name_candidate("1丁目21-1")
    assert not rp._store_name_candidate("ご利用明細")            # 見出し
    assert not rp._store_name_candidate("毎度ありがとうございます。")


# --- コンビニ（軽減税率の印・合計の字が落ちる・下にカード控え） ----------------------------
# 2026-09-24 のファミマで踏んだ形を合成で再現する。踏んだ穴は4つ:
# ①`¥198軽` が価格として読めない ②`軽` を 8% と見ない ③`クレジット支払` の誤読が
# ヘッダの「レジ」に当たって明細が範囲の外へ ④カード控えの日付・金額が本体に混ざる。
# ⑤ついでに見つけた桁落ち（`¥171` → 17）もここで見張る。


def synthetic_convenience_receipt() -> list[dict]:
    """軽減税率 8% の 2 品（198＋171＝369）。**合計の字は読めなかった**ことにして
    金額の行だけ置き、下にカード控えを付ける。"""
    rows: list[dict] = []
    y = 100.0

    def line(cells: list[tuple[float, float, str]], h: float = 30.0) -> None:
        nonlocal y
        for x, w, text in cells:
            rows.append(_box(x, y, w, h, text))
        y += h + 14

    price_x = 700
    line([(300, 240, "ファミリーマート")])
    line([(300, 220, "○○大学前店")])
    line([(300, 200, "電話:052-788-7067")])
    line([(300, 300, "登録番号:T2180001041412")])
    line([(300, 260, "2026年9月24日(木)15:57")])
    line([(300, 150, "レジ4-6200"), (620, 140, "責No.999")])
    line([(300, 160, "領収証")])
    line([(300, 200, "三三三三二三三二")])                       # `==========` の誤読
    line([(300, 260, "手巻シーチキンマヨネ"), (price_x, 120, "¥198軽")])
    line([(300, 240, "ジャスミン茶950"), (price_x, 120, "¥171軽")])
    line([(price_x, 120, "¥369")])                              # 「合　計」の字が落ちた行
    line([(300, 120, "对集"), (price_x, 120, "¥369")])           # 「8%対象」の誤読
    line([(300, 200, "(内消費税等"), (price_x, 100, "¥27")])
    line([(300, 180, "ルレジッ支払"), (price_x, 120, "¥369")])   # 「クレジット支払」の誤読
    line([(300, 420, "「軽」は軽減税率対象商品です。")])
    # ここから下はカード控え（買った物ではない）
    line([(300, 150, "取引日"), (620, 220, "2026年9月24日")])
    line([(300, 100, "CL"), (620, 140, "46930")])
    line([(620, 140, "VISA")])          # 控えを切り離しても支払方法は拾えること
    line([(300, 180, "承認番号"), (620, 160, "778243")])
    line([(300, 180, "支払区分"), (620, 100, "一括")])
    line([(300, 180, "支払金額"), (620, 120, "¥369")])
    line([(300, 100, "AID"), (620, 260, "A0000000031010")])
    line([(300, 200, "お客様控え")])
    return rows


def test_convenience_receipt_reads_items_and_total() -> None:
    """軽減税率の印つきの明細が取れ、合計の字が無くても明細の和から合計が立つこと。"""
    d, checks, review = _finish(rp.parse_boxes(synthetic_convenience_receipt()))

    assert [i["name"] for i in d["items"]] == ["手巻シーチキンマヨネ", "ジャスミン茶950"]
    assert [i["amount"] for i in d["items"]] == [198, 171]     # ⑤ 171 が 17 にならない
    assert [i["tax_rate"] for i in d["items"]] == [8, 8]       # ② `軽` は 8%
    assert d["total"] == 369                                   # 合計の字が無くても和から立つ
    assert d["purchased_at"] == "2026-09-24T15:57"             # ④ 控えの日付を買った日にしない
    assert d["payment_method"] == "credit"
    assert checks["items_sum"]["ok"] is True
    assert review == "ok"


def test_convenience_receipt_drops_the_card_slip() -> None:
    """カード控え（取引日・承認番号・支払区分・AID）は明細にも合計にも混ざらないこと。"""
    d, _checks, _review = _finish(rp.parse_boxes(synthetic_convenience_receipt()))
    names = " ".join(i["name"] for i in d["items"])
    for w in ("支払区分", "承認番号", "AID", "取引日", "お客様控"):
        assert w not in names, f"控えの「{w}」が明細に混ざっています"
    assert all(i["amount"] in (198, 171) for i in d["items"])


def test_price_keeps_its_last_digit() -> None:
    """⑤ 末尾が 1 の金額が 1 桁落ちない（`¥171` → 17 になっていた。2026-09-24）。"""
    for text, want in (("¥171", 171), ("¥211", 211), ("¥1,091", 1091), ("¥171軽", 171), ("¥300外", 300)):
        assert rp._price_from(rp.normalize_text(text)) == want, text


def test_register_keyword_does_not_match_credit_payment() -> None:
    """③「クレジット支払」の誤読がヘッダの「レジ」に当たらないこと。"""
    assert rp._REGISTER_RE.search("レジ4-6200")
    assert not rp._REGISTER_RE.search("ルレジッ支払")
    assert not rp._REGISTER_RE.search("クレジット支払")
