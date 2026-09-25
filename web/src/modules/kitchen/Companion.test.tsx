/* manor web — レシピ詳細の「お供にこんなメニューはどうですか？」（ADR-021 D5）の画面試験。
 * Recipes.test.tsx と同じ流儀（`globalThis.fetch` を直に差し替える）。
 */
import { describe, expect, it, vi, afterEach, beforeEach } from "vitest";
import { render, cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { CompanionPanel } from "./CompanionPanel";
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
        minutes: 10, hero_image: "", score: 7.1,
        reasons: [{ code: "fills_vitamin_c_mg", params: { value: 32.3 } }],
        nutrition: { kcal: 65, protein_g: 2.3, fat_g: 4, carb_g: 7.2, salt_g: 0.8 },
        micro: { vitamin_c_mg: 32.3 },
      },
      {
        key: "catalog:coleslaw", source: "catalog", recipe_id: null, catalog_key: "coleslaw",
        title: "コールスロー", category: "副菜", kind: "生", heat: "none", tags: ["酸味"],
        minutes: 10, hero_image: "", score: 5.3,
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

  it("見出し・足りないもの・理由・出どころを出し、定番は作り方がその場で開く", async () => {
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
      if (url.endsWith("/api/v1/kitchen/companions/coleslaw")) {
        return {
          ok: true, status: 200,
          json: async () => ({
            key: "coleslaw", title: "コールスロー", servings: 2, minutes: 10, heat: "none",
            ingredients: [{ name: "キャベツ", qty: "150", unit: "g" }],
            steps: ["キャベツを千切りにする。", "調味料で和える。"],
          }),
        };
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

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "作り方" }));
    await waitFor(() => expect(screen.getByText("キャベツを千切りにする。")).toBeTruthy());

    // 「今夜一緒に作る」: 定番は catalog_key で送る
    const buttons = screen.getAllByRole("button", { name: "今夜一緒に作る" });
    await user.click(buttons[1]);
    await waitFor(() => expect(posts.length).toBe(1));
    expect(posts[0].url).toContain("/api/v1/kitchen/recipes/6/companions/plan");
    expect(posts[0].body).toMatchObject({ slot: "dinner", catalog_key: "coleslaw" });
    expect((posts[0].body as { date: string }).date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
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
