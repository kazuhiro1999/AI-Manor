/* manor web — レシピ帳の画面試験（ADR-015 D4・§6 追補）。他の module 試験と同じ流儀:
 * `globalThis.fetch` を直に差し替え、`api.ts` の実装（`realApi`）をそのまま通す
 * （`mock.ts` は `?mock=1` の手動確認用——試験は POST/PUT/GET の中身を厳密に検算したいので
 * ここではフェッチを直接見る。settings/Settings.test.tsx・tasks/Plan.test.tsx と同じ）。
 *
 * §6 で一覧の絞り・並びがサーバ側の契約になった（`GET /kitchen/recipes` がクエリを見て
 * `{items}` を返す）ので、ここの fetch モックも簡易フィルタを持つ——本物の
 * `chef/recipes.py` の検算そのものではなく、画面がクエリを正しく組み立て、返ってきた
 * `items` を正しく描画するかを見るためのもの。
 */
import { describe, expect, it, vi, afterEach } from "vitest";
import { render, cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { RecipesRouter } from "./RecipesRouter";
import type { Recipe, RecipeFacets, RecipeMeta } from "../../app/types";
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
    category: "",
    main_ingredient: "",
    cuisine: "",
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
    meta: baseMeta({ favorite: true, tags: ["中華"], times_cooked: 3, last_cooked_at: "2026-09-08", category: "ご飯もの", main_ingredient: "肉", cuisine: "中華" }),
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
    meta: baseMeta({ favorite: false, tags: ["揚げ物"], times_cooked: 0, last_cooked_at: null, category: "主菜", main_ingredient: "肉", cuisine: "和食" }),
  };
}

interface ListItemLike {
  id: number;
  title: string;
  hero_image: string;
  total_minutes: number | null;
  servings: number | null;
  tags: string[];
  favorite: boolean;
  times_cooked: number;
  last_cooked_at: string | null;
  kcal: number | null;
  category: string;
  main_ingredient: string;
  cuisine: string;
  updated_at: string;
}

function toListItem(r: Recipe): ListItemLike {
  return {
    id: r.id,
    title: r.title,
    hero_image: r.hero_image,
    total_minutes: r.total_minutes,
    servings: r.servings,
    tags: r.meta.tags,
    favorite: r.meta.favorite,
    times_cooked: r.meta.times_cooked,
    last_cooked_at: r.meta.last_cooked_at,
    kcal: r.meta.kcal,
    category: r.meta.category,
    main_ingredient: r.meta.main_ingredient,
    cuisine: r.meta.cuisine,
    updated_at: "2026-09-01T00:00:00.000Z",
  };
}

function countFacet(values: string[]): { value: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const v of values) {
    if (!v) continue;
    counts.set(v, (counts.get(v) || 0) + 1);
  }
  return Array.from(counts.entries()).map(([value, count]) => ({ value, count }));
}

function facetsOf(recipes: Recipe[]): RecipeFacets {
  return {
    category: countFacet(recipes.map((r) => r.meta.category)),
    main_ingredient: countFacet(recipes.map((r) => r.meta.main_ingredient)),
    cuisine: countFacet(recipes.map((r) => r.meta.cuisine)),
    tags: countFacet(recipes.flatMap((r) => r.meta.tags)),
  };
}

/** §6 D9: 一覧 API はクエリで絞る契約になった。試験用の簡易フィルタ
 * ——`chef/recipes.py` の検算そのものではなく、画面がクエリを正しく組み立て、
 * 返ってきた `items` を正しく描画するかだけを見る。 */
function filterRecipes(recipes: Recipe[], qs: string): ListItemLike[] {
  const params = new URLSearchParams(qs);
  const q = (params.get("q") || "").toLowerCase();
  const tag = params.get("tag");
  const favoriteParam = params.get("favorite");
  const category = params.get("category");
  const mainIngredient = params.get("main_ingredient");
  const cuisine = params.get("cuisine");
  return recipes
    .filter((r) => !q || r.title.toLowerCase().includes(q) || r.ingredients.some((ing) => ing.name.toLowerCase().includes(q)))
    .filter((r) => !tag || r.meta.tags.includes(tag))
    .filter((r) => favoriteParam == null || r.meta.favorite === (favoriteParam === "1" || favoriteParam === "true"))
    .filter((r) => !category || r.meta.category === category)
    .filter((r) => !mainIngredient || r.meta.main_ingredient === mainIngredient)
    .filter((r) => !cuisine || r.meta.cuisine === cuisine)
    .map(toListItem);
}

