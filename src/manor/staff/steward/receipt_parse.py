"""OCR の箱（四角＋文字）から、規則だけでレシートの下書きを組む（ADR-020 D4）。純粋関数。

入力: `[{"box": [[x,y],[x,y],[x,y],[x,y]], "text": str, "score": float}, ...]`（RapidOCR の形。
どの OCR でもこの形に揃えれば使える）。出力: `receipt_checks.normalize_draft` に渡せる辞書。

段取り:
1. 四角の角度の中央値で座標を回し（傾き・90° 回転）、各箱の中心 y・左右端・高さを持つ
2. 右端の `¥N` の箱を**価格の錨**にし、縦に最も重なる左の箱を品名にする
3. `外8/外10/※/軽` を税率、`(N個×@P)` を数量、負の金額を値引き、合計欄のキーワードを見る
4. 店名・登録番号・日時・電話・支払方法をヘッダの箱から拾う

店舗別テンプレートは持たない。**誤読は正規化表（`lexicon.toml` `[ocr_normalize]`）と、ここの
正規表現の揺れ（`¥` が `早/半/4` に、`外` が `夕/タト/卜` に）で吸収する。**
"""

from __future__ import annotations

import math
import re
import statistics
import unicodedata
from pathlib import Path
from typing import Any

_LEXICON_PATH = Path(__file__).resolve().parent / "lexicon.toml"
_lexicon_cache: dict[str, Any] | None = None


def lexicon() -> dict[str, Any]:
    global _lexicon_cache
    if _lexicon_cache is None:
        import tomllib

        with _LEXICON_PATH.open("rb") as f:
            _lexicon_cache = tomllib.load(f)
    return _lexicon_cache


# --- 文字の正規化 --------------------------------------------------------------------

#: 価格の後ろに付く「外」「内」の誤読と、閉じ括弧など。
_PRICE_TRAIL_RE = re.compile(r"[外内夕タト卜ﾀﾄ\+!1)）」ł十†lt\]]+$")
#: 価格の前に付く「¥」の誤読。
_PRICE_LEAD_RE = re.compile(r"^[¥￥半早平4ギやY\\]\s*")
_PRICE_CORE_RE = re.compile(r"^(-?\d{1,3}(?:[,.]\d{3})+|-?\d{1,6})$")
#: 品名の箱の末尾に価格が混ざった形（`カゴメ…100 マンゴーサ ¥217外`）。
_INLINE_PRICE_RE = re.compile(r"[¥￥]\s*(-?\d{1,3}(?:[,.]\d{3})+|-?\d{1,6})\s*[外内夕タト卜ﾀﾄ\+!1)）ł十†lt\]]*$")
#: 数量行。店によって「2個 × @219」（数量が先）と「(@132 × 2個)」（単価が先）の両方がある。
#: `@` は `0` に、`個` は `个` に誤読されやすいので、どちらの並びでも拾えるようにする。
_QTY_ROW_RE = re.compile(
    r"^[（(]?\s*(?P<n1>[@＠0]?\s*\d{1,3}(?:[,.]\d{3})+|[@＠0]?\s*\d{1,6})\s*(?P<u1>[個个箇点])?"
    r"\s*[×xX*✕]\s*(?P<n2>[@＠0]?\s*\d{1,3}(?:[,.]\d{3})+|[@＠0]?\s*\d{1,6})\s*(?P<u2>[個个箇点])?\s*[）)]?$"
)


def parse_qty_row(text: str) -> tuple[int, int] | None:
    """「数量 × 単価」の行なら `(数量, 単価)`。そうでなければ None。

    どちらの数が数量かは、①`個`（`个`）が付いているほう ②`@`（`0` の誤読を含む）が付いていないほう
    ③小さいほう、の順で決める。`@` の誤読で単価に付く先頭の `0` は落とす（`0110` → `110`）。
    """
    m = _QTY_ROW_RE.match(text.replace(" ", ""))
    if not m:
        return None

    def _num(s: str) -> tuple[int, bool]:
        """(値, @ が付いていたか)。"""
        raw = s.replace(",", "").replace(".", "")
        marked = bool(re.match(r"^[@＠]", raw))
        raw = re.sub(r"^[@＠]", "", raw)
        if len(raw) > 1 and raw.startswith("0"):  # `@` が `0` に読まれた
            raw = raw.lstrip("0") or "0"
            marked = True
        return (int(raw or 0), marked)

    (v1, at1), (v2, at2) = _num(m.group("n1")), _num(m.group("n2"))
    if not v1 or not v2:
        return None
    if m.group("u1") and not m.group("u2"):
        qty, unit = v1, v2
    elif m.group("u2") and not m.group("u1"):
        qty, unit = v2, v1
    elif at1 != at2:
        qty, unit = (v2, v1) if at1 else (v1, v2)
    else:
        qty, unit = (v1, v2) if v1 <= v2 else (v2, v1)
    if qty <= 0 or qty > 999 or unit <= 0:
        return None
    return qty, unit
