/* manor web — API v1 の型。src/manor/board/api_core.py・api_staff.py・api_night.py の
 * JSON 出力（= ADR-005 §2 D8「board API の形をそのまま v1 へ」）から起こした。
 * バックエンドの実装（src/manor/web/）はまだ無いので、ここが両担当の合意点になる。
 */

export type TaskStatus =
  | "todo"
  | "doing"
  | "waiting"
  | "hold"
  | "resident"
  | "done"
  | "withdrawn";

export type Risk = "low" | "medium" | "high";
export type DecisionStatus = "open" | "approved" | "rejected" | "modified";
export type ProjectStatus = "active" | "paused" | "done";
export type ProjectPreset = "careful" | "standard" | "fast";

export interface Task {
  id: string;
  project_id: string | null;
  status: TaskStatus;
  status_note?: string | null;
  owner: string; // "butler" | "master" | <agent name>
  level?: string | null;
  section?: string | null;
  goal?: string | null;
  now?: string | null;
  next?: string | null;
  recommendation?: string | null;
  risk?: Risk | null;
  due?: string | null;
  start?: string | null;
  end?: string | null;
  done_at?: string | null;
  title: string;
  body?: string | null;
  handoff?: Handoff | null;
  //: 起票の出どころ（例: "idea" = 意見箱）。空文字は通常の起票（src/manor/task.py add() 参照）。
  source?: string | null;
  // ADR-014 D2:「誰の件か」（`owner`＝誰が動かすか、とは別軸）。バックエンドが
  // まだ返さない間は undefined になりうる。
  user_id?: string | null;
}

export interface WithdrawnTask extends Task {
  withdrawn_at?: string;
}

export interface Decision {
  id: string;
  status: DecisionStatus;
  title: string;
  asked_at: string;
  days: number;
  stale: boolean;
  risk?: Risk | null;
  background?: string | null;
  ruling?: string | null;
  // ADR-006 §2 D5・D7: 何を見て推奨したか（ファイル・数字・出典を `- ` 箇条書きで。
  // core の `decision.evidence` 列が無い DB でも空文字として必ず入る。src/manor/web/api_v1/tasks.py 参照）。
  evidence?: string;
  project_id: string | null;
  tasks: Task[];
}

export interface ProjectInterest {
  nearest_date: string | null;
  doing: number;
  last_event_at: string | null;
  rank: number;
}

export interface Project {
  id: string;
  code: string;
  title: string;
  kind?: string | null;
  priority: number;
  preset: ProjectPreset;
  status: ProjectStatus;
  next_action?: string | null;
  due?: string | null;
  days_left: number | null;
  interest: ProjectInterest;
  // ADR-014 D2:「誰の件か」。バックエンドがまだ返さない間は undefined になりうる。
  user_id?: string | null;
}

export interface Milestone {
  id: string;
  project_id: string | null;
  title: string;
  date: string;
  approximate: boolean;
  days_left: number | null;
  /** 済んだ日時（ISO）。`null` は「まだ」。**日付（`date`）は書き換えない**ので、
   * 「その日に予定し、済んだ」がそのまま残る（執事の裁定 2026-09-05）。 */
  done_at: string | null;
}

export interface Note {
  id: string;
  title: string;
  body?: string | null;
  project_id: string | null;
}

export interface BoardCounts {
  pending: number;
  doing: number;
  doing_butler: number;
  doing_master: number;
  resident: number;
  blocked_ready: number;
  stale: number;
  done_total: number;
}

export interface Board {
  today: string;
  pending: Decision[];
  tasks: Task[];
  delegated: Task[];
  projects: Project[];
  milestones: Milestone[];
  recent_done: Task[];
  withdrawn_recent: WithdrawnTask[];
  notes: Note[];
  counts: BoardCounts;
  fingerprint: string;
}

export type TimelineEventKind = "milestone" | "deadline" | "remind" | "task";

export interface TimelineEvent {
  kind: TimelineEventKind;
  start: string;
  end: string;
  start_days: number;
  end_days: number;
  title: string;
  approximate: boolean;
  done: boolean;
  overdue: boolean;
  ref: string | number;
  detail: string;
}

export interface TimelineLane {
  id: string;
  project_id: string | null;
  name: string;
  code: string;
  priority: number;
  events: TimelineEvent[];
  scheduled: boolean;
}

export interface Timeline {
  today: string;
  horizon_days: number;
  horizon: string;
  lanes: TimelineLane[];
}

export interface Handoff {
  id: number;
  agent: string;
  task_id: string;
  verdict?: string | null;
  brief?: string;
  report?: string;
}

export interface CheckResult {
  ok: boolean;
  results: Record<string, unknown[]>;
  labels: Record<string, string>;
}

export interface TaskEvent {
  id: number;
  task_id: string;
  from_status: string | null;
  to_status: string;
  actor: string;
  note?: string | null;
  at: string;
}

export interface LogData {
  state: string;
  decided: Decision[];
  handoffs: Handoff[];
  check: CheckResult;
  events: TaskEvent[];
}

export interface CtxResponse {
  id: string;
  markdown: string;
}

/* ---------- meta / auth ---------- */

export interface ModuleMeta {
  id: string;
  title: string;
  icon: string;
  order: number;
  enabled: boolean;
}

export interface AuthMeta {
  mode: "loopback" | "passcode";
  authenticated: boolean;
}

export interface TaskClass {
  id: string;
  label: string;
  default_level: string;
  fixed: boolean;
}

// ADR-010 D2:「タスクの種類」——人に意味のある分類（並べ替え・絞り込み・振り返りの札）。
// level（行動クラス）とは無関係。GET/POST /api/v1/task-kinds・PUT /{id}・DELETE /{id}
// （削除は archive。`other` は消せない）。
export interface TaskKind {
  id: string;
  label: string;
  sort: number;
  archived_at: string | null;
}

