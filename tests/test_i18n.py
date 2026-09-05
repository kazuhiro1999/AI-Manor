"""CLI の多言語化（ja/en）の抜けを塞ぐ試験（ADR-012 §3 D9・D10・D11。5h-2）。

`web/src/app/i18n/parity.test.ts` を手本にした CLI 版。画面とキー空間を共有しない
（`src/manor/i18n/{ja,en}.json`）ので、試験も別に持つ。

## 何を検算するか

1. ja/en のキー集合が完全に一致する（訳し忘れ・余剰のどちらも無い）。
2. どのキーも、解決した文字列がキー自身と一致しない（画面にキーがそのまま出ない）。
3. **差し込みの引数漏れを塞ぐ**（5h-1 の検分で見つかった「辞書は正しいのに呼び出し側が
   引数を渡し忘れ、画面に undefined が出た」という穴——Python 版はここで塞ぐ）。
   全キーのテンプレートから `{name}` 形式の差し込み名を洗い出し、それを満たす引数を
   実際に渡して `t()` を呼び、例外が出ないことを確かめる。TS 版のように固定の
   SAMPLE_PARAMS を手で保守するのではなく、辞書自身から差し込み名を集めるので、
   新しいキーを足しても自動で検算対象に入る。
4. 差し込みの引数を**渡さない**呼び出しは例外になる（黙って `{name}` を残さない）。
5. 未知のキーで呼ぶと例外になる（画面にキーをそのまま出さない）。
"""

from __future__ import annotations

import re

import pytest

from manor import i18n


def test_reset_between_tests() -> None:
    # 他の試験が set_language() で固定した言語がここへ漏れてこないように、
    # 各試験の前提を揃える（実際の分離は各試験内の fixture が行う）。
    i18n.reset_cache()


@pytest.fixture(autouse=True)
def _isolate_language():
    """試験どうしで言語のキャッシュが漏れないようにする。"""
    i18n.reset_cache()
    yield
    i18n.reset_cache()


def test_key_sets_match_exactly() -> None:
    ja_keys = set(i18n.all_keys("ja"))
    en_keys = set(i18n.all_keys("en"))
    missing_in_en = ja_keys - en_keys
    extra_in_en = en_keys - ja_keys
    assert missing_in_en == set(), f"en.json に無いキー（訳し忘れ）: {sorted(missing_in_en)}"
    assert extra_in_en == set(), f"ja.json に無いキー（余剰）: {sorted(extra_in_en)}"


def test_keys_are_not_blank() -> None:
    for lang in ("ja", "en"):
        for key in i18n.all_keys(lang):
            assert key.strip(), f"{lang}.json に空文字のキーがあります"


def test_values_never_equal_their_key() -> None:
    """画面（この場合は端末）にキーがそのまま出ない（ADR-012 D9 の CLI 版）。"""
    for lang in ("ja", "en"):
        i18n.set_language(lang)
        for key in i18n.all_keys(lang):
            placeholders = i18n.placeholders_in(lang, key)
            params = {name: f"<{name}>" for name in placeholders}
            value = i18n.t(key, **params)
            assert value != key, f"{lang}.{key} の値がキー自身と同じです"
            assert value.strip() != "", f"{lang}.{key} の値が空です"


def test_en_has_no_leftover_japanese() -> None:
    """訳し忘れの検算——en.json の値に日本語（ひらがな・カタカナ・漢字）が残っていない。"""
    ja_char_pattern = re.compile(r"[぀-ヿ㐀-鿿]")
    offenders = [key for key in i18n.all_keys("en") if ja_char_pattern.search(_en_value(key))]
    assert offenders == [], f"en.json に日本語が残っています: {offenders}"


def _en_value(key: str) -> str:
    return i18n._load("en")[key]  # noqa: SLF001 - 試験専用の内部アクセス


def test_interpolation_resolves_for_every_key() -> None:
    """全キーを走らせて差し込みが解決することを確かめる（5h-2 の本体。D9 追補）。

    辞書のテンプレートから `{name}` 形式の差し込み名を洗い出し、それを満たす引数を
    渡して呼ぶ——渡し忘れが有れば `I18nError` が飛ぶはずなので、ここで確実に拾う。
    """
    for lang in ("ja", "en"):
        i18n.set_language(lang)
        for key in i18n.all_keys(lang):
            placeholders = i18n.placeholders_in(lang, key)
            params = {name: f"<{name}>" for name in placeholders}
            # 例外が飛べば pytest がそのまま落とす(= 差し込みの引数漏れがあるという合図)。
            i18n.t(key, **params)


def test_missing_interpolation_arg_raises() -> None:
    """引数を渡し忘れたら例外——黙って `{name}` を残したまま返さない(D9 追補の核心)。"""
    i18n.set_language("ja")
    keys_with_params = [k for k in i18n.all_keys("ja") if i18n.placeholders_in("ja", k)]
    assert keys_with_params, "差し込みのあるキーが1つも無い(試験の前提が崩れています)"
    sample_key = keys_with_params[0]
    with pytest.raises(i18n.I18nError):
        i18n.t(sample_key)  # 必要な引数を1つも渡さない


def test_unknown_key_raises() -> None:
    """辞書に無いキーで呼ぶと例外——画面にキーをそのまま出さない。"""
    i18n.set_language("ja")
    with pytest.raises(i18n.I18nError):
        i18n.t("no.such.key.exists")


def test_extra_unused_params_are_ignored() -> None:
    """使われない余分な引数は無害(呼び出し側が念のため多めに渡しても構わない)。"""
    i18n.set_language("ja")
    # 差し込みの無いキーへ余分な引数を渡してもエラーにならない。
    key = next(k for k in i18n.all_keys("ja") if not i18n.placeholders_in("ja", k))
    i18n.t(key, unused="x", another=1)


class TestLanguageResolution:
    def test_env_var_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MANOR_LANG", "en")
        assert i18n.resolve_language(home=None) == "en"

    def test_env_var_invalid_falls_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MANOR_LANG", "fr")
        # 語彙外の MANOR_LANG は無視され、OS ロケール判定へ落ちる(必ず ja/en のどちらかになる)。
        assert i18n.resolve_language(home=None) in ("ja", "en")

    def test_config_setting_used_when_no_env(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MANOR_LANG", raising=False)
        from manor.web.config import set_manor_language

        set_manor_language(tmp_path, "en")
        assert i18n.resolve_language(home=tmp_path) == "en"

    def test_current_language_is_cached_per_process(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MANOR_LANG", "en")
        assert i18n.current_language() == "en"
        # プロセス内キャッシュ済みなので、環境変数を変えても current_language() は動かない。
        monkeypatch.setenv("MANOR_LANG", "ja")
        assert i18n.current_language() == "en"
        i18n.reset_cache()
        assert i18n.current_language() == "ja"

    def test_set_language_rejects_unknown(self) -> None:
        with pytest.raises(ValueError):
            i18n.set_language("fr")
