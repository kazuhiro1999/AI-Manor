import { describe, expect, it, vi, afterEach } from "vitest";
import { render, cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { settingsModule } from "./index";
import type { FaceModelEntry, Meta, RunsData, RunStatsData, SettingsData, SetupInfo, TaskKind } from "../../app/types";
import { ToastBanner, ToastProvider } from "../../components/Toast";

const SettingsScreen = settingsModule.routes[0].element as JSX.Element;

function baseMeta(): Meta {
  return {
    version: "test-0.0.1",
    today: "2026-09-03",
    read_only: false,
    stale: false,
    auth: { mode: "loopback", authenticated: true },
    modules: [{ id: "tasks", title: "タスク", icon: "T", order: 1, enabled: true }],
    home_name: "test-home",
  };
}

function baseSettings(): SettingsData {
  return {
    notify: { quiet_from: 22, quiet_to: 7, has_speak_command: false },
    web: { has_passcode: false, require_passcode: false, is_loopback: true, host: "127.0.0.1" },
    manor: { language: "auto" },
    modules: [{ id: "tasks", title: "タスク", icon: "T", order: 1, enabled: true }],
  };
}

function baseSetupInfo(): SetupInfo {
  return {
    done: true,
    completed_at: "2026-09-01T00:00:00",
    profile: { "master.callname": "旦那様", "butler.callname": "執事", purposes: JSON.stringify(["tasks"]), "purposes.note": "" },
    purposes: [
      { id: "tasks", label: "タスク・プロジェクトの管理" },
      { id: "kitchen", label: "料理・買い物" },
    ],
    presets: [
      { id: "careful", label: "🐢慎重" },
      { id: "standard", label: "🚶標準" },
      { id: "fast", label: "🏃高速" },
    ],
    task_classes: [{ id: "research", label: "情報収集・調査", default_level: "L3", fixed: false }],
    money_apps: [{ id: "none", label: "使っていない" }],
  };
}

// ADR-008 §7 D14: butler は未設置、chef だけ姿が置かれている見本(削除ボタンの有無を試せるように)。
function baseFaceModels(): FaceModelEntry[] {
  return [
    { agent: "butler", label: "執事", has_model: false, size: null, updated_at: null, legacy: false },
    { agent: "chef", label: "料理長", has_model: true, size: 245000, updated_at: "2026-09-01T00:00:00", legacy: false },
    { agent: "housekeeper", label: "家政婦", has_model: false, size: null, updated_at: null, legacy: false },
    { agent: "steward", label: "家令", has_model: false, size: null, updated_at: null, legacy: false },
    { agent: "secretary", label: "秘書", has_model: false, size: null, updated_at: null, legacy: false },
    { agent: "qa", label: "検分", has_model: false, size: null, updated_at: null, legacy: false },
    { agent: "auditor", label: "監査", has_model: false, size: null, updated_at: null, legacy: false },
  ];
}

// ADR-010 D2 後半:「タスクの種類」の見本。`other` を含めて改名・アーカイブ操作の有無を試せる。
function baseTaskKinds(): TaskKind[] {
  return [
    { id: "research", label: "調査・情報収集", sort: 1, archived_at: null },
    { id: "design", label: "検討・設計", sort: 2, archived_at: null },
    { id: "other", label: "その他", sort: 8, archived_at: null },
  ];
}

function mockFetchFor(handlers: {
  runsStats: RunStatsData;
  runs: RunsData;
  faceModels?: FaceModelEntry[];
  taskKinds?: TaskKind[];
  faceOpen?: { opened: boolean; method?: string; reason?: string };
  onFetch?: (url: string, init?: RequestInit) => void;
}) {
  const faceModels = handlers.faceModels ?? baseFaceModels();
  const taskKinds = handlers.taskKinds ?? baseTaskKinds();
  // 既定は opened: false（サーバ側でアプリモードの窓を作れない体）——「小窓を開く」を
  // 何もオプション無しで呼ぶ既存試験がそのままポップアップへのフォールバックを試せるように。
  const faceOpen = handlers.faceOpen ?? { opened: false, method: "none", reason: "テスト既定: Chrome が見つからない体" };
  globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method || "GET").toUpperCase();
    handlers.onFetch?.(url, init);

    // D5: サーバ側でヘッダ無しの窓を開く経路（本命）。
    if (url.includes("/face/open") && method === "POST") {
      const json = async () => ({ opened: faceOpen.opened, method: faceOpen.method ?? (faceOpen.opened ? "app" : "none"), reason: faceOpen.reason ?? "" });
      return { ok: true, status: 200, json };
    }

    // /face/model（単数）は multipart の POST（アップロード）と DELETE（削除）を持つ。
    // /face/models（複数）の一覧 GET とは別経路なので、先に区別する。
    if (url.includes("/face/model") && !url.includes("/face/models")) {
      if (method === "POST") {
        const form = init?.body as FormData;
        const agent = String(form.get("agent") || "");
        const entry = faceModels.find((m) => m.agent === agent);
        const json = async () => ({
          agent,
          label: entry?.label || agent,
          has_model: true,
          size: 12345,
          updated_at: "2026-09-04T00:00:00",
          legacy: false,
        });
        return { ok: true, status: 200, json };
      }
      if (method === "DELETE") {
        const json = async () => ({ agent: "chef", label: "料理長", has_model: false, size: null, updated_at: null, legacy: false });
        return { ok: true, status: 200, json };
      }
    }

    // /task-kinds: POST（追加）・PUT /{id}（改名）は body の内容を映して返す。
    if (url.includes("/task-kinds")) {
      if (method === "POST") {
        const body = init?.body ? JSON.parse(String(init.body)) : {};
        const json = async () => ({ id: body.id, label: body.label, sort: 9, archived_at: null });
        return { ok: true, status: 200, json };
      }
      if (method === "PUT") {
        const idMatch = url.match(/\/task-kinds\/([^/?]+)/);
        const body = init?.body ? JSON.parse(String(init.body)) : {};
        const json = async () => ({
          id: idMatch ? decodeURIComponent(idMatch[1]) : "",
          label: body.label,
          sort: 1,
          archived_at: null,
        });
        return { ok: true, status: 200, json };
      }
      if (method === "DELETE") {
        const idMatch = url.match(/\/task-kinds\/([^/?]+)/);
        const found = taskKinds.find((k) => k.id === (idMatch ? decodeURIComponent(idMatch[1]) : ""));
        const json = async () => ({ ...(found || taskKinds[0]), archived_at: "2026-09-04T00:00:00" });
        return { ok: true, status: 200, json };
      }
    }

    const json = async () => {
      if (url.includes("/runs/stats")) return handlers.runsStats;
      if (url.includes("/runs")) return handlers.runs;
      if (url.includes("/setup")) return baseSetupInfo();
      if (url.includes("/settings")) return baseSettings();
      if (url.includes("/face/models")) return faceModels;
      if (url.includes("/task-kinds")) return taskKinds;
      if (url.includes("/meta")) return baseMeta();
      throw new Error("unexpected fetch: " + url);
    };
    return { ok: true, status: 200, json };
  }) as unknown as typeof fetch;
}

