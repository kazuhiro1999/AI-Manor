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
 *
 * `?url=` 付きで開くと、その URL を欄に入れて自動抽出まで進める（ADR-021 §6: お供の候補の
 * 「レシピ帳に入れる」から来る）。**登録はしない**——下書きを見て決めるのは主人。
 */
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../../app/api";
import { useToast } from "../../components/Toast";
import { ScreenHeader } from "../../components/ScreenHeader";
import { useT } from "../../app/i18n";
import type { Recipe, RecipeImportResult, RecipeMeta } from "../../app/types";
import { RecipeFieldsEditor } from "./RecipeFieldsEditor";
import {
  describeImportMethod,
  emptyNutritionForm,
  emptyRecipeForm,
  formValueToRecipeBody,
  NUTRITION_FIELDS,
  NUTRITION_LABEL_KEY,
  NUTRITION_SOURCE_LABEL_KEY,
  nutritionMetaToForm,
  recipeBodyToFormValue,
  type NutritionFormValue,
  type RecipeFormValue,
} from "./recipeShared";

// 取り込みの下書きが持つ「うちの値」相当（分類3軸）——登録画面には専用の select は
// 置かない（編集ページの `RecipeMetaForm` が本来の置き場）が、下書きに入っていた値は
// 捨てずに持っておき、登録直後の `PUT /recipes/{id}/meta` に一緒に乗せる（ADR-015 D1
// 「本体とうちの値を分ける」を保ちつつ、取り込んだ分類を無かったことにしない）。
interface ImportedAxis {
  category: string;
  mainIngredient: string;
  cuisine: string;
}
function emptyImportedAxis(): ImportedAxis {
  return { category: "", mainIngredient: "", cuisine: "" };
}

// 「薄い抽出」の目安。generic かつ warnings がこれ以上あれば Claude を勧める
// （§6 D7「サイト別の抽出は壊れる前提——汎用も薄ければ Claude を勧める帯を出す」）。
const GENERIC_WARNING_HINT_THRESHOLD = 2;

