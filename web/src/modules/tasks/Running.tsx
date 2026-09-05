import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { usePolling } from "../../app/polling";
import { useEditingGuard } from "../../app/editing";
import { APP_NAME } from "../../app/brand";
import { api, ApiError } from "../../app/api";
import type { Board, Task } from "../../app/types";
import { TaskRow } from "./TaskRow";
import { DoneDays } from "./DoneDays";
import { FoldBlock } from "../../components/FoldBlock";
import { CtxModal } from "./CtxModal";
import { useToast } from "../../components/Toast";
import { doneDateGroups, interestReasonText } from "./utils";
import { useT } from "../../app/i18n";

const TASK_MODE_KEY = "manor-web.taskMode";
type TaskMode = "list" | "tree";

function readTaskMode(): TaskMode {
  try {
    const v = localStorage.getItem(TASK_MODE_KEY);
    return v === "tree" ? "tree" : "list";
  } catch {
    return "list";
  }
}

export function Running({ readOnly: _readOnly }: { readOnly: boolean }) {
  const t = useT();
  const { ref, isEditing: _isEditing } = useEditingGuard<HTMLDivElement>();
  const { data: board, error, reload } = usePolling<Board>("/tasks/board", 5000, ref as React.RefObject<HTMLElement>);
  const [mode, setMode] = useState<TaskMode>(() => readTaskMode());
  const [ctxId, setCtxId] = useState<string | null>(null);
  const [treeOpen, setTreeOpen] = useState<Set<string>>(new Set());
  const [addingNote, setAddingNote] = useState(false);
  const navigate = useNavigate();

  const setModeAndSave = (m: TaskMode) => {
    setMode(m);
    try {
      localStorage.setItem(TASK_MODE_KEY, m);
    } catch {
      /* ignore */
    }
  };

  if (error) return <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>;
  if (!board) return <p className="panel-note">{t("common.loading")}</p>;

  return (
    <div className="view" id="view-running" ref={ref}>
      <SummaryTiles board={board} onJumpJudge={() => navigate("/tasks/judge")} />
      <section className="panel" id="panel-running">
        <div className="panel-head">
          <h2>{t("tasks.running.heading")}</h2>
          <span className="count" id="running-count">
            {t("component.foldBlock.count", { count: (board.tasks || []).length })}
          </span>
          <div className="seg" role="group" aria-label={t("tasks.running.modeAria")}>
            <button
              className="seg-btn"
              type="button"
              aria-pressed={mode === "list"}
              onClick={() => setModeAndSave("list")}
            >
              {t("tasks.running.modeList")}
            </button>
            <button
              className="seg-btn"
              type="button"
              aria-pressed={mode === "tree"}
              onClick={() => setModeAndSave("tree")}
            >
              {t("tasks.running.modeTree")}
            </button>
          </div>
        </div>
        {mode === "list" ? (
          <RunningList board={board} onOpenCtx={setCtxId} />
        ) : (
          <RunningTree board={board} onOpenCtx={setCtxId} treeOpen={treeOpen} setTreeOpen={setTreeOpen} />
        )}
      </section>
      <section className="panel" id="panel-relay">
        <div className="panel-head">
          <h2>{t("tasks.relay.heading")}</h2>
          <span className="count" id="relay-count">
            {t("component.foldBlock.count", { count: (board.notes || []).length })}
          </span>
          {!_readOnly && (
            <button className="btn btn-small btn-primary" style={{ marginLeft: "auto" }} type="button" onClick={() => setAddingNote(true)}>
              {t("tasks.relay.add")}
            </button>
          )}
        </div>
        <p className="panel-note">{t("tasks.relay.hint", { app: APP_NAME })}</p>
        {addingNote && (
          <NoteAddForm
            board={board}
            onClose={() => setAddingNote(false)}
            onSaved={() => {
              setAddingNote(false);
              reload();
            }}
          />
        )}
        <div id="relay-list" className="rows">
          {!board.notes?.length && <p className="panel-note">{t("tasks.relay.empty")}</p>}
          {board.notes?.map((n) => (
            <div className="row-item" key={n.id}>
              <span className="row-id">{n.id}</span>
              <span className="row-title">{n.title}</span>
            </div>
          ))}
        </div>
      </section>
      {ctxId && <CtxModal id={ctxId} readOnly={_readOnly} onClose={() => setCtxId(null)} onChanged={reload} />}
    </div>
  );
}

