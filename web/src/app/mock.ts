/* manor web — 契約どおりの合成データ（ADR-005 §2 の全経路）。バックエンドがまだ無い間、
 * `VITE_MOCK=1` / `?mock=1` のときはここが `/api/v1/...` の代わりに応答する。
 * 要求どおり: 要対応1件・タスク12件・プロジェクト3件・部下のデータ・rules3件・夜勤の報告1件。
 * POST/PUT/DELETE は store を実際に書き換える（画面の操作を確かめられるように）。
 */
import { ApiError, type ApiOptions } from "./api";
import type {
  AgentCard,
  Board,
  CheckResult,
  CtxResponse,
  DashboardData,
  Decision,
  ExtensionDetail,
  FaceModelEntry,
  ExtensionManifest,
  ExtensionOption,
  ExtensionStatus,
  ExtensionSummary,
  Handoff,
  HealthResponse,
  ImportCommitResult,
  ImportPreview,
  KitchenData,
  HouseData,
  LogData,
  Meal,
  MediaItem,
  MenuBandStatus,
  MenuCandidate,
  MenuReason,
  MenuRecommendation,
  Meta,
  MoneyData,
  MoneyExpense,
  NightReport,
  NightStatus,
  PantryItem,
  Project,
  Recipe,
  RecipeBody,
  RecipeFacets,
  RecipeFacetValue,
  RecipeImportResult,
  RecipeListItem,
  RunRow,
  RunsData,
  RunStatsData,
  Rule,
  SecretaryData,
  SettingsData,
  SetupInfo,
  SetupResult,
  ShoppingItem,
  Task,
  TaskClass,
  TaskKind,
  TaskEvent,
  TaskStatus,
  Timeline,
  TimelineLane,
  UserInfo,
} from "./types";

const TODAY = new Date().toISOString().slice(0, 10);

function daysFromToday(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}

/* ---------- projects ---------- */
// ADR-013 D1: 画面から追加した合成プロジェクトの通し番号。既存の P1/P2/X1 とぶつからない
// よう "P" 固定＋連番で作る（本物のバックエンドは node id の連番を使うが、mock はここが
// 唯一の出どころなので、この番号がそのまま新しい project の id になる）。
let projectSeq = 100;
const projects: Project[] = [
  {
    id: "P1",
    code: "p1",
    title: "台所の模様替え",
    kind: "家",
    priority: 1,
    preset: "standard",
    status: "active",
    next_action: "棚の採寸",
    due: daysFromToday(10),
    days_left: 10,
    interest: { nearest_date: daysFromToday(10), doing: 2, last_event_at: null, rank: 1 },
  },
  {
    id: "P2",
    code: "p2",
    title: "確定申告の準備",
    kind: "家",
    priority: 2,
    preset: "careful",
    status: "active",
    next_action: "領収書の整理",
    due: daysFromToday(40),
    days_left: 40,
    interest: { nearest_date: daysFromToday(40), doing: 1, last_event_at: null, rank: 2 },
  },
  {
    id: "X1",
    code: "x1",
    title: "執事の自己改善",
    kind: "執事",
    priority: 3,
    preset: "fast",
    status: "active",
    next_action: "GROWTH.md の棚卸し",
    due: null,
    days_left: null,
    interest: { nearest_date: null, doing: 0, last_event_at: null, rank: 3 },
  },
];

/* ---------- tasks (12件) ---------- */
let taskSeq = 12;
const tasks: Task[] = [
  { id: "T1", project_id: "P1", status: "doing", owner: "master", level: "L1", title: "棚のサイズを測る", body: "台所の北側の壁を測る。", risk: "low" },
  { id: "T2", project_id: "P1", status: "doing", owner: "butler", level: "L2", title: "棚の見積もりを集める", body: "3社から見積もりを取る。", risk: "low" },
  { id: "T3", project_id: "P1", status: "resident", owner: "butler", level: "L2", title: "棚の在庫を見張る", body: "特売の通知を監視する。" },
  { id: "T4", project_id: "P2", status: "doing", owner: "butler", level: "L2", title: "領収書をスキャンする", body: "1月分の領収書をスキャンする。", risk: "medium" },
  { id: "T5", project_id: "P2", status: "todo", owner: "butler", level: "L1", title: "医療費控除の計算", body: "" },
  { id: "T6", project_id: "P2", status: "waiting", owner: "butler", level: "L1", status_note: "税理士の返信待ち", title: "税理士への確認" },
  { id: "T7", project_id: "P2", status: "hold", owner: "butler", level: "L1", status_note: "予算が未確定", title: "会計ソフトの選定" },
  { id: "T8", project_id: "X1", status: "doing", owner: "butler", level: "L3", title: "GROWTH.md の棚卸し", body: "" },
  { id: "T9", project_id: "X1", status: "resident", owner: "butler", level: "L2", title: "スケジュール監視", body: "" },
  { id: "T10", project_id: "P1", status: "done", owner: "butler", level: "L1", title: "採寸道具の購入", done_at: new Date().toISOString(), body: "" },
  { id: "T11", project_id: null, status: "doing", owner: "agent:kitchen", level: "L2", title: "献立の見直し", body: "" },
  { id: "T12", project_id: "P2", status: "withdrawn", owner: "butler", level: "L1", title: "紙の家計簿の継続", body: "電子化に統一したため取り下げ" },
];

const withdrawnRecent = tasks.filter((t) => t.status === "withdrawn").map((t) => ({ ...t, withdrawn_at: new Date().toISOString() }));

/* ---------- decisions (要対応 1件) ---------- */
let decisionSeq = 1;
const decisions: Decision[] = [
  {
    id: "D1",
    status: "open",
    title: "見積もり3社のうちどれにするか",
    asked_at: new Date(Date.now() - 4 * 86400000).toISOString(),
    days: 4,
    stale: true,
    risk: "medium",
    background: "3社の見積もりが揃った。価格差は小さいが納期に差がある。",
    ruling: null,
    evidence: "- 見積書 A社: 12万円・納期3週間\n- 見積書 B社: 11.5万円・納期6週間\n- 見積書 C社: 13万円・納期2週間",
    project_id: "P1",
    tasks: [tasks[1]],
  },
];

const handoffs: Handoff[] = [
  {
    id: 1,
    agent: "kitchen",
    task_id: "T11",
    verdict: null,
    brief: "# 指示書\n献立を見直してください。",
    report: "",
  },
];

let noteSeq = 1;
const notes = [{ id: "N1", title: "資源ごみは第2水曜", body: "", project_id: null as string | null }];

const taskEvents: TaskEvent[] = tasks.slice(0, 5).map((t, i) => ({
  id: i + 1,
  task_id: t.id,
  from_status: null,
  to_status: t.status,
  actor: "board",
  note: "",
  at: new Date(Date.now() - i * 3600000).toISOString(),
}));

/* ---------- kitchen ---------- */
let pantrySeq = 3;
const pantry: PantryItem[] = [
  { id: 1, item: "牛乳", qty: "1", unit: "本", expires: daysFromToday(2), place: "冷蔵庫" },
  { id: 2, item: "米", qty: "5", unit: "kg", expires: daysFromToday(120), place: "パントリー" },
];
let shoppingSeq = 3;
const shopping: ShoppingItem[] = [
  { id: 1, item: "卵", reason: "在庫切れ", aisle: "乳製品" },
  { id: 2, item: "醤油", reason: "残り少ない", aisle: "調味料" },
];
let mealSeq = 2;
const meals: Meal[] = [{ id: 1, date: TODAY, slot: "dinner", dish: "肉じゃが", ingredients: "じゃがいも,牛肉", planned: false }];
const taste = [{ key: "苦手", value: "パクチー" }];

/* ---------- kitchen: レシピ帳（ADR-015 §3・§6 D9。見本1件目は tests/fixtures/chahan.recipe.json
 * と同じ内容——主人がよく作る炒飯。以降は分類3軸（category/main_ingredient/cuisine）が
 * ばらけるように合成した4件——一覧の絞り込み chip・分類ごとの列（D4'）を試せるようにした） ---------- */
let recipeSeq = 5;
// 「畳む」（archive）は消さない（ADR-015 §2 `archive`）。一覧からは外れるが
// `GET /recipes/{id}` はそのまま返る——本物の `chef_recipe.archived_at` と同じ役目を
// この Set が肩代わりする（契約 JSON 自体に archived_at は乗らないため）。
const archivedRecipeIds = new Set<number>();
const recipes: Recipe[] = [
  {
    id: 1,
    title: "パラパラ炒飯（基本）",
    source_url: "https://oceans-nadia.com/user/253470/recipe/440737",
    source_site: "oceans-nadia.com",
    hero_image: "https://picsum.photos/seed/chahan/480/360",
    servings: 2,
    total_minutes: 10,
    ingredients: [
      { name: "ご飯", qty: "300", unit: "g", prep: "温かいものでも冷やご飯でも", group: "主材料" },
      { name: "豚バラ薄切り肉", qty: "50", unit: "g", prep: "粗みじん切り", group: "主材料" },
      { name: "長ねぎ", qty: "20", unit: "cm", prep: "粗みじん切り", group: "主材料" },
      { name: "卵", qty: "3", unit: "個", prep: "割りほぐす", group: "主材料" },
      { name: "しょうが", qty: "5", unit: "g", prep: "みじん切り", group: "主材料" },
      { name: "しょうゆ", qty: "小さじ1", unit: "", prep: "", group: "仕上げ" },
    ],
    tools: ["フライパン", "木べら", "ボウル"],
    phases: [
      { id: "prep", title: "下ごしらえ" },
      { id: "cook", title: "炒める" },
      { id: "finish", title: "仕上げ" },
    ],
    steps: [
      { index: 1, phase: "prep", title: "豚肉を刻む", instruction: "豚バラを粗みじん切りにし、塩ひとつまみをふる。", image: null, ingredients_used: ["豚バラ薄切り肉"], timer_sec: null, completion: "manual", tips: ["ベーコンでも可"] },
      { index: 2, phase: "cook", title: "卵とご飯を入れる", instruction: "強めの中火。油の上に卵液を流し、ご飯をのせて全体を混ぜる。", image: null, ingredients_used: ["卵", "ご飯"], timer_sec: null, completion: "manual", tips: ["パラパラのコツ: 油を多めに"] },
      { index: 3, phase: "finish", title: "味を調える", instruction: "しょうゆを縁から回し入れてザッと炒め、火を止める。", image: null, ingredients_used: ["しょうゆ"], timer_sec: null, completion: "manual", tips: [] },
    ],
    meta: {
      kcal: 620, protein_g: 22, fat_g: 24, carb_g: 78, salt_g: 2.4,
      nutrition_source: "estimated", tags: ["中華", "時短", "米"], rating: 5,
      memo: "うちは油少なめでも十分パラパラになる。", favorite: true, times_cooked: 6,
      last_cooked_at: daysFromToday(-2),
      category: "ご飯もの", main_ingredient: "肉", cuisine: "中華",
    },
  },
  {
    id: 2,
    title: "鶏の唐揚げ",
    source_url: "",
    source_site: "",
    hero_image: "https://picsum.photos/seed/karaage/480/360",
    servings: 3,
    total_minutes: 30,
    ingredients: [
      { name: "鶏もも肉", qty: "400", unit: "g", prep: "一口大に切る", group: "主材料" },
      { name: "しょうゆ", qty: "大さじ1", unit: "", prep: "", group: "下味" },
      { name: "片栗粉", qty: "大さじ4", unit: "", prep: "", group: "衣" },
    ],
    tools: ["揚げ鍋", "バット"],
    phases: [
      { id: "prep", title: "下味" },
      { id: "fry", title: "揚げる" },
    ],
    steps: [
      { index: 1, phase: "prep", title: "下味をつける", instruction: "鶏肉をしょうゆなどに15分漬け込む。", image: null, ingredients_used: ["鶏もも肉", "しょうゆ"], timer_sec: 900, completion: "manual", tips: [] },
      { index: 2, phase: "fry", title: "衣をつけて揚げる", instruction: "片栗粉をまぶし、170度の油で揚げる。", image: null, ingredients_used: ["片栗粉"], timer_sec: 240, completion: "manual", tips: ["二度揚げでカリッと"] },
    ],
    meta: {
      kcal: null, protein_g: null, fat_g: null, carb_g: null, salt_g: null,
      nutrition_source: "", tags: ["揚げ物"], rating: null, memo: "", favorite: false,
      times_cooked: 0, last_cooked_at: null,
      category: "主菜", main_ingredient: "肉", cuisine: "和食",
    },
  },
  {
    id: 3,
    title: "きんぴらごぼう",
    source_url: "",
    source_site: "",
    hero_image: "https://picsum.photos/seed/kinpira/480/360",
    servings: 4,
    total_minutes: 15,
    ingredients: [
      { name: "ごぼう", qty: "1", unit: "本", prep: "ささがき", group: "主材料" },
      { name: "にんじん", qty: "1/2", unit: "本", prep: "細切り", group: "主材料" },
      { name: "ごま油", qty: "小さじ2", unit: "", prep: "", group: "" },
      { name: "しょうゆ", qty: "大さじ1", unit: "", prep: "", group: "調味料" },
      { name: "みりん", qty: "大さじ1", unit: "", prep: "", group: "調味料" },
    ],
    tools: ["フライパン"],
    phases: [
      { id: "prep", title: "下ごしらえ" },
      { id: "cook", title: "炒め煮" },
    ],
    steps: [
      { index: 1, phase: "prep", title: "野菜を切る", instruction: "ごぼうはささがき、にんじんは細切りにする。", image: null, ingredients_used: ["ごぼう", "にんじん"], timer_sec: null, completion: "manual", tips: [] },
      { index: 2, phase: "cook", title: "炒めて煮る", instruction: "ごま油で炒め、調味料を加えて汁気が無くなるまで煮る。", image: null, ingredients_used: ["ごま油", "しょうゆ", "みりん"], timer_sec: 300, completion: "manual", tips: ["水にさらすとアクが抜ける"] },
    ],
    meta: {
      kcal: 95, protein_g: 2, fat_g: 4, carb_g: 12, salt_g: 1.1,
      nutrition_source: "estimated", tags: ["常備菜"], rating: 4, memo: "", favorite: true,
      times_cooked: 2, last_cooked_at: daysFromToday(-9),
      category: "副菜", main_ingredient: "野菜", cuisine: "和食",
    },
  },
  {
    id: 4,
    title: "豆腐とわかめのみそ汁",
    source_url: "https://cookpad.com/recipe/1234567",
    source_site: "cookpad.com",
    hero_image: "https://picsum.photos/seed/miso/480/360",
    servings: 2,
    total_minutes: 10,
    ingredients: [
      { name: "絹ごし豆腐", qty: "150", unit: "g", prep: "さいの目切り", group: "主材料" },
      { name: "乾燥わかめ", qty: "大さじ1", unit: "", prep: "水で戻す", group: "主材料" },
      { name: "だし汁", qty: "400", unit: "ml", prep: "", group: "" },
      { name: "みそ", qty: "大さじ2", unit: "", prep: "", group: "調味料" },
    ],
    tools: ["鍋"],
    phases: [{ id: "cook", title: "煮る" }],
    steps: [
      { index: 1, phase: "cook", title: "具を煮てみそを溶く", instruction: "だし汁を温め、豆腐とわかめを入れてみそを溶き入れる。", image: null, ingredients_used: ["絹ごし豆腐", "乾燥わかめ", "だし汁", "みそ"], timer_sec: null, completion: "manual", tips: [] },
    ],
    meta: {
      kcal: 60, protein_g: 5, fat_g: 2, carb_g: 4, salt_g: 1.8,
      nutrition_source: "estimated", tags: [], rating: null, memo: "", favorite: false,
      times_cooked: 4, last_cooked_at: daysFromToday(-1),
      category: "汁物", main_ingredient: "豆腐・大豆", cuisine: "和食",
    },
  },
  {
    id: 5,
    title: "ナポリタン",
    source_url: "",
    source_site: "",
    hero_image: "",
    servings: 2,
    total_minutes: 20,
    ingredients: [
      { name: "スパゲッティ", qty: "200", unit: "g", prep: "", group: "主材料" },
      { name: "ウインナー", qty: "4", unit: "本", prep: "斜め切り", group: "主材料" },
      { name: "玉ねぎ", qty: "1/2", unit: "個", prep: "薄切り", group: "主材料" },
      { name: "ピーマン", qty: "1", unit: "個", prep: "細切り", group: "主材料" },
      { name: "ケチャップ", qty: "大さじ4", unit: "", prep: "", group: "調味料" },
    ],
    tools: ["フライパン", "鍋"],
    phases: [
      { id: "boil", title: "ゆでる" },
      { id: "fry", title: "炒め合わせる" },
    ],
    steps: [
      { index: 1, phase: "boil", title: "麺をゆでる", instruction: "表示時間どおりにスパゲッティをゆでる。", image: null, ingredients_used: ["スパゲッティ"], timer_sec: null, completion: "manual", tips: [] },
      { index: 2, phase: "fry", title: "具と炒め合わせる", instruction: "具材を炒め、麺とケチャップを加えて絡める。", image: null, ingredients_used: ["ウインナー", "玉ねぎ", "ピーマン", "ケチャップ"], timer_sec: null, completion: "manual", tips: ["ケチャップは先に煮詰めると酸味が飛ぶ"] },
    ],
    meta: {
      kcal: null, protein_g: null, fat_g: null, carb_g: null, salt_g: null,
      nutrition_source: "", tags: ["麺"], rating: null, memo: "", favorite: false,
      times_cooked: 0, last_cooked_at: null,
      category: "麺", main_ingredient: "その他", cuisine: "洋食",
    },
  },
];

