"""chef の純粋関数（ADR-002 §3・§7）。

道具（この module）は判断しない。並べ替え・突き合わせ・集計・検証だけをする。
「何を作るか」「候補のどれを推すか」は担当（LLM）が決める。

DB（`sqlite3.Connection`）を触る関数はここには置かない（`cli.py` の仕事）。
ここにあるのは合成データ・素の辞書・リストだけで試験できる関数に限る。
"""

from __future__ import annotations

import re
import tomllib
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

from manor import i18n
from manor.errors import ManorError

_LEXICON_PATH = Path(__file__).resolve().parent / "lexicon.toml"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# --- lexicon（唯一の出どころは lexicon.toml。ここは読むだけ） ------------------------


def load_lexicon(path: Path | None = None) -> dict[str, object]:
    """`lexicon.toml` を読む。試験は `path` に合成データのファイルを渡せる。"""
    p = path or _LEXICON_PATH
    with p.open("rb") as f:
        return tomllib.load(f)


def basics(path: Path | None = None) -> list[str]:
    """基礎調味料の一覧（在庫の対象外）。"""
    lex = load_lexicon(path)
    return list(lex.get("basics", {}).get("items", []))  # type: ignore[union-attr]


def dish_types(path: Path | None = None) -> dict[str, list[str]]:
    """料理の型 → キーワード一覧。"""
    lex = load_lexicon(path)
    return dict(lex.get("dish_types", {}))  # type: ignore[arg-type]


def ingredient_categories(path: Path | None = None) -> dict[str, list[str]]:
    """食材の分類 → キーワード一覧。"""
    lex = load_lexicon(path)
    return dict(lex.get("ingredient_categories", {}))  # type: ignore[arg-type]


# --- 検証（CLI が受ける形式はここで縛る。日付の“解決”は秘書の道具の領分） -------------


def validate_date(value: str, *, field: str = "日付", field_key: str = "chef.field.date") -> str:
    """`YYYY-MM-DD` だけを受ける。相対日付（「明日」等）は秘書の `resolve_date` の仕事で、
    ここでは扱わない（ADR-002 §2「日付の解決は道具がやる」の対象は secretary 側）。

    `field` は `message_ja`（常に日本語）専用、`field_key` は CLI 表示用の翻訳キー
    （`errors.py` の docstring と同じ考え方。呼び出し側は両方渡す）。
    """
    if not _DATE_RE.match(value):
        raise ManorError(
            f"{field}は YYYY-MM-DD 形式で指定してください: {value!r}",
            code=2,
            key="error.chef.date_format",
            params={"field": i18n.t(field_key), "value": repr(value)},
        )
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ManorError(
            f"{field}が不正な日付です: {value!r}",
            code=2,
            key="error.chef.date_invalid",
            params={"field": i18n.t(field_key), "value": repr(value)},
        ) from exc
    return value


def validate_choice(value: str, choices: Iterable[str], *, field: str) -> str:
    """語彙外なら `ManorError(code=2)`。"""
    choices = list(choices)
    if value not in choices:
        raise ManorError(
            f"{field} は次のいずれかにしてください: {', '.join(choices)}（受け取った値: {value!r}）",
            code=2,
            key="error.chef.choice_invalid",
            params={"field": field, "choices": ", ".join(choices), "value": repr(value)},
        )
    return value


# --- 突き合わせ（ADR-002 §3「突き合わせの規則」） -----------------------------------


def item_match(a: str, b: str) -> bool:
    """`missing` / `bought` が使う突き合わせ。完全ではない（規則2つだけ）。

    規則1: どちらかがどちらかを含む（`卵` ⊂ `ゆで卵`）
    規則2: 2文字以上で先頭と末尾の字が一致（`豚肉` ↔ `豚こま肉`）
    """
    a = a.strip()
    b = b.strip()
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    if len(a) >= 2 and len(b) >= 2 and a[0] == b[0] and a[-1] == b[-1]:
        return True
    return False


def is_staple(item: str, staples: list[str]) -> bool:
    """基礎調味料かどうか（`item_match` で緩く突き合わせる）。"""
    return any(item_match(item, s) for s in staples)


def check_missing(
    requested: list[str], pantry_items: list[str], staples: list[str]
) -> list[dict[str, object]]:
    """`pantry missing` の中身。基礎調味料は結果に含めない（数えない）。

    突き合わせが完全でないため、当たった（`found=True`）ものも結果に出す
    （ADR-002 §3「当たったものも画面に出す」）。
    """
    out: list[dict[str, object]] = []
    for req in requested:
        if is_staple(req, staples):
            continue
        matched = [p for p in pantry_items if item_match(req, p)]
        out.append({"item": req, "found": bool(matched), "matched": matched})
    return out


# --- 在庫の並べ替え・期限 --------------------------------------------------------------


