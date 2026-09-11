/* manor web — レシピの編集画面（ADR-015 D4 `/kitchen/recipes/{id}/edit`）。
 * 本体（RecipeFieldsEditor → `PUT /recipes/{id}`）と「うちの値」（RecipeMetaForm →
 * `PUT /recipes/{id}/meta`）は別の欄・別の送信先（ADR-015 D1「本体とうちの値を分ける」）。
 */
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "../../app/api";
import { useToast } from "../../components/Toast";
import { ScreenHeader } from "../../components/ScreenHeader";
import { useT } from "../../app/i18n";
import type { Recipe } from "../../app/types";
import { RecipeFieldsEditor } from "./RecipeFieldsEditor";
import { RecipeMetaForm } from "./RecipeMetaForm";
import { formValueToRecipeBody, recipeBodyToFormValue, type RecipeFormValue } from "./recipeShared";

export function RecipeEditPage() {
  const t = useT();
  const navigate = useNavigate();
  const { show } = useToast();
  const { id } = useParams<{ id: string }>();
  const recipeId = Number(id);

  const [recipe, setRecipe] = useState<Recipe | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [form, setForm] = useState<RecipeFormValue | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api<Recipe>(`/kitchen/recipes/${recipeId}`)
      .then((r) => {
        if (cancelled) return;
        setRecipe(r);
        setForm(recipeBodyToFormValue(r));
      })
      .catch((err) => {
        if (cancelled) return;
        setLoadError(err instanceof ApiError ? err.message : t("errors.genericLoadFailed"));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recipeId]);

  if (loadError) {
    return (
      <div className="view" id="view-kitchen-recipe-edit">
        <ScreenHeader title={t("kitchen.recipes.editHeading")} description={t("kitchen.recipes.description")} />
        <p className="panel-note">{t("errors.loadFailed", { reason: loadError })}</p>
      </div>
    );
  }
  if (!recipe || !form) {
    return (
      <div className="view" id="view-kitchen-recipe-edit">
        <ScreenHeader title={t("kitchen.recipes.editHeading")} description={t("kitchen.recipes.description")} />
        <p className="panel-note">{t("common.loading")}</p>
      </div>
    );
  }

  const submit = async () => {
    setError(null);
    setBusy(true);
    try {
      const body = formValueToRecipeBody(form);
      const updated = await api<Recipe>(`/kitchen/recipes/${recipeId}`, { method: "PUT", body });
      setRecipe(updated);
      show(t("kitchen.recipes.updated"), "ok", 3000);
      navigate(".."); // `:id/edit` → `:id`（相対。RecipesRouter 単体の試験でも解決できる）
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("errors.saveFailed", { reason: t("common.unknown") }));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="view" id="view-kitchen-recipe-edit">
      <ScreenHeader title={t("kitchen.recipes.editHeading")} description={t("kitchen.recipes.description")} />

      <section className="panel">
        <div className="panel-head">
          <h2>{t("kitchen.recipes.formHeading")}</h2>
        </div>
        <RecipeFieldsEditor value={form} onChange={setForm} />
        {error && <div className="form-error">{error}</div>}
        <div className="form-actions">
          <button type="button" className="btn btn-primary" disabled={busy} onClick={submit}>
            {t("kitchen.recipes.submitUpdate")}
          </button>
        </div>
      </section>

      <RecipeMetaForm recipe={recipe} onUpdated={setRecipe} />
    </div>
  );
}
