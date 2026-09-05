import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { api, ApiError, setUnauthorizedHandler } from "./api";

function mockFetchOnce(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

describe("api", () => {
  const originalFetch = globalThis.fetch;
  beforeEach(() => {
    // VITE_MOCK は未設定・?mock=1 も無い環境なので実 fetch 経路を通る。
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("/api/v1 を前置して fetch する", async () => {
    const fetchMock = mockFetchOnce(200, { ok: true });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    await api("/meta");
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/meta", expect.objectContaining({ method: "GET" }));
  });

  it("成功時は JSON をそのまま返す", async () => {
    globalThis.fetch = mockFetchOnce(200, { hello: "world" }) as unknown as typeof fetch;
    const res = await api<{ hello: string }>("/x");
    expect(res.hello).toBe("world");
  });

  it("!ok のとき detail を message にした ApiError を投げる", async () => {
    globalThis.fetch = mockFetchOnce(400, { detail: "壊れています" }) as unknown as typeof fetch;
    await expect(api("/x")).rejects.toMatchObject({ message: "壊れています", status: 400 });
  });

  it("409 でも ApiError（status=409）になる", async () => {
    globalThis.fetch = mockFetchOnce(409, { detail: "拒否されました" }) as unknown as typeof fetch;
    try {
      await api("/x");
      throw new Error("should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(409);
    }
  });

  it("401 のときは登録した unauthorized ハンドラを呼ぶ", async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    globalThis.fetch = mockFetchOnce(401, { detail: "認証が必要です" }) as unknown as typeof fetch;
    await expect(api("/x")).rejects.toThrow();
    expect(handler).toHaveBeenCalled();
    setUnauthorizedHandler(() => {});
  });

  it("POST では body を JSON にして送る", async () => {
    const fetchMock = mockFetchOnce(200, { ok: true });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    await api("/tasks/task", { method: "POST", body: { title: "t" } });
    const call = fetchMock.mock.calls[0];
    expect(call[1].method).toBe("POST");
    expect(call[1].body).toBe(JSON.stringify({ title: "t" }));
  });
});
