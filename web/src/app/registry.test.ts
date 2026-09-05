import { describe, expect, it } from "vitest";
import { buildRegistry, MODULE_IDS } from "./registry";

describe("registry", () => {
  const registry = buildRegistry(false);

  it("契約どおり10モジュール（login含む）が揃っている", () => {
    expect(registry.map((m) => m.id).sort()).toEqual([...MODULE_IDS].sort());
  });

  it("各モジュールが ModuleDefinition の形を満たす（id/title/icon/order/routes）", () => {
    for (const m of registry) {
      expect(typeof m.id).toBe("string");
      expect(typeof m.title).toBe("string");
      expect(m.title.length).toBeGreaterThan(0);
      expect(typeof m.icon).toBe("string");
      expect(m.icon.length).toBeGreaterThan(0);
      expect(typeof m.order).toBe("number");
      expect(Array.isArray(m.routes)).toBe(true);
      expect(m.routes.length).toBeGreaterThan(0);
    }
  });

  // ADR-010 D7・§4:「登録簿を回して機械で検算する」——1つ足したときに description を
  // 書き忘れても、この試験が落ちて気づける（画面ごとの見出しの有無を、個々の画面試験で
  // 確かめるのではなく、ここで一括して検算する）。
  it("全モジュールが空でない description を持つ（ADR-010 D7: 画面の先頭に出す一行）", () => {
    for (const m of registry) {
      expect(typeof m.description).toBe("string");
      expect(m.description.trim().length).toBeGreaterThan(0);
    }
  });

  it("login は hideFromNav（ナビに出さない）", () => {
    const login = registry.find((m) => m.id === "login")!;
    expect(login.hideFromNav).toBe(true);
  });

  // ADR-011 D1: 設定はサイドバーから外し、右上の歯車アイコンから開く。
  // ルート自体（/settings）は生かしたいので、モジュールは登録簿に残したまま
  // hideFromNav だけを立てる（login と同じ形）。
  it("settings は hideFromNav（サイドバーには出ない。右上の歯車から開く）", () => {
    const settings = registry.find((m) => m.id === "settings")!;
    expect(settings.hideFromNav).toBe(true);
  });

  it("dashboard がサイドバー先頭、agents が2番目（ADR-011 D1 の並び）", () => {
    const withoutHidden = registry.filter((m) => !m.hideFromNav).sort((a, b) => a.order - b.order);
    expect(withoutHidden[0].id).toBe("dashboard");
    expect(withoutHidden[1].id).toBe("agents");
    expect(withoutHidden[2].id).toBe("tasks");
  });

  it("tasks モジュールの badge は board.counts.pending を返す", () => {
    const tasks = registry.find((m) => m.id === "tasks")!;
    expect(tasks.badge).toBeTruthy();
    const board = { counts: { pending: 3 } } as never;
    expect(tasks.badge!(null, board)).toBe(3);
    expect(tasks.badge!(null, undefined)).toBe(null);
  });

  it("readOnly を渡しても tasks モジュールの id/title は変わらない", () => {
    const ro = buildRegistry(true).find((m) => m.id === "tasks")!;
    expect(ro.id).toBe("tasks");
    // ADR-012 §3 D10: title は i18n のキー（"nav.tasks"）を持つ。React の外で作られる
    // ModuleDefinition は解決した文字列ではなくキーのまま持ち、Nav.tsx・ScreenHeader が
    // 描画のたびに useT() で訳す（言語を切り替えても追従するように）。
    expect(ro.title).toBe("nav.tasks");
  });
});
