/* manor web — レシートの詳細・修正（ADR-020 D9 `/money/receipts/{id}`）。
 *
 * 左（スマホでは上）に画像、右にヘッダ（店・日時・合計・小計・税率別税額・支払方法・
 * 税方式）と検算の行、明細の表（その場で編集。select の語彙は `GET /money/categories`）。
 * 保存は全行を送る（ADR-020 D9「items を送ると明細を丸ごと置き換える」）。
 *
 * **PUT で編集できる欄だけをフォームにする**——契約（ADR-020_api_contract.md）の
 * `PUT /money/receipts/{id}` body は `store_name`/`purchased_at`/`total`/`subtotal`/
 * `tax_mode`/`payment_method`/`taxes`/`items`/`learn_aliases` だけで、`store.branch`・
 * `receipt_no`・`tendered`・`change`・`item_count_declared` 等は読み取り専用（自分で
 * 決めた点。詳しくは報告に書く）。
 */
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "../../../app/api";
import type {
  MoneyCategoriesResponse,
  ReceiptDetail,
  ReceiptPaymentMethod,
  ReceiptTax,
  ReceiptTaxMode,
  ReceiptUpdatePayload,
} from "../../../app/types";
import { useToast } from "../../../components/Toast";
import { ScreenHeader } from "../../../components/ScreenHeader";
import { formatDateTime, useT, type TranslationKey } from "../../../app/i18n";
import {
  CHECK_KEYS,
  CHECK_LABEL_KEY,
  emptyReceiptItemForm,
  formToReceiptItemUpdate,
  nextLineNo,
  receiptItemToForm,
  receiptStatusMark,
  type ReceiptItemForm,
} from "./receiptShared";

const TAX_MODE_OPTIONS: ReceiptTaxMode[] = ["exclusive", "inclusive", "unknown"];
const TAX_MODE_KEY: Record<ReceiptTaxMode, TranslationKey> = {
  exclusive: "money.receipts.detail.taxMode.exclusive",
  inclusive: "money.receipts.detail.taxMode.inclusive",
  unknown: "money.receipts.detail.taxMode.unknown",
};

const PAYMENT_METHOD_OPTIONS: ReceiptPaymentMethod[] = ["cash", "credit", "qr", "ic", "unknown"];
const PAYMENT_METHOD_KEY: Record<ReceiptPaymentMethod, TranslationKey> = {
  cash: "money.receipts.detail.paymentMethod.cash",
  credit: "money.receipts.detail.paymentMethod.credit",
  qr: "money.receipts.detail.paymentMethod.qr",
  ic: "money.receipts.detail.paymentMethod.ic",
  unknown: "money.receipts.detail.paymentMethod.unknown",
};

function taxAmount(taxes: ReceiptTax[], rate: 8 | 10): string {
  const found = taxes.find((tx) => tx.rate === rate);
  return found ? String(found.amount) : "";
}

