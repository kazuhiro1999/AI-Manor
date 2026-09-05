/* manor web — モジュールの登録簿（ADR-005 D6）。
 * 各モジュールは `web/src/modules/<id>/index.tsx` が `ModuleDefinition` を export し、
 * ここに並べるだけで現れる。**表示と順序は `GET /api/v1/meta` の `modules` が正**——
 * この配列はその「有効なモジュールの実装が揃っている一覧」であり、実際にナビへ出す
 * 並びは App.tsx が meta.modules の order で並べ替える。
 */
import type { ModuleDefinition } from "./module";
import { dashboardModule } from "../modules/dashboard";
import { agentsModule } from "../modules/agents";
import { tasksModule } from "../modules/tasks";
import { kitchenModule } from "../modules/kitchen";
import { houseModule } from "../modules/house";
import { moneyModule } from "../modules/money";
import { secretaryModule } from "../modules/secretary";
import { rulesModule } from "../modules/rules";
import { importsModule } from "../modules/imports";
import { nightModule } from "../modules/night";
import { settingsModule } from "../modules/settings";
import { loginModule } from "../modules/login";
import { setupModule } from "../modules/setup";
import { extensionsModule } from "../modules/extensions";

export function buildRegistry(readOnly: boolean): ModuleDefinition[] {
  return [
    dashboardModule,
    agentsModule,
    tasksModule(readOnly),
    kitchenModule,
    houseModule,
    moneyModule,
    secretaryModule,
    rulesModule,
    importsModule,
    nightModule,
    settingsModule,
    loginModule,
    setupModule,
    extensionsModule,
  ];
}

export const MODULE_IDS = [
  "dashboard",
  "agents",
  "tasks",
  "kitchen",
  "house",
  "money",
  "secretary",
  "rules",
  "imports",
  "night",
  "settings",
  "login",
  "setup",
  "extensions",
] as const;
