/* T54③ 意見箱の画面の試験（IdeaForm・IdeaList）。他モジュールの大半に *.test.tsx が
 * あるのに、ここだけ無かった穴を埋める（T61 で IdeaList の修正・取り下げは別途
 * IdeaList.test.tsx が担うので、ここはフォームと一覧の基本の道筋だけを見る）。
 */
import { describe, expect, it, vi, afterEach } from "vitest";
import { render, cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { IdeaForm } from "./IdeaForm";
import { IdeaList } from "./IdeaList";
import type { Task } from "../../app/types";
import { ToastProvider } from "../../components/Toast";

function makeItems(): Task[] {
  return [
    { id: "T1", project_id: null, status: "hold", owner: "master", title: "先に入れた意見", body: "本文1", source: "idea" } as Task,
    { id: "T9", project_id: null, status: "done", owner: "master", title: "後に入れた意見", body: "本文2", source: "idea" } as Task,
  ];
}

describe("IdeaForm — 送る前の守り", () => {
  const originalFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = originalFetch;
    cleanup();
  });

  it("本文が空のままでは送信を弾く（POST を呼ばない）", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), init });
      return { ok: true, status: 200, json: async () => ({ id: "T1" }) };
    }) as unknown as typeof fetch;

    const user = userEvent.setup();
    render(
      <ToastProvider>
        <IdeaForm />
      </ToastProvider>
    );
    await user.click(screen.getByRole("button", { name: "送る" }));

    expect(screen.getByText("本文は必須です")).toBeTruthy();
    expect(calls.some((c) => c.url.includes("/api/v1/ideas"))).toBe(false);
  });

  it("本文を入れて送ると POST /api/v1/ideas を送り、onCreated を呼ぶ", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), init });
      return { ok: true, status: 200, json: async () => ({ id: "T9" }) };
    }) as unknown as typeof fetch;

    const onCreated = vi.fn();
    const user = userEvent.setup();
    render(
      <ToastProvider>
        <IdeaForm onCreated={onCreated} />
      </ToastProvider>
    );
    await user.type(screen.getByLabelText("ご意見（先頭行が題名になります）"), "新しい意見");
    await user.click(screen.getByRole("button", { name: "送る" }));

    await waitFor(() => expect(calls.some((c) => c.url.includes("/api/v1/ideas"))).toBe(true));
    const postCall = calls.find((c) => c.url.includes("/api/v1/ideas"));
    expect((postCall?.init?.method || "").toUpperCase()).toBe("POST");
    expect(JSON.parse(String(postCall?.init?.body)).body).toBe("新しい意見");
    expect(onCreated).toHaveBeenCalled();
  });
});

describe("IdeaList — 読込中・空・失敗・状態の札", () => {
  const originalFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = originalFetch;
    cleanup();
  });

  it("読み込み中はローディング文言を出す", () => {
    globalThis.fetch = vi.fn().mockImplementation(() => new Promise(() => {})) as unknown as typeof fetch;
    render(
      <ToastProvider>
        <IdeaList reloadKey={0} />
      </ToastProvider>
    );
    expect(screen.getByText("読み込み中…")).toBeTruthy();
  });

  it("0件なら空の文言を出す", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ items: [] }),
    }) as unknown as typeof fetch;
    render(
      <ToastProvider>
        <IdeaList reloadKey={0} />
      </ToastProvider>
    );
    await waitFor(() => expect(screen.getByText("まだありません。")).toBeTruthy());
  });

  it("読み込みに失敗したら理由を出す", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ detail: "落ちました" }),
    }) as unknown as typeof fetch;
    render(
      <ToastProvider>
        <IdeaList reloadKey={0} />
      </ToastProvider>
    );
    await waitFor(() => expect(screen.getByText(/読み込めませんでした/)).toBeTruthy());
  });

  it("状態ごとの札を出す（hold=仕分け待ち・done=完了 ✓）", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ items: makeItems() }),
    }) as unknown as typeof fetch;
    render(
      <ToastProvider>
        <IdeaList reloadKey={0} />
      </ToastProvider>
    );
    await waitFor(() => expect(screen.getByText("先に入れた意見")).toBeTruthy());
    expect(screen.getByText("仕分け待ち")).toBeTruthy();
    expect(screen.getByText("完了 ✓")).toBeTruthy();
  });

  it("新しい順（id の数値の降順）に並べる", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ items: makeItems() }),
    }) as unknown as typeof fetch;
    render(
      <ToastProvider>
        <IdeaList reloadKey={0} />
      </ToastProvider>
    );
    await waitFor(() => expect(screen.getByText("先に入れた意見")).toBeTruthy());
    const ids = Array.from(document.querySelectorAll(".row-id")).map((n) => n.textContent);
    expect(ids).toEqual(["T9", "T1"]);
  });
});
