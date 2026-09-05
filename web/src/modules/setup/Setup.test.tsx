import { describe, expect, it, vi, afterEach } from "vitest";
import { render, cleanup, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { setupModule } from "./index";
import type { SetupInfo, TaskKind } from "../../app/types";
import { ToastProvider } from "../../components/Toast";
import { MetaContext, type MetaContextValue } from "../../app/MetaContext";
import { APP_NAME } from "../../app/brand";

const SetupScreen = setupModule.routes[0].element as JSX.Element;

// SetupScreen は App が配る MetaContext（POST 成功後に markSetupJustCompleted() を
// 呼び、meta の誘導へ戻らないようにする。上位の App.test.tsx 参照）に依存するので、
// ここでは実 App の代わりに素朴な Provider で包む。
function fakeMetaContext(): MetaContextValue & { markCalls: number } {
  const value = {
    meta: null,
    reload: vi.fn().mockResolvedValue(undefined),
    setupJustCompleted: false,
    markCalls: 0,
    markSetupJustCompleted: () => {
      value.markCalls += 1;
    },
  };
  return value;
}

// ADR-007 §6 D7 の語彙一式（tasks/kitchen/money/house/secretary）。
function baseSetupInfo(overrides?: Partial<SetupInfo>): SetupInfo {
  return {
    done: false,
    completed_at: null,
    profile: {},
    purposes: [
      { id: "tasks", label: "タスク・プロジェクトの管理" },
      { id: "kitchen", label: "料理・買い物" },
      { id: "money", label: "家計" },
      { id: "house", label: "家事・消耗品" },
      { id: "secretary", label: "予定・調べもの・書きもの" },
    ],
    presets: [
      { id: "careful", label: "🐢慎重" },
      { id: "standard", label: "🚶標準" },
      { id: "fast", label: "🏃高速" },
    ],
    task_classes: [
      { id: "general", label: "一般の作業", default_level: "L2", fixed: false },
      { id: "research", label: "情報収集・調査", default_level: "L3", fixed: false },
    ],
    money_apps: [
      { id: "none", label: "使っていない" },
      { id: "zaim", label: "Zaim" },
      { id: "moneyforward", label: "マネーフォワード" },
    ],
    ...overrides,
  };
}

interface FetchCall {
  url: string;
  method: string;
  body: unknown;
}

// ADR-010 D2: GET /task-kinds の見本（既定8つの一部）。setup 側は meta.task_kinds と
// 同じ生成元から /task-kinds を直接取る（往復を1つ増やす代わりに meta のタイミングに
// 依存しない。web/src/modules/setup/index.tsx 参照）。
const TASK_KINDS_FIXTURE: TaskKind[] = [
  { id: "research", label: "調査・情報収集", sort: 1, archived_at: null },
  { id: "build", label: "作成・実装", sort: 3, archived_at: null },
  { id: "other", label: "その他", sort: 8, archived_at: null },
];

function renderSetup(
  info: SetupInfo = baseSetupInfo(),
  ctx: MetaContextValue = fakeMetaContext(),
  taskKinds: TaskKind[] = TASK_KINDS_FIXTURE
): FetchCall[] {
  const calls: FetchCall[] = [];
  globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method || "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    if (url.includes("/task-kinds") && method === "GET") {
      return { ok: true, status: 200, json: async () => taskKinds };
    }
    if (url.includes("/setup") && method === "GET") {
      return { ok: true, status: 200, json: async () => info };
    }
    if (url.includes("/setup") && method === "POST") {
      return {
        ok: true,
        status: 200,
        json: async () => ({ profile: {}, created: { projects: ["P1"], tasks: ["T1"] } }),
      };
    }
    throw new Error("unexpected fetch: " + method + " " + url);
  }) as unknown as typeof fetch;

  render(
    <MemoryRouter initialEntries={["/setup"]}>
      <MetaContext.Provider value={ctx}>
        <ToastProvider>{SetupScreen}</ToastProvider>
      </MetaContext.Provider>
    </MemoryRouter>
  );
  return calls;
}

function findPostBody(calls: FetchCall[]): unknown {
  const call = calls.find((c) => c.method === "POST" && c.url.includes("/setup"));
  return call?.body;
}

