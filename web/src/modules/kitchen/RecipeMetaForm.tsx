/* manor web — 「うちの値」の編集（ADR-015 D1・D4。栄養5つ・評価・メモ・タグ・favorite）。
 * `RecipeEditPage` の中で本体（RecipeFieldsEditor）の下に置く別フォーム——本体は
 * `PUT /recipes/{id}`、うちの値は `PUT /recipes/{id}/meta` と、送信先が最初から分かれている
 * （ADR-015 D1「本体とうちの値を分ける」）。
 *
 * 栄養の数値欄は**触った欄だけ** PUT の body に乗せる（`chef_recipe.set_meta` が「渡した欄
 * だけ」書き換える partial update のため）。触っていない欄まで毎回送ると、`kcal` 等を
 * 1つでも渡した扱いになって `nutrition_source` が自動で `manual` に落ちてしまい——
 * 「栄養を推定」の直後に無関係な評価だけ直して保存しても `estimated` が消えてしまう
 * （`chef/recipes.py` の `set_meta` の docstring 参照）。`baselineRef` に「今サーバに
 * 乗っている値」を持ち、そこから変わった欄だけを検算する。
 */
import { useRef, useState } from "react";
import { api, ApiError } from "../../app/api";
import { useToast } from "../../components/Toast";
import { useT } from "../../app/i18n";
import type { Recipe } from "../../app/types";

interface NutritionStrings {
  kcal: string;
  protein_g: string;
  fat_g: string;
  carb_g: string;
  salt_g: string;
}

function toStrings(meta: Recipe["meta"]): NutritionStrings {
  return {
    kcal: meta.kcal == null ? "" : String(meta.kcal),
    protein_g: meta.protein_g == null ? "" : String(meta.protein_g),
    fat_g: meta.fat_g == null ? "" : String(meta.fat_g),
    carb_g: meta.carb_g == null ? "" : String(meta.carb_g),
    salt_g: meta.salt_g == null ? "" : String(meta.salt_g),
  };
}

const NUTRITION_KEY_LABEL: { key: keyof NutritionStrings; labelKey: "kitchen.recipes.kcalLabel" | "kitchen.recipes.proteinLabel" | "kitchen.recipes.fatLabel" | "kitchen.recipes.carbLabel" | "kitchen.recipes.saltLabel" }[] = [
  { key: "kcal", labelKey: "kitchen.recipes.kcalLabel" },
  { key: "protein_g", labelKey: "kitchen.recipes.proteinLabel" },
  { key: "fat_g", labelKey: "kitchen.recipes.fatLabel" },
  { key: "carb_g", labelKey: "kitchen.recipes.carbLabel" },
  { key: "salt_g", labelKey: "kitchen.recipes.saltLabel" },
];

const NUTRITION_SOURCE_KEY: Record<string, "kitchen.recipes.nutritionSourceEstimated" | "kitchen.recipes.nutritionSourceManual" | "kitchen.recipes.nutritionSourceNone"> = {
  estimated: "kitchen.recipes.nutritionSourceEstimated",
  manual: "kitchen.recipes.nutritionSourceManual",
  "": "kitchen.recipes.nutritionSourceNone",
};

