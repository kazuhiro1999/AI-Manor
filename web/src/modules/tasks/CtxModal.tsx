import { useEffect, useState } from "react";
import { Modal } from "../../components/Modal";
import { Markdown } from "../../components/Markdown";
import { api, ApiError } from "../../app/api";
import type { CtxResponse, TaskStatus } from "../../app/types";
import { useToast } from "../../components/Toast";
import { useT, type TranslationKey } from "../../app/i18n";

const STATUS_KEY: Record<TaskStatus, TranslationKey> = {
  todo: "taskStatus.todo",
  doing: "taskStatus.doing",
  waiting: "taskStatus.waiting",
  hold: "taskStatus.hold",
  resident: "taskStatus.resident",
  done: "taskStatus.done",
  withdrawn: "taskStatus.withdrawn",
};
const STATUS_OPTIONS: TaskStatus[] = ["todo", "doing", "waiting", "hold", "resident", "done", "withdrawn"];

export function CtxModal({
  id,
  readOnly,
  onClose,
  onChanged,
}: {
  id: string;
  readOnly: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const t = useT();
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<TaskStatus>("todo");
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState("");
  const { show } = useToast();

  useEffect(() => {
    let cancelled = false;
    api<CtxResponse>(`/tasks/ctx/${encodeURIComponent(id)}`)
      .then((res) => {
        if (!cancelled) setMarkdown(res.markdown);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : t("common.unknown"));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const submit = async () => {
    setMsg(t("tasks.ctx.sending"));
    try {
      const res = await api<{ id: string; status: string; warnings?: string[] }>(
        `/tasks/task/${encodeURIComponent(id)}/status`,
        { method: "POST", body: { status, note } }
      );
      setMsg(t("tasks.ctx.statusChanged", { id: res.id, status: res.status }) + ((res.warnings || []).length ? t("tasks.ctx.warningsSuffix") : ""));
      show(t("tasks.judge.ruledToast", { id, status }), "ok", 4000);
      onChanged();
    } catch (err) {
      setMsg(t("tasks.ctx.rejected", { reason: err instanceof ApiError ? err.message : t("common.unknown") }));
    }
  };

  return (
    <Modal title={t("tasks.ctx.title", { id })} onClose={onClose}>
      {error ? (
        <p>{t("errors.loadFailed", { reason: error })}</p>
      ) : markdown == null ? (
        <p>{t("common.loading")}</p>
      ) : (
        <Markdown text={markdown} />
      )}
      {id[0] === "T" && !readOnly && (
        <div className="modal-body" style={{ borderTop: "1px solid var(--border)" }}>
          <div style={{ fontWeight: 700, marginBottom: 6 }}>{t("tasks.ctx.changeStatus")}</div>
          <div className="form-inline">
            <select
              className="ruling-input"
              style={{ flex: "0 0 auto" }}
              value={status}
              onChange={(e) => setStatus(e.target.value as TaskStatus)}
              aria-label={t("tasks.ctx.newStatusAria")}
            >
              {STATUS_OPTIONS.map((o) => (
                <option key={o} value={o}>
                  {t(STATUS_KEY[o])} {o}
                </option>
              ))}
            </select>
            <input
              className="ruling-input"
              placeholder={t("tasks.ctx.notePlaceholder")}
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
            <button className="btn btn-primary btn-small" onClick={submit} type="button">
              {t("tasks.ctx.change")}
            </button>
          </div>
          <div style={{ fontSize: 11.5, color: "var(--text-dim)", marginTop: 6 }}>{msg}</div>
        </div>
      )}
    </Modal>
  );
}