// ADR-014 D1・D3:「利用者」（誰として見ているか。認証ではない）。GET /api/v1/users・
// GET /api/v1/meta の user/users がこの形で返す。秘密は持たない（id・name・role だけ）。
export type UserRole = "principal" | "member" | "butler";

export interface UserInfo {
  id: string;
  name: string;
  // ADR-014 D1'（追補）: 呼び名（執事がその人をどう呼ぶか）。利用者名（`name`。識別用）
  // とは別。空文字は「呼び名は未設定・利用者名で呼ぶ」。バックエンドがまだ返さない間は
  // undefined になりうる。
  callname?: string;
  role: UserRole;
}

// ADR-017 D1・D5:「端末」（鍵を持って `/api/v1/kitchen/*` を叩く相手。XR など）。
// GET /api/v1/devices がこの形で返す。**鍵（平文・ハッシュ）は決して返らない**——
// 画面に出せるのは「どの端末が誰として、いつ使ったか」だけ。
export interface DeviceInfo {
  id: string;
  name: string;
  kind: string;
  user_id: string;
  // 利用者名（画面が `/users` を引き直さずに済むようにバックエンドが添える）。
  user_name?: string;
  created_at: string;
  last_seen_at: string | null;
  revoked_at: string | null;
}

export interface Meta {
  version: string;
  today: string;
  read_only: boolean;
  stale: boolean;
  auth: AuthMeta;
  modules: ModuleMeta[];
  task_classes?: TaskClass[];
  // ADR-010 D2: 非アーカイブの task_kind 一覧（画面がここから追加の往復なしにフォームを組める）。
  task_kinds?: TaskKind[];
  // T55: 状態機械（ADR-001 §4）。`task_transitions[いまの状態]` が行ける先、
  // `task_note_required` は入るときに note（何を待つか・理由）が要る状態。
  task_transitions?: Partial<Record<TaskStatus, TaskStatus[]>>;
  task_note_required?: TaskStatus[];
  home_name: string;
  // ADR-007 D4: 初回セットアップが済んでいるか（フロントの誘導用）。
  // バックエンドがまだ足していない間は undefined になりうるので、
  // 判定は必ず `=== false` で行う（undefined を「未完了」扱いしない）。
  setup_done?: boolean;
  // ADR-012 §3 D11: `[manor] language`（`auto`/`ja`/`en`）。`/meta` は認証なしで
  // 読める唯一の経路（login・setup 画面もここから初期言語を得る）。バックエンドが
  // まだ返さない間は undefined —— その場合は前回のキャッシュ（localStorage）のまま。
  language?: string;
  // ADR-014 D3: 見ている利用者（cookie `manor_user` から解決した現在の利用者）と、
  // 切り替え先の選択肢（畳んでいないもの）。バックエンドがまだ返さない間は undefined。
  user?: UserInfo;
  users?: UserInfo[];
}

export interface HealthResponse {
  ok: boolean;
  started_at: string;
  stale: boolean;
}

/* ---------- kitchen ---------- */

export interface PantryItem {
  id: number;
  item: string;
  qty: string; // chef_pantry.qty は TEXT（"不明"などの自由記述を許す）
  unit: string;
  expires?: string | null;
  place?: string | null;
}

export interface ShoppingItem {
  id: number;
  item: string;
  reason?: string | null;
  aisle: string;
}

export interface Meal {
  id: number;
  date: string;
  slot: string;
  dish: string;
  ingredients?: string | null;
  planned?: boolean;
}

export interface TastePref {
  key: string;
  value: string;
}

export interface KitchenData {
  available: boolean;
  pantry?: PantryItem[];
  shopping_by_aisle?: Record<string, ShoppingItem[]>;
  meals_recent?: Meal[];
  taste?: TastePref[];
}

/* ---------- kitchen: レシピ帳（ADR-015 §3・D3・D4） ---------- */

export interface RecipeIngredient {
  name: string;
  qty: string;
  unit: string;
  prep: string;
  group: string;
}

export interface RecipePhase {
  id: string;
  title: string;
}

export type StepCompletion = "manual" | "auto" | "confirm";

export interface RecipeStep {
  index: number;
  phase: string; // RecipePhase.id への参照
  title: string; // ≤12文字（ADR-015 §3）
  instruction: string; // ≤100文字
  image: string | null;
  ingredients_used: string[];
  timer_sec: number | null;
  completion: StepCompletion;
  tips: string[];
}

// `chef_recipe_meta`（ADR-015 D1「うちの値」）。§6 D9: 分類の3軸（語彙は固定。空可）。
export interface RecipeMeta {
  kcal: number | null;
  protein_g: number | null;
  fat_g: number | null;
  carb_g: number | null;
  salt_g: number | null;
  nutrition_source: "" | "site" | "estimated" | "manual";
  tags: string[];
  rating: number | null;
  memo: string;
  favorite: boolean;
  times_cooked: number;
  last_cooked_at: string | null;
  category: string; // 主菜/副菜/汁物/ご飯もの/麺/デザート/その他（空可）
  main_ingredient: string; // 肉/魚介/卵/野菜/豆腐・大豆/きのこ/その他（空可）
  cuisine: string; // 和食/洋食/中華/韓国/エスニック/その他（空可）
}

// `GET/POST/PUT /api/v1/kitchen/recipes*` の契約 JSON（ADR-015 §3）。
export interface Recipe {
  id: number;
  title: string;
  source_url: string;
  source_site: string;
  hero_image: string;
  servings: number | null;
  total_minutes: number | null;
  ingredients: RecipeIngredient[];
  tools: string[];
  phases: RecipePhase[];
  steps: RecipeStep[];
  meta: RecipeMeta;
}

// `recipes.validate()` が受ける形（`id`・`meta` を持たない。登録・本体更新の送信 body）。
export type RecipeBody = Omit<Recipe, "id" | "meta">;