export function RecipeMetaForm({ recipe, onUpdated }: { recipe: Recipe; onUpdated: (recipe: Recipe) => void }) {
  const t = useT();
  const { show } = useToast();

  const [nutrition, setNutrition] = useState<NutritionStrings>(() => toStrings(recipe.meta));
  const baselineRef = useRef<NutritionStrings>(toStrings(recipe.meta));
  const [nutritionSource, setNutritionSource] = useState(recipe.meta.nutrition_source);
  const [tags, setTags] = useState<string[]>(recipe.meta.tags);
  const [newTag, setNewTag] = useState("");
  const [rating, setRating] = useState(recipe.meta.rating == null ? "" : String(recipe.meta.rating));
  const [memo, setMemo] = useState(recipe.meta.memo);
  const [favorite, setFavorite] = useState(recipe.meta.favorite);
  const [estimating, setEstimating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const addTag = () => {
    const v = newTag.trim();
    if (!v || tags.includes(v)) return;
    setTags([...tags, v]);
    setNewTag("");
  };
  const removeTag = (v: string) => setTags(tags.filter((x) => x !== v));

  const estimate = async () => {
    setError(null);
    setEstimating(true);
    try {
      const res = await api<{ meta: Recipe["meta"] }>(`/kitchen/recipes/${recipe.id}/estimate-nutrition`, { method: "POST" });
      const next = toStrings(res.meta);
      setNutrition(next);
      baselineRef.current = next; // 直後の保存で無関係な欄まで manual へ落とさないため
      setNutritionSource(res.meta.nutrition_source);
      onUpdated({ ...recipe, meta: { ...recipe.meta, ...res.meta } });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("errors.saveFailed", { reason: t("common.unknown") }));
    } finally {
      setEstimating(false);
    }
  };

  const submit = async () => {
    setError(null);
    setBusy(true);
    try {
      const body: Record<string, unknown> = { tags, memo, favorite, rating: rating.trim() === "" ? null : Number(rating) };
      for (const { key } of NUTRITION_KEY_LABEL) {
        if (nutrition[key] !== baselineRef.current[key]) {
          body[key] = nutrition[key].trim() === "" ? null : Number(nutrition[key]);
        }
      }
      const updated = await api<Recipe>(`/kitchen/recipes/${recipe.id}/meta`, { method: "PUT", body });
      baselineRef.current = toStrings(updated.meta);
      setNutritionSource(updated.meta.nutrition_source);
      show(t("kitchen.recipes.metaSaved"), "ok", 3000);
      onUpdated(updated);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("errors.saveFailed", { reason: t("common.unknown") }));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>{t("kitchen.recipes.ourValuesHeading")}</h2>
      </div>

      <h3>{t("kitchen.recipes.nutritionHeading")}</h3>
      <p className="panel-note">
        {t("kitchen.recipes.nutritionSourceLabel")}: {t(NUTRITION_SOURCE_KEY[nutritionSource] ?? "kitchen.recipes.nutritionSourceNone")}
      </p>
      <div className="form-inline">
        {NUTRITION_KEY_LABEL.map(({ key, labelKey }) => (
          <div className="form-row" style={{ maxWidth: 110 }} key={key}>
            <label htmlFor={`meta-${key}`}>{t(labelKey)}</label>
            <input
              id={`meta-${key}`}
              className="form-input"
              type="number"
              value={nutrition[key]}
              onChange={(e) => setNutrition({ ...nutrition, [key]: e.target.value })}
            />
          </div>
        ))}
      </div>
      <div className="form-actions">
        <button type="button" className="btn btn-small" disabled={estimating} onClick={estimate}>
          {t("kitchen.recipes.estimateButton")}
        </button>
      </div>

      <div className="form-row" style={{ marginTop: 10 }}>
        <label htmlFor="meta-rating">{t("kitchen.recipes.ratingLabel")}</label>
        <input id="meta-rating" className="form-input" style={{ maxWidth: 90 }} type="number" min={1} max={5} value={rating} onChange={(e) => setRating(e.target.value)} />
      </div>
      <div className="form-row">
        <label htmlFor="meta-memo">{t("kitchen.recipes.memoLabel")}</label>
        <textarea id="meta-memo" className="form-textarea" style={{ minHeight: 60 }} value={memo} onChange={(e) => setMemo(e.target.value)} />
      </div>
      <div className="form-row">
        <label htmlFor="meta-favorite">{t("kitchen.recipes.favoriteToggle")}</label>
        <label>
          <input id="meta-favorite" type="checkbox" checked={favorite} onChange={(e) => setFavorite(e.target.checked)} /> ★
        </label>
      </div>

      <div className="form-row">
        <label htmlFor="meta-tag-add">{t("kitchen.recipes.tagsLabel")}</label>
        <div className="setup-chips">
          {tags.map((tag) => (
            <button type="button" key={tag} className="chip chip-toggle" aria-pressed="true" onClick={() => removeTag(tag)}>
              {tag} ×
            </button>
          ))}
        </div>
        <div className="form-inline">
          <input
            id="meta-tag-add"
            className="form-input"
            placeholder={t("kitchen.recipes.tagAddPlaceholder")}
            value={newTag}
            onChange={(e) => setNewTag(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addTag();
              }
            }}
          />
          <button type="button" className="btn btn-small" onClick={addTag}>
            {t("common.add")}
          </button>
        </div>
      </div>

      {error && <div className="form-error">{error}</div>}
      <div className="form-actions">
        <button type="button" className="btn btn-primary" disabled={busy} onClick={submit}>
          {t("common.save")}
        </button>
      </div>
    </section>
  );
}
