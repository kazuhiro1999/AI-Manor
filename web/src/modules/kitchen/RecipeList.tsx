/* manor web — レシピ帳の一覧（ADR-015 D4 `/kitchen/recipes`）。
 * 検索（題名・材料名）・タグの chip・お気に入りトグル・並び替え。一覧 API
 * （`GET /kitchen/recipes`）には材料名や last_cooked_at が乗らないため、詳細もあわせて
 * 読んで補う（`recipeShared.fetchRecipesWithDetails`。個人のレシピ帳の規模を前提にした
 * 判断——docs/design/ADR-015_recipe_book.md への報告に書いた）。
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../app/api";
import { ScreenHeader } from "../../components/ScreenHeader";
import { useT } from "../../app/i18n";
import type { Recipe, RecipeListItem } from "../../app/types";
import { compareByRecentCooked, fetchRecipesWithDetails, recipeMatchesQuery } from "./recipeShared";

type SortMode = "recent" | "new";

export function RecipeList() {
  const t = useT();
  const [items, setItems] = useState<RecipeListItem[] | null>(null);
  const [details, setDetails] = useState<Record<number, Recipe>>({});
  const [error, setError] = useState<string | null>(null);

  const [q, setQ] = useState("");
  const [tag, setTag] = useState<string | null>(null);
  const [favoriteOnly, setFavoriteOnly] = useState(false);
  const [sortMode, setSortMode] = useState<SortMode>("new");

  useEffect(() => {
    let cancelled = false;
    fetchRecipesWithDetails()
      .then((res) => {
        if (cancelled) return;
        setItems(res.items);
        setDetails(res.details);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : t("errors.genericLoadFailed"));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (error) {
    return (
      <div className="view" id="view-kitchen-recipes">
        <ScreenHeader title={t("kitchen.recipes.listHeading")} description={t("kitchen.recipes.description")} />
        <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>
      </div>
    );
  }
  if (!items) {
    return (
      <div className="view" id="view-kitchen-recipes">
        <ScreenHeader title={t("kitchen.recipes.listHeading")} description={t("kitchen.recipes.description")} />
        <p className="panel-note">{t("common.loading")}</p>
      </div>
    );
  }

  const allTags = Array.from(new Set(items.flatMap((it) => it.tags))).sort();

  const filtered = items
    .filter((it) => !favoriteOnly || it.favorite)
    .filter((it) => !tag || it.tags.includes(tag))
    .filter((it) => recipeMatchesQuery(it, q, details));
  const sorted = sortMode === "recent" ? [...filtered].sort((a, b) => compareByRecentCooked(a, b, details)) : filtered;

  return (
    <div className="view" id="view-kitchen-recipes">
      <ScreenHeader title={t("kitchen.recipes.listHeading")} description={t("kitchen.recipes.description")} />

      <section className="panel">
        <div className="form-inline">
          <input
            className="form-input"
            placeholder={t("kitchen.recipes.searchPlaceholder")}
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <button
            type="button"
            className="chip chip-toggle"
            aria-pressed={favoriteOnly}
            onClick={() => setFavoriteOnly((v) => !v)}
          >
            {t("kitchen.recipes.favoriteOnly")}
          </button>
          <div className="seg" role="group">
            <button type="button" className="seg-btn" aria-pressed={sortMode === "recent"} onClick={() => setSortMode("recent")}>
              {t("kitchen.recipes.sortRecent")}
            </button>
            <button type="button" className="seg-btn" aria-pressed={sortMode === "new"} onClick={() => setSortMode("new")}>
              {t("kitchen.recipes.sortNew")}
            </button>
          </div>
          <Link className="btn btn-primary btn-small" to="/kitchen/recipes/new" style={{ marginLeft: "auto" }}>
            {t("kitchen.recipes.addButton")}
          </Link>
        </div>
        {allTags.length > 0 && (
          <div className="setup-chips" style={{ marginTop: 8 }}>
            <button type="button" className="chip chip-toggle" aria-pressed={tag === null} onClick={() => setTag(null)}>
              {t("kitchen.recipes.allTags")}
            </button>
            {allTags.map((tg) => (
              <button key={tg} type="button" className="chip chip-toggle" aria-pressed={tag === tg} onClick={() => setTag(tg)}>
                {tg}
              </button>
            ))}
          </div>
        )}
      </section>

      <section className="panel">
        {!sorted.length && <p className="panel-note">{t("kitchen.recipes.empty")}</p>}
        <div className="rows">
          {sorted.map((it) => (
            <Link className="row-item" to={`/kitchen/recipes/${it.id}`} key={it.id}>
              {it.hero_image ? (
                <img src={it.hero_image} alt="" className="recipe-hero-thumb" />
              ) : (
                <span className="recipe-hero-thumb recipe-hero-placeholder" aria-label={t("kitchen.recipes.noPhoto")} />
              )}
              <span className="row-title">{it.title}</span>
              {it.total_minutes != null && <span className="row-id">{t("format.minutes", { n: it.total_minutes })}</span>}
              {it.tags.map((tg) => (
                <span className="chip" key={tg}>
                  {tg}
                </span>
              ))}
              <span aria-label={t("kitchen.recipes.favoriteToggle")}>{it.favorite ? "★" : "☆"}</span>
              <span className="row-id">{t("kitchen.recipes.timesCooked", { n: it.times_cooked })}</span>
            </Link>
          ))}
        </div>
      </section>
    </div>
  );
}