/** よく使う一覧・facets のフェッチ応答をまとめて差し込む。 */
function mockListAndFacets(recipes: Recipe[], extra?: (url: string, init?: RequestInit) => Response | null) {
  globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (extra) {
      const res = extra(url, init);
      if (res) return res;
    }
    if (url.includes("/api/v1/kitchen/recipes/facets")) {
      return { ok: true, status: 200, json: async () => facetsOf(recipes) };
    }
    const detailMatch = url.match(/\/api\/v1\/kitchen\/recipes\/(\d+)$/);
    if (detailMatch) {
      const found = recipes.find((r) => r.id === Number(detailMatch[1]));
      return { ok: true, status: 200, json: async () => found };
    }
    if (url.includes("/api/v1/kitchen/recipes")) {
      const qs = url.split("?")[1] || "";
      return { ok: true, status: 200, json: async () => ({ items: filterRecipes(recipes, qs) }) };
    }
    throw new Error("unexpected fetch: " + url);
  }) as unknown as typeof fetch;
}

describe("kitchen recipes — 一覧（ADR-015 D4・§6 D4'・D9）", () => {
  afterEach(() => {
    cleanup();
  });

  it("絞りが無いときは分類ごとの見出し＋横スクロールの列になる", async () => {
    const recipes = [chahan(), karaage()];
    mockListAndFacets(recipes);

    const { container } = render(
      <MemoryRouter initialEntries={["/"]}>
        <ToastProvider>
          <RecipesRouter />
        </ToastProvider>
      </MemoryRouter>
    );

    await waitFor(() => expect(screen.getByText("パラパラ炒飯")).toBeTruthy());
    expect(screen.getByText("鶏の唐揚げ")).toBeTruthy();

    // 分類の見出し（chahan=ご飯もの、karaage=主菜）が2つ、横スクロールの列も2つ出る。
    expect(screen.getByText("ご飯もの")).toBeTruthy();
    expect(screen.getByText("主菜")).toBeTruthy();
    expect(container.querySelectorAll(".recipe-category-scroll").length).toBe(2);
    expect(container.querySelector(".recipe-grid")).toBeNull();
  });

  it("分類の chip で絞ると一覧が1つのグリッドになり、絞った分類だけが残る", async () => {
    const recipes = [chahan(), karaage()];
    mockListAndFacets(recipes);

    const { container } = render(
      <MemoryRouter initialEntries={["/"]}>
        <ToastProvider>
          <RecipesRouter />
        </ToastProvider>
      </MemoryRouter>
    );

    await waitFor(() => expect(screen.getByText("パラパラ炒飯")).toBeTruthy());

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "主菜（1）" }));

    await waitFor(() => expect(screen.queryByText("パラパラ炒飯")).toBeNull());
    expect(screen.getByText("鶏の唐揚げ")).toBeTruthy();
    expect(container.querySelector(".recipe-grid")).toBeTruthy();
    expect(container.querySelector(".recipe-category-scroll")).toBeNull();
  });

  it("検索欄は題名だけでなく材料名にも一致し、タグの chip で絞り込める", async () => {
    const recipes = [chahan(), karaage()];
    mockListAndFacets(recipes);

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
    await user.click(screen.getByRole("button", { name: "揚げ物（1）" }));
    await waitFor(() => expect(screen.queryByText("パラパラ炒飯")).toBeNull());
    expect(screen.getByText("鶏の唐揚げ")).toBeTruthy();
  });

  it("hero_image があるカードは img の src にそのまま描かれる（無いカードは札になる）", async () => {
    const withPhoto = { ...chahan(), hero_image: "https://picsum.photos/seed/chahan-real/480/360" };
    const withoutPhoto = karaage(); // hero_image: ""
    mockListAndFacets([withPhoto, withoutPhoto]);

    const { container } = render(
      <MemoryRouter initialEntries={["/"]}>
        <ToastProvider>
          <RecipesRouter />
        </ToastProvider>
      </MemoryRouter>
    );

    await waitFor(() => expect(screen.getByText("パラパラ炒飯")).toBeTruthy());

    // `alt=""` の装飾画像はアクセシビリティツリーの img ロールから外れるので querySelector で見る。
    const photoCard = screen.getByText("パラパラ炒飯").closest(".recipe-card") as HTMLElement;
    const img = photoCard.querySelector("img") as HTMLImageElement;
    expect(img.src).toBe("https://picsum.photos/seed/chahan-real/480/360");

    const noPhotoCard = screen.getByText("鶏の唐揚げ").closest(".recipe-card") as HTMLElement;
    expect(noPhotoCard.querySelector("img")).toBeNull();
    expect(container.querySelectorAll(".recipe-card-placeholder").length).toBe(1);
  });
});

