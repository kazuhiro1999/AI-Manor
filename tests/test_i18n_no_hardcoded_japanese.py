"""`src/manor/` の中に、翻訳を通していない主人向けの日本語のべた書きが残っていないかを
機械で検算する試験（ADR-012 §3 D9・5h-2）。`web/src/app/i18n/noHardcodedJapanese.test.ts`
の CLI 版——完全ではないが、訳し忘れが黙って通らない形にする。

**コメントと docstring は対象外**（日本語のまま維持する方針。ADR-012・主人の指示どおり）。
Python の `ast` モジュールでソースを構文木にし、コメントは最初から構文木に載らない
（自動的に除外される）。docstring は「モジュール／クラス／関数の本体の最初の文が
文字列リテラルだけの式文」という定義で判定し、その行範囲を除外する。

**`ManorError` の `message_ja` は対象外**（errors.py の docstring 参照——CLI と Web
バックエンドの両方から使われる共有コードの例外は、`message_ja` を常に日本語のまま
保つ設計にした。これは意図的な「訳さない」であって訳し忘れではない）。
`ManorError(...)` 呼び出しの最初の引数（文字列リテラルまたは f-string）の行範囲を
allowlist として自動収集し、検算から外す。

それでも残る「意図して日本語のまま残す」ものは、ALLOWLIST（ファイル単位）・
LINE_ALLOWLIST（行単位）に理由つきで明示する。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
# 中点(・ U+30FB)だけは除く——この2文字幅の区切り文字は日本語の文というより
# 単なる列挙の区切り(`"・".join(...)`)として両方の言語で使っている箇所がある
# (`rule.py`・`gate.py` 等)。他の日本語文字と一緒に現れれば、その文字のほうで
# 引っかかるので検算の抜けにはならない——「・」1文字だけの文字列を誤検知しない
# ようにするだけの調整。
JA_CHAR_PATTERN = re.compile(r"[぀-ヺー-ヿ㐀-鿿]")

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "manor"
# ファイル単位の許可。3つの理由のどれかに当てはまる(コメントで明示する)。
#
#   [データ] 画面の文言ではなく、語彙・持続する記録・外部サービスへ投函する実データ
#            (主人が入れたデータと同じ扱い。訳すと記録・照合・書式互換が壊れる)。
#   [共有]   web/board バックエンドの API 応答ともそのまま共有するコード
#            (ここを訳すと web 側の言語設定に関わらず表示が変わってしまう。
#            5h-1 が完成させた web/ には触らない約束——ADR-012 D13)。
#   [エージェント向け] 主人が端末で直接読む出力ではなく、Claude セッション自身が読む
#            文脈注入・委譲の指示書・射影ファイル・夜勤レポート(CLAUDE.md
#            「射影は hook が起動時に文脈へ注入します」)。5h-2 の対象は
#            「主人が読むコマンドの結果・エラー文・--help」であり、これらは
#            一次の読み手が人間の英語話者ではない。**5h-3 相当の別作業として
#            報告に明記し、ここでは範囲外とする**(訳し忘れではなく未着手)。
ALLOWLIST: set[str] = {
    # [データ] task_kind の既定ラベル(ADR-010 D2)は主人が改名できる設定——policy class
    # ラベルを訳さない判断と同じ理由。
    "task_kind.py",
    "policy.py",
    # [共有]+[データ] 担当の日本語表示名(_LABEL_OVERRIDES・AGENT_SUMMARY)。`agent_label()`
    # は `face_pin.py` の窓タイトル照合と共有するため、言語に応じて変えるとピン留めが
    # 壊れる(ADR-011 D7・指示書に明記)。web 側でのみ使われており、CLI 自体はこの値を
    # 出力に使っていない。
    "agent_meta.py",
    # [データ] Notion に実際に投函するページのタグ(D18)。Notion 側に保存される実データ。
    "notion.py",
    # [データ] 買い物のアイル区分(VALID_AISLES)。task_kind と同じ「閉じた語彙」。
    "staff/chef/cli.py",
    # [データ] staff/chef/nutrition.py（ADR-019・2026-09-13）: 食品成分表の取り込みと
    # 名寄せ。日本語の文字列は**全部データ**——成分表の列見出しを探す語（「食品番号」
    # 「たんぱく質」「食塩相当量」…）、名寄せで「生」を優先するための調理法の語、
    # `Tr`（微量）等の記法。`lexicon.toml` の語彙・`recipe_shaping` の手がかり語と同じ
    # 扱いで、画面の文言ではない（CLI の結果行は `chef.food.*`／`chef.nutrition.*` と
    # して訳し、画面は `kitchen.nutrition.*` を持つ）。**行単位ではなくファイル単位で
    # 許す**——この module は語彙の塊で、行番号で固定すると編集のたびに壊れる
    # （`task.py` の許可が3日で2度ずれた前例）。
    "staff/chef/nutrition.py",
    # [データ] staff/chef/companion.py（ADR-021・2026-09-25）: お供の提案。日本語の文字列は
    # 全部 `lexicon.toml` の語彙と突き合わせる語（型の「生」「汁物」・印の「生野菜」）と、
    # 定番をレシピ帳へ昇格するときに DB に永続するレシピ本体の語（工程の段「作る」・
    # 「手順N」）。理由は符牒で返し、画面が `kitchen.companion.reason.*` で訳す。
    # nutrition.py と同じくファイル単位で許す。
    "staff/chef/companion.py",
    # [エージェント向け]+[データ] staff/chef/food_resolve.py（ADR-022）: `claude -p` へ渡す調べ方の指示文
    # （receipt_reader.py の読み取りの指示と同じ扱い）と、DB に残す記録の語。画面の文言は
    # Web が `settings.food.*` で訳す。
    "staff/chef/food_resolve.py",
    # [エージェント向け]+[単体配布] remote/client/manor_report.py（ADR-025）: 他のPCに1本で置く
    # 送信の道具。manor を入れないPCで動くので i18n を import できない。日本語は、セッションの
    # Claude に注入する報告の作法と、段階の語彙（ダッシュボードの GAS 側の表示と共有）。
    "remote/client/manor_report.py",
    # [データ]+[エージェント向け] remote/store.py・remote/relay.py（ADR-025）: タスクの「現在地」に
    # 書く記録の文（DB に残る実データ）と、起動時の射影に載せる執事向けの行。画面は Web が
    # `sessions.*` で訳す。
    "remote/store.py",
    "remote/relay.py",
    # [執事の道具・未移行] remote/cli.py（ADR-025・2026-10-09）: `manor remote ...` の結果行。
    # 主人のご要望で即日入れたため i18n を後回しにした。夜勤（T119）で `cli.remote.*` へ移し、
    # この行を消す。
    "remote/cli.py",
    # [データ]+[検証] staff/chef/youtube.py: レシピの見出し・量の語彙（「材料」「作り方」「大さじ」…）を
    # 正規表現に持つ（recipe_shaping と同じ扱い）。extensions/youtube.py は他の拡張のマニフェストと同じ [共有]。
    "staff/chef/youtube.py",
    "extensions/youtube.py",
    # [データ] JSON 出力のキーとして使われる日本語ラベル(`tests/staff/test_housekeeper.py`
    # が実際にこの文字列をキーとして検算している——データ契約であって UI 文言ではない。
    # chef/cli.py の VALID_AISLES と同じ判断。5h-2 のサブエージェントが検分して報告)。
    "staff/housekeeper/cli.py",
    # [データ] 銀行・家計簿サービスの CSV 列名プリセット(zaim/moneyforward)と
    # 収入/支出の判定語彙(ADR-005 §2)。CSV というファイル形式側の語彙であって
    # UI 文言ではない。
    "staff/steward/importer.py",
    # [データ] v1(旧 QUEUE.md/PROJECTS.md)の Markdown 表の列見出し・区分そのもの。
    # ここを訳すと既存の v1 ファイルを読めなくなる(パーサが日本語の見出し文字列に
    # 一致させている)。ファイル形式の互換層であって画面の文言ではない。
    "compat/v1/mdtable.py",
    "compat/v1/projects_doc.py",
    "compat/v1/queue_doc.py",
    "import_v1.py",
    # [データ] ICS の未対応部分を示す注記マーカー(`[TZID未解決]` 等)。予定の note 列に
    # 永続する記録で、secretary 側にも同じ文字列一致の判定がある(ADR-012 D5)。
    "ics.py",
    # [データ] `--demo` で入れる合成データ(架空の家庭のタスク・献立・当番など)。
    # 実在しない家の練習用データそのもので、画面の文言ではない。
    "demo.py",
    # [共有] 拡張機能のマニフェスト(label/summary/install_steps/fields)。`manor ext show`
    # と web の設定画面の両方がこの同じ辞書を読む(`extensions/__init__.py` 経由)。
    # CLI 側だけ訳すと同じマニフェストが画面と端末で違う言語になってしまうため、
    # このマニフェスト定義自体は今回の範囲外とする(枠組み側の `extensions/__init__.py`
    # は CLI コマンドとして別に訳した)。
    "extensions/calendar.py",
    "extensions/receipt_ocr.py",
    # [データ]+[エージェント向け] レシートの読み取り（ADR-020・2026-09-22）: `receipt_parse.py` は
    # レシートの合計欄の語（小計・合計・お預り…）と誤読の型、`receipt_reader.py` は `claude -p` へ渡す
    # 読み取り・分類の指示文（recipe_import の STRUCTURE_PROMPT_TEMPLATE と同じ扱い）、
    # `receipt_classify.py`／`receipts.py` は語彙の既定（「未分類」「値引き」「レシート」）で、
    # 明細や支出の memo として DB に永続する実データ。画面の文言は Web が `money.receipts.*` で訳す。
    "staff/steward/receipt_checks.py",  # 品名の名寄せで剥がす記号の表（データ）
    "staff/steward/receipt_parse.py",
    "staff/steward/receipt_reader.py",
    "staff/steward/receipt_classify.py",
    "staff/steward/receipts.py",
    "staff/steward/breakdown.py",  # 語彙の既定（「未分類」「その他」「（店名なし）」）を集計の鍵に使う
    "extensions/notion.py",
    "extensions/slack.py",
    "extensions/tailscale.py",
    "extensions/voicevox.py",
    # [共有] `check.CHECK_LABELS` は `board/api_core.py`・`web/api_v1/tasks.py` が
    # そのまま API 応答に使う共有辞書(cli.py 側は別に `check.label.*` という
    # CLI 専用の対訳を持ち、そちらは訳し済み)。
    "check.py",
    # [共有] board(旧ダッシュボード)・web(現行 Web アプリ)のバックエンド一式。
    # フロントエンド(`web/`。5h-1 で完成済み)と対になる API サーバで、この pass の
    # 対象である「CLI の出力」ではない。`board/__init__.py`(`manor board` の
    # コマンド定義)と `web/__init__.py`(`manor web` のコマンド定義)は CLI の
    # 入り口なので個別に訳し、この ALLOWLIST には含めていない。
    "board/__main__.py",
    "board/_common.py",
    "board/api_core.py",
    "board/api_night.py",
    "board/api_staff.py",
    "board/app.py",
    "board/night.py",
    "web/__main__.py",
    "web/_common.py",
    "web/_install.py",
    "web/app.py",
    "web/passcode.py",
    "web/face.py",
    "web/api_v1/auth.py",
    # [共有] ADR-017 D1・D2（端末の鍵とペアリング）の応答。`web/api_v1/auth.py` と
    # 同じ枠——`detail` は画面（`web/`）と XR の両方が受け取る API の応答で、CLI の
    # 出力ではない（`manor web device ...` の文言は `cli.web.device.*` として訳した）。
    "web/api_v1/devices.py",
    "web/api_v1/face_models.py",
    "web/api_v1/face_thumbnail.py",
    "web/api_v1/face_talk.py",
    "web/api_v1/face_window.py",
    "web/api_v1/house.py",
    "web/api_v1/imports.py",
    "web/api_v1/kitchen.py",
    "web/api_v1/money.py",
    "web/api_v1/receipts.py",  # ADR-020（同じ枠。detail は画面が受け取る API の応答）
    "web/api_v1/night.py",
    "web/api_v1/secretary.py",
    "web/api_v1/setup.py",
    "web/api_v1/tasks.py",
    # [エージェント向け] 起動時の文脈注入・射影の保護メッセージ・圧縮後の案内——
    # 主人ではなく Claude セッション自身が読む(`hooks.py` モジュール docstring・
    # CLAUDE.md「射影は hook が起動時に文脈へ注入します」参照)。
    "hooks.py",
    # [エージェント向け] `manor ctx <id>` の文脈パック。委譲・再開時に Claude セッションへ
    # 渡す文脈で、辺の種類の説明文なども含め一次の読み手はエージェント。
    "ctx.py",
    # [エージェント向け] 委譲の指示書(brief)の見出し・注記。渡す相手は担当のサブ
    # エージェント。CLI 自体のエラー・help は既に訳し済み(handoff.py の ManorError 参照)。
    "handoff.py",
    # [エージェント向け] 射影ファイル(home/STATE.md・QUEUE.md・PROJECTS.md 相当)の
    # 本文生成。起動時に hook が Claude の文脈へ注入する一次資料で、`manor render`
    # 自体のエラー(`error.render.target_unknown`)は既に訳し済み。
    "render.py",
    # [エージェント向け] 夜勤(無人での自走作業)の実行レポート・状態表示。次に起動する
    # Claude セッションが読む再開メモが主目的。
    "night/runner.py",
    "board/api_night.py",
    # [データ] `manor.i18n` 自身の内部エラー("辞書に無いキー"等)。`I18nError` は
    # モジュール docstring に明記した通り「主人には見せない」実装ミス専用の例外
    # (`errors.py` の `localized_message()` はこれを握りつぶして `message_ja` に
    # 逃がす)。翻訳の仕組み自体のエラーメッセージを訳す実益が薄いので日本語のまま。
    "i18n/__init__.py",
    # [データ] 担当モジュールのメタ情報(`NAME`/`LABEL`/`DESCRIPTION`)。agent_meta.py と
    # 同じ理由(担当の日本語名は `agent_label()`・`face_pin.py` の窓タイトル照合と
    # 結びついており、言語に応じて変えると壊れる)。
    "staff/chef/__init__.py",
    "staff/housekeeper/__init__.py",
    "staff/secretary/__init__.py",
    "staff/steward/__init__.py",
    # [データ] VOICEVOX へ渡す音声合成のクエリ検証エラー(`face_speech.py`)。姿の小窓の
    # リップシンク計算という内部処理の例外で、CLI の結果・help ではない
    # (`voice.py`・エンジンの応答文言と同じ「音声合成パイプライン側の文言」という
    # 括り。5h-2 の対象外)。
    "face_speech.py",
    # [データ]+[共有] slack.py の CLI 未満の部分(brief/inbox の本文組み立て・禁止語
    # スキャン・Slack へ実際に投函するメッセージ本文・返信の解析用正規表現)。
    # Slack へ送るメッセージ本文は Notion の DEFAULT_TAGS と同じ「外部へ送る実データ」
    # であって画面の文言ではない。CLI コマンド自体(`manor slack brief|inbox|test`の
    # help・結果行)は既に訳し済み(`_cmd_brief`/`_cmd_inbox`/`_cmd_test`/`register`/
    # `main` 参照)。
    "slack.py",
    # [共有] extensions/__init__.py: `_validate_manifest` の `ExtensionManifestError` は
    # 拡張モジュール自身のバグ(MANIFEST が壊れている)を主人にではなく開発側に伝える
    # 例外——`i18n/__init__.py` の `I18nError` と同じ扱い。`_safe_detect`/`_safe_check`
    # の `reason` はモジュール自身のコメントに明記の通り「web / CLI 共通」の応答。
    # 引数解析・結果行(`register`/`_cmd_list`/`_cmd_show`/`_cmd_set`/`_cmd_test`)は
    # 既に訳し済み。
    "extensions/__init__.py",
    # [共有]+[データ] voice.py: エンジンの起動/停止/合成の `reason`・`detail` は
    # `web/api_v1/extensions.py` が直接呼ぶ共有の応答(サブエージェントの検分どおり)。
    # CLI の引数・見出し・単純な結果行は訳し済み。
    "voice.py",
    # [データ] notify.py: 声かけの定型句(`_PHRASES`/`_PHRASE_MANY`)は VOICEVOX に
    # 読み上げさせる音声合成の内容——画面や端末に印字する文言ではない
    # (`voice.speak` へそのまま渡す。5h-2 の対象は端末の文字出力)。
    "notify.py",
    # [共有] talk_session.py: 状態文言(予算切れ・夜勤ロック中 等)は `voice.speak` で
    # 読み上げる音声合成の内容であり、かつ web の小窓(`web/api_v1/...`)とも共有する
    # 応答。`TalkError` は `ManorError` と違って key/params の仕組みを持たないため、
    # この pass では既存の挙動を変えない判断とした(サブエージェントの報告どおり、
    # 主人にしか決められない設計判断として報告する)。
    "talk_session.py",
    # [データ]+[共有] profile.py: `PURPOSES`/`PRESETS` の語彙・オンボーディングの既定値
    # (「ご主人様」「執事」)・`summary_line()` は `web/api_v1/setup.py` の設定ウィザードと
    # 共有するデータ・ロジック(`PRESETS` は `board/static/app.js` の絵文字表記とも
    # 対応済みと本文コメントに明記)。CLI の `register`/`_cmd_show`/`_cmd_set` 自体は
    # 訳し済み。
    "profile.py",
    # [データ] shortcut.py: デスクトップに実際に作るショートカットのファイル名
    # (`SHORTCUT_LABEL`)と、旧名からの片付けに使う一致対象(`LEGACY_SHORTCUT_LABELS`)。
    # 端末に印字する文言ではなく、主人の机に残る成果物のファイル名そのもの
    # (translating しても実行するたびに前回名が残ってしまうので、書式の再設計が要る
    # ——主人にしか決められない判断として報告する)。CLI の結果行・help は訳し済み。
    "shortcut.py",
}
# 行単位の許可(ファイル全体は検算したいが、特定の行だけ理由があって除外)。
# (相対パス, その行の完全なテキスト) の組で持つ(T49・2026-09-14)。行番号だと
# 上に1行足すたびにずれて誤検知した(3日で5回。詳細は task.py の項目のコメント参照)。
LINE_ALLOWLIST: set[tuple[str, str]] = {
    # [共有] calendar.py: fetch_ics/check_connection の `reason` は web の拡張ステータス
    # 表示(`manor ext check calendar` 相当・設定画面のヘルスチェック)と共有する診断
    # 文字列。CLI 表示側(_cmd_sync/_cmd_list)の「包む文」だけを訳し、ここは日本語のまま。
    # [データ] calendar.py: `claude -p` へ渡す指示の文面そのもの（端末には出ない。
    # slack.py の生成の下書きと同じ扱い）と、ICS の注記マーカー。残りは `reason`
    # ——`check()` / Slack の返信と共有する診断文字列なので訳さない（ADR-012 5d の判断）。
    ('calendar.py', '        return {"ok": False, "reason": f"HTTP エラー: {exc.code}"}'),
    ('calendar.py', '        return {"ok": False, "reason": f"接続できませんでした: {exc.reason}"}'),
    ('calendar.py', '        return {"ok": False, "reason": "タイムアウトしました"}'),
    ('calendar.py', '        return {"ok": False, "reason": f"取得できませんでした: {exc}"}'),
    ('calendar.py', '        return {"ok": False, "reason": "ICS 形式ではないようです（BEGIN:VCALENDAR が見つかりません）"}'),
    ('calendar.py', '            return {"ok": False, "reason": "URL が未設定です（manor ext set calendar --secret url）"}'),
    ('calendar.py', '            return {"ok": True, "reason": "接続できました（予定は0件です）"}'),
    ('calendar.py', '        return {"ok": False, "reason": f"確認できませんでした: {exc}"}'),
    ('calendar.py', '        return {"ok": False, "reason": "URL が未設定です（manor ext set calendar --secret url）"}'),
    ('calendar.py', 'UPDATE_PROMPT_TEMPLATE = """`{tool}` を**ちょうど1回**呼んで、既にある予定を1件直してください。'),
    ('calendar.py', 'PUSH_PROMPT_TEMPLATE = """`{tool}` を**ちょうど1回**呼んで、予定を1件作ってください。'),
    ('calendar.py', '    safe_place = (location or "").replace(chr(10), " ").strip()[:PUSH_TITLE_MAX] or "（なし）"'),
    ('calendar.py', '        return {"ok": False, "html_link": "", "mode": "", "reason": "[calendar] write_calendar_id が未設定です"}'),
    ('calendar.py', '        return {"ok": False, "html_link": "", "mode": mode, "reason": "claude が見つかりません"}'),
    ('calendar.py', '        return {"ok": False, "html_link": "", "mode": mode, "reason": f"claude を呼べません: {exc}"}'),
    ('calendar.py', '        return {"ok": False, "html_link": "", "mode": mode, "reason": "claude がエラーを返しました"}'),
    ('calendar.py', '        reason = result_text.strip()[:200] or "リンクが返りませんでした"'),
    ('calendar.py', '            reason = f"道具を拒否されました（{\', \'.join(denied)}）: {reason}"'),
    ('calendar.py', 'EXTRACT_PROMPT_TEMPLATE = """次の <本文> から予定を1件読み取り、**JSON だけ**を出力してください。'),
    ('calendar.py', '        return {"ok": False, "reason": "claude が見つかりません"}'),
    ('calendar.py', '    weekday = "月火水木金土日"[_dt.strptime(today, "%Y-%m-%d").weekday()]'),
    ('calendar.py', '        return {"ok": False, "reason": f"claude を呼べません: {exc}"}'),
    ('calendar.py', '        return {"ok": False, "reason": "claude の応答を読めません"}'),
    ('calendar.py', '        return {"ok": False, "reason": "claude がエラーを返しました"}'),
    ('calendar.py', '        return {"ok": False, "reason": f"読み取り結果が JSON ではありません: {body.strip()[:120]}"}'),
    ('calendar.py', '        reason = str((data or {}).get("error") or "読み取れません")'),
    ('calendar.py', '        return {"ok": False, "reason": "件名か日付を読み取れません"}'),
    # [共有] face.py: try_open_app_window は web の `/api/v1/face/open` の応答(reason)。
    # _popen_chrome も両方から共有される(コメント参照)。
    ('face.py', '            f"担当が見つかりません: {agent!r}（使えるのは {known}）",'), ('face.py', '        return {"opened": False, "method": "none", "reason": "Chrome が見つかりません"}'), ('face.py', '        return {"opened": False, "method": "none", "reason": f"Chrome を起動できませんでした（{failure}）"}'),
    # [データ] decision.py: 承認・却下のときに入れる既定のルーリング文言そのものは、
    # 台帳に永続する記録(主人が入れたデータと同じ扱い)。CLI の言語設定に関わらず
    # 日本語のまま。
    # 2026-09-06（S12）: `rule()` に actor の説明を足して行がずれた（87 → 101）。
    # 2026-09-11（T16）: `ask()` に asked_by の docstring を足して行がずれた（101 → 105）。
    ('decision.py', '        ruling = {"approved": "承認", "rejected": "却下"}[verdict]'),
    # [データ] chef/ops.py: validate_date の既定 field="日付"。tests/staff/test_chef.py が
    # field 省略で呼ぶため既定値は残すが、エラーの ManorError 側では呼び出し元が
    # field_key を渡して訳している(cli.py 側からの呼び出しはすべて明示的)。
    ('staff/chef/ops.py', 'def validate_date(value: str, *, field: str = "日付", field_key: str = "chef.field.date") -> str:'),
    # [データ] secretary/ops.py: 曜日の対訳表・相対日付(今日/明日/明後日)の受理語彙は
    # 「主人が入力する側」の語彙(ADR-002 §6)であって、出力の文言ではない。
    ('staff/secretary/ops.py', '_WEEKDAY_JA: dict[str, int] = {"月": 0, "火": 1, "水": 2, "木": 3, "金": 4, "土": 5, "日": 6}'), ('staff/secretary/ops.py', '_NEXT_WEEK_RE = re.compile(r"^来週の(月|火|水|木|金|土|日)$")'),
    ('staff/secretary/ops.py', '    if s == "今日" or lowered == "today":'), ('staff/secretary/ops.py', '    if s == "明日" or lowered == "tomorrow":'), ('staff/secretary/ops.py', '    if s == "明後日":'),
    # [データ] secretary/ops.py: ics.py が予定の note に埋め込む注記マーカーとの照合
    # (ics.py 自体が ALLOWLIST 済み。ここは判定のための文字列一致で、表示側の文言は
    # 別に訳し済み)。
    ('staff/secretary/ops.py', '        if "[未対応の繰り返し]" in note:'), ('staff/secretary/ops.py', '        if "[TZID未解決]" in note:'),
    # [データ] secretary/ops.py: validate_time/validate_datetime の既定 field。呼び出し側は
    # すべて明示的に上書きしており、既定値は保守のためだけに残る死んだ経路。
    ('staff/secretary/ops.py', 'def validate_time(value: str, *, field: str = "時刻") -> str:'), ('staff/secretary/ops.py', 'def validate_datetime(value: str, *, field: str = "日時") -> str:'),
    # [データ] steward/cli.py: 定期支払いの記録として DB の memo 列に永続する文字列
    # (decision.py の ruling と同じ「主人のデータ」の扱い)。
    ('staff/steward/cli.py', '        (on, row["amount"], kind, row["category"], f"定期: {row[\'name\']}", util.now()),'),
    # [データ] task.py: link_dependency/dup が task_event.note へ書く定型の一言。
    # 台帳に永続する記録(decision.py の ruling と同じ扱い)。
    # 3日で5回、行番号がずれて誤検知した経緯（T12）を踏まえ、T49（2026-09-14）で
    # (ファイル, 行テキスト) の照合に変えた。以後この種のずれでは壊れない。
    ('task.py', '        status(conn, src, "waiting", note=f"{dst} の後に", actor=actor)'), ('task.py', '    return status(conn, src, "withdrawn", note=f"{dst} と重複のため")'),
    # [データ] project.py: `project.kind` の値そのもの。DB に入っている文字列なので
    # 訳さない——訳すと、既存の行と一致しなくなる（2026-09-09・T26 の実装で追加）。
    ('project.py', 'BUTLER_PROJECT_KIND = "執事"'),
    # [データ] user.py: `seed_defaults` が種（`master`/`butler`）を入れるときの名前の
    # 既定値（「主人」「執事」）。ADR-014 D1「名前の既定値は i18n を通さない」——
    # `profile.summary_line` が「執事」を既定にしているのと同じ扱いで、DB の中身
    # （利用者の呼び名の初期値）であって画面の文言ではない。
    ('user.py', '    master_name = _profile_name(conn, "master.callname", "主人")'), ('user.py', '    butler_name = _profile_name(conn, "butler.callname", "執事")'),
    # [データ] board/__init__.py・web/__init__.py・archive.py・gate.py・night/__init__.py の
    # `LABEL` 定数。`manor.cli` の `_run_init` が「部下: {name}」の一覧に使う想定の
    # 表示名だが、実際に読まれるのは `staff/*` 配下の担当モジュールだけ(grep で確認)。
    # 担当モジュールの `LABEL`(agent_meta.py の ALLOWLIST 理由と同じ)に合わせて
    # 日本語のままにする。
    ('board/__init__.py', 'LABEL = "ダッシュボード"'), ('web/__init__.py', 'LABEL = "Web アプリ"'),
    ('archive.py', 'LABEL = "アーカイブ"'), ('gate.py', 'LABEL = "関門"'), ('night/__init__.py', 'LABEL = "夜勤"'),
    # [データ] archive.py: アーカイブした先を示す Markdown コメントを元ファイルへ
    # 挿入する行。端末には出さず、CHANGELOG.md 等その場に永続する注記(decision.py の
    # ruling と同じ「主人のファイルへ残す記録」の扱い)。
    ('archive.py', '            new_parts.append(f"<!-- archived: {m} → {rel}（{counts[m]}件） -->\\n")'),
    # [データ] gate.py: 振る舞い試験ランナー自身の標準出力(「結果一式: <path>」)を
    # 読み取るための正規表現。ランナー側の出力形式そのものであって manor の文言では
    # ない。
    ('gate.py', '_OUT_DIR_RE = re.compile(r"結果一式:\\s*(.+)\\s*$")'),
    # [データ] staff/chef/recipe_import.py（ADR-015 R2・2026-09-12。D7/D8/D9追補、
    # および同日の追補2（3つの取りこぼし: タグ混入・題名の尾・工程写真の穴埋め）で
    # 行が大きくずれたため、その都度 `_string_constant_offenders` で洗い直した）:
    # `claude -p` へ渡す構造化・整形（D7-2）・栄養推定の指示文面そのもの
    # （`STRUCTURE_PROMPT_TEMPLATE`/`STRUCTURE_RETRY_SUFFIX`/`REFINE_PROMPT_TEMPLATE`/
    # `NUTRITION_PROMPT_TEMPLATE`。端末には出ない。calendar.py の
    # PUSH_PROMPT_TEMPLATE/EXTRACT_PROMPT_TEMPLATE と同じ扱い）と、fetch_page/
    # _call_claude_for_json/_length_violations/_resolve_hero_image/extract_auto/
    # _fill_missing_step_images_from_html の `reason`/`warnings` 文字列
    # （calendar.fetch_ics の `reason` と同じ「共有の診断文字列」の扱い。CLI/Web の
    # 呼び出し側が包む文だけ訳す）。
    # （ADR-015 §6 追補・2026-09-12: 材料の分割・栄養価の取り込みで行数が増え、
    #   このブロック全体を `_string_constant_offenders` で洗い直した）。
    # （2026-09-13・取り込み対象を3サイト広げたとき: JSON-LD のタグ収集・アダプタの
    #   「補い」（`extract_hints`）を足して行数が増え、もう一度洗い直した。
    #   ここで増えた `_LD_TAG_SPLIT_RE` の読点も「サイトの文字列を割るためのデータ」）。
    ('staff/chef/recipe_import.py', '        return {"ok": False, "html": "", "final_url": "", "reason": f"HTTP エラー: {exc.code}"}'), ('staff/chef/recipe_import.py', '        return {"ok": False, "html": "", "final_url": "", "reason": f"接続できませんでした: {exc.reason}"}'),
    ('staff/chef/recipe_import.py', '        return {"ok": False, "html": "", "final_url": "", "reason": "タイムアウトしました"}'), ('staff/chef/recipe_import.py', '        return {"ok": False, "html": "", "final_url": "", "reason": f"取得できませんでした: {exc}"}'),
    ('staff/chef/recipe_import.py', '            "reason": f"本文が大きすぎます（上限 {max_bytes // (1024 * 1024)}MB）",'), ('staff/chef/recipe_import.py', '    return "", ["完成画像が見つかりません"]'),
    ('staff/chef/recipe_import.py', '            "工程の写真の数が本文の工程数と合わないため割り当てていません"'), ('staff/chef/recipe_import.py', '            f"（工程 {len(raw_steps)} 件 / 写真 {len(candidates)} 件）"'),
    ('staff/chef/recipe_import.py', '            "材料のグループの数が本文と合わないため割り当てていません"'), ('staff/chef/recipe_import.py', '            f"（材料 {len(lines)} 件 / グループ {len(groups)} 件）"'),
    ('staff/chef/recipe_import.py', '                "index": 1, "phase": "cook", "title": "要編集",'), ('staff/chef/recipe_import.py', '                "instruction": "手順を自動では読み取れませんでした。内容を確認して編集してください。",'),
    ('staff/chef/recipe_import.py', '        "title": title.strip() or str(source.get("title") or "").strip() or "（タイトル不明）",'), ('staff/chef/recipe_import.py', '                "自動抽出では手順を見つけられませんでした。内容を編集するか、"'),
    ('staff/chef/recipe_import.py', '                "自動抽出できた工程が少ないため、内容をご確認ください"'), ('staff/chef/recipe_import.py', '        return {"ok": False, "recipe": None, "method": "", "warnings": [], "reason": f"自動抽出に失敗しました: {exc}"}'),
    ('staff/chef/recipe_import.py', '        return {"ok": False, "data": None, "reason": "claude が見つかりません"}'), ('staff/chef/recipe_import.py', '        return {"ok": False, "data": None, "reason": f"claude を呼べません: {exc}"}'),
    ('staff/chef/recipe_import.py', '        return {"ok": False, "data": None, "reason": "claude の応答を読めません"}'), ('staff/chef/recipe_import.py', '        return {"ok": False, "data": None, "reason": "claude の応答の形が不正です"}'),
    ('staff/chef/recipe_import.py', '        return {"ok": False, "data": None, "reason": "claude がエラーを返しました"}'), ('staff/chef/recipe_import.py', '        return {"ok": False, "data": None, "reason": f"読み取り結果が JSON ではありません: {body.strip()[:120]}"}'),
    ('staff/chef/recipe_import.py', 'STRUCTURE_PROMPT_TEMPLATE = """次の<ページ>から、料理のレシピを次の JSON へ構造化してください。**JSON だけ**を出力し、前後に説明文もコードブロックの囲みも付けないでください。'), ('staff/chef/recipe_import.py', 'STRUCTURE_RETRY_SUFFIX = """**もう一度お願いします。** 前回の出力は文字数の上限を超えていました。該当する工程だけを書き直し、それぞれの上限に収めてください（他はそのままで構いません）。JSON 全体をもう一度、**JSON だけ**で出力してください。'),
    ('staff/chef/recipe_import.py', '        lines.append(f"分量: {ld[\'yield\']}")'), ('staff/chef/recipe_import.py', '        lines.append(f"所要時間: {ld[\'total_time\']}")'),
    ('staff/chef/recipe_import.py', '        lines.append("材料:")'), ('staff/chef/recipe_import.py', '        lines.append("手順:")'),
    ('staff/chef/recipe_import.py', '    content = content.strip()[:_TEXT_MAX_CHARS] or "（本文を取得できませんでした）"'), ('staff/chef/recipe_import.py', '        images_list = "（画像なし）"'),
    ('staff/chef/recipe_import.py', '        title=str(source.get("title") or "").strip() or "（タイトル不明）",'), ('staff/chef/recipe_import.py', '                f"steps[{i}].title は{recipes._TITLE_MAX}文字以内にしてください"  # noqa: SLF001'),
    ('staff/chef/recipe_import.py', '                f"（{len(title)}文字）: {title!r}"'), ('staff/chef/recipe_import.py', '                f"steps[{i}].instruction は{recipes._INSTRUCTION_MAX}文字以内にしてください"  # noqa: SLF001'),
    ('staff/chef/recipe_import.py', '                f"（{len(instruction)}文字）: {instruction!r}"'), ('staff/chef/recipe_import.py', 'REFINE_PROMPT_TEMPLATE = """次の<下書き>は、料理サイトから自動抽出したレシピの JSON です。**1動作1工程**になるよう `steps` を整え、`title` は12文字以内、`instruction` は100文字以内に収めてください。**JSON だけ**を出力し、前後に説明文もコードブロックの囲みも付けないでください。'),
    # [データ] staff/chef/media.py（ADR-016 D2・2026-09-12）: `fetch_oembed` の
    # `reason` ——`recipe_import.fetch_page` の `reason` と同じ「共有の診断文字列」。
    # **そもそも主人には見えない**（ADR-016 D2-3 のとおり、oEmbed が落ちても登録は
    # 通すので `add_from_url` はこの理由を捨てる。残してあるのは道具から呼んで
    # 切り分けるときのため）。
    ('staff/chef/media.py', '        return {"ok": False, "reason": f"HTTP エラー: {exc.code}"}'), ('staff/chef/media.py', '        return {"ok": False, "reason": f"接続できませんでした: {exc.reason}"}'),
    ('staff/chef/media.py', '        return {"ok": False, "reason": "タイムアウトしました"}'), ('staff/chef/media.py', '        return {"ok": False, "reason": f"取得できませんでした: {exc}"}'),
    ('staff/chef/media.py', '        return {"ok": False, "reason": "応答が大きすぎます"}'), ('staff/chef/media.py', '        return {"ok": False, "reason": f"JSON として読めません: {exc}"}'),
    ('staff/chef/media.py', '        return {"ok": False, "reason": "JSON の形が想定と違います"}'),
    # [データ] staff/chef/recipe_shaping.py（ADR-015 D7・2026-09-12）: 下ごしらえ語
    # （`_PREP_WORDS`）・既定の phase 見出し（`_PHASE_DEFS` の「下ごしらえ」「調理」
    # 「仕上げ」）は自動抽出が機械的に使う手がかり語・レシピ本体に残る見出し文字列
    # ——画面の文言ではなくデータ（`lexicon.toml` の分類語彙と同じ扱い）。
    # [データ] staff/chef/recipe_shaping.py（ADR-015 §6 追補・2026-09-12／ADR-019・
    # 2026-09-13）: 材料の分割で使う量の語彙（`_QTY_PHRASE_WORDS`/`_QTY_COUNTER_UNITS`/
    # 「大さじ」等）と、その語彙を埋め込んだ正規表現（`_NUM_PART`/`_AMOUNT_TAIL_RE` の
    # 「各」等）、材料名の正規化で落とす飾り。`_PREP_WORDS` と同じ「手がかり語」の扱い。
    ('staff/chef/recipe_shaping.py', '    "切る", "切り", "切っ", "刻む", "刻み", "刻ん", "混ぜる", "混ぜ", "溶く", "溶き",'), ('staff/chef/recipe_shaping.py', '    "洗う", "洗い", "むく", "むき", "戻す", "戻し", "解凍", "下ごしらえ",'),
    ('staff/chef/recipe_shaping.py', '    {"id": "prep", "title": "下ごしらえ"},'), ('staff/chef/recipe_shaping.py', '    {"id": "cook", "title": "調理"},'),
    ('staff/chef/recipe_shaping.py', '    {"id": "finish", "title": "仕上げ"},'), ('staff/chef/recipe_shaping.py', '    return result or [{"id": "cook", "title": "調理"}]'),
    ('staff/chef/recipe_shaping.py', '    "適量", "適宜", "少々", "ひとつまみ", "ふたつまみ", "お好みの量", "好みの量", "お好みで",'), ('staff/chef/recipe_shaping.py', '    "kg", "ml", "cc", "cm", "㎝", "g", "個", "枚", "本", "切れ", "束", "株", "房",'),
    ('staff/chef/recipe_shaping.py', '    "丁", "片", "袋", "缶", "合", "杯", "滴",'), ('staff/chef/recipe_shaping.py', '_NUM_PART = r"[\\d０-９]+(?:[./][\\d０-９]+)?(?:と[\\d０-９]+(?:[./][\\d０-９]+)?)?"'),
    ('staff/chef/recipe_shaping.py', '    r"(?P<each>各\\s*)?"'), ('staff/chef/recipe_shaping.py', '    rf"|{_NUM_PART}\\s*(?:{\'|\'.join(_QTY_COUNTER_UNITS)})(?:分)?"'),
    ('staff/chef/recipe_shaping.py', '    for word in ("大さじ", "小さじ", "カップ"):'), ('staff/chef/recipe_shaping.py', '    each = amount.startswith("各")'),
    ('staff/chef/recipe_shaping.py', '    amount_text = ("各" if m.group("each") else "") + m.group("amount")'),
    # [データ] staff/chef/recipe_shaping.py（ADR-015 §3・2026-09-13）:
    # `infer_ingredients_used()`（工程が使う材料の推定）が使う**言語の手がかり**
    # ——材料表ぜんぶを指す言い回し（「全ての材料」）、短い材料名の前に来てよい助詞、
    # 量の言い回し（「大さじ」等。`_QTY_PHRASE_WORDS` の使い回し）、平仮名・漢字・
    # カタカナの文字クラス、末尾の揺れ（「肉」「類」）。`_PREP_WORDS` と同じ
    # 「手がかり語」の扱いで、画面の文言ではない。
    ('staff/chef/recipe_shaping.py', '    "全ての材料", "すべての材料", "全材料", "材料全て", "材料すべて", "材料全部",'), ('staff/chef/recipe_shaping.py', '_PARTICLE_CHARS = frozenset("をはがにでとやもへからばしてただりるきくいえずつ")'),
    ('staff/chef/recipe_shaping.py', '_QTY_LEAD_WORDS: tuple[str, ...] = _QTY_PHRASE_WORDS + ("大さじ", "小さじ", "カップ", "各")'), ('staff/chef/recipe_shaping.py', '_WORD_CHAR_RE = re.compile(r"[々㐀-䶿一-鿿゠-ヿｦ-ﾟ]")'),
    ('staff/chef/recipe_shaping.py', '_HIRAGANA_RE = re.compile(r"[ぁ-ゟ]")'), ('staff/chef/recipe_shaping.py', '    for suffix in ("肉", "類"):'),
    ('staff/chef/recipe_shaping.py', '_MEAT_CUT_GAP = r"[^、。,.・（）()とやをはがにでもの]{0,4}?"'), ('staff/chef/recipe_shaping.py', '    if not base.endswith("肉") or len(base) < 2:'),
    ('staff/chef/recipe_shaping.py', '    pattern = re.compile(rf"{re.escape(base[0])}{_MEAT_CUT_GAP}肉")'),
    # [データ] staff/chef/recipe_sites/kurashiru.py（ADR-015 §7 追補・2026-09-13）:
    # 材料一覧の節を本文から探す保険の正規表現に入る見出し語「材料」
    # （`_INGREDIENT_SECTION_FALLBACK_RE`）。出典サイトの表示に実在する文字列で、
    # 画面の文言ではない（generic.py の見出し語と同じ扱い）。
    ('staff/chef/recipe_sites/kurashiru.py', '_INGREDIENT_SECTION_FALLBACK_RE = re.compile(r"材料(.*?)</section>", re.S)'),
    # [データ] staff/chef/recipe_sites/cookpad.py（ADR-015 D7・2026-09-12）:
    # 「作り方」「手順」はサイトの見出し語を拾うための正規表現の一部（データ）。
    ('staff/chef/recipe_sites/cookpad.py', "_STEP_HEADING_BLOCK_RE = re.compile(r'(?:作り方|手順)[\\s\\S]*?<(?:ol|ul)[^>]*>(.*?)</(?:ol|ul)>', re.S)"),
    # [データ] staff/chef/recipe_sites/nadia.py（ADR-015 §6 追補・2026-09-12）:
    # 栄養価の表示ラベル語（「エネルギー」「たんぱく質」「脂質」「炭水化物」
    # 「食塩相当量」）——出典サイトの DOM に実在するラベル文字列を拾うための
    # 手がかり語であって、画面の文言ではない（`_NUTRITION_LABELS`）。
    ('staff/chef/recipe_sites/nadia.py', '    ("エネルギー", "kcal"),'), ('staff/chef/recipe_sites/nadia.py', '    ("たんぱく質", "protein_g"),'),
    ('staff/chef/recipe_sites/nadia.py', '    ("脂質", "fat_g"),'), ('staff/chef/recipe_sites/nadia.py', '    ("炭水化物", "carb_g"),'),
    ('staff/chef/recipe_sites/nadia.py', '    ("食塩相当量", "salt_g"),'),
    # [データ] staff/chef/recipe_sites/sirogohan.py（ADR-015 D7・2026-09-13）:
    # 分量「(２人分)」と調理時間「調理時間：30分」を本文から拾う正規表現の一部
    # （`_SERVINGS_RE`/`_MINUTES_RE`）。出典サイトの表示に実在する文字列で、
    # 画面の文言ではない。
    ('staff/chef/recipe_sites/sirogohan.py', '_SERVINGS_RE = re.compile(r"([\\d０-９]+)\\s*人分")'), ('staff/chef/recipe_sites/sirogohan.py', '_MINUTES_RE = re.compile(r"([\\d０-９]+)\\s*分")'),
    # [データ] staff/chef/recipe_sites/delishkitchen.py（ADR-015 D7・2026-09-13）:
    # ①栄養価の表示ラベル語（`_NUTRITION_LABELS` の「カロリー」「塩分」等。nadia.py と
    # 同じ理由）②サイト固有の分類語を `lexicon.toml` の語へ寄せる対応表
    # （`_TAG_TRANSLATIONS` の「副菜」「和食」等——分類語彙そのもので、`lexicon.toml` の
    # 分類語彙と同じ「データ」の扱い。⚠ 本来は lexicon 側に英語の手がかり語を足したいが、
    # 別の担当が同じファイルを編集中のため今回は触らず、報告で挙げた）
    # ③分量「【2人分】」を拾う正規表現（`_SERVINGS_NUM_RE`）。
    ('staff/chef/recipe_sites/delishkitchen.py', '    ("カロリー", "kcal"),'), ('staff/chef/recipe_sites/delishkitchen.py', '    ("たんぱく質", "protein_g"),'),
    ('staff/chef/recipe_sites/delishkitchen.py', '    ("脂質", "fat_g"),'), ('staff/chef/recipe_sites/delishkitchen.py', '    ("炭水化物", "carb_g"),'),
    ('staff/chef/recipe_sites/delishkitchen.py', '    ("塩分", "salt_g"),'), ('staff/chef/recipe_sites/delishkitchen.py', '_SERVINGS_NUM_RE = re.compile(r"([\\d０-９]+)\\s*人分")'),
    # [データ] staff/chef/recipe_sites/generic.py（ADR-015 D7・2026-09-12）:
    # 見出し語の手がかり（「材料」「作り方」「手順」等）——lexicon.toml の分類語彙と
    # 同じ「データ」の扱いで、画面の文言ではない。
    ('staff/chef/recipe_sites/generic.py', '_INGREDIENT_HEADER_WORDS = ("材料",)'), ('staff/chef/recipe_sites/generic.py', '_STEP_HEADER_WORDS = ("作り方", "手順", "レシピ手順", "steps", "instructions", "directions")'),
}