describe("setup wizard（ADR-007 §6 D7〜D9）", () => {
  afterEach(() => cleanup());

  it("すべての入力欄にプレースホルダーがある（呼び名・タスク題名など）", async () => {
    const user = userEvent.setup();
    renderSetup();
    await waitFor(() => expect(screen.getByLabelText("主人の呼び名")).toBeTruthy());
    expect(screen.getByPlaceholderText("例: 旦那様 ／ ご主人様")).toBeTruthy();
    expect(screen.getByPlaceholderText("例: 執事")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "次へ" })); // 呼び名 → 使いたい機能
    await waitFor(() => expect(screen.getByPlaceholderText("例: 論文の締切管理と、平日の献立")).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "次へ" })); // → 最初の仕事（tasks が既定 on）

    await waitFor(() => expect(screen.getAllByRole("button", { name: "+ 行を足す" })).toHaveLength(2));
    await user.click(screen.getAllByRole("button", { name: "+ 行を足す" })[1]); // タスクの行
    expect(screen.getByPlaceholderText("例: 関連研究を3本読む")).toBeTruthy();
  });

  // 検分 2026-09-05: 「使いたい機能」段の説明文（差し込みが要る i18n キー）で実機に
  // "undefined" がそのまま出た（呼び出し側が params を渡し忘れ、型検査も素通りしていた
  // ——store.ts の `t()` を、値が関数のキーは params 必須になるよう直した。ここは
  // その再発防止の実測: 説明文に APP_NAME が実際に埋まっていること・"undefined" という
  // 文字列がどこにも出ないことを確かめる）。
  it("「使いたい機能」段の説明文に AI Manor が埋まる（undefined のまま出ない）", async () => {
    const user = userEvent.setup();
    renderSetup();
    await waitFor(() => expect(screen.getByLabelText("主人の呼び名")).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "次へ" })); // 呼び名 → 使いたい機能

    await waitFor(() =>
      expect(screen.getByText(`${APP_NAME} に任せたいことを選びます。あとから増やせます。`)).toBeTruthy()
    );
    expect(screen.queryByText(/undefined/)).toBeNull();
  });

  it("呼び名を入力しなくても次へ進める（callname は必須ではない）", async () => {
    const user = userEvent.setup();
    renderSetup();
    await waitFor(() => expect(screen.getByLabelText("主人の呼び名")).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "次へ" }));
    await waitFor(() => expect(screen.getByText("タスク・プロジェクトの管理")).toBeTruthy());
  });

  it("使いたい機能の選択で段の構成が変わり、進捗表示（N/N）も追随する", async () => {
    const user = userEvent.setup();
    renderSetup();
    await waitFor(() => expect(screen.getByLabelText("主人の呼び名")).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "次へ" })); // → 使いたい機能

    // tasks が既定 on なので「最初の仕事」を含む4段（呼び名・使いたい機能・最初の仕事・確認）。
    await waitFor(() => expect(screen.getByText("3/4 最初の仕事")).toBeTruthy());
    expect(screen.queryByText(/台所の前提/)).toBeNull();

    // tasks を外し、kitchen を選ぶ → 最初の仕事が消え、台所の前提が出る。
    await user.click(screen.getByText("タスク・プロジェクトの管理"));
    await user.click(screen.getByText("料理・買い物"));

    await waitFor(() => expect(screen.queryByText(/最初の仕事/)).toBeNull());
    expect(screen.getByText("3/4 台所の前提")).toBeTruthy();
    expect(screen.getByText("4/4 確認")).toBeTruthy();
  });

  it("戻って使いたい機能を変えると、あとの段が再計算される（隠れた段の入力は送られない）", async () => {
    const user = userEvent.setup();
    const calls = renderSetup();
    await waitFor(() => expect(screen.getByLabelText("主人の呼び名")).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "次へ" })); // → 使いたい機能

    await user.click(screen.getByText("料理・買い物")); // kitchen も on（tasks はそのまま on）
    await user.click(screen.getByRole("button", { name: "次へ" })); // → 最初の仕事

    await waitFor(() => expect(screen.getByRole("button", { name: "戻る" })).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "戻る" })); // → 使いたい機能 に戻る
    await user.click(screen.getByText("料理・買い物")); // kitchen を外す
    await user.click(screen.getByRole("button", { name: "次へ" })); // → 最初の仕事（台所の前提は消えた）

    await waitFor(() => expect(screen.getByRole("button", { name: "あとで" })).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "あとで" })); // 最初の仕事を飛ばす

    // 台所の前提はもう選ばれていないので、確認には出ずそのまま登録するボタンに着く。
    await waitFor(() => expect(screen.getByRole("button", { name: "登録する" })).toBeTruthy());
    expect(screen.queryByText(/台所の前提/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "登録する" }));

    await waitFor(() => expect(findPostBody(calls)).toBeTruthy());
    const body = findPostBody(calls) as Record<string, unknown>;
    expect(body.kitchen).toBeUndefined();
    expect(body.money).toBeUndefined();
  });

  it("台所・家計の段は入力していても「あとで」を押せばその段は body から省かれる", async () => {
    const user = userEvent.setup();
    const calls = renderSetup();
    await waitFor(() => expect(screen.getByLabelText("主人の呼び名")).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "あとで" })); // 呼び名を空にして進む

    // 使いたい機能: kitchen・money も選んでおき、それぞれの段で「あとで」を押せることを確かめる。
    await waitFor(() => expect(screen.getByText("料理・買い物")).toBeTruthy());
    await user.click(screen.getByText("料理・買い物"));
    await user.click(screen.getByText("家計"));
    await user.click(screen.getByRole("button", { name: "次へ" })); // → 最初の仕事（tasks は既定 on のまま）

    await waitFor(() => expect(screen.getByRole("button", { name: "あとで" })).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "あとで" })); // 最初の仕事を飛ばす

    await waitFor(() => expect(screen.getByLabelText("何人分")).toBeTruthy());
    await user.type(screen.getByLabelText("何人分"), "3"); // 入力しても
    await user.click(screen.getByRole("button", { name: "あとで" })); // あとでを押せば送られない

    await waitFor(() => expect(screen.getByLabelText("通貨")).toBeTruthy());
    await user.clear(screen.getByLabelText("通貨"));
    await user.type(screen.getByLabelText("通貨"), "USD");
    await user.click(screen.getByRole("button", { name: "あとで" })); // 家計の前提も飛ばす

    await waitFor(() => expect(screen.getByRole("button", { name: "登録する" })).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "登録する" }));

    await waitFor(() => expect(findPostBody(calls)).toBeTruthy());
    const body = findPostBody(calls) as Record<string, unknown>;
    expect(body.callname).toBe("");
    expect(body.butler_name).toBeUndefined();
    expect(body.projects).toEqual([]);
    expect(body.tasks).toEqual([]);
    expect(body.kitchen).toBeUndefined();
    expect(body.money).toBeUndefined();
    // purposes 自体はあとでで消していないので選んだままになる。
    expect(body.purposes).toEqual(["tasks", "kitchen", "money"]);
  });

  it("執事の裁定（2026-09-03）: 呼び名から確認まで全部「あとで」で進むと、使いたい機能は推奨既定（tasks だけ）に戻り、body は最小構成になる", async () => {
    const user = userEvent.setup();
    const calls = renderSetup();
    await waitFor(() => expect(screen.getByLabelText("主人の呼び名")).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "あとで" })); // 呼び名を空にして進む

    // 使いたい機能: 何も選ばず「あとで」→ 推奨既定（tasks だけ on）に戻る。全部消えるわけではない。
    await waitFor(() => expect(screen.getByText("料理・買い物")).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "あとで" }));

    // tasks が推奨既定で on のままなので、「最初の仕事」の段が出る。そこも「あとで」で飛ばせる。
    await waitFor(() => expect(screen.getByRole("button", { name: "あとで" })).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "あとで" }));

    // kitchen・money は選ばれていないので、その段は出ずそのまま確認に着く。
    await waitFor(() => expect(screen.getByRole("button", { name: "登録する" })).toBeTruthy());
    expect(screen.queryByText(/台所の前提/)).toBeNull();
    expect(screen.queryByText(/家計の前提/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "登録する" }));

    await waitFor(() => expect(findPostBody(calls)).toBeTruthy());
    expect(findPostBody(calls)).toMatchObject({
      callname: "",
      purposes: ["tasks"],
      projects: [],
      tasks: [],
    });
    const body = findPostBody(calls) as Record<string, unknown>;
    expect(body.butler_name).toBeUndefined();
    expect(body.kitchen).toBeUndefined();
    expect(body.money).toBeUndefined();
  });

  it("執事の裁定（2026-09-03）: 家計の段を訪れて何も変えず次へ進むと、既定 {app:none, currency:JPY} を送り、確認に「（既定）」と出す", async () => {
    const user = userEvent.setup();
    const calls = renderSetup();
    await waitFor(() => expect(screen.getByLabelText("主人の呼び名")).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "次へ" })); // → 使いたい機能

    await waitFor(() => expect(screen.getByText("家計")).toBeTruthy());
    await user.click(screen.getByText("家計")); // money を on（tasks は既定 on のまま）
    await user.click(screen.getByRole("button", { name: "次へ" })); // → 最初の仕事

    await waitFor(() => expect(screen.getByRole("button", { name: "あとで" })).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "あとで" })); // 最初の仕事を飛ばす

    // 家計の前提: 何も変えず次へ（あとでではない）。
    await waitFor(() => expect(screen.getByLabelText("家計簿アプリ")).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "次へ" }));

    await waitFor(() => expect(screen.getByText(/使っていない.*JPY（既定）/)).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "登録する" }));

    await waitFor(() => expect(findPostBody(calls)).toBeTruthy());
    const body = findPostBody(calls) as Record<string, unknown>;
    expect(body.money).toEqual({ app: "none", currency: "JPY" });
  });

  it("台所・家計まで含めて一通り進めると、POST の body が契約どおりになる", async () => {
    const user = userEvent.setup();
    const ctx = fakeMetaContext();
    const calls = renderSetup(baseSetupInfo(), ctx);
    await waitFor(() => expect(screen.getByLabelText("主人の呼び名")).toBeTruthy());

    // 1段目: 呼び名
    await user.type(screen.getByLabelText("主人の呼び名"), "主人");
    await user.type(screen.getByLabelText("執事の呼び名"), "セバスチャン");
    await user.click(screen.getByRole("button", { name: "次へ" }));

    // 2段目: 使いたい機能（tasks は既定 on。kitchen・money を追加で選ぶ）
    await waitFor(() => expect(screen.getByText("料理・買い物")).toBeTruthy());
    await user.click(screen.getByText("料理・買い物"));
    await user.click(screen.getByText("家計"));
    await user.type(screen.getByLabelText("ほかにしてほしいこと"), "論文の締切管理と、平日の献立");
    await user.click(screen.getByRole("button", { name: "次へ" }));

    // 3段目: 最初の仕事（プロジェクト・タスク）
    await waitFor(() => expect(screen.getAllByRole("button", { name: "+ 行を足す" })).toHaveLength(2));
    await user.click(screen.getAllByRole("button", { name: "+ 行を足す" })[0]);
    await user.type(screen.getByLabelText("名前"), "博士論文");
    // 日本語の名前からは ascii の記号候補が作れないので p1 になる（既存の suggestProjectCode の仕様）。
    // 記号欄はプレースホルダーの例（thesis）を参考に手で入れ直せる。
    expect(screen.getByLabelText("記号")).toHaveValue("p1");
    expect(screen.getByLabelText("記号")).toHaveAttribute("placeholder", "例: thesis");
    await user.clear(screen.getByLabelText("記号"));
    await user.type(screen.getByLabelText("記号"), "thesis");
    await user.click(screen.getAllByRole("button", { name: "+ 行を足す" })[1]);
    await user.type(screen.getByLabelText("題名"), "関連研究を3本読む");
    // ADR-010 D2: タスクの種類は任意。ここでは選んで、body に kind として乗ることを確かめる
    // （行動クラスはもうここに無い——D1）。
    await user.selectOptions(screen.getByLabelText("タスクの種類"), "research");
    await user.click(screen.getByRole("button", { name: "次へ" }));

    // 4段目: 台所の前提
    await waitFor(() => expect(screen.getByLabelText("何人分")).toBeTruthy());
    await user.type(screen.getByLabelText("何人分"), "2");
    await user.type(screen.getByLabelText("アレルギー"), "えび、そば");
    await user.type(screen.getByLabelText("苦手なもの"), "セロリ");
    await user.click(screen.getByRole("button", { name: "次へ" }));

    // 5段目: 家計の前提
    await waitFor(() => expect(screen.getByLabelText("家計簿アプリ")).toBeTruthy());
    await user.selectOptions(screen.getByLabelText("家計簿アプリ"), "zaim");
    await user.clear(screen.getByLabelText("通貨"));
    await user.type(screen.getByLabelText("通貨"), "JPY");
    await user.click(screen.getByRole("button", { name: "次へ" }));

    // 6段目: 確認 → 登録する
    await waitFor(() => expect(screen.getByRole("button", { name: "登録する" })).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "登録する" }));

    await waitFor(() => expect(findPostBody(calls)).toBeTruthy());
    expect(findPostBody(calls)).toEqual({
      callname: "主人",
      butler_name: "セバスチャン",
      purposes: ["tasks", "kitchen", "money"],
      note: "論文の締切管理と、平日の献立",
      projects: [{ code: "thesis", name: "博士論文", preset: "standard" }],
      tasks: [{ title: "関連研究を3本読む", kind: "research" }],
      kitchen: { household_size: 2, allergies: "えび、そば", dislikes: "セロリ" },
      money: { app: "zaim", currency: "JPY" },
    });
    expect(ctx.markCalls).toBe(1);
  });

  it("ADR-010 D2: 新しい行のタスクの種類の既定は（未選択）で、選ぶと選択した種類が保持される", async () => {
    const user = userEvent.setup();
    renderSetup();
    await waitFor(() => expect(screen.getByLabelText("主人の呼び名")).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "次へ" }));
    await waitFor(() => expect(screen.getByText("タスク・プロジェクトの管理")).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "次へ" })); // → 最初の仕事（tasks 既定 on）

    await waitFor(() => expect(screen.getAllByRole("button", { name: "+ 行を足す" })).toHaveLength(2));
    await user.click(screen.getAllByRole("button", { name: "+ 行を足す" })[1]);
    await waitFor(() => expect(screen.getByLabelText("タスクの種類")).toBeTruthy());
    expect(screen.getByLabelText("タスクの種類")).toHaveValue("");
    expect(screen.getByLabelText("タスクの種類")).toHaveDisplayValue("（未選択）");

    await user.selectOptions(screen.getByLabelText("タスクの種類"), "build");
    expect(screen.getByLabelText("タスクの種類")).toHaveValue("build");
  });

  it("done: true のときは「やり直し」の案内を出し、既存プロフィールで呼び名・使いたい機能を埋める", async () => {
    renderSetup(
      baseSetupInfo({
        done: true,
        completed_at: "2026-09-01T00:00:00",
        profile: {
          "master.callname": "旦那様",
          "butler.callname": "セバスチャン",
          purposes: JSON.stringify(["tasks"]),
          "purposes.note": "既存メモ",
        },
      })
    );

    await waitFor(() => expect(screen.getByText(/すでに設定済みです/)).toBeTruthy());
    expect(screen.getByLabelText("主人の呼び名")).toHaveValue("旦那様");
    expect(screen.getByLabelText("執事の呼び名")).toHaveValue("セバスチャン");
  });
});

