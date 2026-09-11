/* manor web — レシピ帳の内部経路（ADR-015 D4）。台所モジュールの `recipes/*` に1つだけ
 * 登録し、ここでさらに枝分かれさせる——`modules/tasks/Plan.tsx`（`plan/*`）と同じ立て付け。
 */
import { Route, Routes } from "react-router-dom";
import { RecipeList } from "./RecipeList";
import { RecipeNewPage } from "./RecipeNewPage";
import { RecipeDetail } from "./RecipeDetail";
import { RecipeEditPage } from "./RecipeEditPage";

export function RecipesRouter() {
  return (
    <Routes>
      <Route index element={<RecipeList />} />
      <Route path="new" element={<RecipeNewPage />} />
      <Route path=":id" element={<RecipeDetail />} />
      <Route path=":id/edit" element={<RecipeEditPage />} />
    </Routes>
  );
}