export function ReceiptDetailPage() {
  const t = useT();
  const navigate = useNavigate();
  const { show } = useToast();
  const { id } = useParams<{ id: string }>();
  const receiptId = Number(id);

  const [receipt, setReceipt] = useState<ReceiptDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [categories, setCategories] = useState<MoneyCategoriesResponse | null>(null);

  const [storeName, setStoreName] = useState("");
  const [purchasedAt, setPurchasedAt] = useState("");
  const [total, setTotal] = useState("");
  const [subtotal, setSubtotal] = useState("");
  const [taxMode, setTaxMode] = useState<ReceiptTaxMode>("unknown");
  const [paymentMethod, setPaymentMethod] = useState<ReceiptPaymentMethod>("unknown");
  const [tax8, setTax8] = useState("");
  const [tax10, setTax10] = useState("");
  const [items, setItems] = useState<ReceiptItemForm[]>([]);
  const [learnAliases, setLearnAliases] = useState(true);

  const [saving, setSaving] = useState(false);
  const [rereading, setRereading] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState(false);

  const fillFormFrom = (r: ReceiptDetail) => {
    setStoreName(r.store.name);
    setPurchasedAt(r.purchased_at ?? "");
    setTotal(r.total == null ? "" : String(r.total));
    setSubtotal(r.subtotal == null ? "" : String(r.subtotal));
    setTaxMode(r.tax_mode);
    setPaymentMethod(r.payment_method);
    setTax8(taxAmount(r.taxes, 8));
    setTax10(taxAmount(r.taxes, 10));
    setItems(r.items.map(receiptItemToForm));
  };

  const load = () => {
    api<ReceiptDetail>(`/money/receipts/${receiptId}`)
      .then((r) => {
        setReceipt(r);
        setError(null);
        fillFormFrom(r);
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : t("errors.genericLoadFailed")));
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [receiptId]);

  useEffect(() => {
    api<MoneyCategoriesResponse>("/money/categories")
      .then(setCategories)
      .catch(() => setCategories(null)); // 語彙が引けなくても明細の表自体は出す（自由入力にフォールバック）
  }, []);

  const updateItem = (key: string, patch: Partial<ReceiptItemForm>) =>
    setItems((prev) => prev.map((it) => (it.key === key ? { ...it, ...patch } : it)));
  const addItem = () => setItems((prev) => [...prev, emptyReceiptItemForm(nextLineNo(prev))]);
  const removeItem = (key: string) => setItems((prev) => prev.filter((it) => it.key !== key));

  const save = async () => {
    setSaving(true);
    try {
      const taxes: ReceiptTax[] = [];
      if (tax8.trim() !== "") taxes.push({ rate: 8, amount: Number(tax8) });
      if (tax10.trim() !== "") taxes.push({ rate: 10, amount: Number(tax10) });
      const payload: ReceiptUpdatePayload = {
        store_name: storeName,
        purchased_at: purchasedAt.trim() === "" ? null : purchasedAt.trim(),
        total: total.trim() === "" ? null : Number(total),
        subtotal: subtotal.trim() === "" ? null : Number(subtotal),
        tax_mode: taxMode,
        payment_method: paymentMethod,
        taxes,
        items: items.map(formToReceiptItemUpdate),
        learn_aliases: learnAliases,
      };
      const updated = await api<ReceiptDetail>(`/money/receipts/${receiptId}`, { method: "PUT", body: payload });
      setReceipt(updated);
      fillFormFrom(updated);
      show(t("money.receipts.detail.saved"), "ok", 3000);
    } catch (err) {
      show(t("errors.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    } finally {
      setSaving(false);
    }
  };

  const reread = async () => {
    setRereading(true);
    try {
      await api(`/money/receipts/${receiptId}/reread`, { method: "POST" });
      show(t("money.receipts.detail.rereading"), "ok", 3000);
      load();
    } catch (err) {
      show(t("errors.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    } finally {
      setRereading(false);
    }
  };

  const discard = async () => {
    if (!deleteConfirm) {
      setDeleteConfirm(true);
      return;
    }
    try {
      await api(`/money/receipts/${receiptId}`, { method: "DELETE" });
      show(t("money.receipts.detail.deleted"), "ok", 3000);
      navigate("/money/receipts");
    } catch (err) {
      show(t("errors.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    }
  };

  if (error) {
    return (
      <div className="view" id="view-money-receipt-detail">
        <ScreenHeader title={t("money.receipts.listHeading")} description={t("money.receipts.description")} />
        <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>
      </div>
    );
  }
  if (!receipt) {
    return (
      <div className="view" id="view-money-receipt-detail">
        <ScreenHeader title={t("money.receipts.listHeading")} description={t("money.receipts.description")} />
        <p className="panel-note">{t("common.loading")}</p>
      </div>
    );
  }

  const mark = receiptStatusMark(receipt.status, receipt.review, receipt.reason, t);
  const title = receipt.store.name || t("money.receipts.storeUnknown");

  return (
    <div className="view" id="view-money-receipt-detail">
      <ScreenHeader title={title} description={t("money.receipts.description")} />

      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-start" }}>
        <div style={{ flex: "1 1 280px", minWidth: 240 }}>
          <section className="panel">
            {receipt.image_url ? (
              <a href={receipt.image_url} target="_blank" rel="noreferrer">
                <img src={receipt.image_url} alt="" style={{ width: "100%", borderRadius: 8 }} loading="lazy" />
              </a>
            ) : (
              <p className="panel-note">{t("money.receipts.detail.noImage")}</p>
            )}
            <p className="panel-note">
              <span className={"badge-st " + mark.cls}>
                {mark.icon} {mark.label}
              </span>
            </p>
            <p className="panel-note">{t("money.receipts.detail.createdAt", { at: formatDateTime(receipt.created_at) })}</p>
            {receipt.notes.length > 0 && (
              <>
                <h3 style={{ fontSize: 12.5 }}>{t("money.receipts.detail.notesHeading")}</h3>
                <ul className="panel-note">
                  {receipt.notes.map((n, i) => (
                    <li key={i}>{n}</li>
                  ))}
                </ul>
              </>
            )}
            <div className="form-actions">
              <button type="button" className="btn btn-small" disabled={rereading} onClick={reread}>
                {rereading ? t("money.receipts.detail.rereading") : t("money.receipts.detail.reread")}
              </button>
              <button type="button" className="btn btn-small btn-danger" onClick={discard}>
                {deleteConfirm ? t("money.receipts.detail.deleteConfirm") : t("money.receipts.detail.delete")}
              </button>
            </div>
          </section>
        </div>

        <div style={{ flex: "2 1 420px", minWidth: 0 }}>
          <section className="panel">
            <div className="panel-head">
              <h2>{t("money.receipts.detail.headerHeading")}</h2>
            </div>
            <div className="form-grid" style={{ maxWidth: "none" }}>
              <div className="form-inline">
                <div className="form-row" style={{ flex: "1 1 200px" }}>
                  <label htmlFor="receipt-store">{t("money.receipts.detail.storeLabel")}</label>
                  <input id="receipt-store" className="form-input" value={storeName} onChange={(e) => setStoreName(e.target.value)} />
                </div>
                <div className="form-row" style={{ flex: "1 1 180px" }}>
                  <label htmlFor="receipt-purchased-at">{t("money.receipts.detail.purchasedAtLabel")}</label>
                  <input
                    id="receipt-purchased-at"
                    className="form-input"
                    placeholder={t("money.receipts.detail.purchasedAtPlaceholder")}
                    value={purchasedAt}
                    onChange={(e) => setPurchasedAt(e.target.value)}
                  />
                </div>
              </div>
              <div className="form-inline">
                <div className="form-row" style={{ maxWidth: 140 }}>
                  <label htmlFor="receipt-subtotal">{t("money.receipts.detail.subtotalLabel")}</label>
                  <input id="receipt-subtotal" className="form-input" type="number" value={subtotal} onChange={(e) => setSubtotal(e.target.value)} />
                </div>
                <div className="form-row" style={{ maxWidth: 140 }}>
                  <label htmlFor="receipt-total">{t("money.receipts.detail.totalLabel")}</label>
                  <input id="receipt-total" className="form-input" type="number" value={total} onChange={(e) => setTotal(e.target.value)} />
                </div>
                <div className="form-row" style={{ maxWidth: 140 }}>
                  <label htmlFor="receipt-tax8">{t("money.receipts.detail.taxRateLabel", { rate: 8 })}</label>
                  <input id="receipt-tax8" className="form-input" type="number" value={tax8} onChange={(e) => setTax8(e.target.value)} />
                </div>
                <div className="form-row" style={{ maxWidth: 140 }}>
                  <label htmlFor="receipt-tax10">{t("money.receipts.detail.taxRateLabel", { rate: 10 })}</label>
                  <input id="receipt-tax10" className="form-input" type="number" value={tax10} onChange={(e) => setTax10(e.target.value)} />
                </div>
              </div>
              <div className="form-inline">
                <div className="form-row" style={{ maxWidth: 180 }}>
                  <label htmlFor="receipt-tax-mode">{t("money.receipts.detail.taxModeLabel")}</label>
                  <select
                    id="receipt-tax-mode"
                    className="form-select"
                    value={taxMode}
                    onChange={(e) => setTaxMode(e.target.value as ReceiptTaxMode)}
                  >
                    {TAX_MODE_OPTIONS.map((m) => (
                      <option key={m} value={m}>
                        {t(TAX_MODE_KEY[m])}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="form-row" style={{ maxWidth: 180 }}>
                  <label htmlFor="receipt-payment-method">{t("money.receipts.detail.paymentMethodLabel")}</label>
                  <select
                    id="receipt-payment-method"
                    className="form-select"
                    value={paymentMethod}
                    onChange={(e) => setPaymentMethod(e.target.value as ReceiptPaymentMethod)}
                  >
                    {PAYMENT_METHOD_OPTIONS.map((m) => (
                      <option key={m} value={m}>
                        {t(PAYMENT_METHOD_KEY[m])}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            </div>
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>{t("money.receipts.detail.checksHeading")}</h2>
            </div>
            <div className="rows">
              {CHECK_KEYS.map((k) => {
                const c = receipt.checks[k];
                const mark2 = c.ok == null ? "？" : c.ok ? "✓" : "✗";
                return (
                  <div className="row-item" key={k}>
                    <span className="row-title">
                      {mark2} {t(CHECK_LABEL_KEY[k])}
                    </span>
                    <span className="row-id">
                      {c.ok == null
                        ? t("money.receipts.checks.unknown")
                        : t("money.receipts.checks.expectedActual", { expected: c.expected ?? 0, actual: c.actual ?? 0 })}
                    </span>
                  </div>
                );
              })}
            </div>
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>{t("money.receipts.items.heading")}</h2>
            </div>
            <div className="table-scroll">
              <table className="grid">
                <thead>
                  <tr>
                    <th>{t("money.receipts.items.name")}</th>
                    <th>{t("money.receipts.items.qty")}</th>
                    <th>{t("money.receipts.items.unitPrice")}</th>
                    <th>{t("money.receipts.items.amount")}</th>
                    <th>{t("money.receipts.items.taxRate")}</th>
                    <th>{t("money.receipts.items.category")}</th>
                    <th>{t("money.receipts.items.subcategory")}</th>
                    <th>{t("money.receipts.items.itemKind")}</th>
                    <th>{t("money.receipts.items.discount")}</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((it) => {
                    const subcategoryOptions = categories?.categories.find((c) => c.name === it.category)?.subcategories ?? [];
                    return (
                      <tr key={it.key}>
                        <td>
                          <input
                            className="form-input"
                            style={{ minWidth: 120 }}
                            value={it.name}
                            onChange={(e) => updateItem(it.key, { name: e.target.value })}
                          />
                        </td>
                        <td>
                          <input
                            className="form-input"
                            style={{ maxWidth: 70 }}
                            value={it.qty}
                            onChange={(e) => updateItem(it.key, { qty: e.target.value })}
                          />
                        </td>
                        <td>
                          <input
                            className="form-input"
                            style={{ maxWidth: 90 }}
                            type="number"
                            value={it.unit_price}
                            onChange={(e) => updateItem(it.key, { unit_price: e.target.value })}
                          />
                        </td>
                        <td>
                          <input
                            className="form-input"
                            style={{ maxWidth: 90 }}
                            type="number"
                            value={it.amount}
                            onChange={(e) => updateItem(it.key, { amount: e.target.value })}
                          />
                        </td>
                        <td>
                          <select
                            className="form-select"
                            style={{ maxWidth: 90 }}
                            value={it.tax_rate}
                            onChange={(e) => updateItem(it.key, { tax_rate: e.target.value as "" | "8" | "10" })}
                          >
                            <option value="">{t("money.receipts.items.taxNone")}</option>
                            <option value="8">8%</option>
                            <option value="10">10%</option>
                          </select>
                        </td>
                        <td>
                          <select
                            className="form-select"
                            style={{ minWidth: 100 }}
                            value={it.category}
                            onChange={(e) => updateItem(it.key, { category: e.target.value, subcategory: "" })}
                          >
                            <option value="">{t("common.none")}</option>
                            {(categories?.categories ?? []).map((c) => (
                              <option key={c.name} value={c.name}>
                                {c.name}
                              </option>
                            ))}
                          </select>
                        </td>
                        <td>
                          <select
                            className="form-select"
                            style={{ minWidth: 100 }}
                            value={it.subcategory}
                            disabled={!it.category}
                            onChange={(e) => updateItem(it.key, { subcategory: e.target.value })}
                          >
                            <option value="">{t("common.none")}</option>
                            {subcategoryOptions.map((s) => (
                              <option key={s} value={s}>
                                {s}
                              </option>
                            ))}
                          </select>
                        </td>
                        <td>
                          <select
                            className="form-select"
                            style={{ minWidth: 100 }}
                            value={it.item_kind}
                            onChange={(e) => updateItem(it.key, { item_kind: e.target.value })}
                          >
                            <option value="">{t("common.none")}</option>
                            {(categories?.item_kinds ?? []).map((k) => (
                              <option key={k} value={k}>
                                {k}
                              </option>
                            ))}
                          </select>
                        </td>
                        <td>
                          <input
                            type="checkbox"
                            checked={it.is_discount}
                            onChange={(e) => updateItem(it.key, { is_discount: e.target.checked })}
                          />
                        </td>
                        <td>
                          <button type="button" className="btn btn-small btn-danger" onClick={() => removeItem(it.key)}>
                            {t("common.delete")}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                  {!items.length && (
                    <tr>
                      <td colSpan={10}>
                        <p className="panel-note">{t("money.receipts.items.empty")}</p>
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <div className="form-actions">
              <button type="button" className="btn btn-small" onClick={addItem}>
                {t("money.receipts.items.add")}
              </button>
            </div>

            <div className="form-actions" style={{ marginTop: 14, alignItems: "center" }}>
              <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12 }}>
                <input type="checkbox" checked={learnAliases} onChange={(e) => setLearnAliases(e.target.checked)} />
                {t("money.receipts.detail.learnAliases")}
              </label>
              <button type="button" className="btn btn-primary" disabled={saving} onClick={save}>
                {t("money.receipts.detail.save")}
              </button>
            </div>
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>{t("money.receipts.expensesHeading")}</h2>
            </div>
            <div className="rows">
              {!receipt.expenses.length && <p className="panel-note">{t("money.receipts.expensesEmpty")}</p>}
              {receipt.expenses.map((e) => (
                <div className="row-item" key={e.id}>
                  <span className="row-id">{e.date}</span>
                  <span className="row-title">
                    {e.category} {e.memo || ""}
                  </span>
                  <span className="row-id">{t("money.amountYen", { n: e.amount })}</span>
                </div>
              ))}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
