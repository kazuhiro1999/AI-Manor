import { describe, expect, it, vi, afterEach } from "vitest";
import { render, cleanup, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ToastProvider } from "../../components/Toast";
import { agentsModule } from "./index";
import type { AgentCard } from "../../app/types";
import { renderFaceThumbnail } from "./faceRenderer";
import { ja } from "../../app/i18n/ja";

// 姿（VRM）を実際に読む・描く経路は three.js/three-vrm 本体（動的 import で vendor を読む）
// なので jsdom では動かさない。`renderFaceThumbnail`（vendor の読み込み・1フレーム描画・
// toDataURL・レンダラー破棄を1つにまとめた関数）をまるごとモックし、一覧側の
// 「読み込み中は silhouette・成功したら <img> に差し替え・失敗したら silhouette のまま・
// 同時には1体しか処理しない・キャッシュされる」という振る舞いだけを検める。
vi.mock("./faceRenderer", () => ({
  renderFaceThumbnail: vi.fn(),
}));

const mockRenderFaceThumbnail = vi.mocked(renderFaceThumbnail);

function mockFetch(agents: AgentCard[]) {
  globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/agents")) return { ok: true, status: 200, json: async () => agents };
    return { ok: true, status: 200, json: async () => ({}) };
  }) as unknown as typeof fetch;
}

function renderScreen() {
  const element = agentsModule.routes[0].element as React.ReactElement;
  return render(
    <MemoryRouter>
      <ToastProvider>{element}</ToastProvider>
    </MemoryRouter>
  );
}

// 姿の焼き上がりはモジュール内キャッシュに担当 id ごと永続する（「開き直しても描き直さない」
// のが仕様どおりの挙動）。そのため、呼び出し回数・成否を検めるテストは他のテストと
// 絶対に衝突しない固有の id を使う（`agents` 共通フィクスチャの id はレイアウト系の
// テストでだけ使い、姿の挙動には使わない）。
const agents: AgentCard[] = [
  { id: "butler", label: "執事", role: "執事", summary: "主人の判断待ちを采配します。", page: "tasks", has_model: false, enabled: true },
  { id: "chef", label: "料理長", role: "料理長", summary: "献立と在庫を預かります。", page: "kitchen", has_model: true, enabled: true },
  { id: "qa", label: "検分", role: "検分", summary: "作ったものを検めます。", page: null, has_model: false, enabled: true },
];

