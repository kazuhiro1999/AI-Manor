import { useState } from "react";
import type { ModuleDefinition } from "../../app/module";
import { usePolling } from "../../app/polling";
import { api, ApiError } from "../../app/api";
import type { SecretaryData } from "../../app/types";
import { useToast } from "../../components/Toast";
import { ScreenHeader } from "../../components/ScreenHeader";
import { formatDay, useT } from "../../app/i18n";

function SecretaryScreen() {
  const t = useT();
  const { data, error, reload } = usePolling<SecretaryData>("/secretary", 5000);
  const { show } = useToast();

  const [remindText, setRemindText] = useState("");
  const [remindOn, setRemindOn] = useState(new Date().toISOString().slice(0, 10));
  const [remindAt, setRemindAt] = useState("");

  const [evTitle, setEvTitle] = useState("");
  const [evStart, setEvStart] = useState(new Date().toISOString().slice(0, 10));

  const title = t("nav.secretary");
  const description = t("secretary.description");

  // ScreenHeader（ADR-010 D7）は読み込み中・エラー・未導入のときも出す——
  // 「ここで何をするか」は画面の状態に関わらず見えるべきなので。
  if (error) {
    return (
      <div className="view" id="view-secretary">
        <ScreenHeader title={title} description={description} />
        <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="view" id="view-secretary">
        <ScreenHeader title={title} description={description} />
        <p className="panel-note">{t("common.loading")}</p>
      </div>
    );
  }
  if (!data.available) {
    return (
      <div className="view" id="view-secretary">
        <ScreenHeader title={title} description={description} />
        <p className="panel-note">{t("common.staffNotSetUp", { agent: t("agent.secretary"), id: "secretary" })}</p>
      </div>
    );
  }

  const addReminder = async () => {
    if (!remindText.trim()) return;
    try {
      await api("/secretary/reminder", { method: "POST", body: { text: remindText, on: remindOn, at: remindAt || undefined } });
      setRemindText("");
      show(t("secretary.reminders.added"), "ok", 3000);
      reload();
    } catch (err) {
      show(t("errors.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    }
  };

  const doneReminder = async (id: number) => {
    try {
      await api(`/secretary/reminder/${id}/done`, { method: "POST", body: {} });
      show(t("secretary.reminders.done"), "ok", 3000);
      reload();
    } catch (err) {
      show(t("errors.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    }
  };

  const addEvent = async () => {
    if (!evTitle.trim()) return;
    try {
      await api("/secretary/event", { method: "POST", body: { title: evTitle, start: evStart } });
      setEvTitle("");
      show(t("secretary.event.added"), "ok", 3000);
      reload();
    } catch (err) {
      show(t("errors.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    }
  };

  return (
    <div className="view" id="view-secretary">
      <ScreenHeader title={title} description={description} />
      <section className="panel panel-primary">
        <div className="panel-head">
          <h2>{t("secretary.agenda.heading")}</h2>
        </div>
        <div className="rows">
          {!(data.agenda || []).length && <p className="panel-note">{t("common.none")}</p>}
          {/* a.kind はバックエンド（秘書）が組み立てる分類ラベル（例:「控え」「予定」）で、
           * house モジュールの today ラベルと同じ理由（実行時の動的データ、範囲外）で
           * 訳さない。 */}
          {(data.agenda || []).map((a, i) => (
            <div className="row-item" key={i}>
              <span className="row-id">
                {a.overdue ? `${t("secretary.agenda.overdue")} ` : ""}
                {formatDay(a.date, t)}
              </span>
              <span className="row-title">
                [{a.kind}] {a.title}
                {a.detail ? <span className="panel-note" style={{ margin: 0 }}>{" "}{a.detail}</span> : null}
              </span>
            </div>
          ))}
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{t("secretary.reminders.heading")}</h2>
        </div>
        <div className="rows">
          {!(data.reminders_open || []).length && <p className="panel-note">{t("common.none")}</p>}
          {(data.reminders_open || []).map((r) => (
            <div className="row-item" key={r.id}>
              <span className="row-id">
                {r.on_date}
                {r.at_time ? " " + r.at_time : ""}
              </span>
              <span className="row-title">{r.text}</span>
              <button className="btn btn-small" type="button" onClick={() => doneReminder(r.id)}>
                {t("secretary.reminders.markDone")}
              </button>
            </div>
          ))}
        </div>
        <div className="form-inline" style={{ marginTop: 10 }}>
          <input
            className="form-input"
            placeholder={t("secretary.reminders.textPlaceholder")}
            value={remindText}
            onChange={(e) => setRemindText(e.target.value)}
          />
          <input className="form-input" style={{ maxWidth: 150 }} type="date" value={remindOn} onChange={(e) => setRemindOn(e.target.value)} />
          <input
            className="form-input"
            style={{ maxWidth: 100 }}
            placeholder={t("secretary.reminders.timePlaceholder")}
            value={remindAt}
            onChange={(e) => setRemindAt(e.target.value)}
          />
          <button className="btn btn-primary btn-small" type="button" onClick={addReminder}>
            {t("common.add")}
          </button>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{t("secretary.event.heading")}</h2>
        </div>
        <div className="form-inline">
          <input
            className="form-input"
            placeholder={t("secretary.event.titlePlaceholder")}
            value={evTitle}
            onChange={(e) => setEvTitle(e.target.value)}
          />
          <input className="form-input" style={{ maxWidth: 150 }} type="date" value={evStart} onChange={(e) => setEvStart(e.target.value)} />
          <button className="btn btn-primary btn-small" type="button" onClick={addEvent}>
            {t("common.add")}
          </button>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{t("secretary.inbox.heading")}</h2>
        </div>
        <div className="rows">
          {!(data.inbox_unrouted || []).length && <p className="panel-note">{t("common.none")}</p>}
          {(data.inbox_unrouted || []).map((i) => (
            <div className="row-item" key={i.id}>
              <span className="row-id">{i.received_at}</span>
              <span className="row-title">{i.ref}</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

export const secretaryModule: ModuleDefinition = {
  id: "secretary",
  title: "nav.secretary",
  description: "secretary.description",
  icon: "🗂",
  order: 7,
  routes: [{ index: true, element: <SecretaryScreen /> }],
};
