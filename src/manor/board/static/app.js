/* manor board フロントエンド。フレームワーク・CDN は使わない素の JS。 */
"use strict";

const VIEWS = ["judge", "running", "plan", "log", "house"];
const DEFAULT_VIEW = "judge";
const PLAN_TABS = ["timeline", "projects", "milestones"];
const LOG_TABS = ["state", "decided", "handoff", "check", "history", "night"];
const HOUSE_TABS = ["chef", "house", "money", "sec"];

const STATUS_META = {
  doing: { label: "進行中", cls: "st-doing" },
  resident: { label: "常駐", cls: "st-resident" },
  todo: { label: "未着手", cls: "st-todo" },
  hold: { label: "保留", cls: "st-hold" },
  waiting: { label: "待ち", cls: "st-waiting" },
  done: { label: "完了", cls: "st-done" },
  withdrawn: { label: "取り下げ", cls: "st-withdrawn" },
};

const RISK_LABEL = { low: "低", medium: "中", high: "高" };

const PROJECT_STATUS_LABEL = { active: "進行中", paused: "休止", done: "完了" };
const PROJECT_PRESET_LABEL = { careful: "🐢慎重", standard: "🚶標準", fast: "🏃高速" };

/* タスクの並べ方（ステータス別／プロジェクト別）。選択は localStorage に覚える
   （v1 README §2-1「切り替えたら次回もそれで開く」）。 */
const TASK_MODE_KEY = "manor-board.taskMode";
const TASK_MODES = ["list", "tree"];

/* 「① 直近で完了」の畳み方（v1 README §2-1「日ごとに畳んである」）。完了は消えずに
   増え続ける束なので、表示量を「見出し＋開いた日ぶん」に固定する。**既定は閉じる。
   開閉は localStorage に覚える**（主人の要望「完了済みは畳んで開閉できるように」）。 */
const DONE_RECENT_DAYS = 7;
const DONE_OLDER_KEY = "__older__";
const DONE_OPEN_KEY = "manor-board.doneOpen";

function loadDoneOpenSet() {
  try {
    const raw = localStorage.getItem(DONE_OPEN_KEY);
    if (!raw) return new Set();
    const arr = JSON.parse(raw);
    if (Array.isArray(arr)) return new Set(arr);
  } catch (e) { /* 読めなくても空集合（既定=全部閉じる）で始める */ }
  return new Set();
}
function saveDoneOpenSet() {
  try { localStorage.setItem(DONE_OPEN_KEY, JSON.stringify(Array.from(state.doneOpen))); }
  catch (e) { /* 保存できなくても表示は続く */ }
}

const state = {
  view: DEFAULT_VIEW,
  planTab: "timeline",
  logTab: "state",
  houseTab: "chef",
  taskMode: "list",
  timelineSpan: 7,
  board: null,
  fingerprint: null,
  timelineData: null,
  logData: null,
  staff: {},
  drafts: new Map(),
  openDetail: new Set(),
  treeOpen: new Set(),
  doneOpen: loadDoneOpenSet(),
  withdrawnOpen: new Set(),
  handoffOpen: new Set(),
  timelineOpenRef: null,
  nightDates: [],
  nightDate: null,
  nightData: null,
  readOnly: false,
  busy: false,
  pollMs: 5000,
  timer: null,
};

/* ---------- utils ---------- */

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function el(tag, className, html) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (html != null) node.innerHTML = html;
  return node;
}

function banner(message, kind, timeoutMs) {
  const node = document.getElementById("banner");
  node.className = "banner " + (kind || "");
  node.textContent = message;
  node.hidden = false;
  if (banner._t) clearTimeout(banner._t);
  if (timeoutMs) banner._t = setTimeout(() => { node.hidden = true; }, timeoutMs);
}

function setSync(text, cls) {
  const node = document.getElementById("sync");
  node.textContent = text;
  node.className = "chip chip-sync " + (cls || "");
}

/* ---------- 入力中はポーリングで壊さない ----------
 * 主人の指摘（2巡目）: 5秒ごとのポーリングで renderJudge が入力欄ごと作り直すため、
 * 入力中にフォーカスが定期的に外れる。v1 は「入力途中のコメントはポーリングで閉じない」を
 * 文だけでなくフォーカスも含めて守っていた——ここで同じことをする。
 * キー入力中（フォーカス）だけでなく、**IME 変換中（compositionstart〜compositionend）**も
 * 「編集中」として扱う（変換の途中で描き直されると変換そのものが壊れる）。 */
let composingElement = null;

function initCompositionGuard() {
  document.addEventListener("compositionstart", (ev) => { composingElement = ev.target; });
  document.addEventListener("compositionend", (ev) => {
    if (composingElement === ev.target) composingElement = null;
  });
}

/** 指定コンテナの中に、フォーカスされた input/textarea があるか、IME 変換中の要素が
 *  あるかを見る。true なら「このポーリング周はここを再描画しない」の合図——データ
 *  （state.board / state.logData）は更新済みのまま持ち、フォーカスが外れた次の
 *  ポーリングで最新の内容を描く。 */
function isEditingWithin(containerId) {
  const container = document.getElementById(containerId);
  if (!container) return false;
  const active = document.activeElement;
  const activeIsInput = !!(
    active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA") && container.contains(active)
  );
  const composingWithin = !!(composingElement && container.contains(composingElement));
  return activeIsInput || composingWithin;
}

async function api(path, options) {
  const res = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, options));
  let body = null;
  try { body = await res.json(); } catch (e) { body = null; }
  if (!res.ok) {
    const detail = body && body.detail ? body.detail : ("HTTP " + res.status);
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return body;
}

function projectLabel(board, projectId) {
  if (!projectId) return "—";
  const p = (board.projects || []).find((x) => x.id === projectId);
  return p ? (p.code + " " + p.title) : projectId;
}

function daysLeftClass(n) {
  if (n == null) return "";
  if (n <= 14) return "soon";
  if (n <= 35) return "near";
  return "";
}

function daysLeftText(n, approximate) {
  if (n == null) return "—";
  const prefix = approximate ? "約" : "";
  if (n < 0) return prefix + "超過" + (-n) + "日";
  if (n === 0) return prefix + "本日";
  return prefix + "残" + n + "日";
}

function fmtRisk(risk) {
  if (!risk) return "";
  return `<span class="risk risk-${esc(risk)}">risk ${esc(RISK_LABEL[risk] || risk)}</span>`;
}

/** 「詳細を表示」の中身（v1 の D セクション相当）。manor では decision.background と
 *  紐づく task.body の両方を出す——背景（なぜ判断が要るか）と本文（task の地）を
 *  分けて持っているため、どちらかだけでは足りない。 */
function decisionDetailText(d) {
  const parts = [];
  parts.push("背景: " + (d.background || "（背景の記載なし）"));
  for (const t of d.tasks || []) {
    if (t.body && t.body.trim()) parts.push(`[${t.id}] ${t.body.trim()}`);
  }
  return parts.join("\n\n");
}

/* ---------- 設定（配色）。サーバへは送らず localStorage に残す ---------- */

const THEME_KEY = "manor-board.theme";
const THEMES = ["system", "light", "dark"];

function readPref(key, allowed, fallback) {
  let v = null;
  try { v = localStorage.getItem(key); } catch (e) { v = null; }
  return allowed.indexOf(v) >= 0 ? v : fallback;
}
function writePref(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { /* 保存できなくても表示は続く */ }
}

