"""`kitchen`（料理長。ADR-005 §2）。読みは board の `get_chef` をそのまま呼ぶ。
書きは `staff/chef/cli.py` の `cmd_*` を呼ぶ（SQL は web 層に書かない）。
"""

from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from ...board import api_staff as board_staff
from ...errors import ManorError
from .._common import (
    WebContext,
    commit_and_render,
    manor_error_to_http,
    ns,
    open_conn,
    require_writable,
    table_exists,
    viewing_user_id,
)


def _require_chef(conn) -> None:
    if not table_exists(conn, "chef_pantry"):
        raise HTTPException(status_code=404, detail="料理長（chef）が導入されていません")


def _require_chef_recipes(conn) -> None:
    """ADR-015: `chef_recipe` が無い home（更新前に chef を導入した既存 home）向け。
    `manor init` を再実行するまで表が無いことがある——500 ではなく 404 で案内する
    （`_require_chef`/`_require_secretary` と同じ流儀）。
    """
    if not table_exists(conn, "chef_recipe"):
        raise HTTPException(status_code=404, detail="料理長のレシピ帳が未導入です")


def _require_chef_media(conn) -> None:
    """ADR-016: `chef_media` が無い home 向け（`_require_chef_recipes` と同じ流儀）。
    表は `staff/chef/schema.sql` の `CREATE TABLE IF NOT EXISTS` が作る——`manor init`
    （web の `create_app` も起動時に呼ぶ）を通すまでは無いことがある。
    """
    if not table_exists(conn, "chef_media"):
        raise HTTPException(status_code=404, detail="料理長の動画リストが未導入です")


def _require_chef_food(conn) -> None:
    """ADR-019: `chef_food` が無い home 向け（`_require_chef_recipes` と同じ流儀）。"""
    if not table_exists(conn, "chef_food"):
        raise HTTPException(status_code=404, detail="料理長の食品成分表が未導入です")


def _nutrition_error_to_http(exc: ManorError) -> HTTPException:
    """ADR-019 の状態コードへ写す（`_media_error_to_http` と同じ流儀）。

    取り込み・名寄せの引数の誤りは 400（`manor` の慣例は code=2 → 404 だが、画面が
    「打ち間違い」と「無い」を区別できないと直し方が伝わらない）。
    """
    from ...staff.chef import nutrition as chef_nutrition

    if exc.key in (chef_nutrition.ERR_FOOD_IMPORT_FAILED, chef_nutrition.ERR_FOOD_NOT_FOUND):
        return HTTPException(status_code=400, detail=exc.message_ja)
    if exc.key == chef_nutrition.ERR_FOOD_TABLE_MISSING:
        return HTTPException(status_code=404, detail=exc.message_ja)
    return manor_error_to_http(exc)


def _media_error_to_http(exc: ManorError) -> HTTPException:
    """ADR-016 D3 の状態コードへ写す。**写し先の出どころは `chef/media.py` のキー定数**
    ——`recipes` のように「すべて code=2 → 404」にすると、400（URL が読めない）と
    409（重複）を区別できない。
    """
    from ...staff.chef import media as chef_media

    if exc.key == chef_media.ERR_URL_INVALID:
        return HTTPException(status_code=400, detail=exc.message_ja)
    if exc.key == chef_media.ERR_DUPLICATE:
        return HTTPException(status_code=409, detail=exc.message_ja)
    return manor_error_to_http(exc)


def _menu_error_to_http(exc: ManorError) -> HTTPException:
    """ADR-018 の状態コードへ写す（`_media_error_to_http` と同じ流儀——写し先の出どころは
    `chef/menu.py` のキー定数）。引数の誤りは 400、レシピが無いのは 404。
    """
    from ...staff.chef import menu as chef_menu

    if exc.key == chef_menu.ERR_BAD_REQUEST:
        return HTTPException(status_code=400, detail=exc.message_ja)
    if exc.key == chef_menu.ERR_RECIPE_NOT_FOUND:
        return HTTPException(status_code=404, detail=exc.message_ja)
    return manor_error_to_http(exc)


