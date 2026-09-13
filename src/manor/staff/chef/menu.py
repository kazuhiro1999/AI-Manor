"""献立のおすすめ（ADR-018。第一段＝規則だけ・LLM を呼ばない）。

「献立を決めてくれる」ではなく、**レシピ帳から複数の候補を理由つきで並べる**。
採点は**この module の純粋関数**（`score_candidates`）が全部やり、数値は1つも持たない
——帯・重み・気分の語彙はすべて `lexicon.toml` の `[menu.*]` から読む（ADR-018 D2）。

## 立て付け（`recipes.py`・`media.py` と同じ）

- 採点・帯の比較・気分の語彙合わせ … **純粋関数**（DB も外部も触らない。合成 dict で試験できる）
- `recommend()` / `plan()` … DB を読み書きする薄い層（SQL は web 層に書かない）

CLI（`cli.py`）・Web（`web/api_v1/kitchen.py`）の両方がここの関数を呼ぶ。

## 理由は「定型文の符牒」で返す（実装で決めた細部。ADR-018 §4）

ADR-018 D3 は理由を「短い日本語の定型文」と書いたが、Web は ja/en の2言語を出す
（ADR-012）ので、**ここが日本語の文を組むと英語の画面に日本語が出る**。よって返すのは
`{"code": "fills_protein_g", "params": {...}}` の形にし、文へ直すのは表示側
（CLI は `chef.menu.reason.*`、Web は `kitchen.menu.reason.*`）に委ねる。
定型文であること（LLM に書かせない。D5）は変わらない。

## 採点の考え方（ADR-018 D4 の表を式にしたもの）

**帯への寄与を 1.0 の基準に置き、他の因子はその何割か**で表す。こうすると順位が入れ替わった
理由を「帯より在庫が勝った」と言葉で説明できる。帯への寄与は「その候補を足すと、合計の
**帯からの相対逸脱**がどれだけ減るか」——絶対量ではなく相対量にしてあるので、kcal（数百）と
塩分（数 g）を同じ物差しで足せる。

在庫は**加点のみ**（D4。無くても減点しない）。栄養値の無いレシピは候補から外す（D1）。
嫌い・避ける食材が入っているものは候補から外す（D4）。

## 栄養値の「使える／使えない」（ADR-019 D4 で D1 に足した分）

D1 は当初「5項目が揃っているか」だけだったが、材料からの推定（ADR-019）が入って
**同じ数字の並びに確度の差**が生まれた。よって候補にするのは
`nutrition_source` が `site`／`manual`／`estimated` のいずれかで、かつ `estimated` なら
`nutrition_coverage` が `[menu.rules].nutrition_coverage_min`（既定 0.8）以上のものだけ
（`nutrition_status()` が唯一の判定）。足りないものは `partial` として
`excluded_partial` に数え、画面が「名寄せへ」の導線を出せるようにする。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date as date_cls
from datetime import timedelta
from pathlib import Path
from typing import Any

from manor import util
from manor.errors import ManorError

from . import ops

#: 栄養の5項目（`chef_recipe_meta` の列名と同じ。ADR-015 D1）。この5つが揃っていない
#: レシピは候補から外す（ADR-018 D1）。
NUTRIENTS: tuple[str, ...] = ("kcal", "protein_g", "fat_g", "carb_g", "salt_g")

#: 塩分の列名。超過だけ重みが違う（D4「塩分は超えると強く減点」）ので名前で覚えておく。
SALT = "salt_g"

#: 候補として認める `nutrition_source`（ADR-018 D1 ＋ ADR-019 D4）。空文字（出所の記録が
#: 無い）は入れない——**数字があっても出どころが言えないものは献立の根拠にしない**。
ELIGIBLE_SOURCES: tuple[str, ...] = ("site", "manual", "estimated")

#: 栄養値の使えるかどうか（`nutrition_status`）の語彙。
NUTRITION_OK, NUTRITION_MISSING, NUTRITION_PARTIAL = "ok", "missing", "partial"

#: 枠の名（API・画面の語）。`lexicon.toml` の `[menu.slots]` が `category` の語彙へ写す。
SLOT_KINDS: tuple[str, ...] = ("main", "side", "soup")

#: `chef_meal.slot` の語彙（`schema.sql` の CHECK と一致させる）。`cli.VALID_SLOTS` と
#: 同じ値だが、`cli.py` はこの module を import するので逆向きには import できない。
MEAL_SLOTS: tuple[str, ...] = ("breakfast", "lunch", "dinner", "snack")

#: 帯の比較（`band_check`）の結果の語彙。
BAND_IN, BAND_UNDER, BAND_OVER = "in", "under", "over"

#: 理由の並びで「点が小さくても必ず上位に出す」ものに足す下駄（帯の超過。D3 は理由を
#: 1〜3 個に絞ると決めたので、絞られて超過が消えるのを機構で防ぐ）。
_ALWAYS_SHOW = 1000.0

#: 逆に「点は動かさないお知らせ」の理由の優先度（`keeps_salt_low`・`band_fits`）。
_NOTE_ONLY = 0.15

#: `recommend()` が「そのレシピが見つからない」ときに投げるキー。web は 404 に写す。
ERR_RECIPE_NOT_FOUND = "error.chef.menu_recipe_not_found"
#: 引数が語彙外・範囲外。web は 400 に写す。
ERR_BAD_REQUEST = "error.chef.menu_bad_request"


@dataclass
class Scored:
    """1 候補の採点（ADR-018 D3）。`reasons` は定型文の符牒（上の docstring 参照）。"""

    recipe_id: int
    score: float
    reasons: list[dict[str, object]] = field(default_factory=list)
    nutrition: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "recipe_id": self.recipe_id,
            "score": round(self.score, 3),
            "reasons": self.reasons,
            "nutrition": self.nutrition,
        }


# --- lexicon（数値の唯一の出どころ。ここは読むだけ） ------------------------------------


def _menu_table(path: Path | None = None) -> dict[str, Any]:
    lex = ops.load_lexicon(path)
    table = lex.get("menu") or {}
    return dict(table)  # type: ignore[arg-type]


def load_band(path: Path | None = None) -> dict[str, tuple[float, float]]:
    """1 食・1 人前の帯（ADR-018 D2）。`{"kcal": (600.0, 900.0), ...}`。

    `[menu.band]` に無い項目は帯を持たない（採点にも比較にも出ない）——主人が
    「炭水化物は見ない」と行を消したら、そのとおりに振る舞う。
    """
    raw = _menu_table(path).get("band") or {}
    out: dict[str, tuple[float, float]] = {}
    for key in NUTRIENTS:
        pair = raw.get(key)
        if isinstance(pair, Sequence) and not isinstance(pair, str) and len(pair) == 2:
            out[key] = (float(pair[0]), float(pair[1]))
    return out


def load_weights(path: Path | None = None) -> dict[str, float]:
    """採点の重み（ADR-018 D4）。"""
    raw = _menu_table(path).get("weights") or {}
    return {str(k): float(v) for k, v in dict(raw).items()}


def load_rules(path: Path | None = None) -> dict[str, float]:
    """採点の閾値（件数・日数・分数）。"""
    raw = _menu_table(path).get("rules") or {}
    return {str(k): float(v) for k, v in dict(raw).items()}


def load_moods(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """気分のキーワード → 条件（ADR-018 D5。第一段は語彙の一致だけ）。"""
    raw = _menu_table(path).get("mood") or {}
    return {str(k): dict(v) for k, v in dict(raw).items() if isinstance(v, Mapping)}


def load_slot_categories(path: Path | None = None) -> dict[str, str]:
    """枠の名 → `chef_recipe_meta.category` の語彙（`[menu.slots]`）。"""
    raw = _menu_table(path).get("slots") or {}
    return {str(k): str(v) for k, v in dict(raw).items() if str(v)}


# --- 気分（自由文 → 条件。純粋関数） -----------------------------------------------------


#: 条件の語（`[menu.mood]` の節に書ける鍵）。一覧を持つ鍵と数値の鍵で扱いが違う。
_MOOD_LIST_KEYS: tuple[str, ...] = ("cuisine", "main_ingredient", "dish_types", "exclude_dish_types")
_MOOD_MIN_KEYS: tuple[str, ...] = ("max_minutes",)


def match_mood(text: str, moods: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """自由文 `text` に含まれるキーワードの条件を**合わせて**返す（ADR-018 D5）。

    `{"text": 元の文, "matched": [当たった気分の名], "conditions": {合わせた条件}}`。
    当たらなければ `matched` は空・`conditions` も空（気分を無視して続ける）。

    一覧の鍵は和（どちらの条件に合っても加点）、`max_minutes` は**最小**を採る
    （「早く」と「がっつり」を同時に言われたら、厳しいほうの時間に従う）。
    """
    raw = (text or "").strip()
    matched: list[str] = []
    conditions: dict[str, Any] = {}
    if not raw:
        return {"text": "", "matched": matched, "conditions": conditions}

    for name, cond in moods.items():
        keywords = [str(k) for k in (cond.get("keywords") or [])]
        if name not in keywords:
            keywords.append(name)
        if not any(kw and kw in raw for kw in keywords):
            continue
        matched.append(name)
        for key in _MOOD_LIST_KEYS:
            values = [str(v) for v in (cond.get(key) or [])]
            if values:
                merged = list(conditions.get(key) or [])
                merged.extend(v for v in values if v not in merged)
                conditions[key] = merged
        for key in _MOOD_MIN_KEYS:
            if cond.get(key) is not None:
                value = float(cond[key])
                current = conditions.get(key)
                conditions[key] = value if current is None else min(float(current), value)
    return {"text": raw, "matched": matched, "conditions": conditions}


# --- 栄養（純粋関数） --------------------------------------------------------------------


def nutrition_of(source: Mapping[str, Any]) -> dict[str, float] | None:
    """5項目が揃っていれば float の dict、1つでも欠ければ `None`（ADR-018 D1）。"""
    out: dict[str, float] = {}
    for key in NUTRIENTS:
        value = source.get(key)
        if value is None or value == "":
            return None
        try:
            out[key] = float(value)
        except (TypeError, ValueError):
            return None
    return out


def nutrition_status(cand: Mapping[str, Any], minimum: float) -> str:
    """その候補の栄養値が献立の根拠に足りるか（ADR-018 D1・ADR-019 D4）。

    - `missing` … 5項目が揃っていない、または出所（`nutrition_source`）が無い
    - `partial` … 推定（`estimated`）だが解決率が無い／`minimum` に届かない
    - `ok` … 候補にできる

    **`coverage` が `None` の推定は通さない**（2026-09-13 に改めた）——解決率の無い
    `estimated` は ADR-019 より前に `claude -p` が入れた行、つまり**根拠を言えない数字**
    である。以前は「黙ってレシピが減ると主人が驚く」を理由に通していたが、LLM に栄養値を
    言わせる経路そのものを畳んだ（ADR-019 §5）ので、守る相手がもういない。
    `manor chef nutrition rebuild` が成分表から推定し直せば解決率が入り、この判定に乗る。
    """
    if nutrition_of(cand.get("nutrition") or {}) is None:
        return NUTRITION_MISSING
    source = str(cand.get("nutrition_source") or "")
    if source not in ELIGIBLE_SOURCES:
        return NUTRITION_MISSING
    if source != "estimated":
        return NUTRITION_OK
    coverage = cand.get("nutrition_coverage")
    if coverage is None:
        return NUTRITION_PARTIAL
    try:
        return NUTRITION_OK if float(coverage) >= minimum else NUTRITION_PARTIAL
    except (TypeError, ValueError):
        return NUTRITION_PARTIAL


def sum_nutrition(items: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """複数の栄養値を足す（欠けは 0 として扱う——候補は揃ったものだけなので通常は起きない）。"""
    total = {key: 0.0 for key in NUTRIENTS}
    for item in items:
        for key in NUTRIENTS:
            try:
                total[key] += float(item.get(key) or 0.0)
            except (TypeError, ValueError):
                continue
    return {key: round(value, 1) for key, value in total.items()}


def band_check(total: Mapping[str, Any], band: Mapping[str, tuple[float, float]]) -> dict[str, dict[str, object]]:
    """合計と帯の比較（ADR-018 D3 の `band_check`）。項目ごとに 帯内／不足／超過。

    塩分は下限を 0 にしてあるので「不足」にはならない（少ないほどよい）。
    """
    out: dict[str, dict[str, object]] = {}
    for key, (low, high) in band.items():
        value = float(total.get(key) or 0.0)
        if value < low:
            status = BAND_UNDER
        elif value > high:
            status = BAND_OVER
        else:
            status = BAND_IN
        out[key] = {"total": round(value, 1), "min": low, "max": high, "status": status}
    return out


def deviation(
    total: Mapping[str, Any],
    band: Mapping[str, tuple[float, float]],
    weights: Mapping[str, float],
) -> dict[str, float]:
    """帯からの**相対**逸脱（項目ごと）。帯の中なら 0。

    不足は `(下限 - 値) / 下限`、超過は `(値 - 上限) / 上限`。相対量にするのは、kcal（数百）と
    塩分（数 g）を同じ物差しで足せるようにするため。項目ごとの重み（`band_<項目>`）を掛け、
    塩分の超過にだけ `salt_over` を上乗せする（D4「塩分は超えると強く減点」）。
    """
    out: dict[str, float] = {}
    for key, (low, high) in band.items():
        value = float(total.get(key) or 0.0)
        weight = float(weights.get(f"band_{key}", 1.0))
        if value < low:
            out[key] = weight * ((low - value) / low) if low > 0 else 0.0
        elif value > high:
            over = weight * ((value - high) / high) if high > 0 else 0.0
            if key == SALT:
                over *= float(weights.get("salt_over", 1.0))
            out[key] = over
        else:
            out[key] = 0.0
    return out


# --- 突き合わせの小道具（純粋関数） ------------------------------------------------------


def _normalize_pantry(pantry_items: Sequence[Any] | None) -> list[dict[str, Any]]:
    """在庫を `{"item":…, "expires":…}` の列に揃える（文字列の列も受ける）。"""
    out: list[dict[str, Any]] = []
    for entry in pantry_items or []:
        if isinstance(entry, str):
            item, expires = entry, None
        elif isinstance(entry, Mapping):
            item, expires = str(entry.get("item") or ""), entry.get("expires")
        else:
            continue
        if item.strip():
            out.append({"item": item.strip(), "expires": expires})
    return out


def _split_terms(value: Any) -> list[str]:
    """`chef_taste` の値（読点・カンマ区切りの自由記述）を語の列にする。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    raw = str(value).replace("、", ",").replace("・", ",")
    return [part.strip() for part in raw.split(",") if part.strip()]


