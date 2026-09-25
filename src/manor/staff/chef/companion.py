"""お供の提案（ADR-021）。主菜を決めたあとに「お供にこんなメニューはどうですか？」と並べる。

主人の使い方は「主菜を先に決め、それに合う副菜・サラダを**同時に**作る」——ラーメンには
海藻のサラダ、唐揚げにはキャベツのサラダ。基準は「主菜で**足りない栄養を最も補える**もの」。

## 立て付け（`menu.py`・`nutrition.py` と同じ）

- 採点・型の推定・印の推定 … **純粋関数**（DB も外部も触らない。合成 dict で試験できる）
- `suggest()` / `plan_together()` / `adopt()` … DB を読み書きする薄い層

数値と語彙は1つも持たない——下限の目安は `lexicon.toml` の `[menu.floor]`、帯と塩分の重みは
`[menu.band]`・`[menu.weights]`（献立のおすすめと**同じ物差し**）、お供だけの規則は
`[companion.*]` から読む。理由は `menu.py` と同じ「定型文の符牒」で返す（ADR-018 §4.1）。

## 候補の出どころ（ADR-021 D4）

1. うちのレシピ帳の副菜・汁物（`[companion.rules].companion_categories`）
2. **お供の定番**（`companions.toml`）——料理の「型」。材料とグラムは栄養の目安のためだけに持ち
   （栄養値は成分表から推定。ADR-019）、**作り方は持たない**。作り方はレシピサイトの候補
   （`companion_sources.toml`。URL・題名・画像の直リンクだけ）を並べ、主人が見て取り込むかを決める
   （2026-09-25 主人「AI 生成ではなくレシピサイトのものを引用したい」）。

同じ料理が両方にあればレシピ帳のほうを出す——題名の突き合わせ（`menu._dish_matches`）に加え、
**候補の URL を取り込んだレシピがあれば**その定番は出さない（取り込んだものが代わりに並ぶ）。

## 採点（ADR-021 D2・D3）

- **下限の不足がどれだけ埋まるか**（`[menu.floor]`。食物繊維・カリウム・カルシウム・鉄・
  ビタミンC・野菜の量）——ここが頭。相対量（`(下限 − 値) / 下限`）なので mg と g を同じ物差しで足せる
- **帯への寄与**（`menu.deviation` をそのまま。塩分の超過は強く減点）
- **相性**（主菜の型 → `[companion.pairing]`）と**同時に作れるか**（主菜がコンロを塞ぐなら
  火を使わないお供）、うちのレシピ・お気に入り

足した5項目の解決率が `micro_coverage_min` を下回る主菜では、下限の項を**丸ごと見ない**
（0 と読むと「何でも補える」ことになってしまう）。候補の側で下回るものは、その候補の
下限への寄与を 0 とする（分からないものを「補える」とは言わない）。
"""

from __future__ import annotations

import json
import sqlite3
import tomllib
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from manor import util
from manor.errors import ManorError

from . import menu, nutrition, ops

#: 下限で見る項目（`[menu.floor]` の鍵と同じ。`nutrition.MICRO_NUTRIENTS` ＋ 野菜の量）。
FLOOR_KEYS: tuple[str, ...] = (*nutrition.MICRO_NUTRIENTS, nutrition.VEG_G)

#: 候補の出どころ（API の `source`）。
SOURCE_RECIPE, SOURCE_CATALOG = "recipe", "catalog"

#: 加熱の語彙（定番の `heat`・うちのレシピの推定）。
HEAT_NONE, HEAT_RANGE, HEAT_STOVE = "none", "range", "stove"
HEATS: tuple[str, ...] = (HEAT_NONE, HEAT_RANGE, HEAT_STOVE)

#: 定番の一覧（ADR-021 D4）。`lexicon.toml` と同じ「語彙はファイルに置く」流儀。
CATALOG_PATH = Path(__file__).with_name("companions.toml")

#: 定番ごとのレシピサイトの候補（ADR-021 §6）。作り方は引用元のサイトで見る。
SOURCES_PATH = Path(__file__).with_name("companion_sources.toml")

#: お供の型が推定できないとき、火を使わない副菜に当てる型（和え物・おひたし・浅漬けの類）。
RAW_KIND = "生"

#: `menu.py` と同じ「お知らせだけ」の理由の優先度（点は動かさない）。
_NOTE_ONLY = 0.15
#: 超過は必ず見せる（`menu._ALWAYS_SHOW` と同じ考え方）。
_ALWAYS_SHOW = 1000.0

ERR_RECIPE_NOT_FOUND = menu.ERR_RECIPE_NOT_FOUND
ERR_BAD_REQUEST = menu.ERR_BAD_REQUEST
#: 定番の `key` が見つからない。web は 404 に写す。
ERR_CATALOG_NOT_FOUND = "error.chef.companion_not_found"


