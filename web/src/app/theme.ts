/* manor web — テーマ（端末に合わせる／明るい／暗い）。localStorage に残す。
 * board の THEME_KEY / applyTheme と同じ規則。サーバへは送らない。
 */
import { useCallback, useEffect, useState } from "react";

export type Theme = "system" | "light" | "dark";
export const THEMES: Theme[] = ["system", "light", "dark"];
const THEME_KEY = "manor-web.theme";

export function readTheme(): Theme {
  try {
    const v = localStorage.getItem(THEME_KEY);
    return (THEMES as string[]).includes(v || "") ? (v as Theme) : "system";
  } catch {
    return "system";
  }
}

function writeTheme(value: Theme): void {
  try {
    localStorage.setItem(THEME_KEY, value);
  } catch {
    /* 保存できなくても表示は続く */
  }
}

export function applyThemeToDocument(value: Theme): void {
  if (typeof document === "undefined") return;
  if (value === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", value);
}

export function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setThemeState] = useState<Theme>(() => readTheme());

  useEffect(() => {
    applyThemeToDocument(theme);
  }, [theme]);

  const setTheme = useCallback((t: Theme) => {
    setThemeState(t);
    writeTheme(t);
  }, []);

  return [theme, setTheme];
}