def _dish_matches(title: str, dish: str) -> bool:
    """献立の履歴（`chef_meal.dish`）とレシピの題名の突き合わせ。

    `ops.item_match` は材料名向けの緩い規則（先頭と末尾の字が一致すれば当たる）で、
    題名に使うと「肉じゃが」と「肉豆腐」のような別物まで当たってしまう。題名は
    **どちらかがどちらかを含む**ときだけ同じ料理とみなす。
    """
    a, b = (title or "").strip(), (dish or "").strip()
    if not a or not b:
        return False
    return a in b or b in a


def estimate_dish_type(title: str, dish_types_map: Mapping[str, list[str]] | None = None) -> str:
    """題名から調理法を暫定推定する（ADR-018 D4。`[dish_types]` の語彙）。当たらなければ空。"""
    types = dict(dish_types_map) if dish_types_map is not None else ops.dish_types()
    return ops.classify_dish_type(title or "", types) or ""


# --- 採点（純粋関数。ここが本体） --------------------------------------------------------


def score_candidates(
    main: Mapping[str, Any] | None,
    candidates: Sequence[Mapping[str, Any]],
    pantry_items: Sequence[Any] | None = None,
    recent_meals: Sequence[Mapping[str, Any]] | None = None,
    taste: Mapping[str, Any] | None = None,
    mood_conditions: Mapping[str, Any] | None = None,
    band: Mapping[str, tuple[float, float]] | None = None,
    weights: Mapping[str, float] | None = None,
    *,
    slot_kind: str = "side",
    today: str | None = None,
    rules: Mapping[str, float] | None = None,
    dish_types_map: Mapping[str, list[str]] | None = None,
) -> list[Scored]:
    """候補を採点して高い順に返す（ADR-018 D4）。**DB も外部も触らない。**

    - `main`: 主菜（決まっていれば）。`nutrition`・`main_ingredient`・`cuisine`・`title` を見る。
      `None`（主菜も提案する場合）なら多様性の項は 0——**主菜同士の多様性は履歴と評価で付ける**
      （比べる相手がいないため。ADR-018 §4）。
    - `candidates`: `candidate_from_recipe()` が作る形。
    - `slot_kind`: `main`／`side`／`soup`。時間の加点は副菜・汁物だけ（D4）。

    候補から外すのは2つだけ: 栄養値が揃っていない（D1）／嫌い・避ける食材が入っている（D4）。
    在庫は**無くても減点しない**（D4）。同点は `recipe_id` の昇順で安定させる。
    """
    band = dict(band or {})
    weights = dict(weights or {})
    rules = dict(rules or {})
    mood_conditions = dict(mood_conditions or {})
    pantry = _normalize_pantry(pantry_items)
    types_map = dict(dish_types_map) if dish_types_map is not None else ops.dish_types()
    staples = ops.basics()
    today_str = today or util.today()

    history_days = int(rules.get("history_days", 7))
    nutrition_coverage_min = float(rules.get("nutrition_coverage_min", 0.8))
    quick_minutes = float(rules.get("quick_minutes", 15))
    expiring_days = int(rules.get("expiring_days", 3))
    pantry_items_max = int(rules.get("pantry_items_max", 3))
    reasons_max = int(rules.get("reasons_max", 3))

    avoided = _split_terms((taste or {}).get("allergies")) + _split_terms((taste or {}).get("dislikes"))
    liked = _split_terms((taste or {}).get("likes"))

    main_row: Mapping[str, Any] = main or {}
    main_nutrition = dict(main_row.get("nutrition") or {})
    main_ingredient = str(main_row.get("main_ingredient") or "")
    main_cuisine = str(main_row.get("cuisine") or "")
    main_dish_type = str(main_row.get("dish_type") or "")
    if main is not None and not main_dish_type:
        main_dish_type = estimate_dish_type(str(main_row.get("title") or ""), types_map)

    recent_dishes = _recent_dishes(recent_meals, today_str, history_days)
    before = deviation(main_nutrition, band, weights)

    out: list[Scored] = []
    for cand in candidates:
        nutrition = nutrition_of(cand.get("nutrition") or {})
        if nutrition_status(cand, nutrition_coverage_min) != NUTRITION_OK:
            # D1: 栄養値の無いレシピは候補にしない。ADR-019 D4 でここに `partial`
            # （推定の解決率が足りないもの）も加わった。
            continue
        assert nutrition is not None  # noqa: S101 - nutrition_status が ok なら必ず揃っている
        ingredients = [str(name) for name in (cand.get("ingredients") or []) if str(name).strip()]
        if any(ops.item_match(name, avoid) for name in ingredients for avoid in avoided):
            continue  # D4: 嫌い・避ける食材は除外

        title = str(cand.get("title") or "")
        dish_type = str(cand.get("dish_type") or "") or estimate_dish_type(title, types_map)
        # 理由の候補は (大きさ, 符牒, 差し込み) で集め、最後に大きい順に reasons_max 件だけ出す。
        marks: list[tuple[float, str, dict[str, object]]] = []
        score = 0.0

        # --- 帯への寄与（主菜と合わせた合計が帯に近づくか） ---
        combined = sum_nutrition([main_nutrition, nutrition])
        after = deviation(combined, band, weights)
        band_weight = float(weights.get("band", 1.0))
        gains = {key: before.get(key, 0.0) - after.get(key, 0.0) for key in band}
        score += band_weight * sum(gains.values())
        for key, gain in gains.items():
            if gain > 0.05 and before.get(key, 0.0) > 0 and combined[key] <= band[key][1]:
                # 足りなかった項目が埋まった（塩分は下限が 0 なのでここには来ない）。
                marks.append((band_weight * gain, f"fills_{key}", {"value": nutrition[key]}))
        for key, (low, high) in band.items():
            if after.get(key, 0.0) <= 0 or combined[key] <= high:
                continue
            # 超過は**必ず見せる**（点が小さくても主人が気づけるように上位へ回す）。
            code = "salt_over" if key == SALT else f"over_{key}"
            marks.append((_ALWAYS_SHOW + band_weight * after[key], code, {"total": combined[key], "max": high}))
        # 次の2つは**点を足さない**「お知らせ」の理由なので、並びの優先度も小さく置く
        # （実際に点を動かした因子より上に出ると、なぜその順位かの説明にならない）。
        if SALT in band and combined[SALT] <= band[SALT][1] and nutrition[SALT] <= band[SALT][1] / 3:
            marks.append((_NOTE_ONLY, "keeps_salt_low", {"value": nutrition[SALT]}))
        if band and all(value == 0.0 for value in after.values()):
            marks.append((_NOTE_ONLY * 2, "band_fits", {}))

        # --- 多様性（主菜と被らない。主菜の枠では効かない） ---
        if main is not None:
            cand_ingredient = str(cand.get("main_ingredient") or "")
            if main_ingredient and cand_ingredient and cand_ingredient != main_ingredient:
                gain = float(weights.get("diversity_main_ingredient", 0.0))
                score += gain
                marks.append((gain, "main_ingredient_differs", {"main": main_ingredient, "cand": cand_ingredient}))
            if main_dish_type and dish_type and dish_type != main_dish_type:
                gain = float(weights.get("diversity_dish_type", 0.0))
                score += gain
                marks.append((gain, "dish_type_differs", {"main": main_dish_type, "cand": dish_type}))
            cand_cuisine = str(cand.get("cuisine") or "")
            if main_cuisine and cand_cuisine and cand_cuisine == main_cuisine:
                gain = float(weights.get("cuisine_match", 0.0))
                score += gain
                marks.append((gain, "cuisine_match", {"cuisine": cand_cuisine}))

        # --- 在庫（加点のみ。期限が近ければ上乗せ） ---
        used: list[str] = []
        expiring: list[str] = []
        for name in ingredients:
            if ops.is_staple(name, staples):
                continue  # 基礎調味料は在庫に数えない（`[basics]`）
            hit = next((row for row in pantry if ops.item_match(name, str(row["item"]))), None)
            if hit is None:
                continue
            if name not in used:
                used.append(name)
                if ops.is_expiring(hit["expires"], today_str, expiring_days) and hit["expires"] is not None:
                    expiring.append(name)
            if len(used) >= pantry_items_max:
                break
        if used:
            gain = float(weights.get("pantry_item", 0.0)) * len(used)
            score += gain
            marks.append((gain, "pantry_uses", {"items": "・".join(used), "n": len(used)}))
        if expiring:
            gain = float(weights.get("pantry_expiring", 0.0))
            score += gain
            marks.append((gain, "pantry_expiring", {"items": "・".join(expiring)}))

        # --- 履歴（直近に出たものは減点） ---
        recent_hit = next((row for row in recent_dishes if _dish_matches(title, str(row["dish"]))), None)
        last_cooked = str(cand.get("last_cooked_at") or "")
        if recent_hit is None and last_cooked:
            recent_hit = _last_cooked_recently(last_cooked, today_str, history_days)
        if recent_hit is not None:
            penalty = float(weights.get("history_recent", 0.0))
            score -= penalty
            marks.append((penalty, "cooked_recently", {"date": recent_hit["date"]}))
        elif last_cooked:
            gain = float(weights.get("history_not_recent", 0.0))
            score += gain
            marks.append((gain, "not_cooked_recently", {"days": history_days}))

        # --- 好み（好きな食材は加点。嫌いは上で除外済み） ---
        liked_hits = [name for name in ingredients if any(ops.item_match(name, like) for like in liked)]
        if liked_hits:
            gain = float(weights.get("taste_like", 0.0))
            score += gain
            marks.append((gain, "taste_like", {"items": "・".join(liked_hits[:pantry_items_max])}))

        # --- 時間（副菜・汁物で短いものを加点） ---
        minutes = cand.get("total_minutes")
        if slot_kind != "main" and minutes is not None and float(minutes) <= quick_minutes:
            gain = float(weights.get("time_quick", 0.0))
            score += gain
            marks.append((gain, "quick", {"minutes": int(minutes)}))

        # --- 評価・回数（作り慣れたものを少し上に） ---
        rating = cand.get("rating")
        if rating is not None and float(rating) > 3:
            gain = float(weights.get("rating", 0.0)) * (float(rating) - 3)
            score += gain
            marks.append((gain, "well_rated", {"rating": int(rating)}))
        times_cooked = int(cand.get("times_cooked") or 0)
        if times_cooked >= 3:
            gain = float(weights.get("times_cooked", 0.0))
            score += gain
            marks.append((gain, "often_cooked", {"n": times_cooked}))
        if cand.get("favorite"):
            gain = float(weights.get("favorite", 0.0))
            score += gain
            marks.append((gain, "favorite", {}))

        # --- 気分（語彙の一致だけ。合わなくても候補からは外さない） ---
        mood_weight = float(weights.get("mood_match", 0.0))
        mood_label = "・".join(str(v) for v in (mood_conditions.get("labels") or []))
        for key, value in (("cuisine", cand.get("cuisine")), ("main_ingredient", cand.get("main_ingredient"))):
            wanted = [str(v) for v in (mood_conditions.get(key) or [])]
            if wanted and str(value or "") in wanted:
                score += mood_weight
                marks.append((mood_weight, "mood_match", {"mood": mood_label}))
        wanted_types = [str(v) for v in (mood_conditions.get("dish_types") or [])]
        if wanted_types and dish_type and dish_type in wanted_types:
            score += mood_weight
            marks.append((mood_weight, "mood_match", {"mood": mood_label}))
        excluded_types = [str(v) for v in (mood_conditions.get("exclude_dish_types") or [])]
        if excluded_types and dish_type and dish_type in excluded_types:
            score -= mood_weight
            marks.append((mood_weight, "mood_mismatch", {"mood": mood_label}))
        max_minutes = mood_conditions.get("max_minutes")
        if max_minutes is not None and minutes is not None:
            if float(minutes) <= float(max_minutes):
                score += mood_weight
                marks.append((mood_weight, "mood_quick", {"minutes": int(minutes)}))
            else:
                score -= mood_weight
                marks.append((mood_weight, "mood_mismatch", {"mood": mood_label}))

        out.append(
            Scored(
                recipe_id=int(cand["id"]),
                score=score,
                reasons=_pick_reasons(marks, reasons_max),
                nutrition=nutrition,
            )
        )

    out.sort(key=lambda s: (-s.score, s.recipe_id))
    return out