function applyTheme(value, save) {
  const v = THEMES.indexOf(value) >= 0 ? value : "system";
  if (v === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", v);
  for (const btn of document.querySelectorAll("#settings-panel [data-theme]")) {
    btn.setAttribute("aria-pressed", btn.dataset.theme === v ? "true" : "false");
  }
  if (save !== false) writePref(THEME_KEY, v);
}

function toggleSettings(force) {
  const panel = document.getElementById("settings-panel");
  const open = force != null ? force : panel.hidden;
  panel.hidden = !open;
}

function initSettings() {
  applyTheme(readPref(THEME_KEY, THEMES, "system"), false);
  for (const btn of document.querySelectorAll("#settings-panel [data-theme]")) {
    btn.onclick = () => applyTheme(btn.dataset.theme);
  }
  document.getElementById("settings-toggle").onclick = () => toggleSettings();
  document.addEventListener("click", (ev) => {
    const panel = document.getElementById("settings-panel");
    if (panel.hidden) return;
    if (panel.contains(ev.target) || ev.target.closest("#settings-toggle")) return;
    toggleSettings(false);
  });
  const navToggle = document.getElementById("nav-toggle");
  navToggle.onclick = () => document.body.classList.toggle("nav-hidden");
}

/* ---------- ハッシュ / 画面遷移 ---------- */

function parseHash() {
  const raw = (location.hash || "").replace(/^#\/?/, "");
  const parts = raw.split("/").filter(Boolean);
  return { view: parts[0] || DEFAULT_VIEW, tab: parts[1] || null };
}

function currentHash() {
  const tab = state.view === "plan" ? state.planTab : state.view === "log" ? state.logTab
    : state.view === "house" ? state.houseTab : null;
  return "#/" + state.view + (tab ? "/" + tab : "");
}

function showView(name, pushHash) {
  state.view = VIEWS.indexOf(name) >= 0 ? name : DEFAULT_VIEW;
  for (const v of VIEWS) {
    const node = document.getElementById("view-" + v);
    if (node) node.hidden = v !== state.view;
  }
  for (const btn of document.querySelectorAll(".nav-item")) {
    btn.classList.toggle("active", btn.dataset.view === state.view);
  }
  if (pushHash !== false && location.hash !== currentHash()) location.hash = currentHash();
  loadForView(state.view);
}

function showTabGroup(kind, tabs, stateKey, name, pushHash) {
  const value = tabs.indexOf(name) >= 0 ? name : tabs[0];
  state[stateKey] = value;
  for (const t of tabs) {
    const panel = document.getElementById("panel-" + (kind === "house" ? "house-" + t : t));
    if (panel) panel.hidden = t !== value;
  }
  for (const btn of document.querySelectorAll("." + kind + "-tab")) {
    const active = btn.dataset[kind + "Tab"] === value;
    btn.setAttribute("aria-selected", active ? "true" : "false");
  }
  if (pushHash !== false && location.hash !== currentHash()) location.hash = currentHash();
  loadForView(state.view);
}

function showPlanTab(name, pushHash) { showTabGroup("plan", PLAN_TABS, "planTab", name, pushHash); }
function showLogTab(name, pushHash) { showTabGroup("log", LOG_TABS, "logTab", name, pushHash); }
function showHouseTab(name, pushHash) { showTabGroup("house", HOUSE_TABS, "houseTab", name, pushHash); }

/* ---------- ctx モーダル ---------- */

function openCtxModal(id) {
  const root = document.getElementById("modal-root");
  root.innerHTML = "";
  const backdrop = el("div", "modal-backdrop");
  const modal = el("div", "modal");
  const head = el("div", "modal-head", `<h3>${esc(id)} の文脈</h3>`);
  const closeBtn = el("button", "btn btn-small", "閉じる");
  closeBtn.onclick = () => backdrop.remove();
  head.appendChild(closeBtn);
  const body = el("div", "modal-body", "読み込み中…");
  modal.appendChild(head);
  modal.appendChild(body);

  if (id[0] === "T" && !state.readOnly) {
    const form = buildTaskStatusForm(id);
    modal.appendChild(form);
  }

  backdrop.appendChild(modal);
  backdrop.onclick = (ev) => { if (ev.target === backdrop) backdrop.remove(); };
  root.appendChild(backdrop);

  api("/api/ctx/" + encodeURIComponent(id)).then((res) => {
    // **Markdown を描画する。** 以前は記法を素の文字として出していた（主人の指摘
    // 「表示が Markdown 記法そのまま」）。`mdToHtml` は先に HTML エスケープしてから
    // 記法を組み立てるので、`res.markdown` に third-party 由来の文が混じっても安全（md.js 参照）。
    body.innerHTML = res.markdown
      ? `<div class="md-body">${mdToHtml(res.markdown)}</div>`
      : `<p class="panel-note">（内容なし）</p>`;
  }).catch((err) => {
    body.textContent = "読み込めませんでした: " + err.message;
  });
}

function buildTaskStatusForm(taskId) {
  const wrap = el("div", "modal-body", "");
  wrap.style.borderTop = "1px solid var(--border)";
  wrap.innerHTML = `
    <div style="font-weight:700;margin-bottom:6px;">状態を変える</div>
    <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;">
      <select class="ruling-input" style="flex:0 0 auto;" id="ts-status">
        <option value="todo">未着手 todo</option>
        <option value="doing">進行中 doing</option>
        <option value="waiting">待ち waiting</option>
        <option value="hold">保留 hold</option>
        <option value="resident">常駐 resident</option>
        <option value="done">完了 done</option>
        <option value="withdrawn">取り下げ withdrawn</option>
      </select>
      <input class="ruling-input" id="ts-note" placeholder="note（waiting/withdrawn は必須）">
      <button class="btn btn-primary btn-small" id="ts-submit">変更</button>
    </div>
    <div id="ts-msg" style="font-size:11.5px;color:var(--text-dim);margin-top:6px;"></div>
  `;
  wrap.querySelector("#ts-submit").onclick = async () => {
    const status = wrap.querySelector("#ts-status").value;
    const note = wrap.querySelector("#ts-note").value;
    const msg = wrap.querySelector("#ts-msg");
    msg.textContent = "送信中…";
    try {
      const res = await api(`/api/task/${encodeURIComponent(taskId)}/status`, {
        method: "POST", body: JSON.stringify({ status, note }),
      });
      msg.textContent = `${res.id} -> ${res.status}` + ((res.warnings || []).length ? "（警告あり）" : "");
      refresh(true);
    } catch (err) {
      msg.textContent = "拒否されました: " + err.message;
    }
  };
  return wrap;
}

/* ---------- 1. 要対応 ---------- */

function renderJudge(board) {
  const list = document.getElementById("pending-list");
  const pending = board.pending || [];
  document.getElementById("pending-count").textContent = pending.length + "件";
  if (!pending.length) { list.innerHTML = `<p class="panel-note">（なし）</p>`; return; }

  list.innerHTML = "";
  for (const d of pending) {
    const card = el("div", "card" + (d.stale ? " stale" : ""));
    const pj = d.project_id ? projectLabel(board, d.project_id) : "—";
    const openKey = "pending:" + d.id;
    const isOpen = state.openDetail.has(openKey);
    card.innerHTML = `
      <div class="card-head">
        <span class="card-title">${esc(d.id)} ${esc(d.title)}</span>
        <span class="card-pj">${esc(pj)}</span>
        ${d.stale ? '<span class="badge-judge">要判断</span>' : ""}
        <span class="card-days${d.stale ? " stale" : ""}">${d.days}日 滞留</span>
      </div>
      <div class="card-rec">推奨: ${esc(d.recommendation || "（なし）")} ${fmtRisk(d.risk)}</div>
      <button class="detail-toggle" type="button">${isOpen ? "詳細を閉じる" : "詳細を表示"}</button>
      <div class="detail-box" ${isOpen ? "" : "hidden"}>${esc(decisionDetailText(d))}</div>
      <div class="card-actions"></div>
    `;
    card.querySelector(".detail-toggle").onclick = (ev) => {
      const box = card.querySelector(".detail-box");
      box.hidden = !box.hidden;
      ev.target.textContent = box.hidden ? "詳細を表示" : "詳細を閉じる";
      if (box.hidden) state.openDetail.delete(openKey); else state.openDetail.add(openKey);
    };
    const actions = card.querySelector(".card-actions");
    if (state.readOnly) {
      actions.innerHTML = `<span class="panel-note">読み取り専用モードのため裁定できません</span>`;
    } else {
      const input = el("input", "ruling-input");
      // 主人の指摘（2巡目）: 承認・却下は一言なしで押せてよい（core が既定の一言
      // 「承認」「却下」を入れる）。**修正だけ**は何をどう直すかが無いと執事が動けない
      // ので文が必須——プレースホルダでその違いを示す。
      input.placeholder = "裁定の一言（承認・却下は省略可／修正は必須）";
      input.value = state.drafts.get(d.id) || "";
      input.oninput = () => {
        state.drafts.set(d.id, input.value);
        input.classList.remove("input-error");
      };
      const mkBtn = (label, status, cls) => {
        const b = el("button", "btn btn-small " + (cls || ""), label);
        b.onclick = () => ruleDecision(d.id, status, input.value, input);
        return b;
      };
      actions.appendChild(input);
      actions.appendChild(mkBtn("承認", "approved", "btn-primary"));
      actions.appendChild(mkBtn("修正", "modified", ""));
      actions.appendChild(mkBtn("却下", "rejected", "btn-danger"));
    }
    list.appendChild(card);
  }
}

/** 裁定を送る。**承認・却下は一言なしで押せる**（core が既定の一言を入れる）。
 *  **修正だけ**は文が必須——空のまま押されたら入力欄を赤くして送らない（v1「入力が無いと
 *  押せない」を全裁定に掛けていたのを、修正だけに絞った。主人の指摘・2巡目）。 */
async function ruleDecision(id, status, ruling, inputEl) {
  const trimmed = (ruling || "").trim();
  if (status === "modified" && !trimmed) {
    if (inputEl) inputEl.classList.add("input-error");
    banner("修正には、どう直すか一言（ruling）が必要です", "warn", 4000);
    return;
  }
  if (inputEl) inputEl.classList.remove("input-error");
  try {
    await api(`/api/decision/${encodeURIComponent(id)}/rule`, {
      method: "POST", body: JSON.stringify({ status, ruling: trimmed }),
    });
    state.drafts.delete(id);
    banner(`${id} を ${status} にしました`, "ok", 4000);
    refresh(true);
  } catch (err) {
    banner("裁定できませんでした: " + err.message, "error");
  }
}

/* ---------- 2. AIの進行中 ---------- */

/** タイトルの先頭に付いた `[...]` が、渡された `project`（親）の code か名前
 *  （またはその2つを繋いだ表記）と一致するときだけ落とす。DB の t.title 自体は
 *  変えない——表示するときだけの整形（主人の指摘・2巡目「ツリーの `[p3 ...]` が
 *  見にくい」。v1 由来のタイトルに接頭辞が残っていることがある）。 */
function stripLeadingProjectBracket(title, project) {
  const text = title || "";
  if (!project) return text;
  const m = /^\[([^\]]+)\]\s*/.exec(text);
  if (!m) return text;
  const inner = m[1].trim();
  const code = String(project.code || "").trim();
  const name = String(project.title || "").trim();
  const combined = `${code} ${name}`.trim();
  if (inner === code || inner === name || inner === combined) {
    return text.slice(m[0].length);
  }
  return text;
}

function taskRow(board, t, opts) {
  opts = opts || {};
  const meta = STATUS_META[t.status] || { label: t.status, cls: "st-todo" };
  const finished = t.status === "done";
  const row = el("div", "row-item" + (finished ? " finished" : "") + (t.status === "withdrawn" ? " withdrawn" : ""));
  const owner = t.owner === "master" ? '<span class="owner-tag master">（主人）</span>'
    : (t.owner && t.owner !== "butler" ? `<span class="owner-tag">→ ${esc(t.owner)}</span>` : "");
  // **プロジェクト別ツリーでは親行が既にプロジェクトなので、行の `[pj]` 接頭辞は
  // 親と同じなら要らない**（主人の指摘・2巡目）。`opts.parentProject` はツリーの
  // 呼び出しだけが渡す（ステータス別では渡さない＝そこではプロジェクトが分からない
  // ので常に残す）。t.project_id が親と一致するときだけ省く。
  const isUnderMatchingParent = !!(opts.parentProject && String(opts.parentProject.id) === String(t.project_id));
  const pjLabel = opts.pj || projectLabel(board, t.project_id);
  const displayTitle = isUnderMatchingParent
    ? stripLeadingProjectBracket(t.title, opts.parentProject)
    : t.title;
  row.innerHTML = `
    <span class="row-id">${esc(t.id)}</span>
    ${t.level ? `<span class="badge-l">${esc(t.level)}</span>` : ""}
    <span class="badge-st ${meta.cls}">${esc(meta.label)}</span>
    <span class="row-title">${isUnderMatchingParent ? "" : `[${esc(pjLabel)}] `}${esc(displayTitle)}</span>
    ${opts.latest ? '<span class="badge-latest">最新</span>' : ""}
    ${owner}
  `;
  const ctxBtn = el("button", "btn btn-small btn-ghost btn-ctx", "文脈");
  ctxBtn.onclick = () => openCtxModal(t.id);
  row.appendChild(ctxBtn);
  return row;
}

/** A（要対応）の1件をツリー内に小さく出す。裁定は「要対応」画面で行う（v1 pendingMiniRow）。 */
function pendingMiniRow(board, d) {
  const row = el("div", "row-item" + (d.stale ? " stale" : ""));
  row.innerHTML = `
    <span class="row-id">${esc(d.id)}</span>
    <span class="badge-pending-n">要対応</span>
    ${fmtRisk(d.risk)}
    <span class="row-title">${esc(d.title)}</span>
    ${d.stale ? `<span class="card-days stale">${esc(d.days)}日 滞留</span>` : ""}
  `;
  const jump = el("button", "btn btn-small", "裁定する →");
  jump.onclick = () => showView("judge");
  row.appendChild(jump);
  return row;
}

/** 完了の束を「完了日」でまとめる（新しい順）。v1 doneDateGroups と同じ畳み方。
 *  manor の `done_at` は ISO 日時なので、先頭10文字を日付キーにする。 */
function doneDateGroups(items) {
  const ordered = [];
  const index = new Map();
  const sorted = (items || []).slice().sort((a, b) => (b.done_at || "").localeCompare(a.done_at || ""));
  for (const item of sorted) {
    const key = item.done_at ? String(item.done_at).slice(0, 10) : DONE_OLDER_KEY;
    let group = index.get(key);
    if (!group) {
      group = { key: key, label: key === DONE_OLDER_KEY ? "日付なし" : key, items: [] };
      index.set(key, group);
      ordered.push(group);
    }
    group.items.push(item);
  }
  const recent = ordered.filter((g) => g.key !== DONE_OLDER_KEY).slice(0, DONE_RECENT_DAYS);
  const rest = [];
  for (const group of ordered) {
    if (recent.indexOf(group) < 0) rest.push.apply(rest, group.items);
  }
  if (rest.length) recent.push({ key: DONE_OLDER_KEY, label: "それ以前", items: rest });
  return recent;
}

/** 完了の束を「日付の見出し＋折りたたみ」で描く。`scope` は開閉状態の名前空間
 *  （リストとツリー・プロジェクトごとに別々に覚える。混ぜると別の場所の開閉が連動する）。 */
function renderDoneDays(board, items, scope, opts) {
  const options = opts || {};
  const wrap = el("div", "done-days");
  const groups = doneDateGroups(items);
  const latestId = groups.length && groups[0].items.length ? groups[0].items[0].id : null;

  // **既定は閉じる。** 開閉は localStorage に覚えるので、以前に開いた日はリロード後も
  // 開いたまま（主人の要望「完了済みは畳んで開閉できるように」）。ここでは何も自動で
  // 開かない——毎回自動で開くと「畳んである」の意味が無くなる。
  for (const group of groups) {
    const stateKey = scope + "/" + group.key;
    const day = el("div", "done-day");
    const head = el("button", "done-day-head" + (state.doneOpen.has(stateKey) ? " open" : ""));
    head.type = "button";
    head.innerHTML = `<span class="caret">▶</span><span class="done-day-label">${esc(group.label)}</span><span class="count">${group.items.length}件</span>`;
    const body = el("div", "rows done-day-body");
    body.hidden = !state.doneOpen.has(stateKey);
    for (const item of group.items) {
      body.appendChild(taskRow(board, item, {
        pj: options.pj, latest: item.id === latestId, parentProject: options.parentProject,
      }));
    }
    head.onclick = () => {
      if (state.doneOpen.has(stateKey)) state.doneOpen.delete(stateKey); else state.doneOpen.add(stateKey);
      head.classList.toggle("open");
      body.hidden = !state.doneOpen.has(stateKey);
      saveDoneOpenSet();
    };
    day.appendChild(head);
    day.appendChild(body);
    wrap.appendChild(day);
  }
  return wrap;
}

/** 単発の折りたたみブロック（`done-day` の見た目を流用）。「取り下げ」のように
 *  日ごとの束ではない、1つの塊をまるごと畳みたいときに使う。既定は閉じる。 */
function renderFoldBlock(label, count, buildBody, openSet, key) {
  const wrap = el("div", "done-days");
  const day = el("div", "done-day");
  const isOpen = openSet.has(key);
  const head = el("button", "done-day-head" + (isOpen ? " open" : ""));
  head.type = "button";
  head.innerHTML = `<span class="caret">▶</span><span class="done-day-label">${esc(label)}</span><span class="count">${count}件</span>`;
  const body = el("div", "rows done-day-body");
  body.hidden = !isOpen;
  buildBody(body);
  head.onclick = () => {
    if (openSet.has(key)) openSet.delete(key); else openSet.add(key);
    head.classList.toggle("open");
    body.hidden = !openSet.has(key);
  };
  day.appendChild(head);
  day.appendChild(body);
  wrap.appendChild(day);
  return wrap;
}

function renderSummaryTiles(board) {
  const wrap = document.getElementById("running-summary");
  const recent = board.recent_done || [];
  const groups = doneDateGroups(recent);
  const newest = groups[0];
  const total = board.counts.done_total != null ? board.counts.done_total : recent.length;
  wrap.innerHTML = "";
  const tiles = [
    {
      label: "① 直近で完了", value: newest ? newest.items.length : 0,
      sub: newest ? `${newest.label === DONE_OLDER_KEY ? "日付なし" : newest.label} ぶん（累計 ${total}件）` : "完了の記録はありません",
    },
    {
      // **執事のぶんだけ数える。** 主人の作業は別に添える（v1 README §2-1）。
      label: "② 実行中", value: board.counts.doing_butler,
      sub: board.counts.doing_master ? `ほかに主人の作業 ${board.counts.doing_master} 件` : "",
    },
    { label: "③ 私の要対応", value: board.counts.pending, sub: "クリックで要対応へ", click: "judge" },
  ];
  for (const t of tiles) {
    const tile = el("div", "tile" + (t.click ? " clickable" : ""));
    tile.innerHTML = `<div class="tile-label">${esc(t.label)}</div><div class="tile-value">${t.value}</div><div class="tile-sub">${esc(t.sub)}</div>`;
    if (t.click) tile.onclick = () => showView(t.click);
    wrap.appendChild(tile);
  }
}

/** ステータス別の並び順（主人の裁定「1のステータス別」）:
 *  主人の作業（進行中）→ 執事の実行中 → 委譲中 → 常駐 → 未着手・保留・待ち →
 *  直近の完了（日ごとに畳む・既定閉じる）→ 取り下げ（畳む）。
 *  0件のブロックは出さない。「主人の作業」は**進行中だけ**をここで独立させる——
 *  待ち・未着手・常駐の主人の作業は、執事のぶんと同じ「未着手・保留・待ち」
 *  「常駐」ブロックへ（主人）印つきで混ぜる（taskRow の owner-tag が出す）。
 *  「一番関心があるので一番上に」という主人の要望どおり、①②より前に置く。 */
function renderRunningList(board) {
  const wrap = document.getElementById("running-list");
  wrap.innerHTML = "";
  const tasks = board.tasks || [];
  const masterDoing = tasks.filter((t) => t.owner === "master" && t.status === "doing");
  const butlerDoing = tasks.filter((t) => t.status === "doing" && t.owner === "butler");
  const resident = tasks.filter((t) => t.status === "resident");
  const backlog = tasks.filter((t) => ["todo", "waiting", "hold"].indexOf(t.status) >= 0);
  const done = tasks.filter((t) => t.status === "done");
  const withdrawn = board.withdrawn_recent || [];

  const groups = [
    { key: "master-doing", label: "主人の作業（進行中）", rows: masterDoing },
    { key: "doing", label: "実行中（執事）", rows: butlerDoing },
    { key: "delegated", label: "委譲中", rows: (board.delegated || []) },
    { key: "resident", label: "常駐", rows: resident },
    { key: "backlog", label: "未着手・保留・待ち", rows: backlog },
  ];
  for (const g of groups) {
    if (!g.rows.length) continue;
    const block = el("div", "");
    block.appendChild(el("div", "status-block-head", `${esc(g.label)}（${g.rows.length}）`));
    const rows = el("div", "rows");
    for (const t of g.rows) rows.appendChild(taskRow(board, t));
    block.appendChild(rows);
    wrap.appendChild(block);
  }
  if (done.length) {
    const block = el("div", "");
    block.appendChild(el("div", "status-block-head", `① 直近の完了（${done.length}）`));
    block.appendChild(renderDoneDays(board, done, "list"));
    wrap.appendChild(block);
  }
  if (withdrawn.length) {
    const block = el("div", "");
    block.appendChild(el("div", "status-block-head", "取り下げ"));
    block.appendChild(renderFoldBlock("取り下げ（直近7日）", withdrawn.length, (body) => {
      for (const t of withdrawn) body.appendChild(taskRow(board, t));
    }, state.withdrawnOpen, "withdrawn-recent"));
    wrap.appendChild(block);
  }
  if (!wrap.children.length) wrap.innerHTML = `<p class="panel-note">（なし）</p>`;
}

const STATUS_ORDER = ["doing", "resident", "waiting", "hold", "todo", "done", "withdrawn"];

/** プロジェクト別の並び順（主人の裁定「3のプロジェクト別」）の小さな根拠テキスト。
 *  例:「9/3まで・進行中1」。期日が無ければ件数だけ（例:「進行中0」）。 */
function interestReasonText(interest) {
  if (!interest) return "";
  const parts = [];
  if (interest.nearest_date) {
    const d = new Date(interest.nearest_date + "T00:00:00");
    parts.push(`${d.getMonth() + 1}/${d.getDate()}まで`);
  }
  parts.push(`進行中${interest.doing}`);
  return parts.join("・");
}

/** プロジェクト別ツリー。行に配下の件数と「要対応/実行中/常駐/取り下げ」バッジを出す
 *  （閉じたままでも分かる。v1 README §2-1）。
 *
 *  プロジェクトの並びは `project.interest.rank`（`/api/board` がサーバ側で計算した
 *  関心順。主人の裁定「3のプロジェクト別」）に従う——執事のプロジェクト（`kind==='執事'`）
 *  は常に最下部、それ以外は直近の期日→進行中の件数→最後に動いた時刻→優先度→code の順。
 *  `interest` が無い（古いキャッシュ等）ときは優先度だけで並べる保険を残す。 */
function renderRunningTree(board) {
  const wrap = document.getElementById("running-tree");
  wrap.innerHTML = "";
  const tasks = (board.tasks || []).concat(board.withdrawn_recent || []);
  const pending = board.pending || [];
  const byProject = new Map();
  const pendingByProject = new Map();
  for (const t of tasks) {
    const key = t.project_id || "__none__";
    if (!byProject.has(key)) byProject.set(key, []);
    byProject.get(key).push(t);
  }
  for (const d of pending) {
    const key = d.project_id || "__none__";
    if (!pendingByProject.has(key)) pendingByProject.set(key, []);
    pendingByProject.get(key).push(d);
  }
  const projects = (board.projects || []).slice().sort((a, b) => {
    const ra = a.interest ? a.interest.rank : (a.priority != null ? a.priority : 999);
    const rb = b.interest ? b.interest.rank : (b.priority != null ? b.priority : 999);
    return ra - rb;
  });
  const otherKeys = [];
  if (byProject.has("__none__") || pendingByProject.has("__none__")) otherKeys.push("__none__");
  const keys = projects.map((p) => p.id).concat(otherKeys);
  for (const key of keys) {
    const rows = byProject.get(key) || [];
    const pendingRows = pendingByProject.get(key) || [];
    if (!rows.length && !pendingRows.length) continue;
    const proj = projects.find((p) => p.id === key);
    const title = proj ? `${proj.code} ${proj.title}` : "その他（プロジェクト未設定）";
    const total = rows.length + pendingRows.length;
    const group = el("div", "tree-group" + (state.treeOpen.has(key) ? " open" : ""));
    const doingN = rows.filter((r) => r.status === "doing").length;
    const residentN = rows.filter((r) => r.status === "resident").length;
    const withdrawnN = rows.filter((r) => r.status === "withdrawn").length;
    const badges = [];
    if (pendingRows.length) badges.push(`<span class="badge-st badge-pending-n">要対応 ${pendingRows.length}</span>`);
    if (doingN) badges.push(`<span class="badge-st st-doing">実行中 ${doingN}</span>`);
    if (residentN) badges.push(`<span class="badge-st st-resident">常駐 ${residentN}</span>`);
    if (withdrawnN) badges.push(`<span class="badge-st st-withdrawn">取り下げ ${withdrawnN}</span>`);
    // **なぜこの順かが見えること**（執事の裁定「3のプロジェクト別」）。並べ替えの根拠
    // （直近の期日・進行中件数）を小さく添え、執事自身のプロジェクトには印を出す。
    const isButlerProject = proj && proj.kind === "執事";
    const reasonText = proj ? interestReasonText(proj.interest) : "";
    const head = el("div", "tree-group-head");
    head.innerHTML = `<span class="caret">▶</span><span class="tree-group-title">${esc(title)}</span>
      ${isButlerProject ? '<span class="tree-group-kind">執事</span>' : ""}
      ${reasonText ? `<span class="tree-group-interest">${esc(reasonText)}</span>` : ""}
      <span class="tree-group-badges">${badges.join("")}<span class="nav-count">${total}件</span></span>`;
    head.onclick = () => {
      if (state.treeOpen.has(key)) state.treeOpen.delete(key); else state.treeOpen.add(key);
      renderRunningTree(board);
    };
    group.appendChild(head);
    const body = el("div", "tree-group-body");
    body.hidden = !state.treeOpen.has(key);
    if (pendingRows.length) {
      body.appendChild(el("div", "tree-sub", `③ 要対応（${pendingRows.length}）`));
      const prows = el("div", "rows");
      for (const d of pendingRows) prows.appendChild(pendingMiniRow(board, d));
      body.appendChild(prows);
    }
    for (const block of [
      { key: "doing", title: "② 実行中" },
      { key: "resident", title: "常駐（見張り）" },
      { key: "todo", title: "未着手・保留・待ち", extra: ["waiting", "hold"] },
      { key: "done", title: "① 直近で完了", isDone: true },
      { key: "withdrawn", title: "取り下げ（やらないと決めた）" },
    ]) {
      const codes = [block.key].concat(block.extra || []);
      const items = rows.filter((r) => codes.indexOf(r.status) >= 0);
      if (!items.length) continue;
      body.appendChild(el("div", "tree-sub", `${block.title}（${items.length}）`));
      if (block.isDone) {
        body.appendChild(renderDoneDays(board, items, "tree:" + key, { pj: title, parentProject: proj }));
      } else {
        const trows = el("div", "rows");
        for (const t of items) trows.appendChild(taskRow(board, t, { pj: title, parentProject: proj }));
        body.appendChild(trows);
      }
    }
    group.appendChild(body);
    wrap.appendChild(group);
  }
  if (!wrap.children.length) wrap.innerHTML = `<p class="panel-note">（なし）</p>`;
}

function renderRelayList(board) {
  const wrap = document.getElementById("relay-list");
  if (!wrap) return;
  const notes = board.notes || [];
  document.getElementById("relay-count").textContent = notes.length + "件";
  wrap.innerHTML = "";
  if (!notes.length) {
    wrap.innerHTML = `<p class="panel-note">無い。</p>`;
    return;
  }
  for (const n of notes) {
    const row = el("div", "row-item");
    row.innerHTML = `
      <span class="row-id">${esc(n.id)}</span>
      ${n.project_id ? `<span class="card-pj">${esc(projectLabel(board, n.project_id))}</span>` : ""}
      <span class="row-title">${esc(n.title)}</span>
    `;
    wrap.appendChild(row);
  }
}

function setTaskMode(mode, remember) {
  state.taskMode = TASK_MODES.indexOf(mode) >= 0 ? mode : "list";
  document.getElementById("running-list").hidden = state.taskMode !== "list";
  document.getElementById("running-tree").hidden = state.taskMode !== "tree";
  for (const btn of document.querySelectorAll(".seg-btn[data-mode]")) {
    btn.setAttribute("aria-pressed", btn.dataset.mode === state.taskMode ? "true" : "false");
  }
  if (remember !== false) writePref(TASK_MODE_KEY, state.taskMode);
  if (state.board) { renderRunningList(state.board); renderRunningTree(state.board); }
}

function renderRunning(board) {
  renderSummaryTiles(board);
  document.getElementById("running-count").textContent = (board.tasks || []).length + "件";
  renderRunningList(board);
  renderRunningTree(board);
  renderRelayList(board);
}

/* ---------- 3. 計画 ---------- */

function renderProjects(board) {
  const tbody = document.querySelector("#projects-table tbody");
  const rows = board.projects || [];
  document.getElementById("projects-count").textContent = rows.length + "件";
  tbody.innerHTML = "";
  for (const p of rows) {
    const tr = document.createElement("tr");
    const dl = p.days_left;
    // **短い列（code・優先度・preset・期限・残日数）だけ nowrap にする。**
    // 「次の一手」「状態」は長い自由文になりうるので折り返す——ここに全列 nowrap を
    // 付けていたせいで表が横に伸び、期限・残日数を見るのに横スクロールが要った
    // （主人の指摘「計画→プロジェクトの表示が変」）。
    tr.innerHTML = `
      <td class="col-nowrap">${esc(p.code)}</td>
      <td class="col-wide">${esc(p.title)}</td>
      <td class="col-nowrap">${esc(p.kind || "—")}</td>
      <td class="col-nowrap">${esc(p.priority)}</td>
      <td class="col-nowrap">${esc(PROJECT_PRESET_LABEL[p.preset] || p.preset)}</td>
      <td class="col-nowrap">${esc(PROJECT_STATUS_LABEL[p.status] || p.status)}</td>
      <td class="col-wide">${esc(p.next_action || "—")}</td>
      <td class="col-nowrap">${esc(p.due || "—")}</td>
      <td class="days-left col-nowrap ${daysLeftClass(dl)}">${daysLeftText(dl)}</td>
    `;
    tbody.appendChild(tr);
  }
}

function renderMilestones(board) {
  const wrap = document.getElementById("milestone-list");
  const rows = board.milestones || [];
  document.getElementById("milestones-count").textContent = rows.length + "件";
  wrap.innerHTML = "";
  for (const m of rows) {
    const row = el("div", "row-item");
    const dl = m.days_left;
    row.innerHTML = `
      <span class="row-id">${esc(m.date)}${m.approximate ? "頃" : ""}</span>
      <span class="row-title">${esc(m.title)} [${esc(projectLabel(board, m.project_id))}]</span>
      <span class="days-left ${daysLeftClass(dl)}">${daysLeftText(dl, m.approximate)}</span>
    `;
    wrap.appendChild(row);
  }
  if (!rows.length) wrap.innerHTML = `<p class="panel-note">（なし）</p>`;
}

const TL_KIND_LABEL = { milestone: "節目", deadline: "期限", remind: "控え", task: "課題" };

/** クリックした帯の全文を下に描く。控え（remind）は「済にする」から
 *  `POST /api/staff/sec/remind/{id}/done` を呼べる（v1 README §2-4「控えは画面から
 *  済にできる」）。 */
function showTimelineDetail(e) {
  const detail = document.getElementById("timeline-detail");
  detail.hidden = false;
  state.timelineOpenRef = e.kind + ":" + e.ref;
  const head = el("div", null,
    `<strong>[${esc(TL_KIND_LABEL[e.kind] || e.kind)}] ${esc(e.title)}</strong>\n${esc(e.start)} 〜 ${esc(e.end)}${e.approximate ? "（概算・動かせます）" : ""}\n\n${esc(e.detail || "")}`);
  detail.innerHTML = "";
  detail.appendChild(head);
  if (e.kind === "remind" && e.ref != null && !state.readOnly) {
    const act = el("div", "card-actions");
    const btn = el("button", "btn btn-small " + (e.done ? "btn-ghost" : "btn-primary"), e.done ? "未済に戻す" : "済にする");
    btn.onclick = async () => {
      btn.disabled = true;
      try {
        await api(`/api/staff/sec/remind/${encodeURIComponent(e.ref)}/done`, {
          method: "POST", body: JSON.stringify({ note: "" }),
        });
        banner("控えを済にしました。", "ok", 3000);
        loadTimeline();
      } catch (err) {
        banner("控えを更新できませんでした: " + err.message, "error");
        btn.disabled = false;
      }
    };
    act.appendChild(btn);
    detail.appendChild(act);
  }
}

function renderTimeline(data) {
  const wrap = document.getElementById("timeline");
  const looseWrap = document.getElementById("timeline-loose");
  const detail = document.getElementById("timeline-detail");
  wrap.innerHTML = "";
  looseWrap.innerHTML = "";
  if (!data || !data.lanes) { wrap.textContent = "（読み込み中）"; return; }

  const span = state.timelineSpan;
  const today = data.today;
  const isMonth = span > 7;
  const cols = isMonth ? Math.ceil(span / 7) : span;

  let scheduled = data.lanes.filter((ln) => (ln.events || []).some((e) => e.start_days <= span - 1 && e.end_days >= 0));
  const loose = data.lanes.filter((ln) => !scheduled.includes(ln));
  // **絞ったあとに並べ直す。** サーバは 70日ぶんを horizon_days で返し、画面が
  // 1週間/1ヶ月に絞るので、絞った結果に合わせて順序を組み直す（v1 README §2-4「絞った
  // あとに並べ直す」）。プロジェクト外（project_id なし）は一番下へ（v1 と同じ理由:
  // いちばん目に入る場所を「どこにも属さないもの」が占めないようにする）。
  scheduled = scheduled.slice().sort((a, b) => {
    const aNone = !a.project_id, bNone = !b.project_id;
    if (aNone !== bNone) return aNone ? 1 : -1;
    const aStart = Math.min(...a.events.filter((e) => e.start_days <= span - 1 && e.end_days >= 0).map((e) => e.start_days));
    const bStart = Math.min(...b.events.filter((e) => e.start_days <= span - 1 && e.end_days >= 0).map((e) => e.start_days));
    if (aStart !== bStart) return aStart - bStart;
    return (b.priority || 0) - (a.priority || 0);
  });
  document.getElementById("timeline-count").textContent = scheduled.length + "件（表示中）";

  // **開いていた詳細はポーリングで閉じない。** 同じ ref がまだ窓の中にあれば描き直す。
  let reopen = null;
  if (state.timelineOpenRef) {
    for (const ln of scheduled) {
      for (const e of (ln.events || [])) {
        if (e.kind + ":" + e.ref === state.timelineOpenRef) { reopen = e; break; }
      }
      if (reopen) break;
    }
  }
  detail.hidden = !reopen;
  if (!reopen) state.timelineOpenRef = null;

  const table = el("div", "tl-table");
  table.style.setProperty("--tl-cols", cols);

  const header = el("div", "tl-row tl-header");
  header.appendChild(el("div", "tl-name", ""));
  const headTrack = el("div", "tl-track");
  headTrack.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`;
  const todayDate = new Date(today + "T00:00:00");
  for (let i = 0; i < cols; i++) {
    const cell = el("div", "tl-head");
    if (isMonth) {
      cell.textContent = "第" + (i + 1) + "週";
    } else {
      const d = new Date(todayDate.getTime() + i * 86400000);
      const isToday = i === 0;
      if (isToday) cell.classList.add("is-today");
      cell.textContent = (d.getMonth() + 1) + "/" + d.getDate();
    }
    headTrack.appendChild(cell);
  }
  header.appendChild(headTrack);
  table.appendChild(header);

  for (const lane of scheduled) {
    const row = el("div", "tl-row");
    const name = el("div", "tl-name", `<div class="tl-name-main">${esc(lane.name)}</div><div class="tl-next">${esc((lane.events || []).length)}件</div>`);
    row.appendChild(name);
    const track = el("div", "tl-track");
    track.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`;
    for (const e of (lane.events || [])) {
      const startCol = isMonth ? Math.floor(e.start_days / 7) : e.start_days;
      const endCol = isMonth ? Math.floor(e.end_days / 7) : e.end_days;
      if (endCol < 0 || startCol > cols - 1) continue;
      const s = Math.max(0, startCol), en = Math.min(cols - 1, endCol);
      const bar = el("div", `tl-bar tl-${e.kind}` + (e.approximate ? " tl-approx" : "") + (e.overdue ? " tl-overdue" : "") + (e.done ? " tl-done" : ""));
      bar.style.left = `calc(${s} * 100% / ${cols})`;
      bar.style.width = `calc(${(en - s + 1)} * 100% / ${cols} - 6px)`;
      bar.innerHTML = `<span class="tl-what">${esc(e.title)}</span>`;
      bar.title = e.title;
      bar.onclick = () => showTimelineDetail(e);
      track.appendChild(bar);
    }
    row.appendChild(track);
    table.appendChild(row);
  }
  wrap.appendChild(table);

  if (reopen) showTimelineDetail(reopen);

  if (loose.length) {
    looseWrap.appendChild(el("div", "status-block-head", "随時（予定の無いプロジェクト）"));
    // **予定の無いプロジェクトも優先度順に残す。** 消すと「空いた時間にやるもの」が
    // 画面から消える（v1 README §2-4）。`priority` は数字が小さいほど優先度が高い。
    for (const ln of loose.slice().sort((a, b) => (a.priority || 0) - (b.priority || 0))) {
      looseWrap.appendChild(el("div", "tl-loose-item", esc(ln.name)));
    }
  }
}

