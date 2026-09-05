/* manor web — 画面モジュールの契約（ADR-005 §3）。 */
import type { RouteObject } from "react-router-dom";
import type { Meta } from "./types";
import type { TranslationKey } from "./i18n";

export type ModuleId =
  | "dashboard"
  | "agents"
  | "tasks"
  | "kitchen"
  | "house"
  | "money"
  | "secretary"
  | "rules"
  | "imports"
  | "night"
  | "settings"
  | "login"
  | "setup"
  | "extensions";

export interface ModuleDefinition {
  id: ModuleId;
  // ADR-012 §3 D10: ここに入れるのは表示文字列そのものではなく **i18n のキー**
  // （例: "nav.tasks"）。ModuleDefinition はモジュール読み込み時（React の外）に
  // 一度だけ作られるので、解決した文字列を持たせると言語を切り替えても更新されない
  // ——キーのまま持ち、使う側（Nav.tsx・各画面の ScreenHeader 呼び出し）が
  // `useT()` で読むたびに訳す。
  title: TranslationKey; // ナビの表示名の i18n キー。ScreenHeader の画面名にも使う
  // ADR-010 D7: 画面の先頭に出す一行の i18n キー。「ここで何をするか」。README を
  // 読ませない——その場に書く。全モジュールが持つ（registry.test.ts が空でないことを検算する）。
  description: TranslationKey;
  icon: string; // 1〜2文字（絵文字か記号。画像は使わない）
  order: number; // ナビの順（meta.modules の order を優先）
  routes: RouteObject[]; // React Router の経路。先頭が index
  badge?: (meta: Meta | null, data?: unknown) => number | null; // ナビの件数バッジ
  // 滞留3日以上の decision 件数など、赤で強調するバッジ（board の「滞留Nの赤バッジ」相当。
  // docs/board_parity.md §5「サイドバーの件数バッジ」）。0/null なら出さない。
  staleBadge?: (meta: Meta | null, data?: unknown) => number | null;
  hideFromNav?: boolean; // login のようにナビに出さないモジュール
}

// `GET /api/v1/meta` の `modules[].title` はバックエンドが持つ日本語の表示名で、
// フロントの i18n を経由しない（`module_list()`（`src/manor/web/_common.py`）が返す
// 生の文字列）。id は共通なので、画面でこの生の title をそのまま出す代わりに、この
// 対応表で `nav.*` キーへ引き直して訳す（settings/index.tsx の「モジュールの並び」・
// modules/agents/index.tsx の行き先リンクが使う）。
export const MODULE_TITLE_KEY: Record<ModuleId, TranslationKey> = {
  dashboard: "nav.dashboard",
  agents: "nav.agents",
  tasks: "nav.tasks",
  kitchen: "nav.kitchen",
  house: "nav.house",
  money: "nav.money",
  secretary: "nav.secretary",
  rules: "nav.rules",
  imports: "nav.imports",
  night: "nav.night",
  settings: "nav.settings",
  login: "nav.login",
  setup: "nav.setup",
  extensions: "nav.extensions",
};