# --- lexicon（ここは読むだけ） -----------------------------------------------------------


@dataclass(frozen=True)
class Rules:
    """`[companion.*]` と `[menu.floor]`・`[menu.band]`・`[menu.weights]` の写し。"""

    floor: dict[str, float] = field(default_factory=dict)
    band: dict[str, tuple[float, float]] = field(default_factory=dict)
    menu_weights: dict[str, float] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)
    rules: dict[str, Any] = field(default_factory=dict)
    main_kinds: tuple[dict[str, Any], ...] = ()
    pairing: dict[str, dict[str, Any]] = field(default_factory=dict)
    tag_cues: dict[str, list[str]] = field(default_factory=dict)
    raw_veg: dict[str, list[str]] = field(default_factory=dict)
    heat_cues: dict[str, list[str]] = field(default_factory=dict)
    dish_types: dict[str, list[str]] = field(default_factory=dict)
    #: 5項目を候補に使う解決率の下限（`[menu.rules].nutrition_coverage_min`。献立と同じ）。
    macro_coverage_min: float = 0.8

    def rule(self, key: str, default: Any) -> Any:
        return self.rules.get(key, default)


def load_floor(path: Path | None = None) -> dict[str, float]:
    """1 食・1 人前の下限（`[menu.floor]`。ADR-021 D1）。無い行は見ない。"""
    raw = dict(dict(ops.load_lexicon(path).get("menu") or {}).get("floor") or {})  # type: ignore[union-attr]
    out: dict[str, float] = {}
    for key in FLOOR_KEYS:
        try:
            value = float(raw[key])
        except (KeyError, TypeError, ValueError):
            continue
        if value > 0:
            out[key] = value
    return out


def load_rules(path: Path | None = None) -> Rules:
    lex = ops.load_lexicon(path)
    comp = dict(lex.get("companion") or {})  # type: ignore[arg-type]
    return Rules(
        floor=load_floor(path),
        band=menu.load_band(path),
        menu_weights=menu.load_weights(path),
        weights={str(k): float(v) for k, v in dict(comp.get("weights") or {}).items()},
        rules=dict(comp.get("rules") or {}),
        main_kinds=tuple(dict(k) for k in (comp.get("main_kind") or []) if isinstance(k, Mapping)),
        pairing={str(k): dict(v) for k, v in dict(comp.get("pairing") or {}).items() if isinstance(v, Mapping)},
        tag_cues={str(k): [str(w) for w in v] for k, v in dict(comp.get("tag_cues") or {}).items()},
        raw_veg={str(k): [str(w) for w in v] for k, v in dict(comp.get("raw_veg_tags") or {}).items()},
        heat_cues={str(k): [str(w) for w in v] for k, v in dict(comp.get("heat_cues") or {}).items()},
        dish_types=dict(lex.get("dish_types") or {}),  # type: ignore[arg-type]
        macro_coverage_min=float(menu.load_rules(path).get("nutrition_coverage_min", 0.8)),
    )


# --- 型・加熱・印の推定（純粋関数） -------------------------------------------------------


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or ""))


def main_kind(title: str, category: str, rules: Rules) -> str:
    """主菜の型（相性の規則を引く鍵）。`[[companion.main_kind]]` → `[dish_types]` → 分類の順。"""
    text = _nfkc(title)
    for entry in rules.main_kinds:
        cues = [str(c) for c in (entry.get("cues") or [])]
        not_cues = [str(c) for c in (entry.get("not_cues") or [])]
        if any(c and c in text for c in cues) and not any(c and c in text for c in not_cues):
            return str(entry.get("kind") or "")
    dish_type = ops.classify_dish_type(text, rules.dish_types) or ""
    if dish_type:
        return dish_type
    return str(category or "")


def detect_heat(steps_text: str, rules: Rules) -> str:
    """工程の文から加熱を読む（うちのレシピ用。定番は `heat` を明示している）。"""
    text = _nfkc(steps_text)
    if any(c and c in text for c in rules.heat_cues.get("stove", [])):
        return HEAT_STOVE
    if any(c and c in text for c in rules.heat_cues.get("range", [])):
        return HEAT_RANGE
    return HEAT_NONE


def derive_tags(ingredients: Sequence[str], heat: str, rules: Rules, given: Sequence[str] = ()) -> list[str]:
    """材料名から印を付ける（`[companion.tag_cues]`）。`given`（定番の明示）と合わせる。

    `生野菜` は「火を使わず、`[companion.raw_veg_tags]` の印か材料に当たった」ときだけ。
    """
    names = [_nfkc(n) for n in ingredients if str(n).strip()]
    out: list[str] = [str(t) for t in given if str(t)]
    for tag, cues in rules.tag_cues.items():
        if tag in out:
            continue
        if any(c and c in name for name in names for c in cues):
            out.append(tag)
    if heat == HEAT_NONE and "生野菜" not in out:
        raw_tags = rules.raw_veg.get("tags", [])
        raw_cues = rules.raw_veg.get("cues", [])
        if any(t in out for t in raw_tags) or any(c and c in name for name in names for c in raw_cues):
            out.append("生野菜")
    return out