describe("settings — 稼働と費用（ADR-006 §3 D11・§6）", () => {
  afterEach(() => {
    cleanup();
  });

  it("run 表がある home では kind 別の表と直近の実行を描画する", async () => {
    mockFetchFor({
      runsStats: {
        available: true,
        by_kind: [
          { kind: "behavior", count: 2, cost_usd: 0.32, avg_seconds: 90, failed: 1, input_tokens: 2500, output_tokens: 300 },
          { kind: "night", count: 1, cost_usd: 0.55, avg_seconds: 600, failed: 0, input_tokens: 5000, output_tokens: 800 },
        ],
        total_cost_usd: 0.87,
      },
      runs: {
        available: true,
        runs: [
          {
            id: 1,
            kind: "behavior",
            ref: "S6",
            started_at: "2026-09-01T00:00:00",
            ended_at: "2026-09-01T00:01:40",
            model: "claude-sonnet",
            cost_usd: 0.12,
            turns: 6,
            exit_reason: "done",
            note: "",
          },
        ],
      },
    });

    render(<MemoryRouter><ToastProvider>{SettingsScreen}</ToastProvider></MemoryRouter>);

    await waitFor(() => expect(screen.getByText("稼働と費用")).toBeTruthy());
    // 「振る舞い試験」「夜勤」は kind 別の表・直近の実行の両方に出るので getAllByText で見る。
    await waitFor(() => expect(screen.getAllByText("振る舞い試験").length).toBeGreaterThanOrEqual(1));
    expect(screen.getAllByText("夜勤").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("$0.87")).toBeTruthy(); // 合計
    expect(screen.getByText("S6")).toBeTruthy(); // 直近の実行の ref
    expect(screen.queryByText(/まだ記録がありません/)).toBeNull();
  });

  it("run 表が無い home では「まだ記録がありません」の案内を出す", async () => {
    mockFetchFor({
      runsStats: { available: false, by_kind: [], total_cost_usd: 0 },
      runs: { available: false, runs: [] },
    });

    render(<MemoryRouter><ToastProvider>{SettingsScreen}</ToastProvider></MemoryRouter>);

    await waitFor(() => expect(screen.getByText("稼働と費用")).toBeTruthy());
    await waitFor(() =>
      expect(screen.getByText(/まだ記録がありません（夜勤か振る舞い試験を回すと溜まります）/)).toBeTruthy()
    );
    expect(screen.queryByText("振る舞い試験")).toBeNull();
  });
});

