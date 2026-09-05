/* manor web — API v1 の薄い包み（ADR-005 §3）。
 * `/api/v1` を前置し、JSON で読み書きする。401 は /#/login へ、409/400 は ApiError にする。
 * `VITE_MOCK=1`（ビルド時の環境変数）か `?mock=1`（実行時のクエリ）のときは
 * バックエンドが無くても動くよう mock.ts の合成データを返す。
 */
import { t } from "./i18n";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function isMockMode(): boolean {
  if (import.meta.env.VITE_MOCK === "1") return true;
  if (typeof window === "undefined") return false;
  try {
    return new URLSearchParams(window.location.search).get("mock") === "1";
  } catch {
    return false;
  }
}

export const MOCK_MODE = isMockMode();

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn;
}

export interface ApiOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
}

async function realApi<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const res = await fetch("/api/v1" + path, {
    method: options.method || "GET",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });

  let payload: unknown = null;
  try {
    payload = await res.json();
  } catch {
    payload = null;
  }

  if (res.status === 401) {
    if (onUnauthorized) onUnauthorized();
    throw new ApiError(t("errors.unauthorized"), 401);
  }

  if (!res.ok) {
    const detail =
      payload && typeof payload === "object" && "detail" in payload
        ? String((payload as { detail: unknown }).detail)
        : `HTTP ${res.status}`;
    throw new ApiError(detail, res.status);
  }

  return payload as T;
}

export async function api<T>(path: string, options: ApiOptions = {}): Promise<T> {
  if (MOCK_MODE) {
    const { mockApi } = await import("./mock");
    return mockApi<T>(path, options);
  }
  return realApi<T>(path, options);
}

/* multipart 送信（imports のプレビュー・取り込み）は fetch を直接使う（JSON 包みの外）。 */
export async function apiUpload<T>(path: string, form: FormData): Promise<T> {
  if (MOCK_MODE) {
    const { mockApiUpload } = await import("./mock");
    return mockApiUpload<T>(path, form);
  }
  const res = await fetch("/api/v1" + path, {
    method: "POST",
    credentials: "include",
    body: form,
  });
  let payload: unknown = null;
  try {
    payload = await res.json();
  } catch {
    payload = null;
  }
  if (res.status === 401) {
    if (onUnauthorized) onUnauthorized();
    throw new ApiError(t("errors.unauthorized"), 401);
  }
  if (!res.ok) {
    const detail =
      payload && typeof payload === "object" && "detail" in payload
        ? String((payload as { detail: unknown }).detail)
        : `HTTP ${res.status}`;
    throw new ApiError(detail, res.status);
  }
  return payload as T;
}
