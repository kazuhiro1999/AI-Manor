/* manor web — レシピ本体の編集欄（ADR-015 D4）。登録（RecipeNewPage）と編集
 * （RecipeEditPage）の両方から使う「フォームは1つ」の実体——取り込み・手入力どちらで
 * 埋めた値もここへ流れ込む。ここでは値の受け渡しだけを担い、送信（POST/PUT）は
 * 呼び出し側が行う（Settings の各節・setup ウィザードと同じ「値はここ、通信は外」の分担）。
 */
import {
  emptyIngredient,
  emptyPhase,
  emptyStep,
  PHASE_MAX,
  STEP_INSTRUCTION_MAX,
  STEP_TITLE_MAX,
  type IngredientFormValue,
  type PhaseFormValue,
  type RecipeFormValue,
  type StepFormValue,
} from "./recipeShared";
import { useT } from "../../app/i18n";
import type { StepCompletion } from "../../app/types";

function CharCount({ used, max }: { used: number; max: number }) {
  const t = useT();
  const remaining = max - used;
  if (remaining < 0) {
    return <span className="char-count char-count-over">{t("kitchen.recipes.stepTitleOver", { n: -remaining })}</span>;
  }
  return <span className="char-count">{t("kitchen.recipes.stepTitleRemaining", { n: remaining })}</span>;
}

function InstructionCharCount({ used, max }: { used: number; max: number }) {
  const t = useT();
  const remaining = max - used;
  if (remaining < 0) {
    return <span className="char-count char-count-over">{t("kitchen.recipes.stepInstructionOver", { n: -remaining })}</span>;
  }
  return <span className="char-count">{t("kitchen.recipes.stepInstructionRemaining", { n: remaining })}</span>;
}

const COMPLETION_OPTIONS: StepCompletion[] = ["manual", "auto", "confirm"];

