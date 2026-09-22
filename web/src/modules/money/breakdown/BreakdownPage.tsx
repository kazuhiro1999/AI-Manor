/* manor web — 家計の内訳（ADR-020 追補 `GET /money/breakdown?ym=YYYY-MM`）。
 *
 * 主人「マネーフォワードみたいに、何にお金を一番使ったかが分かるようにしたい」への対応。
 * `usePolling` は使わない——過去の月まで5秒おきに読み直す理由が無い。月を切り替える
 * たびに `api()` で1回読み直す（契約どおり `ym` 省略時は今月）。
 *
 * 棒は常に**単一色**（系列は1つ。大項目・中項目・品目・店の識別は色ではなく文字で行う
 * ——契約の「表示の要点」どおり）。横棒は常にトラック幅100%を基準にした割合で描くので
 * スマホ幅でも崩れない。棒の**長さ**はそのリストの中での相対比較用（そのリストの最大値を
 * 100%とする——dashboard の `.dash-bar`（modules/dashboard/index.tsx）と同じ考え方）。
 * 数値そのもの（金額・割合%）は文字で直接添えるので、割合の絶対値を取り違えることはない。
 *
 * 契約になかった／自分で決めた点（報告にも書く）:
 *   - `by_category` に `over`（超過フラグ）が無いので `budget != null && amount > budget`
 *     で自前に判定する（`/money` の `MoneyCategorySummary.over` と同じ考え方）。
 *   - 「この月の記録はありません」を出す条件は契約に明示が無いので
 *     `summary.expense === 0 && by_category.length === 0` とした。
 *   - `summary.income`・`summary.days_with_spending` は今回の見出しの帯の仕様
 *     （5つの指標）に含まれていないので画面には出していない。
 */
import { useEffect, useState } from "react";
import { api, ApiError } from "../../../app/api";
import type {
  MoneyBreakdownCategory,
  MoneyBreakdownDailyPoint,
  MoneyBreakdownItemKind,
  MoneyBreakdownMonthPoint,
  MoneyBreakdownResponse,
  MoneyBreakdownStore,
  MoneyBreakdownSubcategory,
  MoneyBreakdownSummary,
} from "../../../app/types";
import { ScreenHeader } from "../../../components/ScreenHeader";
import { formatDay, formatYm, formatYmShort, useT, type TranslationKey } from "../../../app/i18n";

// receiptShared.ts の `Tr` と同じ簡略型（ヘルパー関数へ `t` を渡すため。個々のキーの
// 引数まではここでは検算しない——呼び出し側の t("...") 自体は厳密に検算される）。
type Tr = (key: TranslationKey, params?: Record<string, string | number>) => string;

const TOP_N = 8; // 中項目・品目・店は上位8件＋「その他」1行（課題の指示どおり）。

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

function ymNow(): string {
  const d = new Date();
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}`;
}

function shiftYm(ym: string, delta: number): string {
  const [y, m] = ym.split("-").map(Number);
  const d = new Date(y, m - 1 + delta, 1);
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}`;
}

// share（0..1）→ 表示用の整数%。
function pct(share: number): number {
  return Math.round(share * 100);
}

/* ---------- 横棒リスト（中項目・品目・店で共有する形） ---------- */

interface BarRow {
  key: string;
  label: string;
  amount: number;
  share: number; // 0..1（当月支出合計に対する割合。契約の share と同じ）
  sub?: string;
}

/** 上位8件だけ見せ、残りは「その他」1行にまとめる（課題の指示どおり）。 */
function collapseOther(rows: BarRow[], otherLabel: string): BarRow[] {
  if (rows.length <= TOP_N) return rows;
  const head = rows.slice(0, TOP_N);
  const rest = rows.slice(TOP_N);
  const amount = rest.reduce((s, r) => s + r.amount, 0);
  const share = rest.reduce((s, r) => s + r.share, 0);
  return [...head, { key: "__other__", label: otherLabel, amount, share }];
}

