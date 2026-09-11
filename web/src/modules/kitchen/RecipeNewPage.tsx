/* manor web — レシピの登録画面（ADR-015 D4 `/kitchen/recipes/new`。§6 D7 で取り込みの
 * 選択肢を追加）。
 * 「取り込む」は既定 `mode: "auto"`（§6 D7の1「自動抽出を先に」——速い。外部を呼ばない）で
 * `POST /recipes/import` を呼び、下書きをそのまま下のフォームへ流し込む。返った `method`
 * （jsonld / adapter:<site> / generic / claude）を1行で出す。「手で書く」は空のフォームに
 * 戻すボタン——取り込み・手入力は同じ1つのフォーム（RecipeFieldsEditor）に流れ込む
 * （ADR-015 D4「フォームは1つ」）。
 *
 * §6 D7の2・3: 自動抽出のあとに任意で
 * ①「Claude で整える」— 今のフォームの中身（`POST /refine`）を渡し、返った下書きで
 *   置き換える（本文全部を渡すより短く速い、という前提）
 * ②「Claude で最初から抽出」— 同じ URL を `mode: "claude"` で取り込み直す（従来の R2 の経路）
 * を選べる。`method === "generic"` かつ warnings が多いとき（薄い抽出の目安）は
 * 「Claude で整えることを勧めます」を添える（§6 D7「汎用も薄ければ Claude を勧める帯」）。
 */
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../../app/api";
import { useToast } from "../../components/Toast";
import { ScreenHeader } from "../../components/ScreenHeader";
import { useT } from "../../app/i18n";
import type { Recipe, RecipeImportResult } from "../../app/types";
import { RecipeFieldsEditor } from "./RecipeFieldsEditor";
import {
  describeImportMethod,
  emptyRecipeForm,
  formValueToRecipeBody,
  recipeBodyToFormValue,
  type RecipeFormValue,
} from "./recipeShared";

// 「薄い抽出」の目安。generic かつ warnings がこれ以上あれば Claude を勧める
// （§6 D7「サイト別の抽出は壊れる前提——汎用も薄ければ Claude を勧める帯を出す」）。
const GENERIC_WARNING_HINT_THRESHOLD = 2;

export function RecipeNewPage() {
  const t = useT();
  const navigate = useNavigate();
  const { show } = useToast();

  const [importUrl, setImportUrl] = useState("");
  const [importing, setImporting] = useState(false);
  const [refining, setRefining] = useState(false);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [method, setMethod] = useState<string | null>(null);
  const [form, setForm] = useState<RecipeFormValue>(() => emptyRecipeForm());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const runImport = async (mode: "auto" | "claude" = "auto") => {
    setError(null);
    if (!importUrl.trim()) {
      setError(t("kitchen.recipes.importUrlRequired"));
      return;
    }
    setImporting(true);
    try {
      const res = await api<RecipeImportResult>("/kitchen/recipes/import", { method: "POST", body: { url: importUrl.trim(), mode } });
      setForm(recipeBodyToFormValue(res.recipe));
      setWarnings(res.warnings || []);
      setMethod(res.method);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("errors.saveFailed", { reason: t("common.unknown") }));
    } finally {
      setImporting(false);
    }
  };

  const refine = async () => {
    setError(null);
    setRefining(true);
    try {
      const res = await api<RecipeImportResult>("/kitchen/recipes/refine", { method: "POST", body: { recipe: formValueToRecipeBody(form) } });
      setForm(recipeBodyToFormValue(res.recipe));
      setWarnings(res.warnings || []);
      setMethod(res.method);
      show(t("kitchen.recipes.refined"), "ok", 3000);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("errors.saveFailed", { reason: t("common.unknown") }));
    } finally {
      setRefining(false);
    }
  };

  const writeByHand = () => {
    setForm(emptyRecipeForm());
    setWarnings([]);
    setMethod(null);
    setError(null);
    setImportUrl("");
  };

  const recommendClaude = method === "generic" && warnings.length >= GENERIC_WARNING_HINT_THRESHOLD;

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
          <button type="button" className="btn btn-primary btn-small" disabled={importing} onClick={() => runImport("auto")}>
            {t("kitchen.recipes.importButton")}
          </button>
          <button type="button" className="btn btn-small" onClick={writeByHand}>
            {t("kitchen.recipes.manualButton")}
          </button>
        </div>

        {method && <p className="panel-note">{describeImportMethod(method, t)}</p>}

        {warnings.length > 0 && (
          <div className="banner warn" style={{ marginTop: 10 }}>
            <strong>{t("kitchen.recipes.warningsHeading")}</strong>
            <ul style={{ margin: "4px 0 0", paddingLeft: "1.2em" }}>
              {warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
            {recommendClaude && <p style={{ margin: "6px 0 0", fontWeight: 700 }}>{t("kitchen.recipes.recommendClaudeHint")}</p>}
          </div>
        )}

        {method && (
          <div className="form-actions">
            <button type="button" className="btn btn-small" disabled={refining || importing} onClick={refine}>
              {refining ? t("kitchen.recipes.refining") : t("kitchen.recipes.refineButton")}
            </button>
            <button type="button" className="btn btn-small" disabled={importing || refining} onClick={() => runImport("claude")}>
              {t("kitchen.recipes.reimportClaudeButton")}
            </button>
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
