/* manor web — 「今日のおすすめ」の画面試験（ADR-018 D6）。`Media.test.tsx` と同じ流儀:
 * `globalThis.fetch` を直に差し替え、`api.ts` の実装をそのまま通す（送る要求の中身を厳密に見る）。
 *
 * ここで押さえたいのは3つ:
 *   1. 入力（人数・気分・主菜）が **クエリとして** 送られること
 *   2. 枠ごとの候補が題名・kcal と塩分・**理由の文**（符牒→文の変換。ADR-018 §4）で描かれること
 *   3. 「この献立にする」が `combo.recipe_ids` を plan へ送ること
 */
import { describe, expect, it, vi, afterEach } from "vitest";
import { render, cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { MenuPage } from "./MenuPage";
import type { MenuCandidate, MenuRecommendation } from "../../app/types";
import { ToastBanner, ToastProvider } from "../../components/Toast";

function candidate(overrides: Partial<MenuCandidate> = {}): MenuCandidate {
  return {
    recipe_id: 2,
    title: "きゅうりとわかめの酢の物",
    hero_image: "https://example.invalid/su.jpg",
    total_minutes: 10,
    category: "副菜",
    main_ingredient: "野菜",
    cuisine: "和食",
    dish_type: "生",
    score: 2.713,
    reasons: [
      { code: "keeps_salt_low", params: { value: 0.3 } },
      { code: "pantry_uses", params: { items: "きゅうり", n: 1 } },
    ],
    nutrition: { kcal: 40, protein_g: 2, fat_g: 0.5, carb_g: 6, salt_g: 0.3 },
    ...overrides,
  };
}

function recommendation(overrides: Partial<MenuRecommendation> = {}): MenuRecommendation {
  return {
    slot: "dinner",
    people: 2,
    band: {
      kcal: { min: 600, max: 900 },
      protein_g: { min: 20, max: 35 },
      fat_g: { min: 15, max: 30 },
      carb_g: { min: 75, max: 130 },
      salt_g: { min: 0, max: 2.5 },
    },
    applied_mood: { text: "", matched: [], conditions: {} },
    main: null,
    slots: {
      main: [candidate({ recipe_id: 1, title: "豚の生姜焼き", category: "主菜", reasons: [] })],
      side: [candidate()],
      soup: [candidate({ recipe_id: 4, title: "なめこの味噌汁", category: "汁物", reasons: [] })],
    },
    combo: {
      recipe_ids: [1, 2, 4],
      items: [
        { recipe_id: 1, title: "豚の生姜焼き", hero_image: "", total_minutes: 15, category: "主菜", main_ingredient: "肉", cuisine: "和食", dish_type: "焼き物", slot_kind: "main" },
        { recipe_id: 2, title: "きゅうりとわかめの酢の物", hero_image: "", total_minutes: 10, category: "副菜", main_ingredient: "野菜", cuisine: "和食", dish_type: "生", slot_kind: "side" },
        { recipe_id: 4, title: "なめこの味噌汁", hero_image: "", total_minutes: 10, category: "汁物", main_ingredient: "きのこ", cuisine: "和食", dish_type: "汁物", slot_kind: "soup" },
      ],
      total: { kcal: 530, protein_g: 29, fat_g: 23.5, carb_g: 40, salt_g: 3.2 },
      band_check: {
        kcal: { total: 530, min: 600, max: 900, status: "under" },
        protein_g: { total: 29, min: 20, max: 35, status: "in" },
        fat_g: { total: 23.5, min: 15, max: 30, status: "in" },
        carb_g: { total: 40, min: 75, max: 130, status: "under" },
        salt_g: { total: 3.2, min: 0, max: 2.5, status: "over" },
      },
    },
    excluded_no_nutrition: 1,
    viewing_user_id: "master",
    ...overrides,
  };
}

interface Call {
  url: string;
  method: string;
  body: unknown;
}

function mockFetch(payload: MenuRecommendation, extra?: (url: string, init?: RequestInit) => unknown) {
  const calls: Call[] = [];
  globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method || "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : null });
    const custom = extra?.(url, init);
    if (custom) return custom;
    if (url.includes("/kitchen/recipes")) {
      return { ok: true, status: 200, json: async () => ({ items: [{ id: 1, title: "豚の生姜焼き", hero_image: "", total_minutes: 15, servings: 2, tags: [], favorite: false, times_cooked: 0, last_cooked_at: null, kcal: 450, category: "主菜", main_ingredient: "肉", cuisine: "和食", updated_at: "2026-09-12T18:00:00" }] }) };
    }
    if (url.includes("/kitchen/menu/recommend")) {
      return { ok: true, status: 200, json: async () => payload };
    }
    return { ok: true, status: 200, json: async () => ({ date: "2026-09-13", slot: "dinner", planned: true, items: [] }) };
  }) as unknown as typeof fetch;
  return calls;
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/kitchen/menu"]}>
      <ToastProvider>
        <MenuPage />
        <ToastBanner />
      </ToastProvider>
    </MemoryRouter>
  );
}

