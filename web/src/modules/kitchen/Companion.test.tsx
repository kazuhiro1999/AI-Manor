/* manor web — レシピ詳細の「お供にこんなメニューはどうですか？」（ADR-021 D5）の画面試験。
 * Recipes.test.tsx と同じ流儀（`globalThis.fetch` を直に差し替える）。
 */
import { describe, expect, it, vi, afterEach, beforeEach } from "vitest";
import { render, cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { CompanionPanel } from "./CompanionPanel";
import { RecipesRouter } from "./RecipesRouter";
import { ToastProvider } from "../../components/Toast";
import type { CompanionSuggestion } from "../../app/types";

function suggestion(overrides: Partial<CompanionSuggestion> = {}): CompanionSuggestion {
  return {
    recipe_id: 6,
    eligible: true,
    main: {
      title: "塩唐揚げ",
      kind: "揚げ物",
      nutrition: { kcal: 552, protein_g: 29, fat_g: 42.8, carb_g: 14, salt_g: 1.9 },
      micro: { fiber_g: 0.1, vitamin_c_mg: 5.3, veg_g: 4 },
      under: ["fiber_g", "veg_g", "calcium_mg", "vitamin_c_mg"],
      over: ["fat_g"],
    },
    floor: { fiber_g: 7, veg_g: 120 },
    items: [
      {
        key: "recipe:7", source: "recipe", recipe_id: 7, catalog_key: null,
        title: "塩昆布の無限キャベツ", category: "副菜", kind: "生", heat: "none", tags: ["葉物"],
        minutes: 10, hero_image: "", sources: [], score: 7.1,
        reasons: [{ code: "fills_vitamin_c_mg", params: { value: 32.3 } }],
        nutrition: { kcal: 65, protein_g: 2.3, fat_g: 4, carb_g: 7.2, salt_g: 0.8 },
        micro: { vitamin_c_mg: 32.3 },
      },
      {
        key: "catalog:coleslaw", source: "catalog", recipe_id: null, catalog_key: "coleslaw",
        title: "コールスロー", category: "副菜", kind: "生", heat: "none", tags: ["酸味"],
        minutes: 10, hero_image: "https://img.example/coleslaw.jpg", score: 5.3,
        sources: [
          {
            url: "https://www.kurashiru.com/recipes/abc", site: "クラシル", title: "基本のコールスロー",
            image: "https://img.example/coleslaw.jpg", minutes: 10, why: "キャベツとマヨネーズの基本形",
          },
        ],
        reasons: [{ code: "pairs_fried", params: { main: "揚げ物" } }],
        nutrition: { kcal: 92, protein_g: 1, fat_g: 7, carb_g: 6, salt_g: 0.7 },
        micro: {},
      },
    ],
    ...overrides,
  };
}

function renderPanel() {
  return render(
    <MemoryRouter>
      <ToastProvider>
        <CompanionPanel recipeId={6} />
      </ToastProvider>
    </MemoryRouter>
  );
}

describe("kitchen — お供の提案（ADR-021 D5）", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });
  afterEach(() => {
    cleanup();
  });

  it("見出し・足りないもの・理由を出し、定番はレシピサイトの候補を開ける（AI の作り方は出さない）", async () => {
    const posts: { url: string; body: unknown }[] = [];
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "POST") {
        posts.push({ url, body: JSON.parse(String(init.body)) });
        return { ok: true, status: 200, json: async () => ({ items: [] }) };
      }
      if (url.endsWith("/api/v1/kitchen/recipes/6/companions")) {
        return { ok: true, status: 200, json: async () => suggestion() };
      }
      throw new Error("unexpected fetch: " + url);
    }) as unknown as typeof fetch;

    renderPanel();
    await waitFor(() => expect(screen.getByText("お供にこんなメニューはどうですか？")).toBeTruthy());
    // 足りないものは頭の3つだけ
    expect(screen.getByText("この一品だけだと 食物繊維・野菜・カルシウム が足りません。")).toBeTruthy();
    expect(screen.getByText("脂質 は多めです。")).toBeTruthy();
    expect(screen.getByText("ビタミンCを補う（32.3 mg）")).toBeTruthy();
    expect(screen.getByText("揚げ物の口直しに")).toBeTruthy();
    // うちのレシピはリンク、定番は文字
    expect(screen.getByRole("link", { name: "塩昆布の無限キャベツ" })).toBeTruthy();
    expect(screen.getByText("定番")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "作り方" })).toBeNull();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "レシピサイトの候補（1）" }));
    expect(screen.getByText("基本のコールスロー")).toBeTruthy();
    const site = screen.getByRole("link", { name: "サイトで見る" }) as HTMLAnchorElement;
    expect(site.href).toBe("https://www.kurashiru.com/recipes/abc");
    expect(site.target).toBe("_blank");
    const importLink = screen.getByRole("link", { name: "レシピ帳に入れる" }) as HTMLAnchorElement;
    expect(importLink.getAttribute("href")).toBe(
      "/kitchen/recipes/new?url=" + encodeURIComponent("https://www.kurashiru.com/recipes/abc")
    );

    // 「今夜一緒に作る」: 定番は catalog_key で送る
    const buttons = screen.getAllByRole("button", { name: "今夜一緒に作る" });
    await user.click(buttons[1]);
    await waitFor(() => expect(posts.length).toBe(1));
    expect(posts[0].url).toContain("/api/v1/kitchen/recipes/6/companions/plan");
    expect(posts[0].body).toMatchObject({ slot: "dinner", catalog_key: "coleslaw" });
    expect((posts[0].body as { date: string }).date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it("取り込み画面は ?url= で開くと自動抽出まで進み、登録はしない", async () => {
    const posts: { url: string; body: unknown }[] = [];
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "POST") posts.push({ url, body: JSON.parse(String(init.body)) });
      if (url.endsWith("/api/v1/kitchen/recipes/import")) {
        return {
          ok: true, status: 200,
          json: async () => ({
            recipe: {
              title: "基本のコールスロー", source_url: "https://www.kurashiru.com/recipes/abc", source_site: "クラシル",
              hero_image: "", servings: 2, total_minutes: 10,
              ingredients: [{ name: "キャベツ", qty: "200", unit: "g", prep: "", group: "" }],
              tools: [], phases: [{ id: "p", title: "作る" }],
              steps: [{ index: 1, phase: "p", title: "和える", instruction: "和える。", image: null, ingredients_used: [], timer_sec: null, completion: "manual", tips: [] }],
            },
            warnings: [], method: "jsonld", meta: null,
          }),
        };
      }
      return { ok: true, status: 200, json: async () => ({ items: [], values: [] }) };
    }) as unknown as typeof fetch;

    render(
      <MemoryRouter initialEntries={["/new?url=" + encodeURIComponent("https://www.kurashiru.com/recipes/abc")]}>
        <ToastProvider>
          <RecipesRouter />
        </ToastProvider>
      </MemoryRouter>
    );
    await waitFor(() => expect(posts.some((p) => p.url.endsWith("/kitchen/recipes/import"))).toBe(true));
    const importCalls = posts.filter((p) => p.url.endsWith("/kitchen/recipes/import"));
    expect(importCalls).toHaveLength(1);
    expect(importCalls[0].body).toMatchObject({ url: "https://www.kurashiru.com/recipes/abc", mode: "auto" });
    await waitFor(() => expect(screen.getByDisplayValue("基本のコールスロー")).toBeTruthy());
    expect(posts.some((p) => /\/kitchen\/recipes$/.test(p.url))).toBe(false);
  });

  it("閉じると畳まれ、この端末に覚えておく", async () => {
    globalThis.fetch = vi.fn().mockImplementation(async () => ({
      ok: true, status: 200, json: async () => suggestion(),
    })) as unknown as typeof fetch;

    renderPanel();
    await waitFor(() => expect(screen.getByText("お供にこんなメニューはどうですか？")).toBeTruthy());
    await userEvent.setup().click(screen.getByRole("button", { name: "閉じる" }));
    expect(screen.queryByText("お供にこんなメニューはどうですか？")).toBeNull();
    expect(screen.getByRole("button", { name: "お供の提案を見る" })).toBeTruthy();
    expect(window.localStorage.getItem("manor.kitchen.companions.hidden")).toBe("1");
  });

  it("主菜でなければ（eligible: false）何も出さない", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => ({
      ok: true, status: 200, json: async () => suggestion({ eligible: false, items: [] }),
    }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    const { container } = renderPanel();
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(container.querySelector("#recipe-companions")).toBeNull();
  });
});
