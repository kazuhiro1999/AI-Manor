/* manor web — セッションの一覧（ADR-025 §10）。
 * 他のPCを含む Claude Code のセッションを、1枚1セッションのカードでグリッドに並べる。
 * 主人が見たいのは4つ: 見出し（何関連か）・今やっていること（段階）・その進捗・主人の次の一手。
 * 活動の状態（作業中／確認待ち／指示待ち／休止）は hook の合図から機械的に決まる（申告ではない）。
 *
 * 見出し・一言・主人の次の一手は各セッションの Claude が書いた自由文なので訳さない
 * （NightPanel と同じ扱い）。
 */
import { useMemo, useState } from "react";
import { usePolling } from "../../app/polling";
import { api } from "../../app/api";
import { ScreenHeader } from "../../components/ScreenHeader";
import { useT, type TranslationKey } from "../../app/i18n";

export type Activity = "review" | "waiting" | "working" | "hold" | "idle" | "closed" | "ended";

export interface RemoteSession {
  session_id: string;
  machine: string;
  activity: Activity;
  title: string;
  phase: string;
  phase_label: string;
  progress: number | null;
  human_next: string;
  human_next_done: boolean;
  held: boolean;
  closed: boolean;
  next_action: string;
  note: string;
  project_id: string | null;
  project_title: string | null;
  linked: boolean;
  task_id: string | null;
  task_title: string | null;
  repo_name: string;
  branch: string | null;
  reported: boolean;
  last_event_at: string | null;
  last_report_at: string | null;
}

export interface SessionsResponse {
  items: RemoteSession[];
  relay: { configured: boolean; ok: boolean; error: string | null };
}

const POLL_MS = 10000;

const ACTIVITY_KEY: Record<Activity, TranslationKey> = {
  review: "sessions.activity.review",
  working: "sessions.activity.working",
  waiting: "sessions.activity.waiting",
  hold: "sessions.activity.hold",
  closed: "sessions.activity.ended",
  idle: "sessions.activity.idle",
  ended: "sessions.activity.ended",
};

const PHASE_KEY: Record<string, TranslationKey> = {
  investigating: "sessions.phase.investigating",
  designing: "sessions.phase.designing",
  implementing: "sessions.phase.implementing",
  fixing: "sessions.phase.fixing",
  implemented: "sessions.phase.implemented",
  testing: "sessions.phase.testing",
  blocked: "sessions.phase.blocked",
  done: "sessions.phase.done",
};

export function minutesAgo(iso: string | null, now: number = Date.now()): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  return Math.max(0, Math.floor((now - t) / 60000));
}

function useAgo() {
  const t = useT();
  return (iso: string | null) => {
    const m = minutesAgo(iso);
    if (m === null) return "—";
    if (m < 1) return t("sessions.ago.now");
    if (m < 60) return t("sessions.ago.minutes", { n: m });
    if (m < 60 * 24) return t("sessions.ago.hours", { n: Math.floor(m / 60) });
    return t("sessions.ago.days", { n: Math.floor(m / 1440) });
  };
}

export type CardAction = "done" | "hold" | "unhold" | "close" | "reopen";

