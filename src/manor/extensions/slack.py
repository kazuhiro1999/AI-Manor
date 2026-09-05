"""Slack 拡張のマニフェスト（ADR-009 D2・D9・§3「Slack 拡張（5b）」）。

送受信の実体（`brief`／`inbox`／`test_connection`／禁止語スキャン等）はすべて
`src/manor/slack.py` に置いてある。このファイルは拡張機構（`src/manor/extensions/`）
向けの**薄いマニフェスト層**——`MANIFEST` と `detect()`／`check()` だけを持つ。

**登録簿（`src/manor/extensions/__init__.py`）へこのファイルを並べるのは別の担当の
作業**（`_MODULES` タプルへ `from . import slack as _slack_mod` を足す1行。ADR-009 D2）。
`detect()`/`check()` の呼び出し規約は、実際に出来上がった `extensions/__init__.py`
（`_safe_detect`/`_safe_check`。`tailscale.py`/`voicevox.py` の実装）を読んで合わせた:

- `detect(home: Path) -> {"installed": bool, "reason": str}` — サービス拡張
  （ローカルにインストールする実体を持たない。D1「サービス＋鍵」）なので `installed`
  は常に `True`。`not_installed` になる余地が無い
- `check(home: Path) -> {"ok": bool, "reason": str}` — `auth.test` を叩いて疎通を
  確かめる。**外部へ実際に触るので、押されたときだけ呼ばれる**（`status()`/`statuses()`/
  `detail()` からは呼ばれない。ADR-009 D3）。`checked_at` は登録簿側
  （`extensions.test()`）が `util.now()` で付けるので、ここでは返さなくてよい
  （返しても無害だが、`slack.test_connection()` の戻り値をそのまま使うため付いている）
"""

from __future__ import annotations

from pathlib import Path

from .. import branding
from .. import slack as slack_mod

MANIFEST: dict[str, object] = {
    "id": "slack",
    "kind": "service",
    "label": "Slack",
    "summary": (
        "朝のブリーフィングを Slack へ送り、スレッドの返信を承認／却下の裁定として"
        f"取り込みます。無くても {branding.APP_NAME} は完全に動きます。"
    ),
    "install_steps": [
        "https://api.slack.com/apps を開き「Create New App」→「From scratch」。"
        "名前は任意（例: manor butler）、インストール先ワークスペースを選ぶ",
        "左メニュー「OAuth & Permissions」→「Scopes」→「Bot Token Scopes」に"
        " chat:write と channels:history を追加する"
        "（招待するチャンネルが非公開なら groups:history も追加）",
        "ページ上部の「Install to Workspace」を実行し、発行された"
        " Bot User OAuth Token（xoxb- で始まる）を控える",
        "通知を受けるチャンネル（プライベート推奨）を用意し、そのチャンネルで"
        " `/invite @<アプリ名>` を実行してこの Bot を招待する",
        "招待したチャンネルを右クリック→チャンネル詳細→一番下の"
        " チャンネル ID（C から始まる文字列）を控える",
        "この画面でチャンネル ID（channel）を入力し、Bot Token（bot_token）を"
        " 貼り付けて保存する",
    ],
    "fields": [
        {
            "key": "channel",
            "label": "チャンネル",
            "kind": "text",
            "required": True,
            "help": "送り先のチャンネル ID",
        },
        {
            "key": "bot_token",
            "label": "Bot Token",
            "kind": "password",
            "required": True,
            "help": "xoxb- で始まる Bot User OAuth Token",
        },
    ],
    "secret_fields": ["bot_token"],
}


def detect(home: Path) -> dict[str, object]:
    """サービス拡張（ローカルにインストールする実体を持たない）。D1「サービス＋鍵」——
    ローカル探索の対象が無いので常に「見つかった」扱いにする。**例外は投げない**。
    `home` はこの拡張では使わない（`tailscale.detect(home)` と同じ引数の形に揃えるため
    受け取るだけ）。
    """
    return {"installed": True, "reason": ""}


def check(home: Path) -> dict[str, object]:
    """`auth.test` を叩いて疎通を確かめる（ADR-009 D3）。**外部へ実際に触るので、
    押されたときだけ呼ばれる**（画面の描画・一覧表示では回さない）。例外は投げない
    ——`src/manor/slack.py` の `test_connection()` がその約束を守っている。
    """
    return slack_mod.test_connection(home)