export function RecipeFieldsEditor({
  value,
  onChange,
}: {
  value: RecipeFormValue;
  onChange: (next: RecipeFormValue) => void;
}) {
  const t = useT();

  const setField = <K extends keyof RecipeFormValue>(key: K, v: RecipeFormValue[K]) => onChange({ ...value, [key]: v });

  const updateIngredient = (key: string, patch: Partial<IngredientFormValue>) =>
    setField(
      "ingredients",
      value.ingredients.map((ing) => (ing.key === key ? { ...ing, ...patch } : ing))
    );
  const addIngredient = () => setField("ingredients", [...value.ingredients, emptyIngredient()]);
  const removeIngredient = (key: string) =>
    setField(
      "ingredients",
      value.ingredients.filter((ing) => ing.key !== key)
    );

  const updatePhase = (key: string, title: string) =>
    setField(
      "phases",
      value.phases.map((p) => (p.key === key ? { ...p, title } : p))
    );
  const addPhase = () => setField("phases", [...value.phases, emptyPhase()]);
  const removePhase = (key: string) => {
    const removed = value.phases.find((p) => p.key === key);
    const remaining = value.phases.filter((p) => p.key !== key);
    const fallbackId = remaining[0]?.id ?? "";
    setField("phases", remaining);
    if (removed) {
      setField(
        "steps",
        value.steps.map((s) => (s.phase === removed.id ? { ...s, phase: fallbackId } : s))
      );
    }
  };

  const updateStep = (key: string, patch: Partial<StepFormValue>) =>
    setField(
      "steps",
      value.steps.map((s) => (s.key === key ? { ...s, ...patch } : s))
    );
  const addStep = () => setField("steps", [...value.steps, emptyStep(value.phases[0]?.id ?? "")]);
  const removeStep = (key: string) =>
    setField(
      "steps",
      value.steps.filter((s) => s.key !== key)
    );
  const moveStep = (index: number, dir: -1 | 1) => {
    const target = index + dir;
    if (target < 0 || target >= value.steps.length) return;
    const next = [...value.steps];
    [next[index], next[target]] = [next[target], next[index]];
    setField("steps", next);
  };

  return (
    <div className="form-grid" style={{ maxWidth: "none" }}>
      <div className="form-row">
        <label htmlFor="recipe-title">{t("kitchen.recipes.titleLabel")}</label>
        <input id="recipe-title" className="form-input" value={value.title} onChange={(e) => setField("title", e.target.value)} />
      </div>
      <div className="form-row">
        <label htmlFor="recipe-source-url">{t("kitchen.recipes.sourceUrlLabel")}</label>
        <input id="recipe-source-url" className="form-input" value={value.sourceUrl} onChange={(e) => setField("sourceUrl", e.target.value)} />
      </div>
      <div className="form-inline">
        <div className="form-row" style={{ maxWidth: 120 }}>
          <label htmlFor="recipe-servings">{t("kitchen.recipes.servingsLabel")}</label>
          <input
            id="recipe-servings"
            className="form-input"
            type="number"
            min={0}
            value={value.servings}
            onChange={(e) => setField("servings", e.target.value)}
          />
        </div>
        <div className="form-row" style={{ maxWidth: 120 }}>
          <label htmlFor="recipe-minutes">{t("kitchen.recipes.minutesLabel")}</label>
          <input
            id="recipe-minutes"
            className="form-input"
            type="number"
            min={0}
            value={value.totalMinutes}
            onChange={(e) => setField("totalMinutes", e.target.value)}
          />
        </div>
      </div>
      <div className="form-row">
        <label htmlFor="recipe-tools">{t("kitchen.recipes.toolsLabel")}</label>
        <input id="recipe-tools" className="form-input" value={value.tools} onChange={(e) => setField("tools", e.target.value)} />
      </div>

      <h3>{t("kitchen.recipes.ingredientsHeading")}</h3>
      <div className="setup-rows">
        {value.ingredients.map((ing) => (
          <div className="setup-row" key={ing.key}>
            <div className="form-row">
              <label htmlFor={`ing-name-${ing.key}`}>{t("kitchen.recipes.ingredientNameLabel")}</label>
              <input
                id={`ing-name-${ing.key}`}
                className="form-input"
                value={ing.name}
                onChange={(e) => updateIngredient(ing.key, { name: e.target.value })}
              />
            </div>
            <div className="form-row" style={{ maxWidth: 90 }}>
              <label htmlFor={`ing-qty-${ing.key}`}>{t("kitchen.recipes.ingredientQtyLabel")}</label>
              <input
                id={`ing-qty-${ing.key}`}
                className="form-input"
                value={ing.qty}
                onChange={(e) => updateIngredient(ing.key, { qty: e.target.value })}
              />
            </div>
            <div className="form-row" style={{ maxWidth: 90 }}>
              <label htmlFor={`ing-unit-${ing.key}`}>{t("kitchen.recipes.ingredientUnitLabel")}</label>
              <input
                id={`ing-unit-${ing.key}`}
                className="form-input"
                value={ing.unit}
                onChange={(e) => updateIngredient(ing.key, { unit: e.target.value })}
              />
            </div>
            <div className="form-row">
              <label htmlFor={`ing-prep-${ing.key}`}>{t("kitchen.recipes.ingredientPrepLabel")}</label>
              <input
                id={`ing-prep-${ing.key}`}
                className="form-input"
                value={ing.prep}
                onChange={(e) => updateIngredient(ing.key, { prep: e.target.value })}
              />
            </div>
            <div className="form-row" style={{ maxWidth: 110 }}>
              <label htmlFor={`ing-group-${ing.key}`}>{t("kitchen.recipes.ingredientGroupLabel")}</label>
              <input
                id={`ing-group-${ing.key}`}
                className="form-input"
                value={ing.group}
                onChange={(e) => updateIngredient(ing.key, { group: e.target.value })}
              />
            </div>
            <button type="button" className="btn btn-small btn-danger setup-row-remove" onClick={() => removeIngredient(ing.key)}>
              {t("common.delete")}
            </button>
          </div>
        ))}
        {!value.ingredients.length && <p className="panel-note">{t("kitchen.recipes.noIngredients")}</p>}
      </div>
      <div className="form-actions">
        <button type="button" className="btn btn-small" onClick={addIngredient}>
          {t("kitchen.recipes.addIngredient")}
        </button>
      </div>

      <h3>{t("kitchen.recipes.phasesHeading")}</h3>
      <p className="panel-note">{t("kitchen.recipes.phaseCountNote")}</p>
      <div className="setup-rows">
        {value.phases.map((p) => (
          <div className="setup-row" key={p.key}>
            <div className="form-row">
              <label htmlFor={`phase-title-${p.key}`}>{t("kitchen.recipes.phaseTitleLabel")}</label>
              <input id={`phase-title-${p.key}`} className="form-input" value={p.title} onChange={(e) => updatePhase(p.key, e.target.value)} />
            </div>
            <button
              type="button"
              className="btn btn-small btn-danger setup-row-remove"
              disabled={value.phases.length <= 1}
              onClick={() => removePhase(p.key)}
            >
              {t("common.delete")}
            </button>
          </div>
        ))}
      </div>
      <div className="form-actions">
        <button type="button" className="btn btn-small" disabled={value.phases.length >= PHASE_MAX} onClick={addPhase}>
          {t("kitchen.recipes.addPhase")}
        </button>
      </div>

      <h3>{t("kitchen.recipes.stepsHeading")}</h3>
      <div className="cards">
        {value.steps.map((s, i) => (
          <div className="card step-card" key={s.key}>
            <div className="card-head">
              <span className="step-num">{i + 1}</span>
              <div className="form-row" style={{ maxWidth: 160 }}>
                <label htmlFor={`step-phase-${s.key}`}>{t("kitchen.recipes.stepPhaseLabel")}</label>
                <select
                  id={`step-phase-${s.key}`}
                  className="form-select"
                  value={s.phase}
                  onChange={(e) => updateStep(s.key, { phase: e.target.value })}
                >
                  {value.phases.map((p) => (
                    <option key={p.key} value={p.id}>
                      {p.title || p.id}
                    </option>
                  ))}
                </select>
              </div>
              <div className="card-actions" style={{ marginLeft: "auto", marginTop: 0 }}>
                <button type="button" className="btn btn-small" disabled={i === 0} onClick={() => moveStep(i, -1)}>
                  {t("kitchen.recipes.moveUp")}
                </button>
                <button type="button" className="btn btn-small" disabled={i === value.steps.length - 1} onClick={() => moveStep(i, 1)}>
                  {t("kitchen.recipes.moveDown")}
                </button>
                <button type="button" className="btn btn-small btn-danger" onClick={() => removeStep(s.key)}>
                  {t("common.delete")}
                </button>
              </div>
            </div>
            <div className="form-row">
              <label htmlFor={`step-title-${s.key}`}>{t("kitchen.recipes.stepTitleLabel")}</label>
              <input
                id={`step-title-${s.key}`}
                className="form-input"
                value={s.title}
                onChange={(e) => updateStep(s.key, { title: e.target.value })}
              />
              <CharCount used={s.title.length} max={STEP_TITLE_MAX} />
            </div>
            <div className="form-row">
              <label htmlFor={`step-instruction-${s.key}`}>{t("kitchen.recipes.stepInstructionLabel")}</label>
              <textarea
                id={`step-instruction-${s.key}`}
                className="form-textarea"
                style={{ minHeight: 50 }}
                value={s.instruction}
                onChange={(e) => updateStep(s.key, { instruction: e.target.value })}
              />
              <InstructionCharCount used={s.instruction.length} max={STEP_INSTRUCTION_MAX} />
            </div>
            <div className="form-inline">
              <div className="form-row">
                <label htmlFor={`step-image-${s.key}`}>{t("kitchen.recipes.stepImageLabel")}</label>
                <input
                  id={`step-image-${s.key}`}
                  className="form-input"
                  value={s.image}
                  onChange={(e) => updateStep(s.key, { image: e.target.value })}
                />
              </div>
              <div className="form-row" style={{ maxWidth: 130 }}>
                <label htmlFor={`step-timer-${s.key}`}>{t("kitchen.recipes.stepTimerLabel")}</label>
                <input
                  id={`step-timer-${s.key}`}
                  className="form-input"
                  type="number"
                  min={0}
                  value={s.timerSec}
                  onChange={(e) => updateStep(s.key, { timerSec: e.target.value })}
                />
              </div>
              <div className="form-row" style={{ maxWidth: 130 }}>
                <label htmlFor={`step-completion-${s.key}`}>{t("kitchen.recipes.stepCompletionLabel")}</label>
                <select
                  id={`step-completion-${s.key}`}
                  className="form-select"
                  value={s.completion}
                  onChange={(e) => updateStep(s.key, { completion: e.target.value as StepCompletion })}
                >
                  {COMPLETION_OPTIONS.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div className="form-row">
              <label htmlFor={`step-ingredients-used-${s.key}`}>{t("kitchen.recipes.stepIngredientsUsedLabel")}</label>
              <input
                id={`step-ingredients-used-${s.key}`}
                className="form-input"
                value={s.ingredientsUsed}
                onChange={(e) => updateStep(s.key, { ingredientsUsed: e.target.value })}
              />
            </div>
            <div className="form-row">
              <label htmlFor={`step-tips-${s.key}`}>{t("kitchen.recipes.stepTipsLabel")}</label>
              <textarea
                id={`step-tips-${s.key}`}
                className="form-textarea"
                style={{ minHeight: 40 }}
                value={s.tips}
                onChange={(e) => updateStep(s.key, { tips: e.target.value })}
              />
            </div>
          </div>
        ))}
        {!value.steps.length && <p className="panel-note">{t("kitchen.recipes.noSteps")}</p>}
      </div>
      <div className="form-actions">
        <button type="button" className="btn btn-small" onClick={addStep}>
          {t("kitchen.recipes.addStep")}
        </button>
      </div>
    </div>
  );
}
