import { useContext, useState } from "react";
import type { Board, Project, Task, TaskStatus } from "../../app/types";
import { StatusBadge } from "../../components/StatusBadge";
import { projectLabel, stripLeadingProjectBracket } from "./utils";
import { useT, type TranslationKey } from "../../app/i18n";
import { AGENT_LABEL_KEY } from "../../app/agentMeta";
import { api, ApiError } from "../../app/api";
import { useToast } from "../../components/Toast";
import { MetaContext } from "../../app/MetaContext";

//: 一覧の行から状態を変える（T55・意見箱「Claudeを介すまでもない進捗更新はアプリで
//: 完結させたい」）。最初は「着手／完了」の1ボタンだったが、主人 2026-09-15「進行中の
//: ものを保留に戻せない・常駐は変更できない。ドロップダウンで自分で設定したい」。
//: 選択肢は `GET /api/v1/meta` の `task_transitions`（状態機械 ADR-001 §4）から
//: 「いまの状態から行ける先」だけを出す。note が要る状態（`task_note_required`。
//: waiting=何を待つか／withdrawn=理由）は選んだあとに入力欄を出して確定させる。
//: 常駐（resident）が取り下げにしか行けないのは状態機械の設計で、画面の都合ではない。
//: level=HG の完了は decision 経由の承認が要るので選択肢に出さない（「詳しく」→CtxModal）。
//: meta がまだ無い（起動直後・古いバックエンド）ときだけ、下の写しにフォールバックする。
const FALLBACK_TRANSITIONS: Partial<Record<TaskStatus, TaskStatus[]>> = {
  todo: ["doing", "hold", "resident", "waiting", "withdrawn"],
  doing: ["done", "hold", "waiting", "withdrawn"],
  waiting: ["doing", "done", "hold", "todo", "withdrawn"],
  hold: ["doing", "todo", "waiting", "withdrawn"],
  resident: ["withdrawn"],
  done: [],
  withdrawn: [],
};
const FALLBACK_NOTE_REQUIRED: TaskStatus[] = ["waiting", "withdrawn"];

const STATUS_LABEL_KEY: Record<TaskStatus, TranslationKey> = {
  doing: "taskStatus.doing",
  resident: "taskStatus.resident",
  todo: "taskStatus.todo",
  hold: "taskStatus.hold",
  waiting: "taskStatus.waiting",
  done: "taskStatus.done",
  withdrawn: "taskStatus.withdrawn",
};