// `GET /api/v1/kitchen/recipes` の一覧行（ADR-015 D3・§6 D9。一覧に絞り・並びに要る値を
// 直接持つ——旧版は last_cooked_at 等が乗らず画面側で詳細もあわせて読んでいたが、契約が
// 広がったので一覧APIの応答だけで完結する（`kitchen/recipeShared.ts` の `fetchRecipeList`
// 参照。もう detail の並行取得はしない）。
export interface RecipeListItem {
  id: number;
  title: string;
  hero_image: string;
  total_minutes: number | null;
  servings: number | null;
  tags: string[];
  favorite: boolean;
  times_cooked: number;
  last_cooked_at: string | null;
  kcal: number | null;
  category: string;
  main_ingredient: string;
  cuisine: string;
  updated_at: string;
}

// `GET /api/v1/kitchen/recipes/facets`（§6 D9）。一覧の chip 列に添える件数つきの語彙。
export interface RecipeFacetValue {
  value: string;
  count: number;
}
export interface RecipeFacets {
  category: RecipeFacetValue[];
  main_ingredient: RecipeFacetValue[];
  cuisine: RecipeFacetValue[];
  tags: RecipeFacetValue[];
}

// `POST /api/v1/kitchen/recipes/import`・`/refine` の返り（R2・§6 D7。ADR-015 D2の4
// 「保存せずに下書きを返す」）。`recipe` は `RecipeBody` と同じ形（`claude -p` の構造化
// 結果を検算前のまま返すことがあるため、上限超えの可能性がある値も含めて緩めに受ける）。
// `method`: `jsonld` / `adapter:<site>`（例 `adapter:nadia`）/ `generic` / `claude`。
export interface RecipeImportResult {
  recipe: RecipeBody;
  method: string;
  warnings: string[];
  // バックエンドが下書きに「うちの値」相当（分類3軸・栄養5つ・出どころ）を添えてくることが
  // ある（例: Nadia のページの kcal 等。`nutrition_source: "site"`）。`recipe`（RecipeBody）
  // は id・meta を持たない契約なので、これは別枠——登録フォームは検算せず、そのまま
  // 「うちの値」欄の初期値に流し込み、登録直後の `PUT /recipes/{id}/meta` で乗せる。
  meta?: Partial<RecipeMeta>;
}

export type RecipeImportMode = "auto" | "claude";

/* ---------- kitchen: 材料からの栄養値の推定（ADR-019 D5） ---------- */

// 名寄せ・換算のできなかった材料1件。`reason` は**符牒**（文にするのは画面の仕事。
// `kitchen.nutrition.reason.*`）——`menu.py` の理由と同じ考え方。
export interface RecipeNutritionUnresolved {
  name: string;
  normalized: string;
  qty: string;
  unit: string;
  grams?: number;
  reason: string; // no_food / no_amount / unknown_unit / no_piece
}

// 材料表に**書かれていない**ぶんの加算1件（ADR-019 §4 追補）。いまは揚げ油・炒め油の
// 吸収だけ（`kind: "oil_absorption"`）。量は**レシピ全体ぶん**で、合計には既に入っている
// ——画面はこれを内訳の1行として見せる。`method` は調理法の語（「唐揚げ」「炒め物」）で、
// `lexicon.toml` の語彙そのもの（訳さない。`reason` のような閉じた符牒ではない）。
export interface RecipeNutritionAdjustment {
  // oil_absorption（書かれていない揚げ油を足した）／salt_discard（茹で湯・塩もみの塩を減らした。ADR-019 §6）
  kind: string;
  method: string; // 揚げ物の型、または boil / rub_squeeze / rub_rinse
  grams: number; // 足した油／減らした塩の g（レシピ全体）
  food_code?: string;
  food_name?: string;
  kept_g?: number; // salt_discard: 口に入ると見なした塩の g（レシピ全体）
  kcal?: number;
  protein_g?: number;
  fat_g?: number;
  carb_g?: number;
  salt_g: number;
}

// `GET /api/v1/kitchen/recipes/{id}/nutrition`。**保存されている5項目に
// `source`/`coverage`/`unresolved`/`adjustments` を足しただけ**（XR が読む形は変わらない）。
export interface RecipeNutrition {
  recipe_id: number;
  servings: number | null;
  kcal: number | null;
  protein_g: number | null;
  fat_g: number | null;
  carb_g: number | null;
  salt_g: number | null;
  source: "" | "site" | "estimated" | "manual";
  coverage: number | null; // 0〜1。推定のときだけ入る
  coverage_min: number; // これを下回ると `partial`（献立の候補に入らない）
  partial: boolean;
  unresolved: RecipeNutritionUnresolved[];
  adjustments: RecipeNutritionAdjustment[]; // 揚げ油の吸収など（合計には既に入っている）
  food_table_available: boolean; // 成分表をまだ取り込んでいなければ false
}

// 成分表の1行（`GET /api/v1/kitchen/food/search`）。100g あたりの値（ADR-019 D1）。
export interface FoodRow {
  food_code: string;
  food_group: string;
  name: string;
  kcal: number | null;
  protein_g: number | null;
  fat_g: number | null;
  carb_g: number | null;
  salt_g: number | null;
  refuse_pct: number;
  per: string;
  source_version: string;
  updated_at: string;
}

// 登録済みの名寄せ（`chef_food_alias`）。
export interface FoodAlias {
  alias: string;
  food_code: string;
  food_name: string | null;
  confidence: "manual" | "rule" | "llm";
  updated_at: string;
}

// 未解決の材料名を束ねた1行（同じ名前は何本のレシピに出ても1行）。
export interface FoodUnresolvedItem {
  normalized: string;
  names: string[];
  reason: string;
  // 換算できない単位（ADR-022。「1袋 = ? g」と聞くため）。古いサーバでは無い。
  units?: string[];
  count: number;
  recipes: { recipe_id: number; title: string }[];
}