function BarList({ rows, t }: { rows: BarRow[]; t: Tr }) {
  if (!rows.length) return <p className="panel-note">{t("common.none")}</p>;
  const max = Math.max(...rows.map((r) => r.share), 0.0001);
  return (
    <div className="rows">
      {rows.map((r) => (
        <div className="row-item breakdown-row" key={r.key}>
          <div className="breakdown-row-head">
            <span className="row-title">{r.label}</span>
            <span className="row-id">{t("money.breakdown.amountShare", { amount: r.amount, share: pct(r.share) })}</span>
          </div>
          <span className="breakdown-bar-track" aria-hidden="true">
            <span className="breakdown-bar-fill" style={{ width: `${Math.round((r.share / max) * 100)}%` }} />
          </span>
          {r.sub && <span className="breakdown-row-sub">{r.sub}</span>}
        </div>
      ))}
    </div>
  );
}

function subcategoryRows(rows: MoneyBreakdownSubcategory[], t: Tr): BarRow[] {
  return rows.map((r, i) => ({
    key: `${r.category}-${r.name}-${i}`,
    label: t("money.breakdown.bySubcategory.withCategory", { category: r.category, name: r.name }),
    amount: r.amount,
    share: r.share,
  }));
}

function itemKindRows(rows: MoneyBreakdownItemKind[], t: Tr): BarRow[] {
  return rows.map((r) => ({
    key: r.name,
    label: r.name,
    amount: r.amount,
    share: r.share,
    sub: t("money.breakdown.byItemKind.count", { n: r.items }),
  }));
}

function storeRows(rows: MoneyBreakdownStore[], t: Tr): BarRow[] {
  return rows.map((r) => ({
    key: r.name,
    label: r.name,
    amount: r.amount,
    share: r.share,
    sub: t("money.breakdown.byStore.count", { n: r.receipts }),
  }));
}

type Tab = "subcategory" | "itemKind" | "store";

function TabBars({ data, t }: { data: MoneyBreakdownResponse; t: Tr }) {
  const [tab, setTab] = useState<Tab>("subcategory");
  const otherLabel = t("money.breakdown.other");
  const rows = collapseOther(
    tab === "subcategory" ? subcategoryRows(data.by_subcategory, t) : tab === "itemKind" ? itemKindRows(data.by_item_kind, t) : storeRows(data.by_store, t),
    otherLabel
  );
  const heading =
    tab === "subcategory" ? t("money.breakdown.bySubcategory.heading") : tab === "itemKind" ? t("money.breakdown.byItemKind.heading") : t("money.breakdown.byStore.heading");
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>{heading}</h2>
        <div className="seg" role="tablist">
          <button type="button" className="seg-btn" aria-pressed={tab === "subcategory"} onClick={() => setTab("subcategory")}>
            {t("money.breakdown.tabs.subcategory")}
          </button>
          <button type="button" className="seg-btn" aria-pressed={tab === "itemKind"} onClick={() => setTab("itemKind")}>
            {t("money.breakdown.tabs.itemKind")}
          </button>
          <button type="button" className="seg-btn" aria-pressed={tab === "store"} onClick={() => setTab("store")}>
            {t("money.breakdown.tabs.store")}
          </button>
        </div>
      </div>
      <BarList rows={rows} t={t} />
    </section>
  );
}

/* ---------- 大項目の内訳（予算・前月・超過つき） ---------- */

function CategoryBars({ rows, t }: { rows: MoneyBreakdownCategory[]; t: Tr }) {
  if (!rows.length) return <p className="panel-note">{t("common.none")}</p>;
  const max = Math.max(...rows.map((r) => r.share), 0.0001);
  return (
    <div className="rows">
      {rows.map((r) => {
        const over = r.budget != null && r.amount > r.budget; // 契約に over は無い。自前で判定（報告に明記）。
        const subParts: string[] = [];
        if (r.budget != null) subParts.push(t("money.breakdown.budget", { n: r.budget }));
        if (r.prev_amount != null) subParts.push(t("money.breakdown.prevAmount", { n: r.prev_amount }));
        return (
          <div className="row-item breakdown-row" key={r.name}>
            <div className="breakdown-row-head">
              <span className="row-title">{r.name}</span>
              <span className="row-id">{t("money.breakdown.amountShare", { amount: r.amount, share: pct(r.share) })}</span>
              {over && <span className="badge-st st-hold">{t("money.summary.over")}</span>}
            </div>
            <span className="breakdown-bar-track" aria-hidden="true">
              <span className="breakdown-bar-fill" style={{ width: `${Math.round((r.share / max) * 100)}%` }} />
            </span>
            {subParts.length > 0 && <span className="breakdown-row-sub">{subParts.join(` ${t("common.listSeparator")} `)}</span>}
          </div>
        );
      })}
    </div>
  );
}

