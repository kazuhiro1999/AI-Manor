"""`home/config.toml` の読み書き（ADR-005 §2「settings」）。

読みは `tomllib`（標準ライブラリ）。**書きは最小の TOML 書き出しを自前で**行う——
既存の他の節（`[notify]` は `notify.py` が読む）を壊さないよう、まず全体を読み、
触る節だけ差し替えてから丸ごと書き直す。

**1段のネストだけ対応する**（ADR-011 D10。`[voice.speakers]` — 担当ごとの話者の
上書き）。節の値（辞書）の中にさらに辞書が入っていれば、それを `[section.key]` という
別見出しのテーブルとして書き出す（`_write_table`）。それ以上のアプリの `config.toml` は
フラットな `key = "value"` の節だけで足りる。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

CONFIG_FILE_NAME = "config.toml"


def read_config(home: Path) -> dict[str, object]:
    """`config.toml` の全体。無い・壊れているときは空辞書（既定値で動く。他の節と同じ約束）。"""
    path = Path(home) / CONFIG_FILE_NAME
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if value is None:
        return '""'
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _write_table(lines: list[str], header: list[str], values: dict[str, object]) -> None:
    """`values` の中のスカラーはこの見出し（`header`）の直下に、辞書は入れ子のテーブル
    （`[a.b]` という別見出し）として再帰的に書く（ADR-011 D10）。空の辞書は書かない
    （`[voice.speakers]` が空なら見出しごと出さない——空のテーブル見出しを config.toml に
    残す理由が無い）。
    """
    scalars = {k: v for k, v in values.items() if not isinstance(v, dict)}
    tables = {k: v for k, v in values.items() if isinstance(v, dict) and v}
    if scalars or not tables:
        lines.append(f"[{'.'.join(header)}]")
        for key, value in scalars.items():
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")
    for key, sub in tables.items():
        _write_table(lines, header + [key], sub)


def write_config(home: Path, data: dict[str, dict[str, object]]) -> None:
    """`data`（節名 → {key: value}）を丸ごと書き出す。呼び出し側が `read_config` の結果へ
    差分を merge してから渡す約束（`update_section` がそれをやる）。値が辞書のキーは
    `[section.key]` という入れ子のテーブルとして書く（`_write_table`。ADR-011 D10）。
    """
    lines: list[str] = []
    for section, values in data.items():
        if not isinstance(values, dict) or not values:
            continue
        _write_table(lines, [section], values)
    text = ("\n".join(lines).rstrip() + "\n") if lines else ""
    path = Path(home) / CONFIG_FILE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    # render.py / handoff.py と同じ理由: Windows での改行変換を避けるためバイト列で書く。
    path.write_bytes(text.encode("utf-8"))


def _merge_update(current: dict[str, object], updates: dict[str, object]) -> None:
    """`updates` を `current` へ重ねる。値が辞書同士なら**再帰的に統合**する（ADR-011
    D10: `[voice.speakers]` のように入れ子のテーブルを持つ節で、今回渡さなかった他の
    担当の上書きを消さないため——単純な `dict.update` だと丸ごと差し替わってしまう）。
    値に `None` を渡すとそのキーを削除する（D10: フォームで空にした担当の上書きを外す印。
    `extensions.voicevox.to_config` が使う）。
    """
    for key, value in updates.items():
        if value is None:
            current.pop(key, None)
            continue
        if isinstance(value, dict):
            # 入れ子のテーブル。**相手側がまだ無くても空の辞書から統合する**——素直に
            # 代入すると削除の印（`None`）まで書き込まれ、`chef = ""` のような空の行が
            # 設定ファイルに残る（2026-09-05 実測。初回保存だけで起きるので試験を足した）。
            nested = current.get(key)
            nested = dict(nested) if isinstance(nested, dict) else {}
            _merge_update(nested, value)
            if nested:
                current[key] = nested
            else:
                current.pop(key, None)  # 中身が空になったテーブルは節ごと残さない
        else:
            current[key] = value


def update_section(home: Path, section: str, updates: dict[str, object]) -> None:
    """`[section]` の中の指定したキーだけを更新する。既存の他の節・他のキーはそのまま残す。
    入れ子のテーブル（辞書の値）は `_merge_update` が深く統合する。
    """
    data = dict(read_config(home))
    current = dict(data.get(section, {})) if isinstance(data.get(section), dict) else {}
    _merge_update(current, updates)
    data[section] = current
    write_config(home, data)


# --- web 節のヘルパー -----------------------------------------------------------------


def get_web_section(home: Path) -> dict[str, object]:
    data = read_config(home)
    section = data.get("web")
    return dict(section) if isinstance(section, dict) else {}


# **`get_passcode()` は無い。** かつては平文を返す関数があったが、消した（2026-09-05）
# ——平文を返す口が存在する限り、いつか誰かが呼ぶ。いまは塩つきのハッシュしか保存して
# おらず（`web/passcode.py`）、元の言葉はどこからも復元できない。


def has_passcode(home: Path) -> bool:
    """設定されているか（真偽だけ。ADR-009 D4「API は `has_<key>` しか返さない」）。"""
    from . import passcode as passcode_mod

    return passcode_mod.has_passcode(home)


def set_passcode(home: Path, passcode: str) -> None:
    """新しい passcode を保存する。**`config.toml` には書かない**——秘密の置き場へ、
    しかもハッシュにして保存する（`web/passcode.py` の docstring 参照）。
    `home` は署名を揃えるために受け取るだけ（置き場は home ごとに分けない。理由は同上）。
    """
    from . import passcode as passcode_mod

    passcode_mod.set_passcode(passcode)


def get_require_passcode(home: Path) -> bool:
    """`[web] require_passcode` の値。`auth.auth_mode` と同じ読み方（真偽の厳密一致）。"""
    return get_web_section(home).get("require_passcode") is True


def set_require_passcode(home: Path, value: bool) -> None:
    """ADR-013 D2: 画面のトグルから書く。**締め出しを防ぐ検算はここではしない**
    ——呼び出し側（`web/api_v1/settings.py`）が「passcode 未設定のまま on」「非ループバックで
    待ち受け中に off」を先に拒む。ここは config.toml へ書くだけの薄い層に保つ。
    """
    update_section(home, "web", {"require_passcode": value})


# --- manor 節のヘルパー（ADR-012 §3 D11: 画面の言語） -----------------------------------

#: `[manor] language` に許す値。`auto` は「ブラウザ・OS の言語に従う」（フロント側で解決。
#: `web/src/app/i18n/language.ts` の `resolveLanguage` と同じ語彙）。
VALID_LANGUAGES = frozenset({"auto", "ja", "en"})


def get_manor_language(home: Path) -> str:
    """`[manor] language` の値。無い・壊れている・語彙外の値なら既定 `"auto"`
    （他の節と同じ「既定値で動く」約束）。"""
    data = read_config(home)
    section = data.get("manor")
    value = section.get("language") if isinstance(section, dict) else None
    return value if isinstance(value, str) and value in VALID_LANGUAGES else "auto"


def set_manor_language(home: Path, language: str) -> None:
    if language not in VALID_LANGUAGES:
        raise ValueError(f"unknown language: {language!r}")
    update_section(home, "manor", {"language": language})