// 覚えた換算（ADR-022 D1）。`llm` は Claude が Web で調べた値（出典つき）。
export interface FoodUnit {
  name: string;
  unit: string;
  grams: number;
  confidence: "manual" | "llm";
  source_url: string;
  note: string;
  updated_at: string;
}

// 調べ係の前回の結果（ADR-022 D4）。
export interface FoodResolveResult {
  asked: number;
  resolved: number;
  unresolved: number;
  failed: boolean;
  reason: string;
  finished_at?: string;
}

export interface FoodResolveStatus {
  running: boolean;
  last: FoodResolveResult | null;
}

// `GET /api/v1/kitchen/food/aliases`（設定 → 食品の名寄せ）。
export interface FoodAliasesPayload {
  unresolved: FoodUnresolvedItem[];
  unresolved_total: number;
  food_table_available: boolean;
  aliases: FoodAlias[];
  units?: FoodUnit[];
  resolve?: FoodResolveStatus;
}

/* ---------- kitchen: 献立のおすすめ（ADR-018 D3） ---------- */

// 栄養の5項目（1人分）。`RecipeMeta` の同名の欄と同じ単位。
export interface MenuNutrition {
  kcal: number;
  protein_g: number;
  fat_g: number;
  carb_g: number;
  salt_g: number;
}

export type MenuNutrient = keyof MenuNutrition;

// 理由は**定型文の符牒**（ADR-018 §4）——文にするのは画面の仕事（`kitchen.menu.reason.*`）。
// サーバが日本語の文を組むと英語の画面に日本語が出てしまうため。
export interface MenuReason {
  code: string;
  params: Record<string, string | number>;
}

// 枠（主菜・副菜・汁物）の候補1件。
export interface MenuCandidate {
  recipe_id: number;
  title: string;
  hero_image: string;
  total_minutes: number | null;
  category: string;
  main_ingredient: string;
  cuisine: string;
  dish_type: string;
  score: number;
  reasons: MenuReason[];
  nutrition: MenuNutrition;
}

// 「この組み合わせ」の1品（採点は持たない——どの枠から来たかだけ）。
export interface MenuComboItem {
  recipe_id: number;
  title: string;
  hero_image: string;
  total_minutes: number | null;
  category: string;
  main_ingredient: string;
  cuisine: string;
  dish_type: string;
  slot_kind: MenuSlotKind;
}

export type MenuSlotKind = "main" | "side" | "soup";
export type MenuBandStatus = "in" | "under" | "over";

export interface MenuBandCheckEntry {
  total: number;
  min: number;
  max: number;
  status: MenuBandStatus;
}

export interface MenuCombo {
  recipe_ids: number[];
  items: MenuComboItem[];
  total: MenuNutrition;
  band_check: Record<string, MenuBandCheckEntry>;
}

// `GET /api/v1/kitchen/menu/recommend` の応答（ADR-018 D3 が正）。
export interface MenuRecommendation {
  slot: string;
  people: number;
  band: Record<string, { min: number; max: number }>;
  applied_mood: { text: string; matched: string[]; conditions: Record<string, unknown> };
  // 主菜を指定したときだけ入る（指定しなければ `slots.main` に候補が並ぶ）。
  main: (MenuComboItem & { nutrition: MenuNutrition | null }) | null;
  slots: Record<MenuSlotKind, MenuCandidate[]>;
  combo: MenuCombo;
  // D1 で候補から外れた（栄養値が無い）レシピの件数。
  excluded_no_nutrition: number;
  // ADR-019 D4: 推定はできたが解決率（coverage）が足りず外した件数と、その下限。
  excluded_partial: number;
  coverage_min: number;
  viewing_user_id: string;
}

/* ---------- kitchen: お供の提案（ADR-021 D5） ---------- */

// 下限で見る6項目（1人分。`[menu.floor]` の鍵）。
export type CompanionNutrient = "fiber_g" | "potassium_mg" | "calcium_mg" | "iron_mg" | "vitamin_c_mg" | "veg_g";
export type CompanionHeat = "none" | "range" | "stove";

// 定番の作り方の候補（レシピサイトのページ。ADR-021 §6）。画像は直リンク。
export interface CompanionSource {
  url: string;
  site: string;
  title: string;
  image: string;
  minutes: number | null;
  why: string;
}

export interface CompanionItem {
  key: string;
  source: "recipe" | "catalog";
  recipe_id: number | null;
  catalog_key: string | null;
  title: string;
  category: string;
  kind: string;
  heat: CompanionHeat;
  tags: string[];
  minutes: number | null;
  hero_image: string;
  // 定番のときだけ入る（うちのレシピは空）。
  sources: CompanionSource[];
  score: number;
  reasons: MenuReason[];
  nutrition: MenuNutrition;
  // 推定できていなければ空（ADR-021 D2「分からないものを補えるとは言わない」）。
  micro: Partial<Record<CompanionNutrient, number>>;
}

// `GET /api/v1/kitchen/recipes/{id}/companions`。`eligible` が偽なら主菜ではない（カードを出さない）。
export interface CompanionSuggestion {
  recipe_id: number;
  eligible: boolean;
  main: {
    title: string;
    kind: string;
    nutrition: Partial<MenuNutrition>;
    micro: Partial<Record<CompanionNutrient, number>>;
    // 足りない度合いの大きい順（下限）／帯を超えるもの。
    under: CompanionNutrient[];
    over: MenuNutrient[];
  };
  floor: Partial<Record<CompanionNutrient, number>>;
  items: CompanionItem[];
  catalog_size?: number;
}


/* ---------- kitchen: 動画リスト（ADR-016 D3） ---------- */

// `GET /api/v1/kitchen/media` の1行。**XR（kitchen-xr）が読むのと同じ形**——ADR-016 D3 の
// JSON が正なので、欄を足したくなったら先に ADR を直す（`user_id` は返らない契約）。
export interface MediaItem {
  id: string;
  title: string;
  video_id: string;
  url: string;
  thumbnail_url: string;
  author: string;
  memo: string;
  sort_order: number;
}

