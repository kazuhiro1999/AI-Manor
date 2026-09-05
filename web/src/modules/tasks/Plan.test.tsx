import { describe, expect, it, vi, afterEach } from "vitest";
import { render, cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { Plan } from "./Plan";
import type { Board } from "../../app/types";
import { ToastProvider } from "../../components/Toast";

/* ADR-013 D1: プロジェクトの作成・変更を画面（計画 → プロジェクト俯瞰）から行えることの
 * 検算。**code はここでしか受け取らない**——変更フォームには code の入力欄が出ないこと、
 * 変更が `POST /tasks/project/{code}` へ送られることを確かめる。 */

function makeBoard(projects: Board["projects"]): Board {
  return {
    today: "2026-09-05",
    pending: [],
    tasks: [],
    delegated: [],
    projects,
    milestones: [],
    recent_done: [],
    withdrawn_recent: [],
    notes: [],
    counts: { pending: 0, doing: 0, doing_butler: 0, doing_master: 0, resident: 0, blocked_ready: 0, stale: 0, done_total: 0 },
    fingerprint: "fp-plan",
  };
}

function baseProject(): Board["projects"][number] {
  return {
    id: "P1",
    code: "gogaku",
    title: "語学の勉強",
    kind: "趣味",
    priority: 2,
    preset: "standard",
    status: "active",
    next_action: "単語帳を作る",
    due: null,
    days_left: null,
    interest: { nearest_date: null, doing: 0, last_event_at: null, rank: 1 },
  };
}

function mockFetchFor(board: Board, onFetch?: (url: string, init?: RequestInit) => void) {
  globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method || "GET").toUpperCase();
    onFetch?.(url, init);

    if (url.includes("/tasks/project") && method === "POST") {
      const body = init?.body ? JSON.parse(String(init.body)) : {};
      const idMatch = url.match(/\/tasks\/project\/([^/?]+)/);
      const json = async () => ({ id: idMatch ? decodeURIComponent(idMatch[1]) : body.code || "P99" });
      return { ok: true, status: 200, json };
    }
    const json = async () => {
      if (url.includes("/tasks/board")) return board;
      throw new Error("unexpected fetch: " + url);
    };
    return { ok: true, status: 200, json };
  }) as unknown as typeof fetch;
}

describe("Plan — プロジェクト俯瞰（ADR-013 D1: 作成・変更を画面から）", () => {
  afterEach(() => {
    cleanup();
  });

  it("「+ プロジェクト」から作成すると POST /tasks/project へ code を含めて送る", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    mockFetchFor(makeBoard([]), (url, init) => calls.push({ url, init }));

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/projects"]}>
        <ToastProvider>
          <Plan readOnly={false} />
        </ToastProvider>
      </MemoryRouter>
    );
    await waitFor(() => expect(screen.getByText("プロジェクト俯瞰")).toBeTruthy());

    await user.click(screen.getByRole("button", { name: "+ プロジェクト" }));
    await user.type(screen.getByLabelText("code"), "gogaku2");
    await user.type(screen.getByLabelText("名前"), "第二の語学");
    await user.click(screen.getByRole("button", { name: "追加" }));

    await waitFor(() =>
      expect(calls.some((c) => c.url.endsWith("/api/v1/tasks/project") && (c.init?.method || "").toUpperCase() === "POST")).toBe(true)
    );
    const postCall = calls.find((c) => c.url.endsWith("/api/v1/tasks/project") && (c.init?.method || "").toUpperCase() === "POST");
    const sent = JSON.parse(String(postCall?.init?.body));
    expect(sent.code).toBe("gogaku2");
    expect(sent.name).toBe("第二の語学");
  });

  it("行の「編集」を開くと code の入力欄は出ない（作成時のみ変更可能という制約）", async () => {
    mockFetchFor(makeBoard([baseProject()]));

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/projects"]}>
        <ToastProvider>
          <Plan readOnly={false} />
        </ToastProvider>
      </MemoryRouter>
    );
    await waitFor(() => expect(screen.getByText("語学の勉強")).toBeTruthy());

    await user.click(screen.getByRole("button", { name: "編集" }));
    await waitFor(() => expect(screen.getByText("gogaku を編集")).toBeTruthy());
    expect(screen.queryByLabelText("code")).toBeNull();
    expect(screen.getByText(/code はここでは変更できません/)).toBeTruthy();
  });

  it("編集を保存すると POST /tasks/project/{code} へ送る（code 自体は送らない）", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    mockFetchFor(makeBoard([baseProject()]), (url, init) => calls.push({ url, init }));

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/projects"]}>
        <ToastProvider>
          <Plan readOnly={false} />
        </ToastProvider>
      </MemoryRouter>
    );
    await waitFor(() => expect(screen.getByText("語学の勉強")).toBeTruthy());

    await user.click(screen.getByRole("button", { name: "編集" }));
    const heading = await screen.findByText("gogaku を編集");
    const form = heading.closest(".form-grid") as HTMLElement;
    const nameInput = within(form).getByDisplayValue("語学の勉強");
    await user.clear(nameInput);
    await user.type(nameInput, "語学の勉強(改)");
    await user.click(within(form).getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(calls.some((c) => c.url.includes("/api/v1/tasks/project/gogaku") && (c.init?.method || "").toUpperCase() === "POST")).toBe(
        true
      )
    );
    const putCall = calls.find((c) => c.url.includes("/api/v1/tasks/project/gogaku"));
    const sent = JSON.parse(String(putCall?.init?.body));
    expect(sent.name).toBe("語学の勉強(改)");
    expect(sent.code).toBeUndefined();
  });
});
