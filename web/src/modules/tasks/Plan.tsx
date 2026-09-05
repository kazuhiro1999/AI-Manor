import { useMemo, useState } from "react";
import { Route, Routes } from "react-router-dom";
import { TabLink } from "../../components/TabLink";
import { usePolling } from "../../app/polling";
import { api, ApiError } from "../../app/api";
import type { Board, Milestone, Project, Timeline, TimelineEvent, TimelineLane } from "../../app/types";
import { daysLeftClass, daysLeftText, projectLabel } from "./utils";
import { useToast } from "../../components/Toast";
import { formatDay, useT, type TranslationKey } from "../../app/i18n";
import { PRESET_LABEL_KEY } from "../../app/setupChoices";

// PlanProjects と ProjectEditRow の両方が使うので module scope に置く（ADR-013 D1）。
const PROJECT_STATUS_KEY: Record<string, TranslationKey> = {
  active: "projectStatus.active",
  paused: "projectStatus.paused",
  done: "projectStatus.done",
};

const TL_KIND_KEY: Record<string, TranslationKey> = {
  milestone: "tlKind.milestone",
  deadline: "tlKind.deadline",
  remind: "tlKind.remind",
  task: "tlKind.task",
};

export function Plan({ readOnly }: { readOnly: boolean }) {
  const t = useT();
  return (
    <div className="view" id="view-plan">
      <div className="seg log-tabs" role="tablist" aria-label={t("tasks.plan.tabsAria")}>
        <TabLink to="timeline" label={t("tasks.plan.tab.timeline")} />
        <TabLink to="projects" label={t("tasks.plan.tab.projects")} />
        <TabLink to="milestones" label={t("tasks.plan.tab.milestones")} />
      </div>
      <Routes>
        <Route index element={<PlanTimeline readOnly={readOnly} />} />
        <Route path="timeline" element={<PlanTimeline readOnly={readOnly} />} />
        <Route path="projects" element={<PlanProjects />} />
        <Route path="milestones" element={<PlanMilestones />} />
      </Routes>
    </div>
  );
}