// 一覧の並び「新しい順」（`sort=recent`）に使う更新日時。作成・本体更新のたびに今へ差し替える
// （契約 JSON 自体には updated_at が乗らないため、この Map が `chef_recipe.updated_at` の
// 役目を肩代わりする）。見本データは古いほうから新しいほうへ差をつけておく。
const recipeUpdatedAt = new Map<number, string>([
  [1, daysFromToday(-30)],
  [2, daysFromToday(-20)],
  [3, daysFromToday(-10)],
  [4, daysFromToday(-3)],
  [5, daysFromToday(-1)],
]);
function touchRecipeUpdatedAt(id: number): void {
  recipeUpdatedAt.set(id, new Date().toISOString());
}

function recipeToListItem(r: Recipe): RecipeListItem {
  return {
    id: r.id,
    title: r.title,
    hero_image: r.hero_image,
    total_minutes: r.total_minutes,
    servings: r.servings,
    tags: r.meta.tags,
    favorite: r.meta.favorite,
    times_cooked: r.meta.times_cooked,
    last_cooked_at: r.meta.last_cooked_at,
    kcal: r.meta.kcal,
    category: r.meta.category,
    main_ingredient: r.meta.main_ingredient,
    cuisine: r.meta.cuisine,
    updated_at: recipeUpdatedAt.get(r.id) || daysFromToday(0),
  };
}

function sortRecipeItems(items: RecipeListItem[], sort: string): RecipeListItem[] {
  const sorted = [...items];
  if (sort === "title") {
    sorted.sort((a, b) => a.title.localeCompare(b.title, "ja"));
  } else if (sort === "cooked") {
    sorted.sort((a, b) => (b.last_cooked_at || "").localeCompare(a.last_cooked_at || ""));
  } else {
    sorted.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  }
  return sorted;
}

function countFacet(values: string[]): RecipeFacetValue[] {
  const counts = new Map<string, number>();
  for (const v of values) {
    if (!v) continue;
    counts.set(v, (counts.get(v) || 0) + 1);
  }
  return Array.from(counts.entries())
    .map(([value, count]) => ({ value, count }))
    .sort((a, b) => b.count - a.count || a.value.localeCompare(b.value, "ja"));
}

function computeRecipeFacets(): RecipeFacets {
  const active = recipes.filter((r) => !archivedRecipeIds.has(r.id));
  return {
    category: countFacet(active.map((r) => r.meta.category)),
    main_ingredient: countFacet(active.map((r) => r.meta.main_ingredient)),
    cuisine: countFacet(active.map((r) => r.meta.cuisine)),
    tags: countFacet(active.flatMap((r) => r.meta.tags)),
  };
}

/* ---------- house ---------- */
let choreSeq = 2;
const chores: { id: number; name: string; every: number; area: string; overdue_days: number | null }[] = [
  { id: 1, name: "ゴミ出し", every: 7, area: "台所", overdue_days: 0 },
  { id: 2, name: "風呂掃除", every: 3, area: "浴室", overdue_days: 1 },
];
const supplies: { item: string; qty: number; threshold: number }[] = [{ item: "トイレットペーパー", qty: 4, threshold: 5 }];

/* ---------- money ---------- */
let expenseSeq = 3;
const expenses: MoneyExpense[] = [
  { id: 1, date: TODAY, category: "食費", memo: "スーパー", amount: 3200, kind: "expense" },
  { id: 2, date: daysFromToday(-2), category: "光熱費", memo: "電気", amount: 8500, kind: "expense" },
];
const budgets: Record<string, number> = { 食費: 40000, 光熱費: 15000 };
const recurring: { id: number; name: string; next_due: string; overdue_days: number; amount: number }[] = [
  { id: 1, name: "家賃", next_due: daysFromToday(5), overdue_days: -5, amount: 80000 },
];

/* ---------- secretary ---------- */
let reminderSeq = 2;
const reminders: { id: number; text: string; on_date: string; at_time: string | null; done_at: string | null }[] = [
  { id: 1, text: "歯医者の予約確認", on_date: daysFromToday(1), at_time: "10:00", done_at: null },
];
const events: { id: number; title: string; start: string; end: string | null; place: string | null }[] = [];
const inbox = [{ id: 1, received_at: new Date().toISOString(), ref: "郵便物 1通" }];

/* ---------- rules (3件) ---------- */
let ruleSeq = 3;
const rules: Rule[] = [
  {
    id: 1,
    title: "来客時は玄関を片付ける",
    body: "来客の1時間前までに玄関の靴を整理する。",
    scope: "family",
    tags: "来客,玄関",
    effective_from: null,
    effective_to: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    archived_at: null,
  },
  {
    id: 2,
    title: "夜22時以降は静穏時間",
    body: "通知音は鳴らさない。",
    scope: "family",
    tags: "静穏,通知",
    effective_from: null,
    effective_to: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    archived_at: null,
  },
  {
    id: 3,
    title: "ゲストWi-Fiのパスワードは月1回変更",
    body: "毎月1日に変更しホワイトボードに書く。",
    scope: "guests",
    tags: "wifi,来客",
    effective_from: null,
    effective_to: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    archived_at: null,
  },
];

/* ---------- extensions（ADR-009。not_installed 1件・ok 1件の見本） ---------- */

interface MockExtensionState {
  manifest: ExtensionManifest;
  installed: boolean; // detect() の合成結果（API からは変えられない。実装と同じ）
  values: Record<string, string | number | boolean | null>;
  secretHas: Record<string, boolean>;
  testedStatus: "ok" | "error" | null; // test() の記録（home/extensions/state.json 相当）
  checkedAt: string | null;
  reason: string;
}

const extensionsStore: Record<string, MockExtensionState> = {
  voicevox: {
    manifest: {
      id: "voicevox",
      label: "VOICEVOX（音声合成）",
      kind: "local_app",
      summary: "執事の声を VOICEVOX で合成します。無くても OS 既定の声で喋ります。",
      install_steps: [
        "1. https://voicevox.hiroshiba.jp/ から VOICEVOX をダウンロードしてインストールします。",
        "2. 一度 VOICEVOX を起動し、初回の利用規約への同意を済ませます。",
        "3. このカードを開いて話者を選び、保存してください。",
      ],
      fields: [
        {
          key: "speaker",
          label: "話者",
          kind: "select",
          options_from: "speakers",
          help: "エンジンから取得した一覧から選びます（エンジンが起動していないと一覧が空になります）",
          required: true,
        },
        { key: "engine_path", label: "エンジンの場所", kind: "path", help: "空なら自動で探します", required: false },
      ],
      secret_fields: [],
    },
    installed: false,
    values: { speaker: null, engine_path: null },
    secretHas: {},
    testedStatus: null,
    checkedAt: null,
    reason: "",
  },
  tailscale: {
    manifest: {
      id: "tailscale",
      label: "Tailscale（外出先からのアクセス）",
      kind: "local_app",
      summary: "自宅の外から manor の Web アプリへ安全につなげます。無くてもループバック（自宅内）では動きます。",
      install_steps: [
        "1. https://tailscale.com/download から Tailscale をインストールし、サインインします。",
        "2. ターミナルで `tailscale serve --bg 8789` を実行します。",
        "3. 設定の passcode を設定したうえで、[web] require_passcode = true を追記してください。",
      ],
      fields: [],
      secret_fields: [],
    },
    installed: true,
    values: {},
    secretHas: {},
    testedStatus: "ok",
    checkedAt: new Date(Date.now() - 3600000).toISOString(),
    reason: "100.64.1.2   mybook               windows  -",
  },
};

const MOCK_SPEAKERS: ExtensionOption[] = [
  { value: 2, label: "四国めたん（ノーマル）" },
  { value: 0, label: "四国めたん（あまあま）" },
  { value: 3, label: "ずんだもん（ノーマル）" },
];

/** 実装（`extensions.status()`）と同じ優先順位: not_installed > needs_config >（記録済み ok/error）> ready。 */
function computeExtensionStatus(id: string): { status: ExtensionStatus; reason: string; checkedAt: string | null } {
  const st = extensionsStore[id];
  if (!st.installed) {
    return { status: "not_installed", reason: st.reason || "見つかりません（合成データ）", checkedAt: null };
  }
  const missing = st.manifest.fields.some((f) => {
    if (!f.required) return false;
    if (st.manifest.secret_fields.includes(f.key)) return !st.secretHas[f.key];
    const v = st.values[f.key];
    return v == null || v === "";
  });
  if (missing) return { status: "needs_config", reason: "", checkedAt: null };
  if (st.testedStatus) return { status: st.testedStatus, reason: st.reason, checkedAt: st.checkedAt };
  return { status: "ready", reason: "", checkedAt: null };
}

function extensionValuesOut(id: string): Record<string, string | number | boolean | null> {
  const st = extensionsStore[id];
  const out: Record<string, string | number | boolean | null> = {};
  for (const field of st.manifest.fields) {
    if (st.manifest.secret_fields.includes(field.key)) {
      out[`has_${field.key}`] = !!st.secretHas[field.key];
    } else {
      out[field.key] = st.values[field.key] ?? null;
    }
  }
  return out;
}

function extensionSummary(id: string): ExtensionSummary {
  const st = extensionsStore[id];
  const computed = computeExtensionStatus(id);
  return {
    id,
    label: st.manifest.label,
    kind: st.manifest.kind,
    summary: st.manifest.summary,
    status: computed.status,
    checked_at: computed.checkedAt,
    reason: computed.reason,
  };
}

function extensionDetail(id: string): ExtensionDetail {
  const st = extensionsStore[id];
  const computed = computeExtensionStatus(id);
  return {
    id,
    manifest: st.manifest,
    values: extensionValuesOut(id),
    install_steps: st.manifest.install_steps,
    status: computed.status,
    checked_at: computed.checkedAt,
    reason: computed.reason,
  };
}

/* ---------- face（姿の小窓。ADR-008 §7 D14・D15） ---------- */

const FACE_AGENT_LABELS: Record<string, string> = {
  butler: "執事",
  chef: "料理長",
  housekeeper: "家政婦",
  steward: "家令",
  secretary: "秘書",
  qa: "検分",
  auditor: "監査",
};

interface MockFaceModelState {
  hasModel: boolean;
  size: number | null;
  updatedAt: string | null;
  legacy: boolean;
}

// chef だけ最初から姿が置かれている見本(一覧・削除ボタンの両方を初期表示で試せるように)。
const faceModelsStore: Record<string, MockFaceModelState> = Object.fromEntries(
  Object.keys(FACE_AGENT_LABELS).map((agent) => [
    agent,
    agent === "chef"
      ? { hasModel: true, size: 245000, updatedAt: new Date(Date.now() - 86400000).toISOString(), legacy: false }
      : { hasModel: false, size: null, updatedAt: null, legacy: false },
  ])
);

function faceModelEntry(agent: string) {
  const st = faceModelsStore[agent];
  return {
    agent,
    label: FACE_AGENT_LABELS[agent] || agent,
    has_model: st.hasModel,
    size: st.size,
    updated_at: st.updatedAt,
    legacy: st.legacy,
  };
}

const GLTF_MAGIC = [0x67, 0x6c, 0x54, 0x46]; // "glTF"
const FACE_MAX_BYTES = 64 * 1024 * 1024;

async function readsAsGltf(file: Blob): Promise<boolean> {
  const head = new Uint8Array(await file.slice(0, 4).arrayBuffer());
  return head.length === 4 && GLTF_MAGIC.every((b, i) => head[i] === b);
}

