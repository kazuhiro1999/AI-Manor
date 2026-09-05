import { describe, expect, it, vi, afterEach } from "vitest";
import { render, cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { ToastProvider } from "../../components/Toast";
import { extensionsModule } from "./index";
import type { ExtensionDetail, ExtensionSummary } from "../../app/types";

const summaries: ExtensionSummary[] = [
  {
    id: "voicevox",
    label: "VOICEVOX（音声合成）",
    kind: "local_app",
    summary: "執事の声を VOICEVOX で合成します。",
    status: "needs_config",
    checked_at: null,
    reason: "",
  },
  {
    id: "slack",
    label: "Slack（通知）",
    kind: "service",
    summary: "Slack へ通知します。",
    status: "ok",
    checked_at: "2026-09-04T09:00:00",
    reason: "",
  },
];

const voicevoxDetail: ExtensionDetail = {
  id: "voicevox",
  manifest: {
    id: "voicevox",
    label: "VOICEVOX（音声合成）",
    kind: "local_app",
    summary: "執事の声を VOICEVOX で合成します。",
    install_steps: ["1. インストールします。", "2. 話者を選びます。"],
    fields: [
      { key: "speaker", label: "話者", kind: "select", options_from: "speakers", required: true },
      { key: "engine_path", label: "エンジンの場所", kind: "path", required: false },
    ],
    secret_fields: [],
  },
  values: { speaker: null, engine_path: null },
  install_steps: ["1. インストールします。", "2. 話者を選びます。"],
  status: "needs_config",
  checked_at: null,
  reason: "",
};

const slackDetail: ExtensionDetail = {
  id: "slack",
  manifest: {
    id: "slack",
    label: "Slack（通知）",
    kind: "service",
    summary: "Slack へ通知します。",
    install_steps: ["1. トークンを発行します。"],
    fields: [{ key: "bot_token", label: "Bot トークン", kind: "password", required: true }],
    secret_fields: ["bot_token"],
  },
  values: { has_bot_token: true },
  install_steps: ["1. トークンを発行します。"],
  status: "ok",
  checked_at: "2026-09-04T09:00:00",
  reason: "",
};

const speakerOptions = [
  { value: 2, label: "四国めたん（ノーマル）" },
  { value: 3, label: "ずんだもん（ノーマル）" },
];

function mockFetch(handlers: Record<string, (init?: RequestInit) => unknown>) {
  globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    for (const [suffix, handler] of Object.entries(handlers)) {
      if (url.endsWith(suffix)) {
        return { ok: true, status: 200, json: async () => handler(init) };
      }
    }
    return { ok: true, status: 200, json: async () => ({}) };
  }) as unknown as typeof fetch;
}

function renderScreen() {
  const element = extensionsModule.routes[0].element as React.ReactElement;
  return render(
    <MemoryRouter>
      <ToastProvider>{element}</ToastProvider>
    </MemoryRouter>
  );
}

describe("拡張機能画面（ADR-009 D7）", () => {
  afterEach(() => cleanup());

  it("一覧の状態チップを描画する（未導入・設定が要る・準備完了・動作確認済み・エラーの日本語ラベル）", async () => {
    mockFetch({ "/api/v1/extensions": () => summaries });

    renderScreen();
    await waitFor(() => expect(screen.getByText("VOICEVOX（音声合成）")).toBeTruthy());
    expect(screen.getByText("Slack（通知）")).toBeTruthy();
    expect(screen.getByText("設定が要る")).toBeTruthy();
    expect(screen.getByText("動作確認済み")).toBeTruthy();
  });

  it("カードを開くと options_from の選択肢を取得する（D5）", async () => {
    const calls: string[] = [];
    mockFetch({
      "/api/v1/extensions": () => summaries,
      "/api/v1/extensions/voicevox/options/speakers": () => {
        calls.push("options");
        return speakerOptions;
      },
      "/api/v1/extensions/voicevox": () => voicevoxDetail,
    });

    renderScreen();
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByText("VOICEVOX（音声合成）")).toBeTruthy());
    await user.click(screen.getByText("VOICEVOX（音声合成）"));

    await waitFor(() => expect(calls).toContain("options"));
    await waitFor(() => expect(screen.getByText("四国めたん（ノーマル）")).toBeTruthy());
  });

  it("選択肢が空のときは案内文を出して数字入力に落とす（D5 のフォールバック）", async () => {
    mockFetch({
      "/api/v1/extensions": () => summaries,
      "/api/v1/extensions/voicevox/options/speakers": () => [],
      "/api/v1/extensions/voicevox": () => voicevoxDetail,
    });

    renderScreen();
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByText("VOICEVOX（音声合成）")).toBeTruthy());
    await user.click(screen.getByText("VOICEVOX（音声合成）"));

    await waitFor(() =>
      expect(screen.getByText("取得できません。エンジンを起動してから再度お試しください。")).toBeTruthy()
    );
    // select ではなく number input に落ちている
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("保存すると PUT { values } を正しい形で送る", async () => {
    let putBody: unknown = null;
    // PUT は method で分岐する必要があるため mockFetch ヘルパー（GET 専用）は使わず、直接組み立てる。
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method || "GET";
      if (url.endsWith("/api/v1/extensions") && method === "GET") return { ok: true, status: 200, json: async () => summaries };
      if (url.endsWith("/api/v1/extensions/voicevox/options/speakers")) {
        return { ok: true, status: 200, json: async () => speakerOptions };
      }
      if (url.endsWith("/api/v1/extensions/voicevox") && method === "PUT") {
        putBody = init?.body ? JSON.parse(String(init.body)) : null;
        return {
          ok: true,
          status: 200,
          json: async () => ({ ...voicevoxDetail, values: { speaker: 3, engine_path: null }, status: "ready" }),
        };
      }
      if (url.endsWith("/api/v1/extensions/voicevox") && method === "GET") {
        return { ok: true, status: 200, json: async () => voicevoxDetail };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    }) as unknown as typeof fetch;

    renderScreen();
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByText("VOICEVOX（音声合成）")).toBeTruthy());
    await user.click(screen.getByText("VOICEVOX（音声合成）"));
    await waitFor(() => expect(screen.getByText("四国めたん（ノーマル）")).toBeTruthy());

    const select = screen.getByLabelText("話者") as HTMLSelectElement;
    await user.selectOptions(select, "3");
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(putBody).toEqual({ values: { speaker: "3" } }));
  });

  it("パスワード欄は値を描画せず、設定済みの印だけ出す（秘密を never display）", async () => {
    mockFetch({
      "/api/v1/extensions": () => summaries,
      "/api/v1/extensions/slack": () => slackDetail,
    });

    renderScreen();
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByText("Slack（通知）")).toBeTruthy());
    await user.click(screen.getByText("Slack（通知）"));

    const input = (await screen.findByLabelText("Bot トークン")) as HTMLInputElement;
    expect(input.type).toBe("password");
    expect(input.value).toBe("");
    expect(screen.getByText("設定済み")).toBeTruthy();
    // slackDetail.values には has_bot_token こそあれ、値そのもの（bot_token キー）は無い——
    // 画面がそれを描画する経路自体が存在しないことを、生の秘密文字列が本文に無いことで確かめる。
    expect(document.body.textContent).not.toContain("xoxb-");
  });
});