describe("settings — 姿（小窓）（ADR-008 §7 D14・D15）", () => {
  afterEach(() => {
    cleanup();
  });

  const emptyRuns = { runsStats: { available: false, by_kind: [], total_cost_usd: 0 }, runs: { available: false, runs: [] } };

  it("担当ごとの行を描画する（butler / chef / housekeeper / steward / secretary / qa / auditor）", async () => {
    mockFetchFor({ ...emptyRuns, faceModels: baseFaceModels() });

    render(<MemoryRouter><ToastProvider>{SettingsScreen}</ToastProvider></MemoryRouter>);

    await waitFor(() => expect(screen.getByText("姿（小窓）")).toBeTruthy());
    for (const label of ["執事", "料理長", "家政婦", "家令", "秘書", "検分", "監査"]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
  });

  it("VRM を選んで送ると /face/model へ multipart で POST する（agent・file を含む）", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    mockFetchFor({ ...emptyRuns, faceModels: baseFaceModels(), onFetch: (url, init) => calls.push({ url, init }) });

    const user = userEvent.setup();
    render(<MemoryRouter><ToastProvider>{SettingsScreen}</ToastProvider></MemoryRouter>);
    await waitFor(() => expect(screen.getByText("姿（小窓）")).toBeTruthy());

    const input = screen.getByLabelText("料理長の VRM ファイル") as HTMLInputElement;
    const file = new File([new Uint8Array([0x67, 0x6c, 0x54, 0x46, 0, 0, 0, 0])], "chef.vrm", { type: "model/gltf-binary" });
    await user.upload(input, file);

    const row = input.closest(".face-model-row") as HTMLElement;
    const button = within(row).getByRole("button", { name: /差し替える|設定する/ });
    await user.click(button);

    await waitFor(() =>
      expect(calls.some((c) => c.url.includes("/api/v1/face/model") && (c.init?.method || "").toUpperCase() === "POST")).toBe(true)
    );
    const uploadCall = calls.find((c) => c.url.includes("/api/v1/face/model") && (c.init?.method || "").toUpperCase() === "POST");
    expect(uploadCall).toBeTruthy();
    const form = uploadCall?.init?.body as FormData;
    expect(form.get("agent")).toBe("chef");
    expect((form.get("file") as File).name).toBe("chef.vrm");
  });

  it("「小窓を開く」はまず POST /face/open を試す。opened: true ならポップアップは開かない（本命はサーバ側のアプリモード）", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    mockFetchFor({
      ...emptyRuns,
      faceModels: baseFaceModels(),
      faceOpen: { opened: true, method: "app", reason: "" },
      onFetch: (url, init) => calls.push({ url, init }),
    });

    const openSpy = vi.fn().mockReturnValue({ focus: vi.fn() });
    const originalOpen = window.open;
    window.open = openSpy as unknown as typeof window.open;

    try {
      const user = userEvent.setup();
      render(<MemoryRouter><ToastProvider>{SettingsScreen}</ToastProvider></MemoryRouter>);
      await waitFor(() => expect(screen.getByText("姿（小窓）")).toBeTruthy());

      const chefRow = (screen.getByLabelText("料理長の VRM ファイル") as HTMLElement).closest(".face-model-row") as HTMLElement;
      await user.click(within(chefRow).getByRole("button", { name: "小窓を開く" }));

      await waitFor(() =>
        expect(calls.some((c) => c.url.includes("/api/v1/face/open") && (c.init?.method || "").toUpperCase() === "POST")).toBe(true)
      );
      const openCall = calls.find((c) => c.url.includes("/api/v1/face/open"));
      expect(JSON.parse(String(openCall?.init?.body))).toEqual({ agent: "chef" });

      // opened: true のとき、サーバ側で既に窓が開いているので window.open は一切呼ばない。
      expect(openSpy).not.toHaveBeenCalled();
    } finally {
      window.open = originalOpen;
    }
  });

  it("POST /face/open が opened: false を返したら、理由をトーストで伝えつつポップアップへフォールバックする（popup 引数・固定名、ADR-008 D16）", async () => {
    mockFetchFor({
      ...emptyRuns,
      faceModels: baseFaceModels(),
      faceOpen: { opened: false, method: "none", reason: "Chrome が見つかりません" },
    });

    const openSpy = vi.fn().mockReturnValue({ focus: vi.fn() });
    const originalOpen = window.open;
    window.open = openSpy as unknown as typeof window.open;

    try {
      const user = userEvent.setup();
      render(
        <MemoryRouter>
          <ToastProvider>
            <ToastBanner />
            {SettingsScreen}
          </ToastProvider>
        </MemoryRouter>
      );
      await waitFor(() => expect(screen.getByText("姿（小窓）")).toBeTruthy());

      const chefRow = (screen.getByLabelText("料理長の VRM ファイル") as HTMLElement).closest(".face-model-row") as HTMLElement;
      await user.click(within(chefRow).getByRole("button", { name: "小窓を開く" }));

      await waitFor(() => expect(openSpy).toHaveBeenCalledTimes(1));
      const [url, name, features] = openSpy.mock.calls[0];
      expect(url).toBe("/face?agent=chef");
      expect(name).toBe("manor-face-chef"); // 固定名。二度押しても同じ窓を使い回す
      expect(String(features)).toMatch(/popup=yes/);
      expect(String(features)).toMatch(/width=280/);
      expect(String(features)).toMatch(/height=340/);
      // 右下へ寄せる（D16）: left/top が現在の availWidth/availHeight から出ている
      expect(String(features)).toMatch(/left=\d+/);
      expect(String(features)).toMatch(/top=\d+/);

      // 開けなかった理由がトーストで伝わる（黙って諦めない）。
      await waitFor(() => expect(screen.getByText(/Chrome が見つかりません/)).toBeTruthy());
    } finally {
      window.open = originalOpen;
    }
  });

  it("popup もブロックされたときはタブへ次善で開く（黙って諦めない）", async () => {
    mockFetchFor({ ...emptyRuns, faceModels: baseFaceModels(), faceOpen: { opened: false, method: "none", reason: "テスト" } });

    // 1回目（popup 狙い）は null（ブロックされた）を返す。
    const openSpy = vi.fn().mockReturnValue(null);
    const originalOpen = window.open;
    window.open = openSpy as unknown as typeof window.open;

    try {
      const user = userEvent.setup();
      render(<MemoryRouter><ToastProvider>{SettingsScreen}</ToastProvider></MemoryRouter>);
      await waitFor(() => expect(screen.getByText("姿（小窓）")).toBeTruthy());

      const chefRow = (screen.getByLabelText("料理長の VRM ファイル") as HTMLElement).closest(".face-model-row") as HTMLElement;
      await user.click(within(chefRow).getByRole("button", { name: "小窓を開く" }));

      await waitFor(() => expect(openSpy).toHaveBeenCalledTimes(2));
      // 2回目はタブへのフォールバック。
      expect(openSpy.mock.calls[1][1]).toBe("_blank");
    } finally {
      window.open = originalOpen;
    }
  });

  it("削除ボタンは姿があるときだけ出る", async () => {
    mockFetchFor({ ...emptyRuns, faceModels: baseFaceModels() }); // chef だけ has_model: true

    render(<MemoryRouter><ToastProvider>{SettingsScreen}</ToastProvider></MemoryRouter>);
    await waitFor(() => expect(screen.getByText("姿（小窓）")).toBeTruthy());

    const chefRow = (screen.getByLabelText("料理長の VRM ファイル") as HTMLElement).closest(".face-model-row") as HTMLElement;
    expect(within(chefRow).getByRole("button", { name: "削除" })).toBeTruthy();

    const butlerRow = (screen.getByLabelText("執事の VRM ファイル") as HTMLElement).closest(".face-model-row") as HTMLElement;
    expect(within(butlerRow).queryByRole("button", { name: "削除" })).toBeNull();
  });
});

