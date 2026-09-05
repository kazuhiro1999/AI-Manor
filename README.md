*English README → [README.en.md](README.en.md)*

# AI Manor

**家のことを任せられる AI 執事と、その部下たち。** [Claude Code](https://claude.com/claude-code) の上で動く、
自分の PC の中だけで完結する個人用のアシスタントです。

執事に話しかけると、タスクを預かり、段取りを決め、必要なら部下に振ります。部下は台所・家事・家計・予定を
それぞれ担当し、献立を考え、買い物リストを作り、消耗品の残量を見て、支出を記録し、今日の予定を並べます。
やり取りはすべて手元のデータベースに残り、Web アプリからも見られます。**外部のサービスへは何も送りません。**

![ダッシュボード](docs/screenshots/dashboard.png)

*画面例はすべて `manor init --demo` の**架空の家庭**です。*

**目次** — [頼めること](#こんなことを頼めます)／[クイックスタート](#クイックスタート)／
[画面](#画面)／[誰が何を預かるか](#誰が何を預かるか)／[そのほかの機能](#そのほかの機能)／
[手元のデータ](#手元のデータの扱い)／[文書](#文書の地図)／[ライセンス](#ライセンス)

## こんなことを頼めます

| 言うこと | 起きること |
|---|---|
| 「牛乳と卵を買っておいて」 | 買い物リストに入り、次に台所を開いたときに出る |
| 「冷蔵庫にあるもので夕飯を考えて」 | 在庫と好み・アレルギーを見て献立を出す。作ったら記録に残る |
| 「来週の水曜に歯医者」 | 予定に入り、当日の朝の一覧と小窓の知らせに出る |
| 「今月いくら使った？」 | 費目ごとの内訳と、予算との差を出す |
| 「この件、どうなってる？」 | そのタスクに繋がる判断・メモ・関連タスクだけを集めて答える |
| 「洗剤が切れそう」 | 消耗品の残量を下げ、切れる前に買い物リストへ回す |

判断に迷うこと（外部への送信・公開・削除・課金）は**勝手に実行せず、承認待ちとして積まれます**。

## クイックスタート

**前提**: [uv](https://docs.astral.sh/uv/)・[Claude Code](https://claude.com/claude-code)・Node.js。
Windows と macOS で動作確認しています（OS 固有スクリプトを持たない設計なので Linux でも動くはずです）。

```bash
uv sync                        # 依存を入れる
uv run manor init --demo       # DB と、試せる合成データ（架空の家庭）
uv run manor web build         # 画面を組む（初回のみ）
uv run manor web serve --open  # http://127.0.0.1:8789/
git config core.hooksPath .githooks   # git 管理下に置くなら（漏れを止めるフック）
```

続けて、このフォルダを **Claude Code** で開いてください。開いた時点で執事として振る舞います
（初回の「このワークスペースを信頼するか」は受け入れてください。受け入れないと許可リストと hooks が効きません）。

自分の家庭で使うときは `--demo` を外します。空の状態で画面を開くと**初回セットアップ**に入り、
呼び名・使いたい機能・最初のプロジェクトとタスクを順に聞かれます（どの段も「あとで」で飛ばせます）。

全コマンドが `manor <group> <verb> [...] [--json]` の形です。正確な引数は `manor <group> --help` で。

## 画面

![台所](docs/screenshots/kitchen.png)

在庫は期限順、買い物リストは売り場別、食事は記録として残ります。ほかの画面は
[`docs/screenshots/`](docs/screenshots/)、構成図は [`docs/diagrams/`](docs/diagrams/)（対話版あり）。

## 誰が何を預かるか

| 誰 | 頼めること（例） | CLI グループ |
|---|---|---|
| 執事 butler | タスク・プロジェクトの管理、判断待ちの裁定、部下への委譲、整合検査、文脈の組み立て | `manor task` `project` `decision` `handoff` `check` `ctx` |
| 料理長 chef | 在庫、献立の提案・記録、買い物リスト、好み・アレルギー | `manor chef` |
| 家政婦 housekeeper | 家事当番、消耗品の残量、設備の手入れ周期、ゴミの日 | `manor house` |
| 家令 steward | 支出・収入、定期支払いの期日、予算との差、月別の傾向 | `manor money` |
| 秘書 secretary | 予定・控え、日次一覧、inbox の仕分け、相対日付の解決 | `manor sec` |
| 検分 qa | 作ったものを検める。直さない | `manor talk qa` |
| 監査 auditor | 執事自身の規則を月1で外から検める | `manor talk auditor` |

**「なんでも屋」は作りません。** 担当を立てるのは繰り返し出てきて完結した領域だけで、各担当は自分の表にしか
書けません。何を頼めるかは [`docs/staff/`](docs/staff/)。執事を介さず直接話すなら `manor talk <name>`。

## そのほかの機能

| 機能 | 何をするか | 詳しく |
|---|---|---|
| **姿の小窓** `manor face` | 画面の隅に VRM の姿を出し、話しかけて頼み事もできる。担当ごとに姿と声を持てる（既定のアバターを同梱） | [`docs/face.md`](docs/face.md) |
| **声かけ** `manor notify` | 判断待ちが**増えたときだけ**、深夜を避けて一度だけ音声で知らせる | [`docs/notify.md`](docs/notify.md) |
| **スマホから** Tailscale | `tailscale serve` と passcode。公開 URL は作りません | [`docs/tailscale.md`](docs/tailscale.md) |
| **カレンダー** `manor calendar` | ICS を読み、予定に取り込む（読むだけ。書き戻しません） | [ADR-012](docs/design/ADR-012_calendar_and_i18n.md) |
| **夜勤** `manor night` | 就寝中に指示書へ書いた作業だけを自走させる（OS への登録は既定オフ） | [`docs/night.md`](docs/night.md) |
| **起動ショートカット** `manor shortcut create` | デスクトップに作る。開くと止めて→ビルド→起動→ブラウザ | [`docs/shortcut.md`](docs/shortcut.md) |
| **家庭のルール** `manor rule` | 門限・来客対応などを scope と tag つきで置く | [`docs/rules.md`](docs/rules.md) |
| **日本語 / 英語** | 画面もコマンドも切り替えられます（「設定 → 言語」） | [ADR-012](docs/design/ADR-012_calendar_and_i18n.md) |

声（VOICEVOX）・Slack・Notion は任意の拡張機能です。**1つも入れなくても完全に動きます**
（[`docs/web.md`](docs/web.md)・[`docs/voice.md`](docs/voice.md)）。

## 手元のデータの扱い

- **外部へ送りません。** 手元の SQLite を読み書きするだけで、外へ出る操作は必ず承認待ちになります
- **あなたのデータは git に入りません。** `home/`（DB・呼び名・タスク・予定・家計）は既定で追跡外です
- **コミット前に漏れを止めます。** `~/.manor/git-leak-terms.txt`（人名・案件名など。**リポジトリの外**）に
  載せた語が混ざっていると、pre-commit がコミットを止めます

## 文書の地図

| 読みたいもの | どこ |
|---|---|
| 各担当に何を頼めるか | [`docs/staff/`](docs/staff/) |
| 設計判断そのもの（ADR） | [`docs/design/`](docs/design/) |
| 全体の索引 | [`docs/README.md`](docs/README.md) |
| 開発の現在地（執事の作業記録） | [`ROADMAP.md`](ROADMAP.md) |
| 機能の変更履歴 | [`CHANGELOG.md`](CHANGELOG.md) |

## ライセンス

**MIT License**（[`LICENSE`](LICENSE)）。

**ただし同梱アバター `assets/face/default.vrm` は対象外です。**
[VRoid Studio](https://vroid.com/studio) でピクシブ株式会社の公式サンプルモデル **AvatarSample** をもとに
作成したもので、[サンプルモデルの利用条件](https://vroid.pixiv.help/hc/ja/articles/4402394424089-AvatarSample-A-Z)に従います
——無償での再配布・改変・商用利用は認められていますが、**有償での再配布は禁じられています**
（[`assets/face/NOTICE.md`](assets/face/NOTICE.md)）。小窓に自分の姿を出すときは、同梱ファイルを
置き換える必要はありません——「設定 → 姿（小窓）」からアップロードすれば、そちらが優先されます。
