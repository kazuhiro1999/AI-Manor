import { describe, expect, it, vi, afterEach, beforeEach } from "vitest";
import { render, cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { Running } from "./Running";
import type { Board, Meta } from "../../app/types";
import { MetaContext, type MetaContextValue } from "../../app/MetaContext";
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
        <ToastProvider>
          <Running readOnly={false} />
        </ToastProvider>
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
        <ToastProvider>
          <Running readOnly={false} />
        </ToastProvider>
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

describe("Running — 一覧の行から状態を選んで変える（T55・意見箱。2026-09-15 ドロップダウンへ）", () => {
  const originalFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = originalFetch;
    cleanup();
  });

  function mockFetchFor(onFetch?: (url: string, init?: RequestInit) => void, board: Board = makeBoard()) {
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      onFetch?.(url, init);
      return { ok: true, status: 200, json: async () => board };
    }) as unknown as typeof fetch;
  }

  function renderRunning(readOnly = false, meta: Partial<Meta> | null = null) {
    const tree = (
      <MemoryRouter>
        <ToastProvider>
          <Running readOnly={readOnly} />
        </ToastProvider>
      </MemoryRouter>
    );
    const ctx: MetaContextValue = {
      meta: meta as Meta,
      reload: async () => {},
      setupJustCompleted: false,
      markSetupJustCompleted: () => {},
    };
    return render(meta ? <MetaContext.Provider value={ctx}>{tree}</MetaContext.Provider> : tree);
  }

  async function rowOf(text: RegExp) {
    await waitFor(() => expect(screen.getByText(text)).toBeTruthy());
    return screen.getByText(text).closest(".row-item") as HTMLElement;
  }

  function optionValues(select: HTMLElement): string[] {
    return Array.from((select as HTMLSelectElement).options).map((o) => o.value);
  }

  it("doing の行の選択肢は「いまの状態＋行ける先」（done・hold・waiting・withdrawn）", async () => {
    mockFetchFor();
    renderRunning();
    const row = await rowOf(/執事のタスク/);
    const select = within(row).getByRole("combobox", { name: /T2/ });
    expect(optionValues(select)).toEqual(["doing", "done", "hold", "waiting", "withdrawn"]);
  });

  it("done を選ぶと status/done を即 POST する", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    mockFetchFor((url, init) => calls.push({ url, init }));
    const user = userEvent.setup();
    renderRunning();
    const row = await rowOf(/執事のタスク/);
    await user.selectOptions(within(row).getByRole("combobox", { name: /T2/ }), "done");
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes("/api/v1/tasks/task/T2/status") && (c.init?.method || "").toUpperCase() === "POST")).toBe(true)
    );
    const postCall = calls.find((c) => c.url.includes("/api/v1/tasks/task/T2/status"));
    const sent = JSON.parse(String(postCall?.init?.body));
    expect(sent).toEqual({ status: "done" });
  });

  it("進行中→保留に戻せる（主人 2026-09-15）", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    mockFetchFor((url, init) => calls.push({ url, init }));
    const user = userEvent.setup();
    renderRunning();
    const row = await rowOf(/執事のタスク/);
    await user.selectOptions(within(row).getByRole("combobox", { name: /T2/ }), "hold");
    await waitFor(() => expect(calls.some((c) => c.url.includes("/api/v1/tasks/task/T2/status"))).toBe(true));
    const sent = JSON.parse(String(calls.find((c) => c.url.includes("/T2/status"))?.init?.body));
    expect(sent.status).toBe("hold");
  });

  it("waiting を選ぶと「何を待つか」の入力欄が出て、確定するまで POST しない", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    mockFetchFor((url, init) => calls.push({ url, init }));
    const user = userEvent.setup();
    renderRunning();
    const row = await rowOf(/執事のタスク/);
    await user.selectOptions(within(row).getByRole("combobox", { name: /T2/ }), "waiting");
    const noteInput = within(row).getByRole("textbox", { name: "何を待つか" });
    expect(calls.some((c) => c.url.includes("/T2/status"))).toBe(false);
    const confirm = within(row).getByRole("button", { name: "確定" });
    expect((confirm as HTMLButtonElement).disabled).toBe(true);
    await user.type(noteInput, "先生の返事");
    await user.click(confirm);
    await waitFor(() => expect(calls.some((c) => c.url.includes("/T2/status"))).toBe(true));
    const sent = JSON.parse(String(calls.find((c) => c.url.includes("/T2/status"))?.init?.body));
    expect(sent).toEqual({ status: "waiting", note: "先生の返事" });
  });

  it("選択肢は meta の task_transitions（状態機械）から取る——画面に表を持たない", async () => {
    mockFetchFor();
    // 状態機械が「doing からは done だけ」に変わった、という meta を渡す
    renderRunning(false, { task_transitions: { doing: ["done"] }, task_note_required: [] });
    const row = await rowOf(/執事のタスク/);
    expect(optionValues(within(row).getByRole("combobox", { name: /T2/ }))).toEqual(["doing", "done"]);
  });

  it("常駐（resident）は取り下げにしか行けない（状態機械どおり）", async () => {
    const board = makeBoard();
    board.tasks = [...board.tasks, { id: "T4", project_id: "P1", status: "resident", owner: "butler", title: "常駐のタスク" } as Board["tasks"][number]];
    mockFetchFor(undefined, board);
    renderRunning();
    const row = await rowOf(/常駐のタスク/);
    expect(optionValues(within(row).getByRole("combobox", { name: /T4/ }))).toEqual(["resident", "withdrawn"]);
  });

  it("readOnly では状態の選択を出さない", async () => {
    mockFetchFor();
    renderRunning(true);
    const row = await rowOf(/執事のタスク/);
    expect(within(row).queryByRole("combobox")).toBeNull();
  });

  it("done の行（終端）には状態の選択を出さない", async () => {
    mockFetchFor();
    renderRunning();
    const row = await rowOf(/完了したタスク/);
    expect(within(row).queryByRole("combobox")).toBeNull();
  });
});
