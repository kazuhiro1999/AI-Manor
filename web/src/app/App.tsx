import { useCallback, useEffect, useLayoutEffect, useMemo, useState } from "react";
import { Navigate, useLocation, useNavigate, useRoutes, type RouteObject } from "react-router-dom";
import { usePolling } from "./polling";
import { setUnauthorizedHandler } from "./api";
import { APP_NAME } from "./brand";
import type { Board, Meta } from "./types";
import { buildRegistry } from "./registry";
import { Nav } from "../components/Nav";
import { ToastBanner, ToastProvider, useToast } from "../components/Toast";
import { isEditingAnywhere } from "./editing";
import { MetaContext, type MetaContextValue } from "./MetaContext";
import { applyThemeToDocument, readTheme } from "./theme";
import { formatDay, useT, syncLanguageFromServer } from "./i18n";

function AppInner() {
  const navigate = useNavigate();
  const location = useLocation();
  const { data: meta, error: metaError, changed: metaChanged, reload: reloadMeta } = usePolling<Meta>("/meta", 5000);
  const { show } = useToast();
  const t = useT();

  // ADR-012 §3 D11: [manor] language の正はバックエンド。/meta は認証なしで読める
  // ので、login・setup も含め起動直後から効く。ここは新規の通信を増やさず、
  // 既に5秒おきに取っている meta へ相乗りするだけ（store.ts の syncLanguageFromServer
  // は値が変わったときしか通知しないので、ポーリングのたびに毎回再描画が走ることはない）。
  useEffect(() => {
    syncLanguageFromServer(meta?.language);
  }, [meta?.language]);

  // 配色（Light/Dark）は設定画面が「変える場所」だが、「効かせる」のはここ——
  // アプリ起動時、どの画面が表示されるより前に一度だけ保存済みの値を当てる。
  // 以前はここが無く、useTheme()（＝ applyThemeToDocument の呼び出し）が設定
  // 画面のマウント内にしか無かったため、設定画面を一度も開かないと保存済みの
  // 配色が反映されなかった（他のどの画面でも既定のまま）。useLayoutEffect で
  // ブラウザが描画する前に当てるので、既定→保存値の点滅も起きない。設定画面は
  // そのまま useTheme() で「変更したら即反映」を担う（保存・適用のロジックは
  // theme.ts 1箇所のみ。ここでは複製しない）。
  useLayoutEffect(() => {
    applyThemeToDocument(readTheme());
  }, []);

  // ADR-007 D6 追補: POST /setup 成功直後は meta（5秒ポーリング）がまだ
  // setup_done: false のことがある。手動リロード無しで /tasks に着地できるよう、
  // 「このセッションで完了した」フラグを立てて誘導条件から除外する
  // （バックエンドの meta が実際に setup_done: true を返すようになれば、
  // 次のポーリングか reload() でそちらでも真になる）。
  const [setupJustCompleted, setSetupJustCompleted] = useState(false);
  const markSetupJustCompleted = useCallback(() => setSetupJustCompleted(true), []);
  const metaCtxValue = useMemo<MetaContextValue>(
    () => ({ meta, reload: reloadMeta, setupJustCompleted, markSetupJustCompleted }),
    [meta, reloadMeta, setupJustCompleted, markSetupJustCompleted]
  );

  useEffect(() => {
    setUnauthorizedHandler(() => navigate("/login"));
  }, [navigate]);

  useEffect(() => {
    if (metaError) show(t("errors.connectionFailed"), "error");
  }, [metaError, show, t]);

  useEffect(() => {
    if (metaChanged) {
      show(t("app.externalUpdateReflected"), "ok", 4000);
    }
  }, [metaChanged, show, t]);

  const readOnly = !!meta?.read_only;
  const registry = useMemo(() => buildRegistry(readOnly), [readOnly]);

  // ナビに出すモジュール: meta.modules で有効なものだけを order 順に。
  // meta がまだ無ければ registry 自体の順（order）で仮表示する。
  const navModules = useMemo(() => {
    const impl = new Map<string, (typeof registry)[number]>(registry.filter((m) => !m.hideFromNav).map((m) => [m.id, m]));
    if (meta?.modules?.length) {
      return meta.modules
        .filter((m) => m.enabled && impl.has(m.id))
        .slice()
        .sort((a, b) => a.order - b.order)
        .map((m) => impl.get(m.id)!);
    }
    return registry.filter((m) => !m.hideFromNav).sort((a, b) => a.order - b.order);
  }, [registry, meta]);

  const { data: board } = usePolling<Board>("/tasks/board", 5000);

  // キーボード 1〜9 でモジュール切り替え。入力中（IME 変換中を含む）は無効。
  useEffect(() => {
    const onKeyDown = (ev: KeyboardEvent) => {
      if (ev.ctrlKey || ev.altKey || ev.metaKey) return;
      if (isEditingAnywhere(ev.target)) return;
      const idx = ev.key && ev.key.length === 1 ? "123456789".indexOf(ev.key) : -1;
      if (idx >= 0 && idx < navModules.length) {
        navigate(`/${navModules[idx].id}`);
        ev.preventDefault();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [navModules, navigate]);

  const routes = useMemo<RouteObject[]>(() => {
    const children: RouteObject[] = registry.flatMap((m) =>
      m.routes.map(
        (r) =>
          ({
            ...r,
            path: (r as { path?: string }).path ? `${m.id}/${(r as { path?: string }).path}` : m.id,
            index: undefined,
          }) as unknown as RouteObject
      )
    );
    return [
      { path: "/", element: <Navigate to="/tasks" replace /> },
      ...children,
      { path: "*", element: <p className="panel-note">{t("app.notFound")}</p> },
    ];
  }, [registry, t]);
  const element = useRoutes(routes);

  const isLoginPath = location.pathname === "/login" || location.pathname.startsWith("/login/");
  const isSetupPath = location.pathname === "/setup" || location.pathname.startsWith("/setup/");

  let content: JSX.Element;
  if (isLoginPath) {
    content = <>{element}</>;
  } else if (meta && meta.setup_done === false && !setupJustCompleted && !isSetupPath) {
    // ADR-007 D6: 初回セットアップが済んでいなければ、login 以外への到達を /setup へ誘導する
    // （このセッションで完了済みなら誘導しない。上の setupJustCompleted 参照）。
    content = <Navigate to="/setup" replace />;
  } else if (isSetupPath) {
    // 初回セットアップは枠を持たないが、**古いサーバの警告だけは出す**。
    // 実測（2026-09-04）: 更新後にサーバを止め忘れたまま主人がセットアップを開き、
    // 「種類が0件」「確認画面で500」に見えた。原因は古いプロセスなのに、それを告げる
    // 帯が枠側にしか無く、**初めての人が最初に見る画面でだけ隠れていた**。
    content = (
      <>
        {meta?.stale && <div className="banner warn">{t("app.staleSetup")}</div>}
        {element}
      </>
    );
  } else {
    content = (
      <>
        <header className="topbar">
          <div className="topbar-main">
            <h1>{APP_NAME}</h1>
            <span className="subtitle">{meta?.home_name || "…"}</span>
          </div>
          <div className="topbar-meta">
            <span id="today" className="chip">
              {/* 曜日つき（主人の指示 2026-09-05）。日付が無いうちは "…" のまま。 */}
              {t("app.today", {
                date: meta?.today || board?.today ? formatDay(meta?.today || board?.today, t) : "…",
              })}
            </span>
            <span id="sync" className={"chip chip-sync " + (metaError ? "error" : "live")}>
              {metaError ? t("app.sync.error") : t("app.sync.live")}
            </span>
            {/* ADR-011 D1: 設定はサイドバーから外し、右上のアイコン（歯車）から開く。 */}
            <button
              className="btn btn-icon"
              type="button"
              onClick={() => navigate("/settings")}
              aria-label={t("app.settings.open")}
              title={t("app.settings.button")}
            >
              ⚙
            </button>
          </div>
        </header>
        {meta?.stale && <div className="banner warn">{t("app.staleDashboard")}</div>}
        {readOnly && <div className="banner warn">{t("app.readOnlyBanner")}</div>}
        <div className="shell">
          <Nav modules={navModules} meta={meta} boardData={board} />
          <div className="content">
            <ToastBanner />
            <main>{element}</main>
            <footer className="footer">
              <p>{t("app.footer", { app: APP_NAME })}</p>
            </footer>
          </div>
        </div>
      </>
    );
  }

  return <MetaContext.Provider value={metaCtxValue}>{content}</MetaContext.Provider>;
}

export function App() {
  return (
    <ToastProvider>
      <AppInner />
    </ToastProvider>
  );
}
