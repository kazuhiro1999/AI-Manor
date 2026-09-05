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
    "web/api_v1/face_models.py",
    "web/api_v1/face_window.py",
    "web/api_v1/house.py",
    "web/api_v1/imports.py",
    "web/api_v1/kitchen.py",
    "web/api_v1/money.py",
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
# "相対パス:行番号" の形。
LINE_ALLOWLIST: set[str] = {
    # [共有] calendar.py: fetch_ics/check_connection の `reason` は web の拡張ステータス
    # 表示(`manor ext check calendar` 相当・設定画面のヘルスチェック)と共有する診断
    # 文字列。CLI 表示側(_cmd_sync/_cmd_list)の「包む文」だけを訳し、ここは日本語のまま。
    "calendar.py:76", "calendar.py:78", "calendar.py:80", "calendar.py:82", "calendar.py:89",
    "calendar.py:101", "calendar.py:107", "calendar.py:110", "calendar.py:122", "calendar.py:190",
    # [共有] face.py: try_open_app_window は web の `/api/v1/face/open` の応答(reason)。
    # _popen_chrome も両方から共有される(コメント参照)。
    "face.py:52", "face.py:226", "face.py:237",
    # [データ] decision.py: 承認・却下のときに入れる既定のルーリング文言そのものは、
    # 台帳に永続する記録(主人が入れたデータと同じ扱い)。CLI の言語設定に関わらず
    # 日本語のまま。
    "decision.py:87",
    # [データ] chef/ops.py: validate_date の既定 field="日付"。tests/staff/test_chef.py が
    # field 省略で呼ぶため既定値は残すが、エラーの ManorError 側では呼び出し元が
    # field_key を渡して訳している(cli.py 側からの呼び出しはすべて明示的)。
    "staff/chef/ops.py:57",
    # [データ] secretary/ops.py: 曜日の対訳表・相対日付(今日/明日/明後日)の受理語彙は
    # 「主人が入力する側」の語彙(ADR-002 §6)であって、出力の文言ではない。
    "staff/secretary/ops.py:26", "staff/secretary/ops.py:33",
    "staff/secretary/ops.py:61", "staff/secretary/ops.py:63", "staff/secretary/ops.py:65",
    # [データ] secretary/ops.py: ics.py が予定の note に埋め込む注記マーカーとの照合
    # (ics.py 自体が ALLOWLIST 済み。ここは判定のための文字列一致で、表示側の文言は
    # 別に訳し済み)。
    "staff/secretary/ops.py:234", "staff/secretary/ops.py:236",
    # [データ] secretary/ops.py: validate_time/validate_datetime の既定 field。呼び出し側は
    # すべて明示的に上書きしており、既定値は保守のためだけに残る死んだ経路。
    "staff/secretary/ops.py:143", "staff/secretary/ops.py:156",
    # [データ] steward/cli.py: 定期支払いの記録として DB の memo 列に永続する文字列
    # (decision.py の ruling と同じ「主人のデータ」の扱い)。
    "staff/steward/cli.py:315",
    # [データ] task.py: link_dependency/dup が task_event.note へ書く定型の一言。
    # 台帳に永続する記録(decision.py の ruling と同じ扱い)。
    "task.py:238", "task.py:528",
    # [データ] board/__init__.py・web/__init__.py・archive.py・gate.py・night/__init__.py の
    # `LABEL` 定数。`manor.cli` の `_run_init` が「部下: {name}」の一覧に使う想定の
    # 表示名だが、実際に読まれるのは `staff/*` 配下の担当モジュールだけ(grep で確認)。
    # 担当モジュールの `LABEL`(agent_meta.py の ALLOWLIST 理由と同じ)に合わせて
    # 日本語のままにする。
    "board/__init__.py:22", "web/__init__.py:19",
    "archive.py:44", "gate.py:58", "night/__init__.py:27",
    # [データ] archive.py: アーカイブした先を示す Markdown コメントを元ファイルへ
    # 挿入する行。端末には出さず、CHANGELOG.md 等その場に永続する注記(decision.py の
    # ruling と同じ「主人のファイルへ残す記録」の扱い)。
    "archive.py:393",
    # [データ] gate.py: 振る舞い試験ランナー自身の標準出力(「結果一式: <path>」)を
    # 読み取るための正規表現。ランナー側の出力形式そのものであって manor の文言では
    # ない。
    "gate.py:91",
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


def _string_constant_offenders(path: Path, tree: ast.Module) -> list[str]:
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
        if f"{rel}:{lineno}" in LINE_ALLOWLIST:
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
    offenders = _string_constant_offenders(path, tree)
    assert offenders == [], "\n".join(offenders)


def test_allowlist_entries_exist_and_still_contain_japanese() -> None:
    """ALLOWLIST の風化を防ぐ: 載せた理由(日本語データを持つ)が消えたら気づけるように。"""
    for rel in ALLOWLIST:
        path = SRC_ROOT / rel
        assert path.is_file(), f"{rel} が見つかりません（ALLOWLIST から外すこと）"
        text = path.read_text(encoding="utf-8")
        assert JA_CHAR_PATTERN.search(text), f"{rel} はもう日本語を含みません（ALLOWLIST から外すこと）"


def test_line_allowlist_entries_still_contain_japanese() -> None:
    for entry in LINE_ALLOWLIST:
        rel, lineno_str = entry.rsplit(":", 1)
        path = SRC_ROOT / rel
        assert path.is_file(), f"{rel} が見つかりません（{entry} を LINE_ALLOWLIST から外すこと）"
        lines = path.read_text(encoding="utf-8").split("\n")
        line = lines[int(lineno_str) - 1] if 0 < int(lineno_str) <= len(lines) else ""
        assert JA_CHAR_PATTERN.search(line), f"{entry} はもう日本語を含みません（行がずれたか、直った可能性）"
