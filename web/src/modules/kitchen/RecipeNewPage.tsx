/* manor web — レシピの登録画面（ADR-015 D4 `/kitchen/recipes/new`）。
 * 「URL を貼る」→ `POST /recipes/import`（R2。Python 担当が並行実装。ここでは契約どおりの
 * 口を呼ぶだけ）の下書きをそのまま下のフォームへ流し込む。「手で書く」は空のフォームに
 * 戻すボタン——取り込み・手入力は同じ1つのフォーム（RecipeFieldsEditor）に流れ込む
 * （ADR-015 D4「フォームは1つ」）。
 */
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../../app/api";
import { useToast } from "../../components/Toast";
import { ScreenHeader } from "../../components/ScreenHeader";
import { useT } from "../../app/i18n";
import type { Recipe, RecipeImportResult } from "../../app/types";
import { RecipeFieldsEditor } from "./RecipeFieldsEditor";
import { emptyRecipeForm, formValueToRecipeBody, recipeBodyToFormValue, type RecipeFormValue } from "./recipeShared";

export function RecipeNewPage() {
  const t = useT();
  const navigate = useNavigate();
  const { show } = useToast();

  const [importUrl, setImportUrl] = useState("");
  const [importing, setImporting] = useState(false);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [form, setForm] = useState<RecipeFormValue>(() => emptyRecipeForm());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const runImport = async () => {
    setError(null);
    if (!importUrl.trim()) {
      setError(t("kitchen.recipes.importUrlRequired"));
      return;
    }
    setImporting(true);
    try {
      const res = await api<RecipeImportResult>("/kitchen/recipes/import", { method: "POST", body: { url: importUrl.trim() } });
      setForm(recipeBodyToFormValue(res.recipe));
      setWarnings(res.warnings || []);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("errors.saveFailed", { reason: t("common.unknown") }));
    } finally {
      setImporting(false);
    }
  };

  const writeByHand = () => {
    setForm(emptyRecipeForm());
    setWarnings([]);
    setError(null);
    setImportUrl("");
  };

  const submit = async () => {
    setError(null);
    setBusy(true);
    try {
      const body = formValueToRecipeBody(form);
      const recipe = await api<Recipe>("/kitchen/recipes", { method: "POST", body });
      show(t("kitchen.recipes.created"), "ok", 3000);
      // 相対経路にしておく（`RecipesRouter` の `new` から `:id` への移動。単独マウントした
      // 試験でも解決できる——絶対経路だと `RecipesRouter` を単体でレンダーする試験で
      // 「マッチする経路が無い」という無害だが紛らわしい警告が出る）。
      navigate(`../${recipe.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("errors.saveFailed", { reason: t("common.unknown") }));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="view" id="view-kitchen-recipe-new">
      <ScreenHeader title={t("kitchen.recipes.newHeading")} description={t("kitchen.recipes.description")} />

      <section className="panel">
        <div className="panel-head">
          <h2>{t("kitchen.recipes.importHeading")}</h2>
        </div>
        <div className="form-inline">
          <input
            className="form-input"
            placeholder={t("kitchen.recipes.importUrlPlaceholder")}
            value={importUrl}
            onChange={(e) => setImportUrl(e.target.value)}
          />
          <button type="button" className="btn btn-primary btn-small" disabled={importing} onClick={runImport}>
            {t("kitchen.recipes.importButton")}
          </button>
          <button type="button" className="btn btn-small" onClick={writeByHand}>
            {t("kitchen.recipes.manualButton")}
          </button>
        </div>
        {warnings.length > 0 && (
          <div className="banner warn" style={{ marginTop: 10 }}>
            <strong>{t("kitchen.recipes.warningsHeading")}</strong>
            <ul style={{ margin: "4px 0 0", paddingLeft: "1.2em" }}>
              {warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{t("kitchen.recipes.formHeading")}</h2>
        </div>
        <RecipeFieldsEditor value={form} onChange={setForm} />
        {error && <div className="form-error">{error}</div>}
        <div className="form-actions">
          <button type="button" className="btn btn-primary" disabled={busy} onClick={submit}>
            {t("kitchen.recipes.submitCreate")}
          </button>
        </div>
      </section>
    </div>
  );
}
