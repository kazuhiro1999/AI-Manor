"""`manor chef ...`（ADR-002 §3）。

引数の組み立てと DB の読み書き。判断（何を勧めるか）はしない。
並べ替え・突き合わせ・集計・検証は `ops.py`（純粋関数）に委ねる。
core のパターン（`src/manor/cli.py`）に合わせ、各コマンドは
`(conn, home, args) -> str | object` を返す。
"""

from __future__ import annotations

import json
import sqlite3

from manor import i18n, util
from manor.errors import ManorError

from . import media, menu, nutrition, ops, recipe_import, recipes

VALID_SLOTS: tuple[str, ...] = ("breakfast", "lunch", "dinner", "snack")
VALID_AISLES: tuple[str, ...] = ("野菜", "肉魚", "乳卵", "主食", "調味料", "その他")
VALID_TASTE_KEYS: tuple[str, ...] = (
    "allergies",
    "dislikes",
    "likes",
    "household_size",
    "cook_minutes",
    "equipment",
    "notes",
)


def _split_csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _pantry_items(conn: sqlite3.Connection) -> list[str]:
    return [str(r["item"]) for r in conn.execute("SELECT item FROM chef_pantry").fetchall()]


def _pantry_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM chef_pantry").fetchone()
    return int(row["n"])


def _require_pantry_not_empty(conn: sqlite3.Connection) -> None:
    """在庫が丸ごと空なら `ManorError(code=2)`。エラーではなく「申告を求めよ」の合図
    （ADR-002 §3・手順書）。空の pantry で expiring/missing を回しても意味のある答えが
    出せないので、担当（LLM）へ「在庫の申告を求める」判断を促す。
    """
    if _pantry_count(conn) == 0:
        raise ManorError(
            "在庫がまだ登録されていません。在庫の申告をお願いします"
            "（目に入ったものだけで結構です。`manor chef pantry add` で教えてください）",
            code=2,
            key="error.chef.pantry_empty",
        )


# --- pantry ---------------------------------------------------------------------


def cmd_pantry_list(conn, home, args) -> object:
    sql = "SELECT * FROM chef_pantry WHERE 1=1"
    params: list[object] = []
    if args.place:
        sql += " AND place = ?"
        params.append(args.place)
    sql += " ORDER BY item"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    if args.json:
        return rows
    if not rows:
        return i18n.t("chef.pantry.list.empty")
    return "\n".join(
        i18n.t(
            "chef.pantry.list.line",
            id=r["id"], item=r["item"], qty=r["qty"], unit=r["unit"],
            expires=r["expires"] or i18n.t("chef.common.unknown"), place=r["place"],
        )
        for r in rows
    )


def cmd_pantry_add(conn, home, args) -> object:
    expires = (
        ops.validate_date(args.expires, field="期限", field_key="chef.field.expires")
        if args.expires
        else None
    )
    now = util.now()
    cur = conn.execute(
        "INSERT INTO chef_pantry (item, qty, unit, expires, place, note, added_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (args.item, args.qty, args.unit, expires, args.place, args.note, now, now),
    )
    pantry_id = cur.lastrowid
    if args.json:
        return {"id": pantry_id, "item": args.item}
    return i18n.t("chef.pantry.add.done", item=args.item, id=pantry_id)


def _find_pantry_rows(conn: sqlite3.Connection, ref: str) -> list[sqlite3.Row]:
    if ref.isdigit():
        return list(conn.execute("SELECT * FROM chef_pantry WHERE id = ?", (int(ref),)).fetchall())
    return list(conn.execute("SELECT * FROM chef_pantry WHERE item = ?", (ref,)).fetchall())


def _resolve_single_pantry_row(conn: sqlite3.Connection, ref: str) -> sqlite3.Row:
    rows = _find_pantry_rows(conn, ref)
    if not rows:
        raise ManorError(
            f"在庫に見つかりません: {ref}",
            code=2,
            key="error.chef.pantry_not_found",
            params={"ref": ref},
        )
    if len(rows) > 1 and not ref.isdigit():
        ids = ", ".join(str(r["id"]) for r in rows)
        raise ManorError(
            f"「{ref}」は在庫に複数あります。id で指定してください: {ids}",
            code=2,
            key="error.chef.pantry_ambiguous",
            params={"ref": ref, "ids": ids},
        )
    return rows[0]


