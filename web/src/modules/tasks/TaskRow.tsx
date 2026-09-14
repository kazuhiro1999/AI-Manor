import { useState } from "react";
import type { Board, Project, Task } from "../../app/types";
import { StatusBadge } from "../../components/StatusBadge";
import { projectLabel, stripLeadingProjectBracket } from "./utils";
import { useT } from "../../app/i18n";
import { AGENT_LABEL_KEY } from "../../app/agentMeta";
import { api, ApiError } from "../../app/api";
import { useToast } from "../../components/Toast";

//: 一覧の行から1クリックで進める、いちばんよく使う遷移だけ（T55・意見箱「Claudeを
//: 介すまでもない進捗更新はボタンポチでやりたい」）。それ以外の遷移（waiting/hold
//: への退避や取り下げ）は「詳しく」ボタン→CtxModal に任せる——note が要る遷移や
//: level=HG の完了（decision 経由の承認が要る）は、そちらの厚いフォームのままでよい。
const QUICK_NEXT_STATUS: Partial<Record<Task["status"], "doing" | "done">> = {
  todo: "doing",
  waiting: "doing",
  hold: "doing",
  doing: "done",
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
  const [busy, setBusy] = useState(false);
  const finished = t.status === "done";
  const quickNext = t.level === "HG" ? undefined : QUICK_NEXT_STATUS[t.status];

  const quickAdvance = async () => {
    if (!quickNext) return;
    setBusy(true);
    try {
      await api(`/tasks/task/${encodeURIComponent(t.id)}/status`, { method: "POST", body: { status: quickNext } });
      show(tr("tasks.judge.ruledToast", { id: t.id, status: quickNext }), "ok", 3000);
      onChanged?.();
    } catch (err) {
      show(tr("tasks.ctx.rejected", { reason: err instanceof ApiError ? err.message : tr("common.unknown") }), "error");
    } finally {
      setBusy(false);
    }
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
      {!readOnly && quickNext && (
        <button className="btn btn-small btn-ghost" type="button" disabled={busy} onClick={quickAdvance}>
          {quickNext === "doing" ? tr("tasks.row.quickStart") : tr("tasks.row.quickDone")}
        </button>
      )}
      <button className="btn btn-small btn-ghost btn-ctx" type="button" onClick={() => onOpenCtx(t.id)}>
        {tr("tasks.row.context")}
      </button>
    </div>
  );
}
