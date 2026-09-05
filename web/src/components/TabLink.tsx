import { NavLink } from "react-router-dom";

/** 計画・記録の「タブ」（React Router のサブ経路をタブの見た目にする）。 */
export function TabLink({ to, label, end }: { to: string; label: string; end?: boolean }) {
  return (
    <NavLink
      to={to}
      end={end}
      role="tab"
      className={({ isActive }) => "seg-btn plan-tab log-tab"}
      style={({ isActive }) =>
        isActive ? { background: "var(--accent)", color: "#201800", fontWeight: 700 } : undefined
      }
    >
      {label}
    </NavLink>
  );
}