/* ---------- runs（ADR-006 §3 D11・§6「稼働と費用」。available: true の見本） ---------- */
const runs: RunRow[] = [
  {
    id: 1,
    kind: "behavior",
    ref: "S6",
    started_at: new Date(Date.now() - 3 * 3600000).toISOString(),
    ended_at: new Date(Date.now() - 3 * 3600000 + 100000).toISOString(),
    model: "claude-sonnet",
    input_tokens: 12000,
    output_tokens: 1800,
    cache_read_tokens: 4000,
    cache_write_tokens: 500,
    cost_usd: 0.11,
    turns: 6,
    exit_reason: "done",
    note: "",
  },
  {
    id: 2,
    kind: "night",
    ref: TODAY,
    started_at: new Date(Date.now() - 8 * 3600000).toISOString(),
    ended_at: new Date(Date.now() - 8 * 3600000 + 900000).toISOString(),
    model: "claude-sonnet",
    input_tokens: 48000,
    output_tokens: 6200,
    cache_read_tokens: 12000,
    cache_write_tokens: 900,
    cost_usd: 0.58,
    turns: 22,
    exit_reason: "done",
    note: "",
  },
  {
    id: 3,
    kind: "gate",
    ref: "staged",
    started_at: new Date(Date.now() - 1 * 3600000).toISOString(),
    ended_at: new Date(Date.now() - 1 * 3600000 + 60000).toISOString(),
    model: "claude-sonnet",
    input_tokens: 6000,
    output_tokens: 700,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
    cost_usd: 0.05,
    turns: 3,
    exit_reason: "failed",
    note: "S8 が落ちた",
  },
];

/* ---------- night (1件) ---------- */
const nightDate = TODAY;
const nightText = `# 夜勤報告 ${TODAY}\n\n昨夜の作業まとめ。\n\n## N1 バックアップの確認\n状態: done\n**やったこと**: バックアップを確認した。\n\n## N2 ログの整理\n状態: hold\n**どこまで**: 半分まで整理した。\n`;

/* ---------- setup（ADR-007 D4）---------- */
// meta.task_classes と同じ生成元。GET /setup の task_classes は、この中から
// fixed かつ HG（default_level）のものを除く（ウィザードから HG 固定クラスは選べない）。
const TASK_CLASSES: TaskClass[] = [
  // policy.toml に足された「一般の作業」。初回セットアップの既定クラス（id が
  // general ならそれを既定にする。web/src/modules/setup/index.tsx の defaultTaskClass）。
  { id: "general", label: "一般の作業", default_level: "L2", fixed: false },
  { id: "workspace_md", label: "ワークスペース内 Markdown の更新", default_level: "L3", fixed: false },
  { id: "research", label: "情報収集・調査", default_level: "L3", fixed: false },
  { id: "overview", label: "全体像の再構成", default_level: "L2", fixed: false },
  { id: "self_config", label: "執事自身の設定変更", default_level: "L2", fixed: false },
  { id: "local_experiment", label: "ローカルの可逆な実験", default_level: "L2", fixed: false },
  { id: "external_ticket", label: "外部チケットの起票・更新", default_level: "L1", fixed: false },
  { id: "external_send", label: "外部への送信・公開", default_level: "HG", fixed: true },
  { id: "auth_billing_pii", label: "認証・課金・個人情報の外部共有", default_level: "HG", fixed: true },
  { id: "irreversible_delete", label: "不可逆な削除", default_level: "HG", fixed: true },
  { id: "git_push_default", label: "既定ブランチへの直接 push / マージ", default_level: "HG", fixed: true },
];

function nonHgTaskClasses(): TaskClass[] {
  return TASK_CLASSES.filter((c) => !(c.fixed && c.default_level === "HG"));
}

/* ---------- task_kind（ADR-010 D2）---------- */
// `manor init` が入れる既定の8つ。`other` は消せない（分類できないものの受け皿）。
let taskKindSeq = 8;
const taskKinds: TaskKind[] = [
  { id: "research", label: "調査・情報収集", sort: 1, archived_at: null },
  { id: "design", label: "検討・設計", sort: 2, archived_at: null },
  { id: "build", label: "作成・実装", sort: 3, archived_at: null },
  { id: "fix", label: "修正・改善", sort: 4, archived_at: null },
  { id: "write", label: "資料・文章の作成", sort: 5, archived_at: null },
  { id: "contact", label: "連絡・調整", sort: 6, archived_at: null },
  { id: "admin", label: "手続き・事務", sort: 7, archived_at: null },
  { id: "other", label: "その他", sort: 8, archived_at: null },
];

function nonArchivedTaskKinds(): TaskKind[] {
  return taskKinds.filter((k) => !k.archived_at).sort((a, b) => a.sort - b.sort);
}

function slugifyTaskKindId(label: string): string {
  const ascii = label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return ascii && /[a-z0-9]/.test(ascii) ? ascii : `kind${taskKindSeq + 1}`;
}

// ADR-007 §6 D7: 「用途」ではなく「使いたい機能」の語彙（旧語彙は捨てた。本番に該当データ無し）。
// `tasks` だけが既定 on（web/src/modules/setup/index.tsx 側でチップの初期選択に反映）。
const PURPOSES: { id: string; label: string }[] = [
  { id: "tasks", label: "タスク・プロジェクトの管理" },
  { id: "kitchen", label: "料理・買い物" },
  { id: "money", label: "家計" },
  { id: "house", label: "家事・消耗品" },
  { id: "secretary", label: "予定・調べもの・書きもの" },
];

const PRESETS: { id: string; label: string }[] = [
  { id: "careful", label: "慎重" },
  { id: "standard", label: "標準" },
  { id: "fast", label: "高速" },
];

// ADR-007 §6 D9: `steward/importer.py` の PRESET_MAPS（zaim・moneyforward）＋「使っていない」。
const MONEY_APPS: { id: string; label: string }[] = [
  { id: "none", label: "使っていない" },
  { id: "zaim", label: "Zaim" },
  { id: "moneyforward", label: "マネーフォワード" },
];

// `?mock=1&setup=0` で「未完了」から画面だけ試せる（既定は既存フローを変えないよう完了済み）。
function initialSetupDone(): boolean {
  if (typeof window === "undefined") return true;
  try {
    const v = new URLSearchParams(window.location.search).get("setup");
    if (v === "0") return false;
  } catch {
    /* noop */
  }
  return true;
}

let setupDone = initialSetupDone();
let setupCompletedAt: string | null = setupDone ? new Date().toISOString() : null;
const profileStore: Record<string, string> = setupDone
  ? {
      "master.callname": "旦那様",
      "butler.callname": "執事",
      purposes: JSON.stringify(["tasks", "kitchen"]),
      "purposes.note": "",
      "money.app": "none",
      "money.currency": "JPY",
      "setup.completed_at": setupCompletedAt as string,
    }
  : { "butler.callname": "執事" };

/* ---------- users（ADR-014 D1・D1'・D3。来月からの同棲に備えた利用者の切り替え） ---------- */
interface MockUser extends UserInfo {
  archived_at: string | null;
}
// ADR-014 D1'（追補）: 利用者名（`name`。識別用）と呼び名（`callname`。執事がどう呼ぶか）
// は別欄——主人「呼び名は主人でいいが、選択時のユーザー名には名前を入れたい」。
const users: MockUser[] = [
  { id: "master", name: "山田 太郎", callname: "旦那様", role: "principal", archived_at: null },
  { id: "u2", name: "同居人", callname: "", role: "member", archived_at: null },
  { id: "butler", name: "執事", callname: "執事", role: "butler", archived_at: null },
];
let userSeq = 2;

/* ---------- devices（ADR-017 D1・D5。端末の鍵。**鍵の平文は mock でも持たない**） ---------- */
interface MockDevice {
  id: string;
  name: string;
  kind: string;
  user_id: string;
  created_at: string;
  last_seen_at: string | null;
  revoked_at: string | null;
}
const devices: MockDevice[] = [
  {
    id: "dev-quest3",
    name: "Quest 3",
    kind: "kitchenxr",
    user_id: "master",
    created_at: "2026-09-10T20:15:00",
    last_seen_at: "2026-09-12T18:40:00",
    revoked_at: null,
  },
];
let deviceSeq = 1;
// ペアリングの待ち行列（番号 → 名前）。mock では `pair/start` を端末が叩く場面が無いので、
// 「画面に入れる番号」を1つだけ用意して、許可の手触りを確かめられるようにしてある。
const mockPairings: Record<string, string> = { "123456": "Quest 3" };
// cookie `manor_user` の合成版（ブラウザの実 Cookie は使わない。mock はこの変数だけで足りる）。
let viewingUserId = "master";

function activeUsers(): MockUser[] {
  return users.filter((u) => !u.archived_at);
}

function currentUserInfo(): UserInfo {
  const found = activeUsers().find((u) => u.id === viewingUserId);
  const fallback = activeUsers().find((u) => u.role === "principal") || activeUsers()[0];
  const u = found || fallback;
  return { id: u.id, name: u.name, callname: u.callname, role: u.role };
}

/* ---------- state ---------- */
let readOnly = false;
let authenticated = true;
// ADR-012 §3 D11: [manor] language の合成版。本物のバックエンドと同じく既定は "auto"。
let manorLanguage: "auto" | "ja" | "en" = "auto";
// ADR-013 D2: [web] passcode / require_passcode の合成版。mock モードはブラウザ内だけの
// デモなので「今ループバックで待ち受けているか」は常に true 固定でよい（本物と違い実際の
// bind host が無い）。
let webHasPasscode = false;
let webRequirePasscode = false;
const webIsLoopback = true;

function badRequest(msg: string): never {
  throw new ApiError(msg, 400);
}
function notFound(msg: string): never {
  throw new ApiError(msg, 404);
}
function conflict(msg: string): never {
  throw new ApiError(msg, 409);
}

const VALID_STATUS: TaskStatus[] = ["todo", "doing", "waiting", "hold", "resident", "done", "withdrawn"];

function computeBoard(): Board {
  const doing = tasks.filter((t) => t.status === "doing");
  const doingButler = doing.filter((t) => t.owner !== "master");
  const resident = tasks.filter((t) => t.status === "resident").length;
  const doneTotal = tasks.filter((t) => t.status === "done").length;
  const visibleTasks = tasks.filter(
    (t) => !["done", "withdrawn"].includes(t.status) || t.status === "done"
  );
  const delegated = tasks.filter((t) => t.status === "doing" && !["butler", "master"].includes(t.owner));
  const fp = JSON.stringify([
    decisions.map((d) => [d.id, d.status]),
    tasks.map((t) => [t.id, t.status]),
  ]);
  return {
    today: TODAY,
    pending: decisions as unknown as Board["pending"],
    tasks: visibleTasks,
    delegated,
    projects,
    milestones: [
      { id: "M1", project_id: "P1", title: "棚の発注", date: daysFromToday(12), approximate: false, days_left: 12, done_at: null },
      // 済んだ節目も1つ置く——画面の「済」表示と戻しボタンを合成データだけで触れるように。
      { id: "M2", project_id: "P2", title: "確定申告 提出", date: daysFromToday(45), approximate: true, days_left: 45, done_at: null },
      { id: "M3", project_id: "P1", title: "棚の下見", date: daysFromToday(-3), approximate: false, days_left: -3, done_at: "2026-09-02T10:00:00" },
    ],
    recent_done: tasks.filter((t) => t.status === "done"),
    withdrawn_recent: withdrawnRecent,
    notes,
    counts: {
      pending: decisions.filter((d) => d.status === "open").length,
      doing: doing.length,
      doing_butler: doingButler.length,
      doing_master: doing.length - doingButler.length,
      resident,
      blocked_ready: 0,
      stale: 0,
      done_total: doneTotal,
    },
    fingerprint: fp.length.toString(36) + "-" + fp.split("").reduce((a, c) => a + c.charCodeAt(0), 0),
  };
}

function computeTimeline(days: number): Timeline {
  const lanes: TimelineLane[] = projects.map((p) => ({
    id: p.id,
    project_id: p.id,
    name: p.title,
    code: p.code,
    priority: p.priority,
    scheduled: !!p.due,
    events: p.due
      ? [
          {
            kind: "deadline",
            start: TODAY,
            end: p.due,
            start_days: 0,
            end_days: Math.max(0, Math.min(days, p.days_left ?? 0)),
            title: `期限: ${p.due}`,
            approximate: false,
            done: false,
            overdue: false,
            ref: p.id,
            detail: `${p.title} の期限`,
          },
        ]
      : [],
  }));
  return { today: TODAY, horizon_days: days, horizon: daysFromToday(days), lanes };
}

function computeLog(): LogData {
  return {
    state: `# 執事の現在地\n\n本日 ${TODAY}。要対応 ${decisions.filter((d) => d.status === "open").length} 件。`,
    decided: decisions.filter((d) => d.status !== "open") as unknown as LogData["decided"],
    handoffs,
    check: computeCheck(),
    events: taskEvents,
  };
}

function computeCheck(): CheckResult {
  return {
    ok: true,
    results: { C1: [], C2: [], C3: [] },
    labels: { C1: "孤立ノード", C2: "期日の逆転", C3: "状態の矛盾" },
  };
}

function findTask(id: string): Task {
  const t = tasks.find((x) => x.id === id);
  if (!t) notFound(`task が見つかりません: ${id}`);
  return t;
}

// ADR-015: `recipes.validate()` を手元で真似た簡易版（見本データの整合を保つだけなので、
// 本物の `chef/recipes.py` ほど厳密ではない。上限文字数と phases/steps の必須だけ検算する）。
function findRecipe(id: number): Recipe {
  const r = recipes.find((x) => x.id === id);
  if (!r) notFound(`レシピが見つかりません: ${id}`);
  return r;
}

function validateRecipeBody(body: RecipeBody): RecipeBody {
  const title = String(body.title || "").trim();
  if (!title) badRequest("title が必須です");
  const phases = Array.isArray(body.phases) ? body.phases : [];
  if (!phases.length) badRequest("phases が必須です（最低1つ）");
  const phaseIds = new Set(phases.map((p) => p.id));
  const steps = Array.isArray(body.steps) ? body.steps : [];
  if (!steps.length) badRequest("steps が必須です（最低1つ）");
  steps.forEach((s, i) => {
    const stitle = String(s.title || "").trim();
    if (!stitle) badRequest(`steps[${i}].title が必須です`);
    if (stitle.length > 12) badRequest(`steps[${i}].title は12文字以内にしてください（${stitle.length}文字）: ${stitle}`);
    const instruction = String(s.instruction || "").trim();
    if (!instruction) badRequest(`steps[${i}].instruction が必須です`);
    if (instruction.length > 60) badRequest(`steps[${i}].instruction は60文字以内にしてください（${instruction.length}文字）: ${instruction}`);
    if (!phaseIds.has(s.phase)) badRequest(`steps[${i}].phase が phases に無い id を指しています: ${s.phase}`);
  });
  return {
    title,
    source_url: String(body.source_url || ""),
    source_site: String(body.source_site || ""),
    hero_image: String(body.hero_image || ""),
    servings: body.servings == null || (body.servings as unknown) === "" ? null : Number(body.servings),
    total_minutes: body.total_minutes == null || (body.total_minutes as unknown) === "" ? null : Number(body.total_minutes),
    ingredients: Array.isArray(body.ingredients) ? body.ingredients : [],
    tools: Array.isArray(body.tools) ? body.tools : [],
    phases,
    steps: steps.map((s, i) => ({ ...s, index: i + 1 })),
  };
}