def _recent_dishes(
    recent_meals: Sequence[Mapping[str, Any]] | None, today: str, days: int
) -> list[dict[str, str]]:
    """`chef_meal` の行から「直近 `days` 日」のものだけを抜く（日付が読めない行は捨てる）。"""
    try:
        limit = date_cls.fromisoformat(today) - timedelta(days=days)
    except ValueError:
        return []
    out: list[dict[str, str]] = []
    for meal in recent_meals or []:
        raw_date = str(meal.get("date") or "")
        try:
            when = date_cls.fromisoformat(raw_date)
        except ValueError:
            continue
        if when >= limit:
            out.append({"date": raw_date, "dish": str(meal.get("dish") or "")})
    return out


def _last_cooked_recently(last_cooked_at: str, today: str, days: int) -> dict[str, str] | None:
    """`last_cooked_at`（日時 or 日付）が直近 `days` 日以内なら日付を返す。"""
    stamp = (last_cooked_at or "")[:10]
    try:
        when = date_cls.fromisoformat(stamp)
        limit = date_cls.fromisoformat(today) - timedelta(days=days)
    except ValueError:
        return None
    return {"date": stamp} if when >= limit else None


def _pick_reasons(
    marks: Sequence[tuple[float, str, dict[str, object]]], limit: int
) -> list[dict[str, object]]:
    """理由を大きい順に `limit` 件（ADR-018 D3「1〜3 個」）。同じ符牒は1回だけ。"""
    seen: set[str] = set()
    out: list[dict[str, object]] = []
    for _, code, params in sorted(marks, key=lambda m: -m[0]):
        if code in seen:
            continue
        seen.add(code)
        out.append({"code": code, "params": params})
        if len(out) >= max(1, limit):
            break
    return out