def _ja_suffixed_assignment_ranges(tree: ast.Module) -> set[int]:
    """`xxx_ja = "..."` の形の代入——`message_ja` は常に日本語という規約に合わせて、
    値を一度別の変数（`label_ja` 等）に置いてから `ManorError(f"...{label_ja}...")`
    へ渡す書き方をする箇所がある(`task.py` の `status()` 参照)。`ManorError(...)` の
    最初の引数そのもの以外はこの規約を機械では気づけないので、変数名の慣習
    (`_ja` で終わる)で救う。真偽の分岐(`"A" if cond else "B"`)の中の文字列も拾う。
    """
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id.endswith("_ja") for t in node.targets):
            continue
        for sub in ast.walk(node.value):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                end = getattr(sub, "end_lineno", sub.lineno)
                lines.update(range(sub.lineno, end + 1))
    return lines


def _docstring_line_ranges(tree: ast.Module) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if not body:
                continue
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                const = first.value
                end = getattr(const, "end_lineno", const.lineno)
                lines.update(range(const.lineno, end + 1))
    return lines


def _manor_error_message_ranges(tree: ast.Module) -> set[int]:
    """`ManorError(...)`（`raise` の有無を問わず）呼び出しの最初の引数の行範囲。
    `message_ja` は常に日本語のまま、という設計上の除外（モジュール docstring 参照）。
    """
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else None)
        if name != "ManorError" or not node.args:
            continue
        first_arg = node.args[0]
        end = getattr(first_arg, "end_lineno", first_arg.lineno)
        lines.update(range(first_arg.lineno, end + 1))
    return lines


