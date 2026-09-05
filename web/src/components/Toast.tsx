/* manor web — board の banner() に相当。全画面共通の1行バナー。 */
import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";

export type ToastKind = "ok" | "warn" | "error" | "";

interface ToastState {
  message: string;
  kind: ToastKind;
}

interface ToastContextValue {
  toast: ToastState | null;
  show: (message: string, kind?: ToastKind, timeoutMs?: number) => void;
  clear: () => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<ToastState | null>(null);
  const timer = useRef<number | null>(null);

  const show = useCallback((message: string, kind: ToastKind = "", timeoutMs?: number) => {
    setToast({ message, kind });
    if (timer.current) window.clearTimeout(timer.current);
    if (timeoutMs) {
      timer.current = window.setTimeout(() => setToast(null), timeoutMs);
    }
  }, []);

  const clear = useCallback(() => setToast(null), []);

  return <ToastContext.Provider value={{ toast, show, clear }}>{children}</ToastContext.Provider>;
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast は ToastProvider の中で使ってください");
  return ctx;
}

export function ToastBanner() {
  const { toast } = useToast();
  return <div id="banner" className={"banner " + (toast?.kind || "")} hidden={!toast}>{toast?.message}</div>;
}