/* ---------- 見出しの帯（stat tiles） ---------- */

function diffText(t: Tr, summary: MoneyBreakdownSummary): string {
  if (summary.prev_expense == null) return t("money.breakdown.stat.diffNone");
  const diff = summary.diff ?? summary.expense - summary.prev_expense;
  const sign = diff > 0 ? "+" : "";
  if (summary.prev_expense > 0) {
    const percent = Math.round((diff / summary.prev_expense) * 100);
    return t("money.breakdown.stat.diffValue", { sign, amount: diff, percent });
  }
  return t("money.breakdown.stat.diffValueNoPercent", { sign, amount: diff }); // 前月が0円だと%が定義できない
}

function StatTiles({ data, t }: { data: MoneyBreakdownResponse; t: Tr }) {
  const tiles = [
    { label: t("money.breakdown.stat.expenseLabel"), value: t("money.amountYen", { n: data.summary.expense }) },
    { label: t("money.breakdown.stat.diffLabel"), value: diffText(t, data.summary) },
    { label: t("money.breakdown.stat.receiptsLabel"), value: t("money.breakdown.stat.receiptsValue", { n: data.summary.receipts }) },
    {
      label: t("money.breakdown.stat.topCategoryLabel"),
      value: data.top_category ? t("money.breakdown.stat.topValue", { name: data.top_category.name, percent: pct(data.top_category.share) }) : t("common.none"),
    },
    {
      label: t("money.breakdown.stat.topItemKindLabel"),
      value: data.top_item_kind ? t("money.breakdown.stat.topValue", { name: data.top_item_kind.name, percent: pct(data.top_item_kind.share) }) : t("common.none"),
    },
  ];
  return (
    <div className="summary">
      {tiles.map((tile) => (
        <div className="tile" key={tile.label}>
          <div className="tile-label">{tile.label}</div>
          <div className="tile-value">{tile.value}</div>
        </div>
      ))}
    </div>
  );
}

/* ---------- よく買ったもの ---------- */