async function loadTimeline() {
  try {
    const days = state.timelineSpan > 7 ? 70 : 7;
    const data = await api(`/api/timeline?days=${days}`);
    state.timelineData = data;
    renderTimeline(data);
  } catch (err) {
    document.getElementById("timeline").textContent = "読み込めませんでした: " + err.message;
  }
}

/* ---------- 4. 記録 ---------- */

function renderState(text) {
  const body = document.getElementById("state-body");
  if (!text) {
    body.innerHTML = `<p class="panel-note">（射影がまだありません。manor render を実行してください）</p>`;
    return;
  }
  // STATE.md 射影を Markdown として描画する（主人の要望「Markdown を描画する」）。
  body.innerHTML = `<div class="md-body">${mdToHtml(text)}</div>`;
}

function renderDecided(board, decided) {
  const wrap = document.getElementById("decided-list");
  document.getElementById("decided-count").textContent = decided.length + "件";
  wrap.innerHTML = "";
  for (const d of decided) {
    const row = el("div", "row-item");
    row.innerHTML = `
      <span class="row-id">${esc(d.id)}</span>
      <span class="badge-st ${d.status === "approved" ? "st-done" : d.status === "rejected" ? "st-withdrawn" : "st-waiting"}">${esc(d.status)}</span>
      <span class="row-title">${esc(d.title)}</span>
    `;
    const openKey = "decided:" + d.id;
    const isOpen = state.openDetail.has(openKey);
    const detailBtn = el("button", "detail-toggle", isOpen ? "詳細を閉じる" : "詳細を表示");
    const box = el("div", "detail-box", `裁定: ${esc(d.ruling || "（なし）")}\n背景: ${esc(d.background || "（なし）")}`);
    box.hidden = !isOpen;
    detailBtn.onclick = () => {
      box.hidden = !box.hidden;
      detailBtn.textContent = box.hidden ? "詳細を表示" : "詳細を閉じる";
      if (box.hidden) state.openDetail.delete(openKey); else state.openDetail.add(openKey);
    };
    const cell = el("div", "");
    cell.style.width = "100%";
    cell.appendChild(row);
    cell.appendChild(detailBtn);
    cell.appendChild(box);
    wrap.appendChild(cell);
  }
  if (!decided.length) wrap.innerHTML = `<p class="panel-note">（なし）</p>`;
}