// 一覧の応答。`updated_at` は一覧全体の最終更新（1件も無ければ null）——XR が
// 「前に読んだときから変わったか」を1つの値で見られるようにするためのもの。
export interface MediaList {
  items: MediaItem[];
  updated_at: string | null;
}

/* ---------- house ---------- */

export interface HouseRow {
  id?: number | string;
  name?: string;
  item?: string;
  what?: string;
  qty?: number | null;
  threshold?: number | null;
  overdue_days?: number | null;
}

export interface HouseData {
  available: boolean;
  today?: Record<string, (HouseRow | string)[]>;
}

/* ---------- money ---------- */

export interface MoneyCategorySummary {
  category: string;
  spent: number;
  budget?: number | null;
  diff?: number | null;
  over: boolean;
}

export interface MoneyDue {
  id: number;
  name: string;
  next_due: string;
  overdue_days: number;
  amount: number;
}

export interface MoneyExpense {
  id: number;
  date: string;
  category: string;
  memo?: string | null;
  amount: number;
  kind: "expense" | "income";
}

export interface MoneyData {
  available: boolean;
  month?: { expenses: MoneyCategorySummary[] };
  due?: MoneyDue[];
  recent_expenses?: MoneyExpense[];
}

/* ---------- money: レシートの読み取り（ADR-020。`/api/v1/money/receipts*`） ---------- */

export type ReceiptStatus = "reading" | "draft" | "committed" | "failed" | "discarded";
export type ReceiptReview = "ok" | "needs_review" | "fixed";
// "" は「まだ読んでいない」（ADR-020 §「GET .../receipts」）。
export type ReceiptMethod = "ocr" | "claude" | "ocr+claude" | "manual" | "";
export type ReceiptTaxMode = "exclusive" | "inclusive" | "unknown";
export type ReceiptPaymentMethod = "cash" | "credit" | "qr" | "ic" | "unknown";
export type ReceiptItemSource = "ocr" | "claude" | "rule" | "alias" | "manual";
export type ReceiptTaxRate = 8 | 10;

// `POST /money/receipts` 応答の簡易チェック（ADR-020 D9）。issues の符牒は
// 画面側で訳す（サーバは文を組まない——他の reason/mood の符牒と同じ約束）。
export type QuickIssue = "no_paper" | "corners_cut" | "blurry" | "too_dark" | "no_total" | "ocr_unavailable";

export interface Quick {
  ok: boolean;
  issues: QuickIssue[];
  paper: boolean;
  corners_inside: boolean;
  sharpness: number | null;
  found: { total: boolean; date: boolean };
}

// `POST /money/receipts` 自体の応答（受付／簡易チェック不合格／重複のどれか）。
export interface ReceiptUploadResult {
  id: number | null;
  status: "reading" | "rejected" | "duplicate";
  quick: Quick;
  duplicate_of?: number;
}

export interface ReceiptSummary {
  id: number;
  status: ReceiptStatus;
  review: ReceiptReview;
  store_name: string;
  purchased_at: string | null;
  total: number | null;
  item_count: number;
  method: ReceiptMethod;
  reason: string;
  created_at: string;
  committed_at: string | null;
}

export interface ReceiptsOcrInfo {
  available: boolean;
  device: "cpu" | "dml" | null;
  model: string | null;
}

// `GET /money/receipts` の応答全体。
export interface ReceiptsListResponse {
  items: ReceiptSummary[];
  ocr: ReceiptsOcrInfo;
  today: { count: number; limit: number };
}

// `ok: null` は「判定できない（材料が無い）」（ADR-020 D9 の Check 定義）。
export interface ReceiptCheck {
  ok: boolean | null;
  expected: number | null;
  actual: number | null;
}

export interface ReceiptChecks {
  items_sum: ReceiptCheck;
  item_count: ReceiptCheck;
  tax_8: ReceiptCheck;
  tax_10: ReceiptCheck;
  total: ReceiptCheck;
  change: ReceiptCheck;
}

export interface ReceiptItem {
  id: number;
  line_no: number;
  name: string;
  name_normalized: string;
  qty: string;
  unit_price: number | null;
  amount: number;
  tax_rate: ReceiptTaxRate | null;
  is_discount: boolean;
  category: string;
  subcategory: string;
  item_kind: string;
  source: ReceiptItemSource;
}

export interface ReceiptTax {
  rate: ReceiptTaxRate;
  amount: number;
}

export interface ReceiptStoreInfo {
  name: string;
  branch: string | null;
  tel: string | null;
  registration_number: string | null;
}

// `ReceiptDetail.expenses` の1行——`steward_expense` の全欄ではなく契約どおりの
// 5つだけ（`MoneyExpense` と似ているが `kind` を持たない別の形）。
export interface ReceiptExpenseRef {
  id: number;
  date: string;
  amount: number;
  category: string;
  memo?: string | null;
}

export interface ReceiptDetail {
  id: number;
  status: ReceiptStatus;
  review: ReceiptReview;
  method: ReceiptMethod;
  reads: number;
  reason: string;
  created_at: string;
  committed_at: string | null;
  created_by: string;
  store: ReceiptStoreInfo;
  purchased_at: string | null;
  receipt_no: string | null;
  tax_mode: ReceiptTaxMode;
  payment_method: ReceiptPaymentMethod;
  subtotal: number | null;
  taxes: ReceiptTax[];
  total: number | null;
  item_count_declared: number | null;
  tendered: number | null;
  change: number | null;
  items: ReceiptItem[];
  checks: ReceiptChecks;
  notes: string[];
  image_url: string;
  expenses: ReceiptExpenseRef[];
}

// `PUT /money/receipts/{id}` の1明細（送るのは全行——ADR-020 D9「画面は全行を送る」）。
export interface ReceiptItemUpdate {
  line_no: number;
  name: string;
  qty: string;
  unit_price: number | null;
  amount: number;
  tax_rate: ReceiptTaxRate | null;
  is_discount: boolean;
  category: string;
  subcategory: string;
  item_kind: string;
}

