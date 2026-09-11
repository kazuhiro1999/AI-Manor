/* manor web — レシピ帳の共有の定数・変換（ADR-015 §3・D4）。
 * フォーム⇄契約 JSON（RecipeBody）の往復と、一覧・台所トップの「直近3件」に使う
 * detail 併読（一覧 API に無い last_cooked_at を補う）をここに集約する
 * ——画面（RecipeList・RecipeForm・RecipeEditPage・kitchen/index.tsx）どうしで書き写さない。
 */
import { api } from "../../app/api";
import type { Recipe, RecipeBody, RecipeIngredient, RecipeListItem, StepCompletion } from "../../app/types";

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

/** ADR-015 D4「直近3件（last_cooked_at 降順、無ければ新しい順）」。一覧 API
 * （`GET /kitchen/recipes`）には last_cooked_at が乗らないため、各レシピの詳細
 * （`GET /recipes/{id}`）もあわせて読んで補う。個人のレシピ帳（数百件規模を想定しない）
 * なので、まとめて並行取得しても軽い——一覧の検索（題名・材料名）にもこの detail を使う。 */
export async function fetchRecipesWithDetails(): Promise<{ items: RecipeListItem[]; details: Record<number, Recipe> }> {
  const items = await api<RecipeListItem[]>("/kitchen/recipes");
  const details: Record<number, Recipe> = {};
  await Promise.all(
    items.map(async (item) => {
      try {
        details[item.id] = await api<Recipe>(`/kitchen/recipes/${item.id}`);
      } catch {
        // 一覧に出ている以上ほぼ起きないが、通信の乱れで1件だけ取れなくても他は諦めない
        // （検索・並び替えはその1件だけ detail 無しの扱いにフォールバックする）。
      }
    })
  );
  return { items, details };
}

/** 「最近作った順」の比較子。無ければ最も古い扱い（=一覧の並び=新しい順の末尾）にする。 */
export function compareByRecentCooked(a: RecipeListItem, b: RecipeListItem, details: Record<number, Recipe>): number {
  const la = details[a.id]?.meta.last_cooked_at || "";
  const lb = details[b.id]?.meta.last_cooked_at || "";
  if (la === lb) return 0;
  return lb.localeCompare(la);
}

/** 題名・材料名のどちらかに一致すれば true（ADR-015 D4「検索欄（題名・材料名）」）。 */
export function recipeMatchesQuery(item: RecipeListItem, q: string, details: Record<number, Recipe>): boolean {
  const needle = q.trim().toLowerCase();
  if (!needle) return true;
  if (item.title.toLowerCase().includes(needle)) return true;
  const detail = details[item.id];
  if (!detail) return false;
  return detail.ingredients.some((ing) => ing.name.toLowerCase().includes(needle));
}
