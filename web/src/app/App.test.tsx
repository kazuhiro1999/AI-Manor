import { describe, expect, it, vi, afterEach } from "vitest";
import { APP_NAME } from "./brand";
import { formatDay, t } from "./i18n";
import { render, cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { App } from "./App";
import type { Board, Meta, SetupInfo } from "./types";

const SETUP_HEADING = `${APP_NAME} の初回セットアップ`;

// 日付は曜日つきで出す（主人の指示 2026-09-05）。**文字列をここに固定しない**
// ——書式を変えるたびに試験を書き直すことになるので、辞書と同じ関数から組む。
//
// **関数にしてある。** 定数にすると読み込み時の言語で固まり、描画時の言語と食い違う
// （実測 2026-09-05: 日本語の画面に対して英語の期待値を作って落ちた）。
const todayChip = () => t("app.today", { date: formatDay("2026-09-03", t) });

function baseMeta(setupDone: boolean): Meta {
  return {
    version: "test-0.0.1",
    today: "2026-09-03",
    read_only: false,
    stale: false,
    auth: { mode: "loopback", authenticated: true },
    modules: [{ id: "tasks", title: "タスク", icon: "T", order: 1, enabled: true }],
    home_name: "test-home",
    setup_done: setupDone,
  };
}

function baseSetupInfo(): SetupInfo {
  return { done: false, completed_at: null, profile: {}, purposes: [], presets: [], task_classes: [], money_apps: [] };
}

function baseBoard(): Board {
  return {
    today: "2026-09-03",
    pending: [],
    tasks: [],
    delegated: [],
    projects: [],
    milestones: [],
    recent_done: [],
    withdrawn_recent: [],
    notes: [],
    counts: { pending: 0, doing: 0, doing_butler: 0, doing_master: 0, resident: 0, blocked_ready: 0, stale: 0, done_total: 0 },
    fingerprint: "x",
  };
}

/** App は /meta・/tasks/board を常にポーリングし、/setup へ誘導されると SetupScreen が
 *  /setup も取りに行く。ここでは3経路だけ意味のあるデータを返し、他は空オブジェクトで
 *  黙らせる（各画面が読み込み中のまま止まっても、この試験の主眼である誘導の有無には
 *  影響しない）。 */
function mockFetch(setupDone: boolean) {
  const meta = baseMeta(setupDone);
  const setupInfo = baseSetupInfo();
  const board = baseBoard();
  globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    const json = async () => {
      if (url.includes("/meta")) return meta;
      if (url.includes("/setup")) return setupInfo;
      if (url.includes("/tasks/board")) return board;
      return {};
    };
    return { ok: true, status: 200, json };
  }) as unknown as typeof fetch;
}

describe("App — 初回セットアップへの誘導（ADR-007 D6）", () => {
  afterEach(() => cleanup());

  it("meta.setup_done === false のとき、/tasks から /setup へ誘導する", async () => {
    mockFetch(false);
    render(
      <MemoryRouter initialEntries={["/tasks"]}>
        <App />
      </MemoryRouter>
    );
    await waitFor(() => expect(screen.getByText(SETUP_HEADING)).toBeTruthy());
  });

  it("meta.setup_done === true のときは誘導しない（/setup の画面は出ない）", async () => {
    mockFetch(true);
    render(
      <MemoryRouter initialEntries={["/tasks"]}>
        <App />
      </MemoryRouter>
    );
    await waitFor(() => expect(screen.getByText(todayChip())).toBeTruthy());
    expect(screen.queryByText(SETUP_HEADING)).toBeNull();
  });

  it("POST /setup 成功直後、meta がまだ setup_done: false のままでも /setup へ戻らず /tasks に着地する", async () => {
    const user = userEvent.setup();
    // 実機で見つかった不具合の再現: バックエンドの meta は次のポーリングまで
    // setup_done: false のまま（このテストでは意図的にずっと false を返し続ける）。
    // それでも「このセッションで完了した」フラグ（MetaContext）で誘導を止められることを確かめる。
    const meta = baseMeta(false);
    const setupInfo: SetupInfo = {
      done: false,
      completed_at: null,
      profile: {},
      purposes: [{ id: "tasks", label: "タスク・プロジェクトの管理" }],
      presets: [{ id: "standard", label: "標準" }],
      task_classes: [{ id: "general", label: "一般の作業", default_level: "L2", fixed: false }],
      money_apps: [{ id: "none", label: "使っていない" }],
    };
    const board = baseBoard();
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method || "GET";
      if (url.includes("/meta")) return { ok: true, status: 200, json: async () => meta };
      if (url.includes("/setup") && method === "GET") return { ok: true, status: 200, json: async () => setupInfo };
      if (url.includes("/setup") && method === "POST") {
        return { ok: true, status: 200, json: async () => ({ profile: {}, created: { projects: [], tasks: [] } }) };
      }
      if (url.includes("/tasks/board")) return { ok: true, status: 200, json: async () => board };
      return { ok: true, status: 200, json: async () => ({}) };
    }) as unknown as typeof fetch;

    render(
      <MemoryRouter initialEntries={["/setup"]}>
        <App />
      </MemoryRouter>
    );

    await waitFor(() => expect(screen.getByLabelText("主人の呼び名")).toBeTruthy());
    await user.type(screen.getByLabelText("主人の呼び名"), "主人");
    await user.click(screen.getByRole("button", { name: "次へ" }));

    // 使いたい機能: tasks が既定 on なので、選ばずそのまま次へ進める。
    await waitFor(() => expect(screen.getByText("タスク・プロジェクトの管理")).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "次へ" }));

    // 最初の仕事: 「あとで」で飛ばす。
    await waitFor(() => expect(screen.getByRole("button", { name: "あとで" })).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "あとで" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "登録する" })).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "登録する" }));

    // /tasks のシェル（topbar の「本日」チップ）に着地し、ウィザードへは戻らない。
    await waitFor(() => expect(screen.getByText(todayChip())).toBeTruthy());
    expect(screen.queryByText(SETUP_HEADING)).toBeNull();
  });
});

