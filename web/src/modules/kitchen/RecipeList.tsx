/* manor web — レシピ帳の一覧（ADR-015 D4 `/kitchen/recipes`。§6 D4'・D9 で全面差し替え）。
 * 検索（題名・材料名）・分類3軸／タグの chip・お気に入りトグル・並び替えは、すべてサーバ
 * （`GET /kitchen/recipes`）へクエリとして投げる——旧版のように一覧に無い欄を detail の
 * 並行取得で補うことはもうしない（一覧 API の応答自体に絞り・並びに要る値が乗るようになった。
 * `recipeShared.fetchRecipeList` 参照）。
 *
 * 本体はグリッド（§6 D4'「写真いっぱいに敷き、下部に題名を白文字で重ねる」）。
 * 絞り込みが1つも効いていないときだけ、参考画面（主人の指定）のように**分類ごとの見出し＋
 * 横スクロールの列**にする——絞ってしまえば分類の見出しに意味が無いので、そのときは
 * 1つのグリッドへ切り替える。
 *
 * ADR-023 D5: 家族の再生リストの **YouTube の動画**も同じ絞り（検索語・分類3軸）で並べる
 * （`GET /kitchen/videos`）。カードに YouTube の印を付け、押すと YouTube を開く（工程ごとの図は
 * 用意できないので、作るときは動画を見る——主人の裁定）。読めるものは「下書きにする」で取り込み画面へ。
 * タグ・お気に入りで絞っているときは動画を出さない（動画はどちらも持たない）。
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../../app/api";
import { ScreenHeader } from "../../components/ScreenHeader";
import { useT } from "../../app/i18n";
import type { RecipeFacets, RecipeFacetValue, RecipeListItem, RecipeVideo, RecipeVideosPayload } from "../../app/types";
import { CATEGORY_OPTIONS, fetchRecipeFacets, fetchRecipeList, type RecipeSortMode } from "./recipeShared";

function RecipeCard({ item }: { item: RecipeListItem }) {
  const t = useT();
  return (
    <div className="recipe-card-wrap">
      <Link className="recipe-card" to={`/kitchen/recipes/${item.id}`}>
        {item.hero_image ? (
          <img src={item.hero_image} alt="" loading="lazy" />
        ) : (
          <span className="recipe-card-placeholder" aria-label={t("kitchen.recipes.noPhoto")}>
            {t("kitchen.recipes.noPhoto")}
          </span>
        )}
        {item.favorite && (
          <span className="recipe-card-fav" aria-label={t("kitchen.recipes.favoriteToggle")}>
            ★
          </span>
        )}
        <span className="recipe-card-title">{item.title}</span>
      </Link>
      <div className="recipe-card-meta">
        {item.total_minutes != null && <span>{t("format.minutes", { n: item.total_minutes })}</span>}
        {item.kcal != null && <span>{t("kitchen.recipes.kcalPerServing", { n: item.kcal })}</span>}
        <span>{t("kitchen.recipes.timesCookedShort", { n: item.times_cooked })}</span>
      </div>
    </div>
  );
}

/** YouTube の動画1本（ADR-023 D5）。押すと YouTube を新しいタブで開く。 */
function VideoCard({ item }: { item: RecipeVideo }) {
  const t = useT();
  const minutes = item.seconds != null ? Math.max(1, Math.round(item.seconds / 60)) : null;
  const matched = item.matched.find((m) => m.field !== "title");
  return (
    <div className="recipe-card-wrap recipe-video" data-video-id={item.video_id}>
      <a className="recipe-card" href={item.url} target="_blank" rel="noreferrer" title={item.title}>
        {item.thumbnail_url ? (
          <img src={item.thumbnail_url} alt="" loading="lazy" />
        ) : (
          <span className="recipe-card-placeholder">{t("kitchen.recipes.noPhoto")}</span>
        )}
        <span
          className="recipe-card-fav"
          style={{ left: 6, right: "auto", background: "#e00", borderRadius: 4, padding: "0 5px", fontSize: 10.5, fontWeight: 700 }}
        >
          ▶ YouTube
        </span>
        <span className="recipe-card-title">{item.title}</span>
      </a>
      <div className="recipe-card-meta">
        {item.seconds != null && (
          <span>{item.seconds <= 60 ? t("kitchen.videos.seconds", { n: item.seconds }) : t("format.minutes", { n: minutes ?? 1 })}</span>
        )}
        {item.channel && <span>{item.channel}</span>}
      </div>
      <div className="recipe-card-meta">
        {item.sources.map((src) => (
          <span key={`${src.user_id}-${src.playlist_title}`}>{t("kitchen.videos.source", { user: src.user_name, playlist: src.playlist_title })}</span>
        ))}
      </div>
      {matched && (
        <div className="recipe-card-meta">
          <span>{t(matched.field === "ingredient" ? "kitchen.videos.matchedIngredient" : "kitchen.videos.matchedTag", { text: matched.text })}</span>
        </div>
      )}
      <div className="recipe-card-meta">
        <Link to={`/kitchen/recipes/new?url=${encodeURIComponent(item.url)}`}>
          {t(item.has_recipe ? "kitchen.videos.toDraft" : "kitchen.videos.toDraftEmpty")}
        </Link>
      </div>
    </div>
  );
}