// `PUT /money/receipts/{id}` の body（全部任意。送った鍵だけ上書き）。
export interface ReceiptUpdatePayload {
  store_name?: string;
  purchased_at?: string | null;
  total?: number | null;
  subtotal?: number | null;
  tax_mode?: ReceiptTaxMode;
  payment_method?: ReceiptPaymentMethod;
  taxes?: ReceiptTax[];
  items?: ReceiptItemUpdate[];
  learn_aliases?: boolean;
}

export interface ReceiptRereadResult {
  id: number;
  status: "reading";
}

// `GET /money/categories`。マネーフォワード ME の大項目・中項目＋manor 独自の品目。
export interface MoneyCategory {
  name: string;
  subcategories: string[];
}

export interface MoneyCategoriesResponse {
  categories: MoneyCategory[];
  item_kinds: string[];
}

/* ---------- money: 内訳（ADR-020 追補。`GET /money/breakdown?ym=YYYY-MM`） ---------- */

export interface MoneyBreakdownMonthPoint {
  ym: string;
  expense: number;
  income: number;
}

export interface MoneyBreakdownSummary {
  expense: number;
  income: number;
  prev_expense: number | null;
  diff: number | null;
  receipts: number;
  days_with_spending: number;
}

// 大項目（マネーフォワード ME の大分類）。budget/prev_amount は無ければ null。
export interface MoneyBreakdownCategory {
  name: string;
  amount: number;
  share: number;
  prev_amount: number | null;
  budget: number | null;
}

// 中項目。登録済みレシートの明細から（`category` は大項目名を添える）。
export interface MoneyBreakdownSubcategory {
  category: string;
  name: string;
  amount: number;
  share: number;
}

// 品目。登録済みレシートの明細から。
export interface MoneyBreakdownItemKind {
  name: string;
  amount: number;
  share: number;
  items: number;
}

// 店。登録済みレシートから。
export interface MoneyBreakdownStore {
  name: string;
  amount: number;
  share: number;
  receipts: number;
}

// よく買ったもの（同じ品名を足した上位10）。
export interface MoneyBreakdownTopItem {
  name: string;
  amount: number;
  qty: number;
  store: string;
  item_kind: string;
  count: number;
}

export interface MoneyBreakdownDailyPoint {
  date: string;
  amount: number;
}

export interface MoneyBreakdownTopStat {
  name: string;
  amount: number;
  share: number;
}

export interface MoneyBreakdownResponse {
  ym: string;
  prev_ym: string;
  months: MoneyBreakdownMonthPoint[]; // データのある月だけ、古い→新しい、直近6か月
  summary: MoneyBreakdownSummary;
  by_category: MoneyBreakdownCategory[]; // 金額の降順
  by_subcategory: MoneyBreakdownSubcategory[]; // 金額の降順
  by_item_kind: MoneyBreakdownItemKind[]; // 金額の降順
  by_store: MoneyBreakdownStore[]; // 金額の降順
  top_items: MoneyBreakdownTopItem[];
  daily: MoneyBreakdownDailyPoint[]; // 月の全日
  top_category: MoneyBreakdownTopStat | null;
  top_item_kind: MoneyBreakdownTopStat | null;
}

/* ---------- secretary ---------- */

export interface AgendaItem {
  date: string;
  kind: string;
  id?: string | number;
  title: string;
  detail?: string;
  overdue: boolean;
}

export interface ReminderItem {
  id: number;
  text: string;
  on_date: string;
  at_time?: string | null;
  done_at?: string | null;
}

export interface InboxItem {
  id: number;
  received_at: string;
  ref: string;
}

export interface SecretaryData {
  available: boolean;
  agenda?: AgendaItem[];
  reminders_open?: ReminderItem[];
  inbox_unrouted?: InboxItem[];
}

/* ---------- rules ---------- */

export type RuleScope = "family" | "adults" | "kids" | "guests" | "staff";

export interface Rule {
  id: number;
  title: string;
  body: string;
  scope: RuleScope;
  tags: string; // 読点区切り
  effective_from?: string | null;
  effective_to?: string | null;
  created_at: string;
  updated_at: string;
  archived_at?: string | null;
}

/* ---------- imports ---------- */

export type ImportFormat = "generic" | "zaim" | "moneyforward";

// 実バックエンド（src/manor/staff/steward/importer.py の ImportResult.to_dict）の形。
// `rows` は取り込み対象のみ、重複は別配列 `duplicates`（同じ行の形。件数ではない）に
// 分かれている——ADR-005 §2 の `{rows, duplicates, unreadable, total}` はここまで細かく
// 決めていなかった（曖昧だった点。報告に書く）。画面側は両方を1つに合わせて
// 「重複は灰色」（ADR-005 §3）を実現する。
export interface ImportPreviewRow {
  line: number;
  date: string;
  amount: number;
  category: string;
  memo?: string;
  kind: "expense" | "income";
  import_hash: string;
}

export interface ImportUnreadableRow {
  line: number;
  raw: Record<string, string>; // 元の CSV 行（列名→値）。マッピングが解決できなかった行
  reason: string;
}

export interface ImportPreview {
  rows: ImportPreviewRow[];
  duplicates: ImportPreviewRow[];
  unreadable: ImportUnreadableRow[];
  total: number;
}

export interface ImportCommitResult {
  inserted: number;
  skipped: number;
}

/* ---------- night ---------- */

export interface NightField {
  label: string;
  text: string;
}

export interface NightTask {
  number?: string;
  title: string;
  state?: "done" | "hold" | "other";
  fields: NightField[];
}

export interface NightParsed {
  ok: boolean;
  title?: string;
  summary?: string[];
  tasks: NightTask[];
}