describe("kitchen recipes — 登録・取り込み（ADR-015 D2・D4・§6 D7）", () => {
  afterEach(() => {
    cleanup();
  });

  it("「取り込む」は mode:auto で呼ばれ、返った method の文言が出る", async () => {
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
          method: "adapter:nadia",
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
    // §6 D7: Nadia の形式で読み取ったことが1行で出る。
    expect(screen.getByText("Nadia の形式で読み取りました")).toBeTruthy();

    const titleInput = screen.getByLabelText("見出し") as HTMLInputElement;
    expect(titleInput.value.length).toBe(13);
    const overNote = titleInput.closest(".form-row") as HTMLElement;
    expect(within(overNote).getByText("1文字超過").className).toContain("char-count-over");

    const importCall = calls.find((c) => c.url.endsWith("/api/v1/kitchen/recipes/import"));
    expect((importCall?.init?.method || "").toUpperCase()).toBe("POST");
    const importBody = JSON.parse(String(importCall?.init?.body));
    expect(importBody.mode).toBe("auto");
  });

  it("汎用の抽出で warnings が多いとき、Claude を勧める一文が出る", async () => {
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/kitchen/recipes/import")) {
        const draft = {
          recipe: {
            title: "取り込んだレシピ（薄い）",
            source_url: "https://unknown-site.example/x",
            source_site: "unknown-site.example",
            hero_image: "",
            servings: null,
            total_minutes: null,
            ingredients: [],
            tools: [],
            phases: [{ id: "prep", title: "下ごしらえ" }],
            steps: [{ index: 1, phase: "prep", title: "見出し", instruction: "本文。", image: null, ingredients_used: [], timer_sec: null, completion: "manual", tips: [] }],
          },
          method: "generic",
          warnings: ["完成画像が見つかりません", "材料の分量を読み取れていません"],
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

    await user.type(screen.getByPlaceholderText("レシピの URL"), "https://unknown-site.example/x");
    await user.click(screen.getByRole("button", { name: "取り込む" }));

    await waitFor(() => expect(screen.getByText("汎用の解析で読み取りました（見出し・箇条書きの推定）")).toBeTruthy());
    expect(screen.getByText("Claude で整えることを勧めます")).toBeTruthy();
  });

  it("「Claude で整える」は /refine を呼び、返った下書きでフォームを置き換える", async () => {
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
            ingredients: [],
            tools: [],
            phases: [{ id: "prep", title: "下ごしらえ" }],
            steps: [
              {
                index: 1,
                phase: "prep",
                title: "調味料を合わせる長い見出し", // 13文字
                instruction: "よく混ぜる。",
                image: null,
                ingredients_used: [],
                timer_sec: null,
                completion: "manual",
                tips: [],
              },
            ],
          },
          method: "generic",
          warnings: ["工程1の見出しが13文字です（上限12文字）"],
        };
        return { ok: true, status: 200, json: async () => draft };
      }
      if (url.endsWith("/api/v1/kitchen/recipes/refine")) {
        const sent = JSON.parse(String(init?.body));
        const refined = {
          recipe: {
            ...sent.recipe,
            steps: sent.recipe.steps.map((s: { title: string }) => ({ ...s, title: s.title.slice(0, 12) })),
          },
          method: "claude",
          warnings: [],
        };
        return { ok: true, status: 200, json: async () => refined };
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
    await waitFor(() => expect((screen.getByLabelText("見出し") as HTMLInputElement).value.length).toBe(13));

    await user.click(screen.getByRole("button", { name: "Claude で整える" }));

    await waitFor(() => expect((screen.getByLabelText("見出し") as HTMLInputElement).value).toBe("調味料を合わせる長い見出".slice(0, 12)));

    const refineCall = calls.find((c) => c.url.endsWith("/api/v1/kitchen/recipes/refine"));
    expect((refineCall?.init?.method || "").toUpperCase()).toBe("POST");
    expect(screen.getByText("Claude で抽出しました")).toBeTruthy();
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
          method: "jsonld",
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
    // 完成画像の URL 欄（主人「枠自体はあるんでしょうか」への対応）——手入力でも欄に
    // 打った値がそのまま送信される（旧版は `hero_image: ""` 固定で捨てられていた）。
    await user.type(screen.getByLabelText("完成画像の URL"), "https://example.com/photo.jpg");

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
    // 下書き（フォームの中身）の URL がそのまま乗る——旧版はここが `""` 固定の不具合だった。
    expect(body.hero_image).toBe("https://example.com/photo.jpg");
  });

  it("取り込みの下書きの hero_image・栄養（うちの値）が登録にそのまま乗る（Nadia 想定）", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const draftHeroImage = "https://picsum.photos/seed/nadia-hero-real/480/360";
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method || "GET").toUpperCase();
      calls.push({ url, init });
      if (url.endsWith("/api/v1/kitchen/recipes/import") && method === "POST") {
        const draft = {
          recipe: {
            title: "取り込んだレシピ（Nadia）",
            source_url: "https://oceans-nadia.com/user/1/recipe/2",
            source_site: "oceans-nadia.com",
            hero_image: draftHeroImage,
            servings: 2,
            total_minutes: 15,
            ingredients: [{ name: "材料1", qty: "1", unit: "個", prep: "", group: "" }],
            tools: [],
            phases: [{ id: "prep", title: "下ごしらえ" }],
            steps: [
              { index: 1, phase: "prep", title: "混ぜる", instruction: "全部混ぜるだけ。", image: null, ingredients_used: [], timer_sec: null, completion: "manual", tips: [] },
            ],
          },
          method: "adapter:nadia",
          warnings: [],
          meta: { kcal: 685, protein_g: 20.5, fat_g: 35.2, carb_g: 66.5, salt_g: 2.8, nutrition_source: "site" },
        };
        return { ok: true, status: 200, json: async () => draft };
      }
      if (url.endsWith("/api/v1/kitchen/recipes") && method === "POST") {
        const created: Recipe = { ...chahan(), id: 11 };
        return { ok: true, status: 200, json: async () => created };
      }
      if (url.endsWith("/api/v1/kitchen/recipes/11/meta") && method === "PUT") {
        return { ok: true, status: 200, json: async () => chahan() };
      }
      throw new Error("unexpected fetch: " + url);
    }) as unknown as typeof fetch;

    const user = userEvent.setup();
    const { container } = render(
      <MemoryRouter initialEntries={["/new"]}>
        <ToastProvider>
          <RecipesRouter />
        </ToastProvider>
      </MemoryRouter>
    );

    await user.type(screen.getByPlaceholderText("レシピの URL"), "https://oceans-nadia.com/user/1/recipe/2");
    await user.click(screen.getByRole("button", { name: "取り込む" }));

    await waitFor(() => expect((screen.getByLabelText("題名") as HTMLInputElement).value).toBe("取り込んだレシピ（Nadia）"));
    // 完成画像の URL 欄に下書きの値がそのまま入り、その場でプレビューされる
    // （`alt=""` の装飾画像なので querySelector で見る）。
    expect((screen.getByLabelText("完成画像の URL") as HTMLInputElement).value).toBe(draftHeroImage);
    expect((container.querySelector(".hero-image-preview") as HTMLImageElement).src).toBe(draftHeroImage);
    // 登録時にも栄養5つが小さく出て、下書きの値が前もって入っている。
    expect((screen.getByLabelText("kcal") as HTMLInputElement).value).toBe("685");
    expect((screen.getByLabelText("たんぱく質(g)") as HTMLInputElement).value).toBe("20.5");
    expect(screen.getByText("栄養の出どころ: 出典の表示値")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "登録" }));

    await waitFor(() =>
      expect(calls.some((c) => c.url.endsWith("/api/v1/kitchen/recipes") && (c.init?.method || "").toUpperCase() === "POST")).toBe(true)
    );
    const postCall = calls.find((c) => c.url.endsWith("/api/v1/kitchen/recipes") && (c.init?.method || "").toUpperCase() === "POST");
    const body = JSON.parse(String(postCall?.init?.body));
    expect(body.hero_image).toBe(draftHeroImage);
    expect(body.source_site).toBe("oceans-nadia.com");

    // 登録直後に PUT .../meta で栄養（うちの値）が乗る——`nutrition_source` は "site" を
    // 明示して、`manual` へ落ちないようにする。
    await waitFor(() =>
      expect(calls.some((c) => c.url.endsWith("/api/v1/kitchen/recipes/11/meta") && (c.init?.method || "").toUpperCase() === "PUT")).toBe(
        true
      )
    );
    const metaCall = calls.find((c) => c.url.endsWith("/api/v1/kitchen/recipes/11/meta") && (c.init?.method || "").toUpperCase() === "PUT");
    const metaBody = JSON.parse(String(metaCall?.init?.body));
    expect(metaBody.kcal).toBe(685);
    expect(metaBody.protein_g).toBe(20.5);
    expect(metaBody.fat_g).toBe(35.2);
    expect(metaBody.carb_g).toBe(66.5);
    expect(metaBody.salt_g).toBe(2.8);
    expect(metaBody.nutrition_source).toBe("site");
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

    // §6 D4': 分類3軸の chip が出る（cuisine=中華 はタグにも同じ文字列があるため件数で見る）。
    expect(screen.getByText("ご飯もの")).toBeTruthy();
    expect(screen.getByText("肉")).toBeTruthy();
    expect(screen.getAllByText("中華").length).toBeGreaterThan(0);

    // tips は最初は畳まれている。
    expect(screen.queryByText("みじん切りで香りが立つ")).toBeNull();
    const stepCard = screen.getByText("しょうがを刻む。").closest(".step-card") as HTMLElement;
    await userEvent.setup().click(within(stepCard).getByRole("button", { name: "tips" }));
    expect(within(stepCard).getByText("みじん切りで香りが立つ")).toBeTruthy();
  });

  it("表示ページの頭に hero_image がそのまま描かれる", async () => {
    const recipe = { ...chahan(), hero_image: "https://picsum.photos/seed/chahan-detail-real/480/360" };
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/kitchen/recipes/1")) {
        return { ok: true, status: 200, json: async () => recipe };
      }
      throw new Error("unexpected fetch: " + url);
    }) as unknown as typeof fetch;

    const { container } = render(
      <MemoryRouter initialEntries={["/1"]}>
        <ToastProvider>
          <RecipesRouter />
        </ToastProvider>
      </MemoryRouter>
    );

    await waitFor(() => expect(screen.getAllByText("パラパラ炒飯").length).toBeGreaterThan(0));
    const hero = container.querySelector(".recipe-hero-head img") as HTMLImageElement;
    expect(hero.src).toBe("https://picsum.photos/seed/chahan-detail-real/480/360");
    expect(container.querySelector(".recipe-hero-head.no-image")).toBeNull();
  });

  // ADR-019 D5: 推定（材料から）の印・解決率・未解決の材料と「名寄せへ」。
  it("推定のレシピには『推定（材料から）』の印と解決率・未解決の材料が出る", async () => {
    const recipe = { ...chahan(), meta: baseMeta({ kcal: 520, nutrition_source: "estimated" }) };
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/kitchen/recipes/1")) {
        return { ok: true, status: 200, json: async () => recipe };
      }
      if (url.endsWith("/api/v1/kitchen/recipes/1/nutrition")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            recipe_id: 1, servings: 2,
            kcal: 520, protein_g: 20, fat_g: 18, carb_g: 65, salt_g: 2.1,
            source: "estimated", coverage: 0.62, coverage_min: 0.8, partial: true,
            unresolved: [{ name: "しょうが", normalized: "しょうが", qty: "5", unit: "g", reason: "no_food" }],
            // ADR-019 §4 追補: 材料表に「適量」としか書かれていない揚げ油の吸収。
            adjustments: [
              {
                kind: "oil_absorption", method: "唐揚げ", grams: 28, food_code: "14006",
                food_name: "調合油", kcal: 248, protein_g: 0, fat_g: 28, carb_g: 0, salt_g: 0,
              },
            ],
            food_table_available: true,
          }),
        };
      }
      throw new Error("unexpected fetch: " + url);
    }) as unknown as typeof fetch;

    const { container } = render(
      <MemoryRouter initialEntries={["/1"]}>
        <ToastProvider>
          <RecipesRouter />
        </ToastProvider>
      </MemoryRouter>
    );

    await waitFor(() => expect(container.querySelector("#recipe-nutrition-estimated")).toBeTruthy());
    expect(screen.getByText("推定（材料から）")).toBeTruthy();
    expect(screen.getByText("名寄せできた材料: 62%")).toBeTruthy();
    // 解決率が下限に届かないので、献立の候補に入らない旨が出る。
    expect(screen.getByText(/献立のおすすめには入りません/)).toBeTruthy();
    // 未解決の材料と「名寄せへ」の導線。
    const unresolved = container.querySelector("#recipe-nutrition-unresolved") as HTMLElement;
    expect(within(unresolved).getByText(/しょうが（5g）/)).toBeTruthy();
    // 揚げ油の吸収（ADR-019 §4 追補）は内訳の1行として出る。
    const adjustments = container.querySelector("#recipe-nutrition-adjustments") as HTMLElement;
    expect(within(adjustments).getByText(/唐揚げの油の吸収（推定）約 28g・\+248kcal/)).toBeTruthy();
    const link = within(unresolved).getByRole("link", { name: "名寄せへ →" }) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/settings#settings-food-aliases");
  });

  it("出典サイトの値のレシピには推定の印を出さない", async () => {
    const recipe = { ...chahan(), meta: baseMeta({ kcal: 520, nutrition_source: "site" }) };
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/kitchen/recipes/1")) {
        return { ok: true, status: 200, json: async () => recipe };
      }
      if (url.endsWith("/api/v1/kitchen/recipes/1/nutrition")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            recipe_id: 1, servings: 2,
            kcal: 520, protein_g: 20, fat_g: 18, carb_g: 65, salt_g: 2.1,
            source: "site", coverage: null, coverage_min: 0.8, partial: false,
            unresolved: [], adjustments: [], food_table_available: true,
          }),
        };
      }
      throw new Error("unexpected fetch: " + url);
    }) as unknown as typeof fetch;

    const { container } = render(
      <MemoryRouter initialEntries={["/1"]}>
        <ToastProvider>
          <RecipesRouter />
        </ToastProvider>
      </MemoryRouter>
    );

    await waitFor(() => expect(screen.getAllByText("パラパラ炒飯").length).toBeGreaterThan(0));
    expect(container.querySelector("#recipe-nutrition-estimated")).toBeNull();
    expect(container.querySelector("#recipe-nutrition-unresolved")).toBeNull();
  });

  // ADR-019 §6: 茹で湯・塩もみの塩は口に入る分だけ数えた——塩分が黙って減らないよう内訳で言う。
  it("茹で湯の塩を減らしたときは、その内訳を1行で出す", async () => {
    const recipe = { ...chahan(), meta: baseMeta({ kcal: 41, nutrition_source: "estimated" }) };
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/kitchen/recipes/1")) {
        return { ok: true, status: 200, json: async () => recipe };
      }
      if (url.endsWith("/api/v1/kitchen/recipes/1/nutrition")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            recipe_id: 1, servings: 2,
            kcal: 41, protein_g: 3, fat_g: 0.4, carb_g: 4, salt_g: 0.9,
            source: "estimated", coverage: 1, coverage_min: 0.8, partial: false,
            unresolved: [],
            adjustments: [{ kind: "salt_discard", method: "boil", grams: 5.9, kept_g: 0.1, salt_g: -5.9 }],
            food_table_available: true,
          }),
        };
      }
      throw new Error("unexpected fetch: " + url);
    }) as unknown as typeof fetch;

    const { container } = render(
      <MemoryRouter initialEntries={["/1"]}>
        <ToastProvider>
          <RecipesRouter />
        </ToastProvider>
      </MemoryRouter>
    );

    await waitFor(() => expect(container.querySelector("#recipe-nutrition-adjustments")).not.toBeNull());
    const adjustments = container.querySelector("#recipe-nutrition-adjustments") as HTMLElement;
    expect(within(adjustments).getByText(/茹で湯の塩は口に入る分だけ数えています（5.9 g は湯と一緒に捨てる・食べるのは約 0.1 g）/)).toBeTruthy();
  });

  it("材料の表は qty と unit が別々の列に来ても崩れない", async () => {
    const recipe = {
      ...chahan(),
      ingredients: [
        { name: "塩", qty: "小さじ", unit: "1/2", prep: "", group: "" },
        { name: "しょうゆ", qty: "大さじ", unit: "1", prep: "", group: "" },
      ],
    };
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/kitchen/recipes/1")) {
        return { ok: true, status: 200, json: async () => recipe };
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

    await waitFor(() => expect(screen.getByText("塩")).toBeTruthy());
    const saltRow = screen.getByText("塩").closest("tr") as HTMLElement;
    const saltCells = within(saltRow).getAllByRole("cell");
    expect(saltCells.map((c) => c.textContent)).toEqual(["塩", "小さじ", "1/2", ""]);

    const soySauceRow = screen.getByText("しょうゆ").closest("tr") as HTMLElement;
    const soySauceCells = within(soySauceRow).getAllByRole("cell");
    expect(soySauceCells.map((c) => c.textContent)).toEqual(["しょうゆ", "大さじ", "1", ""]);
  });

  it("出典サイトの表示値から取った栄養は「出典の表示値」と出る", async () => {
    const recipe = { ...chahan(), meta: { ...chahan().meta, kcal: 685, nutrition_source: "site" as const } };
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/kitchen/recipes/1")) {
        return { ok: true, status: 200, json: async () => recipe };
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

    await waitFor(() => expect(screen.getByText("栄養の出どころ: 出典の表示値")).toBeTruthy());
  });
});