function renderHandoffs(handoffs) {
  const wrap = document.getElementById("handoff-list");
  document.getElementById("handoff-count").textContent = handoffs.length + "件";
  wrap.innerHTML = "";
  for (const h of handoffs) {
    const box = el("div", "card");
    const verdict = h.verdict || "（未裁定）";
    box.innerHTML = `
      <div class="card-head">
        <span class="card-title">H${h.id} ${esc(h.agent)} / ${esc(h.task_id)}</span>
        <span class="card-days">${esc(verdict)}</span>
      </div>
    `;
    const openBtn = el("button", "detail-toggle", "指示書・報告を表示");
    const body = el("div", "detail-box");
    body.hidden = true;
    openBtn.onclick = () => {
      body.hidden = !body.hidden;
      openBtn.textContent = body.hidden ? "指示書・報告を表示" : "閉じる";
      if (!body.hidden && !body.dataset.loaded) {
        api(`/api/handoff/${h.id}`).then((full) => {
          // 指示書（brief）・報告（report）を Markdown として描画する
          // （主人の要望「Markdown を描画する」）。
          const briefHtml = full.brief ? mdToHtml(full.brief) : `<p class="panel-note">（なし）</p>`;
          const reportHtml = full.report ? mdToHtml(full.report) : `<p class="panel-note">（まだ報告なし）</p>`;
          body.innerHTML = `<div class="md-body"><h4>指示書</h4>${briefHtml}<h4>報告</h4>${reportHtml}</div>`;
          body.dataset.loaded = "1";
        }).catch((err) => { body.textContent = "読み込めませんでした: " + err.message; });
      }
    };
    box.appendChild(openBtn);
    box.appendChild(body);
    if (!h.verdict && !state.readOnly) {
      const actions = el("div", "card-actions");
      const note = el("input", "ruling-input");
      note.placeholder = "一言（reject は必須）";
      const acceptBtn = el("button", "btn btn-small btn-primary", "accept");
      acceptBtn.onclick = () => handoffVerdict(h.id, "accept", note.value);
      const rejectBtn = el("button", "btn btn-small btn-danger", "reject");
      rejectBtn.onclick = () => handoffVerdict(h.id, "reject", note.value);
      actions.appendChild(note); actions.appendChild(acceptBtn); actions.appendChild(rejectBtn);
      box.appendChild(actions);
    }
    wrap.appendChild(box);
  }
  if (!handoffs.length) wrap.innerHTML = `<p class="panel-note">（なし）</p>`;
}

