"""CLI の文言の多言語化（ja/en）。ADR-012 §3 D10・D11。

画面（`web/`）と語彙を共有しない——CLI の文言は画面と関心が違うため、辞書もキー空間も
独立に持つ（`web/src/app/i18n/` を手本にしたが、置き場は分ける。ADR-012 D10）。

## 言語の決め方（D11）

`MANOR_LANG` 環境変数 → `home/config.toml` の `[manor] language` → 既定 `auto`。
`auto` は OS のロケールを見て、わからなければ `ja`。

**プロセスにつき1回だけ解決し、以後はキャッシュする**（`current_language()`）。CLI の
1回の起動の中で言語が変わることは無い（D11「CLI は MANOR_LANG を最優先で見る。
一度きりの切り替え用」と同じ発想）。試験でリセットしたいときは `reset_cache()`。

## 抜けを塞ぐ（ADR-012 D9・D11 の CLI 版。5h-2 の本体）

1. `ja.json` と `en.json` のキー集合が完全に一致することは `tests/test_i18n.py` が検算する。
2. 未知のキーで `t()` を呼ぶと**例外を投げる**（画面にキーをそのまま出さない。5h-1 の
   `console.error` だけで済ませる作法と違い、CLI は対話が無いので黙って空文字を返しても
   誰も気づけない。落とすほうが安全側）。
3. **差し込みの引数漏れも例外を投げる**（5h-1 の検分で見つかった「辞書は正しいのに
   呼び出し側が引数を渡し忘れ、画面に undefined が出た」という穴を Python でも塞ぐ。
   `str.format(**kwargs)` は不足キーがあれば素の `KeyError` を投げる仕組みを使い、
   それを分かりやすいメッセージへ包み直すだけ——`{name}` が解決されないまま画面に出る
   経路そのものが無い）。
"""

from __future__ import annotations

import json
import locale
import os
import re
from pathlib import Path
from typing import Final

_DICT_DIR: Final[Path] = Path(__file__).resolve().parent
_VALID_LANGUAGES: Final[tuple[str, ...]] = ("ja", "en")

_PLACEHOLDER_PATTERN: Final[re.Pattern[str]] = re.compile(r"\{(\w+)\}")

_dict_cache: dict[str, dict[str, str]] = {}
_current_lang: str | None = None


class I18nError(Exception):
    """辞書に無いキー・差し込みの引数漏れ。**主人には見せない**——ここが例外を投げる時点で
    CLI 側の実装ミス（呼び出し忘れ・打ち間違い）であり、`ManorError`（業務規則違反）とは別物。
    """


def _load(lang: str) -> dict[str, str]:
    cached = _dict_cache.get(lang)
    if cached is not None:
        return cached
    path = _DICT_DIR / f"{lang}.json"
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise I18nError(f"[i18n] {path} の中身が辞書ではありません")
    _dict_cache[lang] = data
    return data


def _detect_os_language() -> str:
    """OS のロケールから ja/en を推測する。わからなければ ja（D11）。

    Windows は `locale.getlocale()` が `("Japanese_Japan", "932")` のような Windows 独自の
    表記を返す（実機確認 2026-09-05。ISO の `ja_JP` ではない）ので、`ja`/`japan`・
    `en`/`english` のどちらの表記も拾えるよう緩く判定する。
    """
    candidates: list[str] = []
    try:
        code, _enc = locale.getlocale()
        if code:
            candidates.append(code)
    except Exception:  # noqa: BLE001 - ロケール取得の失敗で CLI を止めない
        pass
    for env_name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(env_name)
        if value:
            candidates.append(value)
    for code in candidates:
        lower = code.lower()
        if lower.startswith("ja") or "japan" in lower:
            return "ja"
        if lower.startswith("en") or "english" in lower:
            return "en"
    return "ja"


def resolve_language(home: Path | None = None) -> str:
    """D11 の順番（`MANOR_LANG` → `[manor] language` → OS ロケール → `ja`）で言語を決める。

    キャッシュしない素の関数——`current_language()` がキャッシュ層を持つ。試験からは
    こちらを直接呼べば、プロセス内キャッシュを気にせず何度でも検算できる。
    """
    env_value = os.environ.get("MANOR_LANG", "").strip().lower()
    if env_value in _VALID_LANGUAGES:
        return env_value

    setting = "auto"
    if home is not None:
        try:
            from ..web.config import get_manor_language  # 遅延 import（循環回避）

            setting = get_manor_language(home)
        except Exception:  # noqa: BLE001 - 設定が読めなくても既定で動く
            setting = "auto"

    if setting in _VALID_LANGUAGES:
        return setting
    return _detect_os_language()


def _default_home() -> Path:
    from .. import util  # 遅延 import（循環回避）

    return util.manor_home()


def current_language() -> str:
    """このプロセスで使う言語。初回だけ `resolve_language()` を呼び、以後はキャッシュする。"""
    global _current_lang
    if _current_lang is None:
        _current_lang = resolve_language(_default_home())
    return _current_lang


def set_language(lang: str) -> None:
    """明示的に言語を固定する（`cli.py` の起動時、および試験から使う）。"""
    global _current_lang
    if lang not in _VALID_LANGUAGES:
        raise ValueError(f"unknown language: {lang!r}")
    _current_lang = lang


def reset_cache() -> None:
    """プロセス内キャッシュを外す（試験専用。実行中の CLI がこれを呼ぶことはない）。"""
    global _current_lang
    _current_lang = None


def t(key: str, /, **kwargs: object) -> str:
    """`key` を現在の言語の文字列へ引く。差し込みは `{name}` の形（キーワード引数で渡す）。

    辞書に無いキー・差し込みの引数漏れは**どちらも例外**（`I18nError`）。黙って
    キーのまま返す・`{name}` を残したまま返すことは無い（モジュール docstring の
    「抜けを塞ぐ」参照）。

    **`key` は位置専用引数**（`/` より前）——テンプレートが `{key}` という名前の
    差し込みを持っていても（実例: `chef.taste.show.line` は当初これで衝突していた）、
    `t("...", key="foo")` の `key=` はキーワード引数として第一引数と揉めず
    `**kwargs` 側にそのまま入る。差し込み名を予約語にしない設計より、位置専用に
    しておくほうが「将来また同じ名前の差し込みを作ってしまう」事故を機械的に防げる。
    """
    lang = current_language()
    dict_ = _load(lang)
    if key not in dict_:
        raise I18nError(f"[i18n] 未知のキー: {key!r}（lang={lang}）")
    template = dict_[key]
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError) as exc:
        raise I18nError(
            f"[i18n] 差し込みの引数が足りません: key={key!r} lang={lang} 不足={exc}"
        ) from exc


def all_keys(lang: str) -> list[str]:
    """試験用: 辞書の全キー。"""
    return list(_load(lang).keys())


def placeholders_in(lang: str, key: str) -> set[str]:
    """試験用: そのキーのテンプレートが要求する差し込み名の集合。"""
    return set(_PLACEHOLDER_PATTERN.findall(_load(lang)[key]))