def companion_kind(title: str, category: str, heat: str, rules: Rules, given: str = "") -> str:
    """お供の調理の型。定番は明示（`given`）、うちのレシピは題名から推定する。

    題名で当たらない副菜は、火を使わなければ `生`（和え物・浅漬けの類）とみなす
    ——「塩昆布の無限キャベツ」は題名に型の語が無いが、揚げ物に合わせたいのはまさにこれ。
    """
    if given:
        return given
    dish_type = ops.classify_dish_type(_nfkc(title), rules.dish_types) or ""
    if dish_type:
        return dish_type
    if category == "汁物":
        return "汁物"
    if heat != HEAT_STOVE:
        return RAW_KIND
    return ""


# --- 栄養（純粋関数） --------------------------------------------------------------------


def floor_deficit(values: Mapping[str, Any], floor: Mapping[str, float], weights: Mapping[str, float]) -> dict[str, float]:
    """下限からの**相対**不足（項目ごと。足りていれば 0。最大 1）。`floor_<項目>` の重みを掛ける。"""
    out: dict[str, float] = {}
    for key, low in floor.items():
        try:
            value = float(values.get(key) or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        short = max(0.0, (low - value) / low) if low > 0 else 0.0
        out[key] = float(weights.get(f"floor_{key}", 1.0)) * min(1.0, short)
    return out


def shortfalls(main: Mapping[str, Any], rules: Rules) -> dict[str, object]:
    """主菜だけのときの「足りないもの・多すぎるもの」（カードの見出しに出す）。

    `micro_known` が偽なら下限の項は出さない（足した5項目が推定できていない）。
    """
    nutrition_values = dict(main.get("nutrition") or {})
    micro = dict(main.get("micro") or {})
    under: list[str] = []
    if main.get("micro_known"):
        # 足りない度合い（相対）の大きい順——画面は頭の数個だけを見せる。
        short = {key: (low - float(micro.get(key) or 0.0)) / low for key, low in rules.floor.items() if low > 0}
        under = [key for key, value in sorted(short.items(), key=lambda kv: -kv[1]) if value > 0]
    over: list[str] = []
    if nutrition_values:
        for key, (_low, high) in rules.band.items():
            if float(nutrition_values.get(key) or 0.0) > high:
                over.append(key)
    return {"under": under, "over": over}


# --- 採点（純粋関数。ここが本体） --------------------------------------------------------


@dataclass
class Scored:
    key: str
    score: float
    reasons: list[dict[str, object]] = field(default_factory=list)


def score(
    main: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    rules: Rules,
    *,
    taste: Mapping[str, Any] | None = None,
) -> list[Scored]:
    """候補を採点して高い順に返す（ADR-021 D2・D3）。**DB も外部も触らない。**

    `main` と各候補の形は `main_from_recipe()`・`candidate_from_recipe()`・
    `candidate_from_catalog()` が作る:
    `nutrition`（5項目。候補は揃っていること）・`micro`（下限の6項目）・`micro_known`・
    `kind`・`category`・`tags`・`heat`・`minutes`・`ingredients`・`source`・`favorite`・`rating`。

    外すのは3つだけ: 嫌い・避ける食材が入っている／主菜の型が `exclude_categories` で
    外す分類（汁のある麺に汁物）／5項目の栄養値が無い。同点は `key` の昇順で安定させる。
    """
    w = rules.weights
    mw = rules.menu_weights
    fill_min = float(rules.rule("fill_min", 0.05))
    reasons_max = int(rules.rule("reasons_max", 3))
    quick_minutes = float(rules.rule("quick_minutes", 15))
    busy_minutes = float(rules.rule("busy_minutes", 30))
    busy_kinds = [str(k) for k in (rules.rule("busy_kinds", []) or [])]

    avoided = menu._split_terms((taste or {}).get("allergies")) + menu._split_terms((taste or {}).get("dislikes"))

    m_kind = str(main.get("kind") or "")
    pairing = rules.pairing.get(m_kind, {})
    note = str(pairing.get("note") or "")
    exclude_categories = [str(c) for c in (pairing.get("exclude_categories") or [])]
    prefer_kinds = [str(k) for k in (pairing.get("prefer_kinds") or [])]
    prefer_tags = [str(t) for t in (pairing.get("prefer_tags") or [])]
    avoid_kinds = [str(k) for k in (pairing.get("avoid_kinds") or [])]
    avoid_tags = [str(t) for t in (pairing.get("avoid_tags") or [])]

    main_minutes = main.get("minutes")
    busy = m_kind in busy_kinds or (main_minutes is not None and float(main_minutes) >= busy_minutes)

    main_nutrition = dict(main.get("nutrition") or {})
    main_micro = dict(main.get("micro") or {})
    use_floor = bool(main.get("micro_known")) and bool(rules.floor)
    band_before = menu.deviation(main_nutrition, rules.band, mw)
    floor_before = floor_deficit(main_micro, rules.floor, w) if use_floor else {}
    band_weight = float(mw.get("band", 1.0))
    floor_weight = float(w.get("floor", 1.0))

    out: list[Scored] = []
    for cand in candidates:
        cand_nutrition = menu.nutrition_of(cand.get("nutrition") or {})
        if cand_nutrition is None:
            continue
        if str(cand.get("category") or "") in exclude_categories:
            continue
        ingredients = [str(n) for n in (cand.get("ingredients") or []) if str(n).strip()]
        if any(ops.item_match(name, avoid) for name in ingredients for avoid in avoided):
            continue

        marks: list[tuple[float, str, dict[str, object]]] = []
        total = 0.0

        # --- 下限の不足を埋める（頭の因子） ---
        if use_floor and cand.get("micro_known"):
            cand_micro = dict(cand.get("micro") or {})
            combined_micro = {k: float(main_micro.get(k) or 0.0) + float(cand_micro.get(k) or 0.0) for k in rules.floor}
            floor_after = floor_deficit(combined_micro, rules.floor, w)
            for key in rules.floor:
                gain = floor_before.get(key, 0.0) - floor_after.get(key, 0.0)
                total += floor_weight * gain
                if gain >= fill_min * float(w.get(f"floor_{key}", 1.0)):
                    marks.append((floor_weight * gain, f"fills_{key}", {"value": round(float(cand_micro.get(key) or 0.0), 1)}))

        # --- 帯への寄与（献立のおすすめと同じ式。塩分の超過は強く減点） ---
        combined = menu.sum_nutrition([main_nutrition, cand_nutrition])
        band_after = menu.deviation(combined, rules.band, mw)
        total += band_weight * sum(band_before.get(k, 0.0) - band_after.get(k, 0.0) for k in rules.band)
        # 塩分の「控えめ」は帯の上限の 1/4 まで。超過の警告は**そのお供自身が控えめでない**
        # ときだけ出す——ラーメン（主菜だけで帯を超える）では、どのお供を足しても合計は
        # 超えたままなので、全部に警告が付くと何も言っていないのと同じになる。
        salt_max = rules.band.get(menu.SALT, (0.0, 0.0))[1]
        salt_low = cand_nutrition[menu.SALT] <= salt_max / 4
        if menu.SALT in rules.band and combined[menu.SALT] > salt_max and not salt_low:
            marks.append((_ALWAYS_SHOW, "salt_over", {"total": combined[menu.SALT], "max": salt_max}))
        elif menu.SALT in rules.band and salt_low:
            marks.append((_NOTE_ONLY, "keeps_salt_low", {"value": cand_nutrition[menu.SALT]}))

        # --- 相性（主菜の型 → お供の型・印） ---
        kind = str(cand.get("kind") or "")
        tags = [str(t) for t in (cand.get("tags") or [])]
        if kind and kind in prefer_kinds:
            gain = float(w.get("pairing_kind", 0.0))
            total += gain
            marks.append((gain, f"pairs_{note}" if note else "pairs", {"main": m_kind}))
        tag_hits = [t for t in tags if t in prefer_tags]
        if tag_hits:
            gain = float(w.get("pairing_tag", 0.0)) * len(tag_hits)
            total += gain
            marks.append((gain, "pairs_tags", {"tags": "・".join(tag_hits)}))
        if (kind and kind in avoid_kinds) or any(t in avoid_tags for t in tags):
            penalty = float(w.get("pairing_avoid", 0.0))
            total -= penalty
            marks.append((penalty, "heavy_pair", {"main": m_kind}))

        # --- 同時に作る（主菜がコンロを塞ぐなら、火を使わないお供を上へ） ---
        heat = str(cand.get("heat") or "")
        if busy and heat in (HEAT_NONE, HEAT_RANGE):
            gain = float(w.get("easy_while_busy", 0.0)) * (1.0 if heat == HEAT_NONE else 0.75)
            total += gain
            marks.append((gain, "easy_while_busy", {"heat": heat}))
        elif busy and heat == HEAT_STOVE:
            total -= float(w.get("stove_while_busy", 0.0))
        minutes = cand.get("minutes")
        if minutes is not None and float(minutes) <= quick_minutes:
            gain = float(w.get("quick", 0.0))
            total += gain
            marks.append((gain, "quick", {"minutes": int(minutes)}))

        # --- うちのもの ---
        if cand.get("source") == SOURCE_RECIPE:
            gain = float(w.get("own_recipe", 0.0))
            total += gain
            marks.append((gain, "own_recipe", {}))
        if cand.get("favorite"):
            gain = float(w.get("favorite", 0.0))
            total += gain
            marks.append((gain, "favorite", {}))
        rating = cand.get("rating")
        if rating is not None and float(rating) > 3:
            total += float(w.get("rating", 0.0)) * (float(rating) - 3)

        out.append(Scored(key=str(cand["key"]), score=total, reasons=menu._pick_reasons(marks, reasons_max)))

    out.sort(key=lambda s: (-s.score, s.key))
    return out


# --- 入力の形を作る（純粋関数） ------------------------------------------------------------


def _micro_of(row: Mapping[str, Any], minimum: float) -> tuple[dict[str, float], bool]:
    """保存された下限の6項目と「分かっているか」（解決率が `minimum` 以上か）。"""
    try:
        coverage = float(row.get("micro_coverage") or 0.0)
    except (TypeError, ValueError):
        coverage = 0.0
    values = {k: float(row.get(k) or 0.0) for k in FLOOR_KEYS}
    known = coverage >= minimum and any(row.get(k) is not None for k in FLOOR_KEYS)
    return values, known


def _steps_text(body: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for step in body.get("steps") or []:
        if isinstance(step, Mapping):
            parts.append(str(step.get("title") or ""))
            parts.append(str(step.get("instruction") or ""))
        else:
            parts.append(str(step))
    return " ".join(parts)


def _body_of(row: Mapping[str, Any]) -> dict[str, Any]:
    body = row.get("body")
    if isinstance(body, str) and body:
        try:
            return dict(json.loads(body))
        except ValueError:
            return {}
    return dict(body or {})


def _ingredient_names(body: Mapping[str, Any]) -> list[str]:
    return [
        str(ing.get("name", "")).strip()
        for ing in (body.get("ingredients") or [])
        if isinstance(ing, Mapping) and str(ing.get("name", "")).strip()
    ]


def main_from_recipe(row: Mapping[str, Any], rules: Rules) -> dict[str, Any]:
    """レシピ（本体＋うちの値の1行）→ 主菜の入力。5項目が無くても受ける（ADR-018 §4.10 と同じ）。"""
    minimum = float(rules.rule("micro_coverage_min", 0.6))
    micro, known = _micro_of(row, minimum)
    title = str(row.get("title") or "")
    category = str(row.get("category") or "")
    return {
        "recipe_id": int(row["id"]),
        "title": title,
        "category": category,
        "kind": main_kind(title, category, rules),
        "minutes": row.get("total_minutes"),
        "nutrition": menu.nutrition_of(row) or {},
        "micro": micro,
        "micro_known": known,
    }


def candidate_from_recipe(row: Mapping[str, Any], rules: Rules) -> dict[str, Any]:
    """うちのレシピ帳の副菜・汁物 → 候補の入力。型・加熱・印は題名と材料・工程から推定する。"""
    minimum = float(rules.rule("micro_coverage_min", 0.6))
    micro, known = _micro_of(row, minimum)
    body = _body_of(row)
    title = str(row.get("title") or "")
    category = str(row.get("category") or "")
    ingredients = _ingredient_names(body)
    heat = detect_heat(_steps_text(body), rules)
    try:
        meta_tags = json.loads(str(row.get("tags") or "[]"))
    except ValueError:
        meta_tags = []
    status_row = {
        "nutrition": {k: row.get(k) for k in menu.NUTRIENTS},
        "nutrition_source": row.get("nutrition_source"),
        "nutrition_coverage": row.get("nutrition_coverage"),
    }
    ok = menu.nutrition_status(status_row, rules.macro_coverage_min) == menu.NUTRITION_OK
    return {
        "key": f"recipe:{int(row['id'])}",
        "source": SOURCE_RECIPE,
        "recipe_id": int(row["id"]),
        "title": title,
        "category": category,
        "cuisine": str(row.get("cuisine") or ""),
        "kind": companion_kind(title, category, heat, rules),
        "heat": heat,
        "tags": derive_tags(ingredients, heat, rules, [t for t in meta_tags if isinstance(t, str)]),
        "minutes": row.get("total_minutes"),
        "hero_image": str(row.get("hero_image") or ""),
        "ingredients": ingredients,
        "nutrition": (menu.nutrition_of(row) or {}) if ok else {},
        "micro": micro,
        "micro_known": known,
        "favorite": bool(row.get("favorite")),
        "rating": row.get("rating"),
    }


def candidate_from_catalog(
    dish: Mapping[str, Any],
    est: nutrition.Estimate,
    rules: Rules,
    sources: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """定番1品（`companions.toml` の `[[dish]]`）＋推定＋サイトの候補 → 候補の入力。

    写真は候補の先頭の画像を借りる（直リンク。レシピ帳の `hero_image` と同じ扱い）。
    """
    min_macro = rules.macro_coverage_min
    minimum = float(rules.rule("micro_coverage_min", 0.6))
    heat = str(dish.get("heat") or HEAT_NONE)
    ingredients = [str(i.get("name") or "") for i in dish.get("ingredients") or [] if isinstance(i, Mapping)]
    return {
        "key": f"catalog:{dish['key']}",
        "source": SOURCE_CATALOG,
        "catalog_key": str(dish["key"]),
        "title": str(dish.get("title") or ""),
        "category": str(dish.get("category") or ""),
        "cuisine": str(dish.get("cuisine") or ""),
        "kind": companion_kind(str(dish.get("title") or ""), str(dish.get("category") or ""), heat, rules, str(dish.get("kind") or "")),
        "heat": heat,
        "tags": derive_tags(ingredients, heat, rules, [str(t) for t in dish.get("tags") or []]),
        "minutes": dish.get("minutes"),
        "hero_image": next((str(src.get("image") or "") for src in sources if src.get("image")), ""),
        "sources": [dict(src) for src in sources],
        "ingredients": ingredients,
        "nutrition": dict(est.nutrition) if est.ok and est.coverage >= min_macro else {},
        "micro": {k: float(est.micro.get(k) or 0.0) for k in FLOOR_KEYS},
        "micro_known": est.ok and est.micro_coverage >= minimum,
        "favorite": False,
        "rating": None,
    }


# --- 定番の一覧（ファイル） ----------------------------------------------------------------


def load_catalog(path: Path | None = None) -> list[dict[str, Any]]:
    """`companions.toml` の `[[dish]]` を読む。`key` の無い行・重複した `key` は捨てる。"""
    p = Path(path) if path is not None else CATALOG_PATH
    if not p.is_file():
        return []
    with p.open("rb") as f:
        data = tomllib.load(f)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in data.get("dish") or []:
        if not isinstance(raw, Mapping):
            continue
        key = str(raw.get("key") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(dict(raw))
    return out


#: 候補1件の鍵（`companion_sources.toml` の `[[source]]`。`dish` は定番の `key`）。
SOURCE_FIELDS: tuple[str, ...] = ("url", "site", "title", "image", "minutes", "why")


def load_sources(path: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    """`companion_sources.toml` → `{定番の key: [候補, …]}`（ファイルの並び順のまま）。

    `url` が http(s) でない行・同じ定番の中の重複 URL は捨てる。ファイルが無ければ空。
    """
    p = Path(path) if path is not None else SOURCES_PATH
    if not p.is_file():
        return {}
    with p.open("rb") as f:
        data = tomllib.load(f)
    out: dict[str, list[dict[str, Any]]] = {}
    for raw in data.get("source") or []:
        if not isinstance(raw, Mapping):
            continue
        dish = str(raw.get("dish") or "").strip()
        url = str(raw.get("url") or "").strip()
        if not dish or not url.startswith(("http://", "https://")):
            continue
        rows = out.setdefault(dish, [])
        if any(normalize_url(r["url"]) == normalize_url(url) for r in rows):
            continue
        row = {key: raw.get(key) for key in SOURCE_FIELDS} | {"url": url}
        for key in ("site", "title", "image", "why"):
            row[key] = str(row.get(key) or "")
        try:
            minutes = int(row.get("minutes") or 0)
        except (TypeError, ValueError):
            minutes = 0
        row["minutes"] = minutes if minutes > 0 else None  # 0 は「分からない」（TOML に null が無い）
        rows.append(row)
    return out


def normalize_url(url: str) -> str:
    """出典 URL の突き合わせ用（scheme・host の大小、末尾の `/`、クエリと `#` を無視する）。"""
    text = str(url or "").strip()
    for sep in ("#", "?"):
        text = text.split(sep, 1)[0]
    if "://" in text:
        scheme, rest = text.split("://", 1)
        host, _, path = rest.partition("/")
        text = f"{scheme.lower()}://{host.lower()}/{path}"
    return text.rstrip("/")


def catalog_recipe(dish: Mapping[str, Any]) -> dict[str, Any]:
    """定番1品 → 栄養の推定に渡す形（題名・人数・材料だけ。作り方は持たない）。"""
    return {
        "title": str(dish.get("title") or ""),
        "servings": dish.get("servings") or 2,
        "total_minutes": dish.get("minutes"),
        "ingredients": [
            {"name": str(i.get("name") or ""), "qty": str(i.get("qty") or ""), "unit": str(i.get("unit") or "")}
            for i in dish.get("ingredients") or []
            if isinstance(i, Mapping)
        ],
    }


# --- DB の層 --------------------------------------------------------------------------------


_RECIPE_SELECT = (
    "SELECT r.id, r.title, r.hero_image, r.source_url, r.servings, r.total_minutes, r.body,"
    " m.kcal, m.protein_g, m.fat_g, m.carb_g, m.salt_g, m.nutrition_source, m.nutrition_coverage,"
    " m.rating, m.favorite, m.tags, m.category, m.main_ingredient, m.cuisine"
)


def _recipe_rows(conn: sqlite3.Connection, where: str, args: Sequence[Any]) -> list[dict[str, Any]]:
    micro_cols = ""
    if nutrition._has_micro_columns(conn):
        micro_cols = ", " + ", ".join(f"m.{c}" for c in nutrition.META_MICRO_COLUMNS)
    rows = conn.execute(
        f"{_RECIPE_SELECT}{micro_cols}"
        " FROM chef_recipe r LEFT JOIN chef_recipe_meta m ON m.recipe_id = r.id"
        f" WHERE r.archived_at IS NULL AND {where}",
        tuple(args),
    ).fetchall()
    return [dict(r) for r in rows]


def _food_ready(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'chef_food'").fetchone()
    if row is None:
        return False
    return int(conn.execute("SELECT COUNT(*) AS n FROM chef_food").fetchone()["n"]) > 0


#: 定番の推定の控え。成分表・名寄せ・定番のファイルが変わらない限り同じ答えなので、
#: 画面を開くたびに約 2,500 行の索引を組み直さない（指紋が変われば捨てる）。
_catalog_cache: dict[str, Any] = {"fingerprint": None, "items": []}


def _catalog_fingerprint(conn: sqlite3.Connection, path: Path) -> tuple[Any, ...]:
    food = conn.execute("SELECT COUNT(*) AS n, MAX(updated_at) AS t FROM chef_food").fetchone()
    alias = conn.execute("SELECT COUNT(*) AS n, MAX(updated_at) AS t FROM chef_food_alias").fetchone()
    try:
        blend = tuple(conn.execute("SELECT COUNT(*) AS n, MAX(updated_at) AS t FROM chef_food_blend").fetchone())
    except sqlite3.OperationalError:
        blend = ()
    try:  # 覚えた換算（ADR-022）が変われば推定も変わる
        units = tuple(conn.execute("SELECT COUNT(*) AS n, MAX(updated_at) AS t FROM chef_food_unit").fetchone())
    except sqlite3.OperationalError:
        units = ()
    stat = path.stat() if path.is_file() else None
    return (
        str(path),
        stat.st_mtime_ns if stat else None,
        tuple(food), tuple(alias), blend, units,
        nutrition._has_micro_columns(conn),
    )


def estimate_catalog(conn: sqlite3.Connection, path: Path | None = None) -> list[tuple[dict[str, Any], nutrition.Estimate]]:
    """定番を全部推定する（成分表が無ければ空）。"""
    p = Path(path) if path is not None else CATALOG_PATH
    if not _food_ready(conn):
        return []
    fp = _catalog_fingerprint(conn, p)
    if _catalog_cache["fingerprint"] == fp:
        return list(_catalog_cache["items"])
    tables = nutrition.tables_for(conn)
    index = nutrition.build_index(nutrition.food_rows(conn), tables)
    aliases = nutrition.alias_map(conn)
    blends = nutrition.blend_map(conn)
    items = [
        (dish, nutrition.estimate_nutrition(catalog_recipe(dish), index, aliases, tables, blends))
        for dish in load_catalog(p)
    ]
    _catalog_cache.update(fingerprint=fp, items=items)
    return list(items)


def _main_row(conn: sqlite3.Connection, recipe_id: int) -> dict[str, Any]:
    rows = _recipe_rows(conn, "r.id = ?", (int(recipe_id),))
    if not rows:
        raise ManorError(
            f"レシピが見つかりません: {recipe_id}",
            code=2,
            key=ERR_RECIPE_NOT_FOUND,
            params={"id": recipe_id},
        )
    return rows[0]


def _summary(cand: Mapping[str, Any], scored: Scored) -> dict[str, object]:
    nutr = dict(cand.get("nutrition") or {})
    return {
        "key": str(cand["key"]),
        "source": cand["source"],
        "recipe_id": cand.get("recipe_id"),
        "catalog_key": cand.get("catalog_key"),
        "title": cand["title"],
        "category": cand["category"],
        "kind": cand["kind"],
        "heat": cand["heat"],
        "tags": list(cand.get("tags") or []),
        "minutes": cand.get("minutes"),
        "hero_image": cand.get("hero_image") or "",
        "sources": list(cand.get("sources") or []),
        "score": round(scored.score, 3),
        "reasons": scored.reasons,
        "nutrition": nutr,
        "micro": dict(cand.get("micro") or {}) if cand.get("micro_known") else {},
    }


def suggest(
    conn: sqlite3.Connection,
    recipe_id: int,
    *,
    lexicon_path: Path | None = None,
    catalog_path: Path | None = None,
    sources_path: Path | None = None,
) -> dict[str, object]:
    """`GET /api/v1/kitchen/recipes/{id}/companions` の応答（ADR-021 D5）。

    主菜の分類が `main_categories` に無い（副菜・汁物・デザートを開いた）ときは
    `eligible: false` と空の一覧を返す——画面はカードを出さない。
    """
    rules = load_rules(lexicon_path)
    row = _main_row(conn, recipe_id)
    main = main_from_recipe(row, rules)
    main_categories = [str(c) for c in (rules.rule("main_categories", []) or [])]
    eligible = not main["category"] or main["category"] in main_categories
    base: dict[str, object] = {
        "recipe_id": int(recipe_id),
        "eligible": eligible,
        "main": {
            "title": main["title"],
            "kind": main["kind"],
            "nutrition": main["nutrition"],
            "micro": main["micro"] if main["micro_known"] else {},
            **shortfalls(main, rules),
        },
        "floor": dict(rules.floor),
        "items": [],
    }
    if not eligible:
        return base

    categories = [str(c) for c in (rules.rule("companion_categories", []) or [])]
    marks = ", ".join("?" for _ in categories) or "''"
    own = [
        candidate_from_recipe(r, rules)
        for r in _recipe_rows(conn, f"m.category IN ({marks}) AND r.id <> ?", (*categories, int(recipe_id)))
    ]
    sources = load_sources(sources_path)
    catalog = [
        candidate_from_catalog(dish, est, rules, sources.get(str(dish["key"]), []))
        for dish, est in estimate_catalog(conn, catalog_path)
    ]
    # 同じ料理がレシピ帳にあれば、定番のほうは出さない（うちのものを優先）。題名が同じもの
    # に加え、**候補の URL を取り込んだレシピ**があれば（分類が副菜でなくても）その定番は畳む。
    imported = {
        normalize_url(str(r["source_url"]))
        for r in conn.execute(
            "SELECT source_url FROM chef_recipe WHERE archived_at IS NULL AND source_url <> ''"
        ).fetchall()
    }
    catalog = [
        c
        for c in catalog
        if not any(menu._dish_matches(c["title"], o["title"]) for o in own)
        and not any(normalize_url(str(src["url"])) in imported for src in c["sources"])
    ]
    pool = own + catalog
    by_key = {c["key"]: c for c in pool}

    taste = {str(r["key"]): str(r["value"]) for r in conn.execute("SELECT key, value FROM chef_taste").fetchall()}
    scored = score(main, pool, rules, taste=taste)
    top_n = int(rules.rule("top_n", 3))
    base["items"] = [_summary(by_key[s.key], s) for s in scored[:top_n]]
    base["catalog_size"] = len(catalog)
    return base


def _catalog_dish(key: str, path: Path | None = None) -> dict[str, Any]:
    for dish in load_catalog(path):
        if str(dish.get("key")) == key:
            return dish
    raise ManorError(
        f"お供の定番が見つかりません: {key}",
        code=2,
        key=ERR_CATALOG_NOT_FOUND,
        params={"key": key},
    )


def plan_together(
    conn: sqlite3.Connection,
    recipe_id: int,
    *,
    date: str,
    slot: str,
    companion_recipe_id: int | None = None,
    catalog_key: str | None = None,
    catalog_path: Path | None = None,
) -> dict[str, object]:
    """「一緒に作る」: 主菜とお供を `chef_meal` に `planned=1` で書く（`menu.plan` に乗る）。

    定番は**レシピ帳に入れずに**書く（`chef_meal` は題名と材料だけを持つ）——作ってみて
    気に入ったら「レシピ帳に入れる」を押す、という順にする。
    """
    if (companion_recipe_id is None) == (catalog_key is None):
        raise ManorError(
            "companion_recipe_id と catalog_key のどちらか1つを渡してください",
            code=2,
            key=ERR_BAD_REQUEST,
            params={"detail": "companion"},
        )
    ids = [int(recipe_id)] + ([int(companion_recipe_id)] if companion_recipe_id is not None else [])
    result = menu.plan(conn, date=date, slot=slot, recipe_ids=ids)
    if catalog_key is not None:
        dish = _catalog_dish(catalog_key, catalog_path)
        names = [str(i.get("name") or "") for i in dish.get("ingredients") or [] if isinstance(i, Mapping)]
        cur = conn.execute(
            "INSERT INTO chef_meal (date, slot, dish, ingredients, note, planned, created_at)"
            " VALUES (?, ?, ?, ?, ?, 1, ?)",
            (date, slot, str(dish.get("title") or ""), "、".join(n for n in names if n), "", util.now()),
        )
        result["items"].append({"id": cur.lastrowid, "catalog_key": catalog_key, "dish": str(dish.get("title") or "")})  # type: ignore[union-attr]
    return result
