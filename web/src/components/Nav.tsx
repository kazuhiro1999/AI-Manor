import { NavLink } from "react-router-dom";
import type { ModuleDefinition } from "../app/module";
import type { Meta } from "../app/types";
import { useT } from "../app/i18n";

export function Nav({
  modules,
  meta,
  boardData,
}: {
  modules: ModuleDefinition[];
  meta: Meta | null;
  boardData?: unknown;
}) {
  const t = useT();
  return (
    <nav className="sidenav" id="sidenav" aria-label={t("app.nav.ariaLabel")}>
      {modules.map((m, i) => {
        const badge = m.badge ? m.badge(meta, boardData) : null;
        const stale = m.staleBadge ? m.staleBadge(meta, boardData) : null;
        return (
          <NavLink
            key={m.id}
            to={`/${m.id}`}
            className={({ isActive }) => "nav-item" + (isActive ? " active" : "")}
            data-module={m.id}
          >
            <span className="nav-key">{i + 1 <= 9 ? i + 1 : ""}</span>
            <span className="nav-text">
              {m.icon} {t(m.title)}
            </span>
            {(badge != null || (stale != null && stale > 0)) && (
              <span className="nav-badges">
                {stale != null && stale > 0 && (
                  <span className="nav-count nav-count-stale" title={t("app.nav.staleBadgeTitle")}>
                    {t("app.nav.staleBadgeCount", { count: stale })}
                  </span>
                )}
                {badge != null && <span className={"nav-count" + (badge === 0 ? " zero" : "")}>{badge}</span>}
              </span>
            )}
          </NavLink>
        );
      })}
      <p className="nav-hint">{t("app.nav.hint")}</p>
    </nav>
  );
}
