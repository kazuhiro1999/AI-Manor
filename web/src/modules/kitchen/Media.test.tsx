/* manor web — 動画リストの画面試験（ADR-016 D4）。`Recipes.test.tsx` と同じ流儀:
 * `globalThis.fetch` を直に差し替え、`api.ts` の実装をそのまま通す（`mock.ts` は
 * `?mock=1` の手動確認用。ここでは送られる要求の中身を厳密に見たい）。
 */
import { describe, expect, it, vi, afterEach } from "vitest";
import { render, cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { MediaList } from "./MediaList";
import type { MediaItem } from "../../app/types";
import { ToastBanner, ToastProvider } from "../../components/Toast";

function item(overrides: Partial<MediaItem> = {}): MediaItem {
  return {
    id: "m1",
    title: "煮込み用の長い音楽",
    video_id: "aaaaaaaaaaa",
    url: "https://www.youtube.com/watch?v=aaaaaaaaaaa",
    thumbnail_url: "https://i.ytimg.com/vi/aaaaaaaaaaa/hqdefault.jpg",
    author: "架空チャンネル",
    memo: "",
    sort_order: 1,
    ...overrides,
  };
}

interface Call {
  url: string;
  method: string;
  body: unknown;
}

/** 一覧を返し、書き込みの要求を記録する fetch。`extra` で個別の応答を差し込める。 */
function mockFetch(items: MediaItem[], extra?: (url: string, init?: RequestInit) => unknown) {
  const calls: Call[] = [];
  globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method || "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : null });
    const custom = extra?.(url, init);
    if (custom) return custom;
    if (url.includes("/api/v1/kitchen/media/reorder")) {
      const ids = (JSON.parse(String(init?.body)) as { ids: string[] }).ids;
      return { ok: true, status: 200, json: async () => ({ items: ids.map((id) => items.find((m) => m.id === id)) }) };
    }
    if (method === "GET") {
      return { ok: true, status: 200, json: async () => ({ items, updated_at: "2026-09-12T18:00:00" }) };
    }
    return { ok: true, status: 200, json: async () => items[0] };
  }) as unknown as typeof fetch;
  return calls;
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <ToastProvider>
        <MediaList />
        {/* 画面の外側（App）が持つバナー。ここでは 4xx の文言を検算するために添える。 */}
        <ToastBanner />
      </ToastProvider>
    </MemoryRouter>
  );
}

describe("kitchen media — 動画リスト（ADR-016 D4）", () => {
  afterEach(() => cleanup());

  it("一覧を読んでサムネイル・題名・チャンネル名を出す", async () => {
    mockFetch([item()]);
    const { container } = renderPage();

    await waitFor(() => expect(screen.getByDisplayValue("煮込み用の長い音楽")).toBeTruthy());
    expect(screen.getByText("架空チャンネル")).toBeTruthy();
    const img = container.querySelector(".media-thumb img") as HTMLImageElement;
    expect(img.src).toContain("hqdefault.jpg");
  });

  it("URL を貼って「追加」で POST /kitchen/media へ url を送る", async () => {
    const calls = mockFetch([]);
    renderPage();
    await waitFor(() => expect(screen.getByText("（動画がありません。上の欄に YouTube の URL を貼ってください）")).toBeTruthy());

    await userEvent.type(screen.getByPlaceholderText("YouTube の URL を貼る"), "https://youtu.be/aaaaaaaaaaa");
    await userEvent.click(screen.getByRole("button", { name: "追加" }));

    const post = calls.find((c) => c.method === "POST");
    expect(post?.url).toContain("/api/v1/kitchen/media");
    expect(post?.body).toEqual({ url: "https://youtu.be/aaaaaaaaaaa", memo: "" });
  });

  it("409（重複）はサーバの文言をそのまま出す", async () => {
    mockFetch([], (url, init) => {
      if ((init?.method || "GET") !== "POST") return null;
      return { ok: false, status: 409, json: async () => ({ detail: "この動画は既に登録されています: aaaaaaaaaaa" }) };
    });
    renderPage();
    await waitFor(() => expect(screen.getByPlaceholderText("YouTube の URL を貼る")).toBeTruthy());

    await userEvent.type(screen.getByPlaceholderText("YouTube の URL を貼る"), "https://youtu.be/aaaaaaaaaaa");
    await userEvent.click(screen.getByRole("button", { name: "追加" }));

    await waitFor(() => expect(screen.getByText("この動画は既に登録されています: aaaaaaaaaaa")).toBeTruthy());
  });

  it("題名を直して欄から離れると PATCH で title だけを送る", async () => {
    const calls = mockFetch([item()]);
    renderPage();
    const title = await screen.findByDisplayValue("煮込み用の長い音楽");

    await userEvent.clear(title);
    await userEvent.type(title, "ながら見その1");
    await userEvent.tab();

    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    const patch = calls.find((c) => c.method === "PATCH");
    expect(patch?.url).toContain("/api/v1/kitchen/media/m1");
    expect(patch?.body).toEqual({ title: "ながら見その1" });
  });

  it("「下へ」で reorder に入れ替えた並びを送る", async () => {
    const items = [item(), item({ id: "m2", title: "下ごしらえのラジオ", video_id: "bbbbbbbbbbb", sort_order: 2 })];
    const calls = mockFetch(items);
    renderPage();
    await screen.findByDisplayValue("煮込み用の長い音楽");

    await userEvent.click(screen.getAllByRole("button", { name: "下へ" })[0]);

    await waitFor(() => expect(calls.some((c) => c.url.includes("/reorder"))).toBe(true));
    expect(calls.find((c) => c.url.includes("/reorder"))?.body).toEqual({ ids: ["m2", "m1"] });
  });

  it("削除は二度押し——一度目は確認の文言に変わるだけで DELETE を送らない", async () => {
    const calls = mockFetch([item()]);
    renderPage();
    await screen.findByDisplayValue("煮込み用の長い音楽");

    await userEvent.click(screen.getByRole("button", { name: "削除" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);

    await userEvent.click(screen.getByRole("button", { name: "もう一度押すと削除します" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
  });
});
