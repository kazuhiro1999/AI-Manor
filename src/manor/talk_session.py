"""通話（小窓で話しかける）— `manor talk-session`（ADR-008 §8 D17〜D23。ROADMAP 5j）。

**2026-09-04・主人の訂正で設計が変わった。** 初版は v1 `apps/butler-board/src/butler_board/
face_talk.py`（雑談と調べ物専用・道具を持たせない・ワークスペースの外で起動）をそのまま
移植したが、主人の実際の使い方は「わざわざ Claude アプリを開くほどでもない用事（タスク
追加・予定登録・在庫確認…）を、家族のグループラインのようにこの窓から頼みたい」だった。

> 通話機能は雑談といいましたが、わざわざ Claude アプリを開いて AI執事に依頼するほどでも
> ない件も頼めるようにしたい。例えばタスクや予定の追加、タスク進捗のアップデート、在庫
> 確認など、通常の日常的に使う場合もこの窓からできるように想定しています（家族の
> グループラインみたいな）。そこまでツール使用に制限をかけなくて大丈夫で、カレンダー
> とかは使えて問題ありません。

**この窓は隔離された雑談の口ではなく、小さな執事のセッションである**（ADR D17 改訂・D21）。

## 変わらないこと（D22）

* **先に数える**（D17）。呼ぶ前に往復数を1つ進める。失敗して1回ぶん損するほうが、
  数え損ねて枠（夜勤・関門と同じ財布）を食い潰すより害が小さい
* **打ち止め・時間外は黙らない。失敗は声に出さない**（D17）。前者は理由を画面にも声にも
  出す。後者（`claude` が落ちた・時間切れ）は画面にだけ出し、故障を音声で伝えない
* **会話の中身は残さない**。往復のテキストは `home/` のどこにも書かない。
  `home/talk/usage.json` に残るのは日付と回数だけ、`run` 表に残るのは費用・所要・
  終了理由だけ（`runlog`）——**ただし仕事の結果は DB に残る**（タスクが増えるのは
  記録であって会話ではない。D22）
* **ボタンを押した時点でエンジンを温め、窓を閉じたら自分で起こしたときだけ落とす**（D20）。
  `voice.py`（ADR-008 D7〜D13）の機構をそのまま使う——ここで起動・停止を作り直さない

## 変わったこと（D21・D23）

* **manor のワークスペースで起こす**（`cwd` はリポジトリのルート。`%TEMP%` へ隔離するのを
  やめた）。`CLAUDE.md`・`butler/policy.toml`・`.claude/settings.json` の hooks がそのまま
  効く——**既にある機構（状態機械・HG の門・クラス→level・射影の編集拒否）が全部当たる**
  のが安全の要。`MANOR_HOME` は環境変数で明示し、子プロセスが正しい `home/` を見るように
  する（`talk.py` `run()` と同じ作法）。venv の `Scripts` を PATH の先頭に足す
  ——nested session の Bash から `manor ...`（`uv run` を介さない裸のコマンド。CLAUDE.md
  自身がこの形も使う）が解決するように（`tests/behavior/run.py` と同じ実測済みの対策）
* **`--permission-mode manual` ＋ `--allowed-tools` で事前承認する**（2026-09-04 実機検分）。
  `claude -p` は非対話なので、**事前承認の無い道具は許可待ちのまま拒否される**——
  `--disallowed-tools` を削るだけでは足りない（実測: `--permission-mode dontAsk` のまま
  `Bash` を許可一覧から外した状態で `manor task add` を頼んだところ「許可設定により
  ブロックされています」と拒否された）。`tests/behavior/run.py`（51〜66行・195〜206行）と
  **同じ実績のある形**——`--allowed-tools` にパターンで列挙し、`--disallowed-tools` は
  最後の網として残す
* **Bash は `manor` のコマンドに限る**（下の `ALLOWED_TOOLS`）。窓から欲しいのは
  「タスク・予定の追加、進捗の更新、在庫確認」で、それは全部 `manor …` で足りる。任意の
  シェルまで開ける必要はない——**主人が「Claude アプリを開くほどでもない件」と線を引いた
  側の窓**なので、道具の面も同じ線で引く。もっと自由が要る用件は、主人が本体のセッション
  （`manor talk <agent>` / `claude` そのもの）を開けばよい
* **`mcp__*` は通す**（カレンダー等。主人の明示）。`Read`/`Glob`/`Grep` は文脈を読むために
  通す。**`Write`/`Edit` は入れない**——射影も規則も、窓から書き換える用事は無い
  （`manor …` を通せば DB に入る。射影は hook が別途拒む）
* **`--strict-mcp-config` はやめた**——MCP（Google カレンダー等）も使ってよい（主人の明示）
* `ALLOWED_TOOLS`/`--disallowed-tools` の外にある道具（`Write`/`Edit`/`WebFetch`/
  `WebSearch`/`Skill` 等）は `--permission-mode manual` のもとでは事前承認が無いので
  使えない（非対話なので許可待ちのまま拒否される。上と同じ理由）
* **系統プロンプトは時刻と時間帯だけ**（旧 D19「今だけを渡す」は D22 で撤回）。ワークスペース
  で起こすので、執事は `CLAUDE.md` の指示どおり自分で `manor active` を読む——人格も
  要対応件数も予定も、ここでは持たせない
* **費用は上がる**（D23）。`CLAUDE.md` 等を読むぶん1往復が高くなる（v1 実測で約2.6倍）。
  それでも窓から仕事が頼めることの価値が上回るという主人の判断。1日の往復上限がそのまま
  予算の蓋になる

## `%` を argv に載せると環境変数が漏れる（Windows の `claude.CMD`）

2026-09-04 実測: Windows の `claude` は `claude.CMD`（バッチのシム）で、`subprocess.run`
で渡した argv は `cmd.exe` に再解釈される。`--append-system-prompt` に ``%USERNAME%`` を
含めて渡したところ、**`claude` に届く前に `cmd.exe` が展開し、このPCのユーザー名
がそのまま中身に混ざった**（v1 face_talk.py `_safe_summary`/`check_argv` のコメントが
警告していた罠を、こちらでも実測で踏んだ）。いま系統プロンプトに載るのは時刻・時間帯
だけで混入経路は無いが、**この対策（`_safe_for_argv`／`_check_argv`）は残す**——argv に
文字列を載せる形である限り、将来また混入経路が増えても効く安全網（主人の指示）。

## 道具一覧を実行時に CLI へ聞く仕組みは廃止

前版はここに「静的な拒否一覧を `claude --version` ごとに引き直すプローブ機構」を持って
いたが、**設計変更（道具を塞がない方針）でその機構自体が不要になった**ため丸ごと削除した
（`home/talk/tool-cache.json` は書かない）。
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import tomllib
from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any, Sequence

from . import db as db_mod
from . import i18n
from . import runlog
from . import util
from . import voice

CONFIG_FILE_NAME = "config.toml"
TALK_DIRNAME = "talk"
USAGE_FILE_NAME = "usage.json"

DEFAULT_LIMIT = 20                  # 1日の往復数（D17）
DEFAULT_LOCK = "01:00-07:30"        # 押せない時間帯（夜勤の枠。D17）
DEFAULT_MODEL = "sonnet"            # night/runner.py・slack.py と同じ既定
#: ワークスペース内で実作業（タスク追加・DB書き込み等）をするぶん、旧来の「雑談専用90秒」
#: より余裕を持たせる（D23 の費用容認と同じ判断）。
DEFAULT_TIMEOUT_SEC = 180.0
#: `tests/behavior/run.py` の実作業シナリオと同じ上限（道具を使って実際に手を動かす想定）。
DEFAULT_MAX_TURNS = 15

RUN_KIND = "talk"                   # runlog.VALID_KINDS に既にある語彙

#: 声に出す決まり文句。`apps/butler-face` 相当の文面と揃えておく（画面側が同じ文言を表示する）。
OUT_OF_BUDGET = "本日は打ち止めです。"
NIGHT_LOCKED = "夜のあいだは休ませていただきます。"
DISABLED_MESSAGE = "通話は現在ご利用いただけません（設定で停止中です）。"
EMPTY_TEXT_MESSAGE = "うまく聞き取れませんでした。"

_HELLO_BY_TOD: dict[str, str] = {
    "morning": "おはようございます。",   # 05:00-10:59
    "day": "こんにちは。",               # 11:00-17:59
    "evening": "こんばんは。",           # 18:00-04:59
}
_ASKS: tuple[str, ...] = (
    "何かご用でしょうか。",
    "いかがなさいましたか。",
    "お呼びでしょうか。",
    "ご用命を承ります。",
)

WEEKDAYS: tuple[str, ...] = ("月", "火", "水", "木", "金", "土", "日")

#: いまの時間帯（系統プロンプトの「今」用。D22）。**分は持たせない**（下の `_now_line` 参照）。
TIME_BANDS: tuple[tuple[int, str], ...] = ((5, "朝"), (11, "昼"), (14, "午後"), (18, "夜"), (23, "深夜"))

#: 手元に持つ往復数（`{"role": "user"|"butler", "text": str}` の配列。窓を閉じれば消える
#: ——ここでは古いほうを切り捨てるだけで、どこにも保存しない）。
MAX_HISTORY_MESSAGES = 6

#: **`claude -p` は非対話。事前承認の無い道具は許可待ちのまま拒否される**
#: （2026-09-04 実機検分——`--permission-mode dontAsk` のまま `--disallowed-tools` を
#: 削るだけでは足りず、`manor task add` を頼んでも「許可設定によりブロックされています」
#: と拒否された）。`tests/behavior/run.py`（51〜66行）と**同じ実績のある形**で
#: 事前承認する: **Bash は `manor` のコマンドに限る。** 窓から欲しいのは「タスク・予定の
#: 追加、進捗の更新、在庫確認」で、それは全部 `manor …` で足りる。任意のシェルまで
#: 開ける必要はない——**主人が「Claude アプリを開くほどでもない件」と線を引いた側の窓**
#: なので、道具の面も同じ線で引く。もっと自由が要る用件は、主人が本体のセッション
#: （`manor talk <agent>` / `claude` そのもの）を開けばよい。
#: **`mcp__*` は通す**（カレンダー等。主人の明示）。`Read`/`Glob`/`Grep` は文脈を読むために
#: 通す。**`Write`/`Edit` は入れない**——射影も規則も、窓から書き換える用事は無い
#: （`manor …` を通せば DB に入る。射影は hook が別途拒む）。
ALLOWED_TOOLS: tuple[str, ...] = (
    "Bash(manor:*)",
    "Bash(uv run manor:*)",
    "Bash(uv run --no-sync manor:*)",
    "Read",
    "Glob",
    "Grep",
    "mcp__*",
)

#: **この窓に意味の無い道具だけ**を明示的にも塞ぐ（D21・主人の裁定 2026-09-04）。
#: `--allowed-tools`（上）に無い道具はどのみち非対話では使えないので、実質は二重の網
#: ——`ALLOWED_TOOLS` の書き漏れやパターンの緩みに備える最後の防波堤。
#: 危ないこと自体（外部送信・課金・不可逆な削除）は行動クラス上 HG 固定という既存の政策
#: （`butler/policy.toml`）が止める。ここで塞ぐのはその政策とは別の理由:
#: **7秒で返るはずの窓に、後で勝手に動き出すものを仕込ませない。**
DENY_TOOLS: tuple[str, ...] = (
    "Agent", "Task",                                                              # 下請けの起動
    "CronCreate", "CronDelete", "CronList", "ScheduleWakeup", "RemoteTrigger", "Monitor",  # 常駐・予約
    "SendMessage", "PushNotification",                                            # 別セッションへの発信
)


class TalkError(RuntimeError):
    """返事を作れなかった。理由を主人（画面）に見せるための例外。**声には出さない**。"""


# --- 時計（G1: 時刻・日付に依る歯止めの試験は必ず固定できるようにする） -----------------------


def _now() -> datetime:
    """いまの時刻。`MANOR_NOW`（`util.now()`）があれば固定できる（試験用）。"""
    try:
        return datetime.fromisoformat(util.now())
    except ValueError:
        return datetime.now()


# --- 設定（home/config.toml の [talk]） ----------------------------------------------------


def _load_talk_config(home: Path) -> dict[str, object]:
    """`[talk]` 節。無い・壊れているときは空辞書（`voice._load_voice_config` と同じ流儀）。"""
    path = Path(home) / CONFIG_FILE_NAME
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except Exception:  # noqa: BLE001 - 壊れた TOML は「未設定」として扱う
        return {}
    section = data.get("talk") if isinstance(data, dict) else None
    return section if isinstance(section, dict) else {}


def _cfg_limit(cfg: dict[str, object]) -> int:
    raw = cfg.get("limit")
    if raw is None:
        return DEFAULT_LIMIT
    try:
        return max(0, int(raw))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_LIMIT


def _cfg_lock_raw(cfg: dict[str, object]) -> str:
    raw = cfg.get("lock")
    return raw if isinstance(raw, str) and raw.strip() else DEFAULT_LOCK


def _cfg_model(cfg: dict[str, object]) -> str:
    raw = cfg.get("model")
    return raw.strip() if isinstance(raw, str) and raw.strip() else DEFAULT_MODEL


# --- 時間帯の窓（"HH:MM-HH:MM"。日をまたぐ指定にも耐える） ------------------------------------

_HHMM_RE = re.compile(r"^\s*([01]?\d|2[0-3]):([0-5]\d)\s*$")


def _parse_hhmm(text: str) -> dtime | None:
    m = _HHMM_RE.match(text or "")
    if not m:
        return None
    return dtime(int(m.group(1)), int(m.group(2)))


def _lock_window(raw: str) -> tuple[dtime, dtime] | None:
    """`"01:00-07:30"` の形を読む。`"off"`/空 で外れる。**読めない書式は既定へ倒す**
    ——設定の書き損じで歯止めが消えるほうが困る（v1 `parse_window` と同じ判断）。
    """
    if raw.strip().lower() in {"off", "none", ""}:
        return None
    parts = raw.split("-")
    if len(parts) == 2:
        start, end = _parse_hhmm(parts[0]), _parse_hhmm(parts[1])
        if start and end and start != end:
            return (start, end)
    d_start, d_end = DEFAULT_LOCK.split("-")
    return (_parse_hhmm(d_start), _parse_hhmm(d_end))  # type: ignore[return-value]


def _is_locked(now: datetime, window: tuple[dtime, dtime] | None) -> bool:
    if window is None:
        return False
    start, end = window
    t = now.time()
    if start <= end:
        return start <= t < end
    return t >= start or t < end


# --- 数え方（home/talk/usage.json。{date: count}。voice.py と同じ原子的書き込み） --------------


def talk_dir(home: Path) -> Path:
    return Path(home) / TALK_DIRNAME


def usage_path(home: Path) -> Path:
    return talk_dir(home) / USAGE_FILE_NAME


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """一時ファイル + `os.replace`（`voice._atomic_write_bytes` と同じ。書き込み失敗は飲み込む）。"""
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


def _read_usage_map(home: Path) -> dict[str, int]:
    path = usage_path(home)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except Exception:  # noqa: BLE001 - 壊れたファイルは「記録なし」として扱う
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, int] = {}
    for k, v in data.items():
        try:
            out[str(k)] = max(0, int(v))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    return out


def read_usage(home: Path, day: date) -> int:
    """その日ぶんの往復数。壊れたファイル・記録なしは 0。"""
    return _read_usage_map(home).get(day.isoformat(), 0)


def bump_usage(home: Path, day: date) -> int:
    """1往復ぶん数えて、数えたあとの値を返す（D17「先に数える」の実体）。"""
    data = _read_usage_map(home)
    key = day.isoformat()
    count = data.get(key, 0) + 1
    data[key] = count
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    _atomic_write_bytes(usage_path(home), payload)
    return count


# --- 押せるか（D17。残り回数を隠さない） ------------------------------------------------------


def state(home: Path, *, now: datetime | None = None) -> dict[str, Any]:
    """`{available, limit, used, remaining, reason, lock, message}`。**沈黙しない**
    ——`available=False` のときは必ず `message` に理由が入る。
    """
    home = Path(home)
    now = now or _now()
    cfg = _load_talk_config(home)
    limit = _cfg_limit(cfg)
    window = _lock_window(_cfg_lock_raw(cfg))
    used = read_usage(home, now.date())
    remaining = max(0, limit - used)
    locked = _is_locked(now, window)

    if limit <= 0:
        reason: str | None = "disabled"
    elif locked:
        reason = "night"
    elif remaining <= 0:
        reason = "budget"
    else:
        reason = None

    message = {
        "disabled": DISABLED_MESSAGE,
        "night": NIGHT_LOCKED,
        "budget": OUT_OF_BUDGET,
    }.get(reason or "", "")

    return {
        "available": reason is None,
        "limit": limit,
        "used": used,
        "remaining": remaining,
        "reason": reason,
        "lock": None if window is None else f"{window[0]:%H:%M}-{window[1]:%H:%M}",
        "message": message,
    }


# --- 小窓を開いた／閉じた（D20。LLM は呼ばない） -----------------------------------------------


def _time_of_day(now: datetime) -> str:
    hour = now.hour
    if 5 <= hour < 11:
        return "morning"
    if 11 <= hour < 18:
        return "day"
    return "evening"


def greeting_lines(now: datetime | None = None, rng: random.Random | None = None) -> tuple[str, str]:
    """通話ボタンを押したときの挨拶。**決まり文句を並べるだけ**（LLM は呼ばない）。"""
    now = now or _now()
    return (_HELLO_BY_TOD[_time_of_day(now)], (rng or random).choice(_ASKS))


#: 自分（このプロセス）でエンジンを起こしたかどうか。**起こしたときだけ** `close_session` で
#: 落とす（主人が VOICEVOX エディタを使っているかもしれない。`voice.py` と同じ作法をこちらの
#: 「セッション」単位でも守るための印）。
_engine_started_by_us = False


def _warm_engine_async(home: Path) -> bool:
    """D20: ボタンを押した時点で温める。**待たない**——`voice.engine_start` は起動を
    最大60秒待つ関数なので、そのままだと窓を開く応答が60秒待たされる。バックグラウンドの
    スレッドへ投げるだけで、`voice.engine_start`／`voice.engine_status` 自体は作り直さない。
    """
    global _engine_started_by_us
    if not voice.is_voice_configured(home):
        return False

    def _worker() -> None:
        global _engine_started_by_us
        try:
            if voice.engine_status(home).get("running"):
                return
            result = voice.engine_start(home)
            if result.get("ok") and not result.get("already_running"):
                _engine_started_by_us = True
        except Exception:  # noqa: BLE001 - 声の起動失敗で通話そのものを止めない
            pass

    threading.Thread(target=_worker, daemon=True).start()
    return True


def open_session(home: Path, *, now: datetime | None = None, agent: str | None = None) -> dict[str, Any]:
    """小窓を開いた（通話ボタンを押した）。**決まり文句のみ。LLM は呼ばない。**

    押せる時間帯なら時間帯の挨拶＋ひとこと、押せなければ理由（打ち止め・時間外・無効）を
    そのまま話す——**黙って何も起きないのが一番困る**（D17）。合わせてエンジンを温める（D20）。

    `agent` はその窓の担当（ADR-011 D11）。**挨拶も担当の声で話す**——姿が家政婦なのに
    最初のひとことだけ執事の声、では揃わない（返事だけ担当の声にしていた実装を、執事の
    検分で挨拶まで広げた 2026-09-05）。
    """
    home = Path(home)
    now = now or _now()
    st = state(home, now=now)
    lines: tuple[str, ...] = greeting_lines(now) if st["available"] else (str(st["message"]),)
    text = " ".join(lines)
    spoke = voice.speak(home, text, agent=agent)
    warming = _warm_engine_async(home) if st["available"] else False
    return {**st, "text": text, "lines": list(lines), "spoke": spoke, "warming": warming}


def close_session(home: Path) -> dict[str, Any]:
    """小窓を閉じた。**自分で起こしたエンジンだけ**片付ける（主人が先に起動していたら触らない）。"""
    global _engine_started_by_us
    home = Path(home)
    if not _engine_started_by_us:
        return {"stopped": False}
    _engine_started_by_us = False
    try:
        result = voice.engine_stop(home)
    except Exception:  # noqa: BLE001 - 後片付けの失敗で通話そのものを止めない
        result = {"stopped": False}
    return {"stopped": bool(result.get("stopped", False))}


# --- 文脈は時刻と時間帯だけ（D22。旧 D19 の PERSONA・要対応件数・予定の注入は撤回） --------------


def _time_band(now: datetime) -> str:
    band = TIME_BANDS[-1][1]
    for start, name in TIME_BANDS:
        if now.hour >= start:
            band = name
    return band


def _now_line(now: datetime) -> str:
    """**分は持たせない**（v1 実測「1文字違うだけでキャッシュ全損（$0.00238 → $0.02536。
    10.6倍）」に倣う）。時間帯なら1日に変わるのは境目の5回だけで、20往復のほとんどが
    `--append-system-prompt` の同じ文字列に乗ってキャッシュに当たる。
    """
    return (
        f"いまは{now.year}年{now.month}月{now.day}日"
        f"（{WEEKDAYS[now.weekday()]}曜日）の{now.hour}時ごろ、{_time_band(now)}です。"
        "日付や時間帯を尋ねられたらこれを答える。明日・今週といった言い方も、これを基準にする。"
        "正確な分は持っていないので、何時何分かは分からないと答える。"
    )


def _safe_for_argv(text: str) -> str:
    """argv に載せても崩れない形にする。**要約はしない**——落とすのは Windows の
    `claude.CMD` が食べてしまうものだけ（改行と `%`。空白の連続も畳む）。モジュール
    docstring「`%` を argv に載せると環境変数が漏れる」の実測を踏まえた対策。

    いまの `_system_prompt` は時刻・時間帯（完全に機械生成）しか argv に載せないため
    実際の呼び出し口は無いが、**主人の指示で対策そのものは残してある**——argv に文字列を
    載せる形である限り、将来また混入経路（主人が自由に書ける文字列を系統プロンプトへ
    足す変更）が増えても、ここへ通すだけで効く。
    """
    return " ".join(str(text or "").split()).replace("%", "％")


def _system_prompt(now: datetime) -> str:
    """`--append-system-prompt` へ渡す一式。**時刻と時間帯だけ**（D22）。

    人格・要対応件数・予定は渡さない——ワークスペースで起こすので、執事は `CLAUDE.md` の
    指示どおり自分で `manor active` を読む（旧 D19 の「隔離された雑談の口」という前提が
    D21 で撤回されたため、ここで文脈を絞る理由も無くなった）。
    """
    return _now_line(now)


# --- 呼び出し（claude -p。manor のワークスペースで、manor のコマンドだけ事前承認。会話は残さない） --


def _work_dir() -> Path:
    """`claude` を走らせる場所。**manor のワークスペース（リポジトリのルート）**（D21）。

    `CLAUDE.md`・`.claude/settings.json` の hooks・`butler/policy.toml` がここで初めて
    効く——状態機械・HG の門・クラス→level・射影の編集拒否は「このディレクトリで起こす」
    ことが前提の機構で、`%TEMP%` 等の外部では一切効かない。
    """
    return util.repo_root()


def _posix(p: Path) -> str:
    """Windows のパスを nested session の Bash（Git Bash/MSYS）が解釈できる POSIX 風の
    形に変える（``C:\\...`` → ``/c/...``）。`tests/behavior/run.py` の `_posix` と同じ実装。
    """
    s = str(p)
    if len(s) >= 2 and s[1] == ":":
        s = "/" + s[0].lower() + s[2:]
    return s.replace("\\", "/")


#: venv の `Scripts`（`manor` 実行ファイルの置き場）。**実測で確認済み**（`tests/behavior/
#: run.py` と同じ罠）: これを PATH の先頭に足さないと、nested session の Bash から
#: `manor ...`（`uv run` を介さない裸のコマンド。CLAUDE.md 自身がこの形も使う）が
#: command not found になる。
_VENV_SCRIPTS_POSIX = _posix(util.repo_root() / ".venv" / "Scripts")


def _talk_env(home: Path) -> dict[str, str]:
    """子プロセスの環境。**`MANOR_HOME` を明示的に上書きする**——`home` 引数と実行時の
    環境変数がずれる場合（試験・複数 home の切り替え）でも、子プロセスが正しい `home/` を
    見るようにする（`talk.py` `run()` と同じ作法）。**venv の `Scripts` を PATH の先頭に
    足す**（`_VENV_SCRIPTS_POSIX` 参照。`tests/behavior/run.py` と同じ実測済みの対策）。
    それ以外は現在の環境をそのまま渡す——`MANOR_HOOKS` は触らない（D21「hooks がそのまま
    効く」。無人セッション用の `MANOR_HOOKS=off` は夜勤専用で、ここでは意図的に立てない）。
    """
    env = dict(os.environ)
    env["MANOR_HOME"] = str(home)
    env["PATH"] = _VENV_SCRIPTS_POSIX + os.pathsep + env.get("PATH", "")
    return env


def build_command(
    exe: str,
    *,
    model: str,
    system_prompt_text: str,
    allowed_tools: Sequence[str] = ALLOWED_TOOLS,
    deny_tools: Sequence[str] = DENY_TOOLS,
    max_turns: int = DEFAULT_MAX_TURNS,
) -> list[str]:
    """`claude -p` の一式。`tests/behavior/run.py`（51〜66行・195〜206行）と同じ実績のある
    形——`--permission-mode manual` ＋ `--allowed-tools` にパターンで事前承認し、
    `--disallowed-tools` を最後の網として残す（`night/runner.py` `default_exec_argv` /
    `slack.py` `_claude_generate_argv` とはここだけ流儀が違う——あちらは道具をほぼ
    全部塞ぐ／許さない用途で、こちらは「manor のコマンドだけ実際に打てる」必要があるため）。

    **`--strict-mcp-config` は付けない**（D21。MCP を使ってよい）。
    """
    return [
        exe,
        "-p",
        "--output-format", "json",
        "--model", model,
        "--permission-mode", "manual",
        "--max-turns", str(max_turns),
        "--allowed-tools", *allowed_tools,
        "--disallowed-tools", *deny_tools,
        "--no-session-persistence",
        "--append-system-prompt", system_prompt_text,
    ]


def _check_argv(cmd: Sequence[str]) -> None:
    """`cmd.exe`（Windows の `claude.CMD`）が食べてしまうものが argv に混ざっていないか見る。

    `_safe_for_argv` で主要な混入経路は塞ぐ設計だが、ここは最後の網
    ——**黙って環境変数が漏れる／改行でコマンドが千切れるより、通話を1回やめるほうが害が小さい**。

    :raises TalkError: 改行または `%` が混ざっているとき。
    """
    for arg in cmd[1:]:  # exe 自身のパスは対象外（こちらが決めている）
        if "\n" in arg or "\r" in arg:
            raise TalkError("引数に改行が混ざっていたため、呼び出しを取りやめました")
        if "%" in arg:
            raise TalkError("引数に % が混ざっていたため、呼び出しを取りやめました")


def _build_prompt(text: str, history: Sequence[dict[str, str]] | None) -> str:
    """直近の往復だけを添えて1つの問いかけにする。`--resume` は使わない
    ——窓を閉じたら忘れるのは、履歴を画面側（呼び出し元）が持っているから（ここでは保存しない）。
    """
    turns = [t for t in (history or []) if isinstance(t, dict)][-MAX_HISTORY_MESSAGES:]
    if not turns:
        return text
    lines = ["これまでのやりとり（古い順）:"]
    for turn in turns:
        role = str(turn.get("role") or "").strip()
        content = str(turn.get("text") or "").strip()
        if not content:
            continue
        label = "執事" if role == "butler" else "主人"
        lines.append(f"{label}: {content}")
    lines.append("")
    lines.append(f"主人: {text}")
    return "\n".join(lines)


def _parse_claude_json(stdout: str) -> dict[str, Any]:
    """`--output-format json` の出力を読む。**返事が空・エラー・JSON でない**は
    `TalkError`（呼び出し側が「失敗」として扱う。声には出さない）。
    """
    try:
        data = json.loads(stdout)
    except ValueError as exc:
        raise TalkError("返事を読み取れませんでした") from exc
    if not isinstance(data, dict):
        raise TalkError("返事の形が想定と違います")
    if data.get("is_error"):
        raise TalkError(str(data.get("result") or "返事を作れませんでした"))
    reply = str(data.get("result") or "").strip()
    if not reply:
        raise TalkError("返事が空でした")
    return data


def ask(
    home: Path,
    text: str,
    history: Sequence[dict[str, str]] | None = None,
    *,
    agent: str | None = None,
    now: datetime | None = None,
    claude_bin: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> dict[str, Any]:
    """1往復。**同期で待つ**（D18）。manor のワークスペースで起こす（D21）——`manor …`
    コマンド（`Bash(manor:*)` 等）と `mcp__*`・`Read`/`Glob`/`Grep` を事前承認する
    （`ALLOWED_TOOLS`）。それ以外（`Write`/`Edit` を含む）は `--permission-mode manual`
    のもとでは非対話ゆえに使えない。

    `agent`: **小窓の担当**（ADR-011 D11「声を出す側は担当を渡す」）。姿が家政婦の窓なら
    声も家政婦——`voice.speak` へそのまま渡す（省略・語彙外は既定の声へ倒れる。
    `voice._speaker_for` が面倒を見る。**人格（誰が答えるか）は切り替えない**——ここで
    変わるのは声だけで、`claude -p` の呼び出しは agent に関わらず執事のまま）。

    :raises: 何も投げない。失敗は `{"ok": False, "reply": "..."}` で返す。
    """
    home = Path(home)
    now = now or _now()
    text = (text or "").strip()

    st = state(home, now=now)
    if not text:
        return {
            "ok": False, "reply": EMPTY_TEXT_MESSAGE, "seconds": None,
            "remaining": st["remaining"], "used": st["used"], "limit": st["limit"],
        }
    if not st["available"]:
        # D17: 打ち止め・時間外は黙らない——理由を画面にも声にも出す（v1 と同じ）。
        voice.speak(home, str(st["message"]), agent=agent)
        return {
            "ok": False, "reply": str(st["message"]), "seconds": None,
            "remaining": st["remaining"], "used": st["used"], "limit": st["limit"],
        }

    # D17: **先に数える。** 失敗して1回ぶん損するほうが、数え損ねて枠を食い潰すより害が小さい。
    cfg = _load_talk_config(home)
    limit = st["limit"]
    used = bump_usage(home, now.date())
    remaining = max(0, limit - used)
    model = _cfg_model(cfg)

    conn: sqlite3.Connection | None
    try:
        conn = db_mod.connect(home)
    except Exception:  # noqa: BLE001 - DB が開けないことは通話が止まる理由にならない
        conn = None

    run_id: int | None = None
    if conn is not None:
        try:
            run_id = runlog.start(conn, RUN_KIND, model=model)
            conn.commit()
        except Exception:  # noqa: BLE001 - 記録の失敗で通話そのものを止めない
            run_id = None

    exe = claude_bin or shutil.which("claude") or "claude"

    started = time.time()
    try:
        sys_prompt = _system_prompt(now)
        cmd = build_command(exe, model=model, system_prompt_text=sys_prompt)
        _check_argv(cmd)
        prompt_text = _build_prompt(text, history)
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(_work_dir()),  # D21: manor のワークスペース（hooks・CLAUDE.md が効く）
                env=_talk_env(home),
                input=prompt_text,  # **argv には載せない**（v1 と同じ理由。改行・% の混入対策）
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            raise TalkError(f"{int(timeout)}秒待っても返事がありませんでした") from exc
        except OSError as exc:
            raise TalkError(f"呼び出せませんでした: {exc}") from exc
        if proc.returncode != 0:
            tail_lines = (proc.stderr or "").strip().splitlines()
            tail = tail_lines[-1] if tail_lines else f"終了コード {proc.returncode}"
            raise TalkError(f"返事を作れませんでした: {tail}")
        data = _parse_claude_json(proc.stdout)
    except TalkError as exc:
        seconds = round(time.time() - started, 1)
        if conn is not None and run_id is not None:
            try:
                runlog.finish(conn, run_id, exit_reason="failed", note=str(exc)[:500])
                conn.commit()
            except Exception:  # noqa: BLE001
                pass
        if conn is not None:
            conn.close()
        # D17: **失敗は声に出さない。** 画面には出すが、故障を音声で伝えない。
        return {
            "ok": False, "reply": str(exc), "seconds": seconds,
            "remaining": remaining, "used": used, "limit": limit,
        }

    reply = str(data.get("result") or "").strip()
    seconds = round(time.time() - started, 1)
    if conn is not None and run_id is not None:
        try:
            runlog.finish(conn, run_id, **runlog.from_claude_result(data))
            conn.commit()
        except Exception:  # noqa: BLE001
            pass
    if conn is not None:
        conn.close()

    voice.speak(home, reply, agent=agent)
    return {
        "ok": True, "reply": reply, "seconds": seconds,
        "remaining": remaining, "used": used, "limit": limit,
    }


# --- CLI（`manor talk-session ...`。ブラウザ無しで試すための入口） -----------------------------


def _print_json(obj: object) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _cmd_state(args: argparse.Namespace) -> int:
    home = util.manor_home()
    result = state(home)
    if args.json:
        _print_json(result)
        return 0
    print(
        i18n.t(
            "talk_session.state.summary",
            available=result["available"], used=result["used"], limit=result["limit"],
        )
    )
    if result["message"]:
        print(result["message"])
    return 0


def _cmd_ask(args: argparse.Namespace) -> int:
    home = util.manor_home()
    result = ask(home, args.text, agent=getattr(args, "agent", None))
    if args.json:
        _print_json(result)
        return 0 if result.get("ok") else 1
    print(result.get("reply", ""))
    return 0 if result.get("ok") else 1


def register(subparsers: "argparse._SubParsersAction") -> None:
    """`manor talk-session state|ask "<text>"` を足す（ADR-008 §8）。

    `manor talk <agent>`（対話的に `claude --agent <name>` を起動する既存の道具。`talk.py`）
    とは別物——こちらは主人の画面（小窓）が叩く歯止め付きの1往復で、ブラウザ無しでも
    試せるようにするための CLI。DB には自分で `home` 経由で触れるので `needs_db=False`。
    """
    p = subparsers.add_parser(
        "talk-session", help=i18n.t("cli.talk_session.help")
    )
    sub = p.add_subparsers(dest="verb")

    s = sub.add_parser("state", help=i18n.t("cli.talk_session.state.help"))
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=_cmd_state, is_write=False, needs_db=False)

    s = sub.add_parser("ask", help=i18n.t("cli.talk_session.ask.help"))
    s.add_argument("text")
    s.add_argument(
        "--agent", dest="agent", default=None,
        help=i18n.t("cli.talk_session.ask.agent.help"),
    )
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=_cmd_ask, is_write=True, needs_db=False)