export function SessionCard({ s, onAck }: { s: RemoteSession; onAck?: (id: string, action: CardAction) => void }) {
  const t = useT();
  const ago = useAgo();
  const pct = s.progress ?? null;
  // 見切れた文字は、マウスを乗せると title で全文、カードを押すと折り返して全文を出す。
  const [expanded, setExpanded] = useState(false);
  const linkText = s.linked
    ? [[s.project_id, s.project_title].filter(Boolean).join(" "), s.task_id ? `${s.task_id}${s.task_title ? ` ${s.task_title}` : ""}` : ""]
        .filter(Boolean)
        .join(" › ")
    : t("sessions.unlinked", { repo: s.repo_name });
  return (
    <article
      className={`session-card act-${s.activity} phase-${s.phase || "none"}${expanded ? " is-expanded" : ""}`}
      data-session={s.session_id}
      onClick={() => setExpanded((v) => !v)}
      title={expanded ? undefined : t("sessions.expandHint")}
    >
      <header className="session-top">
        <span className="session-machine">{s.machine || "?"}</span>
        <span className={`session-activity act-${s.activity}`}>
          <span className="session-dot" aria-hidden="true" />
          {t(ACTIVITY_KEY[s.activity])}
        </span>
        <span className="session-ago" title={s.last_event_at || ""}>
          {ago(s.last_event_at)}
        </span>
        {onAck && s.activity !== "ended" && (
          <button
            className="session-close"
            onClick={(e) => {
              e.stopPropagation();
              onAck(s.session_id, s.closed ? "reopen" : "close");
            }}
            title={t(s.closed ? "sessions.close.reopenHint" : "sessions.close.hint")}
          >
            {t(s.closed ? "sessions.close.reopen" : "sessions.close.label")}
          </button>
        )}
      </header>

      <h3 className="session-title" title={s.title || s.repo_name}>{s.title || s.repo_name}</h3>
      <div className={`session-link${s.linked ? "" : " session-unlinked"}`} title={linkText}>
        {linkText}
      </div>

      {expanded && (
        <dl className="session-detail" onClick={(e) => e.stopPropagation()}>
          <dt>{t("sessions.detail.repo")}</dt>
          <dd>{s.repo_name}{s.branch ? ` (${s.branch})` : ""}</dd>
          <dt>{t("sessions.detail.session")}</dt>
          <dd>{s.session_id}</dd>
        </dl>
      )}
      {s.reported ? (
        <>
          <div className="session-doing">
            <span className={`session-phase phase-${s.phase || "none"}`}>
              {s.phase ? t(PHASE_KEY[s.phase] ?? "sessions.phase.unknown") : "—"}
            </span>
            {s.note && <span className="session-note" title={s.note}>{s.note}</span>}
          </div>
          <div className="session-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100}
               aria-valuenow={pct ?? undefined}>
            <div className="session-bar" style={{ width: `${pct ?? 0}%` }} />
            <span className="session-pct">{pct === null ? "—" : `${pct}%`}</span>
          </div>
          <div className={`session-next${s.human_next && !s.human_next_done ? " has-next" : ""}${s.human_next_done ? " is-done" : ""}`}>
            <span className="session-next-label">{t("sessions.next.label")}</span>
            <span className="session-next-text" title={s.human_next || undefined}>
              {s.human_next || t("sessions.next.none")}
            </span>
            {s.human_next && !s.human_next_done && onAck && (
              <span className="session-actions">
                <button
                  className="session-ack"
                  onClick={(e) => {
                    e.stopPropagation();
                    onAck(s.session_id, "done");
                  }}
                  title={t("sessions.next.doneHint")}
                >
                  {t("sessions.next.done")}
                </button>
                <button
                  className="session-hold"
                  onClick={(e) => {
                    e.stopPropagation();
                    onAck(s.session_id, s.held ? "unhold" : "hold");
                  }}
                  title={t(s.held ? "sessions.next.unholdHint" : "sessions.next.holdHint")}
                >
                  {t(s.held ? "sessions.next.unhold" : "sessions.next.hold")}
                </button>
              </span>
            )}
            {s.human_next_done && <span className="session-done-mark">{t("sessions.next.doneMark")}</span>}
          </div>
          {s.next_action && (
            <div className={`session-recommend${s.human_next_done || !s.human_next ? " is-now" : ""}`}>
              <span className="session-next-label">{t("sessions.recommend.label")}</span>
              <span className="session-next-text" title={s.next_action}>{s.next_action}</span>
            </div>
          )}
          <footer className="session-foot">{t("sessions.reportedAgo", { ago: ago(s.last_report_at) })}</footer>
        </>
      ) : (
        <div className="session-waiting">{t("sessions.waitingReport")}</div>
      )}
    </article>
  );
}

/** 状態ごとの並び（主人の指定 2026-10-09）。終了には「終了にする」を押したもの（closed）も入れる。 */
export const ACTIVITY_GROUPS: Activity[] = ["review", "waiting", "working", "hold", "idle", "ended"];
const ACTIVITY_RANK: Record<Activity, number> = {
  review: 0, waiting: 1, working: 2, hold: 3, idle: 4, closed: 5, ended: 5,
};

export type GroupMode = "activity" | "machine";

export interface Group {
  key: string;
  label: string;
  items: RemoteSession[];
}

function newestFirst(a: RemoteSession, b: RemoteSession): number {
  return (Date.parse(b.last_event_at || "") || 0) - (Date.parse(a.last_event_at || "") || 0);
}

/** 並べ方に応じてまとまりを作る（空のまとまりは出さない）。 */
export function groupSessions(items: RemoteSession[], mode: GroupMode, label: (a: Activity) => string): Group[] {
  if (mode === "machine") {
    const machines = Array.from(new Set(items.map((s) => s.machine))).sort();
    return machines.map((m) => ({
      key: `machine:${m}`,
      label: m,
      items: items
        .filter((s) => s.machine === m)
        .sort((a, b) => ACTIVITY_RANK[a.activity] - ACTIVITY_RANK[b.activity] || newestFirst(a, b)),
    }));
  }
  return ACTIVITY_GROUPS.map((g) => ({
    key: `activity:${g}`,
    label: label(g),
    items: items.filter((s) => (s.activity === "closed" ? "ended" : s.activity) === g).sort(newestFirst),
  })).filter((g) => g.items.length > 0);
}