describe("App — 古いサーバの警告（2026-09-04 実測）", () => {
  afterEach(() => cleanup());

  /** 更新後にサーバを止め忘れたまま主人がセットアップを開き、「種類が0件」「確認画面で500」に
   *  見えた。原因は古いプロセスだったが、それを告げる帯は枠側にしか無く、**初めての人が最初に
   *  見る画面でだけ隠れていた**。 */
  it("初回セットアップの画面でも stale の帯を出す", async () => {
    const meta = { ...baseMeta(false), stale: true };
    const setupInfo = baseSetupInfo();
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      const json = async () => {
        if (url.includes("/meta")) return meta;
        if (url.includes("/setup")) return setupInfo;
        if (url.includes("/task-kinds")) return [];
        return {};
      };
      return { ok: true, status: 200, json };
    }) as unknown as typeof fetch;

    render(
      <MemoryRouter initialEntries={["/setup"]}>
        <App />
      </MemoryRouter>
    );
    await waitFor(() => {
      expect(screen.getByText(/サーバのコードが更新されています/)).toBeTruthy();
    });
  });
});

describe("App — 起動時に保存済みの配色を反映する（設定画面を開かなくても効く）", () => {
  afterEach(() => {
    cleanup();
    localStorage.removeItem("manor-web.theme"); // theme.ts の THEME_KEY と同じ文字列（非公開のため直書き）
    document.documentElement.removeAttribute("data-theme");
  });

  it("localStorage に dark が入っていれば、/settings を開かなくても document に data-theme=dark が付く", async () => {
    localStorage.setItem("manor-web.theme", "dark");
    mockFetch(true);

    render(
      <MemoryRouter initialEntries={["/tasks"]}>
        <App />
      </MemoryRouter>
    );

    // useLayoutEffect で描画前に当てているはずなので、他のデータの読み込みを待たずに確認できる。
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    // ついでに /tasks 画面まで来ても剥がれていないことも確かめる。
    await waitFor(() => expect(screen.getByText(todayChip())).toBeTruthy());
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  it("localStorage に light が入っていれば data-theme=light が付く", async () => {
    localStorage.setItem("manor-web.theme", "light");
    mockFetch(true);

    render(
      <MemoryRouter initialEntries={["/tasks"]}>
        <App />
      </MemoryRouter>
    );

    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });

  it("何も保存されていなければ（system）data-theme 属性を付けない", async () => {
    mockFetch(true);

    render(
      <MemoryRouter initialEntries={["/tasks"]}>
        <App />
      </MemoryRouter>
    );

    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
  });
});

describe("App — 設定はサイドバーに出ず、右上の歯車から開く（ADR-011 D1）", () => {
  afterEach(() => cleanup());

  it("meta.modules に settings があっても、サイドバーには出ない。topbar に設定ボタンがある", async () => {
    const meta = baseMeta(true);
    meta.modules = [
      { id: "dashboard", title: "ダッシュボード", icon: "🏠", order: 1, enabled: true },
      { id: "agents", title: "担当", icon: "🧑‍🤝‍🧑", order: 2, enabled: true },
      { id: "tasks", title: "タスク", icon: "T", order: 3, enabled: true },
      // hideFromNav（登録簿側）が効くことを確かめたいので、意図的に enabled: true のまま渡す。
      { id: "settings", title: "設定", icon: "⚙", order: 90, enabled: true },
    ];
    const board = baseBoard();
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      const json = async () => {
        if (url.includes("/meta")) return meta;
        if (url.includes("/tasks/board")) return board;
        if (url.includes("/dashboard")) return {};
        if (url.includes("/agents")) return [];
        return {};
      };
      return { ok: true, status: 200, json };
    }) as unknown as typeof fetch;

    render(
      <MemoryRouter initialEntries={["/tasks"]}>
        <App />
      </MemoryRouter>
    );

    await waitFor(() => expect(screen.getByText(todayChip())).toBeTruthy());
    expect(document.querySelector('.sidenav [data-module="settings"]')).toBeNull();
    expect(document.querySelector('.sidenav [data-module="dashboard"]')).toBeTruthy();
    expect(screen.getByRole("button", { name: "設定を開く" })).toBeTruthy();
  });
});
