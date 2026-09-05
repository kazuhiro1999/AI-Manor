import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { usePolling } from "../../app/polling";
import { useEditingGuard } from "../../app/editing";
import { api, ApiError } from "../../app/api";
import type { Board, DecisionStatus } from "../../app/types";
import { Card } from "../../components/Card";
import { Markdown } from "../../components/Markdown";
import { RiskBadge } from "../../components/StatusBadge";
import { useToast } from "../../components/Toast";
import { projectLabel, decisionDetailText } from "./utils";
import { CtxModal } from "./CtxModal";
import { useT } from "../../app/i18n";

const DRAFTS = new Map<string, string>();

export function Judge({ readOnly }: { readOnly: boolean }) {
  const t = useT();
  const { ref, isEditing } = useEditingGuard<HTMLDivElement>();
  const { data: board, error, reload } = usePolling<Board>("/tasks/board", 5000, ref as React.RefObject<HTMLElement>);
  const [openDetail, setOpenDetail] = useState<Set<string>>(new Set());
  const [ctxId, setCtxId] = useState<string | null>(null);
  const { show } = useToast();
  const draftsRef = useRef(DRAFTS);

  const pending = board?.pending || [];

  const toggleDetail = (key: string) => {
    setOpenDetail((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const ruleDecision = async (id: string, status: DecisionStatus, ruling: string, inputEl: HTMLInputElement | null) => {
    const trimmed = (ruling || "").trim();
    if (status === "modified" && !trimmed) {
      inputEl?.classList.add("input-error");
      show(t("tasks.judge.needsRuling"), "warn", 4000);
      return;
    }
    inputEl?.classList.remove("input-error");
    try {
      await api(`/tasks/decision/${encodeURIComponent(id)}/rule`, { method: "POST", body: { status, ruling: trimmed } });
      draftsRef.current.delete(id);
      show(t("tasks.judge.ruledToast", { id, status }), "ok", 4000);
      reload();
    } catch (err) {
      show(t("tasks.judge.ruleFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    }
  };

  if (error) return <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>;
  if (!board) return <p className="panel-note">{t("common.loading")}</p>;

  return (
    <div className="view" id="view-judge">
      <section className="panel panel-primary" id="panel-judge" ref={ref}>
        <div className="panel-head">
          <h2>{t("tasks.judge.heading")}</h2>
          <span className="count" id="pending-count">
            {t("component.foldBlock.count", { count: pending.length })}
          </span>
          <Link to="/tasks/new" className="btn btn-small" style={{ marginLeft: "auto" }}>
            {t("tasks.judge.addTask")}
          </Link>
        </div>
        <p className="panel-note">{t("tasks.judge.hint")}</p>
        <div id="pending-list" className="cards">
          {!pending.length && <p className="panel-note">{t("common.none")}</p>}
          {pending.map((d) => {
            const pj = d.project_id ? projectLabel(board, d.project_id) : "—";
            const openKey = "pending:" + d.id;
            const isOpen = openDetail.has(openKey);
            return (
              <Card key={d.id} stale={d.stale}>
                <div className="card-head">
                  <span className="card-title">
                    {d.id} {d.title}
                  </span>
                  <span className="card-pj">{pj}</span>
                  {d.stale && <span className="badge-judge">{t("tasks.judge.needsDecision")}</span>}
                  <span className={"card-days" + (d.stale ? " stale" : "")}>{t("tasks.judge.staleDays", { n: d.days })}</span>
                </div>
                <div className="card-rec">
                  {t("tasks.judge.recommendation", { text: d.tasks?.[0]?.recommendation || t("common.none") })} <RiskBadge risk={d.risk} />
                </div>
                {/* ADR-006 §2 D5・D7・§6 担当C: 何を見て推奨したか（`decision.evidence`）。
                    空なら「根拠の記載なし」を薄く表示する（`.panel-note` は既存の薄字スタイル）。 */}
                <div className="card-evidence" data-testid="card-evidence">
                  <div className="card-evidence-label">{t("tasks.judge.evidenceLabel")}</div>
                  {d.evidence && d.evidence.trim() ? (
                    <Markdown text={d.evidence} />
                  ) : (
                    <p className="panel-note">{t("tasks.judge.noEvidence")}</p>
                  )}
                </div>
                <button className="detail-toggle" type="button" onClick={() => toggleDetail(openKey)}>
                  {isOpen ? t("tasks.judge.hideDetail") : t("tasks.judge.showDetail")}
                </button>
                {isOpen && <div className="detail-box">{decisionDetailText(d)}</div>}
                <div className="card-actions">
                  {readOnly ? (
                    <span className="panel-note">{t("tasks.judge.readOnlyNote")}</span>
                  ) : (
                    <JudgeActions d={d} draftsRef={draftsRef} onRule={ruleDecision} />
                  )}
                </div>
              </Card>
            );
          })}
        </div>
      </section>
      {ctxId && (
        <CtxModal
          id={ctxId}
          readOnly={readOnly}
          onClose={() => setCtxId(null)}
          onChanged={() => {
            reload();
          }}
        />
      )}
    </div>
  );
}

function JudgeActions({
  d,
  draftsRef,
  onRule,
}: {
  d: Board["pending"][number];
  draftsRef: React.MutableRefObject<Map<string, string>>;
  onRule: (id: string, status: DecisionStatus, ruling: string, inputEl: HTMLInputElement | null) => void;
}) {
  const t = useT();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [value, setValue] = useState(draftsRef.current.get(d.id) || "");
  return (
    <>
      <input
        ref={inputRef}
        className="ruling-input"
        placeholder={t("tasks.judge.rulingPlaceholder")}
        value={value}
        onChange={(e) => {
          setValue(e.target.value);
          draftsRef.current.set(d.id, e.target.value);
          inputRef.current?.classList.remove("input-error");
        }}
      />
      <button className="btn btn-small btn-primary" type="button" onClick={() => onRule(d.id, "approved", value, inputRef.current)}>
        {t("tasks.judge.approve")}
      </button>
      <button className="btn btn-small" type="button" onClick={() => onRule(d.id, "modified", value, inputRef.current)}>
        {t("tasks.judge.modify")}
      </button>
      <button className="btn btn-small btn-danger" type="button" onClick={() => onRule(d.id, "rejected", value, inputRef.current)}>
        {t("tasks.judge.reject")}
      </button>
    </>
  );
}