def cmd_pantry_use(conn, home, args) -> object:
    row = _resolve_single_pantry_row(conn, args.item)

    if args.all:
        conn.execute("DELETE FROM chef_pantry WHERE id = ?", (row["id"],))
        if args.json:
            return {"id": row["id"], "item": row["item"], "removed": True}
        return i18n.t("chef.pantry.use.done_all", item=row["item"])

    if not args.qty:
        raise ManorError(
            "--qty か --all のどちらかを指定してください",
            code=2,
            key="error.chef.pantry_use_missing_arg",
        )

    remaining = ops.subtract_qty(str(row["qty"]), args.qty)
    if remaining is None:
        raise ManorError(
            f"{row['item']} の数量（{row['qty']}）が不明なため差し引けません。"
            "`--all` で使い切りを記録してください",
            code=2,
            key="error.chef.pantry_qty_unknown",
            params={"item": row["item"], "qty": row["qty"]},
        )
    if remaining == "":
        conn.execute("DELETE FROM chef_pantry WHERE id = ?", (row["id"],))
        if args.json:
            return {"id": row["id"], "item": row["item"], "removed": True}
        return i18n.t("chef.pantry.use.done_all", item=row["item"])

    conn.execute(
        "UPDATE chef_pantry SET qty = ?, updated_at = ? WHERE id = ?",
        (remaining, util.now(), row["id"]),
    )
    if args.json:
        return {"id": row["id"], "item": row["item"], "qty": remaining}
    return i18n.t("chef.pantry.use.done_partial", item=row["item"], remaining=remaining)


def cmd_pantry_remove(conn, home, args) -> object:
    row = _resolve_single_pantry_row(conn, args.ref)
    conn.execute("DELETE FROM chef_pantry WHERE id = ?", (row["id"],))
    if args.json:
        return {"id": row["id"], "item": row["item"], "removed": True}
    return i18n.t("chef.pantry.remove.done", item=row["item"])


def cmd_pantry_expiring(conn, home, args) -> object:
    _require_pantry_not_empty(conn)
    today = util.today()
    rows = [dict(r) for r in conn.execute("SELECT * FROM chef_pantry").fetchall()]
    picked = [r for r in rows if ops.is_expiring(r["expires"], today, args.days)]
    ordered = ops.sort_by_expiry(picked)
    if args.json:
        return ordered
    if not ordered:
        return i18n.t("chef.pantry.expiring.empty")
    return "\n".join(
        i18n.t(
            "chef.pantry.expiring.line",
            item=r["item"], expires=r["expires"] or i18n.t("chef.common.unknown"),
        )
        for r in ordered
    )


def cmd_pantry_missing(conn, home, args) -> object:
    _require_pantry_not_empty(conn)
    requested = _split_csv(args.items)
    pantry_items = _pantry_items(conn)
    staples = ops.basics()
    result = ops.check_missing(requested, pantry_items, staples)
    if args.json:
        return result
    if not result:
        return i18n.t("chef.pantry.missing.empty")
    lines = []
    for r in result:
        mark = i18n.t("chef.pantry.missing.found") if r["found"] else i18n.t("chef.pantry.missing.not_found")
        matched = "/".join(r["matched"]) if r["matched"] else ""
        line = i18n.t("chef.pantry.missing.line", item=r["item"], mark=mark)
        if matched:
            line += i18n.t("chef.pantry.missing.matched_suffix", matched=matched)
        lines.append(line)
    return "\n".join(lines)


# --- meal -------------------------------------------------------------------------


def cmd_meal_log(conn, home, args) -> object:
    date_ = ops.validate_date(args.date, field="日付")
    ops.validate_choice(args.slot, VALID_SLOTS, field="slot")
    now = util.now()
    cur = conn.execute(
        "INSERT INTO chef_meal (date, slot, dish, ingredients, note, planned, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (date_, args.slot, args.dish, args.ingredients, args.note, 1 if args.planned else 0, now),
    )
    meal_id = cur.lastrowid
    if args.json:
        return {"id": meal_id}
    return i18n.t("chef.meal.log.done", id=meal_id)


def cmd_meal_confirm(conn, home, args) -> object:
    row = conn.execute("SELECT id FROM chef_meal WHERE id = ?", (args.id,)).fetchone()
    if row is None:
        raise ManorError(
            f"meal が見つかりません: {args.id}",
            code=2,
            key="error.chef.meal_not_found",
            params={"id": args.id},
        )
    conn.execute("UPDATE chef_meal SET planned = 0 WHERE id = ?", (args.id,))
    if args.json:
        return {"id": args.id, "planned": False}
    return i18n.t("chef.meal.confirm.done", id=args.id)


def cmd_meal_week(conn, home, args) -> object:
    today = util.today()
    start, end = ops.week_range(today, args.days)
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM chef_meal WHERE date BETWEEN ? AND ? ORDER BY date, slot",
            (start, end),
        ).fetchall()
    ]
    result = ops.aggregate_week(rows, start, end, ops.dish_types(), ops.ingredient_categories())
    if args.json:
        return result
    lines = [
        i18n.t(
            "chef.meal.week.header",
            start=result["start"], end=result["end"], coverage=f"{result['coverage_rate']:.0%}",
        )
    ]
    lines.append(i18n.t("chef.meal.week.planned_count", count=result["planned_count"]))
    if result["missing_slots"]:
        lines.append(i18n.t("chef.meal.week.missing_header"))
        for m in result["missing_slots"]:  # type: ignore[union-attr]
            lines.append(i18n.t("chef.meal.week.missing_line", date=m["date"], slot=m["slot"]))
    return "\n".join(lines)


