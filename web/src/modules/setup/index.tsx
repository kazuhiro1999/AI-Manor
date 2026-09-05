/* manor web — 初回セットアップ（ADR-007 D6・§6 D7〜D9／ADR-010）。
 * 「聞きすぎない」（主人の指示、2026-09-03）: 段は選んだ「使いたい機能」から動的に決まる。
 * 呼び名 → 使いたい機能 → [最初の仕事 if tasks] → [台所の前提 if kitchen] →
 * [家計の前提 if money] → 確認。すべての段に「あとで」があり、その段の回答を空にして
 * 次へ進む。既定は推奨設定（自律レベル standard・通貨 JPY）。
 * ADR-010 D1: 行動クラス（内部の自律度）はここでは聞かない。既定 general でサーバーが起票する。
 * 状態は画面内（React state）だけに置く（localStorage には置かない。②の情報を
 * 端末に残さないため）。途中離脱で消えてよい。
 */
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import type { ModuleDefinition } from "../../app/module";
import { api, ApiError } from "../../app/api";
import { APP_NAME } from "../../app/brand";
import type { SetupAnswers, SetupInfo, SetupKitchenAnswer, SetupMoneyAnswer, SetupResult, TaskKind } from "../../app/types";
import { useToast } from "../../components/Toast";
import { useMetaContext } from "../../app/MetaContext";
import { ScreenHeader } from "../../components/ScreenHeader";
import { useT } from "../../app/i18n";
import { purposeLabel } from "../../app/purposeMeta";
import { choiceLabel, MONEY_APP_LABEL_KEY, PRESET_LABEL_KEY } from "../../app/setupChoices";

interface ProjectRow {
  key: number;
  name: string;
  code: string;
  codeEdited: boolean; // true になったら、名前が変わっても記号は自動で追随しない
  preset: string;
}

interface TaskRow {
  key: number;
  title: string;
  projectCode: string; // "" は「なし」
  kind: string; // ADR-010 D2: タスクの種類（任意）。"" は「（未選択）」で送らない
  due: string;
}

let rowSeq = 0;
function nextKey(): number {
  rowSeq += 1;
  return rowSeq;
}

/** 名前から記号の候補を作る（英数字とハイフンのみ・小文字）。ascii が無ければ p1, p2… */
export function suggestProjectCode(name: string, index: number, taken: Set<string>): string {
  const ascii = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  let base = ascii && /[a-z0-9]/.test(ascii) ? ascii : `p${index + 1}`;
  if (!/^[a-z0-9]/.test(base)) base = `p${index + 1}`;
  let candidate = base;
  let n = 2;
  while (taken.has(candidate)) {
    candidate = `${base}-${n}`;
    n += 1;
  }
  return candidate;
}

