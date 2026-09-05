import { useRef, useState } from "react";
import type { ModuleDefinition } from "../../app/module";
import { apiUpload, ApiError } from "../../app/api";
import type { ImportCommitResult, ImportFormat, ImportPreview } from "../../app/types";
import { useToast } from "../../components/Toast";
import { ScreenHeader } from "../../components/ScreenHeader";
import { useT } from "../../app/i18n";

const FORMATS: ImportFormat[] = ["generic", "zaim", "moneyforward"];

function ImportsScreen() {
  const t = useT();
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [format, setFormat] = useState<ImportFormat>("generic");
  // 既定の列名対応表は実際に読みに行く CSV の列名（日本語の家計簿アプリの書式）そのもので、
  // 画面の文言ではなく機能上のデフォルト値——言語を変えても書き換えない
  // （ADR-012 D12 の「主人が入れたデータ」に準じる。ここを英語化すると挙動が変わってしまう）。
  const [map, setMap] = useState("date=日付,amount=金額,category=カテゴリ,memo=内容,kind=収支");
  const [encoding, setEncoding] = useState("utf-8");
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [result, setResult] = useState<ImportCommitResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { show } = useToast();

  const buildForm = () => {
    const form = new FormData();
    if (file) form.append("file", file);
    form.append("format", format);
    form.append("encoding", encoding);
    if (format === "generic") form.append("map", map);
    return form;
  };

  const doPreview = async () => {
    if (!file) {
      setError(t("imports.selectFile"));
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const res = await apiUpload<ImportPreview>("/imports/money/preview", buildForm());
      setPreview(res);
      setResult(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("imports.previewFailed"));
    } finally {
      setBusy(false);
    }
  };

  const doCommit = async () => {
    if (!file) return;
    setBusy(true);
    try {
      const res = await apiUpload<ImportCommitResult>("/imports/money/commit", buildForm());
      setResult(res);
      show(t("imports.committed", { inserted: res.inserted, skipped: res.skipped }), "ok", 5000);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("imports.commitFailed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="view" id="view-imports">
      <ScreenHeader title={t("nav.imports")} description={t("imports.description")} />
      <section className="panel panel-primary">
        <div className="panel-head">
          <h2>{t("imports.heading")}</h2>
        </div>
        <div className="form-grid" style={{ maxWidth: 560 }}>
          <div className="form-row">
            <label>{t("imports.csvFileLabel")}</label>
            <input
              ref={fileRef}
              type="file"
              accept=".csv,text/csv"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
          </div>
          <div className="form-row">
            <label>{t("imports.formatLabel")}</label>
            <select className="form-select" value={format} onChange={(e) => setFormat(e.target.value as ImportFormat)}>
              {FORMATS.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </select>
          </div>
          {format === "generic" && (
            <div className="form-row">
              <label>{t("imports.mapLabel")}</label>
              <input className="form-input" value={map} onChange={(e) => setMap(e.target.value)} />
            </div>
          )}
          <div className="form-row">
            <label>{t("imports.encodingLabel")}</label>
            <select className="form-select" value={encoding} onChange={(e) => setEncoding(e.target.value)}>
              <option value="utf-8">utf-8</option>
              <option value="cp932">cp932</option>
            </select>
          </div>
          {error && <div className="form-error">{error}</div>}
          <div className="form-actions">
            <button className="btn btn-primary" type="button" disabled={busy} onClick={doPreview}>
              {t("imports.previewButton")}
            </button>
            {preview && (
              <button className="btn" type="button" disabled={busy} onClick={doCommit}>
                {t("imports.commitButton")}
              </button>
            )}
          </div>
        </div>
      </section>

      {preview && (
        <section className="panel">
          <div className="panel-head">
            <h2>{t("imports.previewHeading")}</h2>
            <span className="count">
              {t("imports.previewSummary", {
                total: preview.total,
                duplicates: preview.duplicates.length,
                unreadable: preview.unreadable.length,
              })}
            </span>
          </div>
          <div className="rows">
            {/* rows（取り込み対象）と duplicates（重複で除外される行）は API では別配列
                （src/manor/staff/steward/importer.py の ImportResult）だが、画面では
                行番号順に1つへ合わせて「重複は灰色」（ADR-005 §3）を実現する。 */}
            {[...preview.rows.map((r) => ({ ...r, duplicate: false })), ...preview.duplicates.map((r) => ({ ...r, duplicate: true }))]
              .sort((a, b) => a.line - b.line)
              .map((r) => (
                <div className={"row-item import-row" + (r.duplicate ? " duplicate" : "")} key={r.line}>
                  <span className="row-id">{r.date}</span>
                  <span className="row-title">
                    {r.category} {r.memo || ""}
                  </span>
                  <span className="row-id">
                    {r.kind === "income" ? "+" : "-"}
                    {t("money.amountYen", { n: r.amount })}
                  </span>
                  {r.duplicate && <span className="badge-st st-withdrawn">{t("imports.duplicate")}</span>}
                </div>
              ))}
            {preview.unreadable.map((u, i) => (
              <div className="row-item import-row unreadable" key={"u" + i}>
                <span className="row-id">{t("imports.lineLabel", { line: u.line })}</span>
                <span className="row-title">
                  {Object.entries(u.raw)
                    .map(([k, v]) => `${k}=${v}`)
                    .join(" / ")}
                </span>
                <span className="row-id">{u.reason}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {result && (
        <section className="panel">
          <div className="panel-head">
            <h2>{t("imports.resultHeading")}</h2>
          </div>
          <p>{t("imports.resultSummary", { inserted: result.inserted, skipped: result.skipped })}</p>
        </section>
      )}
    </div>
  );
}

export const importsModule: ModuleDefinition = {
  id: "imports",
  title: "nav.imports",
  description: "imports.description",
  icon: "📥",
  order: 9,
  routes: [{ index: true, element: <ImportsScreen /> }],
};