# --- DB（候補の取り出しと献立の書き込み） ------------------------------------------------


def candidate_from_recipe(row: Mapping[str, Any], *, dish_types_map: Mapping[str, list[str]] | None = None) -> dict[str, Any]:
    """DB の1行（レシピ本体＋うちの値）を採点の入力の形へ。調理法は題名から暫定推定する。"""
    body = row.get("body")
    if isinstance(body, str) and body:
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = {}
    else:
        parsed = dict(body or {})
    ingredients = [
        str(ing.get("name", "")).strip()
        for ing in (parsed.get("ingredients") or [])
        if isinstance(ing, Mapping) and str(ing.get("name", "")).strip()
    ]
    title = str(row.get("title") or "")
    return {
        "id": int(row["id"]),
        "title": title,
        "hero_image": str(row.get("hero_image") or ""),
        "total_minutes": row.get("total_minutes"),
        "servings": row.get("servings"),
        "category": str(row.get("category") or ""),
        "main_ingredient": str(row.get("main_ingredient") or ""),
        "cuisine": str(row.get("cuisine") or ""),
        "dish_type": estimate_dish_type(title, dish_types_map),
        "rating": row.get("rating"),
        "favorite": bool(row.get("favorite")),
        "times_cooked": int(row.get("times_cooked") or 0),
        "last_cooked_at": row.get("last_cooked_at"),
        "nutrition": {key: row.get(key) for key in NUTRIENTS},
        "nutrition_source": str(row.get("nutrition_source") or ""),
        # ADR-019 D4: 推定の解決率（`estimated` のときだけ意味を持つ。無ければ None）。
        "nutrition_coverage": row.get("nutrition_coverage"),
        "ingredients": ingredients,
    }


