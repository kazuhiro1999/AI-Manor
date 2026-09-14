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
