/* manor web — レシピ帳の画面試験（ADR-015 D4）。他の module 試験と同じ流儀:
 * `globalThis.fetch` を直に差し替え、`api.ts` の実装（`realApi`）をそのまま通す
 * （`mock.ts` は `?mock=1` の手動確認用——試験は POST/PUT の body を厳密に検算したいので
 * ここではフェッチを直接見る。settings/Settings.test.tsx・tasks/Plan.test.tsx と同じ）。
 */
import { describe, expect, it, vi, afterEach } from "vitest";
import { render, cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { RecipesRouter } from "./RecipesRouter";
import type { Recipe, RecipeListItem, RecipeMeta } from "../../app/types";
import { ToastProvider } from "../../components/Toast";

function baseMeta(overrides: Partial<RecipeMeta> = {}): RecipeMeta {
  return {
    kcal: null,
    protein_g: null,
    fat_g: null,
    carb_g: null,
    salt_g: null,
    nutrition_source: "",
    tags: [],
    rating: null,
    memo: "",
    favorite: false,
    times_cooked: 0,
    last_cooked_at: null,
    ...overrides,
  };
}

function chahan(): Recipe {
  return {
    id: 1,
    title: "パラパラ炒飯",
    source_url: "https://example.com/chahan",
    source_site: "example.com",
    hero_image: "",
    servings: 2,
    total_minutes: 10,
    ingredients: [{ name: "しょうが", qty: "5", unit: "g", prep: "みじん切り", group: "主材料" }],
    tools: ["フライパン"],
    phases: [
      { id: "prep", title: "下ごしらえ" },
      { id: "cook", title: "炒める" },
    ],
    steps: [
      {
        index: 1,
        phase: "prep",
        title: "下ごしらえ",
        instruction: "しょうがを刻む。",
        image: null,
        ingredients_used: ["しょうが"],
        timer_sec: null,
        completion: "manual",
        tips: ["みじん切りで香りが立つ"],
      },
      {
        index: 2,
        phase: "cook",
        title: "炒める",
        instruction: "強火で香りが立つまで炒める。",
        image: null,
        ingredients_used: [],
        timer_sec: 120,
        completion: "manual",
        tips: [],
      },
    ],
    meta: baseMeta({ favorite: true, tags: ["中華"], times_cooked: 3, last_cooked_at: "2026-09-08" }),
  };
}

function karaage(): Recipe {
  return {
    id: 2,
    title: "鶏の唐揚げ",
    source_url: "",
    source_site: "",
    hero_image: "",
    servings: 3,
    total_minutes: 30,
    ingredients: [{ name: "鶏もも肉", qty: "400", unit: "g", prep: "", group: "主材料" }],
    tools: [],
    phases: [{ id: "fry", title: "揚げる" }],
    steps: [
      {
        index: 1,
        phase: "fry",
        title: "揚げる",
        instruction: "170度の油で揚げる。",
        image: null,
        ingredients_used: ["鶏もも肉"],
        timer_sec: null,
        completion: "manual",
        tips: [],
      },
    ],
    meta: baseMeta({ favorite: false, tags: ["揚げ物"], times_cooked: 0, last_cooked_at: null }),
  };
}

function toListItem(r: Recipe): RecipeListItem {
  return { id: r.id, title: r.title, hero_image: r.hero_image, total_minutes: r.total_minutes, tags: r.meta.tags, favorite: r.meta.favorite, times_cooked: r.meta.times_cooked };
}

describe("kitchen recipes — 一覧（ADR-015 D4）", () => {
  afterEach(() => {
    cleanup();
  });

  it("検索欄は題名だけでなく材料名にも一致し、タグの chip で絞り込める", async () => {
    const recipes = [chahan(), karaage()];
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/kitchen/recipes")) {
        return { ok: true, status: 200, json: async () => recipes.map(toListItem) };
      }
      const m = url.match(/\/api\/v1\/kitchen\/recipes\/(\d+)$/);
      if (m) {
        const found = recipes.find((r) => r.id === Number(m[1]));
        return { ok: true, status: 200, json: async () => found };
      }
      throw new Error("unexpected fetch: " + url);
    }) as unknown as typeof fetch;

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/"]}>
        <ToastProvider>
          <RecipesRouter />
        </ToastProvider>
      </MemoryRouter>
    );

    await waitFor(() => expect(screen.getByText("パラパラ炒飯")).toBeTruthy());
    expect(screen.getByText("鶏の唐揚げ")).toBeTruthy();

    // 材料名（題名には出てこない「しょうが」）で検索すると、炒飯だけが残る。
    const search = screen.getByPlaceholderText("題名・材料名で検索");
    await user.type(search, "しょうが");
    await waitFor(() => expect(screen.queryByText("鶏の唐揚げ")).toBeNull());
    expect(screen.getByText("パラパラ炒飯")).toBeTruthy();

    await user.clear(search);
    await waitFor(() => expect(screen.getByText("鶏の唐揚げ")).toBeTruthy());

    // タグ「揚げ物」を押すと唐揚げだけになる。
    await user.click(screen.getByRole("button", { name: "揚げ物" }));
    await waitFor(() => expect(screen.queryByText("パラパラ炒飯")).toBeNull());
    expect(screen.getByText("鶏の唐揚げ")).toBeTruthy();
  });
});