describe("settings — 配色（起動時の反映バグの回帰）", () => {
  afterEach(() => {
    cleanup();
    localStorage.removeItem("manor-web.theme"); // theme.ts の THEME_KEY と同じ文字列（非公開のため直書き）
    document.documentElement.removeAttribute("data-theme");
  });

  const emptyRuns = { runsStats: { available: false, by_kind: [], total_cost_usd: 0 }, runs: { available: false, runs: [] } };

  it("「暗い」を選ぶと、保存を押さなくても document.documentElement へ即反映する", async () => {
    mockFetchFor({ ...emptyRuns });

    const user = userEvent.setup();
    render(<MemoryRouter><ToastProvider>{SettingsScreen}</ToastProvider></MemoryRouter>);
    await waitFor(() => expect(screen.getByText("配色")).toBeTruthy());

    await user.click(screen.getByRole("button", { name: "暗い" }));

    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(screen.getByRole("button", { name: "暗い" }).getAttribute("aria-pressed")).toBe("true");

    // 「明るい」へ選び直すと即座に切り替わる。
    await user.click(screen.getByRole("button", { name: "明るい" }));
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });
});

describe("settings — タスクの種類（ADR-010 D2 後半）", () => {
  afterEach(() => {
    cleanup();
  });

  const emptyRuns = { runsStats: { available: false, by_kind: [], total_cost_usd: 0 }, runs: { available: false, runs: [] } };

  it("sort 順に一覧を描画する（id は固定の副次テキストとして出る）", async () => {
    mockFetchFor({ ...emptyRuns });

    render(<MemoryRouter><ToastProvider>{SettingsScreen}</ToastProvider></MemoryRouter>);

    await waitFor(() => expect(screen.getByText("タスクの種類")).toBeTruthy());
    expect(screen.getByText("調査・情報収集")).toBeTruthy();
    expect(screen.getByText("検討・設計")).toBeTruthy();
    expect(screen.getByText("research")).toBeTruthy(); // id が副次テキストとして出る

    // sort 順（research=1, design=2, other=8）で並んでいること。
    const titles = screen.getAllByText(/調査・情報収集|検討・設計|その他/).map((el) => el.textContent);
    expect(titles).toEqual(["調査・情報収集", "検討・設計", "その他"]);
  });

  it("追加すると POST /task-kinds へ {id, label} を送る", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    mockFetchFor({ ...emptyRuns, onFetch: (url, init) => calls.push({ url, init }) });

    const user = userEvent.setup();
    render(<MemoryRouter><ToastProvider>{SettingsScreen}</ToastProvider></MemoryRouter>);
    await waitFor(() => expect(screen.getByText("タスクの種類")).toBeTruthy());

    const section = screen.getByText("タスクの種類").closest("section") as HTMLElement;
    await user.click(within(section).getByRole("button", { name: "+ 追加" }));

    await user.type(screen.getByLabelText("新しいタスクの種類の id"), "contact");
    await user.type(screen.getByLabelText("新しいタスクの種類の表示名"), "連絡・調整");
    await user.click(within(section).getByRole("button", { name: "追加" }));

    await waitFor(() =>
      expect(calls.some((c) => c.url.includes("/api/v1/task-kinds") && (c.init?.method || "").toUpperCase() === "POST")).toBe(true)
    );
    const postCall = calls.find((c) => c.url.includes("/api/v1/task-kinds") && (c.init?.method || "").toUpperCase() === "POST");
    expect(JSON.parse(String(postCall?.init?.body))).toEqual({ id: "contact", label: "連絡・調整" });
  });

  it("改名すると PUT /task-kinds/{id} へ {label} を送る", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    mockFetchFor({ ...emptyRuns, onFetch: (url, init) => calls.push({ url, init }) });

    const user = userEvent.setup();
    render(<MemoryRouter><ToastProvider>{SettingsScreen}</ToastProvider></MemoryRouter>);
    await waitFor(() => expect(screen.getByText("タスクの種類")).toBeTruthy());

    const row = screen.getByText("調査・情報収集").closest(".row-item") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "改名" }));

    const input = within(row).getByLabelText("research の表示名") as HTMLInputElement;
    await user.clear(input);
    await user.type(input, "調査");
    await user.click(within(row).getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(calls.some((c) => c.url.includes("/api/v1/task-kinds/research") && (c.init?.method || "").toUpperCase() === "PUT")).toBe(
        true
      )
    );
    const putCall = calls.find((c) => c.url.includes("/api/v1/task-kinds/research") && (c.init?.method || "").toUpperCase() === "PUT");
    expect(JSON.parse(String(putCall?.init?.body))).toEqual({ label: "調査" });
  });

  it("other には改名・アーカイブの操作を出さない", async () => {
    mockFetchFor({ ...emptyRuns });

    render(<MemoryRouter><ToastProvider>{SettingsScreen}</ToastProvider></MemoryRouter>);
    await waitFor(() => expect(screen.getByText("タスクの種類")).toBeTruthy());

    const otherRow = screen.getByText("その他").closest(".row-item") as HTMLElement;
    expect(within(otherRow).queryByRole("button", { name: "改名" })).toBeNull();
    expect(within(otherRow).queryByRole("button", { name: "アーカイブ" })).toBeNull();
    expect(within(otherRow).getByText("分類できないものの受け皿なので消せません")).toBeTruthy();

    // 対照として、他の種類には両方の操作があること。
    const researchRow = screen.getByText("調査・情報収集").closest(".row-item") as HTMLElement;
    expect(within(researchRow).getByRole("button", { name: "改名" })).toBeTruthy();
    expect(within(researchRow).getByRole("button", { name: "アーカイブ" })).toBeTruthy();
  });

  it("不正な id は送信前に弾く（fetch を一切呼ばない）", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    mockFetchFor({ ...emptyRuns, onFetch: (url, init) => calls.push({ url, init }) });

    const user = userEvent.setup();
    render(<MemoryRouter><ToastProvider>{SettingsScreen}</ToastProvider></MemoryRouter>);
    await waitFor(() => expect(screen.getByText("タスクの種類")).toBeTruthy());

    const section = screen.getByText("タスクの種類").closest("section") as HTMLElement;
    await user.click(within(section).getByRole("button", { name: "+ 追加" }));

    // 先頭が大文字・記号混じりなど、`^[a-z][a-z0-9_]*$` に反する id。
    await user.type(screen.getByLabelText("新しいタスクの種類の id"), "Contact!");
    await user.type(screen.getByLabelText("新しいタスクの種類の表示名"), "連絡・調整");
    const callsBefore = calls.length;
    await user.click(within(section).getByRole("button", { name: "追加" }));

    expect(screen.getByText(/id は半角小文字で始め/)).toBeTruthy();
    expect(calls.some((c) => c.url.includes("/api/v1/task-kinds") && (c.init?.method || "").toUpperCase() === "POST")).toBe(false);
    expect(calls.length).toBe(callsBefore); // POST どころか一切 fetch していない
  });
});

