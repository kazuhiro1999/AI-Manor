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
  Meta,
  MoneyData,
  MoneyExpense,
  NightReport,
  NightStatus,
  PantryItem,
  Project,
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
    const data: DashboardData = {
      today: TODAY,
      status: {
        ok: actionNeeded === 0,
        action_needed: actionNeeded,
        check_failures: 0,
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