export function TaskRow({
  board,
  t,
  pj,
  latest,
  parentProject,
  onOpenCtx,
  readOnly,
  onChanged,
}: {
  board: Board;
  t: Task;
  pj?: string;
  latest?: boolean;
  parentProject?: Project | null;
  onOpenCtx: (id: string) => void;
  readOnly?: boolean;
  onChanged?: () => void;
}) {
  // props の `t`（Task）が i18n の `useT()` と同じ名前を使っているので、翻訳関数は
  // 別名（`tr`）で受ける——呼び出し側（DoneDays・Running・Plan・Log）が広く `t={task}`
  // という形の props 名に依存しているため、ここを改名すると影響範囲が大きい。
  const tr = useT();
  const { show } = useToast();
  // Provider の外（試験・login 画面）でも落ちないよう useMetaContext ではなく素の useContext。
  const meta = useContext(MetaContext)?.meta ?? null;
  const [busy, setBusy] = useState(false);
  // note が要る状態を選んだあと、入力を待っている状態（null なら待っていない）。
  const [pendingStatus, setPendingStatus] = useState<TaskStatus | null>(null);
  const [note, setNote] = useState("");
  const finished = t.status === "done";

  const transitions = meta?.task_transitions ?? FALLBACK_TRANSITIONS;
  const noteRequired = meta?.task_note_required ?? FALLBACK_NOTE_REQUIRED;
  const nextStatuses = (transitions[t.status] ?? []).filter((s) => !(s === "done" && t.level === "HG"));
  const canChange = !readOnly && nextStatuses.length > 0;

  const post = async (status: TaskStatus, noteText?: string) => {
    setBusy(true);
    try {
      await api(`/tasks/task/${encodeURIComponent(t.id)}/status`, {
        method: "POST",
        body: noteText ? { status, note: noteText } : { status },
      });
      show(tr("tasks.judge.ruledToast", { id: t.id, status: tr(STATUS_LABEL_KEY[status]) }), "ok", 3000);
      setPendingStatus(null);
      setNote("");
      onChanged?.();
    } catch (err) {
      show(tr("tasks.ctx.rejected", { reason: err instanceof ApiError ? err.message : tr("common.unknown") }), "error");
    } finally {
      setBusy(false);
    }
  };

  const onSelect = (value: string) => {
    const status = value as TaskStatus;
    if (status === t.status) {
      setPendingStatus(null);
      return;
    }
    if (noteRequired.includes(status)) {
      setPendingStatus(status);
      setNote("");
      return;
    }
    void post(status);
  };

  const owner =
    t.owner === "master" ? (
      <span className="owner-tag master">{tr("tasks.row.masterOwner")}</span>
    ) : t.owner && t.owner !== "butler" ? (
      <span className="owner-tag">{tr("tasks.row.ownerArrow", { owner: t.owner in AGENT_LABEL_KEY ? tr(AGENT_LABEL_KEY[t.owner]) : t.owner })}</span>
    ) : null;
  const isUnderMatchingParent = !!(parentProject && String(parentProject.id) === String(t.project_id));
  const pjLabel = pj || projectLabel(board, t.project_id);
  const displayTitle = isUnderMatchingParent ? stripLeadingProjectBracket(t.title, parentProject) : t.title;

  return (
    <div className={"row-item" + (finished ? " finished" : "") + (t.status === "withdrawn" ? " withdrawn" : "")}>
      <span className="row-id">{t.id}</span>
      {t.level && <span className="badge-l">{t.level}</span>}
      <StatusBadge status={t.status} />
      <span className="row-title">
        {!isUnderMatchingParent && `[${pjLabel}] `}
        {displayTitle}
      </span>
      {latest && <span className="badge-latest">{tr("tasks.row.latest")}</span>}
      {owner}
      {canChange && (
        <select
          className="form-select"
          style={{ flex: "0 0 auto", minWidth: 0, padding: "2px 6px", fontSize: 11.5 }}
          aria-label={tr("tasks.row.statusSelectAria", { id: t.id })}
          value={pendingStatus ?? t.status}
          disabled={busy}
          onChange={(e) => onSelect(e.target.value)}
        >
          <option value={t.status}>{tr(STATUS_LABEL_KEY[t.status])}</option>
          {nextStatuses.map((s) => (
            <option key={s} value={s}>
              {tr(STATUS_LABEL_KEY[s])}
            </option>
          ))}
        </select>
      )}
      <button className="btn btn-small btn-ghost btn-ctx" type="button" onClick={() => onOpenCtx(t.id)}>
        {tr("tasks.row.context")}
      </button>
      {pendingStatus && (
        <div className="form-inline" style={{ flexBasis: "100%" }}>
          <input
            className="form-input"
            placeholder={pendingStatus === "waiting" ? tr("tasks.row.noteWaiting") : tr("tasks.row.noteReason")}
            aria-label={pendingStatus === "waiting" ? tr("tasks.row.noteWaiting") : tr("tasks.row.noteReason")}
            value={note}
            disabled={busy}
            onChange={(e) => setNote(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && note.trim()) void post(pendingStatus, note.trim());
            }}
          />
          <button className="btn btn-small btn-primary" type="button" disabled={busy || !note.trim()} onClick={() => void post(pendingStatus, note.trim())}>
            {tr("tasks.row.noteConfirm")}
          </button>
          <button className="btn btn-small" type="button" disabled={busy} onClick={() => setPendingStatus(null)}>
            {tr("common.cancel")}
          </button>
        </div>
      )}
    </div>
  );
}
