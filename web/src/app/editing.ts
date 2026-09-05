/* manor web — 「入力中（IME 変換中を含む）は再描画しない」規則。
 * board の `isEditingWithin` / `initCompositionGuard`（src/manor/board/static/app.js）を
 * React hook にしたもの。フォーカスが input/textarea にあるか compositionstart 中なら
 * ポーリングで取得した新しいデータへの差し替えを保留する。
 */
import { useCallback, useEffect, useRef } from "react";

let composingElement: EventTarget | null = null;
let guardInstalled = false;

function installCompositionGuard(): void {
  if (guardInstalled || typeof document === "undefined") return;
  guardInstalled = true;
  document.addEventListener("compositionstart", (ev) => {
    composingElement = ev.target;
  });
  document.addEventListener("compositionend", (ev) => {
    if (composingElement === ev.target) composingElement = null;
  });
}

/** 指定した ref のコンテナ内に、フォーカスされた input/textarea か IME 変換中の要素が
 *  あるかを返す。 */
export function isEditingWithin(container: HTMLElement | null): boolean {
  if (!container || typeof document === "undefined") return false;
  const active = document.activeElement;
  const activeIsInput = !!(
    active &&
    (active.tagName === "INPUT" || active.tagName === "TEXTAREA") &&
    container.contains(active)
  );
  const composingWithin = !!(composingElement && container.contains(composingElement as Node));
  return activeIsInput || composingWithin;
}

/** コンテナの ref と「今このコンテナ内で編集中か」を返す hook。
 *  呼び出し側は、ポーリングで新データが来たとき `isEditing()` が true ならその周の
 *  再描画（state の差し替え）を止める。 */
export function useEditingGuard<T extends HTMLElement = HTMLDivElement>() {
  installCompositionGuard();
  const ref = useRef<T | null>(null);
  const isEditing = useCallback(() => isEditingWithin(ref.current), []);
  return { ref, isEditing };
}

/** キーボードショートカット等で「今どこかの input/textarea/contentEditable に
 *  フォーカスがあるか」を汎用に見る（board の keydown ガードと同じ規則）。 */
export function isEditingAnywhere(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  const tag = el?.tagName || "";
  return tag === "INPUT" || tag === "TEXTAREA" || !!el?.isContentEditable;
}

export function useCompositionGuardInstalled(): void {
  useEffect(() => {
    installCompositionGuard();
  }, []);
}