export function RecipeNewPage() {
  const t = useT();
  const navigate = useNavigate();
  const { show } = useToast();

  const [searchParams] = useSearchParams();
  const initialUrl = searchParams.get("url") || "";
  const [importUrl, setImportUrl] = useState(initialUrl);
  const [importing, setImporting] = useState(false);
  const [refining, setRefining] = useState(false);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [method, setMethod] = useState<string | null>(null);
  // ADR-023 D3: 同じ出典のレシピがもうあるとき（下書きは見せるが、登録はサーバが 409 で断る）。
  const [duplicate, setDuplicate] = useState<RecipeImportResult["duplicate"]>(null);
  const [form, setForm] = useState<RecipeFormValue>(() => emptyRecipeForm());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // 取り込みの下書きが「うちの値」（分類3軸・栄養5つ・出どころ）を持ってきたときの一時置き場
  // （§6 追補。バックエンドが `meta.nutrition_source: "site"` 等を返す想定）。
  const [nutrition, setNutrition] = useState<NutritionFormValue>(() => emptyNutritionForm());
  const [nutritionSource, setNutritionSource] = useState<RecipeMeta["nutrition_source"]>("");
  const [importedAxis, setImportedAxis] = useState<ImportedAxis>(() => emptyImportedAxis());

  const applyImportedMeta = (meta: RecipeImportResult["meta"]) => {
    if (!meta) return;
    setNutrition(nutritionMetaToForm(meta));
    setNutritionSource(meta.nutrition_source ?? "");
    setImportedAxis({
      category: meta.category || "",
      mainIngredient: meta.main_ingredient || "",
      cuisine: meta.cuisine || "",
    });
  };

  const runImport = async (mode: "auto" | "claude" = "auto", url: string = importUrl) => {
    setError(null);
    if (!url.trim()) {
      setError(t("kitchen.recipes.importUrlRequired"));
      return;
    }
    setImporting(true);
    try {
      const res = await api<RecipeImportResult>("/kitchen/recipes/import", { method: "POST", body: { url: url.trim(), mode } });
      setForm(recipeBodyToFormValue(res.recipe));
      setWarnings(res.warnings || []);
      setMethod(res.method);
      setDuplicate(res.duplicate ?? null);
      applyImportedMeta(res.meta);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("errors.saveFailed", { reason: t("common.unknown") }));
    } finally {
      setImporting(false);
    }
  };

  // `?url=` で来たら1回だけ自動抽出する（StrictMode の二重実行で2回取りに行かない）。
  const autoImported = useRef(false);
  useEffect(() => {
    if (!initialUrl || autoImported.current) return;
    autoImported.current = true;
    void runImport("auto", initialUrl);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialUrl]);

  const refine = async () => {
    setError(null);
    setRefining(true);
    try {
      const res = await api<RecipeImportResult>("/kitchen/recipes/refine", { method: "POST", body: { recipe: formValueToRecipeBody(form) } });
      setForm(recipeBodyToFormValue(res.recipe));
      setWarnings(res.warnings || []);
      setMethod(res.method);
      applyImportedMeta(res.meta);
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
    setDuplicate(null);
    setError(null);
    setImportUrl("");
    setNutrition(emptyNutritionForm());
    setNutritionSource("");
    setImportedAxis(emptyImportedAxis());
  };

  const recommendClaude = method === "generic" && warnings.length >= GENERIC_WARNING_HINT_THRESHOLD;

  const submit = async () => {
    setError(null);
    setBusy(true);
    try {
      const body = formValueToRecipeBody(form);
      const recipe = await api<Recipe>("/kitchen/recipes", { method: "POST", body });

      // 取り込みの下書き・手入力のどちらかで「うちの値」（分類3軸・栄養5つ）に値が
      // 入っていれば、登録直後に別送信で乗せる（`POST /recipes` 自体は本体だけの契約
      // ——ADR-015 D1「本体とうちの値を分ける」。ここで作った id が要るので後追いになる）。
      const metaBody: Record<string, unknown> = {};
      if (importedAxis.category) metaBody.category = importedAxis.category;
      if (importedAxis.mainIngredient) metaBody.main_ingredient = importedAxis.mainIngredient;
      if (importedAxis.cuisine) metaBody.cuisine = importedAxis.cuisine;
      for (const key of NUTRITION_FIELDS) {
        if (nutrition[key].trim() !== "") metaBody[key] = Number(nutrition[key]);
      }
      // `nutrition_source` は分かっているときだけ明示する——数値を1つでも渡すと
      // `chef_recipe.set_meta` が既定で `manual` に倒すため（`RecipeMetaForm.tsx` 冒頭の
      // 注記と同じ理由）、取り込みが `site`/`estimated` を返していたらそれを守る。
      if (nutritionSource) metaBody.nutrition_source = nutritionSource;
      if (Object.keys(metaBody).length) {
        try {
          await api(`/kitchen/recipes/${recipe.id}/meta`, { method: "PUT", body: metaBody });
        } catch {
          // うちの値は付随情報——ここで失敗しても登録自体は成立させる（一覧・編集で後から直せる）。
        }
      }

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

        {duplicate && (
          <div className="banner warn" style={{ marginTop: 10 }} id="recipe-import-duplicate">
            {t("kitchen.recipes.duplicateFound", { title: duplicate.title })}{" "}
            <Link to={`/kitchen/recipes/${duplicate.id}`}>{t("kitchen.recipes.duplicateOpen")}</Link>
          </div>
        )}

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

        {/* 「うちの値」の栄養5つは本来は編集ページ（RecipeMetaForm）の持ち物だが、取り込みの
         * 下書きが `meta.kcal` 等（例: Nadia のページの表示値）を持ってくることがあるので、
         * 登録の時点でも小さく見せて確かめられるようにする（ここで直した値は登録直後の
         * `PUT /recipes/{id}/meta` に乗る）。 */}
        <h3>{t("kitchen.recipes.nutritionHeading")}</h3>
        <div className="form-inline">
          {NUTRITION_FIELDS.map((key) => (
            <div className="form-row" style={{ maxWidth: 100 }} key={key}>
              <label htmlFor={`new-nutrition-${key}`}>{t(NUTRITION_LABEL_KEY[key])}</label>
              <input
                id={`new-nutrition-${key}`}
                className="form-input"
                type="number"
                value={nutrition[key]}
                onChange={(e) => setNutrition({ ...nutrition, [key]: e.target.value })}
              />
            </div>
          ))}
        </div>
        <p className="panel-note">
          {t("kitchen.recipes.nutritionSourceLabel")}: {t(NUTRITION_SOURCE_LABEL_KEY[nutritionSource] ?? "kitchen.recipes.nutritionSourceNone")}
        </p>

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