export interface NightReport {
  date: string;
  text: string;
  parsed: NightParsed;
}

export interface NightStatus {
  ok: boolean;
  last_run_at?: string | null;
  detail?: string;
}

/* ---------- face（姿の小窓。ADR-008 §7 D14・D15）---------- */

// GET /api/v1/face/models の1件。`legacy` が立つのは butler だけ、かつ butler.vrm が無く
// model.vrm（後方互換の名前）だけがあるとき（src/manor/web/api_v1/face_models.py 参照）。
export interface FaceModelEntry {
  agent: string;
  label: string;
  has_model: boolean;
  size: number | null;
  updated_at: string | null;
  legacy: boolean;
  /** 同梱の既定アバター（`assets/face/default.vrm`）で出ている。**主人の持ち物ではない**
   * ので消せない。差し替えれば `home/face/<担当>.vrm` が優先される（2026-09-05）。 */
  bundled: boolean;
}

/* ---------- settings ---------- */

export interface SettingsData {
  notify: {
    // home/config.toml [notify] の quiet_from/quiet_to は「時」の整数（0-23。src/manor/notify.py
    // ・src/manor/web/api_v1/settings.py 参照）。"HH:MM" 文字列ではない。
    quiet_from?: number | null;
    quiet_to?: number | null;
    has_speak_command: boolean;
  };
  web: {
    has_passcode: boolean;
    // ADR-013 D2: `[web] require_passcode` の現在値と、「今ループバックで待ち受けて
    // いるか」（非ループバック中は off にできない、の判定に使う）。
    require_passcode: boolean;
    is_loopback: boolean;
    host: string;
  };
  // ADR-012 §3 D11: `[manor] language`。読みは /meta 経由でも得られる（未認証で読める）が、
  // ここにも同じ値を出す（GET /api/v1/settings の形として一貫させる）。
  manor: {
    language: string;
  };
  modules: ModuleMeta[];
}

/* ---------- runs（ADR-006 §3 D11・§6「稼働と費用」）---------- */

export type RunKind = "night" | "behavior" | "gate" | "talk" | "other";

// `run` 表（担当A・core）の1行。まだ表が無い home では `/api/v1/runs*` が
// `{available: false, ...}` を返す（部下の表と同じ約束。src/manor/web/api_v1/runs.py）。
export interface RunRow {
  id: number;
  kind: RunKind;
  ref: string;
  started_at: string;
  ended_at?: string | null;
  model: string;
  input_tokens?: number | null;
  output_tokens?: number | null;
  cache_read_tokens?: number | null;
  cache_write_tokens?: number | null;
  cost_usd?: number | null;
  turns?: number | null;
  exit_reason: string; // done / failed / killed / timeout / limit
  note: string;
}

export interface RunsData {
  available: boolean;
  runs: RunRow[];
}

export interface RunKindStat {
  kind: RunKind;
  count: number;
  cost_usd: number;
  avg_seconds: number | null;
  failed: number;
  input_tokens: number;
  output_tokens: number;
}

export interface RunStatsData {
  available: boolean;
  by_kind: RunKindStat[];
  total_cost_usd: number;
}

/* ---------- dashboard（ADR-011 D2。GET /api/v1/dashboard）---------- */

export interface DashboardStatus {
  ok: boolean;
  action_needed: number;
  open_decisions: number;
  blocked_ready: number;
}

export interface DashboardCounts {
  pending_decisions: number;
  doing_butler: number;
  due_today: number;
  done_this_week: number;
}

export interface DashboardNight {
  available: boolean;
  status?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
}

export type DashboardUpcomingKind = "milestone" | "task" | "event";

export interface DashboardUpcomingItem {
  kind: DashboardUpcomingKind;
  id: string | number;
  title: string;
  date: string;
  approximate: boolean;
  days_left: number | null;
}

export interface DashboardAttentionItem {
  id: string;
  title: string;
  days: number;
  risk?: string | null;
  stale: boolean;
}

export interface DashboardRunsBand {
  available: boolean;
  runs: RunRow[];
}

// `runlog.stats` の生の形（`/api/v1/runs/stats` が加工する前のもの。ADR-006 D23）。
export interface DashboardKindStat {
  kind: string;
  count: number;
  cost_usd: number | null;
  avg_seconds: number | null;
  fail_rate: number;
}

export interface DashboardMostActive {
  available: boolean;
  by_kind: DashboardKindStat[];
}

export interface DashboardUsageCost {
  available: boolean;
  count?: number;
  failed?: number;
  success_rate?: number | null;
  cost_usd?: number | null;
  cost_measured?: number;
}

/* 管制塔の3枚（2026-09-06・外部レビュー）。**新しい集計ではない**——`get_board` が
 * 既に持っているものを、トップ画面に要る形へ並べ替えたもの。 */
export interface DashboardNeedsYou {
  id: string;
  title: string;
  recommendation: string;
  evidence: string;
  risk: string;
  days: number | null;
  project_id: string;
}

export interface DashboardWorkingItem {
  id: string;
  title: string;
  project_id: string;
}

export interface DashboardWorking {
  owner: string;
  tasks: DashboardWorkingItem[];
  more: number;
}

export interface DashboardTodayItem {
  id: string;
  title: string;
  owner: string;
  project_id: string;
}

export interface DashboardRecentItem {
  id: string;
  title: string;
  owner: string;
  at: string;
}

export interface DashboardGreeting {
  /** `morning` / `day` / `evening`。**語ではなく時間帯**——境目はバックエンドの
   * `talk_session.time_of_day` の1箇所にあり、訳はこちらで当てる。 */
  time_of_day: string;
  /** 主人の呼び名（`master.callname`）。未設定なら空文字＝名前を呼ばない。 */
  name: string;
}

