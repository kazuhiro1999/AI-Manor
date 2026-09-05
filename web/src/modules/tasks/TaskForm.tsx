/* manor web — タスクの起票フォーム（`POST /api/v1/tasks/task`）。
 * `--class` の選択肢は `GET /api/v1/meta` の `task_classes`（policy.classes() 由来。
 * 執事の裁定1）があればそれを使う。無ければ（古いバックエンド・mock 未対応）固定の
 * 一覧にフォールバックする。
 */
import { useEffect, useState } from "react";
import { api, ApiError } from "../../app/api";
import { useToast } from "../../components/Toast";
import type { Meta, TaskClass } from "../../app/types";
import { useT } from "../../app/i18n";

const FALLBACK_CLASS_OPTIONS: TaskClass[] = ["L1", "L2", "L3", "HG"].map((id) => ({
  id,
  label: id,
  default_level: id,
  fixed: false,
}));

export function TaskForm({ onCreated }: { onCreated?: () => void }) {
  const t = useT();
  const [title, setTitle] = useState("");
  const [project, setProject] = useState("");
  const [classOptions, setClassOptions] = useState<TaskClass[]>(FALLBACK_CLASS_OPTIONS);
  const [cls, setCls] = useState("L1");
  const [goal, setGoal] = useState("");
  const [now, setNow] = useState("");
  const [next, setNext] = useState("");
  const [due, setDue] = useState("");
  const [body, setBody] = useState("");
  const [recommendation, setRecommendation] = useState("");
  const [evidence, setEvidence] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { show } = useToast();

  useEffect(() => {
    let cancelled = false;
    api<Meta>("/meta")
      .then((meta) => {
        if (cancelled) return;
        if (meta.task_classes && meta.task_classes.length) {
          setClassOptions(meta.task_classes);
          setCls(meta.task_classes[0].id);
        }
      })
      .catch(() => {
        /* meta が取れなくても固定一覧のまま使える */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const submit = async () => {
    setError(null);
    if (!title.trim()) {
      setError(t("tasks.form.titleRequired"));
      return;
    }
    const selected = classOptions.find((c) => c.id === cls);
    if (selected?.default_level === "HG" && !recommendation.trim()) {
      setError(t("tasks.form.hgRecommendationRequired"));
      return;
    }
    setBusy(true);
    try {
      const res = await api<{ id: string }>("/tasks/task", {
        method: "POST",
        body: {
          title,
          project: project || undefined,
          cls,
          goal: goal || undefined,
          now: now || undefined,
          next: next || undefined,
          due: due || undefined,
          body: body || undefined,
          recommendation: recommendation || undefined,
          evidence: evidence || undefined,
        },
      });
      show(t("tasks.form.created", { id: res.id }), "ok", 4000);
      setTitle("");
      setGoal("");
      setNow("");
      setNext("");
      setDue("");
      setBody("");
      setRecommendation("");
      setEvidence("");
      onCreated?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("tasks.form.createFailed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel" id="panel-task-form">
      <div className="panel-head">
        <h2>{t("tasks.form.heading")}</h2>
      </div>
      <div className="form-grid">
        <div className="form-row">
          <label htmlFor="tf-title">{t("tasks.form.titleLabel")}</label>
          <input id="tf-title" className="form-input" value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div className="form-row">
          <label htmlFor="tf-project">{t("tasks.form.projectLabel")}</label>
          <input id="tf-project" className="form-input" value={project} onChange={(e) => setProject(e.target.value)} />
        </div>
        <div className="form-row">
          <label htmlFor="tf-cls">{t("tasks.form.classLabel")}</label>
          <select id="tf-cls" className="form-select" value={cls} onChange={(e) => setCls(e.target.value)}>
            {classOptions.map((c) => (
              <option key={c.id} value={c.id}>
                {c.label}（{c.default_level}）
              </option>
            ))}
          </select>
        </div>
        <div className="form-row">
          <label htmlFor="tf-goal">{t("tasks.form.goalLabel")}</label>
          <input id="tf-goal" className="form-input" value={goal} onChange={(e) => setGoal(e.target.value)} />
        </div>
        <div className="form-row">
          <label htmlFor="tf-now">{t("tasks.form.nowLabel")}</label>
          <input id="tf-now" className="form-input" value={now} onChange={(e) => setNow(e.target.value)} />
        </div>
        <div className="form-row">
          <label htmlFor="tf-next">{t("tasks.form.nextLabel")}</label>
          <input id="tf-next" className="form-input" value={next} onChange={(e) => setNext(e.target.value)} />
        </div>
        <div className="form-row">
          <label htmlFor="tf-due">{t("tasks.form.dueLabel")}</label>
          <input id="tf-due" className="form-input" value={due} onChange={(e) => setDue(e.target.value)} />
        </div>
        <div className="form-row">
          <label htmlFor="tf-body">{t("tasks.form.bodyLabel")}</label>
          <textarea id="tf-body" className="form-textarea" value={body} onChange={(e) => setBody(e.target.value)} />
        </div>
        <div className="form-row">
          <label htmlFor="tf-rec">{t("tasks.form.recommendationLabel")}</label>
          <input id="tf-rec" className="form-input" value={recommendation} onChange={(e) => setRecommendation(e.target.value)} />
        </div>
        <div className="form-row">
          <label htmlFor="tf-evidence">{t("tasks.form.evidenceLabel")}</label>
          <textarea id="tf-evidence" className="form-textarea" value={evidence} onChange={(e) => setEvidence(e.target.value)} />
        </div>
        {error && <div className="form-error">{error}</div>}
        <div className="form-actions">
          <button className="btn btn-primary" type="button" disabled={busy} onClick={submit}>
            {t("tasks.form.submit")}
          </button>
        </div>
      </div>
    </section>
  );
}
