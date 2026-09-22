/* manor web — 家計の内訳の画面試験（ADR-020 追補 `GET /money/breakdown`）。他の module 試験
 * と同じ流儀（Receipts.test.tsx を手本に）: `globalThis.fetch` を直に差し替え、`api.ts` の
 * 実装（`realApi`）をそのまま通す。狭く・具体的に——見出しの帯の数字・大項目の行（予算超過の
 * 「超過」バッジ含む）が描けること、記録が無い月の案内が出ることだけを見る。
 */
import { describe, expect, it, vi, afterEach } from "vitest";
import { render, cleanup, screen, waitFor } from "@testing-library/react";
import { BreakdownPage } from "./breakdown/BreakdownPage";
import type { MoneyBreakdownResponse } from "../../app/types";

function fixture(overrides: Partial<MoneyBreakdownResponse> = {}): MoneyBreakdownResponse {
  return {
    ym: "2026-09",
    prev_ym: "2026-08",
    months: [
      { ym: "2026-08", expense: 98000, income: 0 },
      { ym: "2026-09", expense: 112400, income: 0 },
    ],
    summary: { expense: 112400, income: 0, prev_expense: 98000, diff: 14400, receipts: 9, days_with_spending: 14 },
    by_category: [
      { name: "食費", amount: 65000, share: 0.65, prev_amount: 60000, budget: 70000 },
      { name: "日用品", amount: 18000, share: 0.18, prev_amount: 12000, budget: 15000 }, // 予算超過の見本
    ],
    by_subcategory: [{ category: "食費", name: "食料品", amount: 38000, share: 0.38 }],
    by_item_kind: [{ name: "肉類", amount: 14000, share: 0.14, items: 6 }],
    by_store: [{ name: "ロピア", amount: 45000, share: 0.45, receipts: 4 }],
    top_items: [{ name: "牛乳", amount: 1188, qty: 6, store: "ロピア", item_kind: "乳製品", count: 6 }],
    daily: Array.from({ length: 30 }, (_, i) => ({
      date: `2026-09-${String(i + 1).padStart(2, "0")}`,
      amount: i % 3 === 0 ? 0 : 1000,
    })),
    top_category: { name: "食費", amount: 65000, share: 0.65 },
    top_item_kind: { name: "肉類", amount: 14000, share: 0.14 },
    ...overrides,
  };
}

function mockBreakdown(res: MoneyBreakdownResponse) {
  globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/v1/money/breakdown")) {
      return { ok: true, status: 200, json: async () => res };
    }
    throw new Error("unexpected fetch: " + url);
  }) as unknown as typeof fetch;
}

describe("money breakdown — 内訳（ADR-020 追補）", () => {
  afterEach(() => {
    cleanup();
  });

  it("見出しの帯の数字と大項目の行（予算超過の「超過」バッジ含む）が描ける", async () => {
    mockBreakdown(fixture());

    render(<BreakdownPage />);

    await waitFor(() => expect(screen.getByText("食費")).toBeTruthy());
    // 「今月の支出」タイル自体の値（月の推移グラフの棒ラベルにも同じ金額が出るので、
    // タイルへ絞って読む——getByText の一意性に賭けない）。
    const expenseTile = screen.getByText("今月の支出").closest(".tile");
    expect(expenseTile?.textContent).toContain("112400円");
    expect(screen.getByText("65000円（65%）")).toBeTruthy(); // 大項目「食費」の金額・割合
    expect(screen.getByText("超過")).toBeTruthy(); // 大項目「日用品」: 18000円 > 予算15000円
  });

  it("記録が無い月は案内が出る", async () => {
    mockBreakdown(
      fixture({
        summary: { expense: 0, income: 0, prev_expense: null, diff: null, receipts: 0, days_with_spending: 0 },
        by_category: [],
        by_subcategory: [],
        by_item_kind: [],
        by_store: [],
        top_items: [],
        daily: [],
        top_category: null,
        top_item_kind: null,
      })
    );

    render(<BreakdownPage />);

    await waitFor(() => expect(screen.getByText("この月の記録はありません")).toBeTruthy());
  });
});
