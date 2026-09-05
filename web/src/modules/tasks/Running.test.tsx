import { describe, expect, it, vi, afterEach, beforeEach } from "vitest";
import { render, cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { Running } from "./Running";
import type { Board } from "../../app/types";
import { ToastBanner, ToastProvider } from "../../components/Toast";

function makeBoard(): Board {
  return {
    today: "2026-09-02",
    pending: [],
    tasks: [
      { id: "T1", project_id: "P1", status: "doing", owner: "master", title: "主人のタスク" },
      { id: "T2", project_id: "P1", status: "doing", owner: "butler", title: "執事のタスク" },
      { id: "T3", project_id: "P1", status: "done", owner: "butler", title: "完了したタスク", done_at: new Date().toISOString() },
    ] as Board["tasks"],
    delegated: [],
    projects: [
      {
        id: "P1",
        code: "p1",
        title: "台所",
        priority: 1,
        preset: "standard",
        status: "active",
        days_left: null,
        interest: { nearest_date: null, doing: 2, last_event_at: null, rank: 1 },
      },
    ],
    milestones: [],
    recent_done: [{ id: "T3", project_id: "P1", status: "done", owner: "butler", title: "完了したタスク", done_at: new Date().toISOString() } as Board["recent_done"][number]],
    withdrawn_recent: [],
    notes: [],
    counts: { pending: 0, doing: 2, doing_butler: 1, doing_master: 1, resident: 0, blocked_ready: 0, stale: 0, done_total: 1 },
    fingerprint: "fp1",
  };
}

describe("Running status order", () => {
  const originalFetch = globalThis.fetch;
  beforeEach(() => {
    const board = makeBoard();
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => board,
    }) as unknown as typeof fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    cleanup();
  });

  it("主人の作業（進行中）ブロックが実行中（執事）より先に出る", async () => {
    render(
      <MemoryRouter>
        <Running readOnly={false} />
      </MemoryRouter>
    );
    await waitFor(() => expect(screen.getByText(/主人のタスク/)).toBeTruthy());
    const wrap = document.getElementById("running-list")!;
    const heads = Array.from(wrap.querySelectorAll(".status-block-head")).map((n) => n.textContent);
    const masterIdx = heads.findIndex((h) => h?.includes("主人の作業"));
    const butlerIdx = heads.findIndex((h) => h?.includes("実行中（執事）"));
    expect(masterIdx).toBeGreaterThanOrEqual(0);
    expect(butlerIdx).toBeGreaterThan(masterIdx);
  });

  it("完了したタスクは既定で畳まれている（body が hidden）", async () => {
    render(
      <MemoryRouter>
        <Running readOnly={false} />
      </MemoryRouter>
    );
    await waitFor(() => expect(screen.getByText(/完了したタスク/)).toBeTruthy());
    const row = screen.getByText(/完了したタスク/);
    const body = row.closest(".done-day-body") as HTMLElement;
    expect(body.hidden).toBe(true);
  });
});

describe("Running — 伝達（ADR-013 D3: メモの追加を画面から）", () => {
  const originalFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = originalFetch;
    cleanup();
  });

  function mockFetchFor(onFetch?: (url: string, init?: RequestInit) => void) {
    const board = makeBoard();
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method || "GET").toUpperCase();
      onFetch?.(url, init);
      if (url.includes("/tasks/note") && method === "POST") {
        return { ok: true, status: 200, json: async () => ({ id: "N9" }) };
      }
      return { ok: true, status: 200, json: async () => board };
    }) as unknown as typeof fetch;
  }

  it("readOnly では「+ 伝達」ボタンを出さない", async () => {
    mockFetchFor();
    render(
      <MemoryRouter>
        <ToastProvider>
          <Running readOnly={true} />
        </ToastProvider>
      </MemoryRouter>
    );
    await waitFor(() => expect(screen.getByText("伝達キュー")).toBeTruthy());
    expect(screen.queryByRole("button", { name: "+ 伝達" })).toBeNull();
  });

  it("本文だけを入れて追加すると、about なしで POST /tasks/note へ送る", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    mockFetchFor((url, init) => calls.push({ url, init }));

    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <ToastProvider>
          <Running readOnly={false} />
        </ToastProvider>
      </MemoryRouter>
    );
    await waitFor(() => expect(screen.getByText("伝達キュー")).toBeTruthy());

    await user.click(screen.getByRole("button", { name: "+ 伝達" }));
    await user.type(screen.getByLabelText("本文"), "下位エージェントへの伝達");
    await user.click(screen.getByRole("button", { name: "追加" }));

    await waitFor(() =>
      expect(calls.some((c) => c.url.includes("/api/v1/tasks/note") && (c.init?.method || "").toUpperCase() === "POST")).toBe(true)
    );
    const postCall = calls.find((c) => c.url.includes("/api/v1/tasks/note"));
    const sent = JSON.parse(String(postCall?.init?.body));
    expect(sent.title).toBe("下位エージェントへの伝達");
    expect(sent.about).toBeUndefined();
  });

  it("宛先のプロジェクトを選ぶと about にプロジェクトの id を含めて送る", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    mockFetchFor((url, init) => calls.push({ url, init }));

    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <ToastProvider>
          <Running readOnly={false} />
        </ToastProvider>
      </MemoryRouter>
    );
    await waitFor(() => expect(screen.getByText("伝達キュー")).toBeTruthy());

    await user.click(screen.getByRole("button", { name: "+ 伝達" }));
    await user.type(screen.getByLabelText("本文"), "進捗の伝達");
    await user.selectOptions(screen.getByLabelText(/宛先/), "P1");
    await user.click(screen.getByRole("button", { name: "追加" }));

    await waitFor(() =>
      expect(calls.some((c) => c.url.includes("/api/v1/tasks/note") && (c.init?.method || "").toUpperCase() === "POST")).toBe(true)
    );
    const postCall = calls.find((c) => c.url.includes("/api/v1/tasks/note"));
    const sent = JSON.parse(String(postCall?.init?.body));
    expect(sent.about).toBe("P1");
  });

  it("本文が空のままでは送信を弾く（fetch の POST を呼ばない）", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    mockFetchFor((url, init) => calls.push({ url, init }));

    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <ToastProvider>
          <ToastBanner />
          <Running readOnly={false} />
        </ToastProvider>
      </MemoryRouter>
    );
    await waitFor(() => expect(screen.getByText("伝達キュー")).toBeTruthy());

    await user.click(screen.getByRole("button", { name: "+ 伝達" }));
    await user.click(screen.getByRole("button", { name: "追加" }));

    expect(screen.getByText("本文は必須です")).toBeTruthy();
    expect(calls.some((c) => c.url.includes("/api/v1/tasks/note") && (c.init?.method || "").toUpperCase() === "POST")).toBe(false);
  });
});