describe("kitchen menu — 今日のおすすめ（ADR-018 D6）", () => {
  afterEach(() => cleanup());

  it("枠ごとの候補を題名・kcal と塩分・理由の文で出す", async () => {
    mockFetch(recommendation());
    renderPage();

    await waitFor(() => expect(screen.getAllByText("きゅうりとわかめの酢の物").length).toBeGreaterThan(0));
    expect(screen.getAllByText("40 kcal ／ 塩分 0.3 g").length).toBeGreaterThan(0);
    // 理由は符牒（`keeps_salt_low`）ではなく**文**で出る（ADR-018 §4）。
    expect(screen.getAllByText("塩分を抑える（0.3 g）").length).toBeGreaterThan(0);
    expect(screen.getAllByText("在庫のきゅうりを使う").length).toBeGreaterThan(0);
    // 枠の見出し3つ（主菜が未指定なので主菜の枠も並ぶ）。
    expect(screen.getByRole("heading", { name: "主菜" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "副菜" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "汁物" })).toBeTruthy();
  });

  it("候補を押すとレシピの詳細へ行くリンクになっている", async () => {
    mockFetch(recommendation());
    const { container } = renderPage();
    await waitFor(() => expect(screen.getAllByText("きゅうりとわかめの酢の物").length).toBeGreaterThan(0));
    const links = [...container.querySelectorAll("a.menu-cand")].map((a) => a.getAttribute("href"));
    expect(links).toContain("/kitchen/recipes/2");
  });

  it("帯との比較を 帯内／不足／超過 で出す", async () => {
    mockFetch(recommendation());
    const { container } = renderPage();
    await waitFor(() => expect(screen.getByText("この組み合わせ")).toBeTruthy());
    expect(container.querySelector(".menu-band-cell.band-over")).toBeTruthy();
    expect(container.querySelector(".menu-band-cell.band-under")).toBeTruthy();
    expect(container.querySelector(".menu-band-cell.band-in")).toBeTruthy();
    expect(screen.getAllByText("超過").length).toBe(1);
    // 栄養値なしで外れた件数も添える（D1）。
    expect(screen.getByText("栄養値が無くて候補から外れたレシピ: 1件")).toBeTruthy();
  });

  it("人数・気分・主菜を入れて「探す」でクエリとして送る", async () => {
    const calls = mockFetch(recommendation());
    renderPage();
    await waitFor(() => expect(screen.getAllByText("きゅうりとわかめの酢の物").length).toBeGreaterThan(0));

    const people = screen.getByLabelText("人数");
    await userEvent.clear(people);
    await userEvent.type(people, "4");
    await userEvent.click(screen.getByRole("button", { name: "さっぱり" }));
    await userEvent.selectOptions(screen.getByLabelText("主菜"), "1");
    await userEvent.click(screen.getByRole("button", { name: "探す" }));

    await waitFor(() => expect(calls.filter((c) => c.url.includes("/menu/recommend")).length).toBeGreaterThan(1));
    const last = [...calls].reverse().find((c) => c.url.includes("/menu/recommend"))!;
    expect(last.url).toContain("people=4");
    expect(last.url).toContain("main_recipe_id=1");
    expect(decodeURIComponent(last.url)).toContain("mood=さっぱり");
  });

  it("気分のチップは二度押すと外れる", async () => {
    const calls = mockFetch(recommendation());
    renderPage();
    await waitFor(() => expect(screen.getAllByText("きゅうりとわかめの酢の物").length).toBeGreaterThan(0));

    const chip = screen.getByRole("button", { name: "さっぱり" });
    await userEvent.click(chip);
    expect(chip.getAttribute("aria-pressed")).toBe("true");
    await userEvent.click(chip);
    expect(chip.getAttribute("aria-pressed")).toBe("false");

    await userEvent.click(screen.getByRole("button", { name: "探す" }));
    await waitFor(() => expect(calls.filter((c) => c.url.includes("/menu/recommend")).length).toBeGreaterThan(1));
    const last = [...calls].reverse().find((c) => c.url.includes("/menu/recommend"))!;
    expect(last.url).not.toContain("mood=");
  });

  it("「この献立にする」で combo の recipe_ids を plan へ送る", async () => {
    const calls = mockFetch(recommendation());
    renderPage();
    await waitFor(() => expect(screen.getByText("この組み合わせ")).toBeTruthy());

    await userEvent.click(screen.getByRole("button", { name: "この献立にする" }));

    await waitFor(() => expect(calls.some((c) => c.url.includes("/menu/plan"))).toBe(true));
    const post = calls.find((c) => c.url.includes("/menu/plan"))!;
    expect(post.method).toBe("POST");
    expect((post.body as { recipe_ids: number[] }).recipe_ids).toEqual([1, 2, 4]);
    expect((post.body as { slot: string }).slot).toBe("dinner");
  });

  it("主菜を指定した応答では主菜の枠を出さず「主菜（指定）」を出す", async () => {
    mockFetch(
      recommendation({
        main: {
          recipe_id: 1, title: "豚の生姜焼き", hero_image: "", total_minutes: 15, category: "主菜",
          main_ingredient: "肉", cuisine: "和食", dish_type: "焼き物", slot_kind: "main",
          nutrition: { kcal: 450, protein_g: 24, fat_g: 22, carb_g: 30, salt_g: 1.8 },
        },
        slots: { main: [], side: [candidate()], soup: [] },
      })
    );
    renderPage();
    await waitFor(() => expect(screen.getByRole("heading", { name: "主菜（指定）" })).toBeTruthy());
    expect(screen.queryByRole("heading", { name: "主菜" })).toBeNull();
    expect(screen.getByText("450 kcal ／ 塩分 1.8 g")).toBeTruthy();
  });

  it("plan の 4xx はサーバの文言をそのまま出す", async () => {
    mockFetch(recommendation(), (url, init) => {
      if (!url.includes("/menu/plan") || (init?.method || "GET") !== "POST") return null;
      return { ok: false, status: 400, json: async () => ({ detail: "献立の指定が正しくありません: recipe_ids=[]" }) };
    });
    renderPage();
    await waitFor(() => expect(screen.getByText("この組み合わせ")).toBeTruthy());

    await userEvent.click(screen.getByRole("button", { name: "この献立にする" }));
    await waitFor(() => expect(screen.getByText("献立の指定が正しくありません: recipe_ids=[]")).toBeTruthy());
  });

  it("候補が無い枠には案内を出す（画面は空にしない）", async () => {
    mockFetch(recommendation({ slots: { main: [], side: [], soup: [] }, combo: { recipe_ids: [], items: [], total: { kcal: 0, protein_g: 0, fat_g: 0, carb_g: 0, salt_g: 0 }, band_check: {} } }));
    renderPage();
    await waitFor(() => expect(screen.getAllByText("（候補がありません。レシピ帳に栄養値つきのレシピを足してください）").length).toBe(3));
    expect(screen.getByText("（候補が揃っていません）")).toBeTruthy();
    expect(screen.getByRole("button", { name: "この献立にする" }).hasAttribute("disabled")).toBe(true);
  });
});