export interface DashboardData {
  today: string;
  greeting: DashboardGreeting;
  needs_you: DashboardNeedsYou[];
  working: DashboardWorking[];
  due_today_list: DashboardTodayItem[];
  recent: DashboardRecentItem[];
  status: DashboardStatus;
  counts: DashboardCounts;
  night: DashboardNight;
  upcoming: DashboardUpcomingItem[];
  attention: DashboardAttentionItem[];
  runs_24h: DashboardRunsBand;
  most_active: DashboardMostActive;
  usage_cost: DashboardUsageCost;
}

/* ---------- agents（ADR-011 D3。GET /api/v1/agents）---------- */

export interface AgentCard {
  id: string;
  label: string;
  role: string;
  summary: string;
  page: string | null;
  has_model: boolean;
  enabled: boolean;
}

/* ---------- setup（ADR-007 D4）---------- */

export interface SetupPurpose {
  id: string;
  label: string;
}

export interface SetupPreset {
  id: string;
  label: string;
}

// ADR-007 §6 D9: `steward/importer.py` の `PRESET_MAPS` の id ＋ 先頭に
// `{id:"none", label:"使っていない"}`。
export interface SetupMoneyApp {
  id: string;
  label: string;
}

// GET /api/v1/setup。`profile` は D1 の `profile` 表をそのまま key→value で写したもの
// （`master.callname` `butler.callname` `purposes`（JSON配列文字列）`purposes.note`
// `money.app` `money.currency` `setup.completed_at`）。`task_classes` は meta と同じ生成だが、
// fixed かつ HG のクラスは除かれている（ウィザードから HG 固定クラスは選べない）。
// `purposes` は ADR-007 §6 D7 の語彙（`tasks` `kitchen` `money` `house` `secretary`）。
export interface SetupInfo {
  done: boolean;
  completed_at: string | null;
  profile: Record<string, string>;
  purposes: SetupPurpose[];
  presets: SetupPreset[];
  task_classes: TaskClass[];
  money_apps: SetupMoneyApp[];
}

export interface SetupProjectAnswer {
  code: string;
  name: string;
  due?: string;
  preset?: string;
}

export interface SetupTaskAnswer {
  title: string;
  project_code?: string;
  // ADR-010 D1: 行動クラスは初回セットアップの画面から外れた。送らなければサーバー側の
  // 既定（general）になる。
  cls?: string;
  // ADR-010 D2: タスクの種類（任意）。空なら送らない。
  kind?: string;
  due?: string;
}

// ADR-007 §6 D8「台所の前提」。`chef_taste` の household_size / allergies / dislikes へ
// 書かれる（profile には持たない）。
export interface SetupKitchenAnswer {
  household_size?: number;
  allergies?: string;
  dislikes?: string;
}

// ADR-007 §6 D8「家計の前提」。`profile` の money.app / money.currency へ書かれる。
export interface SetupMoneyAnswer {
  app?: string;
  currency?: string;
}

// POST /api/v1/setup の body（ADR-007 §6 D9）。`kitchen`/`money` は該当の段が
// 出ていた（＝用途が選ばれていた）ときだけ送る。段が出ていない・「あとで」で飛ばした
// ときは省く。
export interface SetupAnswers {
  callname: string;
  butler_name?: string;
  purposes: string[];
  note?: string;
  projects: SetupProjectAnswer[];
  tasks: SetupTaskAnswer[];
  kitchen?: SetupKitchenAnswer;
  money?: SetupMoneyAnswer;
}

export interface SetupResult {
  profile: Record<string, string>;
  created: { projects: string[]; tasks: string[] };
}

// PUT /api/v1/setup/profile の body。
export interface SetupProfileUpdate {
  callname?: string;
  butler_name?: string;
  purposes?: string[];
  note?: string;
}

/* ---------- extensions（ADR-009）---------- */

export type ExtensionKind = "local_app" | "service" | "network";

// D3: 状態は5つ。「判定は道具がやり、名前は機械が決める」。
export type ExtensionStatus = "not_installed" | "needs_config" | "ready" | "ok" | "error";

export type ExtensionFieldKind = "text" | "password" | "number" | "select" | "path";

export interface ExtensionField {
  key: string;
  label: string;
  kind: ExtensionFieldKind;
  options_from?: string; // D5: GET /extensions/{id}/options/{options_from}
  help?: string;
  required?: boolean;
}

export interface ExtensionManifest {
  id: string;
  label: string;
  kind: ExtensionKind;
  summary: string;
  install_steps: string[];
  fields: ExtensionField[];
  secret_fields: string[];
}

// GET /api/v1/extensions の1件。
export interface ExtensionSummary {
  id: string;
  label: string;
  kind: ExtensionKind;
  summary: string;
  status: ExtensionStatus;
  checked_at: string | null;
  reason: string;
}

// GET /api/v1/extensions/{id}・PUT/POST test/DELETE の応答（同じ形に統一。
// src/manor/web/api_v1/extensions.py 参照）。`values` は秘密フィールドについては
// `has_<key>: boolean` だけを持ち、値そのもののキーは無い（D4）。
export interface ExtensionDetail {
  id: string;
  manifest: ExtensionManifest;
  values: Record<string, string | number | boolean | null>;
  install_steps: string[];
  status: ExtensionStatus;
  checked_at: string | null;
  reason: string;
}

export interface ExtensionOption {
  value: string | number;
  label: string;
  /** 親の名（ADR-009 D17）。あると画面が「親 → 子」の2段で選ばせる。無ければ平らな1段。 */
  group?: string;
  /** 2段目に出す短い名（例: スタイル名）。無ければ `label` を使う。 */
  member_label?: string;
}

/** 朝の点検（`GET /api/v1/night/review`）。走ったか・何が片付かなかったか・何晩続いたか。
 *  ⚠ 画面から読むときは `record=false` 相当で、連続日数はサーバが数え直さない。 */
export interface NightReview {
  date: string;
  health: { ok: boolean; reasons: string[] };
  items: { found: boolean; pending: { heading: string; state: string; nights?: number }[] };
  stuck: string[];
}