# --- shopping -----------------------------------------------------------------------


def cmd_shopping_list(conn, home, args) -> object:
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM chef_shopping WHERE bought_at IS NULL ORDER BY aisle, item"
        ).fetchall()
    ]
    if args.json:
        return rows
    if not rows:
        return i18n.t("chef.shopping.list.empty")
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        grouped.setdefault(str(r["aisle"]), []).append(r)
    lines = []
    for aisle, items in grouped.items():
        lines.append(i18n.t("chef.shopping.list.aisle_header", aisle=aisle))
        for it in items:
            if it["reason"]:
                lines.append(i18n.t("chef.shopping.list.item_line_with_reason", item=it["item"], reason=it["reason"]))
            else:
                lines.append(i18n.t("chef.shopping.list.item_line", item=it["item"]))
    return "\n".join(lines)


def cmd_shopping_add(conn, home, args) -> object:
    ops.validate_choice(args.aisle, VALID_AISLES, field="aisle")
    now = util.now()
    cur = conn.execute(
        "INSERT INTO chef_shopping (item, reason, aisle, added_at, bought_at)"
        " VALUES (?, ?, ?, ?, NULL)",
        (args.item, args.reason, args.aisle, now),
    )
    shopping_id = cur.lastrowid
    if args.json:
        return {"id": shopping_id}
    return i18n.t("chef.shopping.add.done", item=args.item)


def cmd_shopping_bought(conn, home, args) -> object:
    items = _split_csv(args.items)
    if not items:
        raise ManorError(
            "品目が指定されていません",
            code=2,
            key="error.chef.shopping_items_missing",
        )
    expires = (
        ops.validate_date(args.expires, field="期限", field_key="chef.field.expires")
        if args.expires
        else None
    )
    now = util.now()

    pantry_items = _pantry_items(conn)
    open_shopping = [
        dict(r)
        for r in conn.execute("SELECT * FROM chef_shopping WHERE bought_at IS NULL").fetchall()
    ]

    results: list[dict[str, object]] = []
    for item in items:
        crossed_off: list[str] = []
        for row in open_shopping:
            if row["bought_at"] is None and ops.item_match(item, str(row["item"])):
                conn.execute("UPDATE chef_shopping SET bought_at = ? WHERE id = ?", (now, row["id"]))
                row["bought_at"] = now
                crossed_off.append(str(row["item"]))

        already_in_pantry = [p for p in pantry_items if ops.item_match(item, p)]
        added_to_pantry = False
        if not already_in_pantry:
            conn.execute(
                "INSERT INTO chef_pantry (item, qty, unit, expires, place, note, added_at, updated_at)"
                " VALUES (?, ?, '', ?, ?, '', ?, ?)",
                (item, args.qty or "不明", expires, args.place or "不明", now, now),
            )
            pantry_items.append(item)
            added_to_pantry = True

        results.append(
            {
                "item": item,
                "crossed_off": crossed_off,
                "added_to_pantry": added_to_pantry,
                "already_in_pantry": already_in_pantry,
            }
        )

    if args.json:
        return results
    lines = []
    for r in results:
        note = (
            i18n.t("chef.shopping.bought.added")
            if r["added_to_pantry"]
            else i18n.t("chef.shopping.bought.already_in_pantry")
        )
        lines.append(i18n.t("chef.shopping.bought.line", item=r["item"], note=note))
    return "\n".join(lines)


# --- taste --------------------------------------------------------------------------


def cmd_taste_show(conn, home, args) -> object:
    rows = [dict(r) for r in conn.execute("SELECT * FROM chef_taste ORDER BY key").fetchall()]
    if args.json:
        return rows
    if not rows:
        return i18n.t("chef.taste.show.empty")
    return "\n".join(i18n.t("chef.taste.show.line", taste_key=r["key"], value=r["value"]) for r in rows)


def cmd_taste_set(conn, home, args) -> object:
    ops.validate_choice(args.key, VALID_TASTE_KEYS, field="key")
    now = util.now()
    conn.execute(
        "INSERT INTO chef_taste (key, value, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (args.key, args.value, now),
    )
    if args.json:
        return {"key": args.key, "value": args.value}
    return i18n.t("chef.taste.set.done", taste_key=args.key)


# --- recipe（レシピ帳。ADR-015 D5） ------------------------------------------------


