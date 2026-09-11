/* manor web — レシピ帳の共有の定数・変換（ADR-015 §3・D4・§6）。
 * フォーム⇄契約 JSON（RecipeBody）の往復、一覧の絞り込み（§6 D9。サーバ側で
 * q/tag/favorite/category/main_ingredient/cuisine/sort を検算する契約になったので、
 * 画面はクエリを組み立てて投げるだけ——旧版のように detail を並行取得して手元で
 * 絞り込む必要はもう無い）、分類3軸の語彙、取り込み method の文言化をここに集約する
 * ——画面（RecipeList・RecipeForm・RecipeEditPage・kitchen/index.tsx）どうしで書き写さない。
 */
import { api } from "../../app/api";
import type { TranslationKey } from "../../app/i18n";
import type { RecipeBody, RecipeFacets, RecipeIngredient, RecipeListItem, StepCompletion } from "../../app/types";

// §6 D9「語彙は固定」（`staff/chef/lexicon.toml` が唯一の出どころ。ここは画面の select・
// 一覧の絞り込みチップに使う写し）。
export const CATEGORY_OPTIONS = ["主菜", "副菜", "汁物", "ご飯もの", "麺", "デザート", "その他"] as const;
export const MAIN_INGREDIENT_OPTIONS = ["肉", "魚介", "卵", "野菜", "豆腐・大豆", "きのこ", "その他"] as const;
export const CUISINE_OPTIONS = ["和食", "洋食", "中華", "韓国", "エスニック", "その他"] as const;

// 契約 §3 の上限（`chef/recipes.py` の `_TITLE_MAX`/`_INSTRUCTION_MAX` と同じ値）。
export const STEP_TITLE_MAX = 12;
export const STEP_INSTRUCTION_MAX = 60;
// ADR-015 D4「phases（2〜4）」——バックエンドは1つ以上しか強制しないので、ここは
// 画面側の目安表示だけ（超えても送信は止めない。主人の実データを弾かないための判断）。
export const PHASE_MIN = 2;
export const PHASE_MAX = 4;

export interface IngredientFormValue extends RecipeIngredient {
  key: string; // 行の React key（並べ替え・削除の安定用。契約 JSON には乗らない）
}

export interface PhaseFormValue {
  key: string;
  id: string;
  title: string;
}

export interface StepFormValue {
  key: string;
  phase: string; // PhaseFormValue.id への参照
  title: string;
  instruction: string;
  image: string;
  ingredientsUsed: string; // カンマ区切りの表示用文字列
  timerSec: string;
  completion: StepCompletion;
  tips: string; // 改行区切りの表示用文字列
}

export interface RecipeFormValue {
  title: string;
  sourceUrl: string;
  sourceSite: string;
  servings: string;
  totalMinutes: string;
  tools: string; // カンマ区切り
  ingredients: IngredientFormValue[];
  phases: PhaseFormValue[];
  steps: StepFormValue[];
}

let keySeq = 1;
function nextKey(prefix: string): string {
  keySeq += 1;
  return `${prefix}-${keySeq}`;
}

export function emptyIngredient(): IngredientFormValue {
  return { key: nextKey("ing"), name: "", qty: "", unit: "", prep: "", group: "" };
}

export function emptyPhase(): PhaseFormValue {
  const key = nextKey("phase");
  return { key, id: key, title: "" };
}

export function emptyStep(defaultPhase: string): StepFormValue {
  return {
    key: nextKey("step"),
    phase: defaultPhase,
    title: "",
    instruction: "",
    image: "",
    ingredientsUsed: "",
    timerSec: "",
    completion: "manual",
    tips: "",
  };
}

export function emptyRecipeForm(): RecipeFormValue {
  const phases = [emptyPhase(), emptyPhase()];
  return {
    title: "",
    sourceUrl: "",
    sourceSite: "",
    servings: "",
    totalMinutes: "",
    tools: "",
    ingredients: [],
    phases,
    steps: [emptyStep(phases[0].id)],
  };
}

export function recipeBodyToFormValue(body: RecipeBody): RecipeFormValue {
  const phases: PhaseFormValue[] = (body.phases.length ? body.phases : [{ id: "", title: "" }]).map((p) => ({
    key: nextKey("phase"),
    id: p.id,
    title: p.title,
  }));
  const steps = [...(body.steps || [])].sort((a, b) => a.index - b.index);
  return {
    title: body.title,
    sourceUrl: body.source_url,
    sourceSite: body.source_site,
    servings: body.servings == null ? "" : String(body.servings),
    totalMinutes: body.total_minutes == null ? "" : String(body.total_minutes),
    tools: (body.tools || []).join(", "),
    ingredients: (body.ingredients || []).map((ing) => ({ ...ing, key: nextKey("ing") })),
    phases,
    steps: steps.map((s) => ({
      key: nextKey("step"),
      phase: s.phase,
      title: s.title,
      instruction: s.instruction,
      image: s.image || "",
      ingredientsUsed: (s.ingredients_used || []).join(", "),
      timerSec: s.timer_sec == null ? "" : String(s.timer_sec),
      completion: s.completion,
      tips: (s.tips || []).join("\n"),
    })),
  };
}

