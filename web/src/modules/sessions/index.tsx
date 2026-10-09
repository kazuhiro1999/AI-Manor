import type { ModuleDefinition } from "../../app/module";
import { SessionsScreen } from "./SessionsScreen";

export const sessionsModule: ModuleDefinition = {
  id: "sessions",
  title: "nav.sessions",
  description: "sessions.description",
  icon: "🖥",
  order: 1.5,
  routes: [{ index: true, element: <SessionsScreen /> }],
};
