/* 朝の点検が夜勤の欄に出ること（2026-09-09・主人のご要望「夜勤の欄で見えるとありがたい」）。
 *
 * ⚠ ブラウザで開いての確認はできていません（`manor web` は passcode を要求し、
 * 執事は主人の passcode を持ちません）。**代わりにここで描画まで確かめます**——
 * 「画面を直したらブラウザで console を見るまで終わりではない」（2026-09-07・G）の
 * 代替として、レンダリングと文言を機械が毎回見る形にしました。
 */
import { describe, expect, it, vi, afterEach, beforeEach } from "vitest";
import { render, cleanup, screen, waitFor } from "@testing-library/react";
import { NightPanel } from "./NightPanel";
import type { NightReview } from "../../app/types";

function mockApi(review: NightReview | null, opts: { reviewFails?: boolean } = {}) {
  globalThis.fetch = vi.fn().mockImplementation((url: string) => {
    const u = String(url);
    if (u.includes("/night/review")) {
      if (opts.reviewFails) return Promise.resolve({ ok: false, status: 500, json: async () => ({}) });
      return Promise.resolve({ ok: true, status: 200, json: async () => review });
    }
    if (u.includes("/night/reports/")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({ date: "2026-09-09", text: "# 報告", parsed: { title: "報告", tasks: [] } }),
      });
    }
    return Promise.resolve({ ok: true, status: 200, json: async () => ({ dates: ["2026-09-09"] }) });
  }) as unknown as typeof fetch;
}

const HEALTHY: NightReview = {
  date: "2026-09-09",
  health: { ok: true, reasons: [] },
  items: { found: true, pending: [] },
  stuck: [],
};

describe("夜勤の欄の朝の点検", () => {
  const originalFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = originalFetch;
    cleanup();
  });
  beforeEach(() => mockApi(HEALTHY));

  it("正常な晩は「正常に終わっています」と出し、色を付けない", async () => {
    render(<NightPanel />);
    await waitFor(() => expect(screen.getByText(/正常に終わっています/)).toBeTruthy());
    const box = document.querySelector(".night-review")!;
    expect(box.classList.contains("is-warn")).toBe(false);
  });

  it("異常があれば理由を並べ、帯に色を付ける", async () => {
    mockApi({
      ...HEALTHY,
      health: { ok: false, reasons: ["道具を 8 回拒まれました（Bash, WebFetch, WebSearch）"] },
    });
    render(<NightPanel />);
    await waitFor(() => expect(screen.getByText(/道具を 8 回拒まれました/)).toBeTruthy());
    expect(document.querySelector(".night-review")!.classList.contains("is-warn")).toBe(true);
  });

  it("片付かなかった件を出し、2晩以上なら続いた晩数を言う", async () => {
    mockApi({
      ...HEALTHY,
      items: {
        found: true,
        pending: [
          { heading: "N2 定例を回す", state: "保留 — 手段が無い", nights: 3 },
          { heading: "N3 数える", state: "保留", nights: 1 },
        ],
      },
      stuck: ["N2 定例を回す"],
    });
    render(<NightPanel />);
    await waitFor(() => expect(screen.getByText(/3晩続けて片付かず: N2 定例を回す/)).toBeTruthy());
    expect(screen.getByText(/片付かず: N3 数える/)).toBeTruthy();
  });

  it("点検が読めなくても報告の表示は止めない", async () => {
    mockApi(HEALTHY, { reviewFails: true });
    render(<NightPanel />);
    await waitFor(() => expect(document.getElementById("night-body")).toBeTruthy());
    expect(document.querySelector(".night-review")).toBeNull();
  });
});
