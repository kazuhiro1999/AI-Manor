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
  home_name: string;
  // ADR-007 D4: 初回セットアップが済んでいるか（フロントの誘導用）。
  // バックエンドがまだ足していない間は undefined になりうるので、
  // 判定は必ず `=== false` で行う（undefined を「未完了」扱いしない）。
  setup_done?: boolean;
  // ADR-012 §3 D11: `[manor] language`（`auto`/`ja`/`en`）。`/meta` は認証なしで
  // 読める唯一の経路（login・setup 画面もここから初期言語を得る）。バックエンドが
  // まだ返さない間は undefined —— その場合は前回のキャッシュ（localStorage）のまま。
  language?: string;
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
  check_failures: number;
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

export interface DashboardData {
  today: string;
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
