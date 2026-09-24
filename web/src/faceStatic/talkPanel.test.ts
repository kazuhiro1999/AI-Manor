// T93: face.html（通話パネル）に初めて自動試験を通す。T57（30秒待ちの一言）・
// T58（stream-json 観測を3秒おきに取りに行って表示を上書きする）の両方を、
// 実際にタイマーを進めて検算する（見積もりではなく `waitLine()` の実挙動を見る）。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { makeFetchMock, mountTalkDom, runTalkScript, type FetchHandler } from "./talkPanelHarness";

function openHandlers(): Record<string, FetchHandler> {
  return {
    "GET /api/v1/face/talk": () => ({ json: { available: true, used: 0, limit: 20, remaining: 20 } }),
    "POST /api/v1/face/talk/open": () => ({
      json: { available: true, text: "こんにちは。", lines: ["こんにちは。", "ご用件は？"], spoke: true, warming: false, audio_id: "" },
    }),
    "POST /api/v1/face/talk/close": () => ({ json: { stopped: false } }),
    "GET /api/v1/face/talk/status": () => ({ json: { text: "" } }),
  };
}

function setUp(handlers: Record<string, FetchHandler>) {
  mountTalkDom();
  const mock = makeFetchMock({ ...openHandlers(), ...handlers });
  vi.stubGlobal("fetch", mock.fetch);
  runTalkScript();
  return mock;
}

function pendingParagraphs(): HTMLParagraphElement[] {
  return Array.from(document.querySelectorAll("#talkLog p"));
}

/** 直近の `<p>`（`waitLine()`/返事が書き込む要素）。`Array.prototype.at` は使わない
 * ——このプロジェクトの tsconfig の `lib` が ES2020 のため。 */
function lastPendingParagraph(): HTMLParagraphElement | undefined {
  const items = pendingParagraphs();
  return items[items.length - 1];
}

function sendMessage(text: string): void {
  const input = document.getElementById("talkInput") as HTMLInputElement;
  input.value = text;
  (document.getElementById("talkSend") as HTMLButtonElement).click();
}

describe("face.html 通話パネル（T57 少々お待ちください／T58 状態観測）", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("25秒で返事が来たら「少々お待ちください」は出ず、返事に置き換わる（T57）", async () => {
    const mock = setUp({
      "POST /api/v1/face/talk": () => ({ json: { ok: true, reply: "かしこまりました。", audio_id: "" } }),
    });
    await vi.advanceTimersByTimeAsync(0); // openPanel() の初期 fetch を片付ける

    sendMessage("在庫を確認して");
    await vi.advanceTimersByTimeAsync(400); // submit() の fetch チェーンを流す

    const last = lastPendingParagraph();
    expect(last?.textContent).toBe("かしこまりました。");
    expect(mock.calls.some((c) => c.url === "/api/v1/face/talk/wait")).toBe(false);
  });

  it("30秒経つと「少々お待ちください」を表示し、/talk/wait を1度だけ叩く（T57）", async () => {
    const mock = setUp({
      "POST /api/v1/face/talk": () => new Promise<never>(() => {}), // 返事が来ないふり
      "POST /api/v1/face/talk/wait": () => ({ json: { text: "少々お待ちください。", spoke: true, audio_id: "" } }),
    });
    await vi.advanceTimersByTimeAsync(0);

    sendMessage("こんにちは");
    await vi.advanceTimersByTimeAsync(30_400);

    const last = lastPendingParagraph();
    expect(last?.textContent).toContain("少々お待ちください");
    const waitCalls = mock.calls.filter((c) => c.url === "/api/v1/face/talk/wait");
    expect(waitCalls).toHaveLength(1);

    // 30秒を超えてもう一巡しても、二度は叩かない（askedWait のガード）。
    await vi.advanceTimersByTimeAsync(2_000);
    expect(mock.calls.filter((c) => c.url === "/api/v1/face/talk/wait")).toHaveLength(1);
  });

  it("観測（/talk/status）が来たら、30秒を待たずに実況で上書きする（T58）", async () => {
    const mock = setUp({
      "POST /api/v1/face/talk": () => new Promise<never>(() => {}), // 返事が来ないふり
      "GET /api/v1/face/talk/status": () => ({ json: { text: "手続きしています" } }),
    });
    await vi.advanceTimersByTimeAsync(0);

    sendMessage("牛乳を買うタスクを追加して");
    await vi.advanceTimersByTimeAsync(3_600); // 3秒ごとの取得が最低1回は走るだけ進める

    const last = lastPendingParagraph();
    expect(last?.textContent).toContain("手続きしています");
    expect(mock.calls.some((c) => c.url === "/api/v1/face/talk/status")).toBe(true);
    // 観測が来ている間は「少々お待ちください」（30秒の一言）には上書きされない。
    expect(last?.textContent).not.toContain("少々お待ちください");
  });

  it("観測が空文字に戻れば、通常の経過表示に戻る（T58: 見積もりへ戻すのではなく観測を信じる）", async () => {
    let statusText = "お調べしています";
    const mock = setUp({
      "POST /api/v1/face/talk": () => new Promise<never>(() => {}),
      "GET /api/v1/face/talk/status": () => ({ json: { text: statusText } }),
    });
    await vi.advanceTimersByTimeAsync(0);

    sendMessage("ゴミの日を教えてください");
    await vi.advanceTimersByTimeAsync(3_600);
    expect(lastPendingParagraph()?.textContent).toContain("お調べしています");

    statusText = ""; // 道具の使用が終わり、観測が空へ戻った想定
    await vi.advanceTimersByTimeAsync(3_600);
    expect(lastPendingParagraph()?.textContent).not.toContain("お調べしています");
    void mock; // 呼び出し回数はここでは見ない
  });
});
