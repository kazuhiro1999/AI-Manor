import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { ModuleDefinition, ModuleId } from "../../app/module";
import { MODULE_TITLE_KEY } from "../../app/module";
import { usePolling } from "../../app/polling";
import { APP_NAME } from "../../app/brand";
import { useEditingGuard } from "../../app/editing";
import { api, apiUpload, ApiError } from "../../app/api";
import type { FaceModelEntry, Meta, RunKindStat, RunRow, RunsData, RunStatsData, SettingsData, SetupInfo, TaskKind } from "../../app/types";
import { useTheme, THEMES, type Theme } from "../../app/theme";
import { fmtCost, fmtDateTime, fmtSeconds, runKindLabel } from "../../app/format";
import { DataTable, type Column } from "../../components/DataTable";
import { useToast } from "../../components/Toast";
import { ScreenHeader } from "../../components/ScreenHeader";
import { t, useT, useLanguageSetting, LANGUAGES, type Language } from "../../app/i18n";
import { purposeLabel } from "../../app/purposeMeta";
import { AGENT_LABEL_KEY } from "../../app/agentMeta";

function parsePurposeIds(raw?: string): string[] {
  if (!raw) return [];
  try {
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

/** ADR-007 D6: 設定画面の「プロフィール」節。呼び名・執事の呼び名・用途を
 *  `PUT /setup/profile` で編集する（プロジェクト・タスクは作らない）。 */
function ProfileSection() {
  const t = useT();
  const { ref, isEditing } = useEditingGuard<HTMLDivElement>();
  const { data: setupInfo, error, reload } = usePolling<SetupInfo>("/setup", 5000, ref as React.RefObject<HTMLElement>);
  const { show } = useToast();

  const [callname, setCallname] = useState("");
  const [butlerName, setButlerName] = useState("");
  const [selectedPurposes, setSelectedPurposes] = useState<string[]>([]);

  useEffect(() => {
    if (setupInfo && !isEditing()) {
      setCallname(setupInfo.profile["master.callname"] || "");
      // 執事の呼び名が未設定のときの初期表示だけは言語に合わせる（D12: 担当の名前は訳す）。
      // 主人が実際に書き込んだ呼び名（②のデータ）はここに来ないので触らない。
      setButlerName(setupInfo.profile["butler.callname"] || t("agent.butler"));
      setSelectedPurposes(parsePurposeIds(setupInfo.profile["purposes"]));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setupInfo]);

  const togglePurpose = (id: string) => {
    setSelectedPurposes((prev) => (prev.includes(id) ? prev.filter((p) => p !== id) : [...prev, id]));
  };

  const save = async () => {
    try {
      await api("/setup/profile", {
        method: "PUT",
        body: { callname, butler_name: butlerName, purposes: selectedPurposes },
      });
      show(t("settings.profile.saved"), "ok", 3000);
      reload();
    } catch (err) {
      show(t("settings.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    }
  };

  return (
    <section className="panel" ref={ref}>
      <div className="panel-head">
        <h2>{t("settings.profile.heading")}</h2>
      </div>
      {error && <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>}
      <div className="form-grid">
        <div className="form-row">
          <label htmlFor="profile-callname">{t("settings.profile.callname")}</label>
          <input id="profile-callname" className="form-input" value={callname} onChange={(e) => setCallname(e.target.value)} />
        </div>
        <div className="form-row">
          <label htmlFor="profile-butler-name">{t("settings.profile.butlerName")}</label>
          <input id="profile-butler-name" className="form-input" value={butlerName} onChange={(e) => setButlerName(e.target.value)} />
        </div>
        <div className="form-row">
          <label>{t("settings.profile.purposes")}</label>
          <div className="setup-chips">
            {(setupInfo?.purposes || []).map((p) => (
              <button
                key={p.id}
                type="button"
                className="chip chip-toggle"
                aria-pressed={selectedPurposes.includes(p.id)}
                onClick={() => togglePurpose(p.id)}
              >
                {purposeLabel(t, p)}
              </button>
            ))}
          </div>
        </div>
      </div>
      <div className="form-actions">
        <button className="btn btn-primary" type="button" onClick={save}>
          {t("common.save")}
        </button>
        <Link className="btn" to="/setup?redo=1">
          {t("settings.profile.redoSetup")}
        </Link>
      </div>
    </section>
  );
}

/** ADR-012 §3 D11: `[manor] language`。選んだ瞬間に画面へ反映し（store.ts が
 * useSyncExternalStore で全画面へ即時に配る）、保存は裏で PUT する——保存の成否を
 * 待って表示を切り替える設計にはしない（2026-09-05 の Light/Dark 不具合と同じ轍を
 * 踏まない。あちらは「反映されるタイミング」が問題だったが、こちらは「反映を通信の
 * 完了待ちにしない」という形で同じ教訓を守る）。 */
function LanguageSection() {
  const t = useT();
  const { setting, setLanguage } = useLanguageSetting();
  const { show } = useToast();

  const choose = async (lang: Language) => {
    setLanguage(lang);
    try {
      await api("/settings", { method: "PUT", body: { manor: { language: lang } } });
    } catch (err) {
      show(t("settings.language.saveFailed") + (err instanceof ApiError ? `: ${err.message}` : ""), "warn", 6000);
    }
  };

  const LANGUAGE_LABEL_KEY = {
    auto: "settings.language.auto",
    ja: "settings.language.ja",
    en: "settings.language.en",
  } as const;

  return (
    <section className="panel panel-primary">
      <div className="panel-head">
        <h2>{t("settings.language.heading")}</h2>
      </div>
      <div className="seg" role="group" aria-label={t("settings.language.heading")}>
        {LANGUAGES.map((l) => (
          <button key={l} className="seg-btn" type="button" aria-pressed={setting === l} onClick={() => choose(l)}>
            {t(LANGUAGE_LABEL_KEY[l])}
          </button>
        ))}
      </div>
      <p className="setting-note">{t("settings.language.hint")}</p>
    </section>
  );
}

const THEME_LABEL_KEY: Record<Theme, "settings.theme.system" | "settings.theme.light" | "settings.theme.dark"> = {
  system: "settings.theme.system",
  light: "settings.theme.light",
  dark: "settings.theme.dark",
};

function fmtBytes(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KB`;
  return `${(n / (1024 * 1024)).toFixed(1)}MB`;
}

// ADR-008 §7 D14・D15 の表記（執事 / 料理長 / 家政婦 / 家令 / 秘書 / 検分 / 監査）と同じ並び。
// ADR-011 D3: 担当の一覧（`web/src/modules/agents/index.tsx`）もこの並びで出す
// （`GET /api/v1/agents` は語彙順＝`talk.available_agents` のアルファベット順で返るため、
// 表示側でこの並びに直す。export してここ1箇所を正にする）。
export const FACE_AGENT_ORDER = ["butler", "chef", "housekeeper", "steward", "secretary", "qa", "auditor"];

// ADR-008 §8 D16: 小窓は「窓」で開く。タブではない。280×340 を画面の右下へ寄せる
// （`screen.availWidth/availHeight` から算出、余白は約16px）。
const FACE_WINDOW_WIDTH = 280;
const FACE_WINDOW_HEIGHT = 340;
const FACE_WINDOW_MARGIN = 16;

type ShowToast = (message: string, kind?: "ok" | "warn" | "error" | "", timeoutMs?: number) => void;

/** ブラウザの `window.open` によるポップアップ次善策（D16）。`name` を担当ごとに
 * 固定するので、二度押しても同じ窓を使い回す（新しい窓を増やさない）。ポップアップは
 * 必ずタイトル・URL バー付きになる——JS からはヘッダ無しの窓を作れない、という制約の
 * 中での次善。popup 自体がブロックされたときは、**黙って諦めない**——タブへさらに
 * 次善のフォールバックをした上で、その旨をトーストで伝える。 */
function openFaceWindowPopup(agent: string, show: ShowToast): void {
  const url = `/face?agent=${encodeURIComponent(agent)}`;
  const name = `manor-face-${agent}`;
  const availW = window.screen?.availWidth || window.innerWidth;
  const availH = window.screen?.availHeight || window.innerHeight;
  const left = Math.max(0, Math.round(availW - FACE_WINDOW_WIDTH - FACE_WINDOW_MARGIN));
  const top = Math.max(0, Math.round(availH - FACE_WINDOW_HEIGHT - FACE_WINDOW_MARGIN));
  const features = `popup=yes,width=${FACE_WINDOW_WIDTH},height=${FACE_WINDOW_HEIGHT},left=${left},top=${top}`;

  let win: Window | null = null;
  try {
    win = window.open(url, name, features);
  } catch {
    win = null;
  }
  if (win) {
    try {
      win.focus();
    } catch {
      /* 別ウィンドウの focus は拒まれることがある */
    }
    return;
  }

  // popup がブロックされたとき（win が null）の次善策。タブでも出さないよりはまし。
  window.open(url, "_blank", "noopener,noreferrer");
  show(t("settings.faceWindow.popupBlocked"), "warn", 6000);
}

/** 小窓を開く（D16）。**本命は `POST /face/open`**——サーバ側で Chrome を
 * `--app=` モード（`manor face` と同じ経路、`face.try_open_app_window`）で起こす。
 * ブラウザの `window.open` によるポップアップは、JS からどう頑張ってもタイトル・URL
 * バーを消せない（ヘッダ無しの窓は OS 側でプロセスを起動しないと作れない）。だから
 * サーバ側で Chrome を起こす経路をまず試し、それが使えたとき（`opened: true`）は
 * 何もしない——窓は既に開いている。使えなかったとき（Chrome が無い等 `opened: false`、
 * または通信自体が失敗したとき）だけ、**黙って諦めず**ポップアップへ次善のフォール
 * バックをし、その理由をトーストで伝える。
 *
 * ADR-011 D4: 「小窓を開く」ボタンは設定画面だけでなく担当の一覧・ダッシュボードにも
 * 増える。ここが唯一の実装——`web/src/modules/agents/index.tsx` はこの関数を import
 * するだけで、開く処理を書き直さない。 */
export async function openFaceWindow(agent: string, show: ShowToast): Promise<void> {
  try {
    const result = await api<{ opened: boolean; method: string; reason: string }>("/face/open", {
      method: "POST",
      body: { agent },
    });
    if (result?.opened) return; // サーバ側でヘッダ無しの窓を開けた。これ以上は何もしない。
    show(
      `${t("settings.faceWindow.serverFailedPrefix")} ${result?.reason || t("settings.faceWindow.unknownReason")}${t("settings.faceWindow.serverFailedSuffix")}`,
      "warn",
      6000
    );
  } catch (err) {
    show(
      `${t("settings.faceWindow.requestFailedPrefix")} ${err instanceof ApiError ? err.message : t("common.unknown")}${t("settings.faceWindow.serverFailedSuffix")}`,
      "warn",
      6000
    );
  }
  openFaceWindowPopup(agent, show);
}

/** 「姿（小窓）」の1担当ぶんの行。差し替え（アップロード）・削除・小窓を開くボタンを持つ
 * （D14・D15）。`home/face/<agent>.vrm` を手で置く代わりに、ここから出し入れする。 */
function FaceModelRow({ entry, onChanged }: { entry: FaceModelEntry; onChanged: () => void }) {
  const t = useT();
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const { show } = useToast();
  // entry.label は GET /face/models（agent_label(agent) 由来）の日本語表示名——担当の
  // 一覧・タスクの行と同じ「id から引き直す」対象（検分 2026-09-05 の指摘で判明）。
  // 語彙外の agent id が来たときだけサーバの label にそのまま落ちる。
  const label = entry.agent in AGENT_LABEL_KEY ? t(AGENT_LABEL_KEY[entry.agent]) : entry.label;

  const doUpload = async () => {
    if (!file) {
      show(t("settings.faceModels.selectFile"), "error");
      return;
    }
    setBusy(true);
    try {
      const form = new FormData();
      form.append("agent", entry.agent);
      form.append("file", file);
      await apiUpload("/face/model", form);
      show(t("settings.faceModels.replaced", { label }), "ok", 3000);
      setFile(null);
      if (fileRef.current) fileRef.current.value = "";
      onChanged();
    } catch (err) {
      show(t("settings.faceModels.replaceFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    } finally {
      setBusy(false);
    }
  };

  const doDelete = async () => {
    setBusy(true);
    try {
      await api(`/face/model?agent=${encodeURIComponent(entry.agent)}`, { method: "DELETE" });
      show(t("settings.faceModels.deleted", { label }), "ok", 3000);
      onChanged();
    } catch (err) {
      show(t("settings.faceModels.deleteFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="row-item face-model-row" data-agent={entry.agent}>
      <span className="row-title">{label}</span>
      <span className="row-id">
        {entry.bundled
          ? t("settings.faceModels.bundled")
          : entry.has_model
            ? `${fmtBytes(entry.size)} / ${fmtDateTime(entry.updated_at)}`
            : t("settings.faceModels.notSet")}
      </span>
      {entry.legacy && (
        <span className="panel-note">
          {t("settings.faceModels.legacyNotePrefix")}
          <code>{t("settings.faceModels.legacyNotePath")}</code>
          {t("settings.faceModels.legacyNoteMiddle")}
          <code>{t("settings.faceModels.legacyNotePath2")}</code>
          {t("settings.faceModels.legacyNoteSuffix")}
        </span>
      )}
      <input
        ref={fileRef}
        type="file"
        accept=".vrm"
        disabled={busy}
        onChange={(e) => setFile(e.target.files?.[0] || null)}
        aria-label={t("settings.faceModels.fileInputAria", { label })}
      />
      <button className="btn" type="button" disabled={busy || !file} onClick={doUpload}>
        {entry.has_model && !entry.bundled
          ? t("settings.faceModels.replace")
          : t("settings.faceModels.set")}
      </button>
      {/* 同梱物は主人の持ち物ではないので消させない（消す先が home/ に無い）。 */}
      {entry.has_model && !entry.legacy && !entry.bundled && (
        <button className="btn" type="button" disabled={busy} onClick={doDelete}>
          {t("settings.faceModels.delete")}
        </button>
      )}
      <button className="btn" type="button" onClick={() => openFaceWindow(entry.agent, show)}>
        {t("settings.faceModels.openWindow")}
      </button>
    </div>
  );
}

/** ADR-008 §7 D15: 姿は拡張機能ではないので「設定」に置く節。担当ごとに現在の有無・
 * 差し替え・削除・小窓を開くリンクを並べる。`model.vrm`（後方互換）は画面から置けない
 * ——置けるのは常に `<agent>.vrm`（D15）。 */
function FaceModelsSection() {
  const t = useT();
  const { data, error, reload } = usePolling<FaceModelEntry[]>("/face/models", 15000);
  const rows = data
    ? [...data].sort((a, b) => FACE_AGENT_ORDER.indexOf(a.agent) - FACE_AGENT_ORDER.indexOf(b.agent))
    : [];

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>{t("settings.faceModels.heading")}</h2>
      </div>
      <p className="panel-note">
        {t("settings.faceModels.hintPrefix")} <code>{t("settings.faceModels.hintPath")}</code> {t("settings.faceModels.hintMiddle")}{" "}
        <code>{t("settings.faceModels.hintCommand")}</code> {t("settings.faceModels.hintSuffix")}
      </p>
      {error && <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>}
      <div className="rows">
        {rows.map((entry) => (
          <FaceModelRow key={entry.agent} entry={entry} onChanged={reload} />
        ))}
      </div>
    </section>
  );
}

// ADR-010 D2 後半: id の形式（`src/manor/task_kind.py` の `_ID_RE` と同じ）。ここで先に
// 弾いて、形式違反をサーバへ投げてエラーを待たせない。
const TASK_KIND_ID_RE = /^[a-z][a-z0-9_]*$/;

// `other` は分類できないものの受け皿——消せる・改名できると「種類が無い」状態を
// 主人が壊せてしまうので、改名・アーカイブの操作をここでは出さない（task_kind.py の
// PROTECTED_ID と同じ理由）。
const TASK_KIND_PROTECTED_ID = "other";

/** ADR-010 D2 後半:「タスクの種類」の1行。表示名は書き換えられるが id は固定
 *（過去のタスクが参照しているので、ここでは触らせない——見せるだけの固定表示）。 */
function TaskKindRow({ kind, onChanged }: { kind: TaskKind; onChanged: () => void }) {
  const t = useT();
  const [editing, setEditing] = useState(false);
  const [label, setLabel] = useState(kind.label);
  const [busy, setBusy] = useState(false);
  const { show } = useToast();
  const isProtected = kind.id === TASK_KIND_PROTECTED_ID;
  const archived = !!kind.archived_at;

  const startEdit = () => {
    setLabel(kind.label);
    setEditing(true);
  };

  const saveRename = async () => {
    const trimmed = label.trim();
    if (!trimmed) {
      show(t("settings.taskKinds.labelRequired"), "error");
      return;
    }
    setBusy(true);
    try {
      await api(`/task-kinds/${encodeURIComponent(kind.id)}`, { method: "PUT", body: { label: trimmed } });
      show(t("settings.taskKinds.renamed"), "ok", 3000);
      setEditing(false);
      onChanged();
    } catch (err) {
      show(t("settings.taskKinds.renameFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    } finally {
      setBusy(false);
    }
  };

  const doArchive = async () => {
    if (!window.confirm(t("settings.taskKinds.archiveConfirm", { label: kind.label }))) return;
    setBusy(true);
    try {
      await api(`/task-kinds/${encodeURIComponent(kind.id)}`, { method: "DELETE" });
      show(t("settings.taskKinds.archivedToast"), "ok", 3000);
      onChanged();
    } catch (err) {
      show(t("settings.taskKinds.archiveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={"row-item" + (archived ? " withdrawn" : "")} data-kind-id={kind.id}>
      <span className="row-id">{kind.id}</span>
      {editing ? (
        <>
          <input
            className="form-input"
            style={{ flex: 1, minWidth: 160 }}
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            disabled={busy}
            aria-label={t("settings.taskKinds.labelFieldAria", { id: kind.id })}
          />
          <button className="btn btn-small btn-primary" type="button" disabled={busy} onClick={saveRename}>
            {t("common.save")}
          </button>
          <button className="btn btn-small" type="button" disabled={busy} onClick={() => setEditing(false)}>
            {t("common.cancel")}
          </button>
        </>
      ) : (
        <>
          <span className="row-title">{kind.label}</span>
          {archived && <span className="badge-st st-withdrawn">{t("common.archived")}</span>}
          {isProtected ? (
            <span className="panel-note">{t("settings.taskKinds.protectedNote")}</span>
          ) : (
            <>
              <button className="btn btn-small" type="button" onClick={startEdit}>
                {t("common.rename")}
              </button>
              {!archived && (
                <button className="btn btn-small btn-danger" type="button" disabled={busy} onClick={doArchive}>
                  {t("common.archive")}
                </button>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}

/** ADR-010 D2 後半: 追加フォーム。id は `TASK_KIND_ID_RE` で先に検算してから送る
 *（バックエンドの検査と同じ形式。ここで弾けば往復を待たずに理由が分かる）。 */
function TaskKindAddForm({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const t = useT();
  const [id, setId] = useState("");
  const [label, setLabel] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { show } = useToast();

  const save = async () => {
    const trimmedId = id.trim();
    const trimmedLabel = label.trim();
    if (!TASK_KIND_ID_RE.test(trimmedId)) {
      setError(t("settings.taskKinds.idInvalid"));
      return;
    }
    if (!trimmedLabel) {
      setError(t("settings.taskKinds.labelRequired"));
      return;
    }
    setError(null);
    setBusy(true);
    try {
      await api("/task-kinds", { method: "POST", body: { id: trimmedId, label: trimmedLabel } });
      show(t("settings.taskKinds.added"), "ok", 3000);
      onSaved();
    } catch (err) {
      show(t("settings.taskKinds.addFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="form-inline" style={{ marginTop: 8, flexWrap: "wrap" }}>
      <input
        className="form-input"
        style={{ maxWidth: 180 }}
        placeholder={t("settings.taskKinds.idPlaceholder")}
        value={id}
        onChange={(e) => setId(e.target.value)}
        disabled={busy}
        aria-label={t("settings.taskKinds.newIdAria")}
      />
      <input
        className="form-input"
        placeholder={t("settings.taskKinds.labelPlaceholder")}
        value={label}
        onChange={(e) => setLabel(e.target.value)}
        disabled={busy}
        aria-label={t("settings.taskKinds.newLabelAria")}
      />
      <button className="btn btn-small btn-primary" type="button" disabled={busy} onClick={save}>
        {t("common.add")}
      </button>
      <button className="btn btn-small" type="button" disabled={busy} onClick={onClose}>
        {t("common.cancel")}
      </button>
      {error && (
        <div className="form-error" style={{ width: "100%" }}>
          {error}
        </div>
      )}
    </div>
  );
}

/** ADR-010 D2 後半:「タスクの種類はWebアプリ側でカスタマイズできた方がいい」という
 * 主人の要望そのもの。並べ替え・振り返りのための札であって、執事の自律の度合い（level）とは
 * 無関係——D1「行動クラス」とは別物なのでここでは混ぜない。 */
function TaskKindsSection() {
  const t = useT();
  const [showArchived, setShowArchived] = useState(false);
  const path = `/task-kinds?all=${showArchived}`;
  const { data, error, reload } = usePolling<TaskKind[]>(path, 5000);
  const [adding, setAdding] = useState(false);

  const rows = data ? [...data].sort((a, b) => a.sort - b.sort) : [];

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>{t("settings.taskKinds.heading")}</h2>
        <button className="btn btn-small btn-primary" style={{ marginLeft: "auto" }} type="button" onClick={() => setAdding(true)}>
          {t("settings.taskKinds.add")}
        </button>
      </div>
      <p className="panel-note">{t("settings.taskKinds.hint")}</p>
      {error && <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>}
      <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12, marginBottom: 8 }}>
        <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />{" "}
        {t("settings.taskKinds.includeArchived")}
      </label>
      <div className="rows">
        {!rows.length && <p className="panel-note">{t("common.none")}</p>}
        {rows.map((k) => (
          <TaskKindRow key={k.id} kind={k} onChanged={reload} />
        ))}
      </div>
      {adding && (
        <TaskKindAddForm
          onClose={() => setAdding(false)}
          onSaved={() => {
            setAdding(false);
            reload();
          }}
        />
      )}
    </section>
  );
}

/** ADR-006 §3 D11・§6 担当C: 「稼働と費用」の節。`run` 表がまだ無い home
 * （担当A の移行前）では `available: false` になる——部下の表と同じ約束。 */
function RunsAndCostPanel() {
  const t = useT();
  const { data: stats, error: statsError } = usePolling<RunStatsData>("/runs/stats?days=30", 5000);
  const { data: recent, error: runsError } = usePolling<RunsData>("/runs?days=30", 5000);

  const statsCols: Column<RunKindStat>[] = [
    { key: "kind", label: t("settings.runs.col.kind"), render: (r) => runKindLabel(r.kind) },
    { key: "count", label: t("settings.runs.col.count"), render: (r) => String(r.count) },
    { key: "cost_usd", label: t("settings.runs.col.totalCost"), render: (r) => fmtCost(r.cost_usd) },
    { key: "avg_seconds", label: t("settings.runs.col.avgDuration"), render: (r) => fmtSeconds(r.avg_seconds) },
    { key: "failed", label: t("settings.runs.col.failed"), render: (r) => String(r.failed) },
  ];

  const runCols: Column<RunRow>[] = [
    { key: "started_at", label: t("settings.runs.col.datetime"), nowrap: true, render: (r) => fmtDateTime(r.started_at) },
    { key: "kind", label: t("settings.runs.col.kind"), render: (r) => runKindLabel(r.kind) },
    { key: "ref", label: "ref", wide: true, render: (r) => r.ref || "—" },
    { key: "model", label: "model", render: (r) => r.model || "—" },
    { key: "cost_usd", label: "$", render: (r) => fmtCost(r.cost_usd) },
    { key: "turns", label: "turns", render: (r) => (r.turns == null ? "—" : String(r.turns)) },
    { key: "exit_reason", label: "exit", render: (r) => r.exit_reason || "—" },
  ];

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>{t("settings.runs.heading")}</h2>
      </div>
      <p className="setting-note">{t("settings.runs.hint")}</p>
      {(statsError || runsError) && <p className="panel-note">{t("errors.loadFailed", { reason: statsError || runsError || t("common.unknown") })}</p>}
      {!stats || !recent ? (
        <p className="panel-note">{t("common.loading")}</p>
      ) : !stats.available || !recent.available ? (
        <p className="panel-note">{t("settings.runs.empty")}</p>
      ) : (
        <>
          <div className="setting-note" style={{ marginBottom: 8 }}>
            {t("settings.runs.totalLabel")} <strong>{fmtCost(stats.total_cost_usd)}</strong>
          </div>
          {stats.by_kind.length ? (
            <DataTable columns={statsCols} rows={stats.by_kind} rowKey={(r) => r.kind} />
          ) : (
            <p className="panel-note">{t("settings.runs.noKindData")}</p>
          )}
          <div className="panel-head" style={{ marginTop: 14 }}>
            <h3 style={{ margin: 0, fontSize: 13 }}>{t("settings.runs.recentHeading")}</h3>
          </div>
          {recent.runs.length ? (
            <DataTable columns={runCols} rows={recent.runs} rowKey={(r) => String(r.id)} />
          ) : (
            <p className="panel-note">{t("settings.runs.noRecent")}</p>
          )}
        </>
      )}
    </section>
  );
}

function SettingsScreen() {
  const t = useT();
  const [theme, setTheme] = useTheme();
  // 静穏時間の入力欄は5秒ポーリングで取得した値を初期表示に使うので、入力中に
  // ポーリングが上書きしないよう editing guard を通す（board の isEditingWithin と同じ規則。
  // 以前はここが無く、入力中に5秒おきへ値が巻き戻るバグがあった）。
  const { ref, isEditing } = useEditingGuard<HTMLDivElement>();
  const { data, error, reload } = usePolling<SettingsData>("/settings", 5000, ref as React.RefObject<HTMLElement>);
  const { data: meta } = usePolling<Meta>("/meta", 5000);
  const { show } = useToast();

  const [quietFrom, setQuietFrom] = useState("");
  const [quietTo, setQuietTo] = useState("");
  const [passcode, setPasscode] = useState("");
  const [requireBusy, setRequireBusy] = useState(false);

  useEffect(() => {
    if (data && !isEditing()) {
      setQuietFrom(data.notify.quiet_from != null ? String(data.notify.quiet_from) : "");
      setQuietTo(data.notify.quiet_to != null ? String(data.notify.quiet_to) : "");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  const save = async () => {
    // notify.quiet_from/quiet_to は「時」の整数（0-23）。home/config.toml の実際の型に合わせる
    // （src/manor/notify.py・src/manor/web/api_v1/settings.py。"HH:MM" 文字列ではない）。
    const from = quietFrom.trim() === "" ? undefined : Number(quietFrom);
    const to = quietTo.trim() === "" ? undefined : Number(quietTo);
    if ((from != null && (Number.isNaN(from) || from < 0 || from > 23)) || (to != null && (Number.isNaN(to) || to < 0 || to > 23))) {
      show(t("settings.quietHours.invalid"), "error");
      return;
    }
    try {
      await api("/settings", {
        method: "PUT",
        body: {
          notify: { quiet_from: from, quiet_to: to },
          web: passcode ? { passcode } : undefined,
        },
      });
      setPasscode("");
      show(t("settings.saved"), "ok", 3000);
      reload();
    } catch (err) {
      show(t("settings.saveFailed", { reason: err instanceof ApiError ? err.message : t("common.unknown") }), "error");
    }
  };

  // ADR-013 D2: require_passcode トグル。**画面側でも塞ぐ**（未設定のまま on にできない／
  // 非ループバックで待ち受け中は off にできない）——サーバ側の検算（web/api_v1/settings.py）
  // だけだと「押せてしまってからエラーになる」体験になるため、押せること自体をここで防ぐ。
  // 実際の締め出し防止そのものはサーバ側の検算が担う（画面はあくまで次善の案内）。
  const canEnableRequirePasscode = !!data?.web.has_passcode;
  const canDisableRequirePasscode = !!data?.web.is_loopback;
  const requirePasscodeChecked = !!data?.web.require_passcode;
  const requirePasscodeDisabled =
    requireBusy || (requirePasscodeChecked ? !canDisableRequirePasscode : !canEnableRequirePasscode);

  const toggleRequirePasscode = async (next: boolean) => {
    setRequireBusy(true);
    try {
      await api("/settings", { method: "PUT", body: { web: { require_passcode: next } } });
      show(t(next ? "settings.passcode.requireEnabled" : "settings.passcode.requireDisabled"), "ok", 3000);
      reload();
    } catch (err) {
      show(
        t(next ? "settings.passcode.requireEnableFailed" : "settings.passcode.requireDisableFailed", {
          reason: err instanceof ApiError ? err.message : t("common.unknown"),
        }),
        "error"
      );
    } finally {
      setRequireBusy(false);
    }
  };

  return (
    <div className="view" id="view-settings">
      <ScreenHeader title={t("nav.settings")} description={t("settings.description", { app: APP_NAME })} />

      <LanguageSection />

      <section className="panel panel-primary">
        <div className="panel-head">
          <h2>{t("settings.theme.heading")}</h2>
        </div>
        <div className="seg" role="group" aria-label={t("settings.theme.heading")}>
          {THEMES.map((th) => (
            <button key={th} className="seg-btn" type="button" aria-pressed={theme === th} onClick={() => setTheme(th)}>
              {t(THEME_LABEL_KEY[th])}
            </button>
          ))}
        </div>
        <p className="setting-note">{t("settings.theme.hint")}</p>
      </section>

      <ProfileSection />

      <TaskKindsSection />

      <FaceModelsSection />

      <section className="panel" ref={ref}>
        <div className="panel-head">
          <h2>{t("settings.quietHours.heading")}</h2>
        </div>
        <p className="setting-note">{t("settings.quietHours.hint")}</p>
        <div className="form-inline">
          <label>
            {t("settings.quietHours.from")}{" "}
            <input
              className="form-input"
              style={{ maxWidth: 80 }}
              type="number"
              min={0}
              max={23}
              value={quietFrom}
              onChange={(e) => setQuietFrom(e.target.value)}
              placeholder="22"
            />
            {t("settings.quietHours.hourSuffix")}
          </label>
          <label>
            {t("settings.quietHours.to")}{" "}
            <input
              className="form-input"
              style={{ maxWidth: 80 }}
              type="number"
              min={0}
              max={23}
              value={quietTo}
              onChange={(e) => setQuietTo(e.target.value)}
              placeholder="7"
            />
            {t("settings.quietHours.hourSuffix")}
          </label>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{t("settings.passcode.heading")}</h2>
        </div>
        <p className="panel-note">
          {t("settings.passcode.hint")}
          {data && (data.web.has_passcode ? t("settings.passcode.set") : t("settings.passcode.unset"))}
        </p>
        <div className="form-inline">
          <input
            className="form-input"
            type="password"
            value={passcode}
            onChange={(e) => setPasscode(e.target.value)}
            placeholder={t("settings.passcode.placeholder")}
          />
        </div>
        <label style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 10 }}>
          <input
            type="checkbox"
            checked={requirePasscodeChecked}
            disabled={requirePasscodeDisabled}
            onChange={(e) => toggleRequirePasscode(e.target.checked)}
          />
          {t("settings.passcode.requireLabel")}
        </label>
        <p className="setting-note">{t("settings.passcode.requireHint")}</p>
        {!canEnableRequirePasscode && !requirePasscodeChecked && (
          <p className="setting-note">{t("settings.passcode.requireNeedsPasscodeHint")}</p>
        )}
        {requirePasscodeChecked && !canDisableRequirePasscode && (
          <p className="setting-note">{t("settings.passcode.requireNeedsLoopbackHint")}</p>
        )}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{t("settings.moduleOrder.heading")}</h2>
        </div>
        <p className="panel-note">{t("settings.moduleOrder.hint")}</p>
        <div className="rows">
          {(meta?.modules || []).map((m) => (
            <div className="row-item" key={m.id}>
              <span className="row-id">{m.order}</span>
              <span className="row-title">
                {m.icon} {m.id in MODULE_TITLE_KEY ? t(MODULE_TITLE_KEY[m.id as ModuleId]) : m.title}
              </span>
              {!m.enabled && <span className="badge-st st-withdrawn">{t("common.disabled")}</span>}
            </div>
          ))}
        </div>
      </section>

      <RunsAndCostPanel />

      <section className="panel">
        <div className="panel-head">
          <h2>{t("settings.state.heading")}</h2>
        </div>
        {error && <p className="panel-note">{t("errors.loadFailed", { reason: error })}</p>}
        <p className="panel-note">
          {t("settings.state.summary", {
            version: meta?.version || "—",
            stale: meta?.stale ? t("settings.state.staleYes") : t("settings.state.no"),
            readOnly: meta?.read_only ? t("settings.state.yes") : t("settings.state.no"),
          })}
        </p>
      </section>

      <div className="form-actions">
        <button className="btn btn-primary" type="button" onClick={save}>
          {t("common.save")}
        </button>
      </div>
    </div>
  );
}

export const settingsModule: ModuleDefinition = {
  id: "settings",
  title: "nav.settings",
  description: "settings.description",
  icon: "⚙",
  order: 90,
  routes: [{ index: true, element: <SettingsScreen /> }],
  // ADR-011 D1: 設定はサイドバーから外し、右上の歯車アイコン（App.tsx の topbar）から
  // 開く。ルート自体（/settings）は生かす——login と同じ「ナビには出ないが到達はできる」形。
  hideFromNav: true,
};
