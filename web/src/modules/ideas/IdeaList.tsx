/* manor web — 過去に登録した意見の一覧（`GET /api/v1/ideas`）。
 * 新しい順。状態の札（hold=仕分け待ち／todo=受付済み／doing・waiting・resident=対応中／
 * done=完了✓／withdrawn=見送り。夜勤 N6・主人のご要望 2026-09-08。todo を「対応中」と
 * 出していたのを 2026-09-14 に分けた——仕分け済みなだけで誰も着手していない件が「対応中」に見え、
 * 主人が「何がブロッカーか」と問われた）。
 * source='idea' のまま残るので、仕分け後に別プロジェクトへ移っても一覧から辿れる。
 */
import { useEffect, useState } from "react";
import { api, ApiError } from "../../app/api";
import type { Task, TaskStatus } from "../../app/types";
import { useT, type TranslationKey } from "../../app/i18n";

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
            .map((it) => (
              <div className="row-item" key={it.id}>
                <span className="row-id">{it.id}</span>
                <span className="row-title">{it.title}</span>
                <IdeaStatusBadge status={it.status} />
              </div>
            ))}
        </div>
      )}
    </section>
  );
}