describe("kitchen recipes — 編集とうちの値（ADR-015 D1・D4・§6 D9）", () => {
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

  it("分類3軸を選ぶと PUT .../meta の body に category/main_ingredient/cuisine が乗る", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const recipe = karaage(); // 分類3軸が初期値ありの見本
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method || "GET").toUpperCase();
      calls.push({ url, init });
      if (url.endsWith("/api/v1/kitchen/recipes/2") && method === "GET") {
        return { ok: true, status: 200, json: async () => recipe };
      }
      if (url.endsWith("/api/v1/kitchen/recipes/2/meta") && method === "PUT") {
        return { ok: true, status: 200, json: async () => recipe };
      }
      throw new Error("unexpected fetch: " + url);
    }) as unknown as typeof fetch;

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/2/edit"]}>
        <ToastProvider>
          <RecipesRouter />
        </ToastProvider>
      </MemoryRouter>
    );

    await waitFor(() => expect(screen.getByText("うちの値")).toBeTruthy());

    // 初期値（karaage: 主菜／肉／和食）が select に反映されている。
    expect((screen.getByLabelText("分類") as HTMLSelectElement).value).toBe("主菜");
    expect((screen.getByLabelText("主な材料") as HTMLSelectElement).value).toBe("肉");
    expect((screen.getByLabelText("ジャンル") as HTMLSelectElement).value).toBe("和食");

    await userEvent.selectOptions(screen.getByLabelText("ジャンル"), "エスニック");

    const metaPanel = screen.getByText("うちの値").closest("section") as HTMLElement;
    await user.click(within(metaPanel).getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(calls.some((c) => c.url.endsWith("/api/v1/kitchen/recipes/2/meta") && (c.init?.method || "").toUpperCase() === "PUT")).toBe(
        true
      )
    );
    const putCall = calls.find((c) => c.url.endsWith("/api/v1/kitchen/recipes/2/meta") && (c.init?.method || "").toUpperCase() === "PUT");
    const body = JSON.parse(String(putCall?.init?.body));
    expect(body.category).toBe("主菜");
    expect(body.main_ingredient).toBe("肉");
    expect(body.cuisine).toBe("エスニック");
  });

});