const PREFS_KEY = "manor.sessions.view";

interface ViewPrefs {
  mode: GroupMode;
  hidden: string[];
}

function loadPrefs(): ViewPrefs {
  try {
    const raw = window.localStorage.getItem(PREFS_KEY);
    if (raw) {
      const v = JSON.parse(raw) as Partial<ViewPrefs>;
      return { mode: v.mode === "machine" ? "machine" : "activity", hidden: Array.isArray(v.hidden) ? v.hidden : [] };
    }
  } catch {
    /* 保存できない環境では毎回既定で開く */
  }
  return { mode: "activity", hidden: [] };
}

function savePrefs(p: ViewPrefs): void {
  try {
    window.localStorage.setItem(PREFS_KEY, JSON.stringify(p));
  } catch {
    /* 同上 */
  }
}

export function SessionsScreen() {
  const t = useT();
  const [showEnded, setShowEnded] = useState(false);
  const [prefs, setPrefs] = useState<ViewPrefs>(loadPrefs);
  const { data, error, loading, reload } = usePolling<SessionsResponse>(
    `/sessions?include_ended=${showEnded ? 1 : 0}`,
    POLL_MS
  );
  const items = data?.items ?? [];
  const groups = useMemo(
    () => groupSessions(items, prefs.mode, (a) => t(ACTIVITY_KEY[a])),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [items, prefs.mode]
  );
  const reviewCount = items.filter((s) => s.activity === "review").length;
  const update = (next: ViewPrefs) => {
    setPrefs(next);
    savePrefs(next);
  };
  const toggle = (key: string) =>
    update({
      ...prefs,
      hidden: prefs.hidden.includes(key) ? prefs.hidden.filter((k) => k !== key) : [...prefs.hidden, key],
    });
  const ack = async (id: string, action: CardAction) => {
    const base = `/sessions/${encodeURIComponent(id)}`;
    try {
      if (action === "done") await api(`${base}/ack`, { method: "POST" });
      else if (action === "hold" || action === "unhold")
        await api(`${base}/hold`, { method: action === "hold" ? "POST" : "DELETE" });
      else await api(`${base}/close`, { method: action === "close" ? "POST" : "DELETE" });
    } finally {
      await reload();
    }
  };

  return (
    <div className="view" id="view-sessions">
      <ScreenHeader title={t("nav.sessions")} description={t("sessions.description")} />
      <div className="sessions-toolbar">
        <span className="sessions-mode" role="group" aria-label={t("sessions.mode.label")}>
          <button
            className={`chip${prefs.mode === "activity" ? " active" : ""}`}
            onClick={() => update({ ...prefs, mode: "activity" })}
          >
            {t("sessions.mode.activity")}
          </button>
          <button
            className={`chip${prefs.mode === "machine" ? " active" : ""}`}
            onClick={() => update({ ...prefs, mode: "machine" })}
          >
            {t("sessions.mode.machine")}
          </button>
        </span>
        <span className="sessions-spacer" />
        {reviewCount > 0 && <span className="sessions-yourturn">{t("sessions.yourTurnCount", { n: reviewCount })}</span>}
        <label className="sessions-ended">
          <input type="checkbox" checked={showEnded} onChange={(e) => setShowEnded(e.target.checked)} />
          {t("sessions.showEnded")}
        </label>
      </div>
      {data && !data.relay.configured && <p className="panel-note">{t("sessions.relay.notConfigured")}</p>}
      {data && data.relay.configured && !data.relay.ok && (
        <p className="panel-note is-warn">{t("sessions.relay.error", { error: data.relay.error || "" })}</p>
      )}
      {error && <p className="panel-note is-warn">{error}</p>}
      {!loading && items.length === 0 && <p className="panel-note">{t("sessions.empty")}</p>}
      {groups.map((g) => {
        const hidden = prefs.hidden.includes(g.key);
        return (
          <section key={g.key} className={`sessions-group${hidden ? " is-hidden" : ""}`} data-group={g.key}>
            <button className="sessions-group-head" onClick={() => toggle(g.key)} aria-expanded={!hidden}>
              <span className="sessions-group-caret" aria-hidden="true">{hidden ? "▸" : "▾"}</span>
              <span className="sessions-group-label">{g.label}</span>
              <span className="sessions-group-count">{g.items.length}</span>
            </button>
            {!hidden && (
              <div className="sessions-grid">
                {g.items.map((s) => (
                  <SessionCard key={s.session_id} s={s} onAck={ack} />
                ))}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