_TAXMARK_RE = re.compile(r"^[外内夕タ卜ト※軽*＊]+\s*(8|10|18)\b")
#: 税印だけの箱（`外8` `外10` と、その誤読 `548` `58` `5外48` `外18`）。
_TAXMARK_ONLY_RE = re.compile(r"^[外内夕タ卜ト※軽*＊5\d]{1,5}$")


def _taxmark_rate(text: str) -> int | None:
    """税印だけの箱なら税率（8/10）、そうでなければ None。"""
    t = text.replace(" ", "")
    if not t or not _TAXMARK_ONLY_RE.match(t):
        return None
    if not any(ch in t for ch in "外内夕タ卜ト※軽*＊") and not t.startswith("5"):
        return None  # ただの数字（価格やレジ番号）は税印ではない
    if "10" in t:
        return 10
    if "8" in t:
        return 8
    return None
_DATE_RE = re.compile(r"(20\d{2})\s*[年/\-.]\s*(\d{1,2})\s*[月/\-.]\s*(\d{1,2})\s*日?")
_TIME_RE = re.compile(r"(\d{1,2})\s*[:：]\s*(\d{2})")
_TEL_RE = re.compile(r"(0\d{1,4}[-‐−]\d{1,4}[-‐−]\d{3,4})")
#: 登録番号。`T1234567890123` と `T8-0600-0100-1562` の両方。区切りは後で落とす。
_REG_RE = re.compile(r"[T1lI7](\s?[\d\-‐−–—\s]{13,22})")
_RECEIPT_NO_RE = re.compile(r"[#＃]\s?(\d{4,8})")

#: 合計欄の鍵（正規化後の文字に当てる正規表現。上から順）。「税額」の誤読（税客・税額頁）も吸う。
_TOTAL_KEYS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("tax8", re.compile(r"(?:外|内)?(?:消費)?税[額客頁等]*\s*8(?!\d)")),
    ("tax10", re.compile(r"(?:外|内)?(?:消費)?税[額客頁等]*\s*10(?!\d)")),
    ("item_count", re.compile(r"点数|買上点|買上")),
    ("tax_included", re.compile(r"^[（(]?\s*内税|^[（(]?\s*消費税[額客頁]?$|^[（(]?\s*税[額客頁]$")),
    ("subtotal", re.compile(r"小計|小駄|小言十|小計十")),
    ("tendered", re.compile(r"お預|預り|預かり|おり$|お豹|予貢り|予買り|お預り金|お頭|お項")),
    ("change", re.compile(r"お釣|釣り|おつり|釣")),
    ("total", re.compile(r"合計|合十|盒言十|急言十|合言十|総合計|十晨")),
)
#: 合計欄の内訳行（税率ごとの対象額など）。合計として拾わない。
_SKIP_WORDS: tuple[str, ...] = ("対象", "対額", "消費税等", "内消", "税率", "うち", "内訳", "税抜", "税込額")
_PAYMENT_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("credit", ("クレジット", "カード", "VISA", "MASTER", "JCB", "AMEX")),
    ("qr", ("PAYPAY", "PayPay", "ペイペイ", "楽天ペイ", "d払い", "AUPAY", "auPAY", "LINEPAY", "メルペイ", "QR")),
    ("ic", ("SUICA", "Suica", "PASMO", "ICOCA", "MANACA", "manaca", "TOICA", "NANACO", "nanaco", "WAON", "EDY", "Edy", "交通系", "電子マネー", "IC")),
    ("cash", ("現金", "お預り", "お釣り", "釣")),
)


_DAKUTEN_MARKS = "`´゛ﾞ゙"
_HANDAKUTEN_MARKS = "°゜ﾟ゚"


def _compose_kana(t: str) -> str:
    """OCR が分けて出した濁点・半濁点（バッククォートや ° で出る）を合成する。合成できない位置の印は消す。"""
    out: list[str] = []
    for ch in t:
        if ch in _DAKUTEN_MARKS or ch in _HANDAKUTEN_MARKS:
            if out:
                mark = "゙" if ch in _DAKUTEN_MARKS else "゚"
                composed = unicodedata.normalize("NFC", out[-1] + mark)
                if len(composed) == 1:
                    out[-1] = composed
                    continue
            continue
        out.append(ch)
    return "".join(out)


#: 店名にならない行（見出し・挨拶・札）。住所は別に正規表現で落とす。
_NOT_STORE_WORDS: tuple[str, ...] = (
    "明細", "領収", "控", "ありがとう", "いらっしゃい", "お買上", "お買い上げ", "レシート", "証",
)
_ADDRESS_RE = re.compile(r"丁目|番地|[都道府県].*?[市区町村]|\d{3}-\d{4}")


def _store_name_candidate(text: str) -> bool:
    """店名になりうる行か。`卓番:28` のような札、住所、挨拶・見出しを外す。"""
    t = text.replace(" ", "")
    if len(re.sub(r"[^\w]", "", t)) < 2:
        return False
    if ":" in t or "：" in t:
        return False
    if any(w in t for w in _NOT_STORE_WORDS) or _ADDRESS_RE.search(t):
        return False
    return not re.fullmatch(r"[\d\-‐−–—./]+", t)