async function handoffVerdict(id, kind, note) {
  if (kind === "reject" && !note.trim()) { banner("reject には理由が必須です", "warn", 4000); return; }
  try {
    await api(`/api/handoff/${id}/${kind}`, { method: "POST", body: JSON.stringify({ note }) });
    banner(`H${id} を ${kind} しました`, "ok", 4000);
    refresh(true);
  } catch (err) {
    banner("できませんでした: " + err.message, "error");
  }
}

function renderCheck(check) {
  const wrap = document.getElementById("check-body");
  document.getElementById("check-count").textContent = check.ok ? "問題なし" : "指摘あり";
  wrap.innerHTML = "";
  for (const [code, items] of Object.entries(check.results || {})) {
    const bad = items.length > 0;
    const box = el("div", "check-item" + (bad ? " bad" : " ok"));
    box.innerHTML = `<strong>${esc(code)}</strong>: ${esc(check.labels[code] || "")}（${items.length}件）`;
    wrap.appendChild(box);
  }
}

function renderHistory(events) {
  const wrap = document.getElementById("history-list");
  document.getElementById("history-count").textContent = events.length + "件";
  wrap.innerHTML = "";
  for (const e of events) {
    const row = el("div", "row-item");
    row.innerHTML = `
      <span class="row-id">${esc(e.at)}</span>
      <span class="row-title">${esc(e.task_id)}: ${esc(e.from_status || "(new)")} -> ${esc(e.to_status)}（${esc(e.actor)}）${esc(e.note || "")}</span>
    `;
    wrap.appendChild(row);
  }
  if (!events.length) wrap.innerHTML = `<p class="panel-note">（なし）</p>`;
}

