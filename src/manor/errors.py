"""manor 全体の例外。

なぜ: CLI の入口を1箇所に絞るため、業務規則違反はすべてこの例外に寄せる。
`code` は CLI の終了コードにそのまま使う（0=正常 / 1=失敗 / 2=見つからない・語彙外）。

## `message_ja` は変えない（5h-2・ADR-012 §3 D13 追補）

`ManorError` は CLI（`cli.py`）と Web バックエンド（`web/_common.py` が
`HTTPException(detail=exc.message_ja)` として使う）の両方から投げられる、業務モジュール
（`task.py` `project.py` `decision.py` …）の共有コード。5h-1 は画面側を完成させ済みで、
その API エラーの `detail` は今もこの日本語文をそのまま使っている——ここを言語切り替え
式にすると Web 側の挙動を変えてしまう（`web/` には触らない約束にも反する）。

よって **`message_ja` は常に日本語のまま**（呼び出し側の互換を壊さない）にし、CLI 表示用
だけ別に `key`/`params`（`manor.i18n` のキーと差し込み引数）を**任意で**持たせる。
`key` を渡さなかった呼び出し（大半の既存コード）は、CLI でも今まで通り日本語のまま出る
——5h-2 は「全てのエラーを訳す」ではなく「主人が最も読む導線から訳す」対応であることの
表れであり、未対応の箇所は報告に明記する。
"""

from __future__ import annotations

from typing import Any


class ManorError(Exception):
    """manor の業務規則違反・入力誤り。CLI はこれだけを捕まえてメッセージを出す。"""

    def __init__(
        self,
        message_ja: str,
        *,
        code: int = 1,
        key: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message_ja)
        self.message_ja = message_ja
        self.code = code
        #: CLI 表示用の翻訳キー（`manor.i18n` の語彙）。渡さなければ `message_ja` のまま
        #: （Web 側の `detail` は `key` の有無に関わらず常に `message_ja`）。
        self.key = key
        self.params = params or {}

    def localized_message(self) -> str:
        """CLI（`cli.py`）が画面に出す文言。`key` があれば現在の言語で解決し、無ければ
        `message_ja`（日本語）をそのまま返す。翻訳の解決自体が壊れても（辞書に無い・
        差し込み漏れ）、主人には元の日本語のエラーを見せる——訳の不備でエラー自体が
        読めなくなるのは本末転倒なので、ここだけは黙って `message_ja` に逃がす。
        """
        if self.key is None:
            return self.message_ja
        from . import i18n

        try:
            return i18n.t(self.key, **self.params)
        except i18n.I18nError:
            return self.message_ja
