import type { Board, Project, Task } from "../../app/types";
import { StatusBadge } from "../../components/StatusBadge";
import { projectLabel, stripLeadingProjectBracket } from "./utils";
import { useT } from "../../app/i18n";
import { AGENT_LABEL_KEY } from "../../app/agentMeta";

export function TaskRow({
  board,
  t,
  pj,
  latest,
  parentProject,
  onOpenCtx,
}: {
  board: Board;
  t: Task;
  pj?: string;
  latest?: boolean;
  parentProject?: Project | null;
  onOpenCtx: (id: string) => void;
}) {
  // props の `t`（Task）が i18n の `useT()` と同じ名前を使っているので、翻訳関数は
  // 別名（`tr`）で受ける——呼び出し側（DoneDays・Running・Plan・Log）が広く `t={task}`
  // という形の props 名に依存しているため、ここを改名すると影響範囲が大きい。
  const tr = useT();
  const finished = t.status === "done";
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
      <button className="btn btn-small btn-ghost btn-ctx" type="button" onClick={() => onOpenCtx(t.id)}>
        {tr("tasks.row.context")}
      </button>
    </div>
  );
}