/* ADR-016: 動画リストの合成データ。実在の動画は指さない（`video_id` は架空の 11 文字）。
 * 本物と同じく `sort_order` 昇順で返し、`updated_at` は一覧全体の最終更新。 */
const mediaItems: MediaItem[] = [
  {
    id: "media-1",
    title: "煮込み用の長い音楽",
    video_id: "mockAAAAAA1",
    url: "https://www.youtube.com/watch?v=mockAAAAAA1",
    thumbnail_url: "https://i.ytimg.com/vi/mockAAAAAA1/hqdefault.jpg",
    author: "架空チャンネル",
    memo: "煮込みのとき",
    sort_order: 1,
  },
  {
    id: "media-2",
    title: "下ごしらえのラジオ",
    video_id: "mockBBBBBB2",
    url: "https://www.youtube.com/watch?v=mockBBBBBB2",
    thumbnail_url: "https://i.ytimg.com/vi/mockBBBBBB2/hqdefault.jpg",
    author: "",
    memo: "",
    sort_order: 2,
  },
];
let mediaUpdatedAt = new Date().toISOString().slice(0, 19);
let mediaSeq = mediaItems.length;

/** 本物の `chef/media.extract_video_id` を手元で真似た簡易版（受ける形は ADR-016 D2-1）。 */
function mockExtractVideoId(url: string): string | null {
  const m = url.trim().match(/(?:youtu\.be\/|\/shorts\/|\/embed\/|\/live\/|\/v\/|[?&]v=)([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])/);
  if (!m) return null;
  if (!/^https?:\/\/([a-z0-9-]+\.)*(youtube\.com|youtu\.be|youtube-nocookie\.com)\//i.test(url.trim())) return null;
  return m[1];
}

export async function mockApi<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const method = options.method || "GET";
  const body = (options.body || {}) as Record<string, unknown>;

  // ---------- 共通 ----------
  if (path === "/meta" && method === "GET") {
    const meta: Meta = {
      version: "mock-0.1.0",
      today: TODAY,
      read_only: readOnly,
      stale: false,
      auth: { mode: "loopback", authenticated },
      modules: [
        { id: "dashboard", title: "ダッシュボード", icon: "🏠", order: 1, enabled: true },
        { id: "agents", title: "担当", icon: "🧑‍🤝‍🧑", order: 2, enabled: true },
        { id: "tasks", title: "タスク", icon: "T", order: 3, enabled: true },
        { id: "kitchen", title: "台所", icon: "K", order: 4, enabled: true },
        { id: "house", title: "家事", icon: "H", order: 5, enabled: true },
        { id: "money", title: "家計", icon: "¥", order: 6, enabled: true },
        { id: "secretary", title: "秘書", icon: "S", order: 7, enabled: true },
        { id: "rules", title: "ルール", icon: "R", order: 8, enabled: true },
        { id: "imports", title: "取り込み", icon: "I", order: 9, enabled: true },
        { id: "night", title: "夜勤", icon: "N", order: 10, enabled: true },
        // ADR-011 D1: 設定はサイドバーから外れる（フロントは hideFromNav で隠す）。
        // meta.modules 自体からは消さない——設定画面の「モジュールの並び」節が引き続き表示に使う。
        { id: "settings", title: "設定", icon: "⚙", order: 90, enabled: true },
        { id: "extensions", title: "拡張機能", icon: "🧩", order: 100, enabled: true },
      ],
      task_classes: TASK_CLASSES,
      task_kinds: nonArchivedTaskKinds(),
      home_name: "mock-home",
      setup_done: setupDone,
      language: manorLanguage,
      // ADR-014 D3・D1'（追補）: 見ている利用者と、切り替え先の選択肢（畳んでいないもの）。
      // callname（呼び名）も併せて返す——利用者名（name）とは別の欄。
      user: currentUserInfo(),
      users: activeUsers().map((u) => ({ id: u.id, name: u.name, callname: u.callname, role: u.role })),
    };
    return meta as unknown as T;
  }
  if (path === "/auth/login" && method === "POST") {
    const passcode = String(body.passcode || "");
    if (passcode.length < 1) badRequest("passcode が必要です");
    authenticated = true;
    return { ok: true } as unknown as T;
  }
  if (path === "/auth/logout" && method === "POST") {
    authenticated = false;
    return { ok: true } as unknown as T;
  }
  if (path === "/auth/me" && method === "GET") {
    return { authenticated } as unknown as T;
  }
  if (path === "/health" && method === "GET") {
    const h: HealthResponse = { ok: true, started_at: new Date().toISOString(), stale: false };
    return h as unknown as T;
  }

  // ---------- users（ADR-014 D1・D3） ----------
  if (path === "/users" && method === "GET") {
    return { items: activeUsers() } as unknown as T;
  }
  if (path === "/users" && method === "POST") {
    const name = String(body.name || "").trim();
    if (!name) badRequest("name が空です");
    const callname = String(body.callname || "").trim();
    let id = String(body.id || "").trim();
    if (id) {
      if (!/^[a-z][a-z0-9_-]{0,31}$/.test(id)) badRequest(`id の形式が不正です: ${id}`);
      if (users.some((u) => u.id === id)) badRequest(`id が重複しています: ${id}`);
    } else {
      do {
        userSeq += 1;
        id = `u${userSeq}`;
      } while (users.some((u) => u.id === id));
    }
    users.push({ id, name, callname, role: "member", archived_at: null });
    return { id } as unknown as T;
  }
  // `/users/switch` は `/users/{id}` より前に見る（後にすると "switch" が id として食われる）。
  if (path === "/users/switch" && method === "POST") {
    const id = String(body.id || "");
    const u = users.find((x) => x.id === id && !x.archived_at);
    if (!u) notFound(`user が見つからない、または畳まれています: ${id}`);
    viewingUserId = id;
    return { user: { id: u.id, name: u.name, role: u.role } } as unknown as T;
  }
  if (path.match(/^\/users\/[^/]+\/archive$/) && method === "POST") {
    const id = decodeURIComponent(path.split("/")[2]);
    const u = users.find((x) => x.id === id);
    if (!u) notFound(`user が見つかりません: ${id}`);
    if (u.role === "principal" || u.role === "butler") badRequest(`${u.role} は畳めません（ADR-014 D1）`);
    u.archived_at = new Date().toISOString();
    return { id } as unknown as T;
  }
  if (path.match(/^\/users\/[^/]+$/) && method === "POST") {
    const id = decodeURIComponent(path.slice("/users/".length));
    const u = users.find((x) => x.id === id);
    if (!u) notFound(`user が見つかりません: ${id}`);
    // ADR-014 D1'（追補）: name（利用者名）・callname（呼び名）はどちらも任意——渡された方だけ更新する。
    if (body.name !== undefined) {
      const name = String(body.name || "").trim();
      if (!name) badRequest("name が空です");
      u.name = name;
    }
    if (body.callname !== undefined) {
      u.callname = String(body.callname || "").trim();
    }
    return { id } as unknown as T;
  }

  // ---------- devices（ADR-017 D1・D2・D5） ----------
  if (path === "/devices" && method === "GET") {
    const items = devices
      .filter((d) => !d.revoked_at)
      .map((d) => ({ ...d, user_name: users.find((u) => u.id === d.user_id)?.name || d.user_id }));
    return { items } as unknown as T;
  }
  if (path === "/devices/pair/approve" && method === "POST") {
    const code = String(body.code || "").trim();
    const userId = String(body.user_id || "").trim();
    const name = mockPairings[code];
    // 番号が当たらない限り何も起きない（実バックエンドと同じ規則。ADR-017 D2-4）。
    if (!name) notFound("その番号のペアリングは見つかりません（間違いか、5分を過ぎています）");
    if (!activeUsers().some((u) => u.id === userId)) notFound(`利用者が見つかりません: ${userId}`);
    deviceSeq += 1;
    const id = `dev-${deviceSeq}`;
    devices.push({
      id,
      name,
      kind: "kitchenxr",
      user_id: userId,
      created_at: new Date().toISOString(),
      last_seen_at: null,
      revoked_at: null,
    });
    delete mockPairings[code];
    // **鍵は返さない**（受け取るのは端末だけ。ADR-017 D2-2）。
    return { pair_id: `pair-${code}`, device_id: id, name, kind: "kitchenxr", user_id: userId } as unknown as T;
  }
  if (path.match(/^\/devices\/[^/]+$/) && method === "DELETE") {
    const id = decodeURIComponent(path.slice("/devices/".length));
    const d = devices.find((x) => x.id === id);
    if (!d) notFound(`端末が見つかりません: ${id}`);
    d.revoked_at = new Date().toISOString();
    return { id, revoked_at: d.revoked_at } as unknown as T;
  }

  // ---------- tasks ----------
  if (path === "/tasks/board" && method === "GET") return computeBoard() as unknown as T;
  if (path.startsWith("/tasks/timeline") && method === "GET") {
    const params = new URLSearchParams(path.split("?")[1] || "");
    const days = Number(params.get("days") || "70");
    return computeTimeline(days) as unknown as T;
  }
  if (path === "/tasks/log" && method === "GET") return computeLog() as unknown as T;
  if (path.startsWith("/tasks/ctx/") && method === "GET") {
    const id = decodeURIComponent(path.slice("/tasks/ctx/".length));
    const t = tasks.find((x) => x.id === id);
    const d = decisions.find((x) => x.id === id);
    const md = t ? `# ${t.id} ${t.title}\n\n${t.body || "（本文なし）"}` : d ? `# ${d.id} ${d.title}\n\n背景: ${d.background}` : "";
    if (!t && !d) notFound(`見つかりません: ${id}`);
    const res: CtxResponse = { id, markdown: md };
    return res as unknown as T;
  }
  if (path.startsWith("/tasks/handoff/") && !path.includes("/accept") && !path.includes("/reject") && method === "GET") {
    const id = Number(path.slice("/tasks/handoff/".length));
    const h = handoffs.find((x) => x.id === id);
    if (!h) notFound(`handoff が見つかりません: H${id}`);
    return h as unknown as T;
  }
  if (path.match(/^\/tasks\/decision\/.+\/rule$/) && method === "POST") {
    const id = decodeURIComponent(path.split("/")[3]);
    const d = decisions.find((x) => x.id === id);
    if (!d) notFound(`decision が見つかりません: ${id}`);
    const status = body.status as "approved" | "rejected" | "modified";
    if (status === "modified" && !String(body.ruling || "").trim()) badRequest("修正には ruling が必要です");
    d.status = status;
    d.ruling = String(body.ruling || (status === "approved" ? "承認" : status === "rejected" ? "却下" : ""));
    return { id, status: d.status } as unknown as T;
  }
  if (path.match(/^\/tasks\/task\/.+\/status$/) && method === "POST") {
    const id = decodeURIComponent(path.split("/")[3]);
    const t = findTask(id);
    const status = body.status as TaskStatus;
    if (!VALID_STATUS.includes(status)) badRequest(`不正な status: ${status}`);
    if ((status === "waiting" || status === "withdrawn") && !String(body.note || "").trim()) {
      conflict(`${status} には note が必要です`);
    }
    t.status = status;
    t.status_note = String(body.note || "");
    if (status === "done") t.done_at = new Date().toISOString();
    taskEvents.unshift({ id: taskEvents.length + 1, task_id: id, from_status: t.status, to_status: status, actor: "web", note: String(body.note || ""), at: new Date().toISOString() });
    return { id, status: t.status, warnings: [] } as unknown as T;
  }
  if (path === "/tasks/task" && method === "POST") {
    const title = String(body.title || "");
    if (!title.trim()) badRequest("title が必要です");
    if (body.cls === "HG" && !String(body.recommendation || "").trim()) {
      badRequest("HG クラスには recommendation が必須です");
    }
    taskSeq += 1;
    const id = `T${taskSeq}`;
    const t: Task = {
      id,
      project_id: (body.project as string) || null,
      status: "todo",
      owner: "master",
      title,
      body: (body.body as string) || "",
      due: (body.due as string) || null,
      goal: (body.goal as string) || null,
      now: (body.now as string) || null,
      next: (body.next as string) || null,
      recommendation: (body.recommendation as string) || null,
    };
    tasks.push(t);
    return t as unknown as T;
  }
  if (path.match(/^\/tasks\/handoff\/.+\/(accept|reject)$/) && method === "POST") {
    const parts = path.split("/");
    const id = Number(parts[3]);
    const kind = parts[4] as "accept" | "reject";
    const h = handoffs.find((x) => x.id === id);
    if (!h) notFound(`handoff が見つかりません: H${id}`);
    if (kind === "reject" && !String(body.note || "").trim()) badRequest("reject には note が必要です");
    h.verdict = kind;
    return h as unknown as T;
  }
  // ADR-013 D1: プロジェクトの作成・変更。
  if (path === "/tasks/project" && method === "POST") {
    const code = String(body.code || "").trim();
    if (!code) badRequest("code が必要です");
    const name = String(body.name || "").trim();
    if (!name) badRequest("name が必要です");
    if (projects.some((p) => p.code === code)) conflict(`project code が重複しています: ${code}`);
    projectSeq += 1;
    const id = `P${projectSeq}`;
    const p: Project = {
      id,
      code,
      title: name,
      kind: (body.kind as string) || "",
      priority: Number(body.priority ?? 3),
      preset: ((body.preset as string) || "standard") as Project["preset"],
      status: ((body.status as string) || "active") as Project["status"],
      next_action: (body.next_action as string) || "",
      due: (body.due as string) || null,
      days_left: null,
      interest: { nearest_date: null, doing: 0, last_event_at: null, rank: projects.length + 1 },
    };
    projects.push(p);
    return { id } as unknown as T;
  }
  if (path.match(/^\/tasks\/project\/.+$/) && method === "POST") {
    const ref = decodeURIComponent(path.slice("/tasks/project/".length));
    const p = projects.find((x) => x.id === ref || x.code === ref);
    if (!p) notFound(`project が見つかりません: ${ref}`);
    if (body.name !== undefined) p.title = String(body.name);
    if (body.kind !== undefined) p.kind = String(body.kind);
    if (body.priority !== undefined) p.priority = Number(body.priority);
    if (body.preset !== undefined) p.preset = body.preset as Project["preset"];
    if (body.status !== undefined) p.status = body.status as Project["status"];
    if (body.due !== undefined) p.due = (body.due as string) || null;
    if (body.next_action !== undefined) p.next_action = String(body.next_action);
    return { id: p.id } as unknown as T;
  }
  // ADR-013 D3: メモ（伝達）の追加。`about` は project の code でも id でも受け付ける
  // ——本物の Web バックエンド（`project.resolve`）と同じ挙動。
  if (path === "/tasks/note" && method === "POST") {
    const title = String(body.title || "").trim();
    if (!title) badRequest("title が必要です");
    let projectId: string | null = null;
    const about = (body.about as string) || "";
    if (about) {
      const p = projects.find((x) => x.id === about || x.code === about);
      if (!p) notFound(`project が見つかりません: ${about}`);
      projectId = p.id;
    }
    noteSeq += 1;
    const id = `N${noteSeq}`;
    notes.push({ id, title, body: (body.body as string) || "", project_id: projectId });
    return { id } as unknown as T;
  }
  if (path === "/tasks/check" && method === "GET") return computeCheck() as unknown as T;

  // ---------- kitchen ----------
  if (path === "/kitchen" && method === "GET") {
    const shoppingByAisle: Record<string, ShoppingItem[]> = {};
    for (const s of shopping) (shoppingByAisle[s.aisle] ||= []).push(s);
    const data: KitchenData = { available: true, pantry: [...pantry].sort((a, b) => (a.expires || "9999").localeCompare(b.expires || "9999")), shopping_by_aisle: shoppingByAisle, meals_recent: meals, taste };
    return data as unknown as T;
  }
  if (path === "/kitchen/pantry" && method === "POST") {
    pantrySeq += 1;
    const item: PantryItem = { id: pantrySeq, item: String(body.item || ""), qty: String(body.qty ?? "不明"), unit: String(body.unit || ""), expires: (body.expires as string) || null, place: (body.place as string) || null };
    if (!item.item.trim()) badRequest("item が必要です");
    pantry.push(item);
    return item as unknown as T;
  }
  if (path.match(/^\/kitchen\/pantry\/\d+\/use$/) && method === "POST") {
    const id = Number(path.split("/")[3]);
    const idx = pantry.findIndex((p) => p.id === id);
    if (idx < 0) notFound("pantry item が見つかりません");
    if (body.all) pantry.splice(idx, 1);
    else {
      const remaining = Number(pantry[idx].qty || 0) - Number(body.qty ?? 1);
      pantry[idx].qty = String(Math.max(0, remaining));
    }
    return { ok: true } as unknown as T;
  }
  if (path.match(/^\/kitchen\/pantry\/\d+$/) && method === "DELETE") {
    const id = Number(path.split("/")[3]);
    const idx = pantry.findIndex((p) => p.id === id);
    if (idx < 0) notFound("pantry item が見つかりません");
    pantry.splice(idx, 1);
    return { ok: true } as unknown as T;
  }
  if (path === "/kitchen/shopping" && method === "POST") {
    shoppingSeq += 1;
    const item: ShoppingItem = { id: shoppingSeq, item: String(body.item || ""), reason: (body.reason as string) || "", aisle: (body.aisle as string) || "その他" };
    if (!item.item.trim()) badRequest("item が必要です");
    shopping.push(item);
    return item as unknown as T;
  }
  if (path === "/kitchen/shopping/bought" && method === "POST") {
    // 実バックエンド（cmd_shopping_bought）は品目名のあいまい一致で消し込む。id ではない。
    const names = (body.items as string[]) || [];
    let removed = 0;
    for (const name of names) {
      const idx = shopping.findIndex((s) => s.item === name);
      if (idx >= 0) {
        shopping.splice(idx, 1);
        removed += 1;
      }
    }
    return { ok: true, removed } as unknown as T;
  }
  if (path === "/kitchen/meal" && method === "POST") {
    mealSeq += 1;
    const meal: Meal = { id: mealSeq, date: String(body.date || TODAY), slot: String(body.slot || ""), dish: String(body.dish || ""), ingredients: (body.ingredients as string) || "", planned: !!body.planned };
    if (!meal.dish.trim()) badRequest("dish が必要です");
    meals.unshift(meal);
    return meal as unknown as T;
  }

  // ---------- kitchen: レシピ帳（ADR-015 D3・D4・§6） ----------
  // `import`/`refine`/`estimate-nutrition` は R2・§6 D7（Python 担当が並行で作る口）だが、
  // 画面を作り切るため合成の応答を用意する（ADR-015 §3 の契約 JSON どおりの下書き・
  // 固定の推定値）。§6 D7「自動抽出を先に」: `mode` 省略時＝`auto` は URL のホスト名から
  // jsonld/アダプタ/汎用を作り分け、`mode:"claude"` は上限内に整った下書きを返す
  // （実際の `claude -p` は呼ばない——遅さを再現する意味は無い）。
  if (path === "/kitchen/recipes/import" && method === "POST") {
    const url = String(body.url || "").trim();
    const mode = String(body.mode || "auto");
    if (!url) badRequest("url が必須です");
    let site = "";
    try {
      site = new URL(url).hostname;
    } catch {
      badRequest(`url が不正です: ${url}`);
    }

    const baseIngredients = [{ name: "材料1", qty: "1", unit: "個", prep: "", group: "" }];
    const basePhases = [
      { id: "prep", title: "下ごしらえ" },
      { id: "cook", title: "作る" },
    ];

    if (mode === "claude") {
      // §6 D7の3「Claude で最初から抽出」: 上限に整えた見出しで返す（遅いが安定という
      // 前提を、画面側では「待たされない」形で表現する——mock なので待たせない）。
      const draft: RecipeBody = {
        title: "取り込んだレシピ（Claude 抽出）",
        source_url: url,
        source_site: site,
        hero_image: "https://picsum.photos/seed/claude-import/480/360",
        servings: 2,
        total_minutes: 15,
        ingredients: baseIngredients,
        tools: [],
        phases: basePhases,
        steps: [
          { index: 1, phase: "prep", title: "下ごしらえ", instruction: "材料を切る。", image: null, ingredients_used: [], timer_sec: null, completion: "manual", tips: [] },
          { index: 2, phase: "cook", title: "炒め合わせる", instruction: "強火で香りが立つまで炒め合わせる。", image: null, ingredients_used: [], timer_sec: null, completion: "manual", tips: [] },
        ],
      };
      const result: RecipeImportResult = { recipe: draft, method: "claude", warnings: [] };
      return result as unknown as T;
    }

    // mode: "auto"（既定。§6 D7の1）。
    if (site.includes("nadia")) {
      // §6 D8「完成画像が取れなかった理由を warnings に1行」の合成例
      // ——Nadia は工程の写真は取れても完成画像が取れないことがあった（主人の実測）。
      const draft: RecipeBody = {
        title: "取り込んだレシピ（Nadia）",
        source_url: url,
        source_site: site,
        hero_image: "",
        servings: 2,
        total_minutes: 15,
        ingredients: baseIngredients,
        tools: [],
        phases: basePhases,
        steps: [
          { index: 1, phase: "prep", title: "下ごしらえをする", instruction: "材料を切る。", image: "https://picsum.photos/seed/nadia-step1/320/240", ingredients_used: [], timer_sec: null, completion: "manual", tips: [] },
          { index: 2, phase: "cook", title: "調味料を合わせて炒める", instruction: "強火で香りが立つまで炒め合わせる。", image: "https://picsum.photos/seed/nadia-step2/320/240", ingredients_used: [], timer_sec: null, completion: "manual", tips: [] },
        ],
      };
      const result: RecipeImportResult = {
        recipe: draft,
        method: "adapter:nadia",
        warnings: ["完成画像が見つかりません（工程の写真は取得できました）", "工程2の見出しが13文字です（上限12文字）"],
        // §6 追補: Nadia のページに載っている栄養表示をそのまま持ってきた例
        // （`nutrition_source: "site"`＝出典の表示値。登録画面の栄養5つに前もって入る）。
        meta: { kcal: 685, protein_g: 20.5, fat_g: 35.2, carb_g: 66.5, salt_g: 2.8, nutrition_source: "site" },
      };
      return result as unknown as T;
    }
    if (site.includes("cookpad")) {
      const draft: RecipeBody = {
        title: "取り込んだレシピ（cookpad）",
        source_url: url,
        source_site: site,
        hero_image: "https://picsum.photos/seed/cookpad-hero/480/360",
        servings: 2,
        total_minutes: 15,
        ingredients: baseIngredients,
        tools: [],
        phases: basePhases,
        steps: [
          { index: 1, phase: "prep", title: "下ごしらえをする", instruction: "材料を切る。", image: null, ingredients_used: [], timer_sec: null, completion: "manual", tips: [] },
          { index: 2, phase: "cook", title: "炒める", instruction: "強火で炒め合わせる。", image: null, ingredients_used: [], timer_sec: null, completion: "manual", tips: [] },
        ],
      };
      const result: RecipeImportResult = { recipe: draft, method: "adapter:cookpad", warnings: [] };
      return result as unknown as T;
    }
    if (site.includes("json")) {
      // JSON-LD の `Recipe` が取れた前提の合成例（§6 D7の1の①）。
      const draft: RecipeBody = {
        title: "取り込んだレシピ（構造化データ）",
        source_url: url,
        source_site: site,
        hero_image: "https://picsum.photos/seed/jsonld-hero/480/360",
        servings: 2,
        total_minutes: 15,
        ingredients: baseIngredients,
        tools: [],
        phases: basePhases,
        steps: [
          { index: 1, phase: "prep", title: "下ごしらえ", instruction: "材料を切る。", image: null, ingredients_used: [], timer_sec: null, completion: "manual", tips: [] },
          { index: 2, phase: "cook", title: "作る", instruction: "工程どおりに作る。", image: null, ingredients_used: [], timer_sec: null, completion: "manual", tips: [] },
        ],
      };
      const result: RecipeImportResult = { recipe: draft, method: "jsonld", warnings: [] };
      return result as unknown as T;
    }
    // ③どちらも無ければ汎用（見出し・<ol>/<li> の推定）。§6 D7「サイト別の抽出は
    // 壊れる前提——汎用も薄ければ Claude を勧める帯を出す」を試せるよう、warnings を多めに
    // 合成する（画面側は generic かつ warnings が多いときに「Claude で整えることを
    // 勧めます」の一文を出す）。
    const draft: RecipeBody = {
      title: "取り込んだレシピ（下書き）",
      source_url: url,
      source_site: site,
      hero_image: "",
      servings: 2,
      total_minutes: 15,
      ingredients: baseIngredients,
      tools: [],
      phases: basePhases,
      steps: [
        { index: 1, phase: "prep", title: "下ごしらえをする", instruction: "材料を切る。", image: null, ingredients_used: [], timer_sec: null, completion: "manual", tips: [] },
        // ADR-015 D2の3「上限超えは2回目もそのまま返して画面で直させる」の合成例
        // ——見出しが13文字（上限12）。warnings と赤い残り字数の両方を合成データで試せる。
        { index: 2, phase: "cook", title: "調味料を合わせて炒める", instruction: "強火で香りが立つまで炒め合わせる。", image: null, ingredients_used: [], timer_sec: null, completion: "manual", tips: [] },
      ],
    };
    const result: RecipeImportResult = {
      recipe: draft,
      method: "generic",
      warnings: ["工程2の見出しが13文字です（上限12文字）", "完成画像が見つかりません", "材料の分量を読み取れていません"],
    };
    return result as unknown as T;
  }
  if (path === "/kitchen/recipes/refine" && method === "POST") {
    // §6 D7の2「Claude で整える」: 自動抽出の下書きを渡し、1動作1工程・≤12/≤60 に整える
    // （本文全部を渡すより短く速い、という前提。mock は機械的に切り詰めて代わりにする）。
    const input = (body.recipe || {}) as RecipeBody;
    const refined: RecipeBody = {
      ...input,
      steps: (input.steps || []).map((s) => ({
        ...s,
        title: s.title.length > 12 ? s.title.slice(0, 12) : s.title,
        instruction: s.instruction.length > 60 ? s.instruction.slice(0, 60) : s.instruction,
      })),
    };
    const result: RecipeImportResult = { recipe: refined, method: "claude", warnings: [] };
    return result as unknown as T;
  }
  if (path.match(/^\/kitchen\/recipes\/\d+\/estimate-nutrition$/) && method === "POST") {
    const id = Number(path.split("/")[3]);
    const r = findRecipe(id);
    // ADR-015 D2の5: 押したときだけ推定し、`nutrition_source='estimated'` で入れる
    // （＝この口自体が「うちの値」へ書き込む。下書きを返すだけの import とは違う）。
    r.meta.kcal = 550;
    r.meta.protein_g = 20;
    r.meta.fat_g = 18;
    r.meta.carb_g = 65;
    r.meta.salt_g = 2.1;
    r.meta.nutrition_source = "estimated";
    return { meta: r.meta } as unknown as T;
  }
  if (path === "/kitchen/recipes/facets" && method === "GET") {
    return computeRecipeFacets() as unknown as T;
  }
  if (path.startsWith("/kitchen/recipes") && method === "GET" && !path.match(/^\/kitchen\/recipes\/\d+$/)) {
    const params = new URLSearchParams(path.split("?")[1] || "");
    const q = (params.get("q") || "").toLowerCase();
    const tag = params.get("tag");
    const favoriteParam = params.get("favorite");
    const category = params.get("category");
    const mainIngredient = params.get("main_ingredient");
    const cuisine = params.get("cuisine");
    const sort = params.get("sort") || "recent";
    const items: RecipeListItem[] = recipes
      .filter((r) => !archivedRecipeIds.has(r.id))
      .filter((r) => !q || r.title.toLowerCase().includes(q) || r.ingredients.some((ing) => ing.name.toLowerCase().includes(q)))
      .filter((r) => !tag || r.meta.tags.includes(tag))
      .filter((r) => favoriteParam == null || r.meta.favorite === (favoriteParam === "1" || favoriteParam === "true"))
      .filter((r) => !category || r.meta.category === category)
      .filter((r) => !mainIngredient || r.meta.main_ingredient === mainIngredient)
      .filter((r) => !cuisine || r.meta.cuisine === cuisine)
      .map(recipeToListItem);
    return { items: sortRecipeItems(items, sort) } as unknown as T;
  }
  if (path.match(/^\/kitchen\/recipes\/\d+$/) && method === "GET") {
    const id = Number(path.split("/")[3]);
    return findRecipe(id) as unknown as T;
  }
  if (path === "/kitchen/recipes" && method === "POST") {
    const v = validateRecipeBody(body as unknown as RecipeBody);
    recipeSeq += 1;
    const recipe: Recipe = {
      id: recipeSeq,
      ...v,
      meta: {
        kcal: null, protein_g: null, fat_g: null, carb_g: null, salt_g: null,
        nutrition_source: "", tags: [], rating: null, memo: "", favorite: false,
        times_cooked: 0, last_cooked_at: null,
        category: "", main_ingredient: "", cuisine: "",
      },
    };
    recipes.push(recipe);
    touchRecipeUpdatedAt(recipe.id);
    return recipe as unknown as T;
  }
  if (path.match(/^\/kitchen\/recipes\/\d+$/) && method === "PUT") {
    const id = Number(path.split("/")[3]);
    const r = findRecipe(id);
    const v = validateRecipeBody(body as unknown as RecipeBody);
    Object.assign(r, v);
    touchRecipeUpdatedAt(id);
    return r as unknown as T;
  }
  if (path.match(/^\/kitchen\/recipes\/\d+\/meta$/) && method === "PUT") {
    const id = Number(path.split("/")[3]);
    const r = findRecipe(id);
    const numericGiven = ["kcal", "protein_g", "fat_g", "carb_g", "salt_g"].some((k) => body[k] !== undefined);
    if (numericGiven && body.nutrition_source === undefined) r.meta.nutrition_source = "manual";
    for (const k of ["kcal", "protein_g", "fat_g", "carb_g", "salt_g", "nutrition_source", "memo"] as const) {
      if (body[k] !== undefined) (r.meta as unknown as Record<string, unknown>)[k] = body[k];
    }
    if (body.rating !== undefined) r.meta.rating = body.rating as number | null;
    if (body.tags !== undefined) r.meta.tags = (body.tags as string[]) || [];
    if (body.favorite !== undefined) r.meta.favorite = !!body.favorite;
    // §6 D9: 分類3軸は手で直せる（常に渡された値で置き換える——栄養と違い partial の
    // 気遣いは要らない。select は常に何かの値を持つ欄なので「触っていない」判定が無い）。
    if (body.category !== undefined) r.meta.category = String(body.category || "");
    if (body.main_ingredient !== undefined) r.meta.main_ingredient = String(body.main_ingredient || "");
    if (body.cuisine !== undefined) r.meta.cuisine = String(body.cuisine || "");
    return r as unknown as T;
  }
  if (path.match(/^\/kitchen\/recipes\/\d+\/archive$/) && method === "POST") {
    const id = Number(path.split("/")[3]);
    findRecipe(id);
    archivedRecipeIds.add(id);
    return { id } as unknown as T;
  }

  // ---------- kitchen: 献立のおすすめ（ADR-018 D3） ----------
  // 採点の本体はバックエンド（`staff/chef/menu.py`）。ここは**画面を組むための形**だけを
  // 返す合成——kcal の大きい順に並べ、理由は符牒を2つ添える（画面が符牒→文の変換を
  // 正しくできているかを `?mock=1` で目で確かめられるようにするため）。
  if (path.startsWith("/kitchen/menu/recommend") && method === "GET") {
    const params = new URLSearchParams(path.includes("?") ? path.slice(path.indexOf("?")) : "");
    const mainId = params.get("main_recipe_id");
    const moodText = params.get("mood") || "";
    const excluded = new Set((params.get("exclude") || "").split(",").filter(Boolean).map(Number));
    const withNutrition = recipes.filter(
      (r) => !archivedRecipeIds.has(r.id) && r.meta.kcal != null && r.meta.salt_g != null && !excluded.has(r.id)
    );
    const mainRecipe = mainId ? findRecipe(Number(mainId)) : null;
    const pick = (category: string): MenuCandidate[] =>
      withNutrition
        .filter((r) => r.meta.category === category && r.id !== mainRecipe?.id)
        .slice(0, 5)
        .map((r, i) => ({
          recipe_id: r.id,
          title: r.title,
          hero_image: r.hero_image || "",
          total_minutes: r.total_minutes,
          category: r.meta.category,
          main_ingredient: r.meta.main_ingredient,
          cuisine: r.meta.cuisine,
          dish_type: "",
          score: Number((2.5 - i * 0.4).toFixed(3)),
          reasons: [
            { code: "main_ingredient_differs", params: { main: "肉", cand: r.meta.main_ingredient || "野菜" } },
            { code: "keeps_salt_low", params: { value: r.meta.salt_g as number } },
          ] as MenuReason[],
          nutrition: {
            kcal: r.meta.kcal as number,
            protein_g: (r.meta.protein_g ?? 0) as number,
            fat_g: (r.meta.fat_g ?? 0) as number,
            carb_g: (r.meta.carb_g ?? 0) as number,
            salt_g: r.meta.salt_g as number,
          },
        }));
    const slots = { main: mainRecipe ? [] : pick("主菜"), side: pick("副菜"), soup: pick("汁物") };
    const comboSources = [
      ...(mainRecipe
        ? [{ recipe: mainRecipe, kind: "main" as const }]
        : slots.main[0]
          ? [{ recipe: findRecipe(slots.main[0].recipe_id), kind: "main" as const }]
          : []),
      ...(slots.side[0] ? [{ recipe: findRecipe(slots.side[0].recipe_id), kind: "side" as const }] : []),
      ...(slots.soup[0] ? [{ recipe: findRecipe(slots.soup[0].recipe_id), kind: "soup" as const }] : []),
    ];
    const total = { kcal: 0, protein_g: 0, fat_g: 0, carb_g: 0, salt_g: 0 };
    for (const { recipe } of comboSources) {
      total.kcal += recipe.meta.kcal ?? 0;
      total.protein_g += recipe.meta.protein_g ?? 0;
      total.fat_g += recipe.meta.fat_g ?? 0;
      total.carb_g += recipe.meta.carb_g ?? 0;
      total.salt_g += recipe.meta.salt_g ?? 0;
    }
    const band: Record<string, { min: number; max: number }> = {
      kcal: { min: 600, max: 900 }, protein_g: { min: 20, max: 35 }, fat_g: { min: 15, max: 30 },
      carb_g: { min: 75, max: 130 }, salt_g: { min: 0, max: 2.5 },
    };
    const bandCheck: Record<string, { total: number; min: number; max: number; status: MenuBandStatus }> = {};
    for (const [key, range] of Object.entries(band)) {
      const value = Number((total[key as keyof typeof total] as number).toFixed(1));
      bandCheck[key] = {
        total: value, min: range.min, max: range.max,
        status: value < range.min ? "under" : value > range.max ? "over" : "in",
      };
    }
    const payload: MenuRecommendation = {
      slot: params.get("slot") || "dinner",
      people: Number(params.get("people") || 2),
      band,
      applied_mood: {
        text: moodText,
        matched: ["さっぱり", "がっつり", "早く", "温かい", "野菜", "魚"].filter((m) => moodText.includes(m)),
        conditions: {},
      },
      main: mainRecipe
        ? {
            recipe_id: mainRecipe.id, title: mainRecipe.title, hero_image: mainRecipe.hero_image || "",
            total_minutes: mainRecipe.total_minutes, category: mainRecipe.meta.category,
            main_ingredient: mainRecipe.meta.main_ingredient, cuisine: mainRecipe.meta.cuisine,
            dish_type: "", slot_kind: "main",
            nutrition: mainRecipe.meta.kcal == null ? null : {
              kcal: mainRecipe.meta.kcal, protein_g: mainRecipe.meta.protein_g ?? 0,
              fat_g: mainRecipe.meta.fat_g ?? 0, carb_g: mainRecipe.meta.carb_g ?? 0,
              salt_g: mainRecipe.meta.salt_g ?? 0,
            },
          }
        : null,
      slots,
      combo: {
        recipe_ids: comboSources.map(({ recipe }) => recipe.id),
        items: comboSources.map(({ recipe, kind }) => ({
          recipe_id: recipe.id, title: recipe.title, hero_image: recipe.hero_image || "",
          total_minutes: recipe.total_minutes, category: recipe.meta.category,
          main_ingredient: recipe.meta.main_ingredient, cuisine: recipe.meta.cuisine,
          dish_type: "", slot_kind: kind,
        })),
        total: {
          kcal: Number(total.kcal.toFixed(1)), protein_g: Number(total.protein_g.toFixed(1)),
          fat_g: Number(total.fat_g.toFixed(1)), carb_g: Number(total.carb_g.toFixed(1)),
          salt_g: Number(total.salt_g.toFixed(1)),
        },
        band_check: bandCheck,
      },
      excluded_no_nutrition: recipes.filter((r) => !archivedRecipeIds.has(r.id) && r.meta.kcal == null).length,
      viewing_user_id: "master",
    };
    return payload as unknown as T;
  }
  if (path === "/kitchen/menu/plan" && method === "POST") {
    const ids = (body.recipe_ids as number[]) || [];
    if (!ids.length) badRequest("recipe_ids が空です");
    for (const id of ids) findRecipe(id);
    return {
      date: String(body.date || ""), slot: String(body.slot || "dinner"), planned: true,
      items: ids.map((id, i) => ({ id: i + 1, recipe_id: id, dish: findRecipe(id).title })),
    } as unknown as T;
  }

  // ---------- kitchen: 動画リスト（ADR-016 D3） ----------
  if (path === "/kitchen/media" && method === "GET") {
    return { items: [...mediaItems], updated_at: mediaItems.length ? mediaUpdatedAt : null } as unknown as T;
  }
  if (path === "/kitchen/media" && method === "POST") {
    const url = String(body.url || "").trim();
    const videoId = mockExtractVideoId(url);
    if (!videoId) badRequest(`YouTube の動画 URL として読めません: ${url}`);
    if (mediaItems.some((m) => m.video_id === videoId)) conflict(`この動画は既に登録されています: ${videoId}`);
    mediaSeq += 1;
    const item: MediaItem = {
      id: `media-${mediaSeq}`,
      title: `動画 ${videoId}`,
      video_id: videoId as string,
      url,
      thumbnail_url: `https://i.ytimg.com/vi/${videoId}/hqdefault.jpg`,
      author: "",
      memo: String(body.memo || ""),
      sort_order: mediaItems.length + 1,
    };
    mediaItems.push(item);
    mediaUpdatedAt = new Date().toISOString().slice(0, 19);
    return item as unknown as T;
  }
  if (path === "/kitchen/media/reorder" && method === "POST") {
    const ids = (body.ids as string[]) || [];
    for (const id of ids) {
      if (!mediaItems.some((m) => m.id === id)) notFound(`動画が見つかりません: ${id}`);
    }
    const ordered = [...ids, ...mediaItems.filter((m) => !ids.includes(m.id)).map((m) => m.id)];
    mediaItems.sort((a, b) => ordered.indexOf(a.id) - ordered.indexOf(b.id));
    mediaItems.forEach((m, i) => (m.sort_order = i + 1));
    mediaUpdatedAt = new Date().toISOString().slice(0, 19);
    return { items: [...mediaItems] } as unknown as T;
  }
  if (path.match(/^\/kitchen\/media\/[^/]+$/) && method === "PATCH") {
    const id = path.split("/")[3];
    const item = mediaItems.find((m) => m.id === id);
    if (!item) notFound(`動画が見つかりません: ${id}`);
    if (body.title !== undefined) item.title = String(body.title || "").trim() || item.video_id;
    if (body.memo !== undefined) item.memo = String(body.memo || "").trim();
    mediaUpdatedAt = new Date().toISOString().slice(0, 19);
    return item as unknown as T;
  }
  if (path.match(/^\/kitchen\/media\/[^/]+$/) && method === "DELETE") {
    const id = path.split("/")[3];
    const index = mediaItems.findIndex((m) => m.id === id);
    if (index < 0) notFound(`動画が見つかりません: ${id}`);
    const [removed] = mediaItems.splice(index, 1);
    mediaItems.forEach((m, i) => (m.sort_order = i + 1));
    return removed as unknown as T;
  }

  // ---------- house ----------
  if (path === "/house" && method === "GET") {
    const data: HouseData = {
      available: true,
      today: {
        "当番": chores.map((c) => ({ id: c.id, name: c.name, what: c.area, overdue_days: c.overdue_days })),
        "少ない消耗品": supplies
          .filter((s) => s.qty <= s.threshold)
          .map((s) => ({ item: s.item, qty: s.qty, threshold: s.threshold })),
      },
    };
    return data as unknown as T;
  }
  if (path.match(/^\/house\/chore\/\d+\/done$/) && method === "POST") {
    const id = Number(path.split("/")[3]);
    const c = chores.find((x) => x.id === id);
    if (!c) notFound("chore が見つかりません");
    c.overdue_days = 0;
    return { ok: true } as unknown as T;
  }
  if (path.match(/^\/house\/supply\/.+$/) && method === "POST") {
    // `/house/supply/{item}`（item 名。id ではない。src/manor/web/api_v1/house.py 参照）
    const item = decodeURIComponent(path.slice("/house/supply/".length));
    const s = supplies.find((x) => x.item === item);
    if (!s) notFound("supply が見つかりません");
    s.qty = Number(body.qty ?? s.qty);
    return { item, qty: s.qty } as unknown as T;
  }
  if (path === "/house/chore" && method === "POST") {
    choreSeq += 1;
    const c = { id: choreSeq, name: String(body.name || ""), every: Number(body.every ?? 7), area: (body.area as string) || "", overdue_days: null };
    if (!c.name.trim()) badRequest("name が必要です");
    chores.push(c);
    return c as unknown as T;
  }

  // ---------- money ----------
  if (path === "/money" && method === "GET") {
    const spentByCat: Record<string, number> = {};
    for (const e of expenses) if (e.kind === "expense") spentByCat[e.category] = (spentByCat[e.category] || 0) + e.amount;
    const cats = Array.from(new Set([...Object.keys(spentByCat), ...Object.keys(budgets)]));
    const data: MoneyData = {
      available: true,
      month: {
        expenses: cats.map((c) => {
          const spent = spentByCat[c] || 0;
          const budget = budgets[c] ?? null;
          const diff = budget != null ? budget - spent : null;
          return { category: c, spent, budget, diff, over: budget != null ? spent > budget : false };
        }),
      },
      due: recurring,
      recent_expenses: [...expenses].sort((a, b) => b.date.localeCompare(a.date)),
    };
    return data as unknown as T;
  }
  if (path.startsWith("/money/summary") && method === "GET") {
    return (await mockApi<MoneyData>("/money")) as unknown as T;
  }
  if (path === "/money/expense" && method === "POST") {
    expenseSeq += 1;
    const e: MoneyExpense = { id: expenseSeq, date: String(body.date || TODAY), category: String(body.category || ""), memo: (body.memo as string) || "", amount: Number(body.amount ?? 0), kind: body.income ? "income" : "expense" };
    if (!e.category.trim()) badRequest("category が必要です");
    if (!(e.amount > 0)) badRequest("amount は正の数である必要があります");
    expenses.unshift(e);
    return e as unknown as T;
  }
  if (path.match(/^\/money\/recurring\/\d+\/paid$/) && method === "POST") {
    const id = Number(path.split("/")[3]);
    const r = recurring.find((x) => x.id === id);
    if (!r) notFound("recurring が見つかりません");
    r.overdue_days = -30;
    return { ok: true } as unknown as T;
  }
  if (path.match(/^\/money\/budget\/.+$/) && method === "PUT") {
    const category = decodeURIComponent(path.split("/")[3]);
    budgets[category] = Number(body.limit ?? 0);
    return { category, limit: budgets[category] } as unknown as T;
  }

  // ---------- secretary ----------
  if (path === "/secretary" && method === "GET") {
    const data: SecretaryData = {
      available: true,
      agenda: [
        ...reminders.filter((r) => !r.done_at).map((r) => ({ date: r.on_date, kind: "控え", title: r.text, overdue: r.on_date < TODAY })),
        ...events.map((e) => ({ date: e.start.slice(0, 10), kind: "予定", title: e.title, overdue: false })),
      ],
      reminders_open: reminders.filter((r) => !r.done_at),
      inbox_unrouted: inbox,
    };
    return data as unknown as T;
  }
  if (path.startsWith("/secretary/agenda") && method === "GET") {
    return ((await mockApi<SecretaryData>("/secretary")).agenda || []) as unknown as T;
  }
  if (path === "/secretary/reminder" && method === "POST") {
    reminderSeq += 1;
    const r = { id: reminderSeq, text: String(body.text || ""), on_date: String(body.on || TODAY), at_time: (body.at as string) || null, done_at: null };
    if (!r.text.trim()) badRequest("text が必要です");
    reminders.push(r);
    return r as unknown as T;
  }
  if (path.match(/^\/secretary\/reminder\/\d+\/done$/) && method === "POST") {
    const id = Number(path.split("/")[3]);
    const r = reminders.find((x) => x.id === id);
    if (!r) notFound("reminder が見つかりません");
    r.done_at = new Date().toISOString();
    return { ok: true } as unknown as T;
  }
  if (path === "/secretary/event" && method === "POST") {
    const ev = { id: events.length + 1, title: String(body.title || ""), start: String(body.start || TODAY), end: (body.end as string) || null, place: (body.place as string) || null };
    if (!ev.title.trim()) badRequest("title が必要です");
    events.push(ev);
    return ev as unknown as T;
  }

  // ---------- rules ----------
  if (path.startsWith("/rules") && method === "GET") {
    const params = new URLSearchParams(path.split("?")[1] || "");
    const tag = params.get("tag");
    const all = params.get("all") === "true" || params.get("all") === "1";
    let out = rules.filter((r) => all || !r.archived_at);
    if (tag) out = out.filter((r) => r.tags.split(/[,、]/).map((s) => s.trim()).includes(tag));
    return out as unknown as T;
  }
  if (path === "/rules" && method === "POST") {
    ruleSeq += 1;
    const r: Rule = {
      id: ruleSeq,
      title: String(body.title || ""),
      body: String(body.body || ""),
      scope: (body.scope as Rule["scope"]) || "family",
      tags: String(body.tags || ""),
      effective_from: (body.effective_from as string) || null,
      effective_to: (body.effective_to as string) || null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      archived_at: null,
    };
    if (!r.title.trim()) badRequest("title が必要です");
    rules.push(r);
    return r as unknown as T;
  }
  if (path.match(/^\/rules\/\d+$/) && method === "PUT") {
    const id = Number(path.split("/")[2]);
    const r = rules.find((x) => x.id === id);
    if (!r) notFound("rule が見つかりません");
    Object.assign(r, body, { updated_at: new Date().toISOString() });
    return r as unknown as T;
  }
  if (path.match(/^\/rules\/\d+$/) && method === "DELETE") {
    const id = Number(path.split("/")[2]);
    const r = rules.find((x) => x.id === id);
    if (!r) notFound("rule が見つかりません");
    r.archived_at = new Date().toISOString();
    return { ok: true } as unknown as T;
  }

  // ---------- night ----------
  if (path === "/night/reports" && method === "GET") return { dates: [nightDate] } as unknown as T;
  if (path.startsWith("/night/reports/") && method === "GET") {
    const date = decodeURIComponent(path.slice("/night/reports/".length));
    if (date !== nightDate) notFound(`夜勤の報告が見つかりません: ${date}`);
    const parsed = {
      ok: true,
      title: `夜勤報告 ${nightDate}`,
      summary: ["昨夜の作業まとめ。"],
      tasks: [
        { number: "N1", title: "バックアップの確認", state: "done" as const, fields: [{ label: "やったこと", text: "バックアップを確認した。" }] },
        { number: "N2", title: "ログの整理", state: "hold" as const, fields: [{ label: "どこまで", text: "半分まで整理した。" }] },
      ],
    };
    const res: NightReport = { date, text: nightText, parsed };
    return res as unknown as T;
  }
  if (path === "/night/status" && method === "GET") {
    const s: NightStatus = { ok: true, last_run_at: new Date().toISOString(), detail: "OK" };
    return s as unknown as T;
  }

  // ---------- imports ----------
  if (path === "/imports/money/preview" && method === "POST") {
    // 実バックエンド（ImportResult.to_dict）の形: rows と duplicates は別配列（重複は
    // rows から除かれる）。unreadable[].raw は元の CSV 行（列名→値の辞書）。
    const rows: ImportPreview = {
      rows: [
        { line: 3, date: daysFromToday(-1), amount: 1200, category: "交通費", memo: "電車", kind: "expense", import_hash: "mock1" },
        { line: 4, date: daysFromToday(-2), amount: 500, category: "雑費", memo: "", kind: "expense", import_hash: "mock2" },
      ],
      duplicates: [{ line: 2, date: TODAY, amount: 3200, category: "食費", memo: "スーパー", kind: "expense", import_hash: "mock0" }],
      unreadable: [{ line: 5, raw: { 日付: "???", 金額: "", カテゴリ: "", 内容: "" }, reason: "日付を読めない" }],
      total: 4,
    };
    return rows as unknown as T;
  }
  if (path === "/imports/money/commit" && method === "POST") {
    const res: ImportCommitResult = { inserted: 2, skipped: 1 };
    return res as unknown as T;
  }

  // ---------- runs（ADR-006 §3 D11・§6）----------
  if (path.startsWith("/runs/stats") && method === "GET") {
    const byKind = ["night", "behavior", "gate"] as const;
    const stats: RunStatsData = {
      available: true,
      by_kind: byKind.map((k) => {
        const rowsOfKind = runs.filter((r) => r.kind === k);
        const count = rowsOfKind.length;
        const costUsd = rowsOfKind.reduce((a, r) => a + (r.cost_usd || 0), 0);
        const failed = rowsOfKind.filter((r) => r.exit_reason !== "done").length;
        const durations = rowsOfKind
          .filter((r) => r.ended_at)
          .map((r) => (new Date(r.ended_at!).getTime() - new Date(r.started_at).getTime()) / 1000);
        const avgSeconds = durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : null;
        return {
          kind: k,
          count,
          cost_usd: costUsd,
          avg_seconds: avgSeconds,
          failed,
          input_tokens: rowsOfKind.reduce((a, r) => a + (r.input_tokens || 0), 0),
          output_tokens: rowsOfKind.reduce((a, r) => a + (r.output_tokens || 0), 0),
        };
      }),
      total_cost_usd: runs.reduce((a, r) => a + (r.cost_usd || 0), 0),
    };
    return stats as unknown as T;
  }
  if (path.startsWith("/runs") && method === "GET") {
    const params = new URLSearchParams(path.split("?")[1] || "");
    const kind = params.get("kind");
    const out = kind ? runs.filter((r) => r.kind === kind) : runs;
    const data: RunsData = { available: true, runs: [...out].sort((a, b) => b.started_at.localeCompare(a.started_at)) };
    return data as unknown as T;
  }

  // ---------- dashboard（ADR-011 D2。新しい集計をしない——board/runs/night の合成データを並べ替えるだけ）----------
  if (path === "/dashboard" && method === "GET") {
    const board = computeBoard();
    const openDecisions = decisions.filter((d) => d.status === "open");
    const dueTodayN = board.tasks.filter((t) => t.due === TODAY && !["done", "withdrawn"].includes(t.status)).length;
    const doneWeekN = board.tasks.filter((t) => t.status === "done").length;
    const actionNeeded = board.counts.pending + board.counts.blocked_ready;
    const nightRun = runs.find((r) => r.kind === "night") || null;
    const upcoming = [...board.milestones]
      .filter((m) => m.days_left == null || m.days_left >= 0)
      .map((m) => ({
        kind: "milestone" as const,
        id: m.id,
        title: m.title,
        date: m.date,
        approximate: m.approximate,
        days_left: m.days_left,
      }))
      .sort((a, b) => a.date.localeCompare(b.date));
    const attention = openDecisions.map((d) => ({
      id: d.id,
      title: d.title,
      days: d.days,
      risk: d.risk ?? null,
      stale: d.stale,
    }));
    const runs24 = [...runs].sort((a, b) => b.started_at.localeCompare(a.started_at));
    const byKind: Record<string, { kind: string; count: number; cost_usd: number; failed: number }> = {};
    for (const r of runs) {
      const b = (byKind[r.kind] ||= { kind: r.kind, count: 0, cost_usd: 0, failed: 0 });
      b.count += 1;
      b.cost_usd += r.cost_usd || 0;
      if (r.exit_reason !== "done") b.failed += 1;
    }
    const mostActive = Object.values(byKind)
      .map((b) => ({ kind: b.kind, count: b.count, cost_usd: b.cost_usd, avg_seconds: null, fail_rate: b.count ? b.failed / b.count : 0 }))
      .sort((a, b) => b.count - a.count);
    const totalCount = runs.length;
    const totalFailed = runs.filter((r) => r.exit_reason !== "done").length;
    const totalCost = runs.reduce((a, r) => a + (r.cost_usd || 0), 0);
    // 管制塔の3枚（2026-09-06）。demo でも「家で何が起きているか」が見えるよう、
    // 既にある board の配列から組む（新しい合成データを増やさない）。
    const needsYou = board.pending.slice(0, 3).map((d) => ({
      id: d.id,
      title: d.title,
      recommendation: d.tasks?.[0]?.recommendation ?? "",
      evidence: d.evidence ?? "",
      risk: d.risk ?? "",
      days: d.days ?? null,
      project_id: d.project_id ?? "",
    }));
    const doingByOwner: Record<string, { id: string; title: string; project_id: string }[]> = {};
    for (const t of board.tasks) {
      if (t.status !== "doing" || t.owner === "master") continue;
      (doingByOwner[t.owner || "butler"] ||= []).push({
        id: t.id,
        title: t.title,
        project_id: t.project_id ?? "",
      });
    }
    const data: DashboardData = {
      today: TODAY,
      greeting: { time_of_day: "morning", name: "あなた" },
      needs_you: needsYou,
      working: Object.entries(doingByOwner)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([owner, items]) => ({ owner, tasks: items.slice(0, 3), more: Math.max(0, items.length - 3) })),
      due_today_list: board.tasks
        .filter((t) => t.due === TODAY && !["done", "withdrawn"].includes(t.status))
        .slice(0, 6)
        .map((t) => ({ id: t.id, title: t.title, owner: t.owner ?? "", project_id: t.project_id ?? "" })),
      recent: board.tasks
        .filter((t) => t.status === "done")
        .sort((a, b) => String(b.done_at ?? "").localeCompare(String(a.done_at ?? "")))
        .slice(0, 5)
        .map((t) => ({ id: t.id, title: t.title, owner: t.owner ?? "", at: t.done_at ?? "" })),
      status: {
        ok: actionNeeded === 0,
        action_needed: actionNeeded,
        open_decisions: board.counts.pending,
        blocked_ready: board.counts.blocked_ready,
      },
      counts: {
        pending_decisions: board.counts.pending,
        doing_butler: board.counts.doing_butler,
        due_today: dueTodayN,
        done_this_week: doneWeekN,
      },
      night: {
        available: !!nightRun,
        status: nightRun ? (nightRun.exit_reason === "done" ? "done" : "failed") : null,
        started_at: nightRun?.started_at ?? null,
        ended_at: nightRun?.ended_at ?? null,
      },
      upcoming,
      attention,
      runs_24h: { available: true, runs: runs24 },
      most_active: { available: true, by_kind: mostActive },
      usage_cost: {
        available: true,
        count: totalCount,
        failed: totalFailed,
        success_rate: totalCount ? (totalCount - totalFailed) / totalCount : null,
        cost_usd: totalCost,
        cost_measured: totalCount,
      },
    };
    return data as unknown as T;
  }

  // ---------- agents（ADR-011 D3）----------
  if (path === "/agents" && method === "GET") {
    const agentSummary: Record<string, string> = {
      butler: "主人の判断待ちとタスク全体の采配、部下への委譲を担います。",
      chef: "在庫・食事の記録・買い物リスト・好みを預かり、献立の提案と記録を行います。",
      housekeeper: "家の中の当番・消耗品の残量・設備の手入れ周期・ゴミの日を預かります。",
      steward: "支出の記録・定期支払いの期日管理・予算との差を扱います（支払いの実行はしません）。",
      secretary: "予定・控え・受け渡し置き場（inbox）の仕分けと、相対日付の解決を担います。",
      qa: "作ったものを主人に渡す前に検めます。直すのではなく、見つけて伝えます。",
      auditor: "①層（規則・道具の定義）の肥大・矛盾を月に一度、外から点検します。",
    };
    const agentPage: Record<string, string | null> = {
      butler: "tasks",
      chef: "kitchen",
      housekeeper: "house",
      steward: "money",
      secretary: "secretary",
      qa: null,
      auditor: null,
    };
    const metaBody = await mockApi<Meta>("/meta");
    const enabledModules = new Set(metaBody.modules.filter((m) => m.enabled).map((m) => m.id));
    const out: AgentCard[] = Object.keys(FACE_AGENT_LABELS).map((agent) => {
      const page = agentPage[agent] ?? null;
      const enabled = page ? enabledModules.has(page) : true;
      return {
        id: agent,
        label: FACE_AGENT_LABELS[agent],
        role: FACE_AGENT_LABELS[agent],
        summary: agentSummary[agent] || "",
        page,
        has_model: faceModelsStore[agent].hasModel,
        enabled,
      };
    });
    return out as unknown as T;
  }

  // ---------- setup（ADR-007 D4）----------
  if (path === "/setup" && method === "GET") {
    const info: SetupInfo = {
      done: setupDone,
      completed_at: setupCompletedAt,
      profile: { ...profileStore },
      purposes: PURPOSES,
      presets: PRESETS,
      task_classes: nonHgTaskClasses(),
      money_apps: MONEY_APPS,
    };
    return info as unknown as T;
  }
  if (path === "/setup" && method === "POST") {
    // ADR-007 §6 D9: 呼び名が空なら「ご主人様」（もう必須ではない）。
    const callname = String(body.callname || "").trim() || "ご主人様";
    const butlerName = String(body.butler_name || "執事").trim() || "執事";
    const purposes = Array.isArray(body.purposes) ? (body.purposes as string[]) : [];
    const validPurposeIds = new Set(PURPOSES.map((p) => p.id));
    for (const p of purposes) if (!validPurposeIds.has(p)) badRequest(`用途: 不明な id です（${p}）`);
    const note = String(body.note || "");
    const projectsIn = Array.isArray(body.projects)
      ? (body.projects as { code: string; name: string; due?: string; preset?: string }[])
      : [];
    const tasksIn = Array.isArray(body.tasks)
      ? (body.tasks as { title: string; project_code?: string; cls?: string; kind?: string; due?: string }[])
      : [];
    const kitchenIn = (body.kitchen || undefined) as
      | { household_size?: number; allergies?: string; dislikes?: string }
      | undefined;
    const moneyIn = (body.money || undefined) as { app?: string; currency?: string } | undefined;
    if (moneyIn?.app && !MONEY_APPS.some((a) => a.id === moneyIn.app)) {
      badRequest(`家計簿アプリ: 不明な id です（${moneyIn.app}）`);
    }

    // apply_setup と同じく、途中で1つでも検証が落ちれば何も書かない（先に全部検証する）。
    const seenCodes = new Set<string>();
    for (const p of projectsIn) {
      if (!p.name || !p.name.trim()) badRequest("プロジェクト名: 必須です");
      if (!p.code || !/^[a-z0-9][a-z0-9-]*$/.test(p.code)) badRequest(`プロジェクト記号: 不正です（${p.code}）`);
      if (seenCodes.has(p.code)) badRequest(`プロジェクト記号: 重複しています（${p.code}）`);
      if (projects.some((existing) => existing.code === p.code)) badRequest(`プロジェクト記号: 既に使われています（${p.code}）`);
      seenCodes.add(p.code);
    }
    // ADR-010 D1: cls はもうウィザードから送られない（省略時はサーバー既定 general）。
    // 送られてきた場合だけ検証する（執事の CLI 起票など、他経路との整合のため）。
    const validClassIds = new Set(nonHgTaskClasses().map((c) => c.id));
    // ADR-010 D2: kind は任意。送られてきたら非アーカイブの語彙内かだけ確かめる。
    const validKindIds = new Set(nonArchivedTaskKinds().map((k) => k.id));
    for (const t of tasksIn) {
      if (!t.title || !t.title.trim()) badRequest("タスク題名: 必須です");
      if (t.cls && !validClassIds.has(t.cls)) badRequest(`行動クラス: 不明です（${t.cls}）`);
      if (t.kind && !validKindIds.has(t.kind)) badRequest(`タスクの種類: 不明です（${t.kind}）`);
      if (t.project_code && !seenCodes.has(t.project_code) && !projects.some((pp) => pp.code === t.project_code)) {
        badRequest(`所属プロジェクト: 見つかりません（${t.project_code}）`);
      }
    }

    profileStore["master.callname"] = callname;
    profileStore["butler.callname"] = butlerName;
    profileStore["purposes"] = JSON.stringify(purposes);
    profileStore["purposes.note"] = note;
    if (moneyIn) {
      profileStore["money.app"] = moneyIn.app || "none";
      profileStore["money.currency"] = moneyIn.currency || "JPY";
    }
    // 台所の答えは profile に持たず chef_taste（ここでは taste 配列）へ（D8）。
    if (kitchenIn) {
      if (kitchenIn.household_size != null) taste.push({ key: "人数", value: String(kitchenIn.household_size) });
      if (kitchenIn.allergies) taste.push({ key: "アレルギー", value: kitchenIn.allergies });
      if (kitchenIn.dislikes) taste.push({ key: "苦手", value: kitchenIn.dislikes });
    }

    const createdProjects: string[] = [];
    for (const p of projectsIn) {
      const id = p.code.toUpperCase();
      projects.push({
        id,
        code: p.code,
        title: p.name,
        kind: null,
        priority: projects.length + 1,
        preset: (p.preset as Project["preset"]) || "standard",
        status: "active",
        next_action: null,
        due: p.due || null,
        days_left: null,
        interest: { nearest_date: p.due || null, doing: 0, last_event_at: null, rank: projects.length + 1 },
      });
      createdProjects.push(id);
    }
    const createdTasks: string[] = [];
    for (const t of tasksIn) {
      taskSeq += 1;
      const id = `T${taskSeq}`;
      const projId = t.project_code ? projects.find((pp) => pp.code === t.project_code)?.id || null : null;
      // ADR-010 D1: cls 省略時はサーバー既定（general）。kind は検証のみ（Task 型に
      // まだ列が無い。board への反映は担当Aの領域——ADR-010 §4 の web/setup 試験の範囲外）。
      const cls = nonHgTaskClasses().find((c) => c.id === (t.cls || "general"));
      tasks.push({
        id,
        project_id: projId,
        status: "todo",
        owner: "master",
        title: t.title,
        body: "",
        due: t.due || null,
        level: cls?.default_level || null,
      });
      createdTasks.push(id);
    }

    setupDone = true;
    setupCompletedAt = new Date().toISOString();
    profileStore["setup.completed_at"] = setupCompletedAt;

    const result: SetupResult = { profile: { ...profileStore }, created: { projects: createdProjects, tasks: createdTasks } };
    return result as unknown as T;
  }
  if (path === "/setup/profile" && method === "PUT") {
    if (body.callname !== undefined) profileStore["master.callname"] = String(body.callname);
    if (body.butler_name !== undefined) profileStore["butler.callname"] = String(body.butler_name);
    if (body.purposes !== undefined) {
      const purposes = Array.isArray(body.purposes) ? (body.purposes as string[]) : [];
      const validPurposeIds = new Set(PURPOSES.map((p) => p.id));
      for (const p of purposes) if (!validPurposeIds.has(p)) badRequest(`用途: 不明な id です（${p}）`);
      profileStore["purposes"] = JSON.stringify(purposes);
    }
    if (body.note !== undefined) profileStore["purposes.note"] = String(body.note);
    return { profile: { ...profileStore } } as unknown as T;
  }

  // ---------- task_kinds（ADR-010 D2）----------
  if ((path === "/task-kinds" || path.startsWith("/task-kinds?")) && method === "GET") {
    const params = new URLSearchParams(path.split("?")[1] || "");
    const all = params.get("all") === "true" || params.get("all") === "1";
    const out = all ? [...taskKinds].sort((a, b) => a.sort - b.sort) : nonArchivedTaskKinds();
    return out as unknown as T;
  }
  if (path === "/task-kinds" && method === "POST") {
    const label = String(body.label || "").trim();
    if (!label) badRequest("label が必要です");
    taskKindSeq += 1;
    let id = slugifyTaskKindId(label);
    if (taskKinds.some((k) => k.id === id)) id = `${id}-${taskKindSeq}`;
    const sort = taskKinds.length ? Math.max(...taskKinds.map((k) => k.sort)) + 1 : 1;
    const k: TaskKind = { id, label, sort, archived_at: null };
    taskKinds.push(k);
    return k as unknown as T;
  }
  if (path.match(/^\/task-kinds\/[^/]+$/) && method === "PUT") {
    const id = decodeURIComponent(path.slice("/task-kinds/".length));
    const k = taskKinds.find((x) => x.id === id);
    if (!k) notFound(`task_kind が見つかりません: ${id}`);
    if (body.label !== undefined) {
      const label = String(body.label).trim();
      if (!label) badRequest("label が必要です");
      k.label = label;
    }
    if (body.sort !== undefined) k.sort = Number(body.sort);
    return k as unknown as T;
  }
  if (path.match(/^\/task-kinds\/[^/]+$/) && method === "DELETE") {
    const id = decodeURIComponent(path.slice("/task-kinds/".length));
    if (id === "other") badRequest("「その他」は消せません（分類できないものの受け皿）");
    const k = taskKinds.find((x) => x.id === id);
    if (!k) notFound(`task_kind が見つかりません: ${id}`);
    k.archived_at = new Date().toISOString();
    return k as unknown as T;
  }

  // ---------- extensions（ADR-009）----------
  if (path === "/extensions" && method === "GET") {
    return Object.keys(extensionsStore).map((id) => extensionSummary(id)) as unknown as T;
  }
  if (path.match(/^\/extensions\/[^/]+$/) && method === "GET") {
    const id = decodeURIComponent(path.slice("/extensions/".length));
    if (!extensionsStore[id]) notFound(`拡張が見つかりません: ${id}`);
    return extensionDetail(id) as unknown as T;
  }
  if (path.match(/^\/extensions\/[^/]+$/) && method === "PUT") {
    const id = decodeURIComponent(path.slice("/extensions/".length));
    const st = extensionsStore[id];
    if (!st) notFound(`拡張が見つかりません: ${id}`);
    const values = (body.values || {}) as Record<string, unknown>;
    for (const field of st.manifest.fields) {
      if (!(field.key in values)) continue;
      const v = values[field.key];
      if (st.manifest.secret_fields.includes(field.key)) {
        st.secretHas[field.key] = String(v ?? "") !== "";
      } else if (v !== null && v !== undefined) {
        st.values[field.key] = v as string | number | boolean;
      }
    }
    return extensionDetail(id) as unknown as T;
  }
  if (path.match(/^\/extensions\/[^/]+\/test$/) && method === "POST") {
    const parts = path.split("/");
    const id = decodeURIComponent(parts[2]);
    const st = extensionsStore[id];
    if (!st) notFound(`拡張が見つかりません: ${id}`);
    st.testedStatus = "ok";
    st.reason = "つながりました（合成データ）";
    st.checkedAt = new Date().toISOString();
    return extensionDetail(id) as unknown as T;
  }
  if (path.match(/^\/extensions\/[^/]+\/options\/[^/]+$/) && method === "GET") {
    const parts = path.split("/");
    const id = decodeURIComponent(parts[2]);
    const name = decodeURIComponent(parts[4]);
    if (!extensionsStore[id]) notFound(`拡張が見つかりません: ${id}`);
    if (id === "voicevox" && name === "speakers") return [...MOCK_SPEAKERS] as unknown as T;
    return [] as unknown as T;
  }
  if (path.match(/^\/extensions\/[^/]+$/) && method === "DELETE") {
    const id = decodeURIComponent(path.slice("/extensions/".length));
    const st = extensionsStore[id];
    if (!st) notFound(`拡張が見つかりません: ${id}`);
    for (const field of st.manifest.fields) {
      if (st.manifest.secret_fields.includes(field.key)) delete st.secretHas[field.key];
      else st.values[field.key] = null;
    }
    st.testedStatus = null;
    st.checkedAt = null;
    st.reason = "";
    return extensionDetail(id) as unknown as T;
  }

  // ---------- face（姿の小窓。ADR-008 §7 D14・D15） ----------
  if (path === "/face/models" && method === "GET") {
    return Object.keys(FACE_AGENT_LABELS).map((agent) => faceModelEntry(agent)) as unknown as T;
  }
  if (path.startsWith("/face/model?") && method === "DELETE") {
    const params = new URLSearchParams(path.split("?")[1] || "");
    const agent = params.get("agent") || "";
    if (!FACE_AGENT_LABELS[agent]) notFound(`担当が見つかりません: ${agent}`);
    const st = faceModelsStore[agent];
    if (st.hasModel && !st.legacy) {
      st.hasModel = false;
      st.size = null;
      st.updatedAt = null;
      return faceModelEntry(agent) as unknown as T;
    }
    if (agent === "butler" && st.legacy) {
      badRequest(
        "旧い名前（home/face/model.vrm）はここから削除できません。home/face/butler.vrm として新しい姿をアップロードして置き換えてください。"
      );
    }
    notFound(`姿が置かれていません（home/face/${agent}.vrm）`);
  }

  // ---------- settings ----------
  if (path === "/settings" && method === "GET") {
    const s: SettingsData = {
      notify: { quiet_from: 22, quiet_to: 7, has_speak_command: false },
      web: { has_passcode: webHasPasscode, require_passcode: webRequirePasscode, is_loopback: webIsLoopback, host: "127.0.0.1" },
      manor: { language: manorLanguage },
      modules: (await mockApi<Meta>("/meta")).modules,
    };
    return s as unknown as T;
  }
  if (path === "/settings" && method === "PUT") {
    // 本物のバックエンド同様、language だけは実際に合成状態へ反映する
    // （デモ・mock モードでも言語切り替えが次の /meta ポーリングで巻き戻らないように）。
    const manor = body.manor as { language?: string } | undefined;
    if (manor?.language === "auto" || manor?.language === "ja" || manor?.language === "en") {
      manorLanguage = manor.language;
    }
    // ADR-013 D2: 本物の `web/api_v1/settings.py` と同じ2つの検算をここでも行う——
    // mock モードでも「締め出しを防ぐ」挙動を試せるように（本物と乖離させない）。
    const web = body.web as { passcode?: string; require_passcode?: boolean } | undefined;
    if (web?.passcode) webHasPasscode = true;
    if (web?.require_passcode !== undefined) {
      if (web.require_passcode && !webHasPasscode) {
        badRequest("passcode が未設定です。先にパスコードを設定してから要求を有効にしてください");
      }
      if (!web.require_passcode && !webIsLoopback) {
        badRequest("ループバック以外で待ち受けている間は解除できません（自分を締め出すのを防ぐため）");
      }
      webRequirePasscode = web.require_passcode;
    }
    return (await mockApi<T>("/settings", { method: "GET" })) as T;
  }

  notFound(`mock: 未対応の経路です: ${method} ${path}`);
}