def _all_candidates(conn: sqlite3.Connection, *, dish_types_map: Mapping[str, list[str]] | None = None) -> list[dict[str, Any]]:
    """畳んでいないレシピ全部（うちの値つき）。数千件を超えない前提で素直に読む
    （`recipes.list_recipes` と同じ割り切り）。"""
    rows = conn.execute(
        "SELECT r.id, r.title, r.hero_image, r.servings, r.total_minutes, r.body,"
        " m.kcal, m.protein_g, m.fat_g, m.carb_g, m.salt_g, m.nutrition_source,"
        " m.nutrition_coverage,"
        " m.rating, m.favorite, m.times_cooked, m.last_cooked_at,"
        " m.category, m.main_ingredient, m.cuisine"
        " FROM chef_recipe r LEFT JOIN chef_recipe_meta m ON m.recipe_id = r.id"
        " WHERE r.archived_at IS NULL"
    ).fetchall()
    return [candidate_from_recipe(dict(row), dish_types_map=dish_types_map) for row in rows]


def _pantry_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        {"item": str(row["item"]), "expires": row["expires"]}
        for row in conn.execute("SELECT item, expires FROM chef_pantry").fetchall()
    ]


def _recent_meal_rows(conn: sqlite3.Connection, *, today: str, days: int) -> list[dict[str, Any]]:
    start, end = ops.week_range(today, days)
    return [
        {"date": str(row["date"]), "dish": str(row["dish"])}
        for row in conn.execute(
            "SELECT date, dish FROM chef_meal WHERE date >= ? AND date <= ?", (start, end)
        ).fetchall()
    ]