def sort_by_expiry(rows: list[dict[str, object]], key: str = "expires") -> list[dict[str, object]]:
    """期限の昇順に並べる。不明（None）は末尾。"""
    return sorted(rows, key=lambda r: (r.get(key) is None, r.get(key) or ""))


def is_expiring(expires: str | None, today: str, days: int) -> bool:
    """`expires` が `today` から `days` 日以内か。**不明（None）は安全側で対象に含める**
    （期限切れを見落とすより、不明を一度余分に見せるほうを選ぶ）。
    """
    if expires is None:
        return True
    try:
        limit = date.fromisoformat(today) + timedelta(days=days)
        return date.fromisoformat(expires) <= limit
    except ValueError:
        return True  # 壊れた日付文字列も不明と同様に安全側へ倒す


# --- 数量（chef_pantry.qty と unit は別カラム。qty は数値か「不明」の自由記述 TEXT） ----


def parse_qty(value: str) -> float | None:
    """`qty` カラムの中身を数値として読む。数値でなければ `None`（「不明」等）。"""
    try:
        return float(value.strip())
    except ValueError:
        return None


def subtract_qty(current: str, amount: str) -> str | None:
    """`current`（`qty` カラム）から `amount` を差し引いた残量を文字列で返す。

    どちらかが数値でなければ `None`（**差し引けない＝使い切り判定をしない**。
    v1 kitchen.md の規則「数量が不明のときは、使い切りの判定をしない」を引き継ぐ）。
    残りが 0 以下なら空文字列（呼び出し側はこれを「使い切り」として扱う）。
    `unit` カラムは別に持つので、ここでは触らない（呼び出し側が据え置く）。
    """
    cur = parse_qty(current)
    amt = parse_qty(amount)
    if cur is None or amt is None:
        return None
    remaining = cur - amt
    if remaining <= 0:
        return ""
    return str(int(remaining)) if remaining == int(remaining) else str(remaining)


# --- 食事の記録（週次集計） -------------------------------------------------------------

WEEK_SLOTS: tuple[str, ...] = ("breakfast", "lunch", "dinner")


def week_range(today: str, days: int) -> tuple[str, str]:
    """`today` を末日として `days` 日分の範囲（開始日, 終了日）を返す。"""
    end = date.fromisoformat(today)
    start = end - timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


def classify_dish_type(dish: str, types: dict[str, list[str]]) -> str | None:
    """`dish` 名にキーワードが含まれる最初の型を返す。当たらなければ `None`。"""
    for type_name, keywords in types.items():
        if any(kw in dish for kw in keywords):
            return type_name
    return None


def classify_ingredients(ingredients: str, categories: dict[str, list[str]]) -> list[str]:
    """読点（`、`）区切りの `ingredients` から、当たった食材分類を重複なく返す。"""
    parts = [p.strip() for p in ingredients.replace("、", ",").split(",") if p.strip()]
    hits: list[str] = []
    for part in parts:
        for cat_name, keywords in categories.items():
            if any(kw in part for kw in keywords):
                if cat_name not in hits:
                    hits.append(cat_name)
                break
    return hits


def aggregate_week(
    meals: list[dict[str, object]],
    start: str,
    end: str,
    dish_types_map: dict[str, list[str]],
    categories_map: dict[str, list[str]],
) -> dict[str, object]:
    """`meal week` の集計。**数えるだけ**（何を勧めるかは言わない）。

    - 料理の型別・食材分類別の回数
    - 欠けた枠（`start`〜`end` の朝・昼・夜で記録が無いもの。`snack` は対象外＝任意の枠）
    - 網羅率（記録がある枠 / 全枠）
    - 未確定（`planned=1`）の件数
    """
    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    n_days = (end_d - start_d).days + 1
    date_list = [(start_d + timedelta(days=i)).isoformat() for i in range(n_days)]

    logged = {(str(m["date"]), str(m["slot"])) for m in meals}
    missing_slots = [
        {"date": d, "slot": s}
        for d in date_list
        for s in WEEK_SLOTS
        if (d, s) not in logged
    ]
    total_slots = len(date_list) * len(WEEK_SLOTS)
    filled_slots = total_slots - len(missing_slots)
    coverage_rate = round(filled_slots / total_slots, 3) if total_slots else 0.0

    dish_type_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    planned_count = 0
    for m in meals:
        dtype = classify_dish_type(str(m.get("dish", "")), dish_types_map)
        if dtype:
            dish_type_counts[dtype] = dish_type_counts.get(dtype, 0) + 1
        for cat in classify_ingredients(str(m.get("ingredients", "")), categories_map):
            category_counts[cat] = category_counts.get(cat, 0) + 1
        if m.get("planned"):
            planned_count += 1

    return {
        "start": start,
        "end": end,
        "dish_type_counts": dish_type_counts,
        "ingredient_category_counts": category_counts,
        "missing_slots": missing_slots,
        "coverage_rate": coverage_rate,
        "planned_count": planned_count,
    }