class PantryAddRequest(BaseModel):
    item: str = Field(..., min_length=1)
    qty: str = "不明"
    unit: str = ""
    expires: str | None = None
    place: str = "不明"


class PantryUseRequest(BaseModel):
    qty: str | None = None
    all: bool = False


class ShoppingAddRequest(BaseModel):
    item: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)
    aisle: str = "その他"


class ShoppingBoughtRequest(BaseModel):
    items: list[str] = Field(default_factory=list)


class MealLogRequest(BaseModel):
    date: str
    slot: str
    dish: str
    ingredients: str = ""
    planned: bool = False


class RecipeMetaRequest(BaseModel):
    """うちの値の部分更新（ADR-015 D3）。`exclude_unset=True` で「渡した欄だけ」を
    `recipes.set_meta` の kwargs へそのまま渡す——欄ごとに Optional にしているのは
    「渡さない」と「null で消す」を区別するためで、`model_dump(exclude_unset=True)`
    が実際に body に含まれていたキーだけを拾う。
    """

    kcal: float | None = None
    protein_g: float | None = None
    fat_g: float | None = None
    carb_g: float | None = None
    salt_g: float | None = None
    nutrition_source: str | None = None
    tags: list[str] | None = None
    rating: int | None = None
    memo: str | None = None
    favorite: bool | None = None
    category: str | None = None
    main_ingredient: str | None = None
    cuisine: str | None = None


class RecipeImportRequest(BaseModel):
    """ADR-015 D2・D3・D7。`url` を受け、**保存しない**下書きを返す。
    `mode` の既定は `"auto"`（自動抽出。速い・外部を呼ばない）。
    """

    url: str = Field(..., min_length=1)
    mode: Literal["auto", "claude"] = "auto"


class RecipeRefineRequest(BaseModel):
    """ADR-015 D7-2。自動抽出の下書きを Claude で整える。**保存しない**。"""

    recipe: dict = Field(default_factory=dict)


class MediaAddRequest(BaseModel):
    """ADR-016 D2。URL を貼るだけ——題名・チャンネル名・サムネイルは oEmbed が補う。"""

    url: str = Field(..., min_length=1)
    memo: str = ""


class MediaUpdateRequest(BaseModel):
    """ADR-016 D3。その場編集。`exclude_unset=True` で「渡した欄だけ」を渡す
    （`RecipeMetaRequest` と同じ作法）。
    """

    title: str | None = None
    memo: str | None = None


class MediaReorderRequest(BaseModel):
    ids: list[str] = Field(default_factory=list)


class MenuPlanRequest(BaseModel):
    """ADR-018 D6「この献立にする」。`recipe_ids` は主菜・副菜・汁物の id（順序は自由）。"""

    date: str
    slot: str = "dinner"
    recipe_ids: list[int] = Field(default_factory=list)


class FoodAliasRequest(BaseModel):
    """ADR-019 D2。未解決の材料名に食品を選んで `manual` で結ぶ。"""

    alias: str = Field(..., min_length=1)
    food_code: str = Field(..., min_length=1)
    confidence: Literal["manual", "rule", "llm"] = "manual"


class CookSessionStartRequest(BaseModel):
    recipe_id: int


class CookSessionEventRequest(BaseModel):
    type: str = Field(..., min_length=1)
    step: int | None = None


