import type { Board, Project, Task } from "../../app/types";
import { FoldBlock } from "../../components/FoldBlock";
import { TaskRow } from "./TaskRow";
import { doneDateGroups } from "./utils";

export function DoneDays({
  board,
  items,
  scope,
  pj,
  parentProject,
  onOpenCtx,
}: {
  board: Board;
  items: Task[];
  scope: string;
  pj?: string;
  parentProject?: Project | null;
  onOpenCtx: (id: string) => void;
}) {
  const groups = doneDateGroups(items);
  const latestId = groups.length && groups[0].items.length ? groups[0].items[0].id : null;
  return (
    <>
      {groups.map((g) => (
        <FoldBlock key={g.key} storageKey={`${scope}/${g.key}`} label={g.label} count={g.items.length}>
          {g.items.map((item) => (
            <TaskRow
              key={item.id}
              board={board}
              t={item}
              pj={pj}
              latest={item.id === latestId}
              parentProject={parentProject}
              onOpenCtx={onOpenCtx}
            />
          ))}
        </FoldBlock>
      ))}
    </>
  );
}
