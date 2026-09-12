/* manor web — 料理長の動画リスト（ADR-016 D4 `/kitchen/media`）。
 *
 * 料理中に「ながら見」する YouTube の一覧。上の欄に URL を貼って「追加」、下にサムネイル付きの
 * 一覧を出し、題名・メモはその場で直す（欄から離れたら保存。保存ボタンを押させない）。
 * 並べ替えは上下ボタン、削除は二度押しの確認（`RecipeDetail` の「畳む」と同じ作法）。
 *
 * **同じ一覧を XR（kitchen-xr）が `GET /api/v1/kitchen/media` で読む**（ADR-016 D3）——
 * 画面のための口ではなく、画面と XR が同じ口を読む形にしてある（XR 専用の口は作らない）。
 * 動画は利用者ごと（ADR-016 D1）なので、絞りはサーバ側の `viewing_user_id` に任せ、
 * 画面はクエリを持たない。
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../../app/api";
import { ScreenHeader } from "../../components/ScreenHeader";
import { useToast } from "../../components/Toast";
import { useT } from "../../app/i18n";
import type { MediaItem, MediaList as MediaListData } from "../../app/types";

/** 1行。題名・メモは**その場で**編集する（欄から離れたときに、変わっていれば送る）。 */
function MediaRow({
  item,
  first,
  last,
  onChanged,
  onMove,
}: {
  item: MediaItem;
  first: boolean;
  last: boolean;
  onChanged: () => void;
  onMove: (delta: -1 | 1) => void;
}) {
  const t = useT();
  const { show } = useToast();
  const [title, setTitle] = useState(item.title);
  const [memo, setMemo] = useState(item.memo);
  const [confirmDelete, setConfirmDelete] = useState(false);

  // 並べ替え・再読み込みでサーバの値が変わったら、編集中でない欄を追従させる。
  useEffect(() => setTitle(item.title), [item.title]);
  useEffect(() => setMemo(item.memo), [item.memo]);

  const save = async (fields: { title?: string; memo?: string }) => {
    try {
      await api<MediaItem>(`/kitchen/media/${item.id}`, { method: "PATCH", body: fields });
      show(t("kitchen.media.saved"), "ok");
      onChanged();
    } catch (err) {
      show(t("errors.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
      onChanged();
    }
  };

  const remove = async () => {
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }
    try {
      await api(`/kitchen/media/${item.id}`, { method: "DELETE" });
      show(t("kitchen.media.deleted"), "ok");
      onChanged();
    } catch (err) {
      show(t("errors.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    }
  };

  return (
    <div className="media-row">
      <a className="media-thumb" href={item.url} target="_blank" rel="noreferrer" title={t("kitchen.media.openInYoutube")}>
        {item.thumbnail_url ? (
          <img src={item.thumbnail_url} alt="" loading="lazy" />
        ) : (
          <span className="media-thumb-placeholder">{t("kitchen.media.noThumbnail")}</span>
        )}
      </a>
      <div className="media-fields">
        <input
          className="form-input media-title"
          value={title}
          placeholder={t("kitchen.media.titlePlaceholder")}
          aria-label={t("kitchen.media.titlePlaceholder")}
          onChange={(e) => setTitle(e.target.value)}
          onBlur={() => title !== item.title && save({ title })}
        />
        <div className="media-sub">{item.author || t("kitchen.media.authorUnknown")}</div>
        <input
          className="form-input media-memo"
          value={memo}
          placeholder={t("kitchen.media.memoPlaceholder")}
          aria-label={t("kitchen.media.memoPlaceholder")}
          onChange={(e) => setMemo(e.target.value)}
          onBlur={() => memo !== item.memo && save({ memo })}
        />
      </div>
      <div className="media-actions">
        <button type="button" className="btn btn-small" disabled={first} aria-label={t("kitchen.media.moveUp")} onClick={() => onMove(-1)}>
          ↑
        </button>
        <button type="button" className="btn btn-small" disabled={last} aria-label={t("kitchen.media.moveDown")} onClick={() => onMove(1)}>
          ↓
        </button>
        <button type="button" className="btn btn-small btn-danger" onClick={remove}>
          {confirmDelete ? t("kitchen.media.deleteConfirm") : t("common.delete")}
        </button>
      </div>
    </div>
  );
}

export function MediaList() {
  const t = useT();
  const { show } = useToast();
  const [data, setData] = useState<MediaListData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const [adding, setAdding] = useState(false);

  const load = () => {
    api<MediaListData>("/kitchen/media")
      .then((res) => {
        setData(res);
        setError(null);
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : t("errors.genericLoadFailed")));
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const add = async () => {
    if (!url.trim()) {
      show(t("kitchen.media.urlRequired"), "error");
      return;
    }
    setAdding(true);
    try {
      await api<MediaItem>("/kitchen/media", { method: "POST", body: { url: url.trim(), memo: "" } });
      setUrl("");
      show(t("kitchen.media.added"), "ok");
      load();
    } catch (err) {
      // サーバの 400（URL が読めない）・409（重複）の文言をそのまま出す
      // （ADR-015 から続く「4xx はその文言をそのまま」の流儀。二重に検算を持たない）。
      show(err instanceof ApiError ? err.message : t("errors.genericLoadFailed"), "error");
    } finally {
      setAdding(false);
    }
  };

  /** 上下の入れ替え。並びの**全体**を送る（サーバは送らなかった行を後ろへ回すが、
   * 画面は全部を持っているので曖昧さを残さない）。 */
  const move = async (index: number, delta: -1 | 1) => {
    if (!data) return;
    const ids = data.items.map((it) => it.id);
    const target = index + delta;
    if (target < 0 || target >= ids.length) return;
    [ids[index], ids[target]] = [ids[target], ids[index]];
    try {
      const res = await api<{ items: MediaItem[] }>("/kitchen/media/reorder", { method: "POST", body: { ids } });
      setData({ items: res.items, updated_at: data.updated_at });
    } catch (err) {
      show(t("errors.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
      load();
    }
  };

  const header = (
    <>
      <div className="form-inline" style={{ marginBottom: 6 }}>
        <Link to="/kitchen/recipes">{t("kitchen.media.backToRecipes")}</Link>
      </div>
      <ScreenHeader title={t("kitchen.media.listHeading")} description={t("kitchen.media.description")} />
    </>
  );

  if (error) {
    return (
      <div className="view" id="view-kitchen-media">
        {header}
        <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="view" id="view-kitchen-media">
        {header}
        <p className="panel-note">{t("common.loading")}</p>
      </div>
    );
  }

  return (
    <div className="view" id="view-kitchen-media">
      {header}

      <section className="panel">
        <div className="form-inline">
          <input
            className="form-input"
            placeholder={t("kitchen.media.urlPlaceholder")}
            aria-label={t("kitchen.media.urlPlaceholder")}
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => {
              // Chrome の共有から貼って Enter で入る（主人の導線）。
              if (e.key === "Enter" && !adding) add();
            }}
          />
          <button className="btn btn-primary btn-small" type="button" disabled={adding} onClick={add}>
            {adding ? t("kitchen.media.adding") : t("kitchen.media.addButton")}
          </button>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{t("kitchen.media.listHeading")}</h2>
          <span className="row-id">{t("kitchen.media.count", { n: data.items.length })}</span>
        </div>
        {!data.items.length && <p className="panel-note">{t("kitchen.media.empty")}</p>}
        <div className="media-rows">
          {data.items.map((it, i) => (
            <MediaRow
              key={it.id}
              item={it}
              first={i === 0}
              last={i === data.items.length - 1}
              onChanged={load}
              onMove={(delta) => move(i, delta)}
            />
          ))}
        </div>
      </section>
    </div>
  );
}
