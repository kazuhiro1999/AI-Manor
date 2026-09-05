/* manor web — `usePolling(path, ms=5000)`。board の `refresh()` の React 版。
 * 入力中（editing.ts）はデータの差し替えを保留する。ページが hidden のときはポーリングを
 * 止め、visibilitychange で復帰時にすぐ1回取る（board の visibilitychange ハンドラと同じ）。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";
import { isEditingWithin } from "./editing";
import { t } from "./i18n";

export interface PollingState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  changed: boolean; // 直前の fingerprint/内容と変わったか（同期チップに使う）
  reload: () => Promise<void>;
}

function fingerprintOf(value: unknown): string {
  if (value && typeof value === "object" && "fingerprint" in (value as Record<string, unknown>)) {
    return String((value as Record<string, unknown>).fingerprint);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(Date.now());
  }
}

export function usePolling<T>(
  path: string,
  ms = 5000,
  guardRef?: React.RefObject<HTMLElement>
): PollingState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [changed, setChanged] = useState(false);
  const lastFp = useRef<string | null>(null);
  const pending = useRef<T | null>(null);
  const timer = useRef<number | null>(null);

  const reload = useCallback(async () => {
    try {
      const res = await api<T>(path);
      const fp = fingerprintOf(res);
      const isEditing = guardRef ? isEditingWithin(guardRef.current) : false;
      if (isEditing) {
        // 入力中はデータだけ手元に置いて、描画（state 差し替え）は次回へ持ち越す。
        pending.current = res;
        setLoading(false);
        return;
      }
      const prev = lastFp.current;
      lastFp.current = fp;
      setData(res);
      setError(null);
      setLoading(false);
      setChanged(prev != null && prev !== fp);
      pending.current = null;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("errors.genericLoadFailed"));
      setLoading(false);
    }
  }, [path, guardRef]);

  useEffect(() => {
    let stopped = false;
    const tick = async () => {
      if (stopped) return;
      // 編集中でなくなっていれば、保留していたデータを反映する。
      if (pending.current != null && !(guardRef && isEditingWithin(guardRef.current))) {
        setData(pending.current);
        pending.current = null;
      }
      await reload();
    };
    tick();
    timer.current = window.setInterval(tick, ms);

    const onVisible = () => {
      if (!document.hidden) tick();
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      stopped = true;
      if (timer.current) window.clearInterval(timer.current);
      document.removeEventListener("visibilitychange", onVisible);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ms]);

  return { data, error, loading, changed, reload };
}