function TopItemsTable({ data, t }: { data: MoneyBreakdownResponse; t: Tr }) {
  if (!data.top_items.length) return <p className="panel-note">{t("money.breakdown.topItems.empty")}</p>;
  return (
    <div className="table-scroll">
      <table className="grid">
        <thead>
          <tr>
            <th>{t("money.breakdown.topItems.name")}</th>
            <th className="col-nowrap">{t("money.breakdown.topItems.countQty")}</th>
            <th className="col-nowrap">{t("money.breakdown.topItems.amount")}</th>
            <th>{t("money.breakdown.topItems.store")}</th>
            <th>{t("money.breakdown.topItems.itemKind")}</th>
          </tr>
        </thead>
        <tbody>
          {data.top_items.map((it, i) => (
            <tr key={`${it.name}-${i}`}>
              <td>{it.name}</td>
              <td className="col-nowrap">{t("money.breakdown.topItems.countQtyValue", { count: it.count, qty: it.qty })}</td>
              <td className="col-nowrap">{t("money.amountYen", { n: it.amount })}</td>
              <td>{it.store}</td>
              <td>{it.item_kind}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ---------- 日別・月の推移（細い縦棒） ---------- */

function DailyChart({ daily, t }: { daily: MoneyBreakdownDailyPoint[]; t: Tr }) {
  if (!daily.length) return <p className="panel-note">{t("common.none")}</p>;
  const max = Math.max(...daily.map((d) => d.amount), 1);
  return (
    <div className="breakdown-vbars">
      {daily.map((d) => {
        const day = Number(d.date.slice(8, 10));
        const weekday = new Date(`${d.date}T00:00:00`).getDay();
        const isWeekend = weekday === 0 || weekday === 6;
        const title = `${formatDay(d.date, t)} ${t("common.listSeparator")} ${t("money.amountYen", { n: d.amount })}`;
        return (
          <div className="breakdown-vcol" key={d.date} title={title}>
            {d.amount > 0 && <span className="breakdown-vbar" style={{ height: `${Math.max(Math.round((d.amount / max) * 100), 2)}%` }} />}
            <span className={"breakdown-vlabel" + (isWeekend ? " is-weekend" : "")}>{day}</span>
          </div>
        );
      })}
    </div>
  );
}

function MonthsChart({ months, currentYm, t }: { months: MoneyBreakdownMonthPoint[]; currentYm: string; t: Tr }) {
  if (!months.length) return <p className="panel-note">{t("common.none")}</p>;
  const max = Math.max(...months.map((m) => m.expense), 1);
  return (
    <div className="breakdown-vbars breakdown-months">
      {months.map((m) => {
        const title = `${formatYm(m.ym)} ${t("common.listSeparator")} ${t("money.amountYen", { n: m.expense })}`;
        return (
          <div className="breakdown-vcol" key={m.ym} title={title}>
            <span
              className={"breakdown-vbar" + (m.ym === currentYm ? " is-current" : "")}
              style={{ height: `${Math.max(Math.round((m.expense / max) * 100), 2)}%` }}
            />
            <span className="breakdown-vamount">{t("money.amountYen", { n: m.expense })}</span>
            <span className="breakdown-vlabel">{formatYmShort(m.ym)}</span>
          </div>
        );
      })}
    </div>
  );
}

/* ---------- 本体 ---------- */

export function BreakdownPage() {
  const t = useT();
  const [ym, setYm] = useState<string>(ymNow());
  const [data, setData] = useState<MoneyBreakdownResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    api<MoneyBreakdownResponse>(`/money/breakdown?ym=${ym}`)
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : t("errors.genericLoadFailed"));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ym]);

  const todayYm = ymNow();
  const nextYm = shiftYm(ym, 1);
  const nextDisabled = nextYm > todayYm;
  const noRecord = !!data && data.summary.expense === 0 && data.by_category.length === 0;

  return (
    <div className="view" id="view-money-breakdown">
      <ScreenHeader title={t("nav.money")} description={t("money.breakdown.description")} />

      <div className="form-inline" style={{ marginBottom: 12 }}>
        <button type="button" className="btn btn-small" onClick={() => setYm((cur) => shiftYm(cur, -1))}>
          {t("money.breakdown.monthPrev")}
        </button>
        <span className="breakdown-month-nav">{formatYm(ym)}</span>
        <button type="button" className="btn btn-small" disabled={nextDisabled} onClick={() => setYm((cur) => shiftYm(cur, 1))}>
          {t("money.breakdown.monthNext")}
        </button>
      </div>

      {error && <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>}
      {!error && !data && <p className="panel-note">{t("common.loading")}</p>}
      {!error && data && noRecord && <p className="panel-note">{t("money.breakdown.empty")}</p>}

      {!error && data && !noRecord && (
        <>
          <StatTiles data={data} t={t} />

          <section className="panel">
            <div className="panel-head">
              <h2>{t("money.breakdown.byCategory.heading")}</h2>
            </div>
            <CategoryBars rows={data.by_category} t={t} />
          </section>

          <TabBars data={data} t={t} />

          <section className="panel">
            <div className="panel-head">
              <h2>{t("money.breakdown.topItems.heading")}</h2>
            </div>
            <TopItemsTable data={data} t={t} />
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>{t("money.breakdown.daily.heading")}</h2>
            </div>
            <DailyChart daily={data.daily} t={t} />
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>{t("money.breakdown.months.heading")}</h2>
            </div>
            <MonthsChart months={data.months} currentYm={todayYm} t={t} />
          </section>
        </>
      )}
    </div>
  );
}