def _string_constant_offenders(path: Path, tree: ast.Module, source_lines: list[str]) -> list[str]:
    docstring_lines = _docstring_line_ranges(tree)
    manor_error_lines = _manor_error_message_ranges(tree)
    ja_assignment_lines = _ja_suffixed_assignment_ranges(tree)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if not JA_CHAR_PATTERN.search(node.value):
            continue
        lineno = node.lineno
        if lineno in docstring_lines or lineno in manor_error_lines or lineno in ja_assignment_lines:
            continue
        rel = path.relative_to(SRC_ROOT).as_posix()
        # 行番号ではなく行の内容そのものと照合する(T49)。上に行を足しても壊れない。
        line_text = source_lines[lineno - 1] if 0 < lineno <= len(source_lines) else ""
        if (rel, line_text) in LINE_ALLOWLIST:
            continue
        offenders.append(f"{rel}:{lineno}: {node.value[:80]!r}")
    return offenders


def _all_py_files() -> list[Path]:
    return sorted(p for p in SRC_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


ALL_FILES = _all_py_files()


def test_self_check_files_found() -> None:
    assert len(ALL_FILES) > 30  # 検算対象が空になっていないことの自己点検


@pytest.mark.parametrize("path", ALL_FILES, ids=lambda p: p.relative_to(SRC_ROOT).as_posix())
def test_no_hardcoded_japanese(path: Path) -> None:
    rel = path.relative_to(SRC_ROOT).as_posix()
    if rel in ALLOWLIST:
        pytest.skip(f"{rel} は ALLOWLIST（データ・語彙。理由はテストファイル冒頭のコメント参照）")
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    offenders = _string_constant_offenders(path, tree, source.split("\n"))
    assert offenders == [], "\n".join(offenders)


def test_allowlist_entries_exist_and_still_contain_japanese() -> None:
    """ALLOWLIST の風化を防ぐ: 載せた理由(日本語データを持つ)が消えたら気づけるように。"""
    for rel in ALLOWLIST:
        path = SRC_ROOT / rel
        assert path.is_file(), f"{rel} が見つかりません（ALLOWLIST から外すこと）"
        text = path.read_text(encoding="utf-8")
        assert JA_CHAR_PATTERN.search(text), f"{rel} はもう日本語を含みません（ALLOWLIST から外すこと）"


def test_line_allowlist_entries_still_contain_japanese() -> None:
    """LINE_ALLOWLIST の風化を防ぐ: 行番号ではなく行の内容そのもので照合するので
    (T49)、ここで壊れうるのは「その行がもうファイルに存在しない」（内容が変わった）
    ときだけ——行の移動では壊れない。
    """
    for rel, line_text in LINE_ALLOWLIST:
        path = SRC_ROOT / rel
        assert path.is_file(), f"{rel} が見つかりません（LINE_ALLOWLIST から外すこと）"
        assert JA_CHAR_PATTERN.search(line_text), f"{rel}: {line_text!r} は日本語を含みません"
        lines = path.read_text(encoding="utf-8").split("\n")
        assert line_text in lines, (
            f"{rel}: {line_text!r} がもうファイルにありません"
            "（内容が変わったので LINE_ALLOWLIST を更新すること）"
        )


def test_line_allowlist_survives_line_shift() -> None:
    """T49 の核心: 許可された行の上に無関係な行を挿入して行番号がずれても、
    (ファイル, 行の内容)で照合するので誤検知しない(旧・行番号方式は3日で5回壊れた)。
    """
    rel, line_text = "decision.py", '        ruling = {"approved": "承認", "rejected": "却下"}[verdict]'
    assert (rel, line_text) in LINE_ALLOWLIST  # 前提: 実在の許可対象であること
    path = SRC_ROOT / rel
    lines = path.read_text(encoding="utf-8").split("\n")
    idx = lines.index(line_text)
    shifted_lines = lines[:idx] + ["# inserted for test_line_allowlist_survives_line_shift"] + lines[idx:]
    shifted_source = "\n".join(shifted_lines)
    tree = ast.parse(shifted_source, filename=str(path))
    offenders = _string_constant_offenders(path, tree, shifted_lines)
    assert offenders == [], "\n".join(offenders)