// 主人のフィードバック（2026-09-04）に対する ADR-010 の修正点。§4 の試験一覧に沿う:
// 「行動クラスの選択欄が無いこと／説明文が出ていること／期限が任意であること」。
describe("setup wizard（ADR-010: 主人のフィードバックへの対応）", () => {
  afterEach(() => cleanup());

  async function gotoWork(user: ReturnType<typeof userEvent.setup>) {
    await waitFor(() => expect(screen.getByLabelText("主人の呼び名")).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "次へ" }));
    await waitFor(() => expect(screen.getByText("タスク・プロジェクトの管理")).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "次へ" })); // → 最初の仕事（tasks 既定 on）
    await waitFor(() => expect(screen.getAllByRole("button", { name: "+ 行を足す" })).toHaveLength(2));
  }

  it("§3: 各段の先頭にも同じ形の見出し（段の題＋一行）が出る（呼び名の段の例）", async () => {
    renderSetup();
    await waitFor(() => expect(screen.getByLabelText("主人の呼び名")).toBeTruthy());
    expect(screen.getByRole("heading", { level: 2, name: "呼び名" })).toBeTruthy();
    expect(screen.getByText("主人と執事の呼び名を決めます。あとから変えられます。")).toBeTruthy();
  });

  it("D1: 行動クラスの選択欄がどこにも無い（プロジェクト行・タスク行のどちらにも）", async () => {
    const user = userEvent.setup();
    renderSetup();
    await gotoWork(user);
    await user.click(screen.getAllByRole("button", { name: "+ 行を足す" })[0]); // プロジェクト行
    await user.click(screen.getAllByRole("button", { name: "+ 行を足す" })[1]); // タスク行

    expect(screen.queryByLabelText("行動クラス")).toBeNull();
    expect(screen.queryByText(/行動クラス/)).toBeNull();
  });

  it("D1・D2: cls は送らない。タスクの種類を選ばなければ kind も送らない", async () => {
    const user = userEvent.setup();
    const calls = renderSetup();
    await gotoWork(user);
    await user.click(screen.getAllByRole("button", { name: "+ 行を足す" })[1]);
    await user.type(screen.getByLabelText("題名"), "見積もりを取る");
    await user.click(screen.getByRole("button", { name: "次へ" })); // → 確認
    await waitFor(() => expect(screen.getByRole("button", { name: "登録する" })).toBeTruthy());
    await user.click(screen.getByRole("button", { name: "登録する" }));

    await waitFor(() => expect(findPostBody(calls)).toBeTruthy());
    const body = findPostBody(calls) as Record<string, unknown>;
    expect(body.tasks).toEqual([{ title: "見積もりを取る" }]); // cls も kind も無い
  });

  it("D3: プロジェクトに『期限』欄は無く、代わりに節目への案内が出る", async () => {
    const user = userEvent.setup();
    renderSetup();
    await gotoWork(user);
    await user.click(screen.getAllByRole("button", { name: "+ 行を足す" })[0]); // プロジェクト行

    // 旧「期限」欄（プロジェクト行）は消えている。タスク行の「期限（任意）」とは別ラベルなので
    // 厳密一致で確かめる（まだタスク行は足していないので、なおのこと「期限」単体は無い）。
    expect(screen.queryByLabelText("期限")).toBeNull();
    expect(screen.getByText("期限のあるものは、あとから節目として足せます。")).toBeTruthy();
  });

  it("D4: プロジェクト・タスク・プリセット・タスクの種類の説明が、フォーカスや操作をしなくても常に見えている", async () => {
    const user = userEvent.setup();
    renderSetup();
    await gotoWork(user);
    // ここではまだ行を1つも足していない・どの入力にも触れていない——それでも説明は出ている。

    expect(
      screen.getByText(
        "関連するタスクをまとめる入れ物です。登録しておくと、タスクの紐づけと全体の進み具合を執事が引き受けます。"
      )
    ).toBeTruthy();
    expect(screen.getByText("ひとつの完結した作業です。期限は無くても構いません。")).toBeTruthy();
    expect(
      screen.getByText(
        // 主人の指摘（2026-09-04）: 「プリセット」ではなく「自律レベル」。絵文字は要らない。
        "自律レベル: 執事がどれくらい自分で判断して進めるかの目安です。慎重＝確認を多めに取る／標準／高速＝任せる範囲を広げる。あとから変えられます。"
      )
    ).toBeTruthy();
    expect(
      screen.getByText("タスクの種類: あとで振り返るための分類です。迷ったら「その他」で構いません。")
    ).toBeTruthy();
  });

  it("D5: タスクの期限は『期限（任意）』のラベルと補助文を持ち、空なら確認画面の行ごと出ない", async () => {
    const user = userEvent.setup();
    renderSetup();
    await gotoWork(user);
    await user.click(screen.getAllByRole("button", { name: "+ 行を足す" })[1]);

    expect(screen.getByLabelText("期限（任意）")).toBeTruthy();
    expect(screen.getByText("決まっていなければ空のままで構いません")).toBeTruthy();

    await user.type(screen.getByLabelText("題名"), "タスクA");
    await user.click(screen.getByRole("button", { name: "次へ" })); // → 確認（期限は空のまま）

    await waitFor(() => expect(screen.getByRole("button", { name: "登録する" })).toBeTruthy());
    expect(screen.getByText("タスクA")).toBeTruthy();
    // 「—」のようなプレースホルダーも、「期限:」という行自体も出ない（無いものを見せない）。
    expect(screen.queryByText(/期限:/)).toBeNull();
    expect(screen.queryByText("—")).toBeNull();
  });

  it("D5: タスクの期限を入れたときは確認画面に『期限: 日付』が出る", async () => {
    const user = userEvent.setup();
    renderSetup();
    await gotoWork(user);
    await user.click(screen.getAllByRole("button", { name: "+ 行を足す" })[1]);
    await user.type(screen.getByLabelText("題名"), "タスクB");
    fireEvent.change(screen.getByLabelText("期限（任意）"), { target: { value: "2026-12-01" } });
    await user.click(screen.getByRole("button", { name: "次へ" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "登録する" })).toBeTruthy());
    expect(screen.getByText(/タスクB.*期限: 2026-12-01/)).toBeTruthy();
  });

  // D6: 入力欄の重なりの修正（.setup-row の折り返しで、.form-input の min-width が
  // .form-row の min-width より広く、隣の行へはみ出していた）。
  //
  // 幾何（getBoundingClientRect）による「矩形が交差しないこと」の検算は、jsdom では
  // レイアウトエンジンが実際には走らず、あらゆる要素が {x:0, y:0, width:0, height:0} を
  // 返すため意味を持たない——全要素が原点の面積ゼロ矩形になり、どんな交差判定を書いても
  // 「常に重ならない（あるいは常に重なる）」という無意味な結果にしかならず、それは
  // 「通るふりをした試験」になってしまう。そのため、ここでは幾何の代わりに
  // 「各フィールドはそれぞれ専用の .form-row を持ち、ラベル1つ＋入力/選択1つだけを
  // 直下に持つ」という構造契約を検算する（別のフィールドの要素を巻き込んでいれば、
  // それはレイアウトが崩れて要素同士が絡んでいる兆候）。
  //
  // 実際の見た目の重なりが無いことは、1280/1024/860/600px の4幅で本物のブラウザ
  // （このセッションでは Vite の開発サーバ + Claude のブラウザツール）に描画して
  // 目視で確認した。詳細と証跡は作業報告の「証跡」節に書く。
  it("D6: .setup-row の各 .form-row はラベル1つ＋入力/選択1つだけを持つ独立した構造である", async () => {
    const user = userEvent.setup();
    renderSetup();
    await gotoWork(user);
    await user.click(screen.getAllByRole("button", { name: "+ 行を足す" })[0]); // プロジェクト行
    await user.click(screen.getAllByRole("button", { name: "+ 行を足す" })[1]); // タスク行

    const setupRows = document.querySelectorAll(".setup-row");
    expect(setupRows.length).toBe(2); // プロジェクト1行・タスク1行

    setupRows.forEach((row) => {
      const formRows = Array.from(row.children).filter((el) => el.classList.contains("form-row"));
      expect(formRows.length).toBeGreaterThan(0);
      formRows.forEach((fr) => {
        // ラベルが先頭の子要素であること（重なりの原因だった「ラベルの下に入力」という
        // 積み上げの前提——flex-direction: column の1列目がラベルであること——が保たれている）。
        expect(fr.firstElementChild?.tagName).toBe("LABEL");
        // 直下の入力/選択はちょうど1つ（他フィールドの入力を巻き込んでいない）。
        const controls = Array.from(fr.children).filter((el) => el.tagName === "INPUT" || el.tagName === "SELECT");
        expect(controls.length).toBe(1);
      });
    });
  });
});