function splitList(s: string): string[] {
  return s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
}

export function deriveSiteFromUrl(url: string): string {
  if (!url.trim()) return "";
  try {
    return new URL(url).hostname;
  } catch {
    return "";
  }
}

/** フォームの入力値 → 契約 JSON（`POST/PUT /recipes*` の body）。**ここでは値を弾かない**
 * ——空欄のまま送って、バックエンドの 400 の文言をそのまま画面に出す（主人の指示どおり
 * 「サーバの4xxはその文言をそのまま出す」。二重に検算ロジックを持つと文言がずれる）。 */
export function formValueToRecipeBody(form: RecipeFormValue): RecipeBody {
  return {
    title: form.title.trim(),
    source_url: form.sourceUrl.trim(),
    source_site: form.sourceSite.trim() || deriveSiteFromUrl(form.sourceUrl),
    hero_image: "",
    servings: form.servings.trim() === "" ? null : Number(form.servings),
    total_minutes: form.totalMinutes.trim() === "" ? null : Number(form.totalMinutes),
    ingredients: form.ingredients.map((ing) => ({
      name: ing.name.trim(),
      qty: ing.qty,
      unit: ing.unit,
      prep: ing.prep,
      group: ing.group,
    })),
    tools: splitList(form.tools),
    phases: form.phases.map((p) => ({ id: p.id, title: p.title.trim() })),
    steps: form.steps.map((s, i) => ({
      index: i + 1,
      phase: s.phase,
      title: s.title.trim(),
      instruction: s.instruction.trim(),
      image: s.image.trim() || null,
      ingredients_used: splitList(s.ingredientsUsed),
      timer_sec: s.timerSec.trim() === "" ? null : Number(s.timerSec),
      completion: s.completion,
      tips: s.tips
        .split("\n")
        .map((x) => x.trim())
        .filter(Boolean),
    })),
  };
}

export type RecipeSortMode = "recent" | "cooked" | "title";

// 一覧の絞り（§6 D9「複数軸の同時絞り込み」）。すべて任意——空・null は「絞らない」。
export interface RecipeListFilters {
  q?: string;
  tag?: string | null;
  favorite?: boolean;
  category?: string | null;
  main_ingredient?: string | null;
  cuisine?: string | null;
  sort?: RecipeSortMode;
}

function buildRecipeListQuery(filters: RecipeListFilters): string {
  const params = new URLSearchParams();
  if (filters.q && filters.q.trim()) params.set("q", filters.q.trim());
  if (filters.tag) params.set("tag", filters.tag);
  if (filters.favorite) params.set("favorite", "1");
  if (filters.category) params.set("category", filters.category);
  if (filters.main_ingredient) params.set("main_ingredient", filters.main_ingredient);
  if (filters.cuisine) params.set("cuisine", filters.cuisine);
  if (filters.sort) params.set("sort", filters.sort);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

/** 一覧の取得（§6 D9）。絞り・並びはサーバ側で検算する契約になったので、ここはクエリを
 * 組み立てて `{items}` を剥がすだけ（旧版の detail 並行取得はもう無い）。 */
export async function fetchRecipeList(filters: RecipeListFilters = {}): Promise<RecipeListItem[]> {
  const res = await api<{ items: RecipeListItem[] }>(`/kitchen/recipes${buildRecipeListQuery(filters)}`);
  return res.items;
}

/** 絞り込み chip に添える件数つきの語彙（§6 D9）。 */
export async function fetchRecipeFacets(): Promise<RecipeFacets> {
  return api<RecipeFacets>("/kitchen/recipes/facets");
}

const ADAPTER_SITE_LABEL: Record<string, string> = { nadia: "Nadia", cookpad: "cookpad" };

/** 取り込みの返り値 `method`（§6 D7）を画面向けの1行にする。バックエンドが返す値は
 * `jsonld` / `adapter:<site>` / `generic` / `claude` の4パターン
 * （`adapter:` は新しいサイトアダプタが増えても壊れないよう接頭辞で判定する）。 */
export function describeImportMethod(method: string, t: (key: TranslationKey, params?: Record<string, string | number>) => string): string {
  if (method === "jsonld") return t("kitchen.recipes.methodJsonld");
  if (method === "claude") return t("kitchen.recipes.methodClaude");
  if (method === "generic") return t("kitchen.recipes.methodGeneric");
  if (method.startsWith("adapter:")) {
    const site = method.slice("adapter:".length);
    return t("kitchen.recipes.methodAdapterGeneric", { site: ADAPTER_SITE_LABEL[site] || site });
  }
  return method;
}
