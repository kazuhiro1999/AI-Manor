"""料理長のレシピ帳（ADR-015 D1・§3）。

契約 JSON（見本: `kitchen-xr/Docs/samples/chahan.recipe.json`）の検算・CRUD・調理セッション。
DB を読み書きするが、ここは純粋関数に近い形を保つ——「何を作るか」「どの候補を推すか」の
判断はしない（`ops.py` の docstring と同じ立て付け）。CLI（`cli.py`）・Web（`web/api_v1/
kitchen.py`）の両方がここの関数を呼ぶ。

## 本体（body）とうちの値（meta）の分離（ADR-015 D1）

`chef_recipe.body` は契約 JSON の `ingredients` / `tools` / `phases` / `steps` を
JSON 文字列で持つ。`title` / `source_url` / `source_site` / `hero_image` / `servings` /
`total_minutes` は列として別に持つ。`chef_recipe_meta` は「うちの値」（栄養・タグ・評価・
メモ・お気に入り・調理回数）——`update()`（本体の丸ごと差し替え）はここに触れない。

## 検算（`validate`）

契約 §3 の形を検算し、正規化した dict を返す。違反はすべて `ManorError(code=2)`——
CLI の終了コード2（見つからない・語彙外と同じ扱い）、Web は 400 へ写る
（`web/_common.manor_error_to_http`）。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from manor import util
from manor.errors import ManorError

from . import ops

#: `steps[].completion` の語彙（ADR-015 §3）。
VALID_COMPLETION: tuple[str, ...] = ("manual", "auto", "confirm")

#: `chef_recipe_meta.nutrition_source` の語彙（schema.sql の CHECK と一致させる）。
#: `"site"`（出典の表示値。ADR-015 §6 追補）は既存 DB では `db.py` の表の作り直しで
#: 冪等に足す——SQLite は `ALTER TABLE` で `CHECK` 制約を変えられないため。
VALID_NUTRITION_SOURCE: tuple[str, ...] = ("", "estimated", "manual", "site")

#: `chef_cook_event.type` の語彙（ADR-015 D3）。
VALID_EVENT_TYPES: tuple[str, ...] = ("next", "prev", "timer_start", "done")

#: `list_recipes(sort=...)` の語彙（ADR-015 D9 追補）。
VALID_SORT: tuple[str, ...] = ("recent", "cooked", "title")

_TITLE_MAX = 12
#: 工程の本文の上限（ADR-015 §3）。**60 → 100 へ広げた**（2026-09-13。主人の実測: クラシル
#: 2件・白ごはん.com 4件・Nadia 3件が60字を超え、取り込みのたびに警告が並んだ）。
#: 出典の文は1文で60字を超えることが珍しくない——**切らない方針は変えない**ので、
#: 上限は「画面で直す前に気づくための目安」であり、超えた分は `warnings` に出るだけ。
_INSTRUCTION_MAX = 100

#: `set_meta` の「渡さなかった」を表す番人（`None` は「明示的に空にする」と区別する）。
_UNSET: Any = object()


# --- 検算（純粋関数。DB を触らない） -------------------------------------------------


def _as_int_or_none(value: object, *, field: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ManorError(
            f"{field} は整数である必要があります: {value!r}", code=2
        ) from exc


def _validate_ingredient(ing: object, index: int) -> dict[str, object]:
    if not isinstance(ing, dict):
        raise ManorError(f"ingredients[{index}] はオブジェクトである必要があります", code=2)
    name = str(ing.get("name") or "").strip()
    if not name:
        raise ManorError(f"ingredients[{index}].name が必須です", code=2)
    return {
        "name": name,
        "qty": str(ing.get("qty") or ""),
        "unit": str(ing.get("unit") or ""),
        "prep": str(ing.get("prep") or ""),
        "group": str(ing.get("group") or ""),
    }


def _validate_phases(phases: object) -> list[dict[str, str]]:
    if not isinstance(phases, list) or not phases:
        raise ManorError("phases が必須です（最低1つ）", code=2)
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for i, ph in enumerate(phases):
        if not isinstance(ph, dict):
            raise ManorError(f"phases[{i}] はオブジェクトである必要があります", code=2)
        pid = str(ph.get("id") or "").strip()
        ptitle = str(ph.get("title") or "").strip()
        if not pid or not ptitle:
            raise ManorError(f"phases[{i}] は id と title が必須です", code=2)
        if pid in seen:
            raise ManorError(f"phases の id が重複しています: {pid!r}", code=2)
        seen.add(pid)
        out.append({"id": pid, "title": ptitle})
    return out


def _validate_step(step: object, index: int, phase_ids: set[str]) -> dict[str, object]:
    if not isinstance(step, dict):
        raise ManorError(f"steps[{index}] はオブジェクトである必要があります", code=2)

    step_index = _as_int_or_none(step.get("index"), field=f"steps[{index}].index")
    if step_index is None:
        raise ManorError(f"steps[{index}].index が必須です", code=2)

    title = str(step.get("title") or "").strip()
    if not title:
        raise ManorError(f"steps[{index}].title が必須です", code=2)
    if len(title) > _TITLE_MAX:
        raise ManorError(
            f"steps[{index}].title は{_TITLE_MAX}文字以内にしてください（{len(title)}文字）: {title!r}",
            code=2,
        )

    instruction = str(step.get("instruction") or "").strip()
    if not instruction:
        raise ManorError(f"steps[{index}].instruction が必須です", code=2)
    if len(instruction) > _INSTRUCTION_MAX:
        raise ManorError(
            f"steps[{index}].instruction は{_INSTRUCTION_MAX}文字以内にしてください"
            f"（{len(instruction)}文字）: {instruction!r}",
            code=2,
        )

    phase = str(step.get("phase") or "").strip()
    if phase not in phase_ids:
        raise ManorError(
            f"steps[{index}].phase が phases に無い id を指しています: {phase!r}", code=2
        )

    completion = str(step.get("completion") or "").strip()
    if completion not in VALID_COMPLETION:
        raise ManorError(
            f"steps[{index}].completion は {'/'.join(VALID_COMPLETION)} のいずれかです: {completion!r}",
            code=2,
        )

    timer_sec = _as_int_or_none(step.get("timer_sec"), field=f"steps[{index}].timer_sec")

    ingredients_used = step.get("ingredients_used") or []
    if not isinstance(ingredients_used, list):
        raise ManorError(f"steps[{index}].ingredients_used はリストである必要があります", code=2)

    tips = step.get("tips") or []
    if not isinstance(tips, list):
        raise ManorError(f"steps[{index}].tips はリストである必要があります", code=2)

    image = step.get("image")
    image = str(image) if image else None

    return {
        "index": step_index,
        "phase": phase,
        "title": title,
        "instruction": instruction,
        "image": image,
        "ingredients_used": [str(x) for x in ingredients_used],
        "timer_sec": timer_sec,
        "completion": completion,
        "tips": [str(x) for x in tips],
    }


def validate(recipe: dict) -> dict:
    """契約 §3 の形を検算し、正規化した dict を返す（`id`・`meta` は含まない——
    `id` は DB が振り、`meta` は `chef_recipe_meta`/`set_meta` の領分なのでここでは扱わない）。
    """
    if not isinstance(recipe, dict):
        raise ManorError("レシピは JSON オブジェクトである必要があります", code=2)

    title = str(recipe.get("title") or "").strip()
    if not title:
        raise ManorError("title が必須です", code=2)

    ingredients = recipe.get("ingredients") or []
    if not isinstance(ingredients, list):
        raise ManorError("ingredients はリストである必要があります", code=2)
    norm_ingredients = [_validate_ingredient(ing, i) for i, ing in enumerate(ingredients)]

    tools = recipe.get("tools") or []
    if not isinstance(tools, list):
        raise ManorError("tools はリストである必要があります", code=2)
    norm_tools = [str(t) for t in tools]

    norm_phases = _validate_phases(recipe.get("phases"))
    phase_ids = {p["id"] for p in norm_phases}

    steps = recipe.get("steps") or []
    if not isinstance(steps, list) or not steps:
        raise ManorError("steps が必須です（最低1つ）", code=2)
    norm_steps = [_validate_step(st, i, phase_ids) for i, st in enumerate(steps)]

    indices = sorted(s["index"] for s in norm_steps)
    if indices != list(range(1, len(indices) + 1)):
        raise ManorError(
            f"steps.index は1からの連番である必要があります（受け取った値: {indices}）", code=2
        )
    norm_steps.sort(key=lambda s: s["index"])

    return {
        "title": title,
        "source_url": str(recipe.get("source_url") or ""),
        "source_site": str(recipe.get("source_site") or ""),
        "hero_image": str(recipe.get("hero_image") or ""),
        "servings": _as_int_or_none(recipe.get("servings"), field="servings"),
        "total_minutes": _as_int_or_none(recipe.get("total_minutes"), field="total_minutes"),
        "ingredients": norm_ingredients,
        "tools": norm_tools,
        "phases": norm_phases,
        "steps": norm_steps,
    }


def classify(recipe: dict, *, site_tags: list[str] | None = None) -> dict[str, str]:
    """材料名・題名・タグ（`site_tags` にサイト側のカテゴリ・keywords を渡してよい）から
    分類3軸を推定する（ADR-015 D9）。当たらなければ空文字——手がかり語は
    `lexicon.toml` が唯一の出どころ（`ops.recipe_*_cues`）。
    """
    parts: list[str] = [str(recipe.get("title") or "")]
    for ing in recipe.get("ingredients") or []:
        if isinstance(ing, dict):
            parts.append(str(ing.get("name") or ""))
    meta = recipe.get("meta")
    if isinstance(meta, dict):
        parts.extend(str(t) for t in (meta.get("tags") or []))
    parts.extend(str(t) for t in (site_tags or []))
    # **小文字に均す**（2026-09-13）。`lexicon.toml` の手がかり語に英語（`side dish`・
    # `japanese`）が入り、出典サイトは `Side dish` のように大文字で書くことがある。
    # 日本語は `lower()` で変わらないので、既存の手がかり語の当たり方は変わらない。
    haystack = " ".join(parts).lower()

    return {
        "category": ops.classify_dish_type(haystack, ops.recipe_category_cues()) or "",
        "main_ingredient": ops.classify_dish_type(haystack, ops.recipe_main_ingredient_cues()) or "",
        "cuisine": ops.classify_dish_type(haystack, ops.recipe_cuisine_cues()) or "",
    }


def _body_json(v: dict) -> str:
    return json.dumps(
        {"ingredients": v["ingredients"], "tools": v["tools"], "phases": v["phases"], "steps": v["steps"]},
        ensure_ascii=False,
    )


# --- CRUD -----------------------------------------------------------------------------


def _recipe_row(conn: sqlite3.Connection, recipe_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM chef_recipe WHERE id = ?", (recipe_id,)).fetchone()
    if row is None:
        raise ManorError(f"レシピが見つかりません: {recipe_id}", code=2)
    return row


def _default_meta() -> dict[str, object]:
    return {
        "kcal": None, "protein_g": None, "fat_g": None, "carb_g": None, "salt_g": None,
        "nutrition_source": "", "tags": [], "rating": None, "memo": "",
        "favorite": False, "times_cooked": 0, "last_cooked_at": None,
        "category": "", "main_ingredient": "", "cuisine": "",
    }


def _meta_dict(row: sqlite3.Row | None) -> dict[str, object]:
    if row is None:
        return _default_meta()
    row_keys = row.keys()
    return {
        "kcal": row["kcal"], "protein_g": row["protein_g"], "fat_g": row["fat_g"],
        "carb_g": row["carb_g"], "salt_g": row["salt_g"],
        "nutrition_source": row["nutrition_source"],
        "tags": json.loads(row["tags"]) if row["tags"] else [],
        "rating": row["rating"], "memo": row["memo"],
        "favorite": bool(row["favorite"]), "times_cooked": row["times_cooked"],
        "last_cooked_at": row["last_cooked_at"],
        # 追補（ADR-015 D9）より前に作られた行の想定は無い（列は db.migrate_core/init が
        # 先に足す）が、念のため無ければ空文字にする。
        "category": row["category"] if "category" in row_keys else "",
        "main_ingredient": row["main_ingredient"] if "main_ingredient" in row_keys else "",
        "cuisine": row["cuisine"] if "cuisine" in row_keys else "",
    }


def _to_contract(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, object]:
    body = json.loads(row["body"])
    meta_row = conn.execute(
        "SELECT * FROM chef_recipe_meta WHERE recipe_id = ?", (row["id"],)
    ).fetchone()
    return {
        "id": row["id"],
        "title": row["title"],
        "source_url": row["source_url"],
        "source_site": row["source_site"],
        "hero_image": row["hero_image"],
        "servings": row["servings"],
        "total_minutes": row["total_minutes"],
        "ingredients": body.get("ingredients", []),
        "tools": body.get("tools", []),
        "phases": body.get("phases", []),
        "steps": body.get("steps", []),
        "meta": _meta_dict(meta_row),
    }


def add(conn: sqlite3.Connection, recipe: dict) -> int:
    """検算して登録する。`chef_recipe_meta` は空の行を添えて作る（うちの値は
    `set_meta` から後で入れる。ADR-015 D1「本体とうちの値を分ける」）。

    **ただし** 渡した `recipe` に `meta.category`/`main_ingredient`/`cuisine`/`tags` が
    あれば、登録と同時にそこへ入れる（ADR-015 D9 追補。取り込みの下書きは
    `recipe_import.extract_auto()` が `classify()` と出典のタグで `meta` を埋めて返す
    ——編集して「登録」まで1つのフォームで完結させるための橋渡し）。**栄養価
    （`kcal`/`protein_g`/`fat_g`/`carb_g`/`salt_g`）と `nutrition_source` も同じ理由で
    渡っていれば入れる**（ADR-015 §6 追補。`recipe_import._merge_nutrition()` が
    サイトの表示値を `meta` へ入れて返す——`nutrition_source='site'` を明示するので
    `set_meta` の「数値を渡したら自動で manual」は働かない）。`meta` の他の欄
    （評価・メモ・favorite）は従来どおり `set_meta` の領分のまま触らない。
    """
    v = validate(recipe)
    now = util.now()
    cur = conn.execute(
        "INSERT INTO chef_recipe"
        " (title, source_url, source_site, hero_image, servings, total_minutes, body,"
        "  created_at, updated_at, archived_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
        (
            v["title"], v["source_url"], v["source_site"], v["hero_image"],
            v["servings"], v["total_minutes"], _body_json(v), now, now,
        ),
    )
    recipe_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO chef_recipe_meta (recipe_id, nutrition_source, tags, favorite, times_cooked)"
        " VALUES (?, '', '[]', 0, 0)",
        (recipe_id,),
    )
    meta_seed = recipe.get("meta")
    if isinstance(meta_seed, dict):
        seed_kwargs: dict[str, object] = {}
        for key in ("category", "main_ingredient", "cuisine"):
            if meta_seed.get(key):
                seed_kwargs[key] = meta_seed[key]
        if meta_seed.get("tags"):
            seed_kwargs["tags"] = meta_seed["tags"]
        for key in ("kcal", "protein_g", "fat_g", "carb_g", "salt_g"):
            if meta_seed.get(key) is not None:
                seed_kwargs[key] = meta_seed[key]
        if meta_seed.get("nutrition_source"):
            seed_kwargs["nutrition_source"] = meta_seed["nutrition_source"]
        if seed_kwargs:
            set_meta(conn, recipe_id, **seed_kwargs)
    return recipe_id


def get(conn: sqlite3.Connection, recipe_id: int) -> dict[str, object]:
    row = _recipe_row(conn, recipe_id)
    return _to_contract(conn, row)


def list_recipes(
    conn: sqlite3.Connection,
    *,
    q: str | None = None,
    tag: str | None = None,
    favorite: bool | None = None,
    category: str | None = None,
    main_ingredient: str | None = None,
    cuisine: str | None = None,
    sort: str = "recent",
    include_archived: bool = False,
) -> list[dict[str, object]]:
    """一覧（ADR-015 D3・D9追補）。各行:
    `id・title・hero_image・total_minutes・servings・tags・favorite・times_cooked・
    last_cooked_at・kcal・category・main_ingredient・cuisine・updated_at`。

    `q` は題名**と材料名**（`body` の JSON を読んで突き合わせる）。`sort` は
    `recent`（既定。`updated_at` 降順）・`cooked`（`last_cooked_at` 降順。NULL は末尾）・
    `title`（あいうえお順）。

    突き合わせは Python 側で行う（`chef_pantry` の在庫規模と同じ想定——1人分のレシピ帳が
    数千件を超えることは無いので、SQL の JSON 関数に頼らず素直に読む）。
    """
    if sort not in VALID_SORT:
        raise ManorError(
            f"sort は {'/'.join(VALID_SORT)} のいずれかです: {sort!r}", code=2
        )

    rows = conn.execute(
        "SELECT r.*, m.tags AS meta_tags, m.favorite AS meta_favorite,"
        " m.times_cooked AS meta_times_cooked, m.last_cooked_at AS meta_last_cooked_at,"
        " m.kcal AS meta_kcal, m.category AS meta_category,"
        " m.main_ingredient AS meta_main_ingredient, m.cuisine AS meta_cuisine"
        " FROM chef_recipe r LEFT JOIN chef_recipe_meta m ON m.recipe_id = r.id"
    ).fetchall()

    out: list[dict[str, object]] = []
    for row in rows:
        if not include_archived and row["archived_at"] is not None:
            continue
        tags = json.loads(row["meta_tags"]) if row["meta_tags"] else []
        fav = bool(row["meta_favorite"]) if row["meta_favorite"] is not None else False
        row_category = row["meta_category"] or ""
        row_main_ingredient = row["meta_main_ingredient"] or ""
        row_cuisine = row["meta_cuisine"] or ""

        if q:
            body = json.loads(row["body"])
            ingredient_names = " ".join(
                str(ing.get("name", "")) for ing in body.get("ingredients") or []
            )
            haystack = f"{row['title']} {ingredient_names}".lower()
            if q.lower() not in haystack:
                continue
        if tag and tag not in tags:
            continue
        if favorite is not None and fav != bool(favorite):
            continue
        if category and row_category != category:
            continue
        if main_ingredient and row_main_ingredient != main_ingredient:
            continue
        if cuisine and row_cuisine != cuisine:
            continue

        out.append(
            {
                "id": row["id"],
                "title": row["title"],
                "hero_image": row["hero_image"],
                "total_minutes": row["total_minutes"],
                "servings": row["servings"],
                "tags": tags,
                "favorite": fav,
                "times_cooked": row["meta_times_cooked"] or 0,
                "last_cooked_at": row["meta_last_cooked_at"],
                "kcal": row["meta_kcal"],
                "category": row_category,
                "main_ingredient": row_main_ingredient,
                "cuisine": row_cuisine,
                "updated_at": row["updated_at"],
            }
        )

    if sort == "title":
        out.sort(key=lambda r: str(r["title"]))
    elif sort == "cooked":
        # last_cooked_at は ISO8601（辞書順=時系列順）。NULL は "" として最小値扱いにし、
        # reverse=True で末尾へ回す。
        out.sort(key=lambda r: str(r["last_cooked_at"] or ""), reverse=True)
    else:  # "recent"（既定）
        out.sort(key=lambda r: str(r["updated_at"]), reverse=True)

    return out


def facets(conn: sqlite3.Connection) -> dict[str, list[dict[str, object]]]:
    """一覧の chip 列用の集計（ADR-015 D9追補）。**畳んだもの（archived）を除いた件数**。

    返り値: `{"category":[{"value","count"}],"main_ingredient":[...],"cuisine":[...],
    "tags":[...]}`。件数の多い順（同数は値の辞書順）。
    """
    rows = conn.execute(
        "SELECT m.category AS category, m.main_ingredient AS main_ingredient,"
        " m.cuisine AS cuisine, m.tags AS tags"
        " FROM chef_recipe r LEFT JOIN chef_recipe_meta m ON m.recipe_id = r.id"
        " WHERE r.archived_at IS NULL"
    ).fetchall()

    def _count(values: list[str]) -> list[dict[str, object]]:
        counts: dict[str, int] = {}
        for v in values:
            if not v:
                continue
            counts[v] = counts.get(v, 0) + 1
        return sorted(
            ({"value": k, "count": n} for k, n in counts.items()),
            key=lambda d: (-int(d["count"]), str(d["value"])),
        )

    tags: list[str] = []
    for row in rows:
        if row["tags"]:
            tags.extend(json.loads(row["tags"]))

    return {
        "category": _count([row["category"] or "" for row in rows]),
        "main_ingredient": _count([row["main_ingredient"] or "" for row in rows]),
        "cuisine": _count([row["cuisine"] or "" for row in rows]),
        "tags": _count(tags),
    }


def update(conn: sqlite3.Connection, recipe_id: int, recipe: dict) -> dict[str, object]:
    """本体の丸ごと差し替え。**`chef_recipe_meta` には触れない**（ADR-015 D1）。"""
    _recipe_row(conn, recipe_id)
    v = validate(recipe)
    now = util.now()
    conn.execute(
        "UPDATE chef_recipe SET title = ?, source_url = ?, source_site = ?, hero_image = ?,"
        " servings = ?, total_minutes = ?, body = ?, updated_at = ? WHERE id = ?",
        (
            v["title"], v["source_url"], v["source_site"], v["hero_image"],
            v["servings"], v["total_minutes"], _body_json(v), now, recipe_id,
        ),
    )
    return get(conn, recipe_id)


def set_meta(
    conn: sqlite3.Connection,
    recipe_id: int,
    *,
    kcal: Any = _UNSET,
    protein_g: Any = _UNSET,
    fat_g: Any = _UNSET,
    carb_g: Any = _UNSET,
    salt_g: Any = _UNSET,
    nutrition_source: Any = _UNSET,
    tags: Any = _UNSET,
    rating: Any = _UNSET,
    memo: Any = _UNSET,
    favorite: Any = _UNSET,
    category: Any = _UNSET,
    main_ingredient: Any = _UNSET,
    cuisine: Any = _UNSET,
) -> dict[str, object]:
    """うちの値の部分更新。**渡した欄だけ**書き換える（渡さなかった引数は既定値の
    `_UNSET` のままなので、SQL の SET句にも入らない）。

    栄養の数値（`kcal`/`protein_g`/`fat_g`/`carb_g`/`salt_g`）を1つでも手で渡し、
    かつ `nutrition_source` を明示していなければ、`nutrition_source` を自動で
    `'manual'` にする（ADR-015 D1「栄養の数値を手で渡したら manual」）。

    `category`/`main_ingredient`/`cuisine` は語彙外なら `ManorError(code=2)`
    （ADR-015 D9。語彙の唯一の出どころは `lexicon.toml`＝`ops.recipe_*_values()`）。
    空文字は「未分類に戻す」として常に許す。
    """
    _recipe_row(conn, recipe_id)
    conn.execute(
        "INSERT OR IGNORE INTO chef_recipe_meta (recipe_id, nutrition_source, tags, favorite, times_cooked)"
        " VALUES (?, '', '[]', 0, 0)",
        (recipe_id,),
    )

    numeric_fields = {"kcal": kcal, "protein_g": protein_g, "fat_g": fat_g, "carb_g": carb_g, "salt_g": salt_g}
    numeric_given = any(v is not _UNSET for v in numeric_fields.values())
    if numeric_given and nutrition_source is _UNSET:
        nutrition_source = "manual"

    if nutrition_source is not _UNSET and nutrition_source not in VALID_NUTRITION_SOURCE:
        raise ManorError(
            f"nutrition_source は 空文字/estimated/manual/site のいずれかです: {nutrition_source!r}",
            code=2,
        )
    if rating is not _UNSET and rating is not None:
        try:
            rating = int(rating)
        except (TypeError, ValueError) as exc:
            raise ManorError(f"rating は整数である必要があります: {rating!r}", code=2) from exc
        if not (1 <= rating <= 5):
            raise ManorError(f"rating は1〜5の範囲です: {rating!r}", code=2)
    if tags is not _UNSET:
        if not isinstance(tags, list):
            raise ManorError("tags はリストである必要があります", code=2)
        tags = [str(t) for t in tags]

    if category is not _UNSET and category:
        values = ops.recipe_category_values()
        if category not in values:
            raise ManorError(
                f"category は次のいずれかにしてください: {', '.join(values)}（受け取った値: {category!r}）",
                code=2,
            )
    if main_ingredient is not _UNSET and main_ingredient:
        values = ops.recipe_main_ingredient_values()
        if main_ingredient not in values:
            raise ManorError(
                f"main_ingredient は次のいずれかにしてください: {', '.join(values)}"
                f"（受け取った値: {main_ingredient!r}）",
                code=2,
            )
    if cuisine is not _UNSET and cuisine:
        values = ops.recipe_cuisine_values()
        if cuisine not in values:
            raise ManorError(
                f"cuisine は次のいずれかにしてください: {', '.join(values)}（受け取った値: {cuisine!r}）",
                code=2,
            )

    columns: list[str] = []
    params: list[object] = []
    for col, value in (
        ("kcal", kcal), ("protein_g", protein_g), ("fat_g", fat_g),
        ("carb_g", carb_g), ("salt_g", salt_g), ("nutrition_source", nutrition_source),
        ("memo", memo), ("category", category), ("main_ingredient", main_ingredient),
        ("cuisine", cuisine),
    ):
        if value is not _UNSET:
            columns.append(col)
            params.append(value)
    if tags is not _UNSET:
        columns.append("tags")
        params.append(json.dumps(tags, ensure_ascii=False))
    if rating is not _UNSET:
        columns.append("rating")
        params.append(rating)
    if favorite is not _UNSET:
        columns.append("favorite")
        params.append(1 if favorite else 0)

    if columns:
        set_clause = ", ".join(f"{c} = ?" for c in columns)
        params.append(recipe_id)
        conn.execute(f"UPDATE chef_recipe_meta SET {set_clause} WHERE recipe_id = ?", params)

    return get(conn, recipe_id)


def archive(conn: sqlite3.Connection, recipe_id: int) -> dict[str, object]:
    """畳む（消さない）。既に畳んでいれば何もしない（冪等。`user.archive` と同じ約束）。"""
    row = _recipe_row(conn, recipe_id)
    if row["archived_at"] is None:
        conn.execute(
            "UPDATE chef_recipe SET archived_at = ? WHERE id = ?", (util.now(), recipe_id)
        )
    return get(conn, recipe_id)


# --- 調理セッション（ADR-015 D3） -------------------------------------------------------


def _session_row(conn: sqlite3.Connection, session_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM chef_cook_session WHERE id = ?", (session_id,)
    ).fetchone()
    if row is None:
        raise ManorError(f"調理セッションが見つかりません: {session_id}", code=2)
    return row


def start_session(conn: sqlite3.Connection, recipe_id: int, *, user_id: str) -> dict[str, object]:
    """調理開始。**同じ利用者の未終了セッションがあればそれを返す**（ADR-015 D3の文言
    どおり——レシピを問わない。「途中の調理が1件だけ」という前提を機構で表す。
    復帰は `current_session` と同じ経路を通るので、ここで新しく作ってしまうと
    途中のセッションが行方不明になる）。
    """
    _recipe_row(conn, recipe_id)
    existing = conn.execute(
        "SELECT * FROM chef_cook_session WHERE user_id = ? AND ended_at IS NULL"
        " ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if existing is not None:
        return {"id": existing["id"], "current": existing["current"]}

    now = util.now()
    cur = conn.execute(
        "INSERT INTO chef_cook_session (recipe_id, user_id, current, started_at, ended_at)"
        " VALUES (?, ?, 1, ?, NULL)",
        (recipe_id, user_id, now),
    )
    return {"id": int(cur.lastrowid), "current": 1}


def current_session(conn: sqlite3.Connection, *, user_id: str) -> dict[str, object] | None:
    """途中起動の復帰。未終了セッションが無ければ `None`。"""
    row = conn.execute(
        "SELECT * FROM chef_cook_session WHERE user_id = ? AND ended_at IS NULL"
        " ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if row is None:
        return None
    return {"id": row["id"], "recipe_id": row["recipe_id"], "current": row["current"]}


def apply_event(
    conn: sqlite3.Connection, session_id: int, type_: str, *, step: int | None = None
) -> dict[str, object]:
    """工程の進行。`prev` は1で止まり、`next` は steps の件数（n）で止まる。

    **`next` で n に達しても `done` と同じ扱いにはしない**（ADR-015 D3）——`timer_start`/
    `done` はその工程の合図をそのまま記録するだけで `current` を動かさない。工程の完了
    （`completion`）の確定は画面／XR 側の判断であって、ここでは「今どこにいるか」だけを
    機械的に進退させる。
    """
    row = _session_row(conn, session_id)
    if row["ended_at"] is not None:
        raise ManorError(f"調理セッションは既に終了しています: {session_id}", code=2)
    if type_ not in VALID_EVENT_TYPES:
        raise ManorError(
            f"type は {'/'.join(VALID_EVENT_TYPES)} のいずれかです: {type_!r}", code=2
        )

    recipe = get(conn, row["recipe_id"])
    n = len(recipe["steps"])  # type: ignore[arg-type]
    current = int(row["current"])
    if type_ == "next":
        current = min(current + 1, n) if n else current
    elif type_ == "prev":
        current = max(current - 1, 1)
    # timer_start / done: current は不変（工程の合図を記録するだけ）。

    now = util.now()
    conn.execute(
        "INSERT INTO chef_cook_event (session_id, at, type, step) VALUES (?, ?, ?, ?)",
        (session_id, now, type_, step),
    )
    if current != int(row["current"]):
        conn.execute("UPDATE chef_cook_session SET current = ? WHERE id = ?", (current, session_id))

    progress = round(current / n, 3) if n else 0.0
    return {"current": current, "progress": progress}


def end_session(conn: sqlite3.Connection, session_id: int) -> dict[str, object]:
    """終了。`times_cooked` を1増やし `last_cooked_at` を更新する（冪等——既に終了して
    いれば2回目は数えない）。"""
    row = _session_row(conn, session_id)
    if row["ended_at"] is None:
        now = util.now()
        conn.execute(
            "UPDATE chef_cook_session SET ended_at = ? WHERE id = ?", (now, session_id)
        )
        conn.execute(
            "UPDATE chef_recipe_meta SET times_cooked = times_cooked + 1, last_cooked_at = ?"
            " WHERE recipe_id = ?",
            (now, row["recipe_id"]),
        )
    return {"id": session_id, "ended": True}
