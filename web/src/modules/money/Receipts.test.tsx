/* manor web — レシート一覧の画面試験（ADR-020 D9）。他の module 試験と同じ流儀:
 * `globalThis.fetch` を直に差し替え、`api.ts` の実装（`realApi`）をそのまま通す
 * （`mock.ts` は `?mock=1` の手動確認用。`imports/Imports.test.tsx`・
 * `kitchen/Recipes.test.tsx` と同じ）。
 *
 * ここでは一覧が描ける・状態の印（✓／⚠／✎／⏳／✗）が出ることだけを見る
 * （`Imports.test.tsx` を手本に、狭く・具体的に）。
 */
import { describe, expect, it, vi, afterEach } from "vitest";
import { render, cleanup, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ReceiptsPage } from "./receipts/ReceiptsPage";
import type { ReceiptsListResponse, ReceiptSummary } from "../../app/types";
import { ToastProvider } from "../../components/Toast";

function summary(overrides: Partial<ReceiptSummary> = {}): ReceiptSummary {
  return {
    id: 1,
    status: "committed",
    review: "ok",
    store_name: "スーパーやまだ",
    purchased_at: "2026-09-20",
    total: 706,
    item_count: 3,
    method: "ocr",
    reason: "",
    created_at: "2026-09-20T10:00:00",
    committed_at: "2026-09-20T10:00:05",
    ...overrides,
  };
}

function mockList(items: ReceiptSummary[]) {
  globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/v1/money/receipts")) {
      const res: ReceiptsListResponse = {
        items,
        ocr: { available: true, device: "cpu", model: "PP-OCRv6-small" },
        today: { count: 2, limit: 30 },
      };
      return { ok: true, status: 200, json: async () => res };
    }
    throw new Error("unexpected fetch: " + url);
  }) as unknown as typeof fetch;
}

describe("money receipts — 一覧（ADR-020 D9）", () => {
  afterEach(() => {
    cleanup();
  });

  it("履歴に日付・店・合計・明細数が出る", async () => {
    mockList([summary()]);

    render(
      <MemoryRouter initialEntries={["/"]}>
        <ToastProvider>
          <ReceiptsPage />
        </ToastProvider>
      </MemoryRouter>
    );

    await waitFor(() => expect(screen.getByText("スーパーやまだ")).toBeTruthy());
    expect(screen.getByText("706円")).toBeTruthy();
    expect(screen.getByText("3点")).toBeTruthy();
  });

  it("状態の印: ok=✓・needs_review=⚠・fixed=✎・reading=⏳・failed=✗ が行ごとに出る", async () => {
    mockList([
      summary({ id: 1, store_name: "OKの店", status: "committed", review: "ok" }),
      summary({ id: 2, store_name: "要確認の店", status: "committed", review: "needs_review" }),
      summary({ id: 3, store_name: "直した店", status: "committed", review: "fixed" }),
      summary({ id: 4, store_name: "読み取り中の店", status: "reading", review: "needs_review", total: null, purchased_at: null }),
      summary({ id: 5, store_name: "失敗した店", status: "failed", review: "needs_review", reason: "no_total", total: null }),
    ]);

    const { container } = render(
      <MemoryRouter initialEntries={["/"]}>
        <ToastProvider>
          <ReceiptsPage />
        </ToastProvider>
      </MemoryRouter>
    );

    await waitFor(() => expect(screen.getByText("OKの店")).toBeTruthy());

    const rowFor = (label: string) => screen.getByText(label).closest(".row-item") as HTMLElement;
    expect(rowFor("OKの店").querySelector(".badge-st")?.textContent).toContain("✓");
    expect(rowFor("要確認の店").querySelector(".badge-st")?.textContent).toContain("⚠");
    expect(rowFor("直した店").querySelector(".badge-st")?.textContent).toContain("✎");
    expect(rowFor("読み取り中の店").querySelector(".badge-st")?.textContent).toContain("⏳");
    expect(rowFor("失敗した店").querySelector(".badge-st")?.textContent).toContain("✗");

    expect(container.querySelectorAll(".row-item").length).toBe(5);
  });

  it("一覧が空のときは空の案内が出る", async () => {
    mockList([]);

    render(
      <MemoryRouter initialEntries={["/"]}>
        <ToastProvider>
          <ReceiptsPage />
        </ToastProvider>
      </MemoryRouter>
    );

    await waitFor(() => expect(screen.getByText("レシートはまだありません")).toBeTruthy());
  });
});