async function loadLog() {
  try {
    const data = await api("/api/log");
    state.logData = data;
    renderState(data.state);
    renderDecided(state.board || {}, data.decided);
    // 委譲の note 欄（accept/reject の一言）も要対応と同じ扱い（主人の指摘・2巡目
    // 「他の画面も同じ扱いに」）。
    if (!isEditingWithin("panel-handoff")) renderHandoffs(data.handoffs);
    renderCheck(data.check);
    renderHistory(data.events);
  } catch (err) {
    banner("記録を読み込めませんでした: " + err.message, "error");
  }
}

/* ---------- 4.5 夜勤の作業報告 ----------
 * 夜勤の仕組みそのもの（誰が・いつ書くか）は別担当（`manor.night`）の領分。ここは
 * `home/night/reports/<日付>.md` を**読んで見せるだけ**（読み取り専用）。 */

const NIGHT_STATE_CLASS = { done: "st-done", hold: "st-hold", other: "st-todo" };

function renderNightDateOptions() {
  const sel = document.getElementById("night-date-select");
  if (!sel) return;
  if (!state.nightDates.length) {
    sel.innerHTML = `<option value="">（報告なし）</option>`;
    return;
  }
  sel.innerHTML = state.nightDates.map((d) => `<option value="${esc(d)}">${esc(d)}</option>`).join("");
  if (state.nightDate) sel.value = state.nightDate;
}

