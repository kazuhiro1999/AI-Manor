/* manor web — 日ごとの畳み（board の renderDoneDays / renderFoldBlock の移植）。
 * 既定は閉じる。開閉は localStorage に覚える（`scope` が名前空間。リストとツリー・
 * プロジェクトごとに別々に覚えるため、呼び出し側が別々の scope を渡す）。
 */
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useT } from "../app/i18n";

const STORAGE_KEY = "manor-web.foldOpen";

function loadOpenSet(): Set<string> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return new Set();
    const arr = JSON.parse(raw);
    if (Array.isArray(arr)) return new Set(arr);
  } catch {
    /* 読めなくても空集合（既定=全部閉じる）で始める */
  }
  return new Set();
}

let sharedSet: Set<string> | null = null;
function getSharedSet(): Set<string> {
  if (!sharedSet) sharedSet = loadOpenSet();
  return sharedSet;
}
function saveSharedSet(): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(Array.from(getSharedSet())));
  } catch {
    /* 保存できなくても表示は続く */
  }
}

export function FoldBlock({
  storageKey,
  label,
  count,
  children,
  defaultOpen = false,
}: {
  storageKey: string;
  label: string;
  count: number;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  const t = useT();
  const set = getSharedSet();
  const [open, setOpen] = useState<boolean>(() => set.has(storageKey) || defaultOpen);

  useEffect(() => {
    if (defaultOpen && !set.has(storageKey)) {
      // defaultOpen だけの制御にとどめ、localStorage の記憶は上書きしない。
    }
  }, [storageKey, defaultOpen, set]);

  const toggle = useCallback(() => {
    setOpen((prev) => {
      const next = !prev;
      if (next) set.add(storageKey);
      else set.delete(storageKey);
      saveSharedSet();
      return next;
    });
  }, [storageKey, set]);

  return (
    <div className="done-days">
      <div className="done-day">
        <button type="button" className={"done-day-head" + (open ? " open" : "")} onClick={toggle} data-testid={`fold-${storageKey}`}>
          <span className="caret">▶</span>
          <span className="done-day-label">{label}</span>
          <span className="count">{t("component.foldBlock.count", { count })}</span>
        </button>
        <div className="rows done-day-body" hidden={!open}>
          {children}
        </div>
      </div>
    </div>
  );
}

export function isFoldOpen(storageKey: string): boolean {
  return getSharedSet().has(storageKey);
}
