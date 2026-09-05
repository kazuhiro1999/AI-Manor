import { NavLink, Outlet } from "react-router-dom";
import { ScreenHeader } from "../../components/ScreenHeader";
import { useT } from "../../app/i18n";

/** tasks モジュール内の4画面（要対応／AIの進行中／計画／記録）の切り替え。
 * board のサイドバーの1〜4番に相当する内部ナビ。 */
export function TasksLayout() {
  const t = useT();
  return (
    <div>
      <ScreenHeader title={t("nav.tasks")} description={t("tasks.description")} />
      <div className="seg log-tabs" role="tablist" aria-label={t("tasks.tabsAriaLabel")}>
        <NavLink to="judge" className="seg-btn plan-tab log-tab">
          {t("tasks.tab.judge")}
        </NavLink>
        <NavLink to="running" className="seg-btn plan-tab log-tab">
          {t("tasks.tab.running")}
        </NavLink>
        <NavLink to="plan" className="seg-btn plan-tab log-tab">
          {t("tasks.tab.plan")}
        </NavLink>
        <NavLink to="log" className="seg-btn plan-tab log-tab">
          {t("tasks.tab.log")}
        </NavLink>
      </div>
      <Outlet />
    </div>
  );
}