describe("settings — 言語（ADR-012 §3 D11）", () => {
  afterEach(() => {
    cleanup();
    localStorage.removeItem("manor-web.language");
  });

  const emptyRuns = { runsStats: { available: false, by_kind: [], total_cost_usd: 0 }, runs: { available: false, runs: [] } };

  it("English を選ぶと、通信の完了を待たずにその場で画面が英語へ切り替わる（2026-09-05 の Light/Dark 不具合の再発防止）", async () => {
    // PUT /settings をわざと解決しない Promise にする——「保存の完了を待ってから表示を
    // 切り替える」実装だったら、この試験は永遠に「配色」のままでタイムアウトして落ちる。
    let resolvePut: ((v: unknown) => void) | undefined;
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method || "GET").toUpperCase();
      if (url.includes("/settings") && method === "PUT") {
        return new Promise((resolve) => {
          resolvePut = resolve;
        });
      }
      const json = async () => {
        if (url.includes("/runs/stats")) return emptyRuns.runsStats;
        if (url.includes("/runs")) return emptyRuns.runs;
        if (url.includes("/setup")) return baseSetupInfo();
        if (url.includes("/settings")) return baseSettings();
        if (url.includes("/face/models")) return baseFaceModels();
        if (url.includes("/task-kinds")) return baseTaskKinds();
        if (url.includes("/meta")) return baseMeta();
        throw new Error("unexpected fetch: " + url);
      };
      return { ok: true, status: 200, json };
    }) as unknown as typeof fetch;

    const user = userEvent.setup();
    render(<MemoryRouter><ToastProvider>{SettingsScreen}</ToastProvider></MemoryRouter>);
    await waitFor(() => expect(screen.getByText("配色")).toBeTruthy());

    await user.click(screen.getByRole("button", { name: "English" }));

    // PUT がまだ未解決（resolvePut が呼ばれていない）のに、見出しはもう英語になっている。
    expect(screen.getByText("Color scheme")).toBeTruthy();
    expect(screen.queryByText("配色")).toBeNull();

    resolvePut?.({ ok: true, status: 200, json: async () => baseSettings() });
  });
});

