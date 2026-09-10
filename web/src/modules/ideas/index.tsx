import { useState } from "react";
import type { ModuleDefinition } from "../../app/module";
import { ScreenHeader } from "../../components/ScreenHeader";
import { useT } from "../../app/i18n";
import { IdeaForm } from "./IdeaForm";
import { IdeaList } from "./IdeaList";

function IdeasScreen() {
  const t = useT();
  const [reloadKey, setReloadKey] = useState(0);
  return (
    <div className="view" id="view-ideas">
      <ScreenHeader title={t("nav.ideas")} description={t("ideas.description")} />
      <IdeaForm onCreated={() => setReloadKey((k) => k + 1)} />
      <IdeaList reloadKey={reloadKey} />
    </div>
  );
}

export const ideasModule: ModuleDefinition = {
  id: "ideas",
  title: "nav.ideas",
  description: "ideas.description",
  icon: "💡",
  order: 4,
  routes: [{ index: true, element: <IdeasScreen /> }],
};
