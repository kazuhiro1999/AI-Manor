import { useState } from "react";
import { Route, Routes } from "react-router-dom";
import { usePolling } from "../../app/polling";
import { useEditingGuard } from "../../app/editing";
import { api, ApiError } from "../../app/api";
import type { Handoff, LogData } from "../../app/types";
import { TabLink } from "../../components/TabLink";
import { Markdown } from "../../components/Markdown";
import { useToast } from "../../components/Toast";
import { NightPanel } from "../night/NightPanel";
import { useT, type TranslationKey } from "../../app/i18n";

export function Log({ readOnly }: { readOnly: boolean }) {
  const t = useT();
  return (
    <div className="view" id="view-log">
      <div className="seg log-tabs" role="tablist" aria-label={t("tasks.log.tabsAria")}>
        <TabLink to="state" label={t("tasks.log.tab.state")} />
        <TabLink to="decided" label={t("tasks.log.tab.decided")} />
        <TabLink to="handoff" label={t("tasks.log.tab.handoff")} />
        <TabLink to="check" label={t("tasks.log.tab.check")} />
        <TabLink to="history" label={t("tasks.log.tab.history")} />
        <TabLink to="night" label={t("nav.night")} />
      </div>
      <Routes>
        <Route index element={<LogState />} />
        <Route path="state" element={<LogState />} />
        <Route path="decided" element={<LogDecided />} />
        <Route path="handoff" element={<LogHandoff readOnly={readOnly} />} />
        <Route path="check" element={<LogCheck />} />
        <Route path="history" element={<LogHistory />} />
        <Route path="night" element={<NightPanel />} />
      </Routes>
    </div>
  );
}

function useLog() {
  const { ref, isEditing: _isEditing } = useEditingGuard<HTMLDivElement>();
  const { data, error, reload } = usePolling<LogData>("/tasks/log", 5000, ref as React.RefObject<HTMLElement>);
  return { ref, data, error, reload };
}

function LogState() {
  const t = useT();
  const { data, error } = useLog();
  if (error) return <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>;
  return (
    <section className="panel" id="panel-state">
      <div className="panel-head">
        <h2>{t("tasks.log.state.heading")}</h2>
      </div>
      <div id="state-body" className="state-body">
        {!data ? (
          t("common.loading")
        ) : data.state ? (
          <Markdown text={data.state} />
        ) : (
          <p className="panel-note">{t("tasks.log.state.noProjection")}</p>
        )}
      </div>
    </section>
  );
}

const DECISION_STATUS_KEY: Record<string, TranslationKey> = {
  approved: "tasks.judge.approve",
  rejected: "tasks.judge.reject",
  modified: "tasks.judge.modify",
};