describe("kitchen recipes — 登録（ADR-015 D2・D4）", () => {
  afterEach(() => {
    cleanup();
  });

  it("「取り込む」で下書きがフォームへ流れ込み、warnings の帯と13文字見出しの残り字数（赤）が出る", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init });
      if (url.endsWith("/api/v1/kitchen/recipes/import")) {
        const draft = {
          recipe: {
            title: "取り込んだレシピ",
            source_url: "https://example.com/x",
            source_site: "example.com",
            hero_image: "",
            servings: 2,
            total_minutes: 15,
            ingredients: [{ name: "材料1", qty: "1", unit: "個", prep: "", group: "" }],
            tools: [],
            phases: [{ id: "prep", title: "下ごしらえ" }],
            steps: [
              {
                index: 1,
                phase: "prep",
                title: "調味料を合わせる長い見出し", // 13文字（上限12）
                instruction: "よく混ぜる。",
                image: null,
                ingredients_used: [],
                timer_sec: null,
                completion: "manual",
                tips: [],
              },
            ],
          },
          warnings: ["工程1の見出しが13文字です（上限12文字）"],
        };
        return { ok: true, status: 200, json: async () => draft };
      }
      throw new Error("unexpected fetch: " + url);
    }) as unknown as typeof fetch;

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/new"]}>
        <ToastProvider>
          <RecipesRouter />
        </ToastProvider>
      </MemoryRouter>
    );

    await user.type(screen.getByPlaceholderText("レシピの URL"), "https://example.com/x");
    await user.click(screen.getByRole("button", { name: "取り込む" }));

    await waitFor(() => expect((screen.getByLabelText("題名") as HTMLInputElement).value).toBe("取り込んだレシピ"));
    expect(screen.getByText("工程1の見出しが13文字です（上限12文字）")).toBeTruthy();

    const titleInput = screen.getByLabelText("見出し") as HTMLInputElement;
    expect(titleInput.value.length).toBe(13);
    const overNote = titleInput.closest(".form-row") as HTMLElement;
    expect(within(overNote).getByText("1文字超過").className).toContain("char-count-over");

    expect(calls.some((c) => c.url.endsWith("/api/v1/kitchen/recipes/import") && (c.init?.method || "").toUpperCase() === "POST")).toBe(
      true
    );
  });

  it("「手で書く」を押すと取り込んだ下書きを消して空のフォームへ戻す", async () => {
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/kitchen/recipes/import")) {
        const draft = {
          recipe: {
            title: "取り込んだレシピ",
            source_url: "",
            source_site: "",
            hero_image: "",
            servings: null,
            total_minutes: null,
            ingredients: [],
            tools: [],
            phases: [{ id: "prep", title: "下ごしらえ" }],
            steps: [{ index: 1, phase: "prep", title: "見出し", instruction: "本文。", image: null, ingredients_used: [], timer_sec: null, completion: "manual", tips: [] }],
          },
          warnings: [],
        };
        return { ok: true, status: 200, json: async () => draft };
      }
      throw new Error("unexpected fetch: " + url);
    }) as unknown as typeof fetch;

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/new"]}>
        <ToastProvider>
          <RecipesRouter />
        </ToastProvider>
      </MemoryRouter>
    );

    await user.type(screen.getByPlaceholderText("レシピの URL"), "https://example.com/x");
    await user.click(screen.getByRole("button", { name: "取り込む" }));
    await waitFor(() => expect((screen.getByLabelText("題名") as HTMLInputElement).value).toBe("取り込んだレシピ"));

    await user.click(screen.getByRole("button", { name: "手で書く（空にする）" }));
    expect((screen.getByLabelText("題名") as HTMLInputElement).value).toBe("");
  });

  it("登録すると POST /kitchen/recipes へ契約どおりの body を送る", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method || "GET").toUpperCase();
      calls.push({ url, init });
      if (url.endsWith("/api/v1/kitchen/recipes") && method === "POST") {
        const created: Recipe = { ...chahan(), id: 9 };
        return { ok: true, status: 200, json: async () => created };
      }
      throw new Error("unexpected fetch: " + url);
    }) as unknown as typeof fetch;

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/new"]}>
        <ToastProvider>
          <RecipesRouter />
        </ToastProvider>
      </MemoryRouter>
    );

    await user.type(screen.getByLabelText("題名"), "簡単な一品");
    // phase を2つとも名付ける（既定で2つ空のphaseが出ている）。
    const phaseInputs = screen.getAllByLabelText("phase の名前");
    await user.type(phaseInputs[0], "下ごしらえ");
    await user.type(phaseInputs[1], "仕上げ");
    await user.type(screen.getByLabelText("見出し"), "混ぜる");
    await user.type(screen.getByLabelText("本文"), "全部混ぜるだけ。");

    await user.click(screen.getByRole("button", { name: "登録" }));

    await waitFor(() =>
      expect(calls.some((c) => c.url.endsWith("/api/v1/kitchen/recipes") && (c.init?.method || "").toUpperCase() === "POST")).toBe(true)
    );
    const postCall = calls.find((c) => c.url.endsWith("/api/v1/kitchen/recipes") && (c.init?.method || "").toUpperCase() === "POST");
    const body = JSON.parse(String(postCall?.init?.body));
    expect(body.title).toBe("簡単な一品");
    expect(body.phases).toEqual([
      { id: expect.any(String), title: "下ごしらえ" },
      { id: expect.any(String), title: "仕上げ" },
    ]);
    expect(body.steps).toEqual([
      {
        index: 1,
        phase: body.phases[0].id,
        title: "混ぜる",
        instruction: "全部混ぜるだけ。",
        image: null,
        ingredients_used: [],
        timer_sec: null,
        completion: "manual",
        tips: [],
      },
    ]);
    expect(body.ingredients).toEqual([]);
    expect(body.tools).toEqual([]);
    expect(body.hero_image).toBe("");
  });
});