function renderNightReport(data) {
  const body = document.getElementById("night-body");
  if (!body) return;
  const parsed = data.parsed || {};
  if (!parsed.ok) {
    // v1 と同じ約束: 構文解析できなければ原文の Markdown 表示に落ちる。
    body.innerHTML = `<p class="panel-note">この日の報告は「## N件名」の形で構造化できませんでした。原文を表示します。</p>
      <div class="md-body">${mdToHtml(data.text || "")}</div>`;
    return;
  }
  const parts = [];
  if (parsed.title) parts.push(`<h3 class="night-title">${esc(parsed.title)}</h3>`);
  if ((parsed.summary || []).length) {
    parts.push(`<div class="night-summary md-body">${parsed.summary.map((p) => mdToHtml(p)).join("")}</div>`);
  }
  parts.push(`<div class="night-tasks">`);
  for (const t of parsed.tasks) {
    const stCls = NIGHT_STATE_CLASS[t.state] || "st-todo";
    parts.push(`<div class="card night-card">`);
    parts.push(`<div class="card-head">
      <span class="card-title">${esc(t.number ? t.number + " " : "")}${esc(t.title)}</span>
      ${t.state ? `<span class="badge-st ${stCls}">${esc(t.state)}</span>` : ""}
    </div>`);
    for (const f of (t.fields || [])) {
      parts.push(`<div class="night-field"><strong>${esc(f.label)}</strong><div class="md-body">${mdToHtml(f.text)}</div></div>`);
    }
    parts.push(`</div>`);
  }
  parts.push(`</div>`);
  body.innerHTML = parts.join("");
}

async function selectNightDate(date) {
  if (!date) return;
  state.nightDate = date;
  renderNightDateOptions();
  const body = document.getElementById("night-body");
  if (body) body.innerHTML = `<p class="panel-note">読み込み中…</p>`;
  try {
    const data = await api("/api/night/reports/" + encodeURIComponent(date));
    state.nightData = data;
    renderNightReport(data);
  } catch (err) {
    if (body) body.innerHTML = `<p class="panel-note">読み込めませんでした: ${esc(err.message)}</p>`;
  }
}

async function loadNightReports() {
  try {
    const res = await api("/api/night/reports");
    state.nightDates = res.dates || [];
    renderNightDateOptions();
    if (!state.nightDates.length) {
      const body = document.getElementById("night-body");
      if (body) body.innerHTML = `<p class="panel-note">（夜勤の報告はまだありません）</p>`;
      return;
    }
    if (!state.nightDate || state.nightDates.indexOf(state.nightDate) < 0) {
      await selectNightDate(state.nightDates[0]);
    }
  } catch (err) {
    banner("夜勤の報告一覧を読み込めませんでした: " + err.message, "error");
  }
}

/* ---------- 5. 家 ---------- */

function renderChef(data) {
  const wrap = document.getElementById("chef-body");
  if (!data.available) { wrap.innerHTML = `<p class="panel-note">料理長（chef）は未導入です</p>`; return; }
  const parts = [];
  parts.push(`<h3 class="sub-head" style="font-size:12.5px;margin:6px 0;">在庫（期限順）</h3>`);
  parts.push(`<div class="rows">${(data.pantry || []).map((p) =>
    `<div class="row-item"><span class="row-title">${esc(p.item)} ${esc(p.qty)}${esc(p.unit)}</span><span class="row-id">期限=${esc(p.expires || "不明")} / ${esc(p.place)}</span></div>`
  ).join("") || '<p class="panel-note">（在庫なし）</p>'}</div>`);
  parts.push(`<h3 class="sub-head" style="font-size:12.5px;margin:14px 0 6px;">買い物リスト（売り場別）</h3>`);
  const aisles = Object.keys(data.shopping_by_aisle || {});
  parts.push(aisles.length ? aisles.map((a) =>
    `<div style="margin-bottom:6px;"><strong style="font-size:12px;">${esc(a)}</strong><div class="rows">${data.shopping_by_aisle[a].map((s) =>
      `<div class="row-item"><span class="row-title">${esc(s.item)}${s.reason ? " — " + esc(s.reason) : ""}</span></div>`).join("")}</div></div>`
  ).join("") : '<p class="panel-note">（買い物リストは空です）</p>');
  parts.push(`<h3 class="sub-head" style="font-size:12.5px;margin:14px 0 6px;">直近7日の食事</h3>`);
  parts.push(`<div class="rows">${(data.meals_recent || []).map((m) =>
    `<div class="row-item"><span class="row-id">${esc(m.date)} ${esc(m.slot)}</span><span class="row-title">${esc(m.dish)}</span>${m.planned ? '<span class="badge-st st-waiting">未確定</span>' : ""}</div>`
  ).join("") || '<p class="panel-note">（記録なし）</p>'}</div>`);
  parts.push(`<h3 class="sub-head" style="font-size:12.5px;margin:14px 0 6px;">好み（taste）</h3>`);
  parts.push(`<div class="rows">${(data.taste || []).map((t) =>
    `<div class="row-item"><span class="row-id">${esc(t.key)}</span><span class="row-title">${esc(t.value)}</span></div>`
  ).join("") || '<p class="panel-note">（未設定）</p>'}</div>`);
  wrap.innerHTML = parts.join("");
}

function renderHouseStaff(data) {
  const wrap = document.getElementById("house-body");
  if (!data.available) { wrap.innerHTML = `<p class="panel-note">家政婦（housekeeper）は未導入です</p>`; return; }
  const today = data.today || {};
  const keys = Object.keys(today);
  if (!keys.length) { wrap.innerHTML = `<p class="panel-note">今日、特に知らせることはありません</p>`; return; }
  const parts = [];
  for (const label of keys) {
    parts.push(`<h3 class="sub-head" style="font-size:12.5px;margin:10px 0 6px;">${esc(label)}</h3>`);
    const rows = today[label];
    const items = rows.map((r) => {
      if (typeof r === "string") return `<div class="row-item"><span class="row-title">${esc(r)}</span></div>`;
      const name = r.name || r.item || "";
      const what = r.what ? `（${esc(r.what)}）` : "";
      const tag = r.overdue_days == null ? "一度も記録なし" : (r.overdue_days >= 0 ? `+${r.overdue_days}日` : `あと${-r.overdue_days}日`);
      const doneBtn = (label.indexOf("当番") >= 0 && r.id && !state.readOnly)
        ? `<button class="btn btn-small" data-chore-done="${r.id}">今日、完了にする</button>` : "";
      return `<div class="row-item"><span class="row-title">${esc(name)}${what}</span><span class="row-id">${esc(tag)}</span>${doneBtn}</div>`;
    }).join("");
    parts.push(`<div class="rows">${items}</div>`);
  }
  wrap.innerHTML = parts.join("");
  for (const btn of wrap.querySelectorAll("[data-chore-done]")) {
    btn.onclick = () => choreDone(btn.dataset.choreDone);
  }
}

async function choreDone(id) {
  try {
    await api(`/api/staff/house/chore/${id}/done`, { method: "POST", body: JSON.stringify({ note: "" }) });
    banner("完了として記録しました", "ok", 3000);
    loadHouseTab("house", true);
  } catch (err) {
    banner("記録できませんでした: " + err.message, "error");
  }
}

function renderMoney(data) {
  const wrap = document.getElementById("money-body");
  if (!data.available) { wrap.innerHTML = `<p class="panel-note">家令（steward）は未導入です</p>`; return; }
  const month = data.month || {};
  const parts = [];
  parts.push(`<h3 class="sub-head" style="font-size:12.5px;margin:6px 0;">今月の分類別合計</h3>`);
  parts.push(`<div class="rows">${(month.expenses || []).map((e) =>
    `<div class="row-item${e.over ? "" : ""}"><span class="row-title">${esc(e.category)}</span><span class="row-id">${e.spent}円${e.budget != null ? `（予算 ${e.budget}円 / 差 ${e.diff > 0 ? "+" : ""}${e.diff}円）` : "（予算未設定）"}</span>${e.over ? '<span class="badge-st st-hold">超過</span>' : ""}</div>`
  ).join("") || '<p class="panel-note">（なし）</p>'}</div>`);
  parts.push(`<h3 class="sub-head" style="font-size:12.5px;margin:14px 0 6px;">定期支払いの期日（14日）</h3>`);
  parts.push(`<div class="rows">${(data.due || []).map((r) =>
    `<div class="row-item"><span class="row-title">${esc(r.name)}</span><span class="row-id">${esc(r.next_due)} ${r.overdue_days > 0 ? "+" + r.overdue_days + "日" : r.overdue_days === 0 ? "本日" : "あと" + (-r.overdue_days) + "日"} / ${r.amount}円</span></div>`
  ).join("") || '<p class="panel-note">（なし）</p>'}</div>`);
  parts.push(`<h3 class="sub-head" style="font-size:12.5px;margin:14px 0 6px;">直近の支出</h3>`);
  parts.push(`<div class="rows">${(data.recent_expenses || []).map((r) =>
    `<div class="row-item"><span class="row-id">${esc(r.date)}</span><span class="row-title">${esc(r.category)} ${esc(r.memo || "")}</span><span class="row-id">${r.kind === "income" ? "+" : "-"}${r.amount}円</span></div>`
  ).join("") || '<p class="panel-note">（なし）</p>'}</div>`);
  wrap.innerHTML = parts.join("");
}

