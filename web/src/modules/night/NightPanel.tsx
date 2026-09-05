/* manor web — 夜勤の作業報告を読むだけの画面（board の night タブの移植）。
 * `src/manor/board/api_night.py` の `/api/night/reports` `/api/night/reports/{date}` を
 * そのまま `/api/v1/night/reports...` へ引き写した契約で読む。書き込み口はない。
 *
 * `report.parsed` の中身（題・要約・各タスクの題や本文）は夜勤の担当が書いた実際の報告文
 * ——執事の成果物であり、`home/` 配下の②に相当する動的なテキストなので、ここでは
 * 訳さない（ADR-012 D12「主人が入れたデータ」に準じる扱い。担当が書いた自由文である点は
 * 主人の入力そのものではないが、静的な UI 文言ではなく実行時に生成される内容という点で
 * 同じ理由で対象外——曖昧だった点として報告に書く）。
 */
import { useEffect, useState } from "react";
import { api, ApiError } from "../../app/api";
import type { NightReport } from "../../app/types";
import { Markdown } from "../../components/Markdown";
import { useT, type TranslationKey } from "../../app/i18n";

const NIGHT_STATE_CLASS: Record<string, string> = { done: "st-done", hold: "st-hold", other: "st-todo" };
const NIGHT_STATE_KEY: Record<string, TranslationKey> = {
  done: "night.state.done",
  hold: "night.state.hold",
  other: "night.state.other",
};

export function NightPanel() {
  const t = useT();
  const [dates, setDates] = useState<string[]>([]);
  const [date, setDate] = useState<string | null>(null);
  const [report, setReport] = useState<NightReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<{ dates: string[] }>("/night/reports")
      .then((res) => {
        setDates(res.dates || []);
        if (res.dates?.length) setDate(res.dates[0]);
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : t("common.unknown")));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!date) return;
    setReport(null);
    api<NightReport>(`/night/reports/${encodeURIComponent(date)}`)
      .then(setReport)
      .catch((err) => setError(err instanceof ApiError ? err.message : t("common.unknown")));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [date]);

  return (
    <section className="panel" id="panel-night">
      <div className="panel-head">
        <h2>{t("night.heading")}</h2>
        {dates.length > 0 && (
          <select
            id="night-date-select"
            className="ruling-input"
            style={{ flex: "0 0 auto", maxWidth: 170 }}
            value={date || ""}
            onChange={(e) => setDate(e.target.value)}
          >
            {dates.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        )}
      </div>
      <p className="panel-note">
        {t("night.hint.prefix")} <code>{t("night.hint.path")}</code> {t("night.hint.suffix")}
      </p>
      <div id="night-body">
        {error && <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>}
        {!error && !dates.length && <p className="panel-note">{t("night.empty")}</p>}
        {!error && dates.length > 0 && !report && <p className="panel-note">{t("common.loading")}</p>}
        {report && <NightReportView report={report} />}
      </div>
    </section>
  );
}

function NightReportView({ report }: { report: NightReport }) {
  const t = useT();
  const parsed = report.parsed;
  if (!parsed || !parsed.ok) {
    return (
      <>
        <p className="panel-note">{t("night.unstructuredNote")}</p>
        <Markdown text={report.text} />
      </>
    );
  }
  return (
    <>
      {parsed.title && <h3 className="night-title">{parsed.title}</h3>}
      {(parsed.summary || []).length > 0 && (
        <div className="night-summary">
          {parsed.summary!.map((p, i) => (
            <Markdown key={i} text={p} />
          ))}
        </div>
      )}
      <div className="night-tasks">
        {parsed.tasks.map((tk, i) => (
          <div className="card night-card" key={i}>
            <div className="card-head">
              <span className="card-title">
                {tk.number ? tk.number + " " : ""}
                {tk.title}
              </span>
              {tk.state && (
                <span className={"badge-st " + (NIGHT_STATE_CLASS[tk.state] || "st-todo")}>
                  {tk.state in NIGHT_STATE_KEY ? t(NIGHT_STATE_KEY[tk.state]) : tk.state}
                </span>
              )}
            </div>
            {(tk.fields || []).map((f, fi) => (
              <div className="night-field" key={fi}>
                <strong>{f.label}</strong>
                <Markdown text={f.text} />
              </div>
            ))}
          </div>
        ))}
      </div>
    </>
  );
}
