import { describe, expect, it, vi, afterEach, beforeEach } from "vitest";
import { render, cleanup, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { Judge } from "./Judge";
import type { Board } from "../../app/types";
import { ToastProvider } from "../../components/Toast";

function makeBoard(pending: Board["pending"]): Board {
  return {
    today: "2026-09-03",
    pending,
    tasks: [],
    delegated: [],
    projects: [],
    milestones: [],
    recent_done: [],
    withdrawn_recent: [],
    notes: [],
    counts: { pending: pending.length, doing: 0, doing_butler: 0, doing_master: 0, resident: 0, blocked_ready: 0, stale: 0, done_total: 0 },
    fingerprint: "fp-judge",
  };
}

describe("Judge — 要対応カードの根拠（ADR-006 §2 D5・D7）", () => {
  const originalFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = originalFetch;
    cleanup();
  });

  it("evidence があれば Markdown として描画する", async () => {
    const board = makeBoard([
      {
        id: "D1",
        status: "open",
        title: "見積もりの判断",
        asked_at: new Date().toISOString(),
        days: 1,
        stale: false,
        risk: "low",
        background: "",
        ruling: null,
        evidence: "- 見積書 A社\n- 見積書 B社",
        project_id: null,
        tasks: [],
      },
    ]);
    beforeEachFetch(board);

    render(
      <ToastProvider>
        <MemoryRouter>
          <Judge readOnly={false} />
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => expect(screen.getByText(/見積もりの判断/)).toBeTruthy());
    const evidenceBlock = document.querySelector('[data-testid="card-evidence"]') as HTMLElement;
    expect(evidenceBlock).toBeTruthy();
    expect(evidenceBlock.textContent).toContain("見積書 A社");
    expect(evidenceBlock.textContent).toContain("見積書 B社");
    expect(evidenceBlock.textContent).not.toContain("根拠の記載なし");
  });

  it("evidence が空文字なら「根拠の記載なし」を薄く出す", async () => {
    const board = makeBoard([
      {
        id: "D2",
        status: "open",
        title: "根拠なしの判断",
        asked_at: new Date().toISOString(),
        days: 1,
        stale: false,
        risk: "low",
        background: "",
        ruling: null,
        evidence: "",
        project_id: null,
        tasks: [],
      },
    ]);
    beforeEachFetch(board);

    render(
      <ToastProvider>
        <MemoryRouter>
          <Judge readOnly={false} />
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => expect(screen.getByText(/根拠なしの判断/)).toBeTruthy());
    const evidenceBlock = document.querySelector('[data-testid="card-evidence"]') as HTMLElement;
    expect(evidenceBlock).toBeTruthy();
    const note = evidenceBlock.querySelector(".panel-note");
    expect(note?.textContent).toBe("根拠の記載なし");
  });

  it("evidence が undefined（古いバックエンド）でも落ちずに「根拠の記載なし」を出す", async () => {
    const board = makeBoard([
      {
        id: "D3",
        status: "open",
        title: "旧バックエンドの判断",
        asked_at: new Date().toISOString(),
        days: 1,
        stale: false,
        risk: "low",
        background: "",
        ruling: null,
        project_id: null,
        tasks: [],
      },
    ]);
    beforeEachFetch(board);

    render(
      <ToastProvider>
        <MemoryRouter>
          <Judge readOnly={false} />
        </MemoryRouter>
      </ToastProvider>
    );

    await waitFor(() => expect(screen.getByText(/旧バックエンドの判断/)).toBeTruthy());
    const evidenceBlock = document.querySelector('[data-testid="card-evidence"]') as HTMLElement;
    expect(evidenceBlock.textContent).toContain("根拠の記載なし");
  });

  function beforeEachFetch(board: Board) {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => board,
    }) as unknown as typeof fetch;
  }
});
