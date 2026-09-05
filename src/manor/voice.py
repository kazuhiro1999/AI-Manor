"""声の機構 `manor voice`（ADR-008 第2期 D7〜D13）。

v1 `apps/butler-face/{speak.ps1, engine.ps1, voice.ps1, warm.ps1}` の移植。**VOICEVOX は
依存にしない**（ADR-008 D5）——`pyproject.toml` は変えない。`uv sync` で入るものは増やさない。
`home/config.toml` の `[voice] engine = "voicevox"` が無ければ「未設定」として振る舞い、
`speak()` は何もせず `False` を返すだけ（VOICEVOX が無くても manor は動く、という性質を壊さない）。

移す価値があるのは声そのものではなく、v1 が事故から学んだ**機構**（D5 の表）:

- **エンジンの起動・停止**（`engine_start`/`engine_stop`/`engine_status`）: 起動に約22秒。
  喋る直前に上げて、**自分で起こしたときだけ**終わりに落とす（D13）
- **wav キャッシュ**（`home/voice/cache/<sha256(text|speaker)>.wav`）: 同じ文は二度合成しない（D11）
- **決まり文句の作り置き**（`warm`）: 文面は `notify.py` の1箇所（`notify.all_phrases()`）から
  取る。ここでは表を持たない（D11）
- **消音の印はディスク**（`home/voice/state.json`）: v1 の事故（2026-09-02）——消音の状態を
  プロセス内に持っていて、夜勤がプロセスごと消えて丸一日黙った。印はディスク、戻す口は
  `restore()` の1本だけ（D9）

発話の門は `speak()` の1つだけ（D8）。`notify.py` はこれを先に試し、`[voice]` が未設定の
ときだけ従来の `speak_command`／OS 既定へ落ちる——**どちらの経路でも `is_muted()` を尊重する**
（「消したのに喋った」を作らないため。判定は `voice` の中に1箇所だけ）。

**ADR-011 D6（リップシンク）**: 合成のとき `audio_query` の生バイト列から `face_speech.build_cues`
で口の形の予定表を作り、`home/voice/mouth.json` へ公開する（`_publish_mouth`）。id は
単調増加——小窓（`face-mouth.js`）は id が変わったときだけ拾う。**キャッシュに当たったとき
（`audio_query` を呼ばない経路）でも口が動くように**、予定表は `.wav` の隣に `.json` として
一緒に保存しておく（`cache_cues_path`。v1 の作法）。予定表が作れなくても（空の
`accent_phrases` 等）合成・再生そのものは失敗にしない——口パクは付け足しであって、
声が本体（D6「声を出すときだけ動く」の逆に、口が作れないせいで声まで止まってはいけない）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import branding
from . import face_speech
from . import i18n
from . import notify as notify_mod
from . import util

CONFIG_FILE_NAME = "config.toml"
VOICE_DIRNAME = "voice"
STATE_FILE_NAME = "state.json"
CACHE_DIRNAME = "cache"
#: 最新の口の形の予定表（ADR-011 D6）。
MOUTH_FILE_NAME = "mouth.json"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 50021
DEFAULT_SPEAKER = 13

#: HTTP タイムアウト（秒）。ADR-008 §6 の契約どおり。
STATUS_TIMEOUT = 3.0
AUDIO_QUERY_TIMEOUT = 30.0
SYNTHESIS_TIMEOUT = 120.0
#: エンジンの起動待ちの上限（秒）。v1 engine.ps1 と同じ60秒（D13）。
ENGINE_START_TIMEOUT = 60

_DEFAULT_STATE: dict[str, object] = {"muted": False, "muted_by_night": False}


# --- 設定（home/config.toml の [voice]。D7） -----------------------------------------------


def _load_voice_config(home: Path) -> dict[str, object]:
    """`home/config.toml` の `[voice]` 節。無い・壊れているときは空辞書
    （＝未設定として振る舞う。`notify._load_notify_config` と同じ流儀）。"""
    path = Path(home) / CONFIG_FILE_NAME
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except Exception:
        return {}
    section = data.get("voice") if isinstance(data, dict) else None
    return section if isinstance(section, dict) else {}


def is_configured(cfg: dict[str, object]) -> bool:
    """`engine` が `"voicevox"`（大小文字は問わない）でなければ未設定（D7）。"""
    engine = cfg.get("engine")
    return isinstance(engine, str) and engine.strip().lower() == "voicevox"


def is_voice_configured(home: Path) -> bool:
    """`home` から読んで判定する版（`notify.py` から呼ぶための公開口）。"""
    return is_configured(_load_voice_config(home))


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _default_engine_path() -> Path:
    """既定のエンジンの場所。**確実なのは Windows だけ**（公式インストーラの既定値）。
    macOS/Linux はベストエフォート（ADR-009 D9。`extensions/voicevox.py` の `detect()` が
    ここが外れてもポート応答へフォールバックするので、外れても実害は無い）:

    - Windows: `%LOCALAPPDATA%/Programs/VOICEVOX/vv-engine/run.exe`（公式インストーラの既定値）
    - macOS: `/Applications/VOICEVOX.app/Contents/Resources/vv-engine/run`
      （公式配布は `.app` バンドルで、エンジンはこの中に同梱されている）
    - Linux: `~/voicevox/vv-engine/run`（公式配布は zip 展開のみで決まったインストール先が
      無い。ホーム直下に展開する利用者を想定した推測でしかない）
    """
    if sys.platform == "darwin":
        return Path("/Applications/VOICEVOX.app/Contents/Resources/vv-engine/run")
    if sys.platform.startswith("win"):
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base / "Programs" / "VOICEVOX" / "vv-engine" / "run.exe"
    return Path.home() / "voicevox" / "vv-engine" / "run"


@dataclass(frozen=True)
class VoiceConfig:
    host: str
    port: int
    speaker: int
    engine_path: Path


def load_config(home: Path) -> VoiceConfig | None:
    """`[voice]` が未設定なら `None`。設定されていれば既定値で埋めて返す。"""
    cfg = _load_voice_config(home)
    if not is_configured(cfg):
        return None
    host = cfg.get("host")
    host = host if isinstance(host, str) and host.strip() else DEFAULT_HOST
    port = _as_int(cfg.get("port"), DEFAULT_PORT)
    speaker = _as_int(cfg.get("speaker"), DEFAULT_SPEAKER)
    engine_path_raw = cfg.get("engine_path")
    if isinstance(engine_path_raw, str) and engine_path_raw.strip():
        engine_path = Path(engine_path_raw)
    else:
        engine_path = _default_engine_path()
    return VoiceConfig(host=host, port=port, speaker=speaker, engine_path=engine_path)


def _base_url(cfg: VoiceConfig) -> str:
    return f"http://{cfg.host}:{cfg.port}"


# --- 担当ごとの話者（ADR-011 D9） -----------------------------------------------------------


def _speaker_for(home: Path, cfg: VoiceConfig, agent: str | None) -> int:
    """解決の順は **`[voice.speakers].<agent>` → `[voice] speaker`**（D9）。`agent` が
    無い・語彙外・値が読めない（数へ変換できない）ときは黙って既定（`cfg.speaker`）へ
    倒す——声を出す経路を1つも止めない（`speak` は例外を投げない、の一部）。
    """
    if not agent:
        return cfg.speaker
    speakers = _load_voice_config(home).get("speakers")
    if not isinstance(speakers, dict):
        return cfg.speaker
    value = speakers.get(agent)
    if value is None:
        return cfg.speaker
    return _as_int(value, cfg.speaker)


# --- 置き場（home/voice/。②git 管理外） ------------------------------------------------------


def voice_dir(home: Path) -> Path:
    return Path(home) / VOICE_DIRNAME


def state_path(home: Path) -> Path:
    return voice_dir(home) / STATE_FILE_NAME


def cache_dir(home: Path) -> Path:
    return voice_dir(home) / CACHE_DIRNAME


def cache_path(home: Path, text: str, speaker: int) -> Path:
    """`home/voice/cache/<sha256(text + "|" + speaker)>.wav`（D11）。
    話者IDが鍵に入るため、`speaker` を変えるとキャッシュは別物になる（作り直しが要る）。

    **ADR-011 D9（担当ごとの声）はここを一切変えない。** 担当ごとに声が違っても、鍵に
    入るのは（担当名ではなく）解決済みの話者ID——同じ話者を指す担当同士は最初から
    同じキャッシュを共有し、違う話者を指す担当同士は最初から別物になる。作り直しは要らない。
    """
    key = f"{text}|{speaker}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return cache_dir(home) / f"{digest}.wav"


def cache_cues_path(home: Path, text: str, speaker: int) -> Path:
    """`cache_path` と同じ鍵の `.json`（口の形の予定表。ADR-011 D6）。**`.wav` と1対1**——
    キャッシュに当たって `audio_query` を呼ばない経路でも、ここを読めば口を動かせる。
    """
    return cache_path(home, text, speaker).with_suffix(".json")


def mouth_path(home: Path) -> Path:
    """最新の口の形の予定表（ADR-011 D6）。"""
    return voice_dir(home) / MOUTH_FILE_NAME


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """一時ファイル + `os.replace`。**`write_bytes` を使う**——`write_text` は Windows で
    改行を `\\r\\n` に変換してしまうため（`render.py` の CRLF の罠と同じ理由）。書き込みに
    失敗しても飲み込む（状態・キャッシュの書き込み失敗で呼び出し元を落とさない）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp_name, path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


