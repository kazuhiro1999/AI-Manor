import { useState } from "react";
import type { ModuleDefinition } from "../../app/module";
import { usePolling } from "../../app/polling";
import { api, ApiError } from "../../app/api";
import type { MoneyData } from "../../app/types";
import { useToast } from "../../components/Toast";
import { ScreenHeader } from "../../components/ScreenHeader";
import { formatDay, useT } from "../../app/i18n";

function MoneyScreen() {
  const t = useT();
  const { data, error, reload } = usePolling<MoneyData>("/money", 5000);
  const { show } = useToast();

  const [date, setDate] = useState(new Date().toISOString().slice(0, 10));
  const [amount, setAmount] = useState("");
  const [category, setCategory] = useState("");
  const [memo, setMemo] = useState("");
  const [income, setIncome] = useState(false);
  const [budgetCategory, setBudgetCategory] = useState("");
  const [budgetLimit, setBudgetLimit] = useState("");

  const title = t("nav.money");
  const description = t("money.description");

  // ScreenHeader（ADR-010 D7）は読み込み中・エラー・未導入のときも出す。
  if (error) {
    return (
      <div className="view" id="view-money">
        <ScreenHeader title={title} description={description} />
        <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="view" id="view-money">
        <ScreenHeader title={title} description={description} />
        <p className="panel-note">{t("common.loading")}</p>
      </div>
    );
  }
  if (!data.available) {
    return (
      <div className="view" id="view-money">
        <ScreenHeader title={title} description={description} />
        <p className="panel-note">{t("common.staffNotSetUp", { agent: t("agent.steward"), id: "steward" })}</p>
      </div>
    );
  }

  const addExpense = async () => {
    if (!category.trim() || !(Number(amount) > 0)) {
      show(t("money.recent.validation"), "warn", 4000);
      return;
    }
    try {
      await api("/money/expense", { method: "POST", body: { date, amount: Number(amount), category, memo: memo || undefined, income } });
      setAmount("");
      setMemo("");
      show(t("money.recent.recorded"), "ok", 3000);
      reload();
    } catch (err) {
      show(t("errors.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    }
  };

  const payRecurring = async (id: number) => {
    try {
      await api(`/money/recurring/${id}/paid`, { method: "POST", body: {} });
      show(t("money.recurring.paidRecorded"), "ok", 4000);
      reload();
    } catch (err) {
      show(t("errors.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    }
  };

  const setBudget = async () => {
    if (!budgetCategory.trim()) return;
    try {
      await api(`/money/budget/${encodeURIComponent(budgetCategory)}`, { method: "PUT", body: { limit: Number(budgetLimit || 0) } });
      show(t("money.budget.set"), "ok", 3000);
      reload();
    } catch (err) {
      show(t("errors.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    }
  };

  return (
    <div className="view" id="view-money">
      <ScreenHeader title={title} description={description} />
      <section className="panel panel-primary">
        <div className="panel-head">
          <h2>{t("money.summary.heading")}</h2>
        </div>
        <p className="panel-note">
          <strong>{t("money.summary.hintStrong")}</strong> {t("money.summary.hintRest")}
        </p>
        <div className="rows">
          {!(data.month?.expenses || []).length && <p className="panel-note">{t("common.none")}</p>}
          {(data.month?.expenses || []).map((e) => (
            <div className="row-item" key={e.category}>
              <span className="row-title">{e.category}</span>
              <span className="row-id">
                {e.budget != null
                  ? t("money.summary.withBudget", {
                      spent: e.spent,
                      budget: e.budget,
                      sign: e.diff && e.diff > 0 ? "+" : "",
                      diff: e.diff ?? 0,
                    })
                  : t("money.summary.noBudget", { spent: e.spent })}
              </span>
              {e.over && <span className="badge-st st-hold">{t("money.summary.over")}</span>}
            </div>
          ))}
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{t("money.recurring.heading")}</h2>
        </div>
        <div className="rows">
          {!(data.due || []).length && <p className="panel-note">{t("common.none")}</p>}
          {(data.due || []).map((r) => (
            <div className="row-item" key={r.id}>
              <span className="row-title">{r.name}</span>
              <span className="row-id">
                {formatDay(r.next_due, t)}{" "}
                {r.overdue_days > 0
                  ? t("money.recurring.overdueDays", { days: r.overdue_days })
                  : r.overdue_days === 0
                    ? t("money.recurring.dueToday")
                    : t("money.recurring.dueInDays", { days: -r.overdue_days })}{" "}
                / {t("money.amountYen", { n: r.amount })}
              </span>
              <button className="btn btn-small" type="button" onClick={() => payRecurring(r.id)}>
                {t("money.recurring.markPaid")}
              </button>
            </div>
          ))}
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{t("money.recent.heading")}</h2>
        </div>
        <div className="rows">
          {!(data.recent_expenses || []).length && <p className="panel-note">{t("common.none")}</p>}
          {(data.recent_expenses || []).map((r) => (
            <div className="row-item" key={r.id}>
              <span className="row-id">{formatDay(r.date, t)}</span>
              <span className="row-title">
                {r.category} {r.memo || ""}
              </span>
              <span className="row-id">
                {r.kind === "income" ? "+" : "-"}
                {t("money.amountYen", { n: r.amount })}
              </span>
            </div>
          ))}
        </div>
        <div className="form-inline" style={{ marginTop: 10 }}>
          <input className="form-input" style={{ maxWidth: 150 }} type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          <input
            className="form-input"
            style={{ maxWidth: 100 }}
            placeholder={t("money.recent.amountPlaceholder")}
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
          />
          <input
            className="form-input"
            placeholder={t("money.recent.categoryPlaceholder")}
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          />
          <input
            className="form-input"
            placeholder={t("money.recent.memoPlaceholder")}
            value={memo}
            onChange={(e) => setMemo(e.target.value)}
          />
          <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12 }}>
            <input type="checkbox" checked={income} onChange={(e) => setIncome(e.target.checked)} /> {t("money.recent.income")}
          </label>
          <button className="btn btn-primary btn-small" type="button" onClick={addExpense}>
            {t("money.recent.record")}
          </button>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{t("money.budget.heading")}</h2>
        </div>
        <div className="form-inline">
          <input
            className="form-input"
            placeholder={t("money.recent.categoryPlaceholder")}
            value={budgetCategory}
            onChange={(e) => setBudgetCategory(e.target.value)}
          />
          <input
            className="form-input"
            style={{ maxWidth: 120 }}
            placeholder={t("money.budget.limitPlaceholder")}
            value={budgetLimit}
            onChange={(e) => setBudgetLimit(e.target.value)}
          />
          <button className="btn btn-primary btn-small" type="button" onClick={setBudget}>
            {t("common.save")}
          </button>
        </div>
      </section>
    </div>
  );
}

export const moneyModule: ModuleDefinition = {
  id: "money",
  title: "nav.money",
  description: "money.description",
  icon: "¥",
  order: 6,
  routes: [{ index: true, element: <MoneyScreen /> }],
};