def _kana_skeleton(s: str) -> str:
    """濁点・半濁点を落としたカタカナの骨格（`クレジット` → `クレシツト`。小書きも大書きに）。"""
    out = []
    for ch in unicodedata.normalize("NFD", s):
        if ch in ("\u3099", "\u309a"):
            continue
        out.append(ch)
    text = unicodedata.normalize("NFC", "".join(out))
    return text.translate(str.maketrans("ァィゥェォッャュョヮ", "アイウエオツヤユヨワ"))


def _contains_fuzzy(text: str, word: str) -> bool:
    """`word` が `text` に（カタカナなら濁点と 1 文字の誤読を許して）含まれるか。
    `クレジット` を `クルシット` と読んでも当たるようにする（OCR の実測）。"""
    if word in text:
        return True
    if len(word) < 4 or not all("ア" <= ch <= "ヶ" or ch == "ー" for ch in word):
        return False
    w = _kana_skeleton(word)
    t = _kana_skeleton(text)
    if w in t:
        return True
    for i in range(len(t) - len(w) + 1):
        window = t[i : i + len(w)]
        if sum(1 for a, b in zip(w, window) if a != b) <= 1:
            return True
    return False


def normalize_text(text: str) -> str:
    """NFKC → 濁点の合成 → 語彙の置換表 → 空白を詰める。"""
    t = unicodedata.normalize("NFKC", str(text or ""))
    t = _compose_kana(t)
    for src, dst in (lexicon().get("ocr_normalize") or {}).items():
        t = t.replace(src, dst)
    return re.sub(r"[\s　]+", " ", t).strip()


def _price_from(text: str) -> int | None:
    """箱の文字が「価格だけ」なら整数に。`¥1,090外` `半299タト` `-17)` など。"""
    t = _PRICE_TRAIL_RE.sub("", _PRICE_LEAD_RE.sub("", text.replace(" ", "")))
    m = _PRICE_CORE_RE.match(t)
    if not m:
        return None
    return int(m.group(1).replace(",", "").replace(".", ""))


def _int(s: str) -> int:
    return int(s.replace(",", "").replace(".", ""))


# --- 箱の幾何 --------------------------------------------------------------------------


def _prepare(boxes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float]:
    """四角の角度の中央値で座標を回し、中心 y・左右端・高さを付ける。戻り値の角度は度。"""
    out: list[dict[str, Any]] = []
    angles: list[float] = []
    for b in boxes:
        pts = b.get("box") or []
        if len(pts) != 4:
            continue
        # 四角の長いほうの辺を文字の並ぶ向きとみなす（90° 回転した写真では短辺が先に来ることがある）
        (x0, y0), (x1, y1), (x2, y2) = pts[0], pts[1], pts[2]
        e1 = math.hypot(x1 - x0, y1 - y0)
        e2 = math.hypot(x2 - x1, y2 - y1)
        if max(e1, e2) > 40:
            if e1 >= e2:
                angles.append(math.atan2(y1 - y0, x1 - x0))
            else:
                angles.append(math.atan2(y2 - y1, x2 - x1))
    if angles:
        # 90° 回転の写真では角度が ±π/2 付近で割れる（+89° と −91°）ので、2 倍角で平均してから戻す。
        sx = sum(math.cos(2 * a) for a in angles)
        sy = sum(math.sin(2 * a) for a in angles)
        angle = 0.5 * math.atan2(sy, sx)
    else:
        angle = 0.0
    c, s = math.cos(-angle), math.sin(-angle)
    for b in boxes:
        pts = b.get("box") or []
        if len(pts) != 4:
            continue
        rot = [(x * c - y * s, x * s + y * c) for x, y in pts]
        xs = [p[0] for p in rot]
        ys = [p[1] for p in rot]
        out.append(
            {
                "text": normalize_text(str(b.get("text") or "")),
                "raw": str(b.get("text") or ""),
                "score": float(b.get("score") or 0.0),
                "x0": min(xs),
                "x1": max(xs),
                "y0": min(ys),
                "y1": max(ys),
                "cy": (min(ys) + max(ys)) / 2,
                "h": max(ys) - min(ys),
            }
        )
    out = [b for b in out if b["text"]]
    # 上下が逆（合計欄がヘッダより上）なら y を反転する（90° の回転方向を取り違えたとき）。
    if out:
        date_y = [b["cy"] for b in out if _DATE_RE.search(b["text"])]
        total_y = [b["cy"] for b in out if any(w in b["text"] for w in ("小計", "合計", "お釣"))]
        if date_y and total_y and min(date_y) > max(total_y):
            top = max(b["y1"] for b in out)
            for b in out:
                b["y0"], b["y1"] = top - b["y1"], top - b["y0"]
                b["cy"] = (b["y0"] + b["y1"]) / 2
            # 左右も反転している
            right = max(b["x1"] for b in out)
            for b in out:
                b["x0"], b["x1"] = right - b["x1"], right - b["x0"]
    out.sort(key=lambda b: (b["cy"], b["x0"]))
    return out, math.degrees(angle)


