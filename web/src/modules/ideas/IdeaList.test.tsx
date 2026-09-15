import { describe, expect, it, vi, afterEach } from "vitest";
import { render, cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { IdeaList } from "./IdeaList";
import type { Task } from "../../app/types";
import { ToastProvider } from "../../components/Toast";

function makeItems(): Task[] {
  return [
    { id: "T1", project_id: null, status: "hold", owner: "master", title: "仕分け待ちの意見", body: "本文1", source: "idea" } as Task,
    { id: "T2", project_id: null, status: "done", owner: "master", title: "完了した意見", body: "本文2", source: "idea" } as Task,
  ];
}

describe("IdeaList — T61 修正・取り下げ", () => {
  const originalFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = originalFetch;
    cleanup();
  });

  function mockFetchFor(onFetch?: (url: string, init?: RequestInit) => void) {
    const items = makeItems();
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      onFetch?.(url, init);
      return { ok: true, status: 200, json: async () => ({ items }) };
    }) as unknown as typeof fetch;
  }

  it("済んでいない意見にだけ「直す」「取り下げ」ボタンが出る", async () => {
    mockFetchFor();
    render(
      <ToastProvider>
        <IdeaList reloadKey={0} />
      </ToastProvider>
    );
    await waitFor(() => expect(screen.getByText("仕分け待ちの意見")).toBeTruthy());

    const openRow = screen.getByText("仕分け待ちの意見").closest(".row-item") as HTMLElement;
    expect(within(openRow).getByRole("button", { name: "直す" })).toBeTruthy();
    expect(within(openRow).getByRole("button", { name: "取り下げ" })).toBeTruthy();

    const doneRow = screen.getByText("完了した意見").closest(".row-item") as HTMLElement;
    expect(within(doneRow).queryByRole("button", { name: "直す" })).toBeNull();
    expect(within(doneRow).queryByRole("button", { name: "取り下げ" })).toBeNull();
  });

  it("「直す」→編集→保存で PATCH /api/v1/ideas/{id} を送る", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    mockFetchFor((url, init) => calls.push({ url, init }));

    const user = userEvent.setup();
    render(
      <ToastProvider>
        <IdeaList reloadKey={0} />
      </ToastProvider>
    );
    await waitFor(() => expect(screen.getByText("仕分け待ちの意見")).toBeTruthy());

    const row = screen.getByText("仕分け待ちの意見").closest(".row-item") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "直す" }));

    const titleInput = screen.getByLabelText("題名") as HTMLInputElement;
    await user.clear(titleInput);
    await user.type(titleInput, "直した題名");
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(calls.some((c) => c.url.includes("/api/v1/ideas/T1") && (c.init?.method || "").toUpperCase() === "PATCH")).toBe(true)
    );
    const patchCall = calls.find((c) => c.url.includes("/api/v1/ideas/T1"));
    const sent = JSON.parse(String(patchCall?.init?.body));
    expect(sent.title).toBe("直した題名");
  });

  it("「取り下げ」→理由入力→確定で status=withdrawn をPOSTする", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    mockFetchFor((url, init) => calls.push({ url, init }));

    const user = userEvent.setup();
    render(
      <ToastProvider>
        <IdeaList reloadKey={0} />
      </ToastProvider>
    );
    await waitFor(() => expect(screen.getByText("仕分け待ちの意見")).toBeTruthy());

    const row = screen.getByText("仕分け待ちの意見").closest(".row-item") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "取り下げ" }));

    await user.type(screen.getByPlaceholderText("取り下げる理由"), "やっぱりやめる");
    await user.click(screen.getByRole("button", { name: "取り下げる" }));

    await waitFor(() =>
      expect(calls.some((c) => c.url.includes("/api/v1/tasks/task/T1/status") && (c.init?.method || "").toUpperCase() === "POST")).toBe(
        true
      )
    );
    const postCall = calls.find((c) => c.url.includes("/api/v1/tasks/task/T1/status"));
    const sent = JSON.parse(String(postCall?.init?.body));
    expect(sent.status).toBe("withdrawn");
    expect(sent.note).toBe("やっぱりやめる");
  });

  it("取り下げの理由が空だと確定ボタンが押せない", async () => {
    mockFetchFor();
    const user = userEvent.setup();
    render(
      <ToastProvider>
        <IdeaList reloadKey={0} />
      </ToastProvider>
    );
    await waitFor(() => expect(screen.getByText("仕分け待ちの意見")).toBeTruthy());

    const row = screen.getByText("仕分け待ちの意見").closest(".row-item") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "取り下げ" }));

    expect(screen.getByRole("button", { name: "取り下げる" })).toBeDisabled();
  });
});