function renderSec(data) {
  const wrap = document.getElementById("sec-body");
  if (!data.available) { wrap.innerHTML = `<p class="panel-note">秘書（secretary）は未導入です</p>`; return; }
  const parts = [];
  parts.push(`<h3 class="sub-head" style="font-size:12.5px;margin:6px 0;">agenda（7日）</h3>`);
  parts.push(`<div class="rows">${(data.agenda || []).map((a) =>
    `<div class="row-item"><span class="row-id">${a.overdue ? "超過 " : ""}${esc(a.date)}</span><span class="row-title">[${esc(a.kind)}] ${esc(a.title)}</span></div>`
  ).join("") || '<p class="panel-note">（なし）</p>'}</div>`);
  parts.push(`<h3 class="sub-head" style="font-size:12.5px;margin:14px 0 6px;">未済の控え</h3>`);
  parts.push(`<div class="rows" id="sec-reminders">${(data.reminders_open || []).map((r) =>
    `<div class="row-item"><span class="row-id">${esc(r.on_date)}${r.at_time ? " " + esc(r.at_time) : ""}</span><span class="row-title">${esc(r.text)}</span>${!state.readOnly ? `<button class="btn btn-small" data-remind-done="${r.id}">済にする</button>` : ""}</div>`
  ).join("") || '<p class="panel-note">（なし）</p>'}</div>`);
  parts.push(`<h3 class="sub-head" style="font-size:12.5px;margin:14px 0 6px;">inbox の未仕分け</h3>`);
  parts.push(`<div class="rows">${(data.inbox_unrouted || []).map((i) =>
    `<div class="row-item"><span class="row-id">${esc(i.received_at)}</span><span class="row-title">${esc(i.ref)}</span></div>`
  ).join("") || '<p class="panel-note">（なし）</p>'}</div>`);
  wrap.innerHTML = parts.join("");
  for (const btn of wrap.querySelectorAll("[data-remind-done]")) {
    btn.onclick = () => remindDone(btn.dataset.remindDone);
  }
}

async function remindDone(id) {
  try {
    await api(`/api/staff/sec/remind/${id}/done`, { method: "POST", body: JSON.stringify({}) });
    banner("済にしました", "ok", 3000);
    loadHouseTab("sec", true);
  } catch (err) {
    banner("できませんでした: " + err.message, "error");
  }
}

async function loadHouseTab(tab, force) {
  if (!force && state.staff[tab]) { paintHouseTab(tab, state.staff[tab]); return; }
  try {
    const data = await api("/api/staff/" + tab);
    state.staff[tab] = data;
    paintHouseTab(tab, data);
  } catch (err) {
    banner("読み込めませんでした: " + err.message, "error");
  }
}

function paintHouseTab(tab, data) {
  if (tab === "chef") renderChef(data);
  else if (tab === "house") renderHouseStaff(data);
  else if (tab === "money") renderMoney(data);
  else if (tab === "sec") renderSec(data);
}

/* ---------- 全体 ---------- */

function renderNav(board) {
  const pending = (board.pending || []).length;
  const stale = (board.pending || []).filter((p) => p.stale).length;
  document.querySelector('[data-count="judge"]').textContent = pending;
  document.querySelector('[data-count="judge"]').classList.toggle("zero", pending === 0);
  document.querySelector('[data-count="running"]').textContent = board.counts.doing;
  document.querySelector('[data-count="plan"]').textContent = (board.projects || []).length;
  const decidedN = state.logData ? state.logData.decided.length : null;
  document.querySelector('[data-count="log"]').textContent = decidedN == null ? "–" : decidedN;
  const alertNode = document.querySelector('[data-alert="judge"]');
  alertNode.hidden = stale === 0;
  alertNode.textContent = "滞留" + stale;
}

function renderPaths(board) {
  document.getElementById("paths").textContent = `本日 ${board.today} 現在。件数: 要対応${board.counts.pending} / 実行中${board.counts.doing} / 常駐${board.counts.resident} / 滞留doing${board.counts.stale}`;
}

function apply(board) {
  state.board = board;
  state.fingerprint = board.fingerprint;
  document.getElementById("today").textContent = "本日 " + board.today;
  // **入力中は要対応の再描画を飛ばす。** データ（state.board）は更新済みなので、
  // フォーカスが外れた次のポーリングで最新の内容が描かれる（isEditingWithin 参照）。
  if (!isEditingWithin("panel-judge")) renderJudge(board);
  renderRunning(board);
  renderProjects(board);
  renderMilestones(board);
  renderNav(board);
  renderPaths(board);
}

async function loadForView(view) {
  if (view === "plan" && state.planTab === "timeline") loadTimeline();
  if (view === "plan" && state.planTab === "projects" && state.board) renderProjects(state.board);
  if (view === "plan" && state.planTab === "milestones" && state.board) renderMilestones(state.board);
  if (view === "log") loadLog();
  if (view === "log" && state.logTab === "night") loadNightReports();
  if (view === "house") loadHouseTab(state.houseTab);
}

async function checkHealth() {
  try {
    const h = await api("/api/health");
    state.readOnly = !!h.read_only;
    if (h.read_only) banner("読み取り専用モードで起動しています（--read-only。裁定・書き込みはできません）", "warn");
    if (h.stale) banner("ダッシュボードのコードが更新されています。サーバを止めて起動し直してください。", "warn");
  } catch (err) { /* health は無くても致命的ではない */ }
}

async function refresh(force) {
  if (state.busy) return;
  state.busy = true;
  try {
    const board = await api("/api/board");
    const previous = state.fingerprint;
    const changed = previous != null && previous !== board.fingerprint;
    apply(board);
    if (state.view === "log") loadLog();
    if (state.view === "log" && state.logTab === "night" && !state.nightDates.length) loadNightReports();
    if (state.view === "house") loadHouseTab(state.houseTab, force);
    if (state.view === "plan" && state.planTab === "timeline") loadTimeline();
    if (changed) {
      // **外部の更新を反映したことを示す。** 執事がファイルを書き換えた／別の端末で
      // 裁定したなど、このタブが起こしていない変化に気づけるように（v1 README §2）。
      setSync("外部の更新を反映しました", "updated");
      setTimeout(() => setSync("同期中", "live"), 4000);
    } else {
      setSync("同期中", "live");
    }
    checkHealth();
  } catch (err) {
    setSync("接続できません", "error");
  } finally {
    state.busy = false;
  }
}

function start() {
  initCompositionGuard();
  document.getElementById("reload").onclick = () => refresh(true);
  for (const btn of document.querySelectorAll(".nav-item")) btn.onclick = () => showView(btn.dataset.view);
  for (const btn of document.querySelectorAll(".plan-tab")) btn.onclick = () => showPlanTab(btn.dataset.planTab);
  for (const btn of document.querySelectorAll(".log-tab")) btn.onclick = () => showLogTab(btn.dataset.logTab);
  const nightSel = document.getElementById("night-date-select");
  if (nightSel) nightSel.onchange = () => selectNightDate(nightSel.value);
  for (const btn of document.querySelectorAll(".house-tab")) btn.onclick = () => showHouseTab(btn.dataset.houseTab);
  for (const btn of document.querySelectorAll('#panel-running .seg-btn[data-mode]')) btn.onclick = () => setTaskMode(btn.dataset.mode);
  setTaskMode(readPref(TASK_MODE_KEY, TASK_MODES, "list"), false);
  for (const btn of document.querySelectorAll('#panel-timeline .seg-btn[data-span]')) {
    btn.onclick = () => {
      state.timelineSpan = Number(btn.dataset.span);
      for (const b of document.querySelectorAll('[data-span]')) b.setAttribute("aria-pressed", b === btn ? "true" : "false");
      loadTimeline();
    };
  }

  window.addEventListener("hashchange", () => {
    const h = parseHash();
    if (h.view === "plan" && h.tab) showPlanTab(h.tab, false);
    if (h.view === "log" && h.tab) showLogTab(h.tab, false);
    if (h.view === "house" && h.tab) showHouseTab(h.tab, false);
    showView(h.view, false);
  });

  const initial = parseHash();
  if (initial.view === "plan" && initial.tab) showPlanTab(initial.tab, false);
  if (initial.view === "log" && initial.tab) showLogTab(initial.tab, false);
  if (initial.view === "house" && initial.tab) showHouseTab(initial.tab, false);
  showView(initial.view, false);

  initSettings();
  checkHealth();
  refresh(true).then(() => {
    state.timer = setInterval(refresh, state.pollMs);
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(false); });

  // **数字キー 1〜5 で画面切り替え、`\` でサイドバー畳み。** 入力中（INPUT/TEXTAREA/
  // contenteditable）は無効にする——コメント欄で "2" と打つたびに画面が切り替わっては困る。
  document.addEventListener("keydown", (ev) => {
    if (ev.ctrlKey || ev.altKey || ev.metaKey) return;
    const tag = (ev.target && ev.target.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA" || (ev.target && ev.target.isContentEditable)) return;
    const idx = ev.key && ev.key.length === 1 ? "12345".indexOf(ev.key) : -1;
    if (idx >= 0 && idx < VIEWS.length) { showView(VIEWS[idx]); ev.preventDefault(); return; }
    if (ev.key === "\\") {
      document.body.classList.toggle("nav-hidden");
      ev.preventDefault();
    }
  });
}

start();
