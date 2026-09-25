/* manor web — レシピ帳に並ぶ YouTube の動画（ADR-023 D5）の画面試験。Recipes.test.tsx と同じ流儀。 */
import { describe, expect, it, vi, afterEach } from "vitest";
import { render, cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { RecipesRouter } from "./RecipesRouter";
import { ToastProvider } from "../../components/Toast";
import type { RecipeVideosPayload } from "../../app/types";

function payload(overrides: Partial<RecipeVideosPayload> = {}): RecipeVideosPayload {
  return {
    items: [
      {
        video_id: "aaaaaaaaaaa",
        url: "https://www.youtube.com/watch?v=aaaaaaaaaaa",
        title: "豚バラキャベツ炒め",
        channel: "ch",
        thumbnail_url: "https://i.ytimg.com/a.jpg",
        seconds: 40,
        has_recipe: true,
        category: "",
        main_ingredient: "肉",
        cuisine: "",
        sources: [
          { user_id: "master", user_name: "主人", playlist_title: "レシピ" },
          { user_id: "u2", user_name: "はなこ", playlist_title: "作りたい" },
        ],
        matched: [{ field: "ingredient", text: "豚バラ肉" }],
      },
    ],
    configured: true,
    sync: { synced_at: "2026-09-25T10:00:00", failed: [], running: false },
    ...overrides,
  };
}

function mockFetch(videos: RecipeVideosPayload, calls: string[]) {
  globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push(`${(init?.method || "GET").toUpperCase()} ${url}`);
    if (url.includes("/kitchen/videos/sync")) return { ok: true, status: 200, json: async () => ({ running: true, last: null }) };
    if (url.includes("/kitchen/videos")) return { ok: true, status: 200, json: async () => videos };
    if (url.includes("/kitchen/recipes/facets")) return { ok: true, status: 200, json: async () => ({ category: [], main_ingredient: [], cuisine: [], tags: [] }) };
    if (url.includes("/kitchen/recipes")) return { ok: true, status: 200, json: async () => ({ items: [] }) };
    throw new Error("unexpected fetch: " + url);
  }) as unknown as typeof fetch;
}

function renderList() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <ToastProvider>
        <RecipesRouter />
      </ToastProvider>
    </MemoryRouter>
  );
}

describe("kitchen recipes — YouTube の動画（ADR-023 D5）", () => {
  afterEach(() => {
    cleanup();
  });

  it("家族の再生リストの動画を YouTube の印つきで並べ、押すと YouTube・下書きは取り込み画面へ", async () => {
    const calls: string[] = [];
    mockFetch(payload(), calls);
    const { container } = renderList();
    await waitFor(() => expect(screen.getByText("豚バラキャベツ炒め")).toBeTruthy());
    expect(screen.getByText("YouTube（家族の再生リスト）")).toBeTruthy();
    const card = container.querySelector('[data-video-id="aaaaaaaaaaa"]') as HTMLElement;
    const open = card.querySelector("a.recipe-card") as HTMLAnchorElement;
    expect(open.href).toBe("https://www.youtube.com/watch?v=aaaaaaaaaaa");
    expect(open.target).toBe("_blank");
    expect(screen.getByText("主人の「レシピ」")).toBeTruthy();
    expect(screen.getByText("はなこの「作りたい」")).toBeTruthy();
    expect(screen.getByText("材料に 豚バラ肉")).toBeTruthy();
    expect(screen.getByRole("link", { name: "下書きにする" }).getAttribute("href")).toBe(
      "/kitchen/recipes/new?url=" + encodeURIComponent("https://www.youtube.com/watch?v=aaaaaaaaaaa")
    );
    expect(screen.getByText(/最後の同期 2026-09-25 10:00/)).toBeTruthy();
  });

  it("検索語は動画の一覧にも同じように送り、同期ボタンは POST する", async () => {
    const calls: string[] = [];
    mockFetch(payload(), calls);
    renderList();
    await waitFor(() => expect(screen.getByText("豚バラキャベツ炒め")).toBeTruthy());
    const user = userEvent.setup();
    await user.type(screen.getByPlaceholderText(/./, { exact: false }) as HTMLInputElement, "豚肉");
    await waitFor(() => expect(calls.some((c) => c.startsWith("GET") && c.includes("/kitchen/videos?q=%E8%B1%9A%E8%82%89"))).toBe(true));
    await user.click(screen.getByRole("button", { name: "再生リストを同期" }));
    await waitFor(() => expect(calls.some((c) => c === "POST /api/v1/kitchen/videos/sync")).toBe(true));
  });

  it("再生リストが設定されていなければ、同期の欄は出さない", async () => {
    const calls: string[] = [];
    mockFetch(payload({ items: [], configured: false }), calls);
    const { container } = renderList();
    await waitFor(() => expect(calls.some((c) => c.includes("/kitchen/videos"))).toBe(true));
    expect(container.querySelector("#recipe-videos-sync")).toBeNull();
  });
});
