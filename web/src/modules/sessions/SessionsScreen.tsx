/* manor web — セッションの一覧（ADR-025 §10）。
 * 他のPCを含む Claude Code のセッションを、1枚1セッションのカードでグリッドに並べる。
 * 主人が見たいのは4つ: 見出し（何関連か）・今やっていること（段階）・その進捗・主人の次の一手。
 * 活動の状態（作業中／あなたの番／休止）は hook の合図から機械的に決まる（申告ではない）。
 *
 * 見出し・一言・主人の次の一手は各セッションの Claude が書いた自由文なので訳さない
 * （NightPanel と同じ扱い）。
 */
import { useMemo, useState } from "react";
import { usePolling } from "../../app/polling";
import { ScreenHeader } from "../../components/ScreenHeader";
import { useT, type TranslationKey } from "../../app/i18n";

export type Activity = "your_turn" | "working" | "idle" | "ended";

export interface RemoteSession {
  session_id: string;
  machine: string;
  activity: Activity;
  title: string;
  phase: string;
  phase_label: string;
  progress: number | null;
  human_next: string;
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
  your_turn: "sessions.activity.yourTurn",
  working: "sessions.activity.working",
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

export function SessionCard({ s }: { s: RemoteSession }) {
  const t = useT();
  const ago = useAgo();
  const pct = s.progress ?? null;
  return (
    <article className={`session-card act-${s.activity} phase-${s.phase || "none"}`} data-session={s.session_id}>
      <header className="session-top">
        <span className="session-machine">{s.machine || "?"}</span>
        <span className={`session-activity act-${s.activity}`}>
          <span className="session-dot" aria-hidden="true" />
          {t(ACTIVITY_KEY[s.activity])}
        </span>
        <span className="session-ago" title={s.last_event_at || ""}>
          {ago(s.last_event_at)}
        </span>
      </header>

      <h3 className="session-title">{s.title || s.repo_name}</h3>
      <div className="session-link">
        {s.linked ? (
          <>
            <span>{[s.project_id, s.project_title].filter(Boolean).join(" ")}</span>
            {s.task_id && (
              <span className="session-task">
                {" › "}
                {s.task_id}
                {s.task_title ? ` ${s.task_title}` : ""}
              </span>
            )}
          </>
        ) : (
          <span className="session-unlinked">{t("sessions.unlinked", { repo: s.repo_name })}</span>
        )}
      </div>

      {s.reported ? (
        <>
          <div className="session-doing">
            <span className={`session-phase phase-${s.phase || "none"}`}>
              {s.phase ? t(PHASE_KEY[s.phase] ?? "sessions.phase.unknown") : "—"}
            </span>
            {s.note && <span className="session-note">{s.note}</span>}
          </div>
          <div className="session-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100}
               aria-valuenow={pct ?? undefined}>
            <div className="session-bar" style={{ width: `${pct ?? 0}%` }} />
            <span className="session-pct">{pct === null ? "—" : `${pct}%`}</span>
          </div>
          <div className={`session-next${s.human_next ? " has-next" : ""}`}>
            <span className="session-next-label">{t("sessions.next.label")}</span>
            <span>{s.human_next || t("sessions.next.none")}</span>
          </div>
          <footer className="session-foot">{t("sessions.reportedAgo", { ago: ago(s.last_report_at) })}</footer>
        </>
      ) : (
        <div className="session-waiting">{t("sessions.waitingReport")}</div>
      )}
    </article>
  );
}

export function SessionsScreen() {
  const t = useT();
  const [showEnded, setShowEnded] = useState(false);
  const [machine, setMachine] = useState<string | null>(null);
  const { data, error, loading } = usePolling<SessionsResponse>(
    `/sessions?include_ended=${showEnded ? 1 : 0}`,
    POLL_MS
  );
  const items = data?.items ?? [];
  const machines = useMemo(() => Array.from(new Set(items.map((s) => s.machine))).sort(), [items]);
  const shown = machine ? items.filter((s) => s.machine === machine) : items;
  const yourTurn = items.filter((s) => s.activity === "your_turn").length;

  return (
    <div className="view" id="view-sessions">
      <ScreenHeader title={t("nav.sessions")} description={t("sessions.description")} />
      <div className="sessions-toolbar">
        <button className={`chip${machine === null ? " active" : ""}`} onClick={() => setMachine(null)}>
          {t("sessions.filter.all", { n: items.length })}
        </button>
        {machines.map((m) => (
          <button key={m} className={`chip${machine === m ? " active" : ""}`} onClick={() => setMachine(m)}>
            {m}
          </button>
        ))}
        <span className="sessions-spacer" />
        {yourTurn > 0 && <span className="sessions-yourturn">{t("sessions.yourTurnCount", { n: yourTurn })}</span>}
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
      {!loading && shown.length === 0 && <p className="panel-note">{t("sessions.empty")}</p>}
      <div className="sessions-grid">
        {shown.map((s) => (
          <SessionCard key={s.session_id} s={s} />
        ))}
      </div>
    </div>
  );
}