/** ADR-013 D3: メモ（伝達）の追加。`about`（宛先のプロジェクト）は任意——
 * 本文だけで残せる（未選択のままでも送信できる）。 */
function NoteAddForm({ board, onClose, onSaved }: { board: Board; onClose: () => void; onSaved: () => void }) {
  const t = useT();
  const { show } = useToast();
  const [title, setTitle] = useState("");
  const [about, setAbout] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);

  const save = async () => {
    const trimmed = title.trim();
    if (!trimmed) {
      show(t("tasks.relay.add.titleRequired"), "error");
      return;
    }
    setBusy(true);
    try {
      await api("/tasks/note", { method: "POST", body: { title: trimmed, about: about || undefined, body } });
      show(t("tasks.relay.add.added"), "ok", 3000);
      onSaved();
    } catch (err) {
      show(t("tasks.relay.add.addFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="form-inline" style={{ flexWrap: "wrap", marginBottom: 8 }}>
      <input
        className="form-input"
        style={{ flex: 1, minWidth: 160 }}
        placeholder={t("tasks.relay.add.titlePlaceholder")}
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        disabled={busy}
        aria-label={t("tasks.relay.add.titlePlaceholder")}
      />
      <label>
        {t("tasks.relay.add.aboutLabel")}{" "}
        <select className="form-input" value={about} onChange={(e) => setAbout(e.target.value)} disabled={busy}>
          <option value="">{t("tasks.relay.add.aboutNone")}</option>
          {(board.projects || []).map((p) => (
            <option key={p.id} value={p.id}>
              {p.code} {p.title}
            </option>
          ))}
        </select>
      </label>
      <input
        className="form-input"
        style={{ flex: 1, minWidth: 160 }}
        placeholder={t("tasks.relay.add.bodyPlaceholder")}
        value={body}
        onChange={(e) => setBody(e.target.value)}
        disabled={busy}
        aria-label={t("tasks.relay.add.bodyPlaceholder")}
      />
      <button className="btn btn-small btn-primary" type="button" disabled={busy} onClick={save}>
        {t("tasks.relay.add.submit")}
      </button>
      <button className="btn btn-small" type="button" disabled={busy} onClick={onClose}>
        {t("common.cancel")}
      </button>
    </div>
  );
}

function SummaryTiles({ board, onJumpJudge }: { board: Board; onJumpJudge: () => void }) {
  const t = useT();
  const recent = board.recent_done || [];
  const groups = doneDateGroups(recent);
  const newest = groups[0];
  const total = board.counts.done_total ?? recent.length;
  const tiles = [
    {
      label: t("tasks.group.doneRecent"),
      value: newest ? newest.items.length : 0,
      sub: newest
        ? t("tasks.summary.doneSubWithCount", { label: newest.label === "__older__" ? t("tasks.doneGroups.noDate") : newest.label, total })
        : t("tasks.summary.noDoneRecord"),
      onClick: undefined as (() => void) | undefined,
    },
    {
      label: t("tasks.summary.progressLabel"),
      value: board.counts.doing_butler,
      sub: board.counts.doing_master ? t("tasks.summary.masterAlso", { n: board.counts.doing_master }) : "",
      onClick: undefined,
    },
    { label: t("tasks.summary.pendingLabel"), value: board.counts.pending, sub: t("tasks.summary.jumpToJudge"), onClick: onJumpJudge },
  ];
  return (
    <div id="running-summary" className="summary">
      {tiles.map((tile) => (
        <div key={tile.label} className={"tile" + (tile.onClick ? " clickable" : "")} onClick={tile.onClick}>
          <div className="tile-label">{tile.label}</div>
          <div className="tile-value">{tile.value}</div>
          <div className="tile-sub">{tile.sub}</div>
        </div>
      ))}
    </div>
  );
}

function RunningList({ board, onOpenCtx }: { board: Board; onOpenCtx: (id: string) => void }) {
  const t = useT();
  const tasks = board.tasks || [];
  const masterDoing = tasks.filter((tk) => tk.owner === "master" && tk.status === "doing");
  const butlerDoing = tasks.filter((tk) => tk.status === "doing" && tk.owner === "butler");
  const resident = tasks.filter((tk) => tk.status === "resident");
  const backlog = tasks.filter((tk) => ["todo", "waiting", "hold"].includes(tk.status));
  const done = tasks.filter((tk) => tk.status === "done");
  const withdrawn = board.withdrawn_recent || [];

  const groups: { key: string; label: string; rows: Task[] }[] = [
    { key: "master-doing", label: t("tasks.group.masterDoing"), rows: masterDoing },
    { key: "doing", label: t("tasks.group.butlerDoing"), rows: butlerDoing },
    { key: "delegated", label: t("tasks.group.delegated"), rows: board.delegated || [] },
    { key: "resident", label: t("tasks.group.resident"), rows: resident },
    { key: "backlog", label: t("tasks.group.backlog"), rows: backlog },
  ];

  const anyContent = groups.some((g) => g.rows.length) || done.length || withdrawn.length;

  return (
    <div id="running-list" className="status-groups">
      {groups.map(
        (g) =>
          g.rows.length > 0 && (
            <div key={g.key}>
              <div className="status-block-head">
                {g.label}
                {t("tasks.group.countSuffix", { n: g.rows.length })}
              </div>
              <div className="rows">
                {g.rows.map((tk) => (
                  <TaskRow key={tk.id} board={board} t={tk} onOpenCtx={onOpenCtx} />
                ))}
              </div>
            </div>
          )
      )}
      {done.length > 0 && (
        <div>
          <div className="status-block-head">
            {t("tasks.group.doneRecent")}
            {t("tasks.group.countSuffix", { n: done.length })}
          </div>
          <DoneDays board={board} items={done} scope="list" onOpenCtx={onOpenCtx} />
        </div>
      )}
      {withdrawn.length > 0 && (
        <div>
          <div className="status-block-head">{t("tasks.group.withdrawn")}</div>
          <FoldBlock storageKey="withdrawn-recent" label={t("tasks.group.withdrawnRecent")} count={withdrawn.length}>
            {withdrawn.map((tk) => (
              <TaskRow key={tk.id} board={board} t={tk} onOpenCtx={onOpenCtx} />
            ))}
          </FoldBlock>
        </div>
      )}
      {!anyContent && <p className="panel-note">{t("common.none")}</p>}
    </div>
  );
}

function RunningTree({
  board,
  onOpenCtx,
  treeOpen,
  setTreeOpen,
}: {
  board: Board;
  onOpenCtx: (id: string) => void;
  treeOpen: Set<string>;
  setTreeOpen: (s: Set<string>) => void;
}) {
  const t = useT();
  const tasks = [...(board.tasks || []), ...(board.withdrawn_recent || [])];
  const pending = board.pending || [];
  const byProject = new Map<string, Task[]>();
  const pendingByProject = new Map<string, Board["pending"]>();
  for (const t of tasks) {
    const key = t.project_id || "__none__";
    if (!byProject.has(key)) byProject.set(key, []);
    byProject.get(key)!.push(t);
  }
  for (const d of pending) {
    const key = d.project_id || "__none__";
    if (!pendingByProject.has(key)) pendingByProject.set(key, []);
    pendingByProject.get(key)!.push(d);
  }
  const projects = (board.projects || []).slice().sort((a, b) => {
    const ra = a.interest ? a.interest.rank : a.priority ?? 999;
    const rb = b.interest ? b.interest.rank : b.priority ?? 999;
    return ra - rb;
  });
  const otherKeys: string[] = [];
  if (byProject.has("__none__") || pendingByProject.has("__none__")) otherKeys.push("__none__");
  const keys = [...projects.map((p) => p.id), ...otherKeys];

  const toggle = (key: string) => {
    const next = new Set(treeOpen);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setTreeOpen(next);
  };

  const rendered = keys
    .map((key) => {
      const rows = byProject.get(key) || [];
      const pendingRows = pendingByProject.get(key) || [];
      if (!rows.length && !pendingRows.length) return null;
      const proj = projects.find((p) => p.id === key) || null;
      const title = proj ? `${proj.code} ${proj.title}` : t("tasks.tree.other");
      const total = rows.length + pendingRows.length;
      const isOpen = treeOpen.has(key);
      const doingN = rows.filter((r) => r.status === "doing").length;
      const residentN = rows.filter((r) => r.status === "resident").length;
      const withdrawnN = rows.filter((r) => r.status === "withdrawn").length;
      // proj.kind の判定値そのもの（"執事"）はバックエンドのデータで、フロントの i18n を
      // 経由しない（プロジェクトの種類を表す実データ。他の担当種類も同じ列に入りうる）。
      // 表示だけ担当名として訳す。
      const isButlerProject = proj && proj.kind === "執事";
      const reasonText = proj ? interestReasonText(proj.interest) : "";

      return (
        <div key={key} className={"tree-group" + (isOpen ? " open" : "")}>
          <div className="tree-group-head" onClick={() => toggle(key)}>
            <span className="caret">▶</span>
            <span className="tree-group-title">{title}</span>
            {isButlerProject && <span className="tree-group-kind">{t("agent.butler")}</span>}
            {reasonText && <span className="tree-group-interest">{reasonText}</span>}
            <span className="tree-group-badges">
              {pendingRows.length > 0 && <span className="badge-st badge-pending-n">{t("tasks.tree.pendingBadge", { n: pendingRows.length })}</span>}
              {doingN > 0 && <span className="badge-st st-doing">{t("tasks.tree.doingBadge", { n: doingN })}</span>}
              {residentN > 0 && <span className="badge-st st-resident">{t("tasks.tree.residentBadge", { n: residentN })}</span>}
              {withdrawnN > 0 && <span className="badge-st st-withdrawn">{t("tasks.tree.withdrawnBadge", { n: withdrawnN })}</span>}
              <span className="nav-count">{t("component.foldBlock.count", { count: total })}</span>
            </span>
          </div>
          {isOpen && (
            <div className="tree-group-body">
              {pendingRows.length > 0 && (
                <>
                  <div className="tree-sub">
                    {t("tasks.tree.pendingSub")}
                    {t("tasks.group.countSuffix", { n: pendingRows.length })}
                  </div>
                  <div className="rows">
                    {pendingRows.map((d) => (
                      <PendingMiniRow key={d.id} d={d} />
                    ))}
                  </div>
                </>
              )}
              {[
                { key: "doing", title: t("tasks.tree.doingSub") },
                { key: "resident", title: t("tasks.tree.residentSub") },
                { key: "todo", title: t("tasks.tree.backlogSub"), extra: ["waiting", "hold"] },
                { key: "done", title: t("tasks.tree.doneSub"), isDone: true },
                { key: "withdrawn", title: t("tasks.tree.withdrawnSub") },
              ].map((block) => {
                const codes = [block.key, ...(block.extra || [])];
                const items = rows.filter((r) => codes.includes(r.status));
                if (!items.length) return null;
                return (
                  <div key={block.key}>
                    <div className="tree-sub">
                      {block.title}
                      {t("tasks.group.countSuffix", { n: items.length })}
                    </div>
                    {block.isDone ? (
                      <DoneDays board={board} items={items} scope={"tree:" + key} pj={title} parentProject={proj} onOpenCtx={onOpenCtx} />
                    ) : (
                      <div className="rows">
                        {items.map((tk) => (
                          <TaskRow key={tk.id} board={board} t={tk} pj={title} parentProject={proj} onOpenCtx={onOpenCtx} />
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      );
    })
    .filter(Boolean);

  return (
    <div id="running-tree" className="tree">
      {rendered.length ? rendered : <p className="panel-note">{t("common.none")}</p>}
    </div>
  );
}

function PendingMiniRow({ d }: { d: Board["pending"][number] }) {
  const t = useT();
  const navigate = useNavigate();
  return (
    <div className={"row-item" + (d.stale ? " stale" : "")}>
      <span className="row-id">{d.id}</span>
      <span className="badge-pending-n">{t("tasks.pendingMini.badge")}</span>
      <span className="row-title">{d.title}</span>
      {d.stale && <span className="card-days stale">{t("tasks.judge.staleDays", { n: d.days })}</span>}
      <button className="btn btn-small" type="button" onClick={() => navigate("/tasks/judge")}>
        {t("tasks.pendingMini.ruleButton")}
      </button>
    </div>
  );
}