describe("kitchen recipes — 表示（ADR-015 D4）", () => {
  afterEach(() => {
    cleanup();
  });

  it("工程をカードで縦に並べ、tips は折りたたみで開ける", async () => {
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/kitchen/recipes/1")) {
        return { ok: true, status: 200, json: async () => chahan() };
      }
      throw new Error("unexpected fetch: " + url);
    }) as unknown as typeof fetch;

    render(
      <MemoryRouter initialEntries={["/1"]}>
        <ToastProvider>
          <RecipesRouter />
        </ToastProvider>
      </MemoryRouter>
    );

    await waitFor(() => expect(screen.getAllByText("パラパラ炒飯").length).toBeGreaterThan(0));
    expect(screen.getByText("しょうがを刻む。")).toBeTruthy();
    expect(screen.getByText("強火で香りが立つまで炒める。")).toBeTruthy();

    // tips は最初は畳まれている。
    expect(screen.queryByText("みじん切りで香りが立つ")).toBeNull();
    const stepCard = screen.getByText("しょうがを刻む。").closest(".step-card") as HTMLElement;
    await userEvent.setup().click(within(stepCard).getByRole("button", { name: "tips" }));
    expect(within(stepCard).getByText("みじん切りで香りが立つ")).toBeTruthy();
  });
});