describe("settings — require_passcode トグル（ADR-013 D2:「締め出しを防ぐ」を画面側でも塞ぐ）", () => {
  afterEach(() => {
    cleanup();
  });

  const emptyRuns = { runsStats: { available: false, by_kind: [], total_cost_usd: 0 }, runs: { available: false, runs: [] } };

  /** `mockFetchFor` は `/settings` を常に `baseSettings()` 固定で返すので、ここでは
   * `web` の値だけを差し替えられる専用の fetch モックを使う。 */
  function mockFetchWithWeb(web: { has_passcode: boolean; require_passcode: boolean; is_loopback: boolean }) {
    const calls: { url: string; init?: RequestInit }[] = [];
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method || "GET").toUpperCase();
      calls.push({ url, init });
      if (url.includes("/settings") && method === "PUT") {
        const json = async () => ({ ...baseSettings(), web: { ...web, host: "127.0.0.1" } });
        return { ok: true, status: 200, json };
      }
      const json = async () => {
        if (url.includes("/runs/stats")) return emptyRuns.runsStats;
        if (url.includes("/runs")) return emptyRuns.runs;
        if (url.includes("/setup")) return baseSetupInfo();
        if (url.includes("/settings")) return { ...baseSettings(), web: { ...web, host: "127.0.0.1" } };
        if (url.includes("/face/models")) return baseFaceModels();
        if (url.includes("/task-kinds")) return baseTaskKinds();
        if (url.includes("/meta")) return baseMeta();
        throw new Error("unexpected fetch: " + url);
      };
      return { ok: true, status: 200, json };
    }) as unknown as typeof fetch;
    return calls;
  }

  it("passcode 未設定のときは on にするトグルを押せない（画面側でも塞ぐ）", async () => {
    mockFetchWithWeb({ has_passcode: false, require_passcode: false, is_loopback: true });

    render(<MemoryRouter><ToastProvider>{SettingsScreen}</ToastProvider></MemoryRouter>);
    await waitFor(() => expect(screen.getByText("passcode の設定")).toBeTruthy());

    const checkbox = screen.getByLabelText("外部からのアクセスにパスコードを要求する") as HTMLInputElement;
    expect(checkbox.checked).toBe(false);
    expect(checkbox.disabled).toBe(true);
    expect(screen.getByText(/先にパスコードを設定してください/)).toBeTruthy();
  });

  it("passcode 設定済みなら on にでき、PUT /settings へ {web:{require_passcode:true}} を送る", async () => {
    const calls = mockFetchWithWeb({ has_passcode: true, require_passcode: false, is_loopback: true });

    const user = userEvent.setup();
    render(<MemoryRouter><ToastProvider>{SettingsScreen}</ToastProvider></MemoryRouter>);
    await waitFor(() => expect(screen.getByText("passcode の設定")).toBeTruthy());

    const checkbox = screen.getByLabelText("外部からのアクセスにパスコードを要求する") as HTMLInputElement;
    expect(checkbox.disabled).toBe(false);
    await user.click(checkbox);

    await waitFor(() =>
      expect(calls.some((c) => c.url.includes("/api/v1/settings") && (c.init?.method || "").toUpperCase() === "PUT")).toBe(true)
    );
    const putCall = calls.find((c) => c.url.includes("/api/v1/settings") && (c.init?.method || "").toUpperCase() === "PUT");
    expect(JSON.parse(String(putCall?.init?.body))).toEqual({ web: { require_passcode: true } });
  });

  it("非ループバックで待ち受け中は on のトグルを外せない（画面側でも塞ぐ）", async () => {
    mockFetchWithWeb({ has_passcode: true, require_passcode: true, is_loopback: false });

    render(<MemoryRouter><ToastProvider>{SettingsScreen}</ToastProvider></MemoryRouter>);
    await waitFor(() => expect(screen.getByText("passcode の設定")).toBeTruthy());

    const checkbox = screen.getByLabelText("外部からのアクセスにパスコードを要求する") as HTMLInputElement;
    expect(checkbox.checked).toBe(true);
    expect(checkbox.disabled).toBe(true);
    expect(screen.getByText(/ループバック以外で待ち受けている間は外せません/)).toBeTruthy();
  });

  it("ループバックなら on から off にでき、PUT /settings へ {web:{require_passcode:false}} を送る", async () => {
    const calls = mockFetchWithWeb({ has_passcode: true, require_passcode: true, is_loopback: true });

    const user = userEvent.setup();
    render(<MemoryRouter><ToastProvider>{SettingsScreen}</ToastProvider></MemoryRouter>);
    await waitFor(() => expect(screen.getByText("passcode の設定")).toBeTruthy());

    const checkbox = screen.getByLabelText("外部からのアクセスにパスコードを要求する") as HTMLInputElement;
    expect(checkbox.checked).toBe(true);
    expect(checkbox.disabled).toBe(false);
    await user.click(checkbox);

    await waitFor(() =>
      expect(calls.some((c) => c.url.includes("/api/v1/settings") && (c.init?.method || "").toUpperCase() === "PUT")).toBe(true)
    );
    const putCall = calls.find((c) => c.url.includes("/api/v1/settings") && (c.init?.method || "").toUpperCase() === "PUT");
    expect(JSON.parse(String(putCall?.init?.body))).toEqual({ web: { require_passcode: false } });
  });
});
