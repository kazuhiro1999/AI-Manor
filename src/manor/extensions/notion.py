"""Notion 拡張のマニフェスト（ADR-009 D2・D9・§7「Notion 拡張（5c）」D18・D19・D20）。

投函の実体（`diary`／`test_connection`／禁止語スキャンの再利用等）はすべて
`src/manor/notion.py` に置いてある。このファイルは拡張機構（`src/manor/extensions/`）
向けの**薄いマニフェスト層**——`MANIFEST` と `detect()`／`check()` だけを持つ
（`extensions/slack.py` と同じ形。呼び出し規約もそちらに合わせてある）:

- `detect(home: Path) -> {"installed": bool, "reason": str}` — サービス拡張
  （ローカルにインストールする実体を持たない。D1「サービス＋鍵」）なので `installed`
  は常に `True`
- `check(home: Path) -> {"ok": bool, "reason": str}` — `GET /v1/users/me` を叩いて
  疎通を確かめる（D20）。**外部へ実際に触るので、押されたときだけ呼ばれる**
  （`status()`/`statuses()`/`detail()` からは呼ばれない。ADR-009 D3）

`install_steps` は v1 `apps/notion-diary/SETUP.md` の手順（データベースの作成・内部
コネクションの作成・データベースへのコネクションの接続・データベース ID の控え方・
トークンの発行と貼り付け）を、画面に収まる長さへ畳んだもの。**プロパティ名
（`名前`／`日付`／`タグ`）を主人自身が用意する必要がある**ことも手順に含めてある
——D20「主人のデータベースの列名が違うときのために設定で上書きできるようにはしない。
まず動くものを1つ」。
"""

from __future__ import annotations

from pathlib import Path

from .. import branding
from .. import notion as notion_mod

MANIFEST: dict[str, object] = {
    "id": "notion",
    "kind": "service",
    "label": "Notion",
    "summary": (
        "その日にあったこと（完了したタスク・裁定・マイルストーン・夜勤の結果）を"
        "Notion のデータベースへ日記として1ページ投函します。"
        f"無くても {branding.APP_NAME} は完全に動きます。"
    ),
    "install_steps": [
        "Notion で新しいページを作り、フルページの Table view データベースを1つ挿入する"
        "（例: 「執事日誌」）。プロパティを「名前」（タイトル・既定である）"
        "「日付」（日付型・必須）「タグ」（マルチセレクト・選択肢は空でよい）の3つに整える",
        "ブラウザのアドレスバーから32文字のデータベース ID を控える"
        "（`https://www.notion.so/<ワークスペース>/` に続く32文字の部分）",
        "Notion の「設定」→「コネクト」→「+ 新しいコネクト」で内部コネクションを作る"
        "（名前は任意）。権限は Read content と Insert content の2つだけ ON にする"
        "（Update content は OFF のままでよい）",
        "発行されたトークン（`ntn_` で始まる文字列）を控える",
        "作ったデータベースのページを開き、右上の「•••」→「コネクト」→"
        "「+ コネクトを追加」で、いま作ったコネクションを選んで接続する"
        "（ここを忘れると投函時に必ず失敗する）",
        "この画面でデータベース ID（database_id）を入力し、トークン（token）を"
        "貼り付けて保存する",
    ],
    "fields": [
        {
            "key": "database_id",
            "label": "データベース ID",
            "kind": "text",
            "required": True,
            "help": "投函先データベースの32文字の ID",
        },
        {
            "key": "token",
            "label": "トークン",
            "kind": "password",
            "required": True,
            "help": "ntn_ で始まる内部インテグレーションのトークン",
        },
    ],
    "secret_fields": ["token"],
}


def detect(home: Path) -> dict[str, object]:
    """サービス拡張（ローカルにインストールする実体を持たない）。D1「サービス＋鍵」——
    ローカル探索の対象が無いので常に「見つかった」扱いにする。**例外は投げない**。
    `home` はこの拡張では使わない（`slack.detect(home)` と同じ引数の形に揃えるため
    受け取るだけ）。
    """
    return {"installed": True, "reason": ""}


def check(home: Path) -> dict[str, object]:
    """`GET /v1/users/me` を叩いて疎通を確かめる（ADR-009 D20）。**外部へ実際に触るので、
    押されたときだけ呼ばれる**（画面の描画・一覧表示では回さない）。例外は投げない
    ——`src/manor/notion.py` の `test_connection()` がその約束を守っている。
    """
    return notion_mod.test_connection(home)