describe("担当の一覧画面（ADR-011 D3）", () => {
  afterEach(() => {
    cleanup();
    mockRenderFaceThumbnail.mockReset();
  });

  it("担当ごとにカードを描画する(名前・役職・要旨)", async () => {
    mockRenderFaceThumbnail.mockResolvedValue({ status: "error" });
    mockFetch(agents);
    renderScreen();

    await waitFor(() => expect(screen.getByText("執事")).toBeTruthy());
    expect(screen.getByText("料理長")).toBeTruthy();
    expect(screen.getByText("検分")).toBeTruthy();
    // 要旨（summary）は id が既知の7人なら i18n 辞書（agent.summary.*）で描画する
    // （ADR-012 D12。バックエンドのモック値 "主人の判断待ちを采配します。" は使われない
    // ——ここが App.test.tsx の APP_NAME と同じ「定数経由で探す」形）。
    expect(screen.getByText(ja["agent.summary.butler"] as string)).toBeTruthy();
  });

  it("page がある担当にはページへのボタンが出る", async () => {
    mockRenderFaceThumbnail.mockResolvedValue({ status: "error" });
    mockFetch(agents);
    renderScreen();

    await waitFor(() => expect(screen.getByText("執事")).toBeTruthy());
    expect(screen.getByRole("link", { name: "タスクへ" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "台所へ" })).toBeTruthy();
  });

  it("page が null の担当（qa/auditor）にはページへのボタンが出ない", async () => {
    mockRenderFaceThumbnail.mockResolvedValue({ status: "error" });
    mockFetch(agents);
    renderScreen();

    await waitFor(() => expect(screen.getByText("検分")).toBeTruthy());
    const qaCard = screen.getByText("検分").closest(".card") as HTMLElement;
    expect(qaCard).toBeTruthy();
    // qa のカードの中に「へ」で終わるページ遷移リンクが無い（小窓を開くボタンだけ）。
    const links = qaCard.querySelectorAll("a");
    expect(links.length).toBe(0);
    expect(screen.getAllByRole("button", { name: "小窓を開く" }).length).toBe(agents.length);
  });

  it("未導入の担当には badge を出す", async () => {
    mockRenderFaceThumbnail.mockResolvedValue({ status: "error" });
    const withDisabled: AgentCard[] = [{ ...agents[1], enabled: false }];
    mockFetch(withDisabled);
    renderScreen();

    await waitFor(() => expect(screen.getByText("料理長")).toBeTruthy());
    expect(screen.getByText("未導入")).toBeTruthy();
  });

  // ---- 姿（VRM）を実際に出す（D3 追補） --------------------------------------------
  // それぞれ他のテストと衝突しない固有の agent id を使う（モジュール内キャッシュが
  // テストをまたいで残るため）。

  it("has_model が無い担当は silhouette のまま、読み込みを試みない", async () => {
    mockRenderFaceThumbnail.mockResolvedValue({ status: "error" });
    const rows: AgentCard[] = [
      { id: "butler-a1", label: "執事A1", role: "執事A1", summary: "s", page: null, has_model: false, enabled: true },
      { id: "chef-a1", label: "料理長A1", role: "料理長A1", summary: "s", page: null, has_model: true, enabled: true },
    ];
    mockFetch(rows);
    renderScreen();

    await waitFor(() => expect(screen.getByText("執事A1")).toBeTruthy());
    const butlerCard = screen.getByText("執事A1").closest(".card") as HTMLElement;
    expect(butlerCard.querySelector(".agent-face-silhouette")).toBeTruthy();
    expect(butlerCard.querySelector("img.agent-face-img")).toBeNull();
    // has_model:false の分は呼ばれない。呼ばれたのは has_model:true の1件だけのはず。
    await waitFor(() => expect(mockRenderFaceThumbnail).toHaveBeenCalledWith("chef-a1"));
    expect(mockRenderFaceThumbnail).toHaveBeenCalledTimes(1);
  });

  it("has_model の担当は読み込みを試み、成功すると silhouette から <img> に差し替わる", async () => {
    mockRenderFaceThumbnail.mockResolvedValue({ status: "loaded", dataUrl: "data:image/png;base64,AAAA" });
    const rows: AgentCard[] = [
      { id: "chef-a2", label: "料理長A2", role: "料理長A2", summary: "s", page: null, has_model: true, enabled: true },
    ];
    mockFetch(rows);
    renderScreen();

    await waitFor(() => expect(screen.getByText("料理長A2")).toBeTruthy());
    const chefCard = screen.getByText("料理長A2").closest(".card") as HTMLElement;

    await waitFor(() => expect(chefCard.querySelector("img.agent-face-img")).toBeTruthy());
    const img = chefCard.querySelector("img.agent-face-img") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe("data:image/png;base64,AAAA");
    expect(chefCard.querySelector(".agent-face-silhouette")).toBeNull();
  });

  it("姿の読み込みが失敗したら silhouette のまま残す（壊れた画像を出さない）", async () => {
    mockRenderFaceThumbnail.mockResolvedValue({ status: "error" });
    const rows: AgentCard[] = [
      { id: "chef-a3", label: "料理長A3", role: "料理長A3", summary: "s", page: null, has_model: true, enabled: true },
    ];
    mockFetch(rows);
    renderScreen();

    await waitFor(() => expect(screen.getByText("料理長A3")).toBeTruthy());
    await waitFor(() => expect(mockRenderFaceThumbnail).toHaveBeenCalledWith("chef-a3"));

    const chefCard = screen.getByText("料理長A3").closest(".card") as HTMLElement;
    expect(chefCard.querySelector(".agent-face-silhouette")).toBeTruthy();
    expect(chefCard.querySelector("img.agent-face-img")).toBeNull();
  });

  it("同時には1体だけ処理する（7体分でも呼び出しが重ならない）", async () => {
    const many: AgentCard[] = [
      { id: "chef-a4", label: "料理長A4", role: "料理長A4", summary: "s1", page: null, has_model: true, enabled: true },
      { id: "housekeeper-a4", label: "家政婦A4", role: "家政婦A4", summary: "s2", page: null, has_model: true, enabled: true },
      { id: "steward-a4", label: "家令A4", role: "家令A4", summary: "s3", page: null, has_model: true, enabled: true },
    ];

    let concurrent = 0;
    let maxConcurrent = 0;
    const resolvers: Array<() => void> = [];
    mockRenderFaceThumbnail.mockImplementation(
      () =>
        new Promise((resolve) => {
          concurrent += 1;
          maxConcurrent = Math.max(maxConcurrent, concurrent);
          resolvers.push(() => {
            concurrent -= 1;
            resolve({ status: "error" });
          });
        })
    );

    mockFetch(many);
    renderScreen();

    await waitFor(() => expect(screen.getByText("家令A4")).toBeTruthy());
    // 3体とも登場している時点で、実際に「実行中」なのは1件だけ（残り2件はキュー待ち）。
    await waitFor(() => expect(mockRenderFaceThumbnail).toHaveBeenCalledTimes(1));
    expect(concurrent).toBe(1);

    resolvers[0]();
    await waitFor(() => expect(mockRenderFaceThumbnail).toHaveBeenCalledTimes(2));
    expect(concurrent).toBe(1);

    resolvers[1]();
    await waitFor(() => expect(mockRenderFaceThumbnail).toHaveBeenCalledTimes(3));
    expect(concurrent).toBe(1);

    resolvers[2]();
    await waitFor(() => expect(concurrent).toBe(0));

    expect(maxConcurrent).toBe(1); // 一度も2体同時には走らなかった
  });

  it("焼いた静止画はモジュール内キャッシュに残り、画面を作り直しても再描画しない", async () => {
    mockRenderFaceThumbnail.mockResolvedValue({ status: "loaded", dataUrl: "data:image/png;base64,BBBB" });
    const rows: AgentCard[] = [
      { id: "chef-a5", label: "料理長A5", role: "料理長A5", summary: "s", page: null, has_model: true, enabled: true },
    ];
    mockFetch(rows);
    renderScreen();

    await waitFor(() => {
      const chefCard = screen.getByText("料理長A5").closest(".card") as HTMLElement;
      expect(chefCard.querySelector("img.agent-face-img")).toBeTruthy();
    });
    expect(mockRenderFaceThumbnail).toHaveBeenCalledTimes(1);

    // 画面を作り直す（「開き直し」を模す）。モジュール内キャッシュは残ったままなので、
    // renderFaceThumbnail は再び呼ばれず、即座に <img> が出る。
    cleanup();
    mockFetch(rows);
    renderScreen();

    await waitFor(() => expect(screen.getByText("料理長A5")).toBeTruthy());
    const chefCard = screen.getByText("料理長A5").closest(".card") as HTMLElement;
    const img = chefCard.querySelector("img.agent-face-img") as HTMLImageElement;
    expect(img).toBeTruthy();
    expect(img.getAttribute("src")).toBe("data:image/png;base64,BBBB");
    expect(mockRenderFaceThumbnail).toHaveBeenCalledTimes(1); // 増えていない
  });
});