describe("kitchen recipes — 編集とうちの値（ADR-015 D1・D4）", () => {
  afterEach(() => {
    cleanup();
  });

  it("触った栄養欄だけを PUT .../meta の body に含める（触っていない欄で nutrition_source を manual に落とさない）", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const recipe = chahan();
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method || "GET").toUpperCase();
      calls.push({ url, init });
      if (url.endsWith("/api/v1/kitchen/recipes/1") && method === "GET") {
        return { ok: true, status: 200, json: async () => recipe };
      }
      if (url.endsWith("/api/v1/kitchen/recipes/1/meta") && method === "PUT") {
        return { ok: true, status: 200, json: async () => recipe };
      }
      throw new Error("unexpected fetch: " + url);
    }) as unknown as typeof fetch;

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/1/edit"]}>
        <ToastProvider>
          <RecipesRouter />
        </ToastProvider>
      </MemoryRouter>
    );

    await waitFor(() => expect(screen.getByText("うちの値")).toBeTruthy());

    const kcalInput = screen.getByLabelText("kcal") as HTMLInputElement;
    await user.clear(kcalInput);
    await user.type(kcalInput, "700");

    await user.type(screen.getByLabelText("評価（1〜5）"), "4");
    await user.type(screen.getByPlaceholderText("タグを追加"), "定番");
    await user.click(screen.getByRole("button", { name: "追加" }));

    const metaPanel = screen.getByText("うちの値").closest("section") as HTMLElement;
    await user.click(within(metaPanel).getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(calls.some((c) => c.url.endsWith("/api/v1/kitchen/recipes/1/meta") && (c.init?.method || "").toUpperCase() === "PUT")).toBe(
        true
      )
    );
    const putCall = calls.find((c) => c.url.endsWith("/api/v1/kitchen/recipes/1/meta") && (c.init?.method || "").toUpperCase() === "PUT");
    const body = JSON.parse(String(putCall?.init?.body));
    expect(body.kcal).toBe(700);
    expect(body.protein_g).toBeUndefined();
    expect(body.fat_g).toBeUndefined();
    expect(body.carb_g).toBeUndefined();
    expect(body.salt_g).toBeUndefined();
    expect(body.tags).toEqual(["中華", "定番"]);
    expect(body.rating).toBe(4);
    expect(body.favorite).toBe(true);
  });

  it("「栄養を推定」を押すと POST .../estimate-nutrition の返り値で栄養欄が埋まる", async () => {
    const recipe = chahan();
    const estimated: RecipeMeta = { ...recipe.meta, kcal: 600, protein_g: 25, fat_g: 20, carb_g: 70, salt_g: 2.5, nutrition_source: "estimated" };
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method || "GET").toUpperCase();
      if (url.endsWith("/api/v1/kitchen/recipes/1") && method === "GET") {
        return { ok: true, status: 200, json: async () => recipe };
      }
      if (url.endsWith("/api/v1/kitchen/recipes/1/estimate-nutrition") && method === "POST") {
        return { ok: true, status: 200, json: async () => ({ meta: estimated }) };
      }
      throw new Error("unexpected fetch: " + url);
    }) as unknown as typeof fetch;

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/1/edit"]}>
        <ToastProvider>
          <RecipesRouter />
        </ToastProvider>
      </MemoryRouter>
    );

    await waitFor(() => expect(screen.getByText("うちの値")).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "栄養を推定" }));

    await waitFor(() => expect((screen.getByLabelText("kcal") as HTMLInputElement).value).toBe("600"));
    expect((screen.getByLabelText("たんぱく質(g)") as HTMLInputElement).value).toBe("25");
    expect(screen.getByText("栄養の出どころ: 推定")).toBeTruthy();
  });
});