function parsePurposeIds(raw?: string): string[] {
  if (!raw) return [];
  try {
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

// ADR-007 §6 D8: 段の構成は選んだ「使いたい機能」から動的に決まる（最少3段、最多6段）。
type StepKind = "callname" | "purposes" | "work" | "kitchen" | "money" | "confirm";

/* 検分 2026-09-05: 以前はここが `Record<StepKind, TranslationKey>`（キーを persist
 * して呼び出し側で `t(MAP[currentStep])` する形）だった。`setup.stepDesc.purposes` は
 * `{app}` の差し込みが要る関数値なのに、`StepKind` という**広い**型を経由すると
 * `t()` の型（値が関数のキーは params 必須）が効かない——`MAP[currentStep]` の型は
 * 「6つの具体的なキーのどれか」ではなく「TranslationKey 全体」に潰れてしまい、
 * 全キーの中には文字列（params 省略可）も混ざっているため、コンパイラは
 * 「省略可」側を許してしまう。実機で "undefined" が出たのはこのため。
 *
 * ここを直すには、**個々の `t("具体的な文字列", ...)` 呼び出し**として書く必要がある
 * （文字列リテラルとして渡されたキーだけ、`t()` の型がそのキー専用の要否を判定できる）。
 * そのため対応表の値を「訳語の文字列」ではなく「`t` を受けて訳す関数」にする——
 * 段の集合を回す既存の構造（`Record<StepKind, ...>`）はそのまま保てる。 */
type StepText = (t: ReturnType<typeof useT>) => string;

const STEP_LABEL: Record<StepKind, StepText> = {
  callname: (t) => t("setup.step.callname"),
  purposes: (t) => t("setup.step.purposes"),
  work: (t) => t("setup.step.work"),
  kitchen: (t) => t("setup.step.kitchen"),
  money: (t) => t("setup.step.money"),
  confirm: (t) => t("setup.step.confirm"),
};

// ADR-010 §3「初回セットアップの各段にも同じ形で置く（段の題＋一行）」。
const STEP_DESCRIPTION: Record<StepKind, StepText> = {
  callname: (t) => t("setup.stepDesc.callname"),
  purposes: (t) => t("setup.stepDesc.purposes", { app: APP_NAME }),
  work: (t) => t("setup.stepDesc.work"),
  kitchen: (t) => t("setup.stepDesc.kitchen"),
  money: (t) => t("setup.stepDesc.money"),
  confirm: (t) => t("setup.stepDesc.confirm"),
};

function computeSteps(purposes: string[]): StepKind[] {
  const steps: StepKind[] = ["callname", "purposes"];
  if (purposes.includes("tasks")) steps.push("work");
  if (purposes.includes("kitchen")) steps.push("kitchen");
  if (purposes.includes("money")) steps.push("money");
  steps.push("confirm");
  return steps;
}

function SetupScreen() {
  const t = useT();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const isRedo = searchParams.get("redo") === "1";
  const { show } = useToast();
  const { reload: reloadMeta, markSetupJustCompleted } = useMetaContext();

  const [info, setInfo] = useState<SetupInfo | null>(null);
  // ADR-010 D2: タスクの種類（任意選択欄の中身）。GET /task-kinds から（meta.task_kinds と
  // 同じ生成元。往復を増やさないよう /setup と並行で取る）。
  const [taskKinds, setTaskKinds] = useState<TaskKind[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [stepIndex, setStepIndex] = useState(0);

  const [callname, setCallname] = useState("");
  const [butlerName, setButlerName] = useState("");

  // tasks は既定 on（ADR-007 §6 D7）。やり直し（既存プロフィールあり）のときは
  // 読み込み後に上書きする。
  const [selectedPurposes, setSelectedPurposes] = useState<string[]>(["tasks"]);
  const [note, setNote] = useState("");

  const [projectRows, setProjectRows] = useState<ProjectRow[]>([]);
  const [projectError, setProjectError] = useState<string | null>(null);

  const [taskRows, setTaskRows] = useState<TaskRow[]>([]);
  const [taskError, setTaskError] = useState<string | null>(null);

  const [householdSize, setHouseholdSize] = useState("");
  const [allergies, setAllergies] = useState("");
  const [dislikes, setDislikes] = useState("");
  const [kitchenSkipped, setKitchenSkipped] = useState(false);

  const [moneyApp, setMoneyApp] = useState("none");
  const [moneyCurrency, setMoneyCurrency] = useState("JPY");
  const [moneySkipped, setMoneySkipped] = useState(false);

  const [submitError, setSubmitError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [res, kinds] = await Promise.all([
          api<SetupInfo>("/setup"),
          // タスクの種類は任意項目なので、取得に失敗しても・配列でない応答が来ても
          // 致命的にはしない（空の一覧で進める。バックエンド未対応の間の安全側）。
          api<TaskKind[]>("/task-kinds")
            .then((r) => (Array.isArray(r) ? r : []))
            .catch(() => [] as TaskKind[]),
        ]);
        if (cancelled) return;
        setInfo(res);
        setTaskKinds(kinds);
        if (res.done) {
          // やり直し（既存プロフィールの上書き）。既存のプロフィールで埋めておく。
          setCallname(res.profile["master.callname"] || "");
          setButlerName(res.profile["butler.callname"] || "");
          setSelectedPurposes(parsePurposeIds(res.profile["purposes"]));
          setNote(res.profile["purposes.note"] || "");
          if (res.profile["money.app"]) setMoneyApp(res.profile["money.app"]);
          if (res.profile["money.currency"]) setMoneyCurrency(res.profile["money.currency"]);
        }
      } catch (err) {
        if (!cancelled) setLoadError(err instanceof ApiError ? err.message : t("common.unknown"));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const steps = useMemo(() => computeSteps(selectedPurposes), [selectedPurposes]);
  const currentIndex = Math.min(stepIndex, steps.length - 1);
  const currentStep = steps[currentIndex];

  const togglePurpose = (id: string) => {
    setSelectedPurposes((prev) => (prev.includes(id) ? prev.filter((p) => p !== id) : [...prev, id]));
  };

  const addProjectRow = () => {
    setProjectRows((prev) => [...prev, { key: nextKey(), name: "", code: "", codeEdited: false, preset: "standard" }]);
  };
  const removeProjectRow = (key: number) => {
    setProjectRows((prev) => prev.filter((r) => r.key !== key));
  };
  const updateProjectName = (key: number, name: string) => {
    setProjectRows((prev) => {
      const taken = new Set(prev.filter((r) => r.key !== key).map((r) => r.code));
      return prev.map((r, idx) => {
        if (r.key !== key) return r;
        const code = r.codeEdited ? r.code : suggestProjectCode(name, idx, taken);
        return { ...r, name, code };
      });
    });
  };
  const updateProjectCode = (key: number, code: string) => {
    // **打った通りに弾かずに、揃える。**「P1」を拒否していた（主人の指摘 2026-09-04）。
    // 大文字を許せないのは見た目の好みではなく、`P` で始まる参照は project の **id**
    // （`P1`・`P2`…）として先に解決されるため——`P1` という記号は自分の id に隠される
    // （`src/manor/project.py` の `resolve`）。なので黙って小文字へ揃える。
    const normalized = code.toLowerCase();
    setProjectRows((prev) => prev.map((r) => (r.key === key ? { ...r, code: normalized, codeEdited: true } : r)));
  };
  const updateProjectPreset = (key: number, preset: string) => {
    setProjectRows((prev) => prev.map((r) => (r.key === key ? { ...r, preset } : r)));
  };

  // ADR-010 D1: 行動クラスはウィザードから外れた。新しい行の既定は「なし」（未選択）。
  const addTaskRow = () => {
    setTaskRows((prev) => [...prev, { key: nextKey(), title: "", projectCode: "", kind: "", due: "" }]);
  };
  const removeTaskRow = (key: number) => {
    setTaskRows((prev) => prev.filter((r) => r.key !== key));
  };
  const updateTaskField = (key: number, field: "title" | "projectCode" | "kind" | "due", value: string) => {
    setTaskRows((prev) => prev.map((r) => (r.key === key ? { ...r, [field]: value } : r)));
  };

  const advance = () => setStepIndex((i) => i + 1);
  const goBack = () => setStepIndex((i) => Math.max(0, i - 1));

  const validateWork = (): boolean => {
    const codes = new Set<string>();
    for (const r of projectRows) {
      if (!r.name.trim()) {
        setProjectError(t("setup.work.nameRequired"));
        return false;
      }
      if (!/^[a-z0-9][a-z0-9-]*$/.test(r.code)) {
        setProjectError(t("setup.work.codeInvalid", { code: r.code || t("setup.work.codeEmpty") }));
        return false;
      }
      if (codes.has(r.code)) {
        setProjectError(t("setup.work.codeDuplicate", { code: r.code }));
        return false;
      }
      codes.add(r.code);
    }
    setProjectError(null);
    for (const r of taskRows) {
      if (!r.title.trim()) {
        setTaskError(t("setup.work.titleRequired"));
        return false;
      }
    }
    setTaskError(null);
    return true;
  };

  const goNext = () => {
    if (currentStep === "work" && !validateWork()) return;
    advance();
  };

  // 「あとで」: その段の回答をクリアして次へ進む（検証はしない）。
  const skipCurrent = () => {
    if (currentStep === "callname") {
      setCallname("");
      setButlerName("");
    } else if (currentStep === "purposes") {
      // 執事の裁定（2026-09-03）: 全部消すのではなく推奨既定（tasks だけ on）に戻す。
      // 「既定は推奨設定」（主人の指示）。何も選ばれない状態は推奨ではない。
      setSelectedPurposes(["tasks"]);
      setNote("");
    } else if (currentStep === "work") {
      setProjectRows([]);
      setTaskRows([]);
      setProjectError(null);
      setTaskError(null);
    } else if (currentStep === "kitchen") {
      setHouseholdSize("");
      setAllergies("");
      setDislikes("");
      setKitchenSkipped(true);
    } else if (currentStep === "money") {
      setMoneyApp("none");
      setMoneyCurrency("JPY");
      setMoneySkipped(true);
    }
    advance();
  };

  const kitchenAnswer = (): SetupKitchenAnswer | undefined => {
    if (!selectedPurposes.includes("kitchen") || kitchenSkipped) return undefined;
    const out: SetupKitchenAnswer = {};
    const n = householdSize.trim();
    if (n) {
      const parsed = Number(n);
      if (!Number.isNaN(parsed)) out.household_size = parsed;
    }
    if (allergies.trim()) out.allergies = allergies.trim();
    if (dislikes.trim()) out.dislikes = dislikes.trim();
    return out;
  };

  const moneyAnswer = (): SetupMoneyAnswer | undefined => {
    if (!selectedPurposes.includes("money") || moneySkipped) return undefined;
    return { app: moneyApp, currency: moneyCurrency.trim() || "JPY" };
  };

  const submit = async () => {
    setBusy(true);
    setSubmitError(null);
    const answers: SetupAnswers = {
      callname: callname.trim(),
      butler_name: butlerName.trim() || undefined,
      purposes: selectedPurposes,
      note: note.trim() || undefined,
      // ADR-010 D3: プロジェクトの期限はここでは聞かない（送らない）。
      projects: projectRows.map((r) => ({
        code: r.code,
        name: r.name.trim(),
        preset: r.preset || undefined,
      })),
      // ADR-010 D1: cls は送らない（サーバー既定）。D2: kind は選ばれていれば送る。
      tasks: taskRows.map((r) => ({
        title: r.title.trim(),
        project_code: r.projectCode || undefined,
        kind: r.kind || undefined,
        due: r.due || undefined,
      })),
    };
    const kitchen = kitchenAnswer();
    if (kitchen) answers.kitchen = kitchen;
    const money = moneyAnswer();
    if (money) answers.money = money;
    try {
      await api<SetupResult>("/setup", { method: "POST", body: answers });
      // App の meta（5秒ポーリング）はまだ setup_done: false の可能性があるので、
      // 「このセッションで完了した」フラグを先に立ててから navigate する
      // （手動リロード無しで /tasks に着地させるため。ADR-007 D6 追補）。
      markSetupJustCompleted();
      show(t("setup.registered"), "ok", 3000);
      navigate("/tasks");
      reloadMeta().catch(() => {
        /* 失敗しても setupJustCompleted があるので誘導には戻らない */
      });
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : t("setup.registerFailed"));
    } finally {
      setBusy(false);
    }
  };

  if (loadError) {
    return (
      <div className="setup-shell">
        <div className="setup-card">
          <p className="panel-note">{t("errors.loadFailed", { reason: loadError })}</p>
        </div>
      </div>
    );
  }
  if (!info) {
    return (
      <div className="setup-shell">
        <div className="setup-card">
          <p className="panel-note">{t("common.loading")}</p>
        </div>
      </div>
    );
  }

  const projectOptionsForTasks = projectRows.filter((r) => r.code);
  const displayCallname = callname.trim() || t("setup.defaultMaster");
  const displayButlerName = butlerName.trim() || t("setup.defaultButlerName");
  const kitchenFilled = selectedPurposes.includes("kitchen") && !kitchenSkipped && (householdSize.trim() || allergies.trim() || dislikes.trim());
  const moneyFilled = selectedPurposes.includes("money") && !moneySkipped;
  const moneyCurrencyDisplay = moneyCurrency.trim() || "JPY";
  // 執事の裁定（2026-09-03）: 段を訪れて何も変えなかった（推奨既定のまま）ときは
  // 確認画面で「使っていない ／ JPY（既定）」のように既定であることを示す。
  const moneyIsDefault = moneyApp === "none" && moneyCurrencyDisplay === "JPY";
  const workFilled = steps.includes("work") && (projectRows.length > 0 || taskRows.length > 0);

  return (
    <div className="setup-shell">
      <div className="setup-card">
        {/* ADR-010 D7: setup は自前のシェルを持つので ScreenHeader は使わず、その場に一行を置く。 */}
        <h1>{t("setup.heading", { app: APP_NAME })}</h1>
        <p className="panel-note">{t("setup.description")}</p>
        {(isRedo || info.done) && <p className="setup-note">{t("setup.redoNote")}</p>}

        <div className="setup-progress">
          {steps.map((kind, i) => (
            <div key={kind} className={"setup-step" + (i === currentIndex ? " active" : i < currentIndex ? " done" : "")}>
              {i + 1}/{steps.length} {STEP_LABEL[kind](t)}
            </div>
          ))}
        </div>

        {/* ADR-010 §3: 各段にも同じ形の見出し（段の題＋一行）を置く。共通部品を再利用する。 */}
        <ScreenHeader title={STEP_LABEL[currentStep](t)} description={STEP_DESCRIPTION[currentStep](t)} />

        {currentStep === "callname" && (
          <div className="form-grid">
            <div className="form-row">
              <label htmlFor="setup-callname">{t("setup.callname.masterLabel")}</label>
              <input
                id="setup-callname"
                className="form-input"
                value={callname}
                onChange={(e) => setCallname(e.target.value)}
                placeholder={t("setup.callname.masterPlaceholder")}
              />
            </div>
            <div className="form-row">
              <label htmlFor="setup-butler-name">{t("setup.callname.butlerLabel")}</label>
              <input
                id="setup-butler-name"
                className="form-input"
                value={butlerName}
                onChange={(e) => setButlerName(e.target.value)}
                placeholder={t("setup.callname.butlerPlaceholder")}
              />
            </div>
          </div>
        )}

        {currentStep === "purposes" && (
          <div className="form-grid">
            <div className="form-row">
              <label>{t("setup.purposes.label")}</label>
              <div className="setup-chips">
                {info.purposes.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    className="chip chip-toggle"
                    aria-pressed={selectedPurposes.includes(p.id)}
                    onClick={() => togglePurpose(p.id)}
                  >
                    {purposeLabel(t, p)}
                  </button>
                ))}
              </div>
            </div>
            <div className="form-row">
              <label htmlFor="setup-note">{t("setup.purposes.noteLabel")}</label>
              <textarea
                id="setup-note"
                className="form-textarea"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder={t("setup.purposes.notePlaceholder")}
              />
            </div>
          </div>
        )}

        {currentStep === "work" && (
          <div>
            <h3>{t("setup.work.projectsHeading")}</h3>
            {/* ADR-010 D4: 語の説明は常に出す（フォーカス時だけだと触る前に読めない）。 */}
            <p className="panel-note">{t("setup.work.projectsHint1")}</p>
            <p className="panel-note">{t("setup.work.projectsHint2")}</p>
            <div className="setup-rows">
              {projectRows.map((r) => (
                <div className="setup-row" key={r.key}>
                  <div className="form-row">
                    <label htmlFor={`setup-project-name-${r.key}`}>{t("setup.work.nameLabel")}</label>
                    <input
                      id={`setup-project-name-${r.key}`}
                      className="form-input"
                      value={r.name}
                      onChange={(e) => updateProjectName(r.key, e.target.value)}
                      placeholder={t("setup.work.namePlaceholder")}
                    />
                  </div>
                  <div className="form-row">
                    <label htmlFor={`setup-project-code-${r.key}`}>{t("setup.work.codeLabel")}</label>
                    <input
                      id={`setup-project-code-${r.key}`}
                      className="form-input"
                      value={r.code}
                      onChange={(e) => updateProjectCode(r.key, e.target.value)}
                      placeholder={t("setup.work.codePlaceholder")}
                    />
                  </div>
                  <div className="form-row">
                    <label htmlFor={`setup-project-preset-${r.key}`}>{t("setup.work.presetLabel")}</label>
                    <select
                      id={`setup-project-preset-${r.key}`}
                      className="form-select"
                      value={r.preset}
                      onChange={(e) => updateProjectPreset(r.key, e.target.value)}
                    >
                      {info.presets.map((p) => (
                        <option key={p.id} value={p.id}>
                          {choiceLabel(PRESET_LABEL_KEY, t, p)}
                        </option>
                      ))}
                    </select>
                  </div>
                  <button
                    type="button"
                    className="btn btn-small btn-danger setup-row-remove"
                    onClick={() => removeProjectRow(r.key)}
                  >
                    {t("common.delete")}
                  </button>
                </div>
              ))}
              {!projectRows.length && <p className="panel-note">{t("setup.work.noRows")}</p>}
            </div>
            <div className="form-actions">
              <button type="button" className="btn btn-small" onClick={addProjectRow}>
                {t("setup.work.addRow")}
              </button>
            </div>
            {/* ADR-010 D3: プロジェクトの期限はここでは聞かない。節目（milestone）へ誘導する。 */}
            <p className="panel-note">{t("setup.work.dueHint")}</p>
            {projectError && <div className="form-error">{projectError}</div>}

            <h3 style={{ marginTop: 18 }}>{t("setup.work.tasksHeading")}</h3>
            <p className="panel-note">{t("setup.work.tasksHint1")}</p>
            <p className="panel-note">{t("setup.work.tasksHint2")}</p>
            <div className="setup-rows">
              {taskRows.map((r) => (
                <div className="setup-row" key={r.key}>
                  <div className="form-row">
                    <label htmlFor={`setup-task-title-${r.key}`}>{t("setup.work.titleLabel")}</label>
                    <input
                      id={`setup-task-title-${r.key}`}
                      className="form-input"
                      value={r.title}
                      onChange={(e) => updateTaskField(r.key, "title", e.target.value)}
                      placeholder={t("setup.work.titlePlaceholder")}
                    />
                  </div>
                  <div className="form-row">
                    <label htmlFor={`setup-task-project-${r.key}`}>{t("setup.work.projectFieldLabel")}</label>
                    <select
                      id={`setup-task-project-${r.key}`}
                      className="form-select"
                      value={r.projectCode}
                      onChange={(e) => updateTaskField(r.key, "projectCode", e.target.value)}
                    >
                      <option value="">{t("setup.work.noneOption")}</option>
                      {projectOptionsForTasks.map((p) => (
                        <option key={p.code} value={p.code}>
                          {p.code}（{p.name}）
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="form-row">
                    {/* ADR-010 D1・D2: 行動クラス（内部の自律度）はここでは聞かない。
                        代わりに人に意味のある「タスクの種類」を任意で選ばせる。 */}
                    <label htmlFor={`setup-task-kind-${r.key}`}>{t("setup.work.kindLabel")}</label>
                    <select
                      id={`setup-task-kind-${r.key}`}
                      className="form-select"
                      value={r.kind}
                      onChange={(e) => updateTaskField(r.key, "kind", e.target.value)}
                    >
                      <option value="">{t("setup.work.kindUnselected")}</option>
                      {taskKinds.map((k) => (
                        <option key={k.id} value={k.id}>
                          {k.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="form-row">
                    {/* ADR-010 D5: 期限は任意。ラベルと補助文でそう見えるようにする。 */}
                    <label htmlFor={`setup-task-due-${r.key}`}>{t("setup.work.dueLabel")}</label>
                    <input
                      id={`setup-task-due-${r.key}`}
                      className="form-input"
                      type="date"
                      value={r.due}
                      onChange={(e) => updateTaskField(r.key, "due", e.target.value)}
                    />
                    <span className="panel-note">{t("setup.work.dueHintInline")}</span>
                  </div>
                  <button
                    type="button"
                    className="btn btn-small btn-danger setup-row-remove"
                    onClick={() => removeTaskRow(r.key)}
                  >
                    {t("common.delete")}
                  </button>
                </div>
              ))}
              {!taskRows.length && <p className="panel-note">{t("setup.work.noRows")}</p>}
            </div>
            <div className="form-actions">
              <button type="button" className="btn btn-small" onClick={addTaskRow}>
                {t("setup.work.addRow")}
              </button>
            </div>
            {taskError && <div className="form-error">{taskError}</div>}
          </div>
        )}

        {currentStep === "kitchen" && (
          <div className="form-grid">
            <div className="form-row">
              <label htmlFor="setup-household-size">{t("setup.kitchen.householdLabel")}</label>
              <input
                id="setup-household-size"
                className="form-input"
                type="number"
                value={householdSize}
                onChange={(e) => {
                  setHouseholdSize(e.target.value);
                  setKitchenSkipped(false);
                }}
                placeholder={t("setup.kitchen.householdPlaceholder")}
              />
            </div>
            <div className="form-row">
              <label htmlFor="setup-allergies">{t("setup.kitchen.allergiesLabel")}</label>
              <input
                id="setup-allergies"
                className="form-input"
                value={allergies}
                onChange={(e) => {
                  setAllergies(e.target.value);
                  setKitchenSkipped(false);
                }}
                placeholder={t("setup.kitchen.allergiesPlaceholder")}
              />
            </div>
            <div className="form-row">
              <label htmlFor="setup-dislikes">{t("setup.kitchen.dislikesLabel")}</label>
              <input
                id="setup-dislikes"
                className="form-input"
                value={dislikes}
                onChange={(e) => {
                  setDislikes(e.target.value);
                  setKitchenSkipped(false);
                }}
                placeholder={t("setup.kitchen.dislikesPlaceholder")}
              />
            </div>
          </div>
        )}

        {currentStep === "money" && (
          <div className="form-grid">
            <div className="form-row">
              <label htmlFor="setup-money-app">{t("setup.money.appLabel")}</label>
              <select
                id="setup-money-app"
                className="form-select"
                value={moneyApp}
                onChange={(e) => {
                  setMoneyApp(e.target.value);
                  setMoneySkipped(false);
                }}
              >
                {info.money_apps.map((a) => (
                  <option key={a.id} value={a.id}>
                    {choiceLabel(MONEY_APP_LABEL_KEY, t, a)}
                  </option>
                ))}
              </select>
            </div>
            <div className="form-row">
              <label htmlFor="setup-money-currency">{t("setup.money.currencyLabel")}</label>
              <input
                id="setup-money-currency"
                className="form-input"
                value={moneyCurrency}
                onChange={(e) => {
                  setMoneyCurrency(e.target.value);
                  setMoneySkipped(false);
                }}
                placeholder={t("setup.money.currencyPlaceholder")}
              />
            </div>
            <p className="panel-note">{t("setup.money.csvHint")}</p>
          </div>
        )}

        {currentStep === "confirm" && (
          <div className="setup-summary">
            <div>
              <h3>{t("setup.confirm.callnameHeading")}</h3>
              <p className="panel-note">{t("setup.confirm.callnameSummary", { master: displayCallname, butler: displayButlerName })}</p>
            </div>
            {(selectedPurposes.length > 0 || note.trim()) && (
              <div>
                <h3>{t("setup.confirm.purposesHeading")}</h3>
                <p className="panel-note">
                  {selectedPurposes.length
                    ? selectedPurposes
                        .map((id) => {
                          const p = info.purposes.find((x) => x.id === id);
                          return p ? purposeLabel(t, p) : id;
                        })
                        .join(t("common.itemSeparator"))
                    : t("common.none")}
                  {note.trim() && t("setup.confirm.purposesNoteSuffix", { note: note.trim() })}
                </p>
              </div>
            )}
            {workFilled && (
              <div>
                <h3>{t("setup.confirm.projectsHeading")}</h3>
                {projectRows.length ? (
                  <div className="rows">
                    {projectRows.map((r) => (
                      <div className="row-item" key={r.key}>
                        <span className="row-title">
                          {r.code} — {r.name}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="panel-note">{t("common.none")}</p>
                )}
                <h3 style={{ marginTop: 8 }}>{t("setup.confirm.tasksHeading")}</h3>
                {taskRows.length ? (
                  <div className="rows">
                    {taskRows.map((r) => (
                      <div className="row-item" key={r.key}>
                        {/* ADR-010 D5: 空の期限は「—」ではなく行ごと出さない。kind も未選択なら省く。 */}
                        <span className="row-title">
                          {r.title}
                          {r.projectCode && t("setup.confirm.taskProjectSuffix", { code: r.projectCode })}
                          {r.kind && t("setup.confirm.taskKindSuffix", { label: taskKinds.find((k) => k.id === r.kind)?.label || r.kind })}
                          {r.due && t("setup.confirm.taskDueSuffix", { due: r.due })}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="panel-note">{t("common.none")}</p>
                )}
              </div>
            )}
            {kitchenFilled && (
              <div>
                <h3>{t("setup.confirm.kitchenHeading")}</h3>
                <p className="panel-note">
                  {householdSize.trim() && t("setup.confirm.kitchenHouseholdSuffix", { n: householdSize.trim() })}
                  {allergies.trim() && t("setup.confirm.kitchenAllergiesSuffix", { text: allergies.trim() })}
                  {dislikes.trim() && t("setup.confirm.kitchenDislikesSuffix", { text: dislikes.trim() })}
                </p>
              </div>
            )}
            {moneyFilled && (
              <div>
                <h3>{t("setup.confirm.moneyHeading")}</h3>
                <p className="panel-note">
                  {(() => {
                    const app = info.money_apps.find((a) => a.id === moneyApp);
                    return app ? choiceLabel(MONEY_APP_LABEL_KEY, t, app) : moneyApp;
                  })()}{" "}
                  ／ {moneyCurrencyDisplay}
                  {moneyIsDefault ? t("setup.confirm.moneyDefault") : ""}
                </p>
              </div>
            )}
            {submitError && <div className="form-error">{submitError}</div>}
          </div>
        )}

        <div className="form-actions" style={{ marginTop: 16 }}>
          {currentIndex > 0 && (
            <button type="button" className="btn" onClick={goBack} disabled={busy}>
              {t("common.back")}
            </button>
          )}
          {currentStep !== "confirm" && (
            <>
              <button type="button" className="btn btn-primary" onClick={goNext}>
                {t("common.next")}
              </button>
              <button type="button" className="btn" onClick={skipCurrent}>
                {t("common.later")}
              </button>
            </>
          )}
          {currentStep === "confirm" && (
            <button type="button" className="btn btn-primary" onClick={submit} disabled={busy}>
              {t("setup.submit")}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export const setupModule: ModuleDefinition = {
  id: "setup",
  title: "nav.setup",
  description: "setup.description",
  icon: "🧭",
  order: 100,
  hideFromNav: true,
  routes: [{ index: true, element: <SetupScreen /> }],
};
