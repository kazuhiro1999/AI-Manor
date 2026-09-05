/* manor web — 拡張機能（ADR-009 D7）。サイドバー最下部。VOICEVOX・Tailscale 等、外部の
 * アプリ・サービスに依存する機能をカードで一覧し、開くと導入手順・設定フォーム・「試す」を出す。
 * **設定手順は画面に出す**——README を読ませない。
 */
import { useEffect, useState } from "react";
import type { ModuleDefinition } from "../../app/module";
import { usePolling } from "../../app/polling";
import { api, ApiError } from "../../app/api";
import { APP_NAME } from "../../app/brand";
import type {
  ExtensionDetail,
  ExtensionField,
  ExtensionOption,
  ExtensionStatus,
  ExtensionSummary,
} from "../../app/types";
import { useToast } from "../../components/Toast";
import { ScreenHeader } from "../../components/ScreenHeader";
import { useT, type TranslationKey } from "../../app/i18n";

// D3: 状態は5つ。判定は道具（detect/check）がやり、名前はここが決める。
const STATUS_CLASS: Record<ExtensionStatus, string> = {
  not_installed: "st-todo",
  needs_config: "st-waiting",
  ready: "st-doing",
  ok: "st-done",
  error: "st-hold",
};
const STATUS_KEY: Record<ExtensionStatus, TranslationKey> = {
  not_installed: "extensionsStatus.not_installed",
  needs_config: "extensionsStatus.needs_config",
  ready: "extensionsStatus.ready",
  ok: "extensionsStatus.ok",
  error: "extensionsStatus.error",
};

function ExtensionStatusChip({ status }: { status: ExtensionStatus }) {
  const t = useT();
  const cls = STATUS_CLASS[status] || "st-todo";
  const label = status in STATUS_KEY ? t(STATUS_KEY[status]) : status;
  return <span className={"badge-st " + cls}>{label}</span>;
}

function ExtensionsScreen() {
  const t = useT();
  const { data: list, error, reload } = usePolling<ExtensionSummary[]>("/extensions", 5000);
  const [openId, setOpenId] = useState<string | null>(null);

  return (
    <div className="view" id="view-extensions">
      <ScreenHeader title={t("nav.extensions")} description={t("extensions.description")} />
      <section className="panel panel-primary">
        <div className="panel-head">
          <h2>{t("extensions.listHeading")}</h2>
          <span className="count">{t("component.foldBlock.count", { count: (list || []).length })}</span>
        </div>
        <p className="panel-note">{t("extensions.hint", { app: APP_NAME })}</p>
        {error && <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>}
        <div className="cards">
          {!list?.length && !error && <p className="panel-note">{t("common.none")}</p>}
          {(list || []).map((ext) => (
            <ExtensionCard
              key={ext.id}
              summary={ext}
              open={openId === ext.id}
              onToggle={() => setOpenId((cur) => (cur === ext.id ? null : ext.id))}
              onChanged={reload}
            />
          ))}
        </div>
      </section>
    </div>
  );
}

