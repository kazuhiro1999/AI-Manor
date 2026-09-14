/* manor web — 過去に登録した意見の一覧（`GET /api/v1/ideas`）。
 * 新しい順。状態の札（hold=仕分け待ち／todo=受付済み／doing・waiting・resident=対応中／
 * done=完了✓／withdrawn=見送り。夜勤 N6・主人のご要望 2026-09-08。todo を「対応中」と
 * 出していたのを 2026-09-14 に分けた——仕分け済みなだけで誰も着手していない件が「対応中」に見え、
 * 主人が「何がブロッカーか」と問われた）。
 * source='idea' のまま残るので、仕分け後に別プロジェクトへ移っても一覧から辿れる。
 */
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../../app/api";
import type { Task, TaskStatus } from "../../app/types";
import { useT, type TranslationKey } from "../../app/i18n";
import { useToast } from "../../components/Toast";

//: T61「送った本人が後から確認・修正・取り下げできる」。編集・取り下げは
//: done/withdrawn（済んだもの）には出さない——終わった意見を直す・取り下げる意味がない
//: （バックエンドの状態機械でも done は終端で withdrawn へ遷移できない）。
const EDITABLE_STATUSES: TaskStatus[] = ["hold", "todo", "doing", "waiting", "resident"];

const STATUS_CLASS: Record<TaskStatus, string> = {
  hold: "st-hold",
  todo: "st-todo",
  doing: "st-doing",
  waiting: "st-doing",
  resident: "st-doing",
  done: "st-done",
  withdrawn: "st-withdrawn",
};

const STATUS_KEY: Record<TaskStatus, TranslationKey> = {
  hold: "ideas.status.hold",
  todo: "ideas.status.todo",
  doing: "ideas.status.doing",
  waiting: "ideas.status.waiting",
  resident: "ideas.status.resident",
  done: "ideas.status.done",
  withdrawn: "ideas.status.withdrawn",
};

function IdeaStatusBadge({ status }: { status: TaskStatus | string }) {
  const t = useT();
  const known = status in STATUS_KEY;
  const cls = known ? STATUS_CLASS[status as TaskStatus] : "st-todo";
  const label = known ? t(STATUS_KEY[status as TaskStatus]) : status;
  return <span className={"badge-st " + cls}>{label}</span>;
}

export function IdeaList({ reloadKey }: { reloadKey: number }) {
  const t = useT();
  const [items, setItems] = useState<Task[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [withdrawingId, setWithdrawingId] = useState<string | null>(null);

  const load = useCallback(() => {
    api<{ items: Task[] }>("/ideas")
      .then((res) => setItems(res.items))
      .catch((err) => setError(err instanceof ApiError ? err.message : t("ideas.list.loadFailed")));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    let cancelled = false;
    api<{ items: Task[] }>("/ideas")
      .then((res) => {
        if (cancelled) return;
        setItems(res.items);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : t("ideas.list.loadFailed"));
      });
    return () => {
      cancelled = true;
    };
  }, [reloadKey, t]);

  return (
    <section className="panel" id="panel-idea-list">
      <div className="panel-head">
        <h2>{t("ideas.list.heading")}</h2>
      </div>
      {error && <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>}
      {!error && items === null && <p className="panel-note">{t("common.loading")}</p>}
      {!error && items !== null && items.length === 0 && <p className="panel-note">{t("ideas.list.empty")}</p>}
      {!error && items !== null && items.length > 0 && (
        <div className="rows">
          {[...items]
            .reverse()
            .map((it) =>
              editingId === it.id ? (
                <IdeaEditRow
                  key={it.id}
                  item={it}
                  onDone={() => {
                    setEditingId(null);
                    load();
                  }}
                  onCancel={() => setEditingId(null)}
                />
              ) : withdrawingId === it.id ? (
                <IdeaWithdrawRow
                  key={it.id}
                  item={it}
                  onDone={() => {
                    setWithdrawingId(null);
                    load();
                  }}
                  onCancel={() => setWithdrawingId(null)}
                />
              ) : (
                <div className="row-item" key={it.id}>
                  <span className="row-id">{it.id}</span>
                  <span className="row-title">{it.title}</span>
                  <IdeaStatusBadge status={it.status} />
                  {EDITABLE_STATUSES.includes(it.status) && (
                    <>
                      <button className="btn btn-small btn-ghost" type="button" onClick={() => setEditingId(it.id)}>
                        {t("ideas.list.edit")}
                      </button>
                      <button className="btn btn-small btn-ghost" type="button" onClick={() => setWithdrawingId(it.id)}>
                        {t("ideas.list.withdraw")}
                      </button>
                    </>
                  )}
                </div>
              )
            )}
        </div>
      )}
    </section>
  );
}

