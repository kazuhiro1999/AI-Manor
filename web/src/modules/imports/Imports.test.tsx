import { describe, expect, it, vi, afterEach, beforeEach } from "vitest";
import { render, cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { importsModule } from "./index";
import type { ImportPreview } from "../../app/types";
import { ToastProvider } from "../../components/Toast";

const ImportsScreen = importsModule.routes[0].element as JSX.Element;

describe("imports preview", () => {
  const originalFetch = globalThis.fetch;
  beforeEach(() => {
    const preview: ImportPreview = {
      rows: [{ line: 2, date: "2026-09-02", amount: 200, category: "雑費", memo: "", kind: "expense", import_hash: "h2" }],
      duplicates: [{ line: 1, date: "2026-09-01", amount: 100, category: "食費", memo: "", kind: "expense", import_hash: "h1" }],
      unreadable: [],
      total: 2,
    };
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => preview,
    }) as unknown as typeof fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    cleanup();
  });

  it("重複した行に import-row duplicate クラスが付く", async () => {
    const user = userEvent.setup();
    render(<ToastProvider>{ImportsScreen}</ToastProvider>);
    const file = new File(["date,amount\n2026-09-01,100"], "test.csv", { type: "text/csv" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, file);
    const button = screen.getByRole("button", { name: "プレビュー" });
    await user.click(button);
    await waitFor(() => expect(document.querySelectorAll(".import-row").length).toBeGreaterThan(0));
    const rows = document.querySelectorAll(".import-row");
    expect(rows.length).toBe(2);
    expect(rows[0].classList.contains("duplicate")).toBe(true);
    expect(rows[1].classList.contains("duplicate")).toBe(false);
  });
});