def _require_chef_recipe_table(conn: sqlite3.Connection) -> None:
    """`chef_recipe` が無い home（このリポジトリ更新前に導入された既存 home）向け。

    core の `migrate_core` は部下のスキーマに触れない約束（`db.py` の docstring 参照）
    なので、`manor init` を再実行するまで表が無いことがある——`ManorError(code=2)` で
    「未導入なので `manor init` を」と案内する（`_require_pantry_not_empty` と同じ流儀）。
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'chef_recipe'"
    ).fetchone()
    if row is None:
        raise ManorError(
            "料理長のレシピ帳が未導入です。`manor init` を実行してください",
            code=2,
            key="error.chef.recipe_table_missing",
        )


def cmd_recipe_list(conn, home, args) -> object:
    _require_chef_recipe_table(conn)
    rows = recipes.list_recipes(
        conn, q=args.q, tag=args.tag, favorite=args.favorite,
        category=args.category, main_ingredient=args.main_ingredient, cuisine=args.cuisine,
        sort=args.sort, include_archived=args.archived,
    )
    if args.json:
        return rows
    if not rows:
        return i18n.t("chef.recipe.list.empty")
    return "\n".join(
        i18n.t(
            "chef.recipe.list.line",
            id=r["id"],
            title=r["title"],
            total_minutes=(
                r["total_minutes"] if r["total_minutes"] is not None else i18n.t("chef.common.unknown")
            ),
            tags=", ".join(r["tags"]),  # type: ignore[arg-type]
            favorite=i18n.t("chef.recipe.favorite_mark") if r["favorite"] else "",
        )
        for r in rows
    )


def cmd_recipe_show(conn, home, args) -> object:
    _require_chef_recipe_table(conn)
    result = recipes.get(conn, args.id)
    if args.json:
        return result
    lines = [i18n.t("chef.recipe.show.header", id=result["id"], title=result["title"])]
    lines.append(i18n.t("chef.recipe.show.steps_count", count=len(result["steps"])))  # type: ignore[arg-type]
    if result["meta"]["favorite"]:  # type: ignore[index]
        lines.append(i18n.t("chef.recipe.show.favorite"))
    return "\n".join(lines)


def cmd_recipe_add(conn, home, args) -> object:
    _require_chef_recipe_table(conn)
    try:
        with open(args.file, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as exc:
        raise ManorError(
            f"ファイルが見つかりません: {args.file}",
            code=2,
            key="error.chef.recipe_file_not_found",
            params={"path": args.file},
        ) from exc
    except json.JSONDecodeError as exc:
        raise ManorError(
            f"JSON として読めません: {args.file}（{exc}）",
            code=2,
            key="error.chef.recipe_file_invalid_json",
            params={"path": args.file, "exc": str(exc)},
        ) from exc
    recipe_id = recipes.add(conn, data)
    nutrition.refresh(conn, recipe_id)  # ADR-019 D4「再計算の契機」: 登録
    if args.json:
        return {"id": recipe_id}
    return i18n.t("chef.recipe.add.done", id=recipe_id)


def cmd_recipe_set(conn, home, args) -> object:
    _require_chef_recipe_table(conn)
    kwargs: dict[str, object] = {}
    if args.kcal is not None:
        kwargs["kcal"] = args.kcal
    if args.protein is not None:
        kwargs["protein_g"] = args.protein
    if args.fat is not None:
        kwargs["fat_g"] = args.fat
    if args.carb is not None:
        kwargs["carb_g"] = args.carb
    if args.salt is not None:
        kwargs["salt_g"] = args.salt
    if args.tags is not None:
        kwargs["tags"] = _split_csv(args.tags)
    if args.rating is not None:
        kwargs["rating"] = args.rating
    if args.memo is not None:
        kwargs["memo"] = args.memo
    if args.favorite is not None:
        kwargs["favorite"] = args.favorite
    if args.category is not None:
        kwargs["category"] = args.category
    if args.main_ingredient is not None:
        kwargs["main_ingredient"] = args.main_ingredient
    if args.cuisine is not None:
        kwargs["cuisine"] = args.cuisine
    result = recipes.set_meta(conn, args.id, **kwargs)
    if args.json:
        return result
    return i18n.t("chef.recipe.set.done", id=args.id)


def cmd_recipe_archive(conn, home, args) -> object:
    _require_chef_recipe_table(conn)
    recipes.archive(conn, args.id)
    if args.json:
        return {"id": args.id, "archived": True}
    return i18n.t("chef.recipe.archive.done", id=args.id)


def cmd_recipe_import(conn, home, args) -> object:
    """ADR-015 R2・D7。既定は `--mode auto`（自動抽出。速い・外部を呼ばない）で
    **下書きを JSON で出す**（`--save` が無ければ登録しない）。"""
    _require_chef_recipe_table(conn)
    result = recipe_import.import_from_url(args.url, mode=args.mode)
    if not result.get("ok"):
        raise ManorError(
            f"レシピの取り込みに失敗しました: {result.get('reason', '')}",
            key="error.chef.recipe_import_failed",
            params={"reason": result.get("reason", "")},
        )
    recipe = result["recipe"]
    method = result.get("method", "")
    warnings = result.get("warnings") or []
    if args.save:
        recipe_id = recipes.add(conn, recipe)
        nutrition.refresh(conn, recipe_id)  # ADR-019 D4「再計算の契機」: 登録
        if args.json:
            return {"id": recipe_id, "method": method, "warnings": warnings}
        return i18n.t("chef.recipe.import.saved", id=recipe_id)
    # 保存しないときは下書きをそのまま返す（`_emit_result` が文字列以外は JSON で
    # 出す——ADR-015 D2「編集できる下書き」は構造化データそのものなので、
    # 1行の人間向け文にする意味が薄い。`--json` の有無を問わない）。
    return {"recipe": recipe, "method": method, "warnings": warnings}


# `manor chef recipe estimate`（`claude -p` に栄養価を言わせる ADR-015 D2 手順5 の口）は
# 2026-09-13 に畳んだ（ADR-019 §5）。材料からの推定は `manor chef nutrition rebuild`。


# --- food / nutrition（食品成分表と推定。ADR-019 D1・D4） ---------------------------


def cmd_food_import(conn, home, args) -> object:
    """成分表を取り込む。**引数を省略すると同梱 CSV**（ADR-019 §4 追補）——版を上げる
    ときだけ、主人が公式サイトから落とした `.xlsx`（または CSV）の道を渡す。冪等。
    """
    path = args.path if args.path else None
    result = nutrition.import_food_table(conn, path, source_version=args.source_version)
    # 取り込んだ直後に名寄せの種を入れる（ADR-019 §4）——`nutrition rebuild` の前に
    # 済ませておかないと、初回の推定が「長ねぎ」「片栗粉」を丸ごと取りこぼす。
    seeded = nutrition.seed_aliases(conn)
    if args.json:
        return {**result, "seed": seeded}
    return "\n".join([
        i18n.t(
            "chef.food.import.done",
            rows=result["rows"], added=result["added"],
            updated=result["updated"], total=result["total"],
        ),
        _seed_line(seeded),
    ])


def cmd_food_export(conn, home, args) -> object:
    """`chef_food` を正規化 CSV へ書き出す（ADR-019 §4 追補）。引数省略で同梱 CSV の場所。

    **DB は読むだけ**——同梱 CSV を版上げ後に作り直すときや、検分用に使う。
    """
    path = args.path if args.path else None
    result = nutrition.export_food_table(conn, path)
    if args.json:
        return result
    return i18n.t("chef.food.export.done", rows=result["rows"], path=result["path"])


def _seed_line(seeded: dict) -> str:
    """`manor chef food seed` の結果行（取り込みの後にも同じ文を出す）。"""
    return i18n.t(
        "chef.food.seed.done",
        aliases=seeded["aliases"], added=seeded["added"],
        updated=seeded["updated"], kept=seeded["kept_manual"],
    )


def cmd_food_seed(conn, home, args) -> object:
    """名寄せの種（`food_aliases_seed.toml`）を `chef_food_alias` へ入れる（ADR-019 §4）。

    冪等。**主人が手で決めた名寄せ（`manual`）は上書きしない。**
    """
    result = nutrition.seed_aliases(conn)
    if args.json:
        return result
    lines = [_seed_line(result)]
    if result["missing"]:
        lines.append(i18n.t("chef.food.seed.missing", n=len(result["missing"])))
    return "\n".join(lines)


def cmd_food_search(conn, home, args) -> object:
    """成分表を名前の部分一致で引く（名寄せの下ごしらえ。画面の検索と同じ口）。"""
    rows = nutrition.search_foods(conn, args.q, limit=args.limit)
    if args.json:
        return rows
    if not rows:
        return i18n.t("chef.food.search.empty")
    return "\n".join(
        i18n.t(
            "chef.food.search.line",
            food_code=r["food_code"],
            name=r["name"],
            kcal=r["kcal"] if r["kcal"] is not None else i18n.t("chef.common.unknown"),
        )
        for r in rows
    )


def cmd_nutrition_rebuild(conn, home, args) -> object:
    """材料から栄養値を推定して書く（ADR-019 D4）。`--recipe` で1本だけ。

    `site`（出典の表示値）・`manual`（手入力）のレシピは**上書きしない**。
    """
    _require_chef_recipe_table(conn)
    result = nutrition.rebuild(conn, recipe_id=args.recipe)
    if args.json:
        return result
    lines = [
        i18n.t(
            "chef.nutrition.rebuild.done",
            updated=result["updated"], skipped=result["skipped"], unresolved=result["unresolved"],
        )
    ]
    # 調理による油の吸収を足した件数（ADR-019 §4 追補）。材料表に書かれていない油を
    # 足しているので、黙って値が増えたように見えないよう1行で言う。
    if result.get("oil_adjusted"):
        lines.append(
            i18n.t(
                "chef.nutrition.rebuild.oil",
                n=result["oil_adjusted"], grams=result["oil_grams"],
            )
        )
    return "\n".join(lines)


def cmd_nutrition_unresolved(conn, home, args) -> object:
    """名寄せできていない材料名の一覧（ADR-019 D5 の画面と同じ中身）。"""
    _require_chef_recipe_table(conn)
    result = nutrition.unresolved_summary(conn)
    if args.json:
        return result
    items = result["items"]
    if not items:
        return i18n.t("chef.nutrition.unresolved.empty")
    return "\n".join(
        i18n.t(
            "chef.nutrition.unresolved.line",
            name=it["names"][0] if it["names"] else it["normalized"],
            normalized=it["normalized"],
            count=it["count"],
        )
        for it in items  # type: ignore[union-attr]
    )


# --- media（動画リスト。ADR-016 D5） ------------------------------------------------


def _require_chef_media_table(conn: sqlite3.Connection) -> None:
    """`chef_media` が無い home 向け（`_require_chef_recipe_table` と同じ理由・同じ流儀）。"""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'chef_media'"
    ).fetchone()
    if row is None:
        raise ManorError(
            "料理長の動画リストが未導入です。`manor init` を実行してください",
            code=2,
            key="error.chef.media_table_missing",
        )


def cmd_media_list(conn, home, args) -> object:
    """読み取りの確認用（ADR-016 D5）。**登録・並べ替えは Web の仕事**——URL を貼る導線は
    画面のほうが主人の手に合う（Chrome の共有から貼る想定）。

    動画は利用者ごと（ADR-016 D1）。CLI には cookie が無いので、`--user` を省いたら
    主人（`user.principal_id`）の一覧を見る。
    """
    from manor import user as user_mod

    _require_chef_media_table(conn)
    user_id = args.user or user_mod.principal_id(conn)
    payload = media.list_payload(conn, user_id=user_id)
    if args.json:
        return payload
    items = payload["items"]
    if not items:
        return i18n.t("chef.media.list.empty")
    return "\n".join(
        i18n.t(
            "chef.media.list.line",
            sort_order=it["sort_order"],
            title=it["title"],
            author=it["author"] or i18n.t("chef.common.unknown"),
            video_id=it["video_id"],
        )
        for it in items  # type: ignore[union-attr]
    )


# --- menu（献立のおすすめ。ADR-018 D7-1） -------------------------------------------


def _menu_reason_text(reason: dict) -> str:
    """理由の符牒を文へ（`menu.py` は文を組まない。ADR-018 §4）。"""
    params = {str(k): v for k, v in (reason.get("params") or {}).items()}
    return i18n.t(f"chef.menu.reason.{reason.get('code')}", **params)


def cmd_menu(conn, home, args) -> object:
    """確認用（ADR-018 D7-1）。**決めるのは Web の仕事**——CLI は「規則がどう並べたか」を
    その場で見るための口で、`--json` は API と同じ形をそのまま出す。
    """
    _require_chef_recipe_table(conn)
    payload = menu.recommend(
        conn,
        main_recipe_id=args.main,
        people=args.people,
        mood=args.mood or "",
        slot=args.slot,
        exclude=[int(v) for v in _split_csv(args.exclude or "")],
    )
    if args.json:
        return payload

    lines: list[str] = []
    main = payload.get("main")
    if isinstance(main, dict):
        lines.append(i18n.t("chef.menu.show.main_fixed", title=main["title"]))
    applied = payload.get("applied_mood") or {}
    if isinstance(applied, dict) and applied.get("matched"):
        lines.append(i18n.t("chef.menu.show.mood", mood="・".join(applied["matched"])))
    slots = payload.get("slots") or {}
    for kind in menu.SLOT_KINDS:
        rows = slots.get(kind) or []  # type: ignore[union-attr]
        if kind == "main" and isinstance(main, dict):
            continue
        lines.append(i18n.t(f"chef.menu.slot.{kind}"))
        if not rows:
            lines.append(i18n.t("chef.menu.show.empty_slot"))
            continue
        for rank, row in enumerate(rows, start=1):
            lines.append(
                i18n.t(
                    "chef.menu.show.line",
                    rank=rank,
                    title=row["title"],
                    score=row["score"],
                    reasons="・".join(_menu_reason_text(r) for r in row["reasons"]),
                )
            )
    combo = payload.get("combo") or {}
    total = combo.get("total") or {}  # type: ignore[union-attr]
    if total:
        lines.append(i18n.t("chef.menu.show.combo", **{k: total[k] for k in menu.NUTRIENTS}))
        checks = combo.get("band_check") or {}  # type: ignore[union-attr]
        parts = [
            i18n.t("chef.menu.nutrient." + key) + " " + i18n.t("chef.menu.band." + str(value["status"]))
            for key, value in checks.items()
        ]
        lines.append(i18n.t("chef.menu.show.band", items="・".join(parts)))
    if payload.get("excluded_no_nutrition"):
        lines.append(i18n.t("chef.menu.show.excluded", n=payload["excluded_no_nutrition"]))
    if payload.get("excluded_partial"):
        # ADR-019 D4: 推定はできたが名寄せが足りず外したもの（`manor chef nutrition
        # unresolved` で何を名寄せすれば増えるかが分かる）。
        lines.append(i18n.t("chef.menu.show.excluded_partial", n=payload["excluded_partial"]))
    return "\n".join(lines)


# --- パーサ組み立て -----------------------------------------------------------------


def register(subparsers) -> None:
    """`manor chef ...` を足す（ADR-001 §11）。core の `build_parser` が呼ぶ。"""
    chef_p = subparsers.add_parser("chef", help=i18n.t("cli.chef.help"))
    chef_sub = chef_p.add_subparsers(dest="verb")

    # --- pantry ---
    pantry_p = chef_sub.add_parser("pantry", help=i18n.t("cli.chef.pantry.help"))
    pantry_sub = pantry_p.add_subparsers(dest="pantry_verb")

    p = pantry_sub.add_parser("list")
    p.add_argument("--place")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_pantry_list, is_write=False)

    p = pantry_sub.add_parser("add")
    p.add_argument("item")
    p.add_argument("--qty", default="不明")
    p.add_argument("--unit", default="")
    p.add_argument("--expires")
    p.add_argument("--place", default="不明")
    p.add_argument("--note", default="")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_pantry_add, is_write=True)

    p = pantry_sub.add_parser("use")
    p.add_argument("item")
    p.add_argument("--qty")
    p.add_argument("--all", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_pantry_use, is_write=True)

    p = pantry_sub.add_parser("remove")
    p.add_argument("ref")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_pantry_remove, is_write=True)

    p = pantry_sub.add_parser("expiring")
    p.add_argument("--days", type=int, default=3)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_pantry_expiring, is_write=False)

    p = pantry_sub.add_parser("missing")
    p.add_argument("items")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_pantry_missing, is_write=False)

    # --- meal ---
    meal_p = chef_sub.add_parser("meal", help=i18n.t("cli.chef.meal.help"))
    meal_sub = meal_p.add_subparsers(dest="meal_verb")

    p = meal_sub.add_parser("log")
    p.add_argument("--date", required=True)
    p.add_argument("--slot", required=True)
    p.add_argument("--dish", required=True)
    p.add_argument("--ingredients", default="")
    p.add_argument("--planned", action="store_true")
    p.add_argument("--note", default="")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_meal_log, is_write=True)

    p = meal_sub.add_parser("confirm")
    p.add_argument("id", type=int)
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_meal_confirm, is_write=True)

    p = meal_sub.add_parser("week")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_meal_week, is_write=False)

    # --- shopping ---
    shopping_p = chef_sub.add_parser("shopping", help=i18n.t("cli.chef.shopping.help"))
    shopping_sub = shopping_p.add_subparsers(dest="shopping_verb")

    p = shopping_sub.add_parser("list")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_shopping_list, is_write=False)

    p = shopping_sub.add_parser("add")
    p.add_argument("item")
    p.add_argument("--reason", required=True)
    p.add_argument("--aisle", default="その他")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_shopping_add, is_write=True)

    p = shopping_sub.add_parser("bought")
    p.add_argument("items")
    p.add_argument("--qty")
    p.add_argument("--expires")
    p.add_argument("--place")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_shopping_bought, is_write=True)

    # --- taste ---
    taste_p = chef_sub.add_parser("taste", help=i18n.t("cli.chef.taste.help"))
    taste_sub = taste_p.add_subparsers(dest="taste_verb")

    p = taste_sub.add_parser("show")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_taste_show, is_write=False)

    p = taste_sub.add_parser("set")
    p.add_argument("key")
    p.add_argument("value")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_taste_set, is_write=True)

    # --- recipe（ADR-015 D5） ---
    recipe_p = chef_sub.add_parser("recipe", help=i18n.t("cli.chef.recipe.help"))
    recipe_sub = recipe_p.add_subparsers(dest="recipe_verb")

    p = recipe_sub.add_parser("list")
    p.add_argument("--q")
    p.add_argument("--tag")
    p.add_argument("--category")
    p.add_argument("--main-ingredient", dest="main_ingredient")
    p.add_argument("--cuisine")
    p.add_argument("--sort", choices=list(recipes.VALID_SORT), default="recent")
    fav_group = p.add_mutually_exclusive_group()
    fav_group.add_argument("--favorite", dest="favorite", action="store_true")
    fav_group.add_argument("--no-favorite", dest="favorite", action="store_false")
    p.set_defaults(favorite=None)
    p.add_argument("--archived", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_recipe_list, is_write=False)

    p = recipe_sub.add_parser("show")
    p.add_argument("id", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_recipe_show, is_write=False)

    p = recipe_sub.add_parser("add")
    p.add_argument("--file", required=True)
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_recipe_add, is_write=True)

    p = recipe_sub.add_parser("set")
    p.add_argument("id", type=int)
    p.add_argument("--kcal", type=float)
    p.add_argument("--protein", type=float)
    p.add_argument("--fat", type=float)
    p.add_argument("--carb", type=float)
    p.add_argument("--salt", type=float)
    p.add_argument("--tags")
    p.add_argument("--rating", type=int)
    p.add_argument("--memo")
    p.add_argument("--category")
    p.add_argument("--main-ingredient", dest="main_ingredient")
    p.add_argument("--cuisine")
    fav_group2 = p.add_mutually_exclusive_group()
    fav_group2.add_argument("--favorite", dest="favorite", action="store_true")
    fav_group2.add_argument("--no-favorite", dest="favorite", action="store_false")
    p.set_defaults(favorite=None)
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_recipe_set, is_write=True)

    p = recipe_sub.add_parser("archive")
    p.add_argument("id", type=int)
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_recipe_archive, is_write=True)

    # --- recipe import（ADR-015 R2・D7） ---
    p = recipe_sub.add_parser("import")
    p.add_argument("url")
    p.add_argument("--mode", choices=["auto", "claude"], default="auto")
    p.add_argument("--save", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_recipe_import, is_write=True)

    # --- food（食品成分表。ADR-019 D1） ---
    food_p = chef_sub.add_parser("food", help=i18n.t("cli.chef.food.help"))
    food_sub = food_p.add_subparsers(dest="food_verb")

    p = food_sub.add_parser("import")
    p.add_argument("path", nargs="?", default=None, help=i18n.t("cli.chef.food.import.path.help"))
    p.add_argument(
        "--source-version",
        dest="source_version",
        default=nutrition.DEFAULT_SOURCE_VERSION,
        help=i18n.t("cli.chef.food.import.source_version.help"),
    )
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_food_import, is_write=True)

    p = food_sub.add_parser("export", help=i18n.t("cli.chef.food.export.help"))
    p.add_argument("path", nargs="?", default=None, help=i18n.t("cli.chef.food.export.path.help"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_food_export, is_write=False)

    p = food_sub.add_parser("seed", help=i18n.t("cli.chef.food.seed.help"))
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_food_seed, is_write=True)

    p = food_sub.add_parser("search")
    p.add_argument("q")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_food_search, is_write=False)

    # --- nutrition（材料からの推定。ADR-019 D4） ---
    nutrition_p = chef_sub.add_parser("nutrition", help=i18n.t("cli.chef.nutrition.help"))
    nutrition_sub = nutrition_p.add_subparsers(dest="nutrition_verb")

    p = nutrition_sub.add_parser("rebuild")
    p.add_argument("--recipe", type=int, help=i18n.t("cli.chef.nutrition.rebuild.recipe.help"))
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_nutrition_rebuild, is_write=True)

    p = nutrition_sub.add_parser("unresolved")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_nutrition_unresolved, is_write=False)

    # --- media（動画リスト。ADR-016 D5。読み取りだけ置く） ---
    media_p = chef_sub.add_parser("media", help=i18n.t("cli.chef.media.help"))
    media_sub = media_p.add_subparsers(dest="media_verb")

    p = media_sub.add_parser("list")
    p.add_argument("--user", help=i18n.t("cli.chef.media.list.user.help"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_media_list, is_write=False)

    # --- menu（献立のおすすめ。ADR-018 D7-1。下位の動詞を持たない葉のコマンド） ---
    p = chef_sub.add_parser("menu", help=i18n.t("cli.chef.menu.help"))
    p.add_argument("--main", type=int, help=i18n.t("cli.chef.menu.main.help"))
    p.add_argument("--people", type=int, default=2, help=i18n.t("cli.chef.menu.people.help"))
    p.add_argument("--mood", default="", help=i18n.t("cli.chef.menu.mood.help"))
    p.add_argument("--slot", default="dinner", help=i18n.t("cli.chef.menu.slot.help"))
    p.add_argument("--exclude", default="")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_menu, is_write=False)