export async function mockApiUpload<T>(path: string, _form: FormData): Promise<T> {
  if (path === "/imports/money/preview") {
    return (await mockApi<T>(path, { method: "POST" })) as T;
  }
  if (path === "/imports/money/commit") {
    return (await mockApi<T>(path, { method: "POST" })) as T;
  }
  if (path === "/face/model") {
    const agent = String(_form.get("agent") || "");
    if (!FACE_AGENT_LABELS[agent]) notFound(`担当が見つかりません: ${agent}`);
    const file = _form.get("file");
    if (!(file instanceof Blob)) badRequest("file が必要です");
    // 拡張子・Content-Type ではなく中身（先頭4バイト）で確かめる(実バックエンドと同じ規則)。
    if (!(await readsAsGltf(file))) {
      badRequest(
        "VRM（glTF バイナリ）として読めません。先頭4バイトが glTF の魔法数ではありません（拡張子は見ていません）。"
      );
    }
    if (file.size > FACE_MAX_BYTES) {
      throw new ApiError(`ファイルが大きすぎます（上限 ${FACE_MAX_BYTES / (1024 * 1024)}MB）`, 413);
    }
    faceModelsStore[agent] = { hasModel: true, size: file.size, updatedAt: new Date().toISOString(), legacy: false };
    return faceModelEntry(agent) as unknown as T;
  }
  notFound(`mock: 未対応の経路です: POST ${path}`);
}