def _taste_rows(conn: sqlite3.Connection) -> dict[str, str]:
    return {str(row["key"]): str(row["value"]) for row in conn.execute("SELECT key, value FROM chef_taste").fetchall()}


def _summary(cand: Mapping[str, Any]) -> dict[str, object]:
    """候補の表示用の要約（題名・写真・時間。ADR-018 D3）。"""
    return {
        "recipe_id": int(cand["id"]),
        "title": str(cand.get("title") or ""),
        "hero_image": str(cand.get("hero_image") or ""),
        "total_minutes": cand.get("total_minutes"),
        "category": str(cand.get("category") or ""),
        "main_ingredient": str(cand.get("main_ingredient") or ""),
        "cuisine": str(cand.get("cuisine") or ""),
        "dish_type": str(cand.get("dish_type") or ""),
    }


def recommend(
    conn: sqlite3.Connection,
    *,
    main_recipe_id: int | None = None,
    people: int = 2,
    mood: str = "",
    slot: str = "dinner",
    exclude: Sequence[int] | None = None,
    today: str | None = None,
    lexicon_path: Path | None = None,
) -> dict[str, object]:
    """`GET /api/v1/kitchen/menu/recommend` の応答そのもの（ADR-018 D3）。

    枠ごとに最大 `top_n` 件（既定 5）。`main_recipe_id` を渡せばその主菜を固定し、
    省けば主菜の枠も並べる。`combo` は「主菜（固定 or 主菜の 1 位）＋副菜 1 位＋汁物 1 位」。
    """
    if slot not in MEAL_SLOTS:
        raise ManorError(
            f"slot は {'/'.join(MEAL_SLOTS)} のいずれかです: {slot!r}",
            code=2,
            key=ERR_BAD_REQUEST,
            params={"detail": f"slot={slot!r}"},
        )
    if int(people) < 1:
        raise ManorError(
            f"people は 1 以上にしてください: {people!r}",
            code=2,
            key=ERR_BAD_REQUEST,
            params={"detail": f"people={people!r}"},
        )

    band = load_band(lexicon_path)
    weights = load_weights(lexicon_path)
    rules = load_rules(lexicon_path)
    moods = load_moods(lexicon_path)
    slot_categories = load_slot_categories(lexicon_path)
    dish_types_map = ops.dish_types(lexicon_path)
    today_str = today or util.today()
    top_n = int(rules.get("top_n", 5))
    history_days = int(rules.get("history_days", 7))

    applied_mood = match_mood(mood, moods)
    # 理由の差し込みに使う「気分の名」を条件へ添える（表示側が文を組めるようにする）。
    conditions = dict(applied_mood["conditions"])
    if applied_mood["matched"]:
        conditions["labels"] = list(applied_mood["matched"])

    excluded_ids = {int(i) for i in (exclude or [])}
    all_candidates = _all_candidates(conn, dish_types_map=dish_types_map)
    by_id = {int(c["id"]): c for c in all_candidates}

    main_candidate: dict[str, Any] | None = None
    if main_recipe_id is not None:
        main_candidate = by_id.get(int(main_recipe_id))
        if main_candidate is None:
            raise ManorError(
                f"レシピが見つかりません: {main_recipe_id}",
                code=2,
                key=ERR_RECIPE_NOT_FOUND,
                params={"id": main_recipe_id},
            )
        excluded_ids.add(int(main_recipe_id))

    pantry = _pantry_rows(conn)
    recent = _recent_meal_rows(conn, today=today_str, days=history_days)
    taste = _taste_rows(conn)

    # 主菜に栄養値が無いことはある（D1 は**候補**を絞る規則で、主人が選んだ主菜は拒まない）。
    # そのときは帯への寄与は「主菜 0」として数え、多様性・気分だけが効く。
    main_nutrition = nutrition_of(main_candidate["nutrition"]) if main_candidate else None
    main_for_scoring: dict[str, Any] | None = None
    if main_candidate is not None:
        main_for_scoring = dict(main_candidate)
        main_for_scoring["nutrition"] = main_nutrition or {}

    no_nutrition = 0
    partial = 0
    coverage_floor = float(rules.get("nutrition_coverage_min", 0.8))
    slots: dict[str, list[dict[str, object]]] = {}
    for kind in SLOT_KINDS:
        category = slot_categories.get(kind, "")
        if main_candidate is not None and kind == "main":
            slots[kind] = []  # 主菜は決まっている（`main` に載せる）
            continue
        pool = [
            c
            for c in all_candidates
            if c["category"] == category and int(c["id"]) not in excluded_ids
        ]
        statuses = [nutrition_status(c, coverage_floor) for c in pool]
        no_nutrition += sum(1 for st in statuses if st == NUTRITION_MISSING)
        partial += sum(1 for st in statuses if st == NUTRITION_PARTIAL)
        scored = score_candidates(
            None if kind == "main" else main_for_scoring,
            pool,
            pantry,
            recent,
            taste,
            conditions,
            band,
            weights,
            slot_kind=kind,
            today=today_str,
            rules=rules,
            dish_types_map=dish_types_map,
        )
        slots[kind] = [{**_summary(by_id[s.recipe_id]), **s.as_dict()} for s in scored[:top_n]]

    combo = _build_combo(
        main_candidate=main_candidate,
        main_nutrition=main_nutrition,
        slots=slots,
        by_id=by_id,
        band=band,
    )

    return {
        "slot": slot,
        "people": int(people),
        "band": {key: {"min": low, "max": high} for key, (low, high) in band.items()},
        "applied_mood": {
            "text": applied_mood["text"],
            "matched": applied_mood["matched"],
            "conditions": applied_mood["conditions"],
        },
        "main": (
            {**_summary(main_candidate), "nutrition": main_nutrition}
            if main_candidate is not None
            else None
        ),
        "slots": slots,
        "combo": combo,
        "excluded_no_nutrition": no_nutrition,
        # ADR-019 D4: 推定はできたが解決率が足りず候補から外したもの（画面は「名寄せへ」
        # の導線を出せる）。
        "excluded_partial": partial,
        "coverage_min": coverage_floor,
    }