def _overlap(a: dict[str, Any], b: dict[str, Any]) -> float:
    """縦の重なり（小さい方の高さに対する割合）。"""
    inter = min(a["y1"], b["y1"]) - max(a["y0"], b["y0"])
    denom = max(1.0, min(a["h"], b["h"]))
    return inter / denom


# --- 本体 ------------------------------------------------------------------------------


def parse_boxes(boxes: list[dict[str, Any]]) -> dict[str, Any]:
    """OCR の箱 → 下書き（`receipt_checks.normalize_draft` に渡す前の生の辞書）。
    `_parse` の値に `parse_notes`（規則が迷った箇所の符号）を付けて返す。"""
    items_boxes, angle = _prepare(boxes)
    draft: dict[str, Any] = {
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
        "parse": {"angle": round(angle, 2), "boxes": len(items_boxes), "notes": []},
    }
    if not items_boxes:
        return draft
    h_med = statistics.median(b["h"] for b in items_boxes)

    # 価格の箱と、その列の右端
    for b in items_boxes:
        b["price"] = _price_from(b["text"])
        b["taxmark"] = _taxmark_rate(b["text"])
        b["is_taxmark"] = b["taxmark"] is not None
        if b["is_taxmark"]:
            b["price"] = None
    price_boxes = [b for b in items_boxes if b["price"] is not None]
    if price_boxes:
        x_right = statistics.median(b["x1"] for b in price_boxes)
        for b in price_boxes:
            if b["x1"] < x_right - 4 * h_med:
                b["price"] = None  # 左のほうにある数字（レジ番号・時刻など）は価格ではない
    else:
        x_right = max(b["x1"] for b in items_boxes)

    # --- ヘッダ・合計欄・支払 ---------------------------------------------------------
    total_rows: dict[str, dict[str, Any]] = {}
    first_total_y: float | None = None
    header_lines: list[dict[str, Any]] = []
    all_text = " ".join(b["text"] for b in items_boxes)

    for b in items_boxes:
        t = b["text"].replace(" ", "")
        m = _DATE_RE.search(t)
        if m and draft["purchased_at"] is None:
            y, mo, da = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1 <= mo <= 12 and 1 <= da <= 31:
                draft["purchased_at"] = f"{y:04d}-{mo:02d}-{da:02d}"
                tm = _TIME_RE.search(t[m.end():])
                if tm and int(tm.group(1)) < 24:
                    draft["purchased_at"] += f"T{int(tm.group(1)):02d}:{tm.group(2)}"
                draft["_date_y"] = b["cy"]
        if "登録番号" in t or "登绿番号" in t or re.search(r"[T1]\d{13}", t):
            for mr in _REG_RE.finditer(t):
                digits = re.sub(r"\D", "", mr.group(1))
                if len(digits) == 13 and draft["store"]["registration_number"] is None:
                    draft["store"]["registration_number"] = "T" + digits
                    break
        mt = _TEL_RE.search(t)
        if mt and draft["store"]["tel"] is None:
            draft["store"]["tel"] = mt.group(1).replace("‐", "-").replace("−", "-")
        mn = _RECEIPT_NO_RE.search(t)
        if mn and draft["receipt_no"] is None:
            draft["receipt_no"] = mn.group(1)

    date_y = draft.pop("_date_y", None)
    header_end = date_y if date_y is not None else items_boxes[0]["cy"]
    for b in items_boxes:
        t = b["text"].replace(" ", "")
        if b["cy"] - header_end > 6 * h_med:
            continue
        if (
            _DATE_RE.search(t) or _REG_RE.search(t) or _RECEIPT_NO_RE.search(t) or _TEL_RE.search(t)
            or re.fullmatch(r"\d{1,2}:\d{2}", t) or re.fullmatch(r"[R#]?\d{4,8}", t)
            or any(w in t for w in ("精算機", "レジ", "責任者", "担当", "お会計券", "会計券", "登録番号", "No.",
                                    "伝票", "テーブル", "卓", "人数", "領収", "領収証", "領収書"))
            or re.fullmatch(r"\d{1,2}[名様人]", t) or re.fullmatch(r"[^\d:：]{1,5}[:：]\S{1,12}", t)
        ):
            header_end = max(header_end, b["cy"])
    # 店名: 日付・登録番号より上の、数字でない行から。語彙の店名に当たればそれ。
    top_limit = date_y if date_y is not None else items_boxes[0]["cy"] + 6 * h_med
    for b in items_boxes:
        if b["cy"] >= top_limit:
            break
        t = b["text"]
        if _DATE_RE.search(t) or _TEL_RE.search(t) or re.search(r"\d{6,}", t):
            continue
        if len(re.sub(r"[^\w]", "", t)) < 2:
            continue
        header_lines.append(b)
    store_names: dict[str, str] = lexicon().get("store_names") or {}
    for b in header_lines:
        for frag, canon in store_names.items():
            if frag in b["text"]:
                draft["store"]["name"] = canon
                break
        if draft["store"]["name"]:
            break
    if not draft["store"]["name"] and header_lines:
        # 店名はたいてい**一番大きい字**（ロゴの下の店名）。目立つ行が無ければ、最も文字らしい行。
        named = [b for b in header_lines if _store_name_candidate(b["text"])]
        big = [b for b in named if b["h"] >= 1.4 * h_med]
        if big:
            cand = max(big, key=lambda b: (b["h"], b["score"]))
        elif named:
            cand = max(named, key=lambda b: len(re.sub(r"[^\w]", "", b["text"])) * b["score"])
        else:
            cand = header_lines[0]
        draft["store"]["name"] = cand["text"][:40]
    for b in header_lines:
        if "店" in b["text"] and b["text"] != draft["store"]["name"]:
            draft["store"]["branch"] = b["text"].replace(draft["store"]["name"], "").strip("・ ")[:30] or None
            break

    # 合計欄: キーワードの箱と、同じ行（縦の重なりが最大）の価格。無ければ次の行の価格（大きい字は行がずれる）。
    for b in items_boxes:
        t = b["text"].replace(" ", "")
        if b["cy"] <= header_end:
            continue  # ヘッダ（「お会計券」など）は合計欄ではない
        if any(w in t for w in _SKIP_WORDS) and not any(w in t for w in ("合計", "小計")):
            continue
        key = None
        for k, pat in _TOTAL_KEYS:
            if pat.search(t):
                key = k
                break
        if key is None or key in total_rows:
            continue
        if key == "total" and ("小計" in t or "小駄" in t):
            continue
        # 同じ行の右側の価格（相手の箱の高さに対する縦の重なりが最大のもの）
        mates = []
        for p in price_boxes:
            if p is b or p["price"] is None or p["x0"] <= b["x0"]:
                continue
            ov = (min(p["y1"], b["y1"]) - max(p["y0"], b["y0"])) / max(1.0, p["h"])
            if ov > 0.5:
                mates.append((ov, p))
        value: int | None = None
        if mates:
            mates.sort(key=lambda m: (-m[0], -m[1]["x1"]))
            value = mates[0][1]["price"]
        elif key == "item_count":
            for p in items_boxes:
                if p is not b and p["x0"] > b["x0"] and _overlap(p, b) > 0.3:
                    mc = re.match(r"^(\d+)点$", p["text"].replace(" ", ""))
                    if mc:
                        value = int(mc.group(1))
                        break
        else:
            inline = _INLINE_PRICE_RE.search(b["text"])
            if inline:
                value = _int(inline.group(1))
            elif key == "item_count":
                mc = re.search(r"(\d+)\s*点", t)
                value = int(mc.group(1)) if mc else None
        if value is None and key in ("total", "tendered", "change"):
            below = [
                p for p in price_boxes
                if p["price"] is not None and 0 < p["cy"] - b["cy"] < 1.6 * max(b["h"], h_med) and p["x0"] > b["x1"] - h_med
            ]
            if below:
                value = min(below, key=lambda p: p["cy"] - b["cy"])["price"]
        if key == "item_count" and value is None:
            mc = re.search(r"(\d{1,3})点(?![\s\d]*回)", all_text.replace(" ", "")[int(len(all_text) * 0.5):])
            value = int(mc.group(1)) if mc else None
        if value is None:
            # 値の無い行は合計欄ではない——明細の見出し「商品名 数量 合計」の `合計` を
            # 取り違えると、そこで明細が終わってしまう（キャッツカフェのレシートで実測）。
            continue
        value_box = mates[0][1] if mates else None
        total_rows[key] = {"box": b, "value": value, "value_box": value_box}
        for box in (b, value_box):
            if box is not None and (first_total_y is None or box["cy"] < first_total_y):
                first_total_y = box["cy"]

    draft["parse"]["totals"] = {k: [r["box"]["text"], r["value"]] for k, r in total_rows.items()}
    for key in ("subtotal", "total", "item_count", "tendered", "change"):
        if key in total_rows and total_rows[key]["value"] is not None:
            draft[key] = total_rows[key]["value"]
    for key, rate in (("tax8", 8), ("tax10", 10)):
        if key in total_rows and total_rows[key]["value"] is not None:
            draft["taxes"].append({"rate": rate, "amount": total_rows[key]["value"]})
    # 税率の指定が無い「内税額 ¥420」: 合計欄に税率が 1 つだけ出ていれば、その税率の税額とする
    if not draft["taxes"] and "tax_included" in total_rows and total_rows["tax_included"]["value"] is not None:
        totals_text = "".join(b["text"] for b in items_boxes if first_total_y is not None and b["cy"] >= first_total_y - h_med)
        rates = {int(r) for r in re.findall(r"(\d{1,2})\s*%", totals_text) if r in ("8", "10")}
        if len(rates) == 1:
            draft["taxes"].append({"rate": rates.pop(), "amount": total_rows["tax_included"]["value"]})
    # 「小計 27点 ¥4,620」のように合計欄の行の中に買上点数があることがある
    if draft["item_count"] is None:
        for key in ("subtotal", "total"):
            row = total_rows.get(key)
            if not row:
                continue
            for p in items_boxes:
                if p is row["box"] or _overlap(p, row["box"]) <= 0.3:
                    continue
                mc = re.fullmatch(r"(\d{1,3})点", p["text"].replace(" ", ""))
                if mc:
                    draft["item_count"] = int(mc.group(1))
                    break
            if draft["item_count"] is not None:
                break

    # 位置で補う: 合計の行より下の価格で 合計以上 のものは お預り、お預り−合計 に等しいものは お釣り。
    # 合計が無ければ、小計の行より下の**大きい字**の価格（小計以上）を合計とみなす。
    if first_total_y is not None:
        used_vals = {r["value"] for r in total_rows.values() if r["value"] is not None}
        subtotal_v = draft.get("subtotal")
        if draft["total"] is None:
            big = [
                p for p in price_boxes
                if p["price"] is not None and p["cy"] > first_total_y and p["h"] > 1.2 * h_med
                and p["price"] not in used_vals and (subtotal_v is None or p["price"] >= subtotal_v)
            ]
            if big:
                draft["total"] = min(big, key=lambda p: p["cy"])["price"]
                notes_pos = draft["parse"].setdefault("notes", [])
                notes_pos.append("total_by_position")
        if draft["total"] is not None:
            total_row_y = total_rows["total"]["box"]["cy"] if "total" in total_rows else first_total_y
            below = [p for p in price_boxes if p["price"] is not None and p["cy"] > total_row_y and p["price"] != draft["total"]]
            if draft["tendered"] is None:
                cands = [p for p in below if p["price"] >= draft["total"] and p["price"] not in used_vals]
                if cands:
                    draft["tendered"] = min(cands, key=lambda p: p["cy"])["price"]
            if draft["change"] is None and draft["tendered"] is not None:
                want = draft["tendered"] - draft["total"]
                if any(p["price"] == want for p in below):
                    draft["change"] = want
            elif draft["change"] is not None and draft["tendered"] is not None and draft["tendered"] - draft["total"] != draft["change"]:
                # お釣りの取り違え（内訳行の数字を拾った）: お預り−合計 に等しい価格があればそちら
                want = draft["tendered"] - draft["total"]
                if any(p["price"] == want for p in below):
                    draft["change"] = want

    if "内税" in all_text or "内消費税" in all_text and "外税" not in all_text and "外8" not in all_text:
        draft["tax_mode"] = "inclusive" if "外税" not in all_text and "外8" not in all_text and "外10" not in all_text else "exclusive"
    if "外税" in all_text or "外8" in all_text or "外10" in all_text:
        draft["tax_mode"] = "exclusive"

    for method, words in _PAYMENT_WORDS:
        if any(_contains_fuzzy(all_text, w) for w in words):
            draft["payment_method"] = method
            break

    # 明細の列の見出し（「商品名 数量 合計」）。2 語以上が同じ行に並んでいたら見出しとみなし、
    # ①その行から下を明細とする（`合計` を合計欄と取り違えない）②「数量」の列の位置を覚える。
    qty_col: tuple[float, float] | None = None
    header_row_bottom: float | None = None
    for b in items_boxes:
        if not re.fullmatch(r"[（(]?(商品名|品名|品目|数量|数|単価|金額|合計|点数)[）)]?", b["text"].replace(" ", "")):
            continue
        same_row = [
            c for c in items_boxes
            if c is not b and _overlap(c, b) > 0.3
            and re.fullmatch(r"[（(]?(商品名|品名|品目|数量|数|単価|金額|合計|点数)[）)]?", c["text"].replace(" ", ""))
        ]
        if not same_row:
            continue
        header_row_bottom = max(x["y1"] for x in [b, *same_row])
        for c in [b, *same_row]:
            if re.fullmatch(r"[（(]?(数量|数)[）)]?", c["text"].replace(" ", "")):
                pad = 1.5 * h_med
                qty_col = (c["x0"] - pad, c["x1"] + pad)
        break

    # --- 明細（価格の錨） ----------------------------------------------------------
    items_top = header_end + 0.4 * h_med
    if header_row_bottom is not None:
        items_top = max(items_top, header_row_bottom + 0.1 * h_med)
    items_bottom = first_total_y if first_total_y is not None else items_boxes[-1]["cy"] + 1
    totals_box_ids = {id(x) for r in total_rows.values() for x in (r["box"], r.get("value_box")) if x is not None}
    region = [
        b for b in items_boxes
        if items_top < b["cy"] < items_bottom - 0.3 * h_med and id(b) not in totals_box_ids
    ]
    used: set[int] = set()
    items: list[dict[str, Any]] = []
    notes: list[str] = []

    anchors = sorted(
        [b for b in region if b["price"] is not None and b["x1"] >= x_right - 4 * h_med],
        key=lambda b: b["cy"],
    )
    # 品名の箱の末尾に価格が混ざっている箱も錨にする
    for b in region:
        if b["price"] is None and not b["is_taxmark"]:
            m = _INLINE_PRICE_RE.search(b["text"])
            if m and b["x1"] >= x_right - 6 * h_med:
                b["inline_price"] = _int(m.group(1))
                b["inline_name"] = b["text"][: m.start()].strip()
                anchors.append(b)
    anchors.sort(key=lambda b: b["cy"])

    # 品名の候補（価格でも税印でも数量行でもない箱）
    def _in_qty_col(b: dict[str, Any]) -> bool:
        if qty_col is None:
            return False
        center = (b["x0"] + b["x1"]) / 2
        return qty_col[0] <= center <= qty_col[1] and bool(re.fullmatch(r"\d{1,3}", b["text"].replace(" ", "")))

    name_boxes = [
        b for b in region
        if b["price"] is None and b.get("inline_price") is None and not b["is_taxmark"]
        and parse_qty_row(b["text"]) is None and not _in_qty_col(b)
    ]
    name_boxes.sort(key=lambda b: b["cy"])
    # 系統的なずれ: 錨ごとに最も近い品名の箱との cy の差の中央値（残った傾きのぶん）
    offsets: list[float] = []
    for a in anchors:
        cands = [b for b in name_boxes if b["x1"] <= a["x0"] + 0.5 * h_med]
        if cands:
            nearest = min(cands, key=lambda b: abs(b["cy"] - a["cy"]))
            if abs(nearest["cy"] - a["cy"]) < 1.2 * h_med:
                offsets.append(a["cy"] - nearest["cy"])
    offset = statistics.median(offsets) if offsets else 0.0
    pitch = statistics.median([b["cy"] - a["cy"] for a, b in zip(anchors, anchors[1:])]) if len(anchors) > 1 else 1.6 * h_med
    pitch = max(pitch, h_med)

    pending_tax: int | None = None
    for a in anchors:
        aid = id(a)
        if aid in used:
            continue
        used.add(aid)
        amount = a["price"] if a["price"] is not None else a.get("inline_price")
        a_name = a.get("inline_name") or ""
        target = a["cy"] - offset
        # 同じ行（ずれを引いた後）で左にある未使用の箱。同じ行に複数あれば x 順に繋ぐ
        name_parts: list[dict[str, Any]] = []
        for b in name_boxes:
            if id(b) in used or b["x0"] >= a["x0"] or b["x1"] > a["x0"] + 1.0 * h_med:
                continue
            if abs(b["cy"] - target) <= 0.45 * pitch:
                name_parts.append(b)
        if not name_parts and not a_name:
            cands = [
                b for b in name_boxes
                if id(b) not in used and b["x0"] < a["x0"] and b["x1"] <= a["x0"] + 1.0 * h_med
                and abs(b["cy"] - target) <= 0.7 * pitch
            ]
            if cands:
                name_parts = [min(cands, key=lambda b: abs(b["cy"] - target))]
        # 横に重なる箱同士（上下に積まれた別の行）は、行に最も近い 1 つだけを残す
        name_parts.sort(key=lambda b: abs(b["cy"] - target))
        kept: list[dict[str, Any]] = []
        for b in name_parts:
            if any(min(b["x1"], k["x1"]) - max(b["x0"], k["x0"]) > 0.5 * min(b["x1"] - b["x0"], k["x1"] - k["x0"]) for k in kept):
                continue
            kept.append(b)
        name_parts = kept
        for b in name_parts:
            used.add(id(b))
        name_parts.sort(key=lambda b: b["x0"])
        tax: int | None = None
        words: list[str] = []
        for b in name_parts:
            t = b["text"]
            mt = _TAXMARK_RE.match(t.replace(" ", ""))
            if mt:
                tax = int(mt.group(1)) if mt.group(1) != "18" else 8
                t = t.replace(" ", "")[mt.end():]
            words.append(t)
        if a_name:
            ma = _TAXMARK_RE.match(a_name.replace(" ", ""))
            if ma:
                tax = int(ma.group(1)) if ma.group(1) != "18" else 8
                a_name = a_name.replace(" ", "")[ma.end():]
        name = " ".join(w for w in words if w).strip() or a_name
        # 同じ行の税印の箱
        if tax is None:
            for b in region:
                if b["is_taxmark"] and id(b) not in used and abs(b["cy"] - target) <= 0.5 * pitch:
                    tax = b["taxmark"]
                    used.add(id(b))
                    break
        if tax is None:
            tax = pending_tax
        pending_tax = None
        # 「数量」の列に数字があれば数量（単価は金額から割る）
        col_qty: int | None = None
        if qty_col is not None:
            for b in region:
                if id(b) in used or not _in_qty_col(b) or abs(b["cy"] - target) > 0.45 * pitch:
                    continue
                col_qty = int(b["text"].replace(" ", ""))
                used.add(id(b))
                break
        # 価格の付いた数量行（`(@132×2個) ¥264` のように 1 行に収まっている店）は直前の明細へ
        qty_row = parse_qty_row(name)
        if qty_row and items:
            items[-1]["qty"], items[-1]["unit_price"] = qty_row
            if amount is not None and items[-1].get("amount") is None:
                items[-1]["amount"] = amount
            continue
        if amount is not None and amount < 0:
            compact = name.replace(" ", "")
            if re.match(r"^[（(]?\d+点", compact):
                continue  # 値引きの内訳行（「(3点 1回 -17)」）。値引き自体は直前の行で数えている
            items.append({"name": name or "値引き", "qty": 1, "unit_price": None, "amount": amount, "tax_rate": tax, "is_discount": True, "source": "ocr", "_cy": a["cy"]})
            continue
        if not name:
            notes.append(f"price_without_name:{amount}")
        qty = col_qty if col_qty and col_qty > 0 else 1
        unit = (amount // qty) if (qty > 1 and amount is not None and amount % qty == 0) else None
        items.append({"name": name, "qty": qty, "unit_price": unit, "amount": amount, "tax_rate": tax, "is_discount": False, "source": "ocr", "_cy": a["cy"]})

    # 錨に付かなかった箱: 税印だけの行は直後の明細の税率、数量行は直前の明細、それ以外は品名だけの行
    leftovers = [b for b in region if id(b) not in used]
    for b in sorted(leftovers, key=lambda b: b["cy"]):
        t = b["text"].replace(" ", "")
        if b["is_taxmark"]:
            nxt = next((i for i in items if i.get("tax_rate") is None and i["_cy"] > b["cy"] - 0.2 * pitch), None)
            if nxt is not None:
                nxt["tax_rate"] = b["taxmark"]
            continue
        qty_row = parse_qty_row(t)
        if qty_row:
            above = [i for i in items if i["_cy"] < b["cy"] + 0.3 * pitch and not i.get("is_discount")]
            if above:
                prev = max(above, key=lambda i: i["_cy"])
                prev["qty"], prev["unit_price"] = qty_row
            continue
        if len(re.sub(r"[^\w]", "", t)) < 2 or _price_from(b["text"]) is not None:
            continue
        mt = _TAXMARK_RE.match(t)
        tax = (int(mt.group(1)) if mt.group(1) != "18" else 8) if mt else None
        name = b["text"].replace(" ", "")[mt.end():] if mt else b["text"]
        items.append({"name": name, "qty": 1, "unit_price": None, "amount": None, "tax_rate": tax, "is_discount": False, "source": "ocr", "_cy": b["cy"]})
        notes.append("name_without_price")

    items.sort(key=lambda i: i["_cy"])
    for it in items:
        it.pop("_cy", None)

    # 数量×単価と金額が矛盾する行（数量行の付け間違い）は数量を戻す
    for it in items:
        if it.get("unit_price") and it.get("amount") is not None and it["qty"] * it["unit_price"] != it["amount"]:
            if it["unit_price"] == it["amount"]:
                it["qty"] = 1
            else:
                notes.append("qty_mismatch")

    # 合計欄が明細の中に紛れていたら外す（`小計` の値が明細の錨に取られたとき）
    if draft.get("subtotal") is not None:
        items = [i for i in items if not (i["amount"] == draft["subtotal"] and not i["name"])]

    # 小計の**後**に引かれる値引き（`JAF5%  -148`）。合計欄の中にあるので明細の領域には入らない。
    if first_total_y is not None:
        total_row_y = total_rows["total"]["box"]["cy"] if "total" in total_rows else None
        for p in price_boxes:
            if p["price"] is None or p["price"] >= 0 or id(p) in totals_box_ids:
                continue
            if p["cy"] <= first_total_y - 0.3 * h_med:
                continue  # 明細の中の値引きは通常の経路で拾っている
            if total_row_y is not None and p["cy"] >= total_row_y - 0.3 * h_med:
                continue
            left = [
                b for b in items_boxes
                if b is not p and id(b) not in totals_box_ids and b["x1"] <= p["x0"] and _overlap(b, p) > 0.3
            ]
            name = " ".join(b["text"] for b in sorted(left, key=lambda b: b["x0"])).strip()
            items.append({
                "name": name or "値引き", "qty": 1, "unit_price": None, "amount": p["price"],
                "tax_rate": None, "is_discount": True, "source": "ocr",
            })
            notes.append("discount_after_subtotal")

    draft["items"] = items
    draft["parse"]["notes"].extend(notes)
    if draft.get("total") is None:
        # 合計が読めないときは、お預り−お釣り で導く
        if isinstance(draft.get("tendered"), int) and isinstance(draft.get("change"), int):
            draft["total"] = draft["tendered"] - draft["change"]
            notes.append("total_from_change")
    return draft