function LogDecided() {
  const t = useT();
  const { data, error } = useLog();
  const [open, setOpen] = useState<Set<string>>(new Set());
  if (error) return <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>;
  const decided = data?.decided || [];
  const toggle = (id: string) => {
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  const STATUS_CLASS: Record<string, string> = { approved: "st-done", rejected: "st-withdrawn", modified: "st-waiting" };
  return (
    <section className="panel" id="panel-decided">
      <div className="panel-head">
        <h2>{t("tasks.log.decided.heading")}</h2>
        <span className="count" id="decided-count">
          {t("component.foldBlock.count", { count: decided.length })}
        </span>
      </div>
      <div id="decided-list" className="rows">
        {!decided.length && <p className="panel-note">{t("common.none")}</p>}
        {decided.map((d) => {
          const isOpen = open.has("decided:" + d.id);
          return (
            <div key={d.id}>
              <div className="row-item">
                <span className="row-id">{d.id}</span>
                <span className={"badge-st " + (STATUS_CLASS[d.status] || "st-waiting")}>
                  {d.status in DECISION_STATUS_KEY ? t(DECISION_STATUS_KEY[d.status]) : d.status}
                </span>
                <span className="row-title">{d.title}</span>
              </div>
              <button className="detail-toggle" type="button" onClick={() => toggle("decided:" + d.id)}>
                {isOpen ? t("tasks.judge.hideDetail") : t("tasks.judge.showDetail")}
              </button>
              {isOpen && (
                <div className="detail-box">
                  {t("tasks.log.decided.rulingLabel", { text: d.ruling || t("common.none") })}
                  {"\n"}
                  {t("tasks.log.decided.backgroundLabel", { text: d.background || t("common.none") })}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function LogHandoff({ readOnly }: { readOnly: boolean }) {
  const t = useT();
  const { data, error, reload } = useLog();
  const [openId, setOpenId] = useState<number | null>(null);
  const [loaded, setLoaded] = useState<Map<number, Handoff>>(new Map());
  const { show } = useToast();
  if (error) return <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>;
  const handoffs = data?.handoffs || [];

  const toggle = async (h: Handoff) => {
    if (openId === h.id) {
      setOpenId(null);
      return;
    }
    setOpenId(h.id);
    if (!loaded.has(h.id)) {
      try {
        const full = await api<Handoff>(`/tasks/handoff/${h.id}`);
        setLoaded((prev) => new Map(prev).set(h.id, full));
      } catch {
        /* ignore */
      }
    }
  };

  const verdict = async (h: Handoff, kind: "accept" | "reject", note: string) => {
    if (kind === "reject" && !note.trim()) {
      show(t("tasks.log.handoff.rejectRequiresNote"), "warn", 4000);
      return;
    }
    try {
      await api(`/tasks/handoff/${h.id}/${kind}`, { method: "POST", body: { note } });
      show(t("tasks.log.handoff.verdictToast", { id: h.id, kind }), "ok", 4000);
      reload();
    } catch (err) {
      show(t("tasks.log.handoff.verdictFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    }
  };

  return (
    <section className="panel" id="panel-handoff">
      <div className="panel-head">
        <h2>{t("tasks.log.handoff.heading")}</h2>
        <span className="count" id="handoff-count">
          {t("component.foldBlock.count", { count: handoffs.length })}
        </span>
      </div>
      <p className="panel-note">{t("tasks.log.handoff.hint")}</p>
      <div id="handoff-list" className="rows">
        {!handoffs.length && <p className="panel-note">{t("common.none")}</p>}
        {handoffs.map((h) => {
          const isOpen = openId === h.id;
          const full = loaded.get(h.id);
          return <HandoffCard key={h.id} h={h} isOpen={isOpen} full={full} readOnly={readOnly} onToggle={() => toggle(h)} onVerdict={verdict} />;
        })}
      </div>
    </section>
  );
}

function HandoffCard({
  h,
  isOpen,
  full,
  readOnly,
  onToggle,
  onVerdict,
}: {
  h: Handoff;
  isOpen: boolean;
  full?: Handoff;
  readOnly: boolean;
  onToggle: () => void;
  onVerdict: (h: Handoff, kind: "accept" | "reject", note: string) => void;
}) {
  const t = useT();
  const [note, setNote] = useState("");
  return (
    <div className="card">
      <div className="card-head">
        <span className="card-title">
          H{h.id} {h.agent} / {h.task_id}
        </span>
        <span className="card-days">{h.verdict || t("tasks.log.handoff.unruled")}</span>
      </div>
      <button className="detail-toggle" type="button" onClick={onToggle}>
        {t("tasks.log.handoff.showToggle")}
      </button>
      {isOpen && (
        <div className="detail-box">
          {!full ? (
            t("common.loading")
          ) : (
            <>
              <h4>{t("tasks.log.handoff.briefHeading")}</h4>
              <Markdown text={full.brief} />
              <h4>{t("tasks.log.handoff.reportHeading")}</h4>
              <Markdown text={full.report} />
            </>
          )}
        </div>
      )}
      {!h.verdict && !readOnly && (
        <div className="card-actions">
          <input
            className="ruling-input"
            placeholder={t("tasks.log.handoff.notePlaceholder")}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <button className="btn btn-small btn-primary" type="button" onClick={() => onVerdict(h, "accept", note)}>
            accept
          </button>
          <button className="btn btn-small btn-danger" type="button" onClick={() => onVerdict(h, "reject", note)}>
            reject
          </button>
        </div>
      )}
    </div>
  );
}

function LogCheck() {
  const t = useT();
  const { data, error } = useLog();
  if (error) return <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>;
  if (!data) return <p className="panel-note">{t("common.loading")}</p>;
  const check = data.check;
  return (
    <section className="panel" id="panel-check">
      <div className="panel-head">
        <h2>{t("tasks.log.check.heading")}</h2>
        <span className="count" id="check-count">
          {check.ok ? t("tasks.log.check.ok") : t("tasks.log.check.issues")}
        </span>
      </div>
      <p className="panel-note">
        {t("tasks.log.check.hintPrefix")} <code>manor check</code> {t("tasks.log.check.hintSuffix")}
      </p>
      <div id="check-body">
        {Object.entries(check.results || {}).map(([code, items]) => {
          const bad = (items as unknown[]).length > 0;
          return (
            <div key={code} className={"check-item" + (bad ? " bad" : " ok")}>
              <strong>{code}</strong>: {check.labels[code] || ""}
              {t("tasks.log.check.itemSuffix", { n: (items as unknown[]).length })}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function LogHistory() {
  const t = useT();
  const { data, error } = useLog();
  if (error) return <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>;
  const events = data?.events || [];
  return (
    <section className="panel" id="panel-history">
      <div className="panel-head">
        <h2>{t("tasks.log.history.heading")}</h2>
        <span className="count" id="history-count">
          {t("component.foldBlock.count", { count: events.length })}
        </span>
      </div>
      <p className="panel-note">{t("tasks.log.history.hint")}</p>
      <div id="history-list" className="rows">
        {!events.length && <p className="panel-note">{t("common.none")}</p>}
        {events.map((e) => (
          <div className="row-item" key={e.id}>
            <span className="row-id">{e.at}</span>
            <span className="row-title">
              {e.task_id}: {e.from_status || "(new)"} -&gt; {e.to_status}
              {t("tasks.log.history.actorSuffix", { actor: e.actor })}
              {e.note || ""}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
