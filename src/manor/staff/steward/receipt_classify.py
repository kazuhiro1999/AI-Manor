"""明細の分類（ADR-020 D7）: 辞書（`steward_item_alias`）→ 語彙のキーワード → 店の既定 → 残りは Claude。

3 階層: 大項目・中項目（マネーフォワード ME の語彙）＋ 品目（`item_kind`。manor 独自）。
語彙は `lexicon.toml`（`[categories]` `[item_kinds]` `[item_kind_defaults]` `[item_kind_keywords]` `[store_defaults]`）。
"""

from __future__ import annotations

import sqlite3
import unicodedata
from typing import Any

from ... import util
from . import receipt_checks, receipt_parse


def categories() -> dict[str, list[str]]:
    cats = receipt_parse.lexicon().get("categories") or {}
    return {str(k): [str(x) for x in v] for k, v in cats.items()}


def item_kinds() -> list[str]:
    return [str(x) for x in (receipt_parse.lexicon().get("item_kinds") or {}).get("names", [])]


def kind_default(kind: str) -> tuple[str, str]:
    defaults = receipt_parse.lexicon().get("item_kind_defaults") or {}
    pair = defaults.get(kind)
    if isinstance(pair, list) and len(pair) == 2:
        return str(pair[0]), str(pair[1])
    return "", ""


def _is_kana_or_ascii(s: str) -> bool:
    return all(("゠" <= ch <= "ヿ") or ch.isascii() for ch in s)


def _kind_by_keyword(name: str) -> str:
    """品名に語彙のキーワードが含まれていれば品目。**強い当たりだけ**採る（短いカナの語は
    「アジノモト」の「アジ」のように誤爆するので、語が 4 文字以上か、漢字を含むか、空白で区切られた
    語の頭か末尾に一致するとき）。複数当たれば最も長い語が勝ち、同じ長さなら先に書いた品目。"""
    text = unicodedata.normalize("NFKC", name)
    text_upper = text.upper()
    tokens = [tok for tok in text.replace("(", " ").replace(")", " ").split(" ") if tok]
    keywords = receipt_parse.lexicon().get("item_kind_keywords") or {}
    best: tuple[int, int, str] | None = None  # (長さ, −順位, 品目)
    for rank, (kind, words) in enumerate(keywords.items()):
        for w in words:
            w_norm = unicodedata.normalize("NFKC", str(w)).strip()
            if not w_norm:
                continue
            hit = w_norm in text or w_norm.upper() in text_upper
            if not hit:
                continue
            strong = (
                len(w_norm) >= 4
                or not _is_kana_or_ascii(w_norm)
                or any(tok.startswith(w_norm) or tok.endswith(w_norm) or tok.upper() == w_norm.upper() for tok in tokens)
            )
            if not strong:
                continue
            cand = (len(w_norm), -rank, str(kind))
            if best is None or cand > best:
                best = cand
    return best[2] if best else ""


def store_default(store: dict[str, Any]) -> tuple[str, str]:
    defaults = receipt_parse.lexicon().get("store_defaults") or {}
    for key in (store.get("registration_number"), store.get("name")):
        if key and key in defaults:
            pair = defaults[key]
            if isinstance(pair, list) and len(pair) == 2:
                return str(pair[0]), str(pair[1])
    return "", ""


def alias_lookup(conn: sqlite3.Connection, names_normalized: list[str]) -> dict[str, dict[str, str]]:
    if not names_normalized:
        return {}
    marks = ",".join("?" for _ in names_normalized)
    rows = conn.execute(
        f"SELECT name_normalized, category, subcategory, item_kind FROM steward_item_alias WHERE name_normalized IN ({marks})",
        names_normalized,
    ).fetchall()
    return {r[0]: {"category": r[1], "subcategory": r[2], "item_kind": r[3]} for r in rows}


