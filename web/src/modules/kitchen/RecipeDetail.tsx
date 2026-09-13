/* manor web — レシピの表示画面（ADR-015 D4 `/kitchen/recipes/{id}`）。
 * XR（kitchen-xr）と同じ「今の工程」の見え方をここでも確かめられるよう、工程はカードで
 * 縦に並べる（ADR-015 D4「工程をカードで縦に」）。右側の「うちの値」は表示のみ——
 * 編集は `/kitchen/recipes/{id}/edit` に分ける（ADR-015 D1「本体とうちの値を分ける」を
 * 画面のページ遷移にもそのまま映す）。
 */
import { useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "../../app/api";
import { useToast } from "../../components/Toast";
import { ScreenHeader } from "../../components/ScreenHeader";
import { formatDay, useT } from "../../app/i18n";
import type { Recipe, RecipeNutrition } from "../../app/types";
import { NUTRITION_SOURCE_LABEL_KEY, NUTRITION_UNRESOLVED_REASON_KEY, formatIngredientAmount } from "./recipeShared";

export function RecipeDetail() {
  const t = useT();
  const navigate = useNavigate();
  const { show } = useToast();
  const { id } = useParams<{ id: string }>();
  const recipeId = Number(id);

  const [recipe, setRecipe] = useState<Recipe | null>(null);
  // 栄養値の出どころ（ADR-019 D5）。本体とは別の口（`/nutrition`）——`source`/`coverage`/
  // `unresolved` は保存されている5項目に**足すだけ**の値で、XR が読む契約は変わらない。
  const [nutrition, setNutrition] = useState<RecipeNutrition | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openTips, setOpenTips] = useState<Set<number>>(new Set());
  const [archiveConfirm, setArchiveConfirm] = useState(false);

  const load = () => {
    api<Recipe>(`/kitchen/recipes/${recipeId}`)
      .then(setRecipe)
      .catch((err) => setError(err instanceof ApiError ? err.message : t("errors.genericLoadFailed")));
    // 推定の情報は**落ちても画面を壊さない**（成分表を入れていない home では 404）。
    api<RecipeNutrition>(`/kitchen/recipes/${recipeId}/nutrition`)
      .then(setNutrition)
      .catch(() => setNutrition(null));
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recipeId]);

  const toggleTips = (index: number) => {
    setOpenTips((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };

  const toggleFavorite = async () => {
    if (!recipe) return;
    try {
      const updated = await api<Recipe>(`/kitchen/recipes/${recipeId}/meta`, { method: "PUT", body: { favorite: !recipe.meta.favorite } });
      setRecipe(updated);
    } catch (err) {
      show(t("errors.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    }
  };

  const fold = async () => {
    if (!archiveConfirm) {
      setArchiveConfirm(true);
      return;
    }
    try {
      await api(`/kitchen/recipes/${recipeId}/archive`, { method: "POST" });
      show(t("kitchen.recipes.folded"), "ok", 3000);
      navigate(".."); // `:id` → 一覧（相対。RecipesRouter 単体の試験でも解決できる）
    } catch (err) {
      show(t("errors.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    }
  };

  if (error) {
    return (
      <div className="view" id="view-kitchen-recipe-detail">
        <ScreenHeader title={t("kitchen.recipes.listHeading")} description={t("kitchen.recipes.description")} />
        <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>
      </div>
    );
  }
  if (!recipe) {
    return (
      <div className="view" id="view-kitchen-recipe-detail">
        <ScreenHeader title={t("kitchen.recipes.listHeading")} description={t("kitchen.recipes.description")} />
        <p className="panel-note">{t("common.loading")}</p>
      </div>
    );
  }

  const phaseTitle = (id: string) => recipe.phases.find((p) => p.id === id)?.title || id;

  return (
    <div className="view" id="view-kitchen-recipe-detail">
      <ScreenHeader title={recipe.title} description={t("kitchen.recipes.description")} />

      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-start" }}>
        <div style={{ flex: "2 1 480px", minWidth: 0 }}>
          <section className="panel">
            {/* §6 D4'「表示ページの頭は完成画像に題名と★を重ねる」。 */}
            <div className={`recipe-hero-head${recipe.hero_image ? "" : " no-image"}`}>
              {recipe.hero_image ? (
                <img src={recipe.hero_image} alt="" loading="lazy" />
              ) : (
                <div className="recipe-hero-placeholder" aria-label={t("kitchen.recipes.noPhoto")} />
              )}
              <div className="recipe-hero-head-overlay">
                <h2>{recipe.title}</h2>
                <button type="button" className="btn btn-icon" aria-label={t("kitchen.recipes.favoriteToggle")} onClick={toggleFavorite}>
                  {recipe.meta.favorite ? "★" : "☆"}
                </button>
              </div>
            </div>

            {/* §6 D4'「kcal/人・分・作った回数の行」。 */}
            <div className="recipe-stats-row">
              {recipe.meta.kcal != null && <span>{t("kitchen.recipes.kcalPerServing", { n: recipe.meta.kcal })}</span>}
              {recipe.total_minutes != null && <span>{t("format.minutes", { n: recipe.total_minutes })}</span>}
              <span>{t("kitchen.recipes.timesCooked", { n: recipe.meta.times_cooked })}</span>
              {recipe.servings != null && <span>{t("kitchen.recipes.detailServings", { n: recipe.servings })}</span>}
            </div>

            {recipe.source_url && (
              <p className="panel-note">
                <a href={recipe.source_url} target="_blank" rel="noreferrer">
                  {recipe.source_site || t("kitchen.recipes.sourceLink")}
                </a>
              </p>
            )}

            {/* 分類3軸（§6 D9）＋自由なタグ。 */}
            <div className="form-inline">
              {recipe.meta.category && <span className="chip">{recipe.meta.category}</span>}
              {recipe.meta.main_ingredient && <span className="chip">{recipe.meta.main_ingredient}</span>}
              {recipe.meta.cuisine && <span className="chip">{recipe.meta.cuisine}</span>}
              {recipe.meta.tags.map((tag) => (
                <span className="chip" key={tag}>
                  {tag}
                </span>
              ))}
            </div>
            <div className="form-actions">
              <Link className="btn btn-small" to={`/kitchen/recipes/${recipeId}/edit`}>
                {t("common.edit")}
              </Link>
              <button type="button" className="btn btn-small btn-danger" onClick={fold}>
                {archiveConfirm ? t("kitchen.recipes.foldConfirm") : t("kitchen.recipes.foldButton")}
              </button>
            </div>
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>{t("kitchen.recipes.ingredientsHeading")}</h2>
            </div>
            <div className="table-scroll">
              <table className="grid">
                <thead>
                  <tr>
                    <th>{t("kitchen.recipes.ingredientsTableName")}</th>
                    <th>{t("kitchen.recipes.ingredientsTableQty")}</th>
                    <th>{t("kitchen.recipes.ingredientsTableUnit")}</th>
                    <th>{t("kitchen.recipes.ingredientsTablePrep")}</th>
                  </tr>
                </thead>
                <tbody>
                  {/* §6 D4'「材料の表（グループの字下げ）」——グループが変わるたびに見出し行を
                   * 挟み、グループつきの行は名前を字下げする（参考画面の「A」と同じ見え方）。 */}
                  {(() => {
                    let lastGroup: string | null = null;
                    return recipe.ingredients.map((ing, i) => {
                      const rows: ReactNode[] = [];
                      if (ing.group && ing.group !== lastGroup) {
                        rows.push(
                          <tr className="recipe-ing-group-row" key={`g-${i}`}>
                            <td colSpan={4}>{ing.group}</td>
                          </tr>
                        );
                      }
                      lastGroup = ing.group || null;
                      rows.push(
                        <tr key={i}>
                          <td className={ing.group ? "recipe-ing-indent" : undefined}>{ing.name}</td>
                          <td>{ing.qty}</td>
                          <td>{ing.unit}</td>
                          <td>{ing.prep}</td>
                        </tr>
                      );
                      return rows;
                    });
                  })()}
                </tbody>
              </table>
            </div>
            {recipe.tools.length > 0 && (
              <p className="panel-note">
                {t("kitchen.recipes.toolsHeading")}: {recipe.tools.join(t("common.listSeparator"))}
              </p>
            )}
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>{t("kitchen.recipes.stepsHeading")}</h2>
            </div>
            <div className="cards">
              {recipe.steps.map((step) => (
                <div className="card step-card" key={step.index}>
                  <div className="card-head">
                    <span className="step-num">{step.index}</span>
                    <span className="card-title">{step.title}</span>
                    <span className="card-pj">{phaseTitle(step.phase)}</span>
                  </div>
                  {step.image && <img src={step.image} alt="" className="step-image" />}
                  <p className="card-body">{step.instruction}</p>
                  {step.timer_sec != null && <p className="panel-note">{t("format.seconds", { n: step.timer_sec })}</p>}
                  {step.tips.length > 0 && (
                    <>
                      <button className="detail-toggle" type="button" onClick={() => toggleTips(step.index)}>
                        {t("kitchen.recipes.stepTipsToggle")}
                      </button>
                      {openTips.has(step.index) && (
                        <div className="detail-box">
                          {step.tips.map((tip, i) => (
                            <div key={i}>{tip}</div>
                          ))}
                        </div>
                      )}
                    </>
                  )}
                </div>
              ))}
            </div>
          </section>
        </div>

        <aside style={{ flex: "1 1 260px", minWidth: 240 }}>
          <section className="panel">
            <div className="panel-head">
              <h2>{t("kitchen.recipes.ourValuesHeading")}</h2>
            </div>
            <p className="panel-note">
              {t("kitchen.recipes.kcalLabel")}: {recipe.meta.kcal ?? t("common.none")} / {t("kitchen.recipes.proteinLabel")}: {recipe.meta.protein_g ?? t("common.none")}
            </p>
            <p className="panel-note">
              {t("kitchen.recipes.fatLabel")}: {recipe.meta.fat_g ?? t("common.none")} / {t("kitchen.recipes.carbLabel")}: {recipe.meta.carb_g ?? t("common.none")} /{" "}
              {t("kitchen.recipes.saltLabel")}: {recipe.meta.salt_g ?? t("common.none")}
            </p>
            <p className="panel-note">
              {t("kitchen.recipes.nutritionSourceLabel")}: {t(NUTRITION_SOURCE_LABEL_KEY[recipe.meta.nutrition_source] ?? "kitchen.recipes.nutritionSourceNone")}
            </p>
            {/* ADR-019 D5: 推定（材料から）の印と解決率、未解決の材料と「名寄せへ」。 */}
            {recipe.meta.nutrition_source === "estimated" && (
              <div id="recipe-nutrition-estimated">
                <p className="panel-note">
                  <span className="chip">{t("kitchen.nutrition.estimatedBadge")}</span>
                </p>
                {nutrition?.coverage != null && (
                  <p className="panel-note">
                    {t("kitchen.nutrition.coverage", { percent: Math.round(nutrition.coverage * 100) })}
                  </p>
                )}
                {nutrition?.partial && (
                  <p className="panel-note warn">
                    {t("kitchen.nutrition.partialWarning", { min: Math.round(nutrition.coverage_min * 100) })}
                  </p>
                )}
              </div>
            )}
            {/* ADR-019 §4 追補: 材料表に書かれていない油（「揚げ油 適量」）の吸収。
                黙って kcal が増えたように見えないよう、内訳の1行で言う。 */}
            {nutrition && (nutrition.adjustments?.length ?? 0) > 0 && (
              <div id="recipe-nutrition-adjustments">
                <ul className="panel-note">
                  {(nutrition.adjustments ?? []).map((a) => (
                    <li key={`${a.kind}-${a.method}`}>
                      {t("kitchen.nutrition.oilAbsorption", {
                        method: a.method,
                        grams: Math.round(a.grams),
                        kcal: Math.round(a.kcal),
                      })}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {nutrition && nutrition.unresolved.length > 0 && (
              <div id="recipe-nutrition-unresolved">
                <p className="panel-note">{t("kitchen.nutrition.unresolvedHeading")}</p>
                <ul className="panel-note">
                  {nutrition.unresolved.map((u) => (
                    <li key={`${u.normalized}-${u.name}`}>
                      {t("kitchen.nutrition.unresolvedLine", {
                        name: u.name,
                        amount: formatIngredientAmount(u.qty, u.unit) || t("kitchen.nutrition.amountUnknown"),
                      })}
                      {" — "}
                      {t(NUTRITION_UNRESOLVED_REASON_KEY[u.reason] ?? "kitchen.nutrition.reason.no_food")}
                    </li>
                  ))}
                </ul>
                <Link className="btn btn-small" to="/settings#settings-food-aliases">
                  {t("kitchen.nutrition.toAliases")}
                </Link>
              </div>
            )}
            {nutrition && !nutrition.food_table_available && (
              <p className="panel-note">{t("kitchen.nutrition.tableMissing")}</p>
            )}
            <p className="panel-note">
              {t("kitchen.recipes.ratingLabel")}: {recipe.meta.rating ?? t("common.none")}
            </p>
            {recipe.meta.memo && <p className="panel-note">{recipe.meta.memo}</p>}
            <p className="panel-note">{t("kitchen.recipes.timesCookedLabel")}: {recipe.meta.times_cooked}</p>
            <p className="panel-note">
              {t("kitchen.recipes.lastCookedLabel")}: {recipe.meta.last_cooked_at ? formatDay(recipe.meta.last_cooked_at, t) : t("common.none")}
            </p>
          </section>
        </aside>
      </div>
    </div>
  );
}