# --- 状態（home/voice/state.json。D9） ----------------------------------------------------


def state(home: Path) -> dict[str, object]:
    """`{"muted": bool, "muted_by_night": bool}`。無い・壊れているときは両方 `False`。"""
    path = state_path(home)
    if not path.is_file():
        return dict(_DEFAULT_STATE)
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except Exception:
        return dict(_DEFAULT_STATE)
    if not isinstance(data, dict):
        return dict(_DEFAULT_STATE)
    return {
        "muted": bool(data.get("muted", False)),
        "muted_by_night": bool(data.get("muted_by_night", False)),
    }


def is_muted(home: Path) -> bool:
    return bool(state(home).get("muted", False))


def _write_state(home: Path, data: dict[str, object]) -> dict[str, object]:
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    _atomic_write_bytes(state_path(home), payload)
    return data


def mute(home: Path, *, by_night: bool = False) -> dict[str, object]:
    """`manor voice off`。`muted_by_night` の意味は**「夜勤が黙らせた」**に厳密化してある
    （「夜勤が走っている間ずっと立っている旗」ではない）。

    - `by_night=False`（手動）: 常に `{"muted": True, "muted_by_night": False}` を書く
      （v1 `voice.ps1` と同じ——OR で積み増さない。主人が手で `off` すれば必ず False に戻る）
    - `by_night=True`（夜勤）: **既に手動で消音されているとき（`muted=True` かつ
      `muted_by_night=False`）は `muted_by_night` を立てない**。既に黙っているので夜勤と
      してやることは無く、印を書き換えないことで `restore()` が何もしなくなり、**主人の
      手動消音が夜勤をまたいで残る**（ADR-008 D9 の不変条件——「主人がご自分で消した消音は
      決して戻さない」。夜勤側が `muted_by_night=True` で上書きしてしまうと、夜勤終了時の
      `restore()` が手動消音まで一緒に戻してしまう事故になる）
    """
    if by_night:
        current = state(home)
        if current.get("muted") and not current.get("muted_by_night"):
            return _write_state(home, {"muted": True, "muted_by_night": False})
        return _write_state(home, {"muted": True, "muted_by_night": True})
    return _write_state(home, {"muted": True, "muted_by_night": False})


