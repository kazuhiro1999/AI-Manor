import type { ModuleDefinition } from "../../app/module";
import { NightPanel } from "./NightPanel";
import { ScreenHeader } from "../../components/ScreenHeader";
import { useT } from "../../app/i18n";

// NightPanel はここ（単独モジュール）と tasks の「記録」タブの両方から使われる
// （modules/tasks/Log.tsx）。ScreenHeader は NightPanel 自体には置かず、ここ（単独
// モジュールの入口）でだけ被せる——記録タブ側は tasks の ScreenHeader を既に持つので、
// NightPanel に直接置くと二重見出しになる。
function NightScreen() {
  const t = useT();
  return (
    <div className="view" id="view-night">
      <ScreenHeader title={t("nav.night")} description={t("night.description")} />
      <NightPanel />
    </div>
  );
}

export const nightModule: ModuleDefinition = {
  id: "night",
  title: "nav.night",
  description: "night.description",
  icon: "🌙",
  order: 10,
  routes: [{ index: true, element: <NightScreen /> }],
};