def classify_items(conn: sqlite3.Connection, draft: dict[str, Any], *, use_claude: bool = True, claude_bin: str | None = None) -> dict[str, Any]:
    """下書きの明細に分類を付ける（その場で書き換える）。戻り値 `{"unknown": [...], "claude": {"called", "ok", "reason", "cost"}}`。
    - 既に分類のある行（手入力・再読み）は触らない
    - 辞書 → キーワード → Claude（未知をまとめて 1 回）→ 店の既定 → 未分類
    """
    items: list[dict[str, Any]] = list(draft.get("items") or [])
    store = draft.get("store") if isinstance(draft.get("store"), dict) else {}
    aliases = alias_lookup(conn, [str(i.get("name_normalized") or receipt_checks.normalize_name(i.get("name"))) for i in items])
    unknown: list[dict[str, Any]] = []
    for it in items:
        if it.get("category") and it.get("item_kind"):
            continue
        key = str(it.get("name_normalized") or receipt_checks.normalize_name(it.get("name")))
        hit = aliases.get(key)
        if hit and (hit["category"] or hit["item_kind"]):
            it["category"] = hit["category"]
            it["subcategory"] = hit["subcategory"]
            it["item_kind"] = hit["item_kind"]
            it["source"] = "alias" if it.get("source") in ("ocr", "claude", "rule") else it.get("source")
            continue
        kind = _kind_by_keyword(str(it.get("name") or ""))
        if kind:
            cat, sub = kind_default(kind)
            it["item_kind"] = kind
            it["category"] = it.get("category") or cat
            it["subcategory"] = it.get("subcategory") or sub
            continue
        unknown.append(it)

    claude_info: dict[str, Any] = {"called": False, "ok": None, "reason": "", "cost": 0.0}
    if unknown and use_claude:
        from . import receipt_reader  # noqa: PLC0415

        names = [str(i.get("name") or "") for i in unknown if not i.get("is_discount")]
        if names and receipt_reader.claude_available(claude_bin):
            claude_info["called"] = True
            res = receipt_reader.classify_names(names, item_kinds=item_kinds(), categories=categories(), claude_bin=claude_bin)
            claude_info["ok"] = res["ok"]
            claude_info["reason"] = res["reason"]
            claude_info["cost"] = res["cost"]
            if res["ok"]:
                for it in unknown:
                    hit = res["map"].get(str(it.get("name") or ""))
                    if hit:
                        it["item_kind"] = hit["item_kind"]
                        cat, sub = hit["category"], hit["subcategory"]
                        if not cat and hit["item_kind"]:
                            cat, sub = kind_default(hit["item_kind"])
                        it["category"] = cat
                        it["subcategory"] = sub
                        it["source"] = "claude" if it.get("source") == "ocr" else it.get("source")

    cat_d, sub_d = store_default(store)
    still: list[dict[str, Any]] = []
    for it in unknown:
        if it.get("is_discount") and not it.get("category"):
            # 値引きは直前の商品の分類に寄せる
            prev = _prev_item(items, it)
            if prev is not None:
                it["category"], it["subcategory"], it["item_kind"] = prev.get("category", ""), prev.get("subcategory", ""), prev.get("item_kind", "")
                continue
        if not it.get("category"):
            if cat_d:
                it["category"], it["subcategory"] = cat_d, sub_d
            else:
                it["category"], it["subcategory"] = "未分類", "未分類"
            still.append(it)
    return {"unknown": [str(i.get("name") or "") for i in still], "claude": claude_info}


def _prev_item(items: list[dict[str, Any]], it: dict[str, Any]) -> dict[str, Any] | None:
    idx = items.index(it)
    for j in range(idx - 1, -1, -1):
        if not items[j].get("is_discount"):
            return items[j]
    return None


def learn_aliases(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    """主人が直した分類を辞書に覚える（ADR-020 D7）。分類の無い行・値引き行は覚えない。"""
    n = 0
    now = util.now()
    for it in items:
        if it.get("is_discount"):
            continue
        key = str(it.get("name_normalized") or receipt_checks.normalize_name(it.get("name")))
        if not key or not (it.get("category") or it.get("item_kind")):
            continue
        conn.execute(
            "INSERT INTO steward_item_alias(name_normalized, category, subcategory, item_kind, updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(name_normalized) DO UPDATE SET category=excluded.category, subcategory=excluded.subcategory, "
            "item_kind=excluded.item_kind, updated_at=excluded.updated_at",
            (key, str(it.get("category") or ""), str(it.get("subcategory") or ""), str(it.get("item_kind") or ""), now),
        )
        n += 1
    return n