function IdeaEditRow({ item, onDone, onCancel }: { item: Task; onDone: () => void; onCancel: () => void }) {
  const t = useT();
  const { show } = useToast();
  const [title, setTitle] = useState(item.title);
  const [body, setBody] = useState(item.body || "");
  const [busy, setBusy] = useState(false);

  const save = async () => {
    if (!title.trim() || !body.trim()) return;
    setBusy(true);
    try {
      await api(`/ideas/${encodeURIComponent(item.id)}`, { method: "PATCH", body: { title, body } });
      show(t("ideas.list.updated", { id: item.id }), "ok", 3000);
      onDone();
    } catch (err) {
      show(err instanceof ApiError ? err.message : t("ideas.list.updateFailed"), "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="row-item form-inline" style={{ flexWrap: "wrap" }}>
      <span className="row-id">{item.id}</span>
      <input className="form-input" style={{ flex: 1, minWidth: 160 }} value={title} onChange={(e) => setTitle(e.target.value)} aria-label={t("ideas.list.titleAria")} disabled={busy} />
      <textarea className="form-textarea" style={{ flexBasis: "100%" }} value={body} onChange={(e) => setBody(e.target.value)} aria-label={t("ideas.list.bodyAria")} disabled={busy} />
      <button className="btn btn-small btn-primary" type="button" disabled={busy} onClick={save}>
        {t("ideas.list.save")}
      </button>
      <button className="btn btn-small" type="button" disabled={busy} onClick={onCancel}>
        {t("common.cancel")}
      </button>
    </div>
  );
}

function IdeaWithdrawRow({ item, onDone, onCancel }: { item: Task; onDone: () => void; onCancel: () => void }) {
  const t = useT();
  const { show } = useToast();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const confirm = async () => {
    if (!note.trim()) return;
    setBusy(true);
    try {
      await api(`/tasks/task/${encodeURIComponent(item.id)}/status`, { method: "POST", body: { status: "withdrawn", note } });
      show(t("ideas.list.withdrawn", { id: item.id }), "ok", 3000);
      onDone();
    } catch (err) {
      show(err instanceof ApiError ? err.message : t("ideas.list.withdrawFailed"), "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="row-item form-inline" style={{ flexWrap: "wrap" }}>
      <span className="row-id">{item.id}</span>
      <span className="row-title">{item.title}</span>
      <input
        className="form-input"
        style={{ flex: 1, minWidth: 160 }}
        placeholder={t("ideas.list.withdrawReasonPlaceholder")}
        value={note}
        onChange={(e) => setNote(e.target.value)}
        aria-label={t("ideas.list.withdrawReasonPlaceholder")}
        disabled={busy}
      />
      <button className="btn btn-small btn-primary" type="button" disabled={busy || !note.trim()} onClick={confirm}>
        {t("ideas.list.withdrawConfirm")}
      </button>
      <button className="btn btn-small" type="button" disabled={busy} onClick={onCancel}>
        {t("common.cancel")}
      </button>
    </div>
  );
}