def register(app: FastAPI, ctx: WebContext) -> None:
    @app.get("/api/v1/kitchen")
    def kitchen() -> dict[str, object]:
        with open_conn(ctx) as conn:
            return board_staff.get_chef(conn, ctx.home)

    @app.post("/api/v1/kitchen/pantry")
    def pantry_add(body: PantryAddRequest) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.chef import cli as chef_cli

        with open_conn(ctx) as conn:
            _require_chef(conn)
            try:
                result = chef_cli.cmd_pantry_add(
                    conn, ctx.home,
                    ns(item=body.item, qty=body.qty, unit=body.unit, expires=body.expires, place=body.place, note=""),
                )
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result  # type: ignore[return-value]

    @app.post("/api/v1/kitchen/pantry/{pantry_id}/use")
    def pantry_use(pantry_id: int, body: PantryUseRequest) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.chef import cli as chef_cli

        with open_conn(ctx) as conn:
            _require_chef(conn)
            try:
                result = chef_cli.cmd_pantry_use(
                    conn, ctx.home, ns(item=str(pantry_id), qty=body.qty, all=body.all)
                )
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result  # type: ignore[return-value]

    @app.delete("/api/v1/kitchen/pantry/{pantry_id}")
    def pantry_remove(pantry_id: int) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.chef import cli as chef_cli

        with open_conn(ctx) as conn:
            _require_chef(conn)
            try:
                result = chef_cli.cmd_pantry_remove(conn, ctx.home, ns(ref=str(pantry_id)))
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result  # type: ignore[return-value]

    @app.post("/api/v1/kitchen/shopping")
    def shopping_add(body: ShoppingAddRequest) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.chef import cli as chef_cli

        with open_conn(ctx) as conn:
            _require_chef(conn)
            try:
                result = chef_cli.cmd_shopping_add(
                    conn, ctx.home, ns(item=body.item, reason=body.reason, aisle=body.aisle)
                )
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result  # type: ignore[return-value]

    @app.post("/api/v1/kitchen/shopping/bought")
    def shopping_bought(body: ShoppingBoughtRequest) -> object:
        require_writable(ctx)
        from ...staff.chef import cli as chef_cli

        if not body.items:
            raise HTTPException(status_code=400, detail="items が空です")
        with open_conn(ctx) as conn:
            _require_chef(conn)
            try:
                result = chef_cli.cmd_shopping_bought(
                    conn, ctx.home, ns(items=",".join(body.items), qty=None, expires=None, place=None)
                )
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result

    @app.post("/api/v1/kitchen/meal")
    def meal_log(body: MealLogRequest) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.chef import cli as chef_cli

        with open_conn(ctx) as conn:
            _require_chef(conn)
            try:
                result = chef_cli.cmd_meal_log(
                    conn, ctx.home,
                    ns(date=body.date, slot=body.slot, dish=body.dish, ingredients=body.ingredients,
                       planned=body.planned, note=""),
                )
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result  # type: ignore[return-value]

    # --- recipes（ADR-015 D3） ---

    @app.get("/api/v1/kitchen/recipes")
    def recipes_list(
        q: str | None = None,
        tag: str | None = None,
        favorite: bool | None = None,
        category: str | None = None,
        main_ingredient: str | None = None,
        cuisine: str | None = None,
        sort: str = "recent",
    ) -> dict[str, object]:
        from ...staff.chef import recipes as chef_recipes

        with open_conn(ctx) as conn:
            _require_chef_recipes(conn)
            try:
                items = chef_recipes.list_recipes(
                    conn, q=q, tag=tag, favorite=favorite, category=category,
                    main_ingredient=main_ingredient, cuisine=cuisine, sort=sort,
                )
            except ManorError as exc:
                raise manor_error_to_http(exc)
            return {"items": items}

    @app.get("/api/v1/kitchen/recipes/facets")
    def recipes_facets() -> dict[str, object]:
        """一覧の chip 列用の集計（ADR-015 D9追補）。**`{recipe_id}` より前に登録する**
        ——さもないと `facets` が `int` の `recipe_id` に化けようとして 422 になる。
        """
        from ...staff.chef import recipes as chef_recipes

        with open_conn(ctx) as conn:
            _require_chef_recipes(conn)
            return chef_recipes.facets(conn)

    @app.get("/api/v1/kitchen/recipes/{recipe_id}")
    def recipe_get(recipe_id: int) -> dict[str, object]:
        from ...staff.chef import recipes as chef_recipes

        with open_conn(ctx) as conn:
            _require_chef_recipes(conn)
            try:
                return chef_recipes.get(conn, recipe_id)
            except ManorError as exc:
                raise manor_error_to_http(exc)

    @app.post("/api/v1/kitchen/recipes")
    def recipe_add(body: dict) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.chef import nutrition as chef_nutrition
        from ...staff.chef import recipes as chef_recipes

        with open_conn(ctx) as conn:
            _require_chef_recipes(conn)
            try:
                recipe_id = chef_recipes.add(conn, body)
                # ADR-019 D4「再計算の契機」: 登録。成分表が無い home では静かに何もしない。
                chef_nutrition.refresh(conn, recipe_id)
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            result = chef_recipes.get(conn, recipe_id)
            commit_and_render(conn, ctx)
            return result

    @app.put("/api/v1/kitchen/recipes/{recipe_id}")
    def recipe_update(recipe_id: int, body: dict) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.chef import nutrition as chef_nutrition
        from ...staff.chef import recipes as chef_recipes

        with open_conn(ctx) as conn:
            _require_chef_recipes(conn)
            try:
                result = chef_recipes.update(conn, recipe_id, body)
                # ADR-019 D4「再計算の契機」: 材料の編集（本体の差し替えはここだけ）。
                chef_nutrition.refresh(conn, recipe_id)
                result = chef_recipes.get(conn, recipe_id)
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result

    @app.put("/api/v1/kitchen/recipes/{recipe_id}/meta")
    def recipe_set_meta(recipe_id: int, body: RecipeMetaRequest) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.chef import recipes as chef_recipes

        with open_conn(ctx) as conn:
            _require_chef_recipes(conn)
            fields = body.model_dump(exclude_unset=True)
            try:
                result = chef_recipes.set_meta(conn, recipe_id, **fields)
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result

    @app.post("/api/v1/kitchen/recipes/{recipe_id}/archive")
    def recipe_archive(recipe_id: int) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.chef import recipes as chef_recipes

        with open_conn(ctx) as conn:
            _require_chef_recipes(conn)
            try:
                result = chef_recipes.archive(conn, recipe_id)
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result

    # --- recipes: 取り込み・栄養推定（ADR-015 R2。`claude -p` を呼ぶ） ---

    @app.post("/api/v1/kitchen/recipes/import")
    def recipe_import_from_url(body: RecipeImportRequest) -> dict[str, object]:
        """**保存しない。** 下書きを返すだけ（登録は `POST /api/v1/kitchen/recipes`）。
        `mode` の既定は `"auto"`（ADR-015 D7。自動抽出を先に）。
        """
        from ...staff.chef import recipe_import as chef_recipe_import

        with open_conn(ctx) as conn:
            _require_chef_recipes(conn)
        try:
            result = chef_recipe_import.import_from_url(body.url, mode=body.mode)
        except ManorError as exc:
            raise manor_error_to_http(exc)
        if not result.get("ok"):
            raise HTTPException(status_code=502, detail=str(result.get("reason") or ""))
        recipe = result["recipe"]
        assert isinstance(recipe, dict)  # noqa: S101 - import_from_url() が ok なら必ず dict
        return {
            "recipe": recipe,
            "method": result.get("method", ""),
            "warnings": result.get("warnings") or [],
            # 画面は result.meta を読む（コーディネーターの指示。2026-09-12）。
            # recipe["meta"] と同じ辞書——CLI/`--save` はそちらを使うので残す。
            "meta": recipe.get("meta"),
        }

    @app.post("/api/v1/kitchen/recipes/refine")
    def recipe_refine(body: RecipeRefineRequest) -> dict[str, object]:
        """ADR-015 D7-2。自動抽出の下書きを Claude で整える。**保存しない**。"""
        from ...staff.chef import recipe_import as chef_recipe_import

        result = chef_recipe_import.refine_with_claude(body.recipe)
        if not result.get("ok"):
            raise HTTPException(status_code=502, detail=str(result.get("reason") or ""))
        recipe = result["recipe"]
        assert isinstance(recipe, dict)  # noqa: S101 - refine_with_claude() が ok なら必ず dict
        return {
            "recipe": recipe,
            "method": "claude",
            "warnings": result.get("warnings") or [],
            "meta": recipe.get("meta"),
        }

    # `POST /recipes/{id}/estimate-nutrition`（`claude -p` に栄養価を言わせる ADR-015
    # D2 手順5 の口）は 2026-09-13 に畳んだ（ADR-019 §5「LLM に栄養値を言わせない」）。
    # 材料からの推定は `POST /food/aliases` の副作用と `manor chef nutrition rebuild`。

    # --- nutrition / food（材料からの推定と名寄せ。ADR-019 D5） ---
    #
    # `GET /recipes/{id}/nutrition` は**保存されている5項目に `source`/`coverage`/
    # `unresolved` を足すだけ**——XR（kitchen-xr）が読む形は変えない（ADR-019 D5）。

    @app.get("/api/v1/kitchen/recipes/{recipe_id}/nutrition")
    def recipe_nutrition(recipe_id: int) -> dict[str, object]:
        from ...staff.chef import nutrition as chef_nutrition

        with open_conn(ctx) as conn:
            _require_chef_recipes(conn)
            try:
                return chef_nutrition.nutrition_payload(conn, recipe_id)
            except ManorError as exc:
                raise _nutrition_error_to_http(exc)

    @app.post("/api/v1/kitchen/recipes/{recipe_id}/rebuild-nutrition")
    def recipe_rebuild_nutrition(recipe_id: int) -> dict[str, object]:
        """材料から推定して保存する（ADR-019 D4）。`site`／`manual` は上書きしない。"""
        require_writable(ctx)
        from ...staff.chef import nutrition as chef_nutrition

        with open_conn(ctx) as conn:
            _require_chef_recipes(conn)
            _require_chef_food(conn)
            try:
                chef_nutrition.rebuild(conn, recipe_id=recipe_id)
                payload = chef_nutrition.nutrition_payload(conn, recipe_id)
            except ManorError as exc:
                conn.rollback()
                raise _nutrition_error_to_http(exc)
            commit_and_render(conn, ctx)
            return payload

    @app.get("/api/v1/kitchen/food/search")
    def food_search(q: str = "", limit: int = 30) -> dict[str, object]:
        """成分表を名前の部分一致で引く（ADR-019 D5「食品を選ぶ」）。"""
        from ...staff.chef import nutrition as chef_nutrition

        with open_conn(ctx) as conn:
            _require_chef_food(conn)
            try:
                return {"items": chef_nutrition.search_foods(conn, q, limit=limit)}
            except ManorError as exc:
                raise _nutrition_error_to_http(exc)

    @app.get("/api/v1/kitchen/food/aliases")
    def food_aliases() -> dict[str, object]:
        """名寄せの画面の中身（ADR-019 D5）: 未解決の一覧＋登録済みの名寄せ。"""
        from ...staff.chef import nutrition as chef_nutrition

        with open_conn(ctx) as conn:
            _require_chef_recipes(conn)
            _require_chef_food(conn)
            try:
                unresolved = chef_nutrition.unresolved_summary(conn)
                aliases = chef_nutrition.list_aliases(conn)
            except ManorError as exc:
                raise _nutrition_error_to_http(exc)
            return {
                "unresolved": unresolved["items"],
                "unresolved_total": unresolved["total"],
                "food_table_available": unresolved.get("available", True),
                "aliases": aliases,
            }

    @app.post("/api/v1/kitchen/food/aliases")
    def food_alias_set(body: FoodAliasRequest) -> dict[str, object]:
        """名寄せを1件入れ、**その場で全件を推定し直す**（ADR-019 D4「再計算の契機」）。"""
        require_writable(ctx)
        from ...staff.chef import nutrition as chef_nutrition

        with open_conn(ctx) as conn:
            _require_chef_recipes(conn)
            _require_chef_food(conn)
            try:
                saved = chef_nutrition.set_alias(
                    conn, body.alias, body.food_code, confidence=body.confidence
                )
                rebuilt = chef_nutrition.rebuild(conn)
            except ManorError as exc:
                conn.rollback()
                raise _nutrition_error_to_http(exc)
            commit_and_render(conn, ctx)
            return {"alias": saved, "rebuilt": {k: rebuilt[k] for k in ("updated", "skipped")}}

    @app.delete("/api/v1/kitchen/food/aliases/{alias}")
    def food_alias_remove(alias: str) -> dict[str, object]:
        """名寄せを1件消し、推定し直す（間違えて結んだときの戻し口）。"""
        require_writable(ctx)
        from ...staff.chef import nutrition as chef_nutrition

        with open_conn(ctx) as conn:
            _require_chef_recipes(conn)
            _require_chef_food(conn)
            try:
                removed = chef_nutrition.remove_alias(conn, alias)
                chef_nutrition.rebuild(conn)
            except ManorError as exc:
                conn.rollback()
                raise _nutrition_error_to_http(exc)
            commit_and_render(conn, ctx)
            return removed

    # --- cook-sessions（ADR-015 D3） ---

    @app.post("/api/v1/kitchen/cook-sessions")
    def cook_session_start(request: Request, body: CookSessionStartRequest) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.chef import recipes as chef_recipes

        with open_conn(ctx) as conn:
            _require_chef_recipes(conn)
            uid = viewing_user_id(request, conn)
            try:
                result = chef_recipes.start_session(conn, body.recipe_id, user_id=uid)
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result

    @app.get("/api/v1/kitchen/cook-sessions/current")
    def cook_session_current(request: Request) -> dict[str, object]:
        from ...staff.chef import recipes as chef_recipes

        with open_conn(ctx) as conn:
            _require_chef_recipes(conn)
            uid = viewing_user_id(request, conn)
            result = chef_recipes.current_session(conn, user_id=uid)
            return result if result is not None else {"id": None, "recipe_id": None, "current": None}

    @app.post("/api/v1/kitchen/cook-sessions/{session_id}/events")
    def cook_session_event(session_id: int, body: CookSessionEventRequest) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.chef import recipes as chef_recipes

        with open_conn(ctx) as conn:
            _require_chef_recipes(conn)
            try:
                result = chef_recipes.apply_event(conn, session_id, body.type, step=body.step)
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result

    @app.post("/api/v1/kitchen/cook-sessions/{session_id}/end")
    def cook_session_end(session_id: int) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.chef import recipes as chef_recipes

        with open_conn(ctx) as conn:
            _require_chef_recipes(conn)
            try:
                result = chef_recipes.end_session(conn, session_id)
            except ManorError as exc:
                conn.rollback()
                raise manor_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result

    # --- menu（献立のおすすめ。ADR-018 D6） ---
    #
    # `viewing_user_id`（ADR-014 D3）を解決して応答に載せるが、**絞りには使わない**
    # ——台所は共通（ADR-014 D4）なので、誰が開いても同じおすすめが出るのが正
    # （動画リストが利用者ごとだったのとは逆の判断。ADR-018 §4 に明記）。
    # 端末鍵の範囲（`web/net.py` の `/api/v1/kitchen`）にそのまま入る。

    @app.get("/api/v1/kitchen/menu/recommend")
    def menu_recommend(
        request: Request,
        main_recipe_id: int | None = None,
        people: int = 2,
        mood: str = "",
        slot: str = "dinner",
        exclude: str = "",
    ) -> dict[str, object]:
        """枠ごとの候補を採点順に返す（ADR-018 D3）。`exclude` はカンマ区切りの id。"""
        from ...staff.chef import menu as chef_menu

        try:
            excluded = [int(part) for part in exclude.split(",") if part.strip()]
        except ValueError:
            raise HTTPException(status_code=400, detail="exclude はカンマ区切りの id です")
        with open_conn(ctx) as conn:
            _require_chef_recipes(conn)
            try:
                result = chef_menu.recommend(
                    conn,
                    main_recipe_id=main_recipe_id,
                    people=people,
                    mood=mood,
                    slot=slot,
                    exclude=excluded,
                )
            except ManorError as exc:
                raise _menu_error_to_http(exc)
            result["viewing_user_id"] = viewing_user_id(request, conn)
            return result

    @app.post("/api/v1/kitchen/menu/plan")
    def menu_plan(body: MenuPlanRequest) -> dict[str, object]:
        """「この献立にする」（ADR-018 D6）。`chef_meal` に `planned=1` で行を書く。"""
        require_writable(ctx)
        from ...staff.chef import menu as chef_menu

        with open_conn(ctx) as conn:
            _require_chef_recipes(conn)
            _require_chef(conn)
            try:
                result = chef_menu.plan(
                    conn, date=body.date, slot=body.slot, recipe_ids=body.recipe_ids
                )
            except ManorError as exc:
                conn.rollback()
                raise _menu_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result

    # --- media（動画リスト。ADR-016 D3） ---
    #
    # すべて `viewing_user_id`（ADR-014 D3）で絞る——動画は利用者ごと（ADR-016 D1）。
    # `reorder` を `{media_id}` より先に登録する（`facets` と同じ理由——さもないと
    # `reorder` が `media_id` として吸われる）。

    @app.get("/api/v1/kitchen/media")
    def media_list(request: Request) -> dict[str, object]:
        """**XR（kitchen-xr）はこの口だけを読む**（ADR-016 D3）。"""
        from ...staff.chef import media as chef_media

        with open_conn(ctx) as conn:
            _require_chef_media(conn)
            return chef_media.list_payload(conn, user_id=viewing_user_id(request, conn))

    @app.post("/api/v1/kitchen/media", status_code=201)
    def media_add(request: Request, body: MediaAddRequest) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.chef import media as chef_media

        with open_conn(ctx) as conn:
            _require_chef_media(conn)
            try:
                result = chef_media.add_from_url(
                    conn, body.url, user_id=viewing_user_id(request, conn), memo=body.memo
                )
            except ManorError as exc:
                conn.rollback()
                raise _media_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result

    @app.post("/api/v1/kitchen/media/reorder")
    def media_reorder(request: Request, body: MediaReorderRequest) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.chef import media as chef_media

        with open_conn(ctx) as conn:
            _require_chef_media(conn)
            try:
                items = chef_media.reorder(conn, body.ids, user_id=viewing_user_id(request, conn))
            except ManorError as exc:
                conn.rollback()
                raise _media_error_to_http(exc)
            commit_and_render(conn, ctx)
            return {"items": items}

    @app.patch("/api/v1/kitchen/media/{media_id}")
    def media_update(request: Request, media_id: str, body: MediaUpdateRequest) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.chef import media as chef_media

        with open_conn(ctx) as conn:
            _require_chef_media(conn)
            fields = body.model_dump(exclude_unset=True)
            try:
                result = chef_media.update(
                    conn, media_id, user_id=viewing_user_id(request, conn), **fields
                )
            except ManorError as exc:
                conn.rollback()
                raise _media_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result

    @app.delete("/api/v1/kitchen/media/{media_id}")
    def media_remove(request: Request, media_id: str) -> dict[str, object]:
        require_writable(ctx)
        from ...staff.chef import media as chef_media

        with open_conn(ctx) as conn:
            _require_chef_media(conn)
            try:
                result = chef_media.remove(conn, media_id, user_id=viewing_user_id(request, conn))
            except ManorError as exc:
                conn.rollback()
                raise _media_error_to_http(exc)
            commit_and_render(conn, ctx)
            return result
