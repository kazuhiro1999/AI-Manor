"""steward の CSV 取り込み（ADR-005 §2「imports」）。**純粋関数のみ。DB には触らない。**

CLI（`manor money import`）と Web（`POST /api/v1/imports/money/preview|commit`）の
両方がここを呼ぶ。実際の `INSERT INTO steward_expense` は呼び出し側（`cli.py` の
`cmd_money_import` / `web/api_v1/imports.py`）が行う（D3「書き込みは core の関数だけ」の
精神を steward 内でも守る——このモジュールは判断も書き込みもしない、突き合わせと正規化だけ）。

**規則（ADR-005 §2 のまま）**:
- 日付は `YYYY-MM-DD` へ正規化する。`YYYY/MM/DD` も受ける
- 金額は整数の円（`-` `¥` `,` を取り除く）
- 収入/支出は `kind` 列があればその値を読む。無ければ金額の負号で判定する
  （**曖昧だった点**: ADR-005 の原文「収入は kind 列か負号で判定」を文字どおりに取り、
  負号つきの金額は収入とみなす。多くの家計簿アプリは支出をマイナス表示するため、直感とは
  逆になりうる——実物のエクスポート形式が分かり次第、執事の裁定で直す）
- 同じ行（date・amount・memo を正規化した文字列の sha256 の先頭16桁）は二重に入れない
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field

from manor import i18n
from manor.errors import ManorError

_DATE_ISO_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_DATE_SLASH_RE = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$")
_AMOUNT_STRIP_RE = re.compile(r"[¥￥,，\s]")

REQUIRED_MAP_FIELDS: tuple[str, ...] = ("date", "amount")
OPTIONAL_MAP_FIELDS: tuple[str, ...] = ("category", "memo", "kind")

#: プリセットの列名は**推定**（実物の CSV サンプルが手元に無いための最善の推測。
#: ADR-005 §2「プリセットの列名は推定」）。実物と違えば `--map` で上書きできる。
PRESET_MAPS: dict[str, dict[str, str]] = {
    "zaim": {
        "date": "日付", "amount": "金額（円）", "category": "カテゴリ",
        "memo": "内容", "kind": "収支",
    },
    "moneyforward": {
        "date": "日付", "amount": "金額（円）", "category": "大項目",
        "memo": "内容", "kind": "収支",
    },
}

_INCOME_WORDS: frozenset[str] = frozenset({"収入", "income", "入金"})
_EXPENSE_WORDS: frozenset[str] = frozenset({"支出", "expense", "出金"})


@dataclass
class ImportRow:
    line: int
    date: str
    amount: int
    kind: str  # 'expense' | 'income'
    category: str
    memo: str
    import_hash: str

    def to_dict(self) -> dict[str, object]:
        return {
            "line": self.line, "date": self.date, "amount": self.amount, "kind": self.kind,
            "category": self.category, "memo": self.memo, "import_hash": self.import_hash,
        }


@dataclass
class UnreadableRow:
    line: int
    reason: str
    raw: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {"line": self.line, "reason": self.reason, "raw": self.raw}


@dataclass
class ImportResult:
    rows: list[ImportRow]
    duplicates: list[ImportRow]
    unreadable: list[UnreadableRow]
    total: int

    def to_dict(self) -> dict[str, object]:
        return {
            "rows": [r.to_dict() for r in self.rows],
            "duplicates": [r.to_dict() for r in self.duplicates],
            "unreadable": [r.to_dict() for r in self.unreadable],
            "total": self.total,
        }


def parse_map(spec: str) -> dict[str, str]:
    """`"date=日付,amount=金額,category=カテゴリ"` を辞書にする。"""
    out: dict[str, str] = {}
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise ManorError(
                f"--map の形式が不正です（key=value でない）: {chunk!r}",
                code=2,
                key="error.money.map_chunk_not_kv",
                params={"chunk": repr(chunk)},
            )
        key, _, value = chunk.partition("=")
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise ManorError(
                f"--map の形式が不正です: {chunk!r}",
                code=2,
                key="error.money.map_chunk_invalid",
                params={"chunk": repr(chunk)},
            )
        out[key] = value
    return out


def resolve_map(fmt: str, map_spec: str | None) -> dict[str, str]:
    """`--format` と `--map` から実際に使う列名の対応表を作る。

    `generic` は `--map` が必須（ADR-005 §2）。`zaim`/`moneyforward` はプリセットに
    `--map` を上書きで重ねられる（プリセットの推定が実物とずれたときの逃げ道）。
    """
    if fmt == "generic":
        if not map_spec or not map_spec.strip():
            raise ManorError(
                "--format generic のときは --map が必須です",
                code=2,
                key="error.money.import_generic_needs_map",
            )
        column_map = parse_map(map_spec)
    elif fmt in PRESET_MAPS:
        column_map = dict(PRESET_MAPS[fmt])
        if map_spec and map_spec.strip():
            column_map.update(parse_map(map_spec))
    else:
        known = " / ".join(("generic", *sorted(PRESET_MAPS)))
        raise ManorError(
            f"語彙外の --format です: {fmt!r}（使えるのは {known}）",
            code=2,
            key="error.money.import_format_unknown",
            params={"format": repr(fmt), "known": known},
        )

    missing = [f for f in REQUIRED_MAP_FIELDS if f not in column_map]
    if missing:
        raise ManorError(
            f"--map に必須の項目がありません: {', '.join(missing)}",
            code=2,
            key="error.money.map_missing_fields",
            params={"missing": ", ".join(missing)},
        )
    return column_map


def normalize_date(value: str) -> str | None:
    """`YYYY-MM-DD` か `YYYY/MM/DD` を `YYYY-MM-DD` へ。読めなければ `None`（例外にしない）。"""
    text = (value or "").strip()
    m = _DATE_ISO_RE.match(text) or _DATE_SLASH_RE.match(text)
    if not m:
        return None
    year, month, day = (int(g) for g in m.groups())
    try:
        import datetime as _dt

        return _dt.date(year, month, day).isoformat()
    except ValueError:
        return None


def normalize_amount(value: str) -> tuple[int, bool] | None:
    """金額文字列を `(絶対値, 負号だったか)` にする。`¥` `,` `全角，` `空白` を取り除いた上で
    整数だけを受ける（小数は読めない扱い）。読めなければ `None`。
    """
    text = _AMOUNT_STRIP_RE.sub("", (value or "").strip())
    if not text:
        return None
    negative = text.startswith("-")
    if negative:
        text = text[1:]
    elif text.startswith("+"):
        text = text[1:]
    if not text.isdigit():
        return None
    return int(text), negative


def interpret_kind(value: str) -> str | None:
    """`kind` 列の生の値を `'expense'`/`'income'` に読む。既知の語でなければ `None`
    （呼び出し側は `None` のとき金額の負号で判定する）。
    """
    text = (value or "").strip()
    if text in _INCOME_WORDS:
        return "income"
    if text in _EXPENSE_WORDS:
        return "expense"
    return None


def compute_hash(date: str, amount: int, memo: str) -> str:
    """`date|amount|memo`（memo は前後空白を落として正規化）の sha256 の先頭16桁。"""
    normalized = f"{date}|{amount}|{(memo or '').strip()}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def parse_csv(
    text: str,
    column_map: dict[str, str],
    *,
    existing_hashes: frozenset[str] = frozenset(),
) -> ImportResult:
    """CSV 本文（デコード済みの文字列）を正規化する。**ここでは何も書き込まない。**

    `existing_hashes` は呼び出し側が事前に `SELECT import_hash FROM steward_expense
    WHERE import_hash IS NOT NULL` で取っておいた既存の指紋の集合——DB との突き合わせは
    ここでは行わず、集合を受け取るだけにして純粋関数のまま保つ。
    """
    reader = csv.DictReader(io.StringIO(text))
    rows: list[ImportRow] = []
    duplicates: list[ImportRow] = []
    unreadable: list[UnreadableRow] = []
    seen_in_file: set[str] = set()
    total = 0

    date_col = column_map["date"]
    amount_col = column_map["amount"]
    category_col = column_map.get("category")
    memo_col = column_map.get("memo")
    kind_col = column_map.get("kind")

    for line_no, raw in enumerate(reader, start=2):  # 1行目はヘッダ
        total += 1
        date_raw = str(raw.get(date_col) or "").strip()
        amount_raw = str(raw.get(amount_col) or "").strip()
        category = str(raw.get(category_col) or "").strip() if category_col else ""
        memo = str(raw.get(memo_col) or "").strip() if memo_col else ""
        kind_raw = str(raw.get(kind_col) or "").strip() if kind_col else ""

        date = normalize_date(date_raw)
        if date is None:
            unreadable.append(
                UnreadableRow(line=line_no, reason=i18n.t("money.import.reason_date_unreadable"), raw=dict(raw))
            )
            continue

        parsed_amount = normalize_amount(amount_raw)
        if parsed_amount is None:
            unreadable.append(
                UnreadableRow(line=line_no, reason=i18n.t("money.import.reason_amount_unreadable"), raw=dict(raw))
            )
            continue
        amount, negative = parsed_amount

        kind = interpret_kind(kind_raw) or ("income" if negative else "expense")
        import_hash = compute_hash(date, amount, memo)
        row = ImportRow(
            line=line_no, date=date, amount=amount, kind=kind, category=category, memo=memo,
            import_hash=import_hash,
        )
        if import_hash in existing_hashes or import_hash in seen_in_file:
            duplicates.append(row)
        else:
            rows.append(row)
            seen_in_file.add(import_hash)

    return ImportResult(rows=rows, duplicates=duplicates, unreadable=unreadable, total=total)
