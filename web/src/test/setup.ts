import "@testing-library/jest-dom/vitest";
import { beforeEach } from "vitest";
import { setLanguageSetting } from "../app/i18n/store";

// manor web の既存試験の大半は日本語の文字列で画面を探す（ADR-012 §3 D9・実装メモ参照）。
// jsdom の既定の navigator.language は "en-US" のことが多く、[manor] language の既定
// "auto" のまま何もしないと、試験が English 側で描画されて既存の assertion が軒並み
// 落ちる（i18n を入れる前には存在しなかった落とし方）。試験の既定言語を明示的に "ja" に
// 固定し、言語切り替えそのものを確かめる試験（i18n/*.test.ts・Settings.test.tsx の該当
// ケースなど）だけが自分で `setLanguageSetting` を呼んで上書きする。
beforeEach(() => {
  setLanguageSetting("ja");
});
