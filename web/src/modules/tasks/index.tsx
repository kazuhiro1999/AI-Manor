import type { ModuleDefinition } from "../../app/module";
import type { Board } from "../../app/types";
import { Judge } from "./Judge";
import { Running } from "./Running";
import { Plan } from "./Plan";
import { Log } from "./Log";
import { TaskForm } from "./TaskForm";
import { TasksLayout } from "./TasksLayout";

export function tasksModule(readOnly: boolean): ModuleDefinition {
  return {
    id: "tasks",
    title: "nav.tasks",
    description: "tasks.description",
    icon: "📋",
    order: 3,
    routes: [
      {
        element: <TasksLayout />,
        children: [
          { index: true, element: <Judge readOnly={readOnly} /> },
          { path: "judge", element: <Judge readOnly={readOnly} /> },
          { path: "running", element: <Running readOnly={readOnly} /> },
          { path: "plan/*", element: <Plan readOnly={readOnly} /> },
          { path: "log/*", element: <Log readOnly={readOnly} /> },
        ],
      },
      { path: "new", element: <TaskForm /> },
    ],
    badge: (_meta, data) => {
      const board = data as Board | undefined;
      if (!board) return null;
      return board.counts?.pending ?? null;
    },
    // board の「滞留Nの赤バッジ」（docs/board_parity.md §5）: 3日以上滞留した open な
    // decision の件数。0件なら Nav 側で出さない。
    staleBadge: (_meta, data) => {
      const board = data as Board | undefined;
      if (!board) return null;
      return (board.pending || []).filter((p) => p.stale).length;
    },
  };
}
