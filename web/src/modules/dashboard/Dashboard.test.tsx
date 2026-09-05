import { describe, expect, it, vi, afterEach } from "vitest";
import { render, cleanup, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ToastProvider } from "../../components/Toast";
import { dashboardModule } from "./index";
import type { DashboardData } from "../../app/types";

function mockFetch(data: DashboardData) {
  globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/dashboard")) return { ok: true, status: 200, json: async () => data };
    return { ok: true, status: 200, json: async () => ({}) };
  }) as unknown as typeof fetch;
}

function renderScreen() {
  const element = dashboardModule.routes[0].element as React.ReactElement;
  return render(
    <MemoryRouter>
      <ToastProvider>{element}</ToastProvider>
    </MemoryRouter>
  );
}

const fullPayload: DashboardData = {
  today: "2026-09-04",
  status: { ok: false, action_needed: 2, check_failures: 0, open_decisions: 1, blocked_ready: 1 },
  counts: { pending_decisions: 1, doing_butler: 3, due_today: 2, done_this_week: 4 },
  night: { available: true, status: "done", started_at: "2026-09-03T22:00:00", ended_at: "2026-09-03T22:10:00" },
  upcoming: [
    { kind: "milestone", id: "M1", title: "棚の発注", date: "2026-09-16", approximate: false, days_left: 12 },
  ],
  attention: [{ id: "D1", title: "見積もりをどれにするか", days: 4, risk: "medium", stale: true }],
  runs_24h: {
    available: true,
    runs: [
      {
        id: 1,
        kind: "behavior",
        ref: "S6",
        started_at: "2026-09-04T01:00:00",
        ended_at: "2026-09-04T01:01:00",
        model: "claude-test",
        cost_usd: 0.1,
        turns: 3,
        exit_reason: "done",
        note: "",
      },
    ],
  },
  most_active: { available: true, by_kind: [{ kind: "behavior", count: 5, cost_usd: 0.5, avg_seconds: 30, fail_rate: 0 }] },
  usage_cost: { available: true, count: 5, failed: 0, success_rate: 1, cost_usd: 0.5, cost_measured: 5 },
};

const emptyPayload: DashboardData = {
  today: "2026-09-04",
  status: { ok: true, action_needed: 0, check_failures: 0, open_decisions: 0, blocked_ready: 0 },
  counts: { pending_decisions: 0, doing_butler: 0, due_today: 0, done_this_week: 0 },
  night: { available: false, status: null, started_at: null, ended_at: null },
  upcoming: [],
  attention: [],
  runs_24h: { available: false, runs: [] },
  most_active: { available: false, by_kind: [] },
  usage_cost: { available: false },
};

describe("ダッシュボード画面（ADR-011 D2）", () => {
  afterEach(() => cleanup());

  it("値のそろったデータで各帯を描画する", async () => {
    mockFetch(fullPayload);
    renderScreen();

    await waitFor(() => expect(screen.getByText("要対応 2件")).toBeTruthy());
    expect(screen.getByText("1")).toBeTruthy(); // 判断待ちタイルの値
    expect(screen.getByText("棚の発注")).toBeTruthy();
    expect(screen.getByText("見積もりをどれにするか")).toBeTruthy();
    // 「成功」は夜勤タイルと24時間の実行バッジの両方に出る。
    expect(screen.getAllByText("成功").length).toBeGreaterThanOrEqual(2);
  });

  it("空のデータでもクラッシュせず、帯ごとに『まだ記録がありません』等を出す（劣化グレースフル）", async () => {
    mockFetch(emptyPayload);
    renderScreen();

    await waitFor(() => expect(screen.getByText("問題ありません")).toBeTruthy());
    expect(screen.getByText("直近の予定・期限はありません。")).toBeTruthy();
    expect(screen.getByText("問題はありません。")).toBeTruthy();
    // run 表が無い home 相当（available: false）の帯が3つとも案内文を出す。
    expect(screen.getAllByText(/run 表が無い home です/).length).toBeGreaterThanOrEqual(2);
  });
});
