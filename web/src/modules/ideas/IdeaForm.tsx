/* manor web — 意見箱の起票フォーム（`POST /api/v1/ideas`）。
 * `TaskForm.tsx` を手本にするが、書き写さない——本文1つだけ（題名は先頭行から機械が取る。
 * `task_mod.add_idea` 参照。主人に2度書かせない。夜勤 N6）。
 */
import { useState } from "react";
import { api, ApiError } from "../../app/api";
import { useToast } from "../../components/Toast";
import { useT } from "../../app/i18n";

export function IdeaForm({ onCreated }: { onCreated?: () => void }) {
  const t = useT();
  const [body, setBody] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { show } = useToast();

  const submit = async () => {
    setError(null);
    if (!body.trim()) {
      setError(t("ideas.form.bodyRequired"));
      return;
    }
    setBusy(true);
    try {
      const res = await api<{ id: string }>("/ideas", { method: "POST", body: { body } });
      show(t("ideas.form.created", { id: res.id }), "ok", 4000);
      setBody("");
      onCreated?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("ideas.form.createFailed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel" id="panel-idea-form">
      <div className="panel-head">
        <h2>{t("ideas.form.heading")}</h2>
      </div>
      <div className="form-grid">
        <div className="form-row">
          <label htmlFor="if-body">{t("ideas.form.bodyLabel")}</label>
          <textarea id="if-body" className="form-textarea" value={body} onChange={(e) => setBody(e.target.value)} />
        </div>
        {error && <div className="form-error">{error}</div>}
        <div className="form-actions">
          <button className="btn btn-primary" type="button" disabled={busy} onClick={submit}>
            {t("ideas.form.submit")}
          </button>
        </div>
      </div>
    </section>
  );
}