function FacetRow({
  label,
  values,
  selected,
  onSelect,
}: {
  label: string;
  values: RecipeFacetValue[];
  selected: string | null;
  onSelect: (v: string | null) => void;
}) {
  const t = useT();
  if (!values.length) return null;
  return (
    <div className="setup-chips" style={{ marginTop: 8, alignItems: "center" }}>
      <span className="row-id" style={{ marginRight: 2 }}>
        {label}
      </span>
      <button type="button" className="chip chip-toggle" aria-pressed={selected === null} onClick={() => onSelect(null)}>
        {t("kitchen.recipes.allTags")}
      </button>
      {values.map((f) => (
        <button
          key={f.value}
          type="button"
          className="chip chip-toggle"
          aria-pressed={selected === f.value}
          onClick={() => onSelect(selected === f.value ? null : f.value)}
        >
          {f.value}（{f.count}）
        </button>
      ))}
    </div>
  );
}

export function RecipeList() {
  const t = useT();
  const [items, setItems] = useState<RecipeListItem[] | null>(null);
  const [facets, setFacets] = useState<RecipeFacets | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [q, setQ] = useState("");
  const [tag, setTag] = useState<string | null>(null);
  const [category, setCategory] = useState<string | null>(null);
  const [mainIngredient, setMainIngredient] = useState<string | null>(null);
  const [cuisine, setCuisine] = useState<string | null>(null);
  const [favoriteOnly, setFavoriteOnly] = useState(false);
  const [sortMode, setSortMode] = useState<RecipeSortMode>("recent");
  const [videos, setVideos] = useState<RecipeVideosPayload | null>(null);
  const [videoTick, setVideoTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    fetchRecipeFacets()
      .then((res) => {
        if (!cancelled) setFacets(res);
      })
      .catch(() => {
        // chip の件数が出ないだけ——一覧そのものは出せるので致命的にしない。
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchRecipeList({ q, tag, favorite: favoriteOnly, category, main_ingredient: mainIngredient, cuisine, sort: sortMode })
      .then((res) => {
        if (cancelled) return;
        setItems(res);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : t("errors.genericLoadFailed"));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, tag, favoriteOnly, category, mainIngredient, cuisine, sortMode]);

  // ADR-023 D5: 同じ絞りで YouTube の動画も引く。落ちてもレシピの一覧は壊さない。
  const videosOff = !!tag || favoriteOnly;
  useEffect(() => {
    if (videosOff) {
      setVideos(null);
      return;
    }
    let cancelled = false;
    const query = new URLSearchParams();
    if (q.trim()) query.set("q", q.trim());
    if (category) query.set("category", category);
    if (mainIngredient) query.set("main_ingredient", mainIngredient);
    if (cuisine) query.set("cuisine", cuisine);
    const qs = query.toString();
    api<RecipeVideosPayload>(`/kitchen/videos${qs ? `?${qs}` : ""}`)
      .then((res) => {
        if (!cancelled) setVideos(res);
      })
      .catch(() => {
        if (!cancelled) setVideos(null);
      });
    return () => {
      cancelled = true;
    };
  }, [q, category, mainIngredient, cuisine, videosOff, videoTick]);

  // 同期が走っている間は 3 秒おきに読み直す（終われば止まる）。
  useEffect(() => {
    if (!videos?.sync.running) return;
    const timer = window.setTimeout(() => setVideoTick((n) => n + 1), 3000);
    return () => window.clearTimeout(timer);
  }, [videos]);

  const syncVideos = async () => {
    try {
      await api("/kitchen/videos/sync", { method: "POST" });
    } finally {
      setVideoTick((n) => n + 1);
    }
  };

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

  // §6 D4'「絞りが無いときは分類ごとの列、絞りがあるときは1つのグリッド」。
  const hasFilter = !!q.trim() || !!tag || favoriteOnly || !!category || !!mainIngredient || !!cuisine;

  const groups: { key: string; label: string; rows: RecipeListItem[] }[] = [];
  if (!hasFilter) {
    for (const c of CATEGORY_OPTIONS) {
      const rows = items.filter((it) => it.category === c);
      if (rows.length) groups.push({ key: c, label: c, rows });
    }
    const uncategorized = items.filter((it) => !CATEGORY_OPTIONS.includes(it.category as (typeof CATEGORY_OPTIONS)[number]));
    if (uncategorized.length) groups.push({ key: "__none", label: t("kitchen.recipes.uncategorizedHeading"), rows: uncategorized });
  }

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
            <button type="button" className="seg-btn" aria-pressed={sortMode === "cooked"} onClick={() => setSortMode("cooked")}>
              {t("kitchen.recipes.sortRecent")}
            </button>
            <button type="button" className="seg-btn" aria-pressed={sortMode === "recent"} onClick={() => setSortMode("recent")}>
              {t("kitchen.recipes.sortNew")}
            </button>
            <button type="button" className="seg-btn" aria-pressed={sortMode === "title"} onClick={() => setSortMode("title")}>
              {t("kitchen.recipes.sortTitle")}
            </button>
          </div>
          <Link className="btn btn-primary btn-small" to="/kitchen/recipes/new" style={{ marginLeft: "auto" }}>
            {t("kitchen.recipes.addButton")}
          </Link>
          {/* ADR-016 D4・ADR-018 D6: 動画リストと献立への行き来（料理長の持ち場の中で
              何枚かを往復する。どちらも別ページなので、行き来はここに並べて置く）。 */}
          <Link className="btn btn-small" to="/kitchen/menu">
            {t("kitchen.menu.link")}
          </Link>
          <Link className="btn btn-small" to="/kitchen/media">
            {t("kitchen.recipes.mediaLink")}
          </Link>
        </div>

        {facets && (
          <>
            <FacetRow label={t("kitchen.recipes.filterCategoryLabel")} values={facets.category} selected={category} onSelect={setCategory} />
            <FacetRow
              label={t("kitchen.recipes.filterMainIngredientLabel")}
              values={facets.main_ingredient}
              selected={mainIngredient}
              onSelect={setMainIngredient}
            />
            <FacetRow label={t("kitchen.recipes.filterCuisineLabel")} values={facets.cuisine} selected={cuisine} onSelect={setCuisine} />
            <FacetRow label={t("kitchen.recipes.filterTagsLabel")} values={facets.tags} selected={tag} onSelect={setTag} />
          </>
        )}
      </section>

      {videos?.configured && (
        <section className="panel" id="recipe-videos-sync">
          <div className="form-inline">
            <span className="panel-note">
              {videos.sync.running
                ? t("kitchen.videos.syncing")
                : videos.sync.synced_at
                  ? t("kitchen.videos.lastSync", { at: videos.sync.synced_at.replace("T", " ").slice(0, 16) })
                  : t("kitchen.videos.neverSynced")}
            </span>
            <button type="button" className="btn btn-small" disabled={videos.sync.running} onClick={syncVideos}>
              {t("kitchen.videos.syncButton")}
            </button>
          </div>
          {videos.sync.failed.length > 0 && (
            <p className="panel-note">{t("kitchen.videos.syncFailed", { reason: videos.sync.failed.map((f) => f.reason).join(", ") })}</p>
          )}
        </section>
      )}

      <section className="panel">
        {!items.length && !(videos?.items.length) && <p className="panel-note">{t("kitchen.recipes.empty")}</p>}
        {hasFilter ? (
          <div className="recipe-grid">
            {items.map((it) => (
              <RecipeCard item={it} key={it.id} />
            ))}
            {(videos?.items ?? []).map((v) => (
              <VideoCard item={v} key={v.video_id} />
            ))}
          </div>
        ) : (
          <div className="recipe-category-groups">
            {groups.map((g) => (
              <div key={g.key}>
                <div className="recipe-category-head">{g.label}</div>
                <div className="recipe-category-scroll">
                  {g.rows.map((it) => (
                    <RecipeCard item={it} key={it.id} />
                  ))}
                </div>
              </div>
            ))}
            {(videos?.items.length ?? 0) > 0 && (
              <div key="__youtube">
                <div className="recipe-category-head">{t("kitchen.videos.heading")}</div>
                <div className="recipe-category-scroll">
                  {(videos?.items ?? []).map((v) => (
                    <VideoCard item={v} key={v.video_id} />
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