function ExtensionCard({
  summary,
  open,
  onToggle,
  onChanged,
}: {
  summary: ExtensionSummary;
  open: boolean;
  onToggle: () => void;
  onChanged: () => void;
}) {
  const t = useT();
  const [detail, setDetail] = useState<ExtensionDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);
  const { show } = useToast();

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setTestResult(null);
    api<ExtensionDetail>(`/extensions/${summary.id}`)
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .catch((err) => {
        if (!cancelled) show(t("errors.loadFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, summary.id]);

  const runTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const d = await api<ExtensionDetail>(`/extensions/${summary.id}/test`, { method: "POST" });
      setDetail(d);
      setTestResult(
        d.status === "ok" ? t("extensions.testSuccess", { reason: d.reason || "" }) : t("extensions.testFailure", { reason: d.reason || t("common.unknown") })
      );
      onChanged();
    } catch (err) {
      setTestResult(t("extensions.testError", { reason: err instanceof ApiError ? err.message : t("common.unknown") }));
    } finally {
      setTesting(false);
    }
  };

  const forget = async () => {
    try {
      const d = await api<ExtensionDetail>(`/extensions/${summary.id}`, { method: "DELETE" });
      setDetail(d);
      setTestResult(null);
      show(t("extensions.forgotten"), "ok", 3000);
      onChanged();
    } catch (err) {
      show(t("errors.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    }
  };

  return (
    <div className="card" data-ext-id={summary.id}>
      <div className="card-head" role="button" tabIndex={0} style={{ cursor: "pointer" }} onClick={onToggle}>
        <span className="card-title">{summary.label}</span>
        <ExtensionStatusChip status={summary.status} />
        {summary.checked_at && <span className="card-days">{t("extensions.checkedAt", { date: summary.checked_at })}</span>}
      </div>
      <p className="panel-note">{summary.summary}</p>
      {summary.status === "error" && summary.reason && <p className="panel-note">{t("extensions.reason", { reason: summary.reason })}</p>}
      {open && (
        <div className="detail-box">
          {loading && <p className="panel-note">{t("common.loading")}</p>}
          {detail && (
            <>
              <div className="card-evidence">
                <div className="card-evidence-label">{t("extensions.installSteps")}</div>
                <ol style={{ margin: "4px 0 0", paddingLeft: 18, fontSize: 12.5 }}>
                  {detail.install_steps.map((step, i) => (
                    <li key={i}>{step}</li>
                  ))}
                </ol>
              </div>
              {detail.manifest.fields.length > 0 && (
                <ExtensionForm
                  extId={summary.id}
                  detail={detail}
                  onSaved={(d) => {
                    setDetail(d);
                    onChanged();
                  }}
                />
              )}
              <div className="card-actions">
                <button className="btn btn-small btn-primary" type="button" disabled={testing} onClick={runTest}>
                  {testing ? t("extensions.testing") : t("extensions.test")}
                </button>
                <button className="btn btn-small btn-danger" type="button" onClick={forget}>
                  {t("extensions.forget")}
                </button>
              </div>
              {testResult && <p className="panel-note">{testResult}</p>}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function ExtensionForm({
  extId,
  detail,
  onSaved,
}: {
  extId: string;
  detail: ExtensionDetail;
  onSaved: (d: ExtensionDetail) => void;
}) {
  const t = useT();
  const [values, setValues] = useState<Record<string, string>>({});
  // options[key] === undefined: まだ取得中。 [] : 取得できた（空 = D5 のフォールバック）。
  const [options, setOptions] = useState<Record<string, ExtensionOption[] | undefined>>({});
  const { show } = useToast();

  useEffect(() => {
    const initial: Record<string, string> = {};
    for (const field of detail.manifest.fields) {
      if (field.kind === "password") {
        initial[field.key] = "";
        continue;
      }
      const v = detail.values[field.key];
      initial[field.key] = v == null ? "" : String(v);
    }
    setValues(initial);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail.id]);

  useEffect(() => {
    let cancelled = false;
    // カードを開いたとき（＝このフォームがマウントされたとき）に動的な選択肢を取りに行く（D5）。
    for (const field of detail.manifest.fields) {
      if (!field.options_from) continue;
      api<ExtensionOption[]>(`/extensions/${extId}/options/${field.options_from}`)
        .then((opts) => {
          if (!cancelled) setOptions((prev) => ({ ...prev, [field.key]: opts }));
        })
        .catch(() => {
          if (!cancelled) setOptions((prev) => ({ ...prev, [field.key]: [] }));
        });
    }
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [extId, detail.id]);

  const save = async () => {
    const body: Record<string, string> = {};
    for (const field of detail.manifest.fields) {
      const v = values[field.key];
      if (v === undefined || v === "") continue; // 空欄は「変更しない」（秘密を空で上書きしない）
      body[field.key] = v;
    }
    try {
      const d = await api<ExtensionDetail>(`/extensions/${extId}`, { method: "PUT", body: { values: body } });
      show(t("extensions.saved"), "ok", 3000);
      onSaved(d);
    } catch (err) {
      show(t("errors.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    }
  };

  return (
    <div className="form-grid" style={{ marginTop: 8 }}>
      {detail.manifest.fields.map((field) => (
        <ExtensionFieldInput
          key={field.key}
          field={field}
          value={values[field.key] ?? ""}
          hasSecret={Boolean(detail.values[`has_${field.key}`])}
          options={options[field.key]}
          onChange={(v) => setValues((prev) => ({ ...prev, [field.key]: v }))}
        />
      ))}
      <div className="form-actions">
        <button className="btn btn-primary btn-small" type="button" onClick={save}>
          {t("common.save")}
        </button>
      </div>
    </div>
  );
}

/** ADR-009 D17: 「親 → 子」の2段で選ばせる。VOICEVOX の話者127件のような長い一覧のため。
 *
 * 保存される値は子の `value` ひとつだけ——**契約は平らなまま**で、見せ方だけを変える。
 * 既に値が入っていれば、その値を含む親を初期表示にする（開き直しても迷子にならない）。 */
function GroupedSelectField({
  id,
  field,
  options,
  value,
  onChange,
}: {
  id: string;
  field: ExtensionField;
  options: ExtensionOption[];
  value: string;
  onChange: (v: string) => void;
}) {
  const t = useT();
  const otherGroup = t("extensions.otherGroup");
  const groups: string[] = [];
  for (const opt of options) {
    const g = opt.group || otherGroup;
    if (!groups.includes(g)) groups.push(g);
  }
  const groupOfValue = options.find((o) => String(o.value) === value)?.group;
  const [group, setGroup] = useState<string>(groupOfValue || "");
  useEffect(() => {
    if (groupOfValue && groupOfValue !== group) setGroup(groupOfValue);
    // 値が外から変わったとき（保存後の再読込など）だけ親を追随させる
  }, [groupOfValue]); // eslint-disable-line react-hooks/exhaustive-deps

  const members = options.filter((o) => (o.group || otherGroup) === group);

  return (
    <div className="form-row">
      <label htmlFor={id}>{field.label}</label>
      <div className="form-inline">
        <select
          id={id}
          className="form-select"
          value={group}
          onChange={(e) => {
            setGroup(e.target.value);
            onChange(""); // 親を変えたら子は選び直し。前の子が残って別人になるのを防ぐ
          }}
        >
          <option value="">{t("extensions.selectPrompt")}</option>
          {groups.map((g) => (
            <option key={g} value={g}>
              {g}
            </option>
          ))}
        </select>
        <select
          className="form-select"
          aria-label={t("extensions.styleAriaLabel", { label: field.label })}
          value={value}
          disabled={!group}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">{group ? t("extensions.styleSelectPrompt") : t("extensions.selectFirst")}</option>
          {members.map((opt) => (
            <option key={String(opt.value)} value={String(opt.value)}>
              {opt.member_label || opt.label}
            </option>
          ))}
        </select>
      </div>
      {field.help && <span className="panel-note">{field.help}</span>}
    </div>
  );
}

function ExtensionFieldInput({
  field,
  value,
  hasSecret,
  options,
  onChange,
}: {
  field: ExtensionField;
  value: string;
  hasSecret: boolean;
  options: ExtensionOption[] | undefined;
  onChange: (v: string) => void;
}) {
  const t = useT();
  const id = `ext-field-${field.key}`;

  if (field.kind === "password") {
    return (
      <div className="form-row">
        <label htmlFor={id}>{field.label}</label>
        <input
          id={id}
          className="form-input"
          type="password"
          value={value}
          placeholder={t("extensions.passwordPlaceholder")}
          onChange={(e) => onChange(e.target.value)}
        />
        {hasSecret ? (
          <span className="badge-st st-done" style={{ width: "fit-content" }}>
            {t("extensions.secretSet")}
          </span>
        ) : (
          <span className="badge-st st-todo" style={{ width: "fit-content" }}>
            {t("extensions.secretUnset")}
          </span>
        )}
        {field.help && <span className="panel-note">{field.help}</span>}
      </div>
    );
  }

  if (field.kind === "select" && field.options_from) {
    if (options === undefined) {
      return (
        <div className="form-row">
          <label htmlFor={id}>{field.label}</label>
          <p className="panel-note">{t("extensions.fetchingOptions")}</p>
        </div>
      );
    }
    if (options.length > 0) {
      // ADR-009 D17: 選択肢が `group` を持つなら「親 → 子」の2段で選ばせる。
      // VOICEVOX の話者は127件あり、平らな1つの箱では探せない（主人の指摘 2026-09-04）。
      const grouped = options.some((o) => o.group);
      if (grouped) {
        return <GroupedSelectField id={id} field={field} options={options} value={value} onChange={onChange} />;
      }
      return (
        <div className="form-row">
          <label htmlFor={id}>{field.label}</label>
          <select id={id} className="form-select" value={value} onChange={(e) => onChange(e.target.value)}>
            <option value="">{t("extensions.selectPrompt")}</option>
            {options.map((opt) => (
              <option key={String(opt.value)} value={String(opt.value)}>
                {opt.label}
              </option>
            ))}
          </select>
          {field.help && <span className="panel-note">{field.help}</span>}
        </div>
      );
    }
    // D5: 外部が落ちていれば空を返す → 画面は案内を出して数字の直接入力に落とす。
    return (
      <div className="form-row">
        <label htmlFor={id}>{field.label}</label>
        <input id={id} className="form-input" type="number" value={value} onChange={(e) => onChange(e.target.value)} />
        <p className="panel-note">{t("extensions.optionsUnavailable")}</p>
      </div>
    );
  }

  if (field.kind === "number") {
    return (
      <div className="form-row">
        <label htmlFor={id}>{field.label}</label>
        <input id={id} className="form-input" type="number" value={value} onChange={(e) => onChange(e.target.value)} />
        {field.help && <span className="panel-note">{field.help}</span>}
      </div>
    );
  }

  // text / path はどちらも1行テキスト（path は自動探索の説明を help で出す運用）。
  return (
    <div className="form-row">
      <label htmlFor={id}>{field.label}</label>
      <input id={id} className="form-input" value={value} onChange={(e) => onChange(e.target.value)} />
      {field.help && <span className="panel-note">{field.help}</span>}
    </div>
  );
}

export const extensionsModule: ModuleDefinition = {
  id: "extensions",
  title: "nav.extensions",
  description: "extensions.description",
  icon: "🧩",
  order: 100,
  routes: [{ index: true, element: <ExtensionsScreen /> }],
};
