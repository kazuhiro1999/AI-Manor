"""VOICEVOX 拡張（ADR-009 D9・ADR-011 D10）。

`[voice] engine = "voicevox"` を読む既存の実装（`src/manor/voice.py`）はそのまま——
ここはその上に「導入されているか」「疎通するか」「話者を名前で選べるか」の口を足すだけ。
**`speaker = 13` を手で書かせる問題を解く**のが本題（ADR-009 §1）。

`detect`/`check`/`options` は例外を外へ出さない（呼び出し元の `extensions/__init__.py` も
念のため包むが、ここでも自前で守る）。

**ADR-011 D10（担当ごとの声）**: `speaker`（既定）に加えて、担当ごとの `speaker_<agent>`
フィールドを `agent_meta` から機械的に組み立てる——担当が増えてもここを書き足す必要が無い。
画面のフォームはこれまでどおり `fields` から自動で組まれる（新しい画面は作らない）。保存先は
新しい節ではなく既存の `[voice.speakers]`（入れ子のテーブル）——`speaker_<agent>` という
平らなフィールド鍵と、実際の保存先（`[voice.speakers].<agent>`）がずれるので、その変換を
`to_config`/`from_config`/`config_keys`（`extensions/__init__.py` が呼ぶ任意のフック。
ADR-009 D2 に追記）で担う。
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import agent_meta
from .. import voice as voice_mod

#: フォームのフィールド鍵の接頭辞。`speaker_housekeeper` → 担当 `housekeeper`。
_SPEAKER_FIELD_PREFIX = "speaker_"


def _speaker_agent_fields() -> list[dict[str, object]]:
    """担当ごとの話者フィールド（D10）。`agent_meta.valid_agents()`（`.claude/agents/*.md`
    ＋ butler）から組み立てる——担当の一覧をここで手書きすると、新しい担当を足したときに
    書き漏らす（D9「Build the list from agent_meta so adding an agent later cannot
    forget it」）。
    """
    fields: list[dict[str, object]] = []
    for agent in agent_meta.valid_agents():
        fields.append(
            {
                "key": f"{_SPEAKER_FIELD_PREFIX}{agent}",
                "label": f"{agent_meta.agent_label(agent)}の声",
                "kind": "select",
                "options_from": "speakers",
                "help": "空なら既定の話者（上の「既定の話者」）を使います。",
                "required": False,
            }
        )
    return fields


MANIFEST: dict[str, object] = {
    "id": "voicevox",
    "label": "VOICEVOX（音声合成）",
    "kind": "local_app",
    "summary": "執事の声を VOICEVOX で合成します。無くても OS 既定の声で喋ります。",
    "install_steps": [
        "1. https://voicevox.hiroshiba.jp/ から VOICEVOX をダウンロードしてインストールします。",
        "2. 一度 VOICEVOX を起動し、初回の利用規約への同意を済ませます（このカードからは操作しません）。",
        "3. このカードを開いて話者を選び、保存してください。エンジンの起動・停止は manor が話す直前に自動で行います。",
    ],
    "fields": [
        {
            "key": "speaker",
            "label": "既定の話者",
            "kind": "select",
            "options_from": "speakers",
            "help": "エンジンから取得した一覧から選びます（エンジンが起動していないと一覧が空になります）",
            "required": True,
        },
        *_speaker_agent_fields(),
        {
            "key": "engine_path",
            "label": "エンジンの場所",
            "kind": "path",
            "help": "空なら自動で探します",
            "required": False,
        },
    ],
    "secret_fields": [],
}

#: 非秘密の設定は新しい `[voicevox]` ではなく既存の `[voice]` 節へ書く
#: （ADR-009 D4「既存の [voice] はこの形。互換を保つ」・D9「既存のものを載せ替える」）。
CONFIG_SECTION = "voice"

#: 設定を保存するたびに必ず書き足す隠しキー（画面のフォームには出さない。D2「engine（隠し）」）。
#: これが無いと既存の `voice.py`（`[voice] engine == "voicevox"` を見る）が拾わない。
IMPLIED_CONFIG: dict[str, object] = {"engine": "voicevox"}


# --- ADR-011 D10: speaker_<agent> ⇔ [voice.speakers].<agent> の変換 --------------------------


def to_config(values: dict[str, object]) -> dict[str, object]:
    """フォームの値（`speaker`・`engine_path`・`speaker_<agent>`…）を `[voice]` 節へ
    書く形に変換する。`speaker_<agent>` は `[voice.speakers] <agent>` へ束ねる——空文字
    （フォームで欄を空にした＝「既定に戻す」）は、その担当のキーを削除する印として `None`
    を返す（`web/config.py` の `_merge_update` が `None` を削除として扱う）。
    """
    out: dict[str, object] = {}
    speakers: dict[str, object] = {}
    for key, value in values.items():
        if key.startswith(_SPEAKER_FIELD_PREFIX):
            agent = key[len(_SPEAKER_FIELD_PREFIX) :]
            if value is None or (isinstance(value, str) and not value.strip()):
                speakers[agent] = None  # 削除の印（D10「空なら既定を使う」）
            else:
                speakers[agent] = value
        else:
            out[key] = value
    if speakers:
        out["speakers"] = speakers
    return out


def from_config(cfg: dict[str, object]) -> dict[str, object]:
    """`[voice]` 節の生の値から、フォームの `speaker_<agent>` 値へ（`to_config` の逆）。
    上書きが無い担当は `None`（フォームでは空欄になる）。
    """
    speakers = cfg.get("speakers")
    speakers = speakers if isinstance(speakers, dict) else {}
    return {
        f"{_SPEAKER_FIELD_PREFIX}{agent}": speakers.get(agent) for agent in agent_meta.valid_agents()
    }


def config_keys() -> set[str]:
    """`forget()` が `[voice]` から消す生キー一式。`speakers` は入れ子のテーブルまるごと
    ——担当ごとの上書きはすべてこの拡張の持ち物なので、丸ごと消してよい。
    """
    return {"engine", "speaker", "engine_path", "speakers"}


def _raw_config(home: Path) -> dict[str, object]:
    """`[voice]` の生の中身。`engine` が未設定でも読める版
    （`voice.load_config()` は `is_configured()` を要求するので、保存前の検出・話者一覧
    取得にはそのまま使えない——エンジンは入っているが speaker をまだ選んでいない状態でも
    導入検出・話者一覧の取得はできてほしい）。
    """
    return voice_mod._load_voice_config(Path(home))


def _resolve_engine_path(home: Path) -> Path:
    cfg = _raw_config(home)
    raw = cfg.get("engine_path")
    if isinstance(raw, str) and raw.strip():
        return Path(raw)
    return voice_mod._default_engine_path()


def _resolve_host_port(home: Path) -> tuple[str, int]:
    cfg = _raw_config(home)
    host = cfg.get("host")
    host = host if isinstance(host, str) and host.strip() else voice_mod.DEFAULT_HOST
    port = voice_mod._as_int(cfg.get("port"), voice_mod.DEFAULT_PORT)
    return host, port


def detect(home: Path) -> dict[str, object]:
    """導入済みか。**`[voice] engine` の設定は見ない**——エンジンのバイナリが既定
    （または `engine_path` で指定済み）の場所にあるか、ポートが既に応答しているかのどちらか。
    """
    home = Path(home)
    engine_path = _resolve_engine_path(home)
    if engine_path.is_file():
        return {"installed": True, "reason": f"エンジンが見つかりました: {engine_path}"}
    host, port = _resolve_host_port(home)
    body = voice_mod._http_get(f"http://{host}:{port}/version", timeout=voice_mod.STATUS_TIMEOUT)
    if body is not None:
        return {"installed": True, "reason": "エンジンがポートに応答しました"}
    return {"installed": False, "reason": "VOICEVOX のエンジンが見つかりません"}


def check(home: Path) -> dict[str, object]:
    """`GET /version` で疎通を確かめる。**保存済みの設定（`[voice] engine = "voicevox"`）が
    前提**——`voice.load_config()` を使う（未保存なら理由を返して終わる。例外は出さない）。
    """
    try:
        cfg = voice_mod.load_config(Path(home))
        if cfg is None:
            return {"ok": False, "reason": "未設定です（話者を選んで保存してください）"}
        body = voice_mod._http_get(f"http://{cfg.host}:{cfg.port}/version", timeout=voice_mod.STATUS_TIMEOUT)
        if body is None:
            return {"ok": False, "reason": "エンジンに接続できません（起動していますか）"}
        try:
            version: object = json.loads(body.decode("utf-8"))
        except Exception:
            version = body.decode("utf-8", errors="replace")
        return {"ok": True, "reason": f"接続できました（version={version}）"}
    except Exception as exc:  # noqa: BLE001 - check() は例外を外へ出さない
        return {"ok": False, "reason": f"確認できませんでした: {exc}"}


def options(home: Path, name: str) -> list[dict[str, object]]:
    """`options_from: "speakers"`。`GET /speakers` を平らにする——
    `[{value: <スタイルID>, label: "<話者名>（<スタイル名>）"}]`（ADR-009 §1 の本題）。
    エンジンが落ちていれば空リスト（例外は出さない）。
    """
    if name != "speakers":
        return []
    try:
        host, port = _resolve_host_port(Path(home))
        body = voice_mod._http_get(f"http://{host}:{port}/speakers", timeout=voice_mod.STATUS_TIMEOUT)
        if body is None:
            return []
        data = json.loads(body.decode("utf-8"))
        if not isinstance(data, list):
            return []
        out: list[dict[str, object]] = []
        for speaker in data:
            if not isinstance(speaker, dict):
                continue
            speaker_name = str(speaker.get("name", ""))
            styles = speaker.get("styles")
            if not isinstance(styles, list):
                continue
            for style in styles:
                if not isinstance(style, dict):
                    continue
                style_id = style.get("id")
                if style_id is None:
                    continue
                style_name = str(style.get("name", ""))
                # `group` を添えると画面が「話者 → スタイル」の2段で選ばせる（ADR-009 D17）。
                # `label` は単独でも意味が通る形のまま残す——`group` を解さない読み手
                # （CLI・古い画面）でも「四国めたん（ノーマル）」と読める。
                out.append(
                    {
                        "value": style_id,
                        "label": f"{speaker_name}（{style_name}）",
                        "group": speaker_name,
                        "member_label": style_name,
                    }
                )
        return out
    except Exception:  # noqa: BLE001 - options() は例外を出さず空で返す（D5）
        return []