describe("IdeaList — 確認の往復（主人 2026-09-15: 報告→OK／もう少し→夜勤へ）", () => {
  const originalFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = originalFetch;
    cleanup();
  });

  function itemsForReview(): Task[] {
    return [
      { id: "T5", project_id: null, status: "waiting", owner: "master", title: "一覧から状態を変えたい", body: "一覧から状態を変えたい", status_note: "タスク一覧の各行に状態のドロップダウンが付きました", source: "idea" } as Task,
      { id: "T6", project_id: null, status: "todo", owner: "master", title: "戻された意見", body: "戻された意見", status_note: "ボタンではなくドロップダウンで", source: "idea" } as Task,
      { id: "T7", project_id: null, status: "done", owner: "master", title: "済んだ意見", body: "済んだ意見", status_note: "", source: "idea" } as Task,
    ];
  }

  function mockFetchFor(onFetch?: (url: string, init?: RequestInit) => void) {
    const items = itemsForReview();
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      onFetch?.(url, init);
      return { ok: true, status: 200, json: async () => ({ items }) };
    }) as unknown as typeof fetch;
  }

  function renderList() {
    return render(
      <ToastProvider>
        <IdeaList reloadKey={0} />
      </ToastProvider>
    );
  }

  it("ご確認待ちの意見には「執事からの報告」と OK／もう少し が出る。済んだ意見には出ない", async () => {
    mockFetchFor();
    renderList();
    await waitFor(() => expect(screen.getByText("一覧から状態を変えたい")).toBeTruthy());
    const row = screen.getByText("一覧から状態を変えたい").closest(".row-item") as HTMLElement;
    expect(within(row).getByText("ご確認待ち")).toBeTruthy();
    expect(within(row).getByText(/タスク一覧の各行に状態のドロップダウンが付きました/)).toBeTruthy();
    expect(within(row).getByRole("button", { name: "OK" })).toBeTruthy();
    expect(within(row).getByRole("button", { name: "もう少し" })).toBeTruthy();
    const doneRow = screen.getByText("済んだ意見").closest(".row-item") as HTMLElement;
    expect(within(doneRow).queryByRole("button", { name: "OK" })).toBeNull();
  });

  it("OK を押すと status=done を POST する", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    mockFetchFor((url, init) => calls.push({ url, init }));
    const user = userEvent.setup();
    renderList();
    await waitFor(() => expect(screen.getByText("一覧から状態を変えたい")).toBeTruthy());
    const row = screen.getByText("一覧から状態を変えたい").closest(".row-item") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "OK" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("/api/v1/tasks/task/T5/status"))).toBe(true));
    const sent = JSON.parse(String(calls.find((c) => c.url.includes("/T5/status"))?.init?.body));
    expect(sent).toEqual({ status: "done" });
  });

  it("「もう少し」→FB を書いて「夜勤へ戻す」で status=todo と note を POST する。空なら押せない", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    mockFetchFor((url, init) => calls.push({ url, init }));
    const user = userEvent.setup();
    renderList();
    await waitFor(() => expect(screen.getByText("一覧から状態を変えたい")).toBeTruthy());
    const row = screen.getByText("一覧から状態を変えたい").closest(".row-item") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "もう少し" }));
    const confirm = screen.getByRole("button", { name: "夜勤へ戻す" });
    expect((confirm as HTMLButtonElement).disabled).toBe(true);
    await user.type(screen.getByRole("textbox", { name: /直してほしい点/ }), "常駐も変えられるように");
    await user.click(confirm);
    await waitFor(() => expect(calls.some((c) => c.url.includes("/api/v1/tasks/task/T5/status"))).toBe(true));
    const sent = JSON.parse(String(calls.find((c) => c.url.includes("/T5/status"))?.init?.body));
    expect(sent).toEqual({ status: "todo", note: "常駐も変えられるように" });
  });

  it("夜勤へ戻した意見（todo・status_note あり）には「前回のご指摘」が出る", async () => {
    mockFetchFor();
    renderList();
    await waitFor(() => expect(screen.getByText("戻された意見")).toBeTruthy());
    const row = screen.getByText("戻された意見").closest(".row-item") as HTMLElement;
    expect(within(row).getByText(/前回のご指摘/)).toBeTruthy();
    expect(within(row).getByText(/ボタンではなくドロップダウンで/)).toBeTruthy();
  });

  it("完了した意見に status_note があれば「執事からの報告」として読める（T68）", async () => {
    const items: Task[] = [
      { id: "T8", project_id: null, status: "done", owner: "master", title: "済んだ意見2", body: "済んだ意見2", status_note: "ドロップダウンを付けました", source: "idea" } as Task,
    ];
    globalThis.fetch = vi.fn().mockImplementation(async () => ({ ok: true, status: 200, json: async () => ({ items }) })) as unknown as typeof fetch;
    renderList();
    await waitFor(() => expect(screen.getByText("済んだ意見2")).toBeTruthy());
    const row = screen.getByText("済んだ意見2").closest(".row-item") as HTMLElement;
    expect(within(row).getByText(/執事からの報告/)).toBeTruthy();
    expect(within(row).getByText(/ドロップダウンを付けました/)).toBeTruthy();
  });

  it("完了した意見に status_note が無ければ報告は出ない", async () => {
    mockFetchFor();
    renderList();
    await waitFor(() => expect(screen.getByText("済んだ意見")).toBeTruthy());
    const row = screen.getByText("済んだ意見").closest(".row-item") as HTMLElement;
    expect(within(row).queryByText(/執事からの報告/)).toBeNull();
  });
});
