"""YouTube 拡張のマニフェスト（ADR-009 D2。レシピ動画の検証・ADR-023 の前段）。

YouTube Data API v3 の**API キー**（利用者ごと。ADR-014 D5 の `per_user`）で、公開・限定公開の
再生リスト・動画の概要欄・コメントを読む。読む実体は `staff/chef/youtube.py`。ここは拡張機構向けの
薄いマニフェスト層（`notion.py` と同じ形）。

- API キーで読めるのは**公開・限定公開**のものだけ。**非公開の再生リスト・「後で見る」は読めない**
  （利用者の Google アカウントでの OAuth が要る。検証の結果で要否を決める）。
- `check()` は `videos.list` を1回呼ぶ（割り当て 1 単位）。押されたときだけ。
"""

from __future__ import annotations

from pathlib import Path

from .. import branding

MANIFEST: dict[str, object] = {
    "id": "youtube",
    "kind": "service",
    "label": "YouTube（レシピ動画）",
    "summary": (
        "YouTube Data API で、再生リストに保存したレシピ動画の題名・概要欄・コメントを読みます"
        "（素材や料理名で探す・レシピとして取り込むための検証中）。"
        f"無くても {branding.APP_NAME} は完全に動きます。"
    ),
    "install_steps": [
        "Google Cloud Console（https://console.cloud.google.com/）でプロジェクトを1つ作る（既存のものでもよい）",
        "「API とサービス」→「ライブラリ」で YouTube Data API v3 を有効にする",
        "「API とサービス」→「認証情報」→「認証情報を作成」→「API キー」でキーを発行する",
        "発行したキーの「API の制限」で YouTube Data API v3 だけに絞る（漏れたときの被害を小さくする）",
        "キーを登録する: 端末で `uv run manor ext set youtube --secret api_key` を打ち、キーを貼り付けて Enter"
        "（画面・履歴には残りません）",
        "「試す」を押すか `uv run manor ext test youtube` で疎通を確かめる",
    ],
    "fields": [
        {
            "key": "api_key",
            "label": "API キー",
            "kind": "password",
            "required": True,
            "per_user": True,
            "help": "YouTube Data API v3 の API キー（利用者ごと）",
        },
        {
            "key": "playlists",
            "label": "再生リスト",
            "kind": "text",
            "required": False,
            "per_user": True,
            "help": "レシピ動画を保存している再生リストの URL（複数はカンマ区切り。公開か限定公開のもの）",
        },
    ],
    "secret_fields": ["api_key"],
}


def detect(home: Path) -> dict[str, object]:
    """サービス拡張（ローカルの実体を持たない）なので常に「見つかった」扱い。"""
    return {"installed": True, "reason": ""}


def check(home: Path) -> dict[str, object]:
    """principal の API キーで `videos.list` を1回呼んで疎通を確かめる。例外は投げない。"""
    from ..staff.chef import youtube as yt  # noqa: PLC0415 - 押されたときだけ読み込む

    return yt.check_key(home)