def _build_combo(
    *,
    main_candidate: Mapping[str, Any] | None,
    main_nutrition: dict[str, float] | None,
    slots: Mapping[str, list[dict[str, object]]],
    by_id: Mapping[int, Mapping[str, Any]],
    band: Mapping[str, tuple[float, float]],
) -> dict[str, object]:
    """「主菜＋副菜 1 位＋汁物 1 位」の合計と帯の比較（ADR-018 D3）。

    主菜が固定ならそれ、未指定なら主菜の枠の 1 位を使う。枠が空（候補が無い）なら
    その分は足さない——**候補が揃っていなくても画面は出す**（帯の比較が「不足」に出るだけ）。
    """
    items: list[dict[str, object]] = []
    nutritions: list[Mapping[str, Any]] = []

    if main_candidate is not None:
        items.append({**_summary(main_candidate), "slot_kind": "main"})
        nutritions.append(main_nutrition or {})
    elif slots.get("main"):
        top = slots["main"][0]
        items.append({**_summary(by_id[int(top["recipe_id"])]), "slot_kind": "main"})
        nutritions.append(dict(top["nutrition"]))  # type: ignore[arg-type]

    for kind in ("side", "soup"):
        rows = slots.get(kind) or []
        if not rows:
            continue
        top = rows[0]
        items.append({**_summary(by_id[int(top["recipe_id"])]), "slot_kind": kind})
        nutritions.append(dict(top["nutrition"]))  # type: ignore[arg-type]

    total = sum_nutrition(nutritions)
    return {
        "recipe_ids": [int(i["recipe_id"]) for i in items],
        "items": items,
        "total": total,
        "band_check": band_check(total, band),
    }


