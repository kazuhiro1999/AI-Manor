/* manor web — レシート読み取り（ADR-020）の共有の定数・変換・文言化。
 * `ReceiptsPage`（一覧＋撮る）と `ReceiptDetailPage`（詳細・修正）の両方が使う
 * ——recipeShared.ts と同じ「値の変換はここ、通信・描画は画面側」の分担。
 */
import type { TranslationKey } from "../../../app/i18n";
import type {
  Quick,
  QuickIssue,
  ReceiptChecks,
  ReceiptItem,
  ReceiptItemUpdate,
  ReceiptReview,
  ReceiptStatus,
} from "../../../app/types";

type Tr = (key: TranslationKey, params?: Record<string, string | number>) => string;

// `Quick.issues` の符牒 → 訳語キー（ADR-020 D9。サーバは文を組まない）。
export const QUICK_ISSUE_KEY: Record<QuickIssue, TranslationKey> = {
  no_paper: "money.receipts.quick.noPaper",
  corners_cut: "money.receipts.quick.cornersCut",
  blurry: "money.receipts.quick.blurry",
  too_dark: "money.receipts.quick.tooDark",
  no_total: "money.receipts.quick.noTotal",
  ocr_unavailable: "money.receipts.quick.ocrUnavailable",
};

export function quickIssueLabels(quick: Quick, t: Tr): string[] {
  return quick.issues.map((issue) => t(QUICK_ISSUE_KEY[issue]));
}

/** `reason` が `duplicate_of:<id>` の形なら id を返す（ADR-020 D9「重複」）。 */
export function parseDuplicateOf(reason: string): number | null {
  const m = /^duplicate_of:(\d+)$/.exec(reason || "");
  return m ? Number(m[1]) : null;
}

export interface StatusMark {
  icon: string;
  label: string;
  cls: string; // 既存の badge-st 配色（StatusBadge・main.css）を流用する
}

/** 一覧・詳細で共通の「状態の印」（ADR-020 D9「✓ ok／⚠ needs_review／✎ fixed／⏳ reading／✗ failed」）。
 * `status` が `committed`/`draft` のときは `review` で分ける。`discarded` は
 * 重複（`duplicate_of:<id>`）とそれ以外（取り消し）を分けて言う——主人には
 * 「登録済み（#n）」と「取り消した」は別の話だから（自分で決めた点。報告に書く）。 */
export function receiptStatusMark(status: ReceiptStatus, review: ReceiptReview, reason: string, t: Tr): StatusMark {
  if (status === "reading") return { icon: "⏳", label: t("money.receipts.status.reading"), cls: "st-waiting" };
  if (status === "failed") {
    const label =
      reason === "no_total"
        ? t("money.receipts.reason.noTotal")
        : reason === "ocr_error"
          ? t("money.receipts.reason.ocrError")
          : reason === "claude_error"
            ? t("money.receipts.reason.claudeError")
            : reason === "limit"
              ? t("money.receipts.reason.limit")
              : t("money.receipts.status.failed");
    return { icon: "✗", label, cls: "st-hold" };
  }
  if (status === "discarded") {
    const dup = parseDuplicateOf(reason);
    if (dup != null) return { icon: "≡", label: t("money.receipts.status.duplicateOf", { id: dup }), cls: "st-withdrawn" };
    return { icon: "✗", label: t("money.receipts.status.discarded"), cls: "st-withdrawn" };
  }
  // committed（多くはここ）・draft は review で分ける。
  if (review === "ok") return { icon: "✓", label: t("money.receipts.review.ok"), cls: "st-done" };
  if (review === "fixed") return { icon: "✎", label: t("money.receipts.review.fixed"), cls: "st-doing" };
  return { icon: "⚠", label: t("money.receipts.review.needsReview"), cls: "st-waiting" };
}

export const CHECK_KEYS: (keyof ReceiptChecks)[] = ["items_sum", "item_count", "tax_8", "tax_10", "total", "change"];

export const CHECK_LABEL_KEY: Record<keyof ReceiptChecks, TranslationKey> = {
  items_sum: "money.receipts.checks.itemsSum",
  item_count: "money.receipts.checks.itemCount",
  tax_8: "money.receipts.checks.tax8",
  tax_10: "money.receipts.checks.tax10",
  total: "money.receipts.checks.total",
  change: "money.receipts.checks.change",
};

/* ---------- 明細の編集フォーム値（契約 JSON ⇄ 画面） ---------- */

export interface ReceiptItemForm {
  key: string; // 行の React key（並べ替え・削除の安定用。契約 JSON には乗らない）
  line_no: number;
  name: string;
  qty: string;
  unit_price: string; // 空欄可（null 相当）
  amount: string;
  tax_rate: "" | "8" | "10";
  is_discount: boolean;
  category: string;
  subcategory: string;
  item_kind: string;
}

let keySeq = 1;
function nextKey(): string {
  keySeq += 1;
  return `ritem-${keySeq}`;
}

export function receiptItemToForm(item: ReceiptItem): ReceiptItemForm {
  return {
    key: nextKey(),
    line_no: item.line_no,
    name: item.name,
    qty: item.qty,
    unit_price: item.unit_price == null ? "" : String(item.unit_price),
    amount: String(item.amount),
    tax_rate: item.tax_rate == null ? "" : (String(item.tax_rate) as "8" | "10"),
    is_discount: item.is_discount,
    category: item.category,
    subcategory: item.subcategory,
    item_kind: item.item_kind,
  };
}

export function emptyReceiptItemForm(nextLineNo: number): ReceiptItemForm {
  return {
    key: nextKey(),
    line_no: nextLineNo,
    name: "",
    qty: "",
    unit_price: "",
    amount: "0",
    tax_rate: "",
    is_discount: false,
    category: "",
    subcategory: "",
    item_kind: "",
  };
}

/** 送信直前だけ空文字の name を弾かない——サーバの 400 をそのまま出す（recipeShared.ts
 * `formValueToRecipeBody` と同じ「二重に検算ロジックを持たない」方針）。 */
export function formToReceiptItemUpdate(form: ReceiptItemForm): ReceiptItemUpdate {
  return {
    line_no: form.line_no,
    name: form.name,
    qty: form.qty,
    unit_price: form.unit_price.trim() === "" ? null : Number(form.unit_price),
    amount: Number(form.amount || 0),
    tax_rate: form.tax_rate === "" ? null : (Number(form.tax_rate) as 8 | 10),
    is_discount: form.is_discount,
    category: form.category,
    subcategory: form.subcategory,
    item_kind: form.item_kind,
  };
}

export function nextLineNo(items: ReceiptItemForm[]): number {
  return items.reduce((max, it) => Math.max(max, it.line_no), 0) + 1;
}
