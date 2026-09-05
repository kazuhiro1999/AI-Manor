import { useMemo, useState } from "react";
import type { ModuleDefinition } from "../../app/module";
import { usePolling } from "../../app/polling";
import { api, ApiError } from "../../app/api";
import type { Rule, RuleScope } from "../../app/types";
import { Markdown } from "../../components/Markdown";
import { useToast } from "../../components/Toast";
import { ScreenHeader } from "../../components/ScreenHeader";
import { useT, type TranslationKey } from "../../app/i18n";

const SCOPE_OPTIONS: RuleScope[] = ["family", "adults", "kids", "guests", "staff"];
const SCOPE_KEY: Record<RuleScope, TranslationKey> = {
  family: "ruleScope.family",
  adults: "ruleScope.adults",
  kids: "ruleScope.kids",
  guests: "ruleScope.guests",
  staff: "ruleScope.staff",
};

function RulesScreen() {
  const t = useT();
  const [tag, setTag] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [q, setQ] = useState("");
  const path = `/rules?tag=${encodeURIComponent(tag)}&all=${showAll}`;
  const { data: rules, error, reload } = usePolling<Rule[]>(path, 5000);
  const { show } = useToast();

  const [editing, setEditing] = useState<Rule | null>(null);
  const [creating, setCreating] = useState(false);

  const filtered = useMemo(() => {
    const list = rules || [];
    if (!q.trim()) return list;
    const needle = q.trim().toLowerCase();
    return list.filter((r) => r.title.toLowerCase().includes(needle) || r.body.toLowerCase().includes(needle));
  }, [rules, q]);

  const archive = async (id: number) => {
    try {
      await api(`/rules/${id}`, { method: "DELETE" });
      show(t("rules.archived"), "ok", 3000);
      reload();
    } catch (err) {
      show(t("errors.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    }
  };

  const title = t("nav.rules");
  const description = t("rules.description");

  if (error) {
    return (
      <div className="view" id="view-rules">
        <ScreenHeader title={title} description={description} />
        <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>
      </div>
    );
  }

  return (
    <div className="view" id="view-rules">
      <ScreenHeader title={title} description={description} />
      <section className="panel panel-primary">
        <div className="panel-head">
          <h2>{t("rules.heading")}</h2>
          <span className="count">{t("component.foldBlock.count", { count: filtered.length })}</span>
          <button className="btn btn-small btn-primary" style={{ marginLeft: "auto" }} type="button" onClick={() => setCreating(true)}>
            {t("rules.add")}
          </button>
        </div>
        <div className="form-inline">
          <input className="form-input" placeholder={t("rules.searchPlaceholder")} value={q} onChange={(e) => setQ(e.target.value)} />
          <input
            className="form-input"
            style={{ maxWidth: 160 }}
            placeholder={t("rules.tagFilterPlaceholder")}
            value={tag}
            onChange={(e) => setTag(e.target.value)}
          />
          <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12 }}>
            <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> {t("rules.includeArchived")}
          </label>
        </div>
        <div className="cards" style={{ marginTop: 10 }}>
          {!filtered.length && <p className="panel-note">{t("common.none")}</p>}
          {filtered.map((r) => (
            <div className="card" key={r.id}>
              <div className="card-head">
                <span className="card-title">{r.title}</span>
                <span className="card-pj">{r.scope in SCOPE_KEY ? t(SCOPE_KEY[r.scope]) : r.scope}</span>
                {r.archived_at && <span className="badge-st st-withdrawn">{t("common.archived")}</span>}
              </div>
              <Markdown text={r.body} />
              <div className="card-actions">
                <span className="panel-note">{t("rules.tags", { tags: r.tags || t("common.none") })}</span>
                <button className="btn btn-small" type="button" onClick={() => setEditing(r)}>
                  {t("common.edit")}
                </button>
                {!r.archived_at && (
                  <button className="btn btn-small btn-danger" type="button" onClick={() => archive(r.id)}>
                    {t("common.archive")}
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      </section>
      {(creating || editing) && (
        <RuleEditor
          rule={editing}
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
          onSaved={() => {
            setCreating(false);
            setEditing(null);
            reload();
          }}
        />
      )}
    </div>
  );
}

function RuleEditor({ rule, onClose, onSaved }: { rule: Rule | null; onClose: () => void; onSaved: () => void }) {
  const t = useT();
  const [title, setTitle] = useState(rule?.title || "");
  const [body, setBody] = useState(rule?.body || "");
  const [scope, setScope] = useState<RuleScope>(rule?.scope || "family");
  const [tags, setTags] = useState(rule?.tags || "");
  const [error, setError] = useState<string | null>(null);
  const { show } = useToast();

  const save = async () => {
    if (!title.trim()) {
      setError(t("rules.titleRequired"));
      return;
    }
    try {
      if (rule) {
        await api(`/rules/${rule.id}`, { method: "PUT", body: { title, body, scope, tags } });
      } else {
        await api("/rules", { method: "POST", body: { title, body, scope, tags } });
      }
      show(t("rules.saved"), "ok", 3000);
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("errors.saveFailed", { reason: t("common.unknown") }));
    }
  };

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>{rule ? t("rules.editHeading") : t("rules.addHeading")}</h2>
      </div>
      <div className="form-grid" style={{ maxWidth: 640 }}>
        <div className="form-row">
          <label>{t("rules.titleLabel")}</label>
          <input className="form-input" value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div className="form-row">
          <label>{t("rules.scopeLabel")}</label>
          <select className="form-select" value={scope} onChange={(e) => setScope(e.target.value as RuleScope)}>
            {SCOPE_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {t(SCOPE_KEY[s])}
              </option>
            ))}
          </select>
        </div>
        <div className="form-row">
          <label>{t("rules.tagsLabel")}</label>
          <input className="form-input" value={tags} onChange={(e) => setTags(e.target.value)} />
        </div>
        <div style={{ display: "flex", gap: 12 }}>
          <div className="form-row" style={{ flex: 1 }}>
            <label>{t("rules.bodyLabel")}</label>
            <textarea className="form-textarea" value={body} onChange={(e) => setBody(e.target.value)} />
          </div>
          <div className="form-row" style={{ flex: 1 }}>
            <label>{t("rules.preview")}</label>
            <div className="detail-box" style={{ minHeight: 90 }}>
              <Markdown text={body} />
            </div>
          </div>
        </div>
        {error && <div className="form-error">{error}</div>}
        <div className="form-actions">
          <button className="btn btn-primary" type="button" onClick={save}>
            {t("common.save")}
          </button>
          <button className="btn" type="button" onClick={onClose}>
            {t("common.cancel")}
          </button>
        </div>
      </div>
    </section>
  );
}

export const rulesModule: ModuleDefinition = {
  id: "rules",
  title: "nav.rules",
  description: "rules.description",
  icon: "📜",
  order: 8,
  routes: [{ index: true, element: <RulesScreen /> }],
};