def plan(
    conn: sqlite3.Connection,
    *,
    date: str,
    slot: str,
    recipe_ids: Sequence[int],
) -> dict[str, object]:
    """`POST /api/v1/kitchen/menu/plan`。`chef_meal` に `planned=1` で行を書く（D6）。

    献立の保存先を専用の表にせず `chef_meal` に寄せたのは、**書いたそばから履歴の減点に
    効く**ため（ADR-018 D4 の「履歴」は `chef_meal` を見る）。`chef_meal` は
    `recipe_id` を持たないので、題名（`dish`）と材料（読点区切り）を写して書く。
    """
    # 日付の検算は `ops` に任せるが、鍵は `ERR_BAD_REQUEST` に付け替える——web は
    # 「引数の誤りは 400」で揃えたいのに、`ops.validate_date` の鍵は code=2 経由で
    # 404 に写ってしまう（ADR-018 の状態コードは `_menu_error_to_http` が唯一の写し先）。
    try:
        ops.validate_date(date)  # `field` の既定（「日付」）のまま——ここで文言を持たない
    except ManorError as exc:
        raise ManorError(
            exc.message_ja, code=2, key=ERR_BAD_REQUEST, params={"detail": f"date={date!r}"}
        ) from exc
    if slot not in MEAL_SLOTS:
        raise ManorError(
            f"slot は {'/'.join(MEAL_SLOTS)} のいずれかです: {slot!r}",
            code=2,
            key=ERR_BAD_REQUEST,
            params={"detail": f"slot={slot!r}"},
        )
    ids = [int(i) for i in (recipe_ids or [])]
    if not ids:
        raise ManorError(
            "recipe_ids が空です",
            code=2,
            key=ERR_BAD_REQUEST,
            params={"detail": "recipe_ids=[]"},
        )

    now = util.now()
    written: list[dict[str, object]] = []
    for recipe_id in ids:
        row = conn.execute(
            "SELECT id, title, body FROM chef_recipe WHERE id = ? AND archived_at IS NULL",
            (recipe_id,),
        ).fetchone()
        if row is None:
            raise ManorError(
                f"レシピが見つかりません: {recipe_id}",
                code=2,
                key=ERR_RECIPE_NOT_FOUND,
                params={"id": recipe_id},
            )
        cand = candidate_from_recipe(dict(row))
        cur = conn.execute(
            "INSERT INTO chef_meal (date, slot, dish, ingredients, note, planned, created_at)"
            " VALUES (?, ?, ?, ?, ?, 1, ?)",
            (date, slot, cand["title"], "、".join(cand["ingredients"]), "", now),
        )
        written.append({"id": cur.lastrowid, "recipe_id": int(row["id"]), "dish": cand["title"]})
    return {"date": date, "slot": slot, "planned": True, "items": written}