function PlanTimeline({ readOnly }: { readOnly: boolean }) {
  const t = useT();
  const [span, setSpan] = useState<7 | 35>(7);
  const days = span > 7 ? 70 : 7;
  const { data, error, reload } = usePolling<Timeline>(`/tasks/timeline?days=${days}`, 5000);
  const [openRef, setOpenRef] = useState<string | null>(null);
  const { show } = useToast();

  const { scheduled, loose } = useMemo(() => {
    if (!data) return { scheduled: [] as TimelineLane[], loose: [] as TimelineLane[] };
    let sc = data.lanes.filter((ln) => (ln.events || []).some((e) => e.start_days <= span - 1 && e.end_days >= 0));
    const lo = data.lanes.filter((ln) => !sc.includes(ln));
    sc = sc.slice().sort((a, b) => {
      const aNone = !a.project_id;
      const bNone = !b.project_id;
      if (aNone !== bNone) return aNone ? 1 : -1;
      const aEvents = a.events.filter((e) => e.start_days <= span - 1 && e.end_days >= 0);
      const bEvents = b.events.filter((e) => e.start_days <= span - 1 && e.end_days >= 0);
      const aStart = Math.min(...aEvents.map((e) => e.start_days));
      const bStart = Math.min(...bEvents.map((e) => e.start_days));
      if (aStart !== bStart) return aStart - bStart;
      return (b.priority || 0) - (a.priority || 0);
    });
    return { scheduled: sc, loose: lo };
  }, [data, span]);

  if (error) return <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>;
  if (!data) return <p className="panel-note">{t("common.loading")}</p>;

  const isMonth = span > 7;
  const cols = isMonth ? Math.ceil(span / 7) : span;
  const today = new Date(data.today + "T00:00:00");

  let openEvent: TimelineEvent | null = null;
  if (openRef) {
    outer: for (const ln of scheduled) {
      for (const e of ln.events || []) {
        if (e.kind + ":" + e.ref === openRef) {
          openEvent = e;
          break outer;
        }
      }
    }
  }

  const remindDone = async (e: TimelineEvent) => {
    try {
      await api(`/secretary/reminder/${encodeURIComponent(String(e.ref))}/done`, { method: "POST", body: { note: "" } });
      show(t("tasks.timeline.reminderDone"), "ok", 3000);
      reload();
    } catch (err) {
      show(t("tasks.timeline.reminderUpdateFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    }
  };

  return (
    <section className="panel panel-primary" id="panel-timeline">
      <div className="panel-head">
        <h2>{t("tasks.timeline.heading")}</h2>
        <span className="count" id="timeline-count">
          {t("tasks.timeline.count", { n: scheduled.length })}
        </span>
        <div className="seg" role="group" aria-label={t("tasks.timeline.periodAria")}>
          <button className="seg-btn" type="button" aria-pressed={span === 7} onClick={() => setSpan(7)}>
            {t("tasks.timeline.week")}
          </button>
          <button className="seg-btn" type="button" aria-pressed={span === 35} onClick={() => setSpan(35)}>
            {t("tasks.timeline.month")}
          </button>
        </div>
      </div>
      <div id="timeline" className="timeline">
        <div className="tl-table" style={{ ["--tl-cols" as string]: cols }}>
          <div className="tl-row tl-header">
            <div className="tl-name" />
            <div className="tl-track" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
              {Array.from({ length: cols }).map((_, i) => {
                if (isMonth) return <div className="tl-head" key={i}>{t("tasks.timeline.weekNum", { n: i + 1 })}</div>;
                const d = new Date(today.getTime() + i * 86400000);
                return (
                  <div className={"tl-head" + (i === 0 ? " is-today" : "")} key={i}>
                    {d.getMonth() + 1}/{d.getDate()}
                  </div>
                );
              })}
            </div>
          </div>
          {scheduled.map((lane) => (
            <div className="tl-row" key={lane.id}>
              <div className="tl-name">
                <div className="tl-name-main">{lane.name}</div>
                <div className="tl-next">{t("tasks.timeline.eventCount", { n: (lane.events || []).length })}</div>
              </div>
              <div className="tl-track" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
                {(lane.events || []).map((e, idx) => {
                  const startCol = isMonth ? Math.floor(e.start_days / 7) : e.start_days;
                  const endCol = isMonth ? Math.floor(e.end_days / 7) : e.end_days;
                  if (endCol < 0 || startCol > cols - 1) return null;
                  const s = Math.max(0, startCol);
                  const en = Math.min(cols - 1, endCol);
                  return (
                    <div
                      key={idx}
                      className={
                        `tl-bar tl-${e.kind}` +
                        (e.approximate ? " tl-approx" : "") +
                        (e.overdue ? " tl-overdue" : "") +
                        (e.done ? " tl-done" : "")
                      }
                      style={{ left: `calc(${s} * 100% / ${cols})`, width: `calc(${en - s + 1} * 100% / ${cols} - 6px)` }}
                      title={e.title}
                      onClick={() => setOpenRef(e.kind + ":" + e.ref)}
                    >
                      <span className="tl-what">{e.title}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </div>
      {openEvent && (
        <div id="timeline-detail" className="tl-detail">
          <strong>
            [{openEvent.kind in TL_KIND_KEY ? t(TL_KIND_KEY[openEvent.kind]) : openEvent.kind}] {openEvent.title}
          </strong>
          {"\n"}
          {openEvent.start} 〜 {openEvent.end}
          {openEvent.approximate ? t("tasks.timeline.approxNote") : ""}
          {"\n\n"}
          {openEvent.detail || ""}
          {openEvent.kind === "remind" && !readOnly && (
            <div className="card-actions">
              <button className={"btn btn-small " + (openEvent.done ? "btn-ghost" : "btn-primary")} type="button" onClick={() => remindDone(openEvent!)}>
                {openEvent.done ? t("tasks.timeline.markUndone") : t("tasks.timeline.markDone")}
              </button>
            </div>
          )}
        </div>
      )}
      <div id="timeline-loose" className="tl-loose">
        {loose.length > 0 && (
          <>
            <div className="status-block-head">{t("tasks.timeline.looseHeading")}</div>
            {loose
              .slice()
              .sort((a, b) => (a.priority || 0) - (b.priority || 0))
              .map((ln) => (
                <div className="tl-loose-item" key={ln.id}>
                  {ln.name}
                </div>
              ))}
          </>
        )}
      </div>
    </section>
  );
}

function PlanProjects() {
  const t = useT();
  const { data: board, error, reload } = usePolling<Board>("/tasks/board", 5000);
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  if (error) return <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>;
  if (!board) return <p className="panel-note">{t("common.loading")}</p>;
  const rows = board.projects || [];
  return (
    <section className="panel" id="panel-projects">
      <div className="panel-head">
        <h2>{t("tasks.projects.heading")}</h2>
        <span className="count" id="projects-count">
          {t("component.foldBlock.count", { count: rows.length })}
        </span>
        <button className="btn btn-small btn-primary" style={{ marginLeft: "auto" }} type="button" onClick={() => setAdding(true)}>
          {t("tasks.projects.add")}
        </button>
      </div>
      <p className="panel-note">{t("tasks.projects.hint")}</p>
      {adding && (
        <ProjectAddForm
          onClose={() => setAdding(false)}
          onSaved={() => {
            setAdding(false);
            reload();
          }}
        />
      )}
      <div className="table-scroll">
        <table id="projects-table" className="grid">
          <thead>
            <tr>
              <th>{t("tasks.projects.col.code")}</th>
              <th>{t("tasks.projects.col.project")}</th>
              <th>{t("tasks.projects.col.kind")}</th>
              <th>{t("tasks.projects.col.priority")}</th>
              <th>{t("tasks.projects.col.preset")}</th>
              <th>{t("tasks.projects.col.status")}</th>
              <th>{t("tasks.projects.col.nextAction")}</th>
              <th>{t("tasks.projects.col.due")}</th>
              <th>{t("tasks.projects.col.daysLeft")}</th>
              <th>{t("tasks.projects.col.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((p) =>
              editingId === p.id ? (
                <tr key={p.id}>
                  <td colSpan={10}>
                    <ProjectEditForm
                      project={p}
                      onClose={() => setEditingId(null)}
                      onSaved={() => {
                        setEditingId(null);
                        reload();
                      }}
                    />
                  </td>
                </tr>
              ) : (
                <tr key={p.id}>
                  <td className="col-nowrap">{p.code}</td>
                  <td className="col-wide">{p.title}</td>
                  <td className="col-nowrap">{p.kind || "—"}</td>
                  <td className="col-nowrap">{p.priority}</td>
                  <td className="col-nowrap">{p.preset in PRESET_LABEL_KEY ? t(PRESET_LABEL_KEY[p.preset]) : p.preset}</td>
                  <td className="col-nowrap">{p.status in PROJECT_STATUS_KEY ? t(PROJECT_STATUS_KEY[p.status]) : p.status}</td>
                  <td className="col-wide">{p.next_action || "—"}</td>
                  <td className="col-nowrap">{p.due || "—"}</td>
                  <td className={"days-left col-nowrap " + daysLeftClass(p.days_left)}>{daysLeftText(p.days_left)}</td>
                  <td className="col-nowrap">
                    <button className="btn btn-small" type="button" onClick={() => setEditingId(p.id)}>
                      {t("tasks.projects.edit")}
                    </button>
                  </td>
                </tr>
              )
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** ADR-013 D1: プロジェクトの作成。**code はここでだけ**受け取る——作成後は
 * 変更できない（`ProjectEditForm` に code の入力欄が無いのはそのため）。 */
function ProjectAddForm({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const t = useT();
  const { show } = useToast();
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [kind, setKind] = useState("");
  const [priority, setPriority] = useState("3");
  const [preset, setPreset] = useState("standard");
  const [status, setStatus] = useState("active");
  const [due, setDue] = useState("");
  const [nextAction, setNextAction] = useState("");
  const [busy, setBusy] = useState(false);

  const save = async () => {
    const trimmedCode = code.trim();
    const trimmedName = name.trim();
    if (!trimmedCode) {
      show(t("tasks.projects.add.codeRequired"), "error");
      return;
    }
    if (!trimmedName) {
      show(t("tasks.projects.add.nameRequired"), "error");
      return;
    }
    setBusy(true);
    try {
      await api("/tasks/project", {
        method: "POST",
        body: {
          code: trimmedCode,
          name: trimmedName,
          kind,
          priority: Number(priority) || 3,
          preset,
          status,
          due: due.trim() || null,
          next_action: nextAction,
        },
      });
      show(t("tasks.projects.add.created", { code: trimmedCode }), "ok", 3000);
      onSaved();
    } catch (err) {
      show(t("tasks.projects.add.createFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="form-grid" style={{ marginBottom: 10 }}>
      <div className="form-row">
        <label htmlFor="project-add-code">{t("tasks.projects.add.codeLabel")}</label>
        <input id="project-add-code" className="form-input" value={code} onChange={(e) => setCode(e.target.value)} disabled={busy} />
      </div>
      <p className="setting-note">{t("tasks.projects.add.codeHint")}</p>
      <div className="form-inline">
        <input
          className="form-input"
          placeholder={t("tasks.projects.add.namePlaceholder")}
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={busy}
          aria-label={t("tasks.projects.add.namePlaceholder")}
        />
        <input
          className="form-input"
          placeholder={t("tasks.projects.add.kindPlaceholder")}
          value={kind}
          onChange={(e) => setKind(e.target.value)}
          disabled={busy}
          aria-label={t("tasks.projects.add.kindPlaceholder")}
        />
        <label>
          {t("tasks.projects.add.priorityLabel")}{" "}
          <input
            className="form-input"
            style={{ maxWidth: 70 }}
            type="number"
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
            disabled={busy}
          />
        </label>
      </div>
      <div className="form-inline">
        <label>
          {t("tasks.projects.add.presetLabel")}{" "}
          <select className="form-input" value={preset} onChange={(e) => setPreset(e.target.value)} disabled={busy}>
            {Object.keys(PRESET_LABEL_KEY).map((id) => (
              <option key={id} value={id}>
                {t(PRESET_LABEL_KEY[id])}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t("tasks.projects.add.statusLabel")}{" "}
          <select className="form-input" value={status} onChange={(e) => setStatus(e.target.value)} disabled={busy}>
            {Object.keys(PROJECT_STATUS_KEY).map((id) => (
              <option key={id} value={id}>
                {t(PROJECT_STATUS_KEY[id])}
              </option>
            ))}
          </select>
        </label>
        <input
          className="form-input"
          placeholder={t("tasks.projects.add.dueLabel")}
          value={due}
          onChange={(e) => setDue(e.target.value)}
          disabled={busy}
          aria-label={t("tasks.projects.add.dueLabel")}
        />
      </div>
      <div className="form-inline">
        <input
          className="form-input"
          style={{ flex: 1 }}
          placeholder={t("tasks.projects.add.nextActionPlaceholder")}
          value={nextAction}
          onChange={(e) => setNextAction(e.target.value)}
          disabled={busy}
          aria-label={t("tasks.projects.add.nextActionPlaceholder")}
        />
      </div>
      <div className="form-actions">
        <button className="btn btn-small btn-primary" type="button" disabled={busy} onClick={save}>
          {t("tasks.projects.add.submit")}
        </button>
        <button className="btn btn-small" type="button" disabled={busy} onClick={onClose}>
          {t("common.cancel")}
        </button>
      </div>
    </div>
  );
}

/** ADR-013 D1: プロジェクトの変更。**`code` の入力欄を出さない**——あとから変えると
 * 他から参照している記号（タスクの `project` 引数・タイムラインの紐づけ等）が全部外れる。 */
function ProjectEditForm({ project, onClose, onSaved }: { project: Project; onClose: () => void; onSaved: () => void }) {
  const t = useT();
  const { show } = useToast();
  const [name, setName] = useState(project.title);
  const [kind, setKind] = useState(project.kind || "");
  const [priority, setPriority] = useState(String(project.priority));
  const [preset, setPreset] = useState<string>(project.preset);
  const [status, setStatus] = useState<string>(project.status);
  const [due, setDue] = useState(project.due || "");
  const [nextAction, setNextAction] = useState(project.next_action || "");
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setBusy(true);
    try {
      await api(`/tasks/project/${encodeURIComponent(project.code)}`, {
        method: "POST",
        body: {
          name,
          kind,
          priority: Number(priority) || 0,
          preset,
          status,
          due: due.trim() || null,
          next_action: nextAction,
        },
      });
      show(t("tasks.projects.edit.saved"), "ok", 3000);
      onSaved();
    } catch (err) {
      show(t("tasks.projects.edit.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="form-grid">
      <div className="panel-head">
        <h3 style={{ margin: 0, fontSize: 13 }}>{t("tasks.projects.edit.heading", { code: project.code })}</h3>
      </div>
      <p className="panel-note">{t("tasks.projects.edit.codeFixedNote")}</p>
      <div className="form-inline">
        <input
          className="form-input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={busy}
          aria-label={t("tasks.projects.add.namePlaceholder")}
        />
        <input
          className="form-input"
          value={kind}
          onChange={(e) => setKind(e.target.value)}
          disabled={busy}
          aria-label={t("tasks.projects.add.kindPlaceholder")}
        />
        <label>
          {t("tasks.projects.add.priorityLabel")}{" "}
          <input
            className="form-input"
            style={{ maxWidth: 70 }}
            type="number"
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
            disabled={busy}
          />
        </label>
      </div>
      <div className="form-inline">
        <label>
          {t("tasks.projects.add.presetLabel")}{" "}
          <select className="form-input" value={preset} onChange={(e) => setPreset(e.target.value)} disabled={busy}>
            {Object.keys(PRESET_LABEL_KEY).map((id) => (
              <option key={id} value={id}>
                {t(PRESET_LABEL_KEY[id])}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t("tasks.projects.add.statusLabel")}{" "}
          <select className="form-input" value={status} onChange={(e) => setStatus(e.target.value)} disabled={busy}>
            {Object.keys(PROJECT_STATUS_KEY).map((id) => (
              <option key={id} value={id}>
                {t(PROJECT_STATUS_KEY[id])}
              </option>
            ))}
          </select>
        </label>
        <input
          className="form-input"
          placeholder={t("tasks.projects.add.dueLabel")}
          value={due}
          onChange={(e) => setDue(e.target.value)}
          disabled={busy}
          aria-label={t("tasks.projects.add.dueLabel")}
        />
      </div>
      <div className="form-inline">
        <input
          className="form-input"
          style={{ flex: 1 }}
          placeholder={t("tasks.projects.add.nextActionPlaceholder")}
          value={nextAction}
          onChange={(e) => setNextAction(e.target.value)}
          disabled={busy}
          aria-label={t("tasks.projects.add.nextActionPlaceholder")}
        />
      </div>
      <div className="form-actions">
        <button className="btn btn-small btn-primary" type="button" disabled={busy} onClick={save}>
          {t("common.save")}
        </button>
        <button className="btn btn-small" type="button" disabled={busy} onClick={onClose}>
          {t("common.cancel")}
        </button>
      </div>
    </div>
  );
}

function PlanMilestones() {
  const t = useT();
  const { show } = useToast();
  const { data: board, error, reload } = usePolling<Board>("/tasks/board", 5000);
  if (error) return <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>;
  if (!board) return <p className="panel-note">{t("common.loading")}</p>;
  const rows = board.milestones || [];

  // 済み／取り消しは **DB の1列を書くだけ**なので、裁定にも委譲にも載せない
  // （主人 2026-09-05:「わざわざエージェントを通す必要もない」）。
  const setDone = async (m: Milestone, done: boolean) => {
    try {
      await api(`/tasks/milestone/${encodeURIComponent(m.id)}/${done ? "done" : "undone"}`, { method: "POST" });
      reload();
    } catch (err) {
      show(
        t("tasks.milestones.updateFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }),
        "error"
      );
    }
  };

  return (
    <section className="panel" id="panel-milestones">
      <div className="panel-head">
        <h2>{t("tasks.milestones.heading")}</h2>
        <span className="count" id="milestones-count">
          {t("component.foldBlock.count", { count: rows.length })}
        </span>
      </div>
      <div id="milestone-list" className="rows">
        {!rows.length && <p className="panel-note">{t("common.none")}</p>}
        {rows.map((m) => (
          <div className={"row-item" + (m.done_at ? " row-done" : "")} key={m.id}>
            <span className="row-id">
              {formatDay(m.date, t)}
              {m.approximate ? t("dashboard.upcoming.approx") : ""}
            </span>
            <span className="row-title">
              {m.title} [{projectLabel(board, m.project_id)}]
            </span>
            {/* 済んだものは残り日数を出さない（過ぎていても赤くしない）。代わりに「済」。 */}
            {m.done_at ? (
              <span className="days-left">{t("tasks.milestones.done")}</span>
            ) : (
              <span className={"days-left " + daysLeftClass(m.days_left)}>
                {daysLeftText(m.days_left, m.approximate)}
              </span>
            )}
            <button className="btn btn-small" type="button" onClick={() => setDone(m, !m.done_at)}>
              {m.done_at ? t("tasks.milestones.undo") : t("tasks.milestones.markDone")}
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}