def unmute(home: Path) -> dict[str, object]:
    """`manor voice on`。主人が自分で戻した——両方 `False` にする。"""
    return _write_state(home, {"muted": False, "muted_by_night": False})


def restore(home: Path) -> bool:
    """`manor voice restore`。**`muted_by_night` が立っているときだけ**戻す（D9）。
    主人が自分で消した消音（`muted_by_night=False`）は決して戻さない——印が無ければ何もしない。

    v1 の事故（2026-09-02）: 消音の状態をプロセス内に持っていて、夜勤がプロセスごと消えて
    丸一日黙った。印はディスク、戻す口はこの1本だけにする（`night/runner.py` が開始・終了の
    両方でこの関数を呼ぶ。ADR-008 D10）。

    戻り値: 戻したら `True`、何もしなければ `False`。
    """
    if not state(home).get("muted_by_night"):
        return False
    unmute(home)
    return True


# --- 口の形の予定表（home/voice/mouth.json。ADR-011 D6） ------------------------------------


def _publish_mouth(home: Path, cues: list[dict[str, object]]) -> None:
    """最新の口の形の予定表を `home/voice/mouth.json` へ書く（atomic）。id は単調増加——
    画面（`face-mouth.js`）は id が変わったときだけ拾う。書き込みに失敗しても飲み込む
    （口パクは付け足し。`_write_state` と同じ流儀——失敗で `speak_detail` を落とさない）。
    """
    home = Path(home)
    prev_id = 0
    path = mouth_path(home)
    try:
        if path.is_file():
            prev = json.loads(path.read_bytes().decode("utf-8"))
            if isinstance(prev, dict) and isinstance(prev.get("id"), int):
                prev_id = prev["id"]
    except Exception:
        prev_id = 0
    payload = {"id": prev_id + 1, "started_at": time.time(), "cues": cues}
    try:
        _atomic_write_bytes(path, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    except Exception:
        pass


def latest_mouth(home: Path) -> dict[str, object]:
    """`GET /api/v1/face/mouth` の中身。無い・壊れていれば `{"id": None}`。"""
    path = mouth_path(Path(home))
    if not path.is_file():
        return {"id": None}
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except Exception:
        return {"id": None}
    if not isinstance(data, dict) or not isinstance(data.get("id"), int):
        return {"id": None}
    cues = data.get("cues")
    return {
        "id": data["id"],
        "started_at": data.get("started_at"),
        "cues": cues if isinstance(cues, list) else [],
    }


def _publish_cached_mouth(home: Path, text: str, speaker: int) -> None:
    """キャッシュ命中（`audio_query` を呼ばない経路）でも口が動くように、`.wav` の隣の
    `.json`（`cache_cues_path`）があれば読んで公開する。無ければ何もしない——
    この機能より前に作られたキャッシュには `.json` が無く、そのぶんは動かせない
    （作り直すまでの間だけの制限。声自体は変わらず鳴る）。
    """
    path = cache_cues_path(home, text, speaker)
    if not path.is_file():
        return
    try:
        cues = json.loads(path.read_bytes().decode("utf-8"))
        if isinstance(cues, list):
            _publish_mouth(home, cues)
    except Exception:
        pass


def _build_and_cache_cues(
    home: Path, text: str, speaker: int, query_bytes: bytes
) -> list[dict[str, object]] | None:
    """`audio_query` の生バイト列から口の形の予定表を作り、`.wav` の隣に保存する。
    失敗しても `None` を返すだけ（例外を上げない。口パクは付け足し）。
    """
    try:
        query = json.loads(query_bytes.decode("utf-8"))
        cues = face_speech.build_cues(query)
    except Exception:
        return None
    try:
        _atomic_write_bytes(
            cache_cues_path(home, text, speaker),
            json.dumps(cues, ensure_ascii=False).encode("utf-8"),
        )
    except Exception:
        pass
    return cues


# --- HTTP（urllib のみ。依存を増やさない。D7） ----------------------------------------------


def _http_get(url: str, timeout: float) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - 127.0.0.1 のローカルエンジンのみ
            return resp.read()
    except Exception:
        return None


def _http_post(
    url: str, data: bytes | None, timeout: float, headers: dict[str, str] | None = None
) -> bytes | None:
    try:
        req = urllib.request.Request(
            url, data=data if data is not None else b"", method="POST", headers=headers or {}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.read()
    except Exception:
        return None


# --- エンジンの生死は道具が面倒を見る（D13） -------------------------------------------------


def engine_status(home: Path) -> dict[str, object]:
    """エンジンの生死。`GET /version` で確かめる（v1 `engine.ps1` の Test-EngineReady と同じ）。"""
    cfg = load_config(home)
    if cfg is None:
        return {"configured": False, "running": False}
    body = _http_get(f"{_base_url(cfg)}/version", timeout=STATUS_TIMEOUT)
    if body is None:
        return {"configured": True, "running": False, "host": cfg.host, "port": cfg.port}
    try:
        version: object = json.loads(body.decode("utf-8"))
    except Exception:
        version = body.decode("utf-8", errors="replace")
    return {
        "configured": True,
        "running": True,
        "version": version,
        "host": cfg.host,
        "port": cfg.port,
    }


def engine_start(home: Path, *, wait_seconds: int = ENGINE_START_TIMEOUT) -> dict[str, object]:
    """既に動いていれば何もしない。起動待ちは最大60秒。**例外は投げない**——
    見つからなければ1行の理由を添えて `ok=False` を返すだけ（D13）。
    """
    cfg = load_config(home)
    if cfg is None:
        return {"ok": False, "reason": '未設定です（[voice] engine が "voicevox" ではありません）'}
    if engine_status(home).get("running"):
        return {"ok": True, "reason": "すでに動いています", "already_running": True}
    if not cfg.engine_path.is_file():
        return {
            "ok": False,
            "reason": f"エンジンが見つかりません: {cfg.engine_path}",
            "already_running": False,
        }

    popen_kwargs: dict[str, object] = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform.startswith("win"):
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(  # noqa: S603 - engine_path は home/config.toml の主人自身の設定
            [str(cfg.engine_path), "--host", cfg.host, "--port", str(cfg.port)], **popen_kwargs
        )
    except OSError as exc:
        return {"ok": False, "reason": f"起動できませんでした: {exc}", "already_running": False}

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if engine_status(home).get("running"):
            return {"ok": True, "reason": "起動しました", "already_running": False}
        time.sleep(1)
    return {
        "ok": False,
        "reason": f"{wait_seconds}秒待っても応答がありませんでした",
        "already_running": False,
    }


def _find_pid_by_port(port: int) -> int | None:
    """ポートを listen している PID（Windows のみ。best-effort。他 OS は探索しない）。"""
    if not sys.platform.startswith("win"):
        return None
    try:
        proc = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        if "LISTEN" not in parts[3].upper():
            continue
        if not parts[1].endswith(f":{port}"):
            continue
        try:
            return int(parts[-1])
        except ValueError:
            continue
    return None


def _kill_pid(pid: int) -> bool:
    if sys.platform.startswith("win"):
        try:
            proc = subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"], capture_output=True, text=True, timeout=15
            )
            return proc.returncode == 0
        except Exception:
            return False
    try:
        os.kill(pid, 15)  # SIGTERM
        return True
    except Exception:
        return False


def engine_stop(home: Path) -> dict[str, object]:
    """動いていれば止める。動いていなければ何もしない。例外は投げない。"""
    cfg = load_config(home)
    if cfg is None:
        return {
            "ok": False,
            "reason": '未設定です（[voice] engine が "voicevox" ではありません）',
            "stopped": False,
        }
    pid = _find_pid_by_port(cfg.port)
    if pid is None:
        return {"ok": True, "reason": "エンジンは動いていません", "stopped": False}
    if _kill_pid(pid):
        return {"ok": True, "reason": f"停止しました（PID {pid}）", "stopped": True, "pid": pid}
    return {"ok": False, "reason": f"停止できませんでした（PID {pid}）", "stopped": False, "pid": pid}


# --- 合成（audio_query → synthesis） --------------------------------------------------------


def _audio_query(cfg: VoiceConfig, text: str, speaker: int) -> bytes | None:
    qs = urllib.parse.urlencode({"speaker": speaker, "text": text})
    return _http_post(f"{_base_url(cfg)}/audio_query?{qs}", None, timeout=AUDIO_QUERY_TIMEOUT)


def _synthesis(cfg: VoiceConfig, query_bytes: bytes, speaker: int) -> bytes | None:
    qs = urllib.parse.urlencode({"speaker": speaker})
    return _http_post(
        f"{_base_url(cfg)}/synthesis?{qs}",
        query_bytes,
        timeout=SYNTHESIS_TIMEOUT,
        headers={"Content-Type": "application/json"},
    )


def _ensure_cached(home: Path, cfg: VoiceConfig, text: str, speaker: int) -> dict[str, object]:
    """キャッシュに無ければ合成して保存する。**エンジンの起動・停止はしない**——
    呼び出し側（`speak_detail` は1回ごと、`warm` はまとめて1回）が管理する（D11）。
    `speaker` は呼び出し側が解決済み（`_speaker_for`。D9）のものを渡す——ここでは
    `cfg.speaker`（既定）を直接見ない。

    ADR-011 D6: 新規に合成したときは口の形の予定表も作り、`.wav` の隣へ `.json` として
    一緒に保存する（`_build_and_cache_cues`）。戻り値の `cues` は公開できたときだけ
    リスト、それ以外（キャッシュ命中・予定表を作れなかった）は `None`。
    """
    wav_path = cache_path(home, text, speaker)
    if wav_path.is_file():
        return {"ok": True, "cached": True, "reason": "", "wav": wav_path, "cues": None}
    query = _audio_query(cfg, text, speaker)
    if query is None:
        return {
            "ok": False, "cached": False, "reason": "audio_query に失敗しました",
            "wav": None, "cues": None,
        }
    wav_bytes = _synthesis(cfg, query, speaker)
    if wav_bytes is None:
        return {
            "ok": False, "cached": False, "reason": "synthesis に失敗しました",
            "wav": None, "cues": None,
        }
    _atomic_write_bytes(wav_path, wav_bytes)
    if not wav_path.is_file():
        return {
            "ok": False, "cached": False, "reason": "wav を保存できませんでした",
            "wav": None, "cues": None,
        }
    cues = _build_and_cache_cues(home, text, speaker, query)
    return {"ok": True, "cached": False, "reason": "", "wav": wav_path, "cues": cues}


# --- 再生 --------------------------------------------------------------------------------


def _play(path: Path) -> bool:
    """Windows: PowerShell の `Media.SoundPlayer` で同期再生。macOS: `afplay`。それ以外は
    未対応で `False`。`notify._speak` と同じく argv を組み立てて `shell=False` で呼ぶ
    （シェルを介さないので注入の余地が無い）。
    """
    if sys.platform.startswith("win"):
        escaped = str(path).replace("'", "''")
        script = f"(New-Object Media.SoundPlayer '{escaped}').PlaySync()"
        argv = ["powershell", "-NoProfile", "-Command", script]
    elif sys.platform == "darwin":
        argv = ["afplay", str(path)]
    else:
        return False
    try:
        result = subprocess.run(argv, shell=False, timeout=120, capture_output=True)
    except Exception:
        return False
    return result.returncode == 0


# --- speak() — 発話の門は1つだけ（D8） --------------------------------------------------------


def speak(home: Path, text: str, *, agent: str | None = None) -> bool:
    """発話の門。消音中・未設定なら鳴らさず `False`。**例外は投げない**。
    `agent` を渡すと `[voice.speakers].<agent>` を優先する（D9。無ければ既定）。
    理由が欲しい呼び出し元（CLI の `say`）は `speak_detail()` を使う。
    """
    return bool(speak_detail(home, text, agent=agent)["ok"])


def speak_detail(home: Path, text: str, *, agent: str | None = None) -> dict[str, object]:
    """`speak()` の詳細版。戻り値: `ok` / `reason`（1行。空文字は成功） / `cached` / `wav`。
    `agent` の解決は `_speaker_for`（D9）——語彙外・値が読めない担当は既定へ倒す。
    """
    home = Path(home)
    try:
        if is_muted(home):
            return {"ok": False, "reason": "消音中です", "cached": False, "wav": None}
        cfg = load_config(home)
        if cfg is None:
            return {
                "ok": False,
                "reason": '未設定です（home/config.toml の [voice] engine = "voicevox" がありません）',
                "cached": False,
                "wav": None,
            }
        speaker = _speaker_for(home, cfg, agent)

        wav_path = cache_path(home, text, speaker)
        if wav_path.is_file():
            # キャッシュに当たれば**エンジンには一切触れない**（D11）。
            # ADR-011 D6: 予定表は `.wav` の隣の `.json` から読む（`audio_query` を呼ばない
            # 経路なので、ここでしか公開できない）。無ければ黙って諦める——口パクは
            # 付け足しであって、無くても再生は止めない。
            _publish_cached_mouth(home, text, speaker)
            played = _play(wav_path)
            return {
                "ok": played,
                "reason": "" if played else "再生できませんでした",
                "cached": True,
                "wav": str(wav_path),
            }

        started_by_us = False
        if not engine_status(home).get("running"):
            start_result = engine_start(home)
            if not start_result.get("ok"):
                return {
                    "ok": False,
                    "reason": str(start_result.get("reason", "エンジンを起動できませんでした")),
                    "cached": False,
                    "wav": None,
                }
            started_by_us = not bool(start_result.get("already_running", False))
        try:
            outcome = _ensure_cached(home, cfg, text, speaker)
        finally:
            if started_by_us:
                engine_stop(home)  # 自分で起こしたときだけ落とす（D13）

        if not outcome["ok"]:
            return {"ok": False, "reason": str(outcome["reason"]), "cached": False, "wav": None}
        # ADR-011 D6: 再生の直前に公開する——小窓のポーリングと再生開始がほぼ揃う。
        cues = outcome.get("cues")
        if isinstance(cues, list):
            _publish_mouth(home, cues)
        played = _play(Path(str(outcome["wav"])))
        return {
            "ok": played,
            "reason": "" if played else "再生できませんでした",
            "cached": False,
            "wav": str(outcome["wav"]),
        }
    except Exception as exc:  # noqa: BLE001 - 声は落ちてよいが、呼び出し元は落とさない
        return {"ok": False, "reason": f"予期しないエラー: {exc}", "cached": False, "wav": None}


# --- warm() — 決まり文句の作り置き（D11） ----------------------------------------------------


def _all_configured_speakers(home: Path, cfg: VoiceConfig) -> set[int]:
    """既定＋全担当の解決済み話者IDの集合（重複は自然に潰れる。ADR-011 D9）。
    `agent_meta` は `.claude/agents/*.md` を読むので、遅延 import で循環を避ける
    （`agent_meta` → `talk` は問題無いが、依存の向きをここで増やさないための作法）。
    """
    from . import agent_meta as agent_meta_mod

    speakers = {cfg.speaker}
    for agent in agent_meta_mod.valid_agents():
        speakers.add(_speaker_for(home, cfg, agent))
    return speakers


def warm(home: Path, *, agent: str | None = None) -> dict[str, object]:
    """`notify` が喋りうる文をまとめて作り置きする。文面は `notify.all_phrases()` から取る
    ——**文面の出どころは1箇所**（v1 は `phrases.txt` と実装の二重管理でずれてキャッシュを
    外していた）。エンジンの起動は1回だけ。**再生はしない**（v1 `warm.ps1` が `speak.ps1` を
    `-DryRun -KeepEngine` で呼ぶのと同じ考え方）。

    ADR-011 D9・D11: `agent` を指定すればその担当の声だけ。**省略時は全担当ぶん**——
    `[voice.speakers]` に個別の上書きが無い担当は既定と同じ話者に解決されるので、
    `set` に潰れて重複合成にはならない（初回起動でまとめて作り置きできるように）。
    """
    home = Path(home)
    phrases = notify_mod.all_phrases()
    result: dict[str, object] = {
        "total": len(phrases),
        "done": 0,
        "cached": 0,
        "failed": 0,
        "notes": [],
    }
    cfg = load_config(home)
    if cfg is None:
        result["notes"].append(  # type: ignore[union-attr]
            '未設定のため作り置きできません（[voice] engine が "voicevox" ではありません）'
        )
        return result

    if agent is not None:
        speakers = {_speaker_for(home, cfg, agent)}
    else:
        speakers = _all_configured_speakers(home, cfg)
    result["speakers"] = sorted(speakers)

    started_by_us = False
    if not engine_status(home).get("running"):
        start_result = engine_start(home)
        if not start_result.get("ok"):
            result["notes"].append(  # type: ignore[union-attr]
                str(start_result.get("reason", "エンジンを起動できませんでした"))
            )
            result["failed"] = len(phrases) * len(speakers)
            return result
        started_by_us = not bool(start_result.get("already_running", False))
    try:
        for speaker in speakers:
            for text in phrases:
                outcome = _ensure_cached(home, cfg, text, speaker)
                if not outcome["ok"]:
                    result["failed"] = int(result["failed"]) + 1  # type: ignore[arg-type]
                    result["notes"].append(  # type: ignore[union-attr]
                        f"{text}（speaker={speaker}）: {outcome['reason']}"
                    )
                elif outcome["cached"]:
                    result["cached"] = int(result["cached"]) + 1  # type: ignore[arg-type]
                else:
                    result["done"] = int(result["done"]) + 1  # type: ignore[arg-type]
    finally:
        if started_by_us:
            engine_stop(home)
    return result


# --- CLI（`manor voice ...`。DB には触れない = needs_db=False） -------------------------------


def _print_json(obj: object) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _cmd_status(args: argparse.Namespace) -> int:
    home = util.manor_home()
    cfg = _load_voice_config(home)
    configured = is_configured(cfg)
    st = state(home)
    result: dict[str, object] = {"configured": configured, **st}
    if configured:
        result["engine"] = engine_status(home)
    if args.json:
        _print_json(result)
        return 0
    if not configured:
        print(i18n.t("voice.status.not_configured"))
    else:
        eng = result["engine"]
        assert isinstance(eng, dict)
        running = i18n.t("voice.status.engine_running") if eng.get("running") else i18n.t("voice.status.engine_stopped")
        print(i18n.t("voice.status.engine_line", host=eng.get("host"), port=eng.get("port"), running=running))
    muted_label = i18n.t("voice.status.muted_label") if st["muted"] else i18n.t("voice.status.speaking_label")
    tail = i18n.t("voice.status.muted_by_night_tail") if st["muted_by_night"] else ""
    print(i18n.t("voice.status.state_line", label=muted_label, tail=tail))
    return 0


def _cmd_on(args: argparse.Namespace) -> int:
    home = util.manor_home()
    result = unmute(home)
    if args.json:
        _print_json(result)
        return 0
    print(i18n.t("voice.on.done"))
    return 0


def _cmd_off(args: argparse.Namespace) -> int:
    home = util.manor_home()
    result = mute(home, by_night=bool(args.by_night))
    if args.json:
        _print_json(result)
        return 0
    print(i18n.t("voice.off.done"))
    return 0


def _cmd_restore(args: argparse.Namespace) -> int:
    home = util.manor_home()
    restored = restore(home)
    if args.json:
        _print_json({"restored": restored})
        return 0
    if restored:
        print(i18n.t("voice.restore.done"))
    else:
        print(i18n.t("voice.restore.nothing"))
    return 0


def _cmd_say(args: argparse.Namespace) -> int:
    home = util.manor_home()
    agent = getattr(args, "agent", None)
    if args.dry_run:
        cfg = _load_voice_config(home)
        configured = is_configured(cfg)
        muted = is_muted(home)
        result: dict[str, object] = {
            "would_speak": configured and not muted,
            "configured": configured,
            "muted": muted,
            "text": args.text,
            "agent": agent,
        }
        if args.json:
            _print_json(result)
            return 0
        if not configured:
            print(i18n.t("voice.say.dry_run.not_configured"))
        elif muted:
            print(i18n.t("voice.say.dry_run.muted"))
        else:
            print(i18n.t("voice.say.dry_run.would_speak", text=args.text))
        return 0

    detail = speak_detail(home, args.text, agent=agent)
    if args.json:
        _print_json(detail)
        return 0
    if detail["ok"]:
        print(i18n.t("voice.say.done"))
    else:
        print(i18n.t("voice.say.failed", reason=detail["reason"]))
    return 0


def _cmd_warm(args: argparse.Namespace) -> int:
    home = util.manor_home()
    result = warm(home, agent=getattr(args, "agent", None))
    if args.json:
        _print_json(result)
        return 0
    print(i18n.t("voice.warm.summary", done=result["done"], cached=result["cached"], failed=result["failed"]))
    for note in result["notes"]:  # type: ignore[union-attr]
        print(note)
    return 0


def _cmd_engine_start(args: argparse.Namespace) -> int:
    home = util.manor_home()
    result = engine_start(home)
    if args.json:
        _print_json(result)
        return 0 if result.get("ok") else 1
    print(result.get("reason", ""))
    return 0 if result.get("ok") else 1


def _cmd_engine_stop(args: argparse.Namespace) -> int:
    home = util.manor_home()
    result = engine_stop(home)
    if args.json:
        _print_json(result)
        return 0 if result.get("ok") else 1
    print(result.get("reason", ""))
    return 0 if result.get("ok") else 1


def _cmd_engine_status(args: argparse.Namespace) -> int:
    home = util.manor_home()
    result = engine_status(home)
    if args.json:
        _print_json(result)
        return 0
    if not result.get("configured"):
        print(i18n.t("voice.engine.not_configured"))
    elif result.get("running"):
        print(i18n.t("voice.engine.running", host=result.get("host"), port=result.get("port")))
    else:
        print(i18n.t("voice.engine.stopped"))
    return 0


def register(subparsers: "argparse._SubParsersAction") -> None:
    """`manor voice ...` を足す（ADR-008 第2期）。DB には触れない（`needs_db=False`）。"""
    p = subparsers.add_parser("voice", help=i18n.t("cli.voice.help", app_name=branding.APP_NAME))
    sub = p.add_subparsers(dest="verb")

    s = sub.add_parser("status", help=i18n.t("cli.voice.status.help"))
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=_cmd_status, needs_db=False)

    s = sub.add_parser("on", help=i18n.t("cli.voice.on.help"))
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=_cmd_on, needs_db=False)

    s = sub.add_parser("off", help=i18n.t("cli.voice.off.help"))
    s.add_argument(
        "--by-night", action="store_true", dest="by_night", help=i18n.t("cli.voice.off.by_night.help")
    )
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=_cmd_off, needs_db=False)

    s = sub.add_parser("restore", help=i18n.t("cli.voice.restore.help"))
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=_cmd_restore, needs_db=False)

    s = sub.add_parser("say", help=i18n.t("cli.voice.say.help"))
    s.add_argument("text")
    s.add_argument(
        "--agent", dest="agent", default=None,
        help=i18n.t("cli.voice.say.agent.help"),
    )
    s.add_argument(
        "--dry-run", action="store_true", dest="dry_run", help=i18n.t("cli.voice.say.dry_run.help")
    )
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=_cmd_say, needs_db=False)

    s = sub.add_parser("warm", help=i18n.t("cli.voice.warm.help"))
    s.add_argument(
        "--agent", dest="agent", default=None,
        help=i18n.t("cli.voice.warm.agent.help"),
    )
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=_cmd_warm, needs_db=False)

    engine_p = sub.add_parser("engine", help=i18n.t("cli.voice.engine.help"))
    engine_sub = engine_p.add_subparsers(dest="engine_verb")

    e = engine_sub.add_parser("start")
    e.add_argument("--json", action="store_true")
    e.set_defaults(func=_cmd_engine_start, needs_db=False)

    e = engine_sub.add_parser("stop")
    e.add_argument("--json", action="store_true")
    e.set_defaults(func=_cmd_engine_stop, needs_db=False)

    e = engine_sub.add_parser("status")
    e.add_argument("--json", action="store_true")
    e.set_defaults(func=_cmd_engine_status, needs_db=False)
