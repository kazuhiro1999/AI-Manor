"""声かけ — 要対応が増えたときだけ、一度だけ鳴らす。

v1 `apps/butler-face/notify.ps1`（`AI執事/03_design/発話設計.md`）の移植。**沈黙が既定**:

  ① 件数がある     … `decision.status='open'` の数
  ② 増えた         … 前回（`home/notify-state.json`）と比べて増えたときだけ。同じ・減った・
                       初回（状態ファイルが無い）は黙る
  ③ 静穏時間でない … 既定 22:00〜07:00（`home/config.toml` `[notify]` で変更可）。家族にも聞こえる
      ↓ すべて満たしたときだけ
    喋る（文面は v1 `phrases.txt` と一字一句同じ）

**鳴らさなかった場合も、状態ファイルは必ず更新する**（`--dry-run` を除く）。次の増加を
正しく見るため——鳴らさなかった分をあとでまとめて鳴らすことはしない（驚かせるだけ）。

`run()` は stdout に何も書かない（Stop hook から呼ばれても、hook の出力を汚さないため）。
文面を画面へ出すのは CLI 側（`main()` / `register()` で登録される `manor notify`）の仕事。
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import tomllib
from datetime import datetime
from pathlib import Path

from . import db, i18n, util

STATE_FILE_NAME = "notify-state.json"
CONFIG_FILE_NAME = "config.toml"

#: v1 `apps/butler-face/phrases.txt` の [要対応] と一字一句同じ。ずれるとキャッシュを外す。
_PHRASES: dict[int, str] = {
    0: "要対応の件はございません。",
    1: "要対応の件が、1件ございます。",
    2: "要対応の件が、2件ございます。",
    3: "要対応の件が、3件ございます。",
}
_PHRASE_MANY = "要対応の件が、4件以上ございます。"

#: Windows 既定。VOICEVOX 等を使いたい主人は `home/config.toml` の `speak_command` で上書きする。
_WINDOWS_DEFAULT_SPEAK = (
    "powershell -NoProfile -Command "
    "\"Add-Type -AssemblyName System.Speech; "
    "(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('{text}')\""
)
_MACOS_DEFAULT_SPEAK = "say {text}"

def _phrase_for(pending: int) -> str:
    if pending <= 0:
        return _PHRASES[0]
    return _PHRASES.get(pending, _PHRASE_MANY)


def all_phrases() -> list[str]:
    """`notify` が喋りうる文の全体集合（`manor voice warm` が使う。ADR-008 D11）。

    文面の出どころをここ1箇所に絞るための口——`voice.warm()` はこの関数を呼ぶだけで、
    `_PHRASES` / `_PHRASE_MANY` を自分では持たない（v1 は `phrases.txt` と実装の二重管理で
    ずれてキャッシュを外していた。ここでは表を1つにする）。
    """
    return sorted(set(_PHRASES.values()) | {_PHRASE_MANY})


def _pending_count(home: Path) -> int | None:
    """`decision.status='open'` の数。DB が無い・開けないときは None。"""
    if not (home / "manor.db").is_file():
        return None
    try:
        conn = db.connect(home)
    except Exception:
        return None
    try:
        row = conn.execute("SELECT COUNT(*) AS c FROM decision WHERE status = 'open'").fetchone()
        return int(row["c"]) if row is not None else 0
    except Exception:
        return None
    finally:
        conn.close()


def _read_state(home: Path) -> int | None:
    """前回の件数。状態ファイルが無い・壊れているときは None（＝初回として扱う）。"""
    path = home / STATE_FILE_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return int(data.get("last_pending"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _write_state(home: Path, pending: int, now_str: str) -> None:
    path = home / STATE_FILE_NAME
    payload = {"last_pending": pending, "checked_at": now_str}
    try:
        home.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _load_notify_config(home: Path) -> dict[str, object]:
    """`home/config.toml` の `[notify]` 節。無い・壊れているときは空辞書（既定値を使う）。"""
    path = home / CONFIG_FILE_NAME
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except Exception:
        return {}
    section = data.get("notify") if isinstance(data, dict) else None
    return section if isinstance(section, dict) else {}


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _hour_of(now_str: str) -> int:
    try:
        return datetime.fromisoformat(now_str).hour
    except (TypeError, ValueError):
        return datetime.now().hour


def _in_quiet_hours(hour: int, quiet_from: int, quiet_to: int) -> bool:
    if quiet_from > quiet_to:
        return hour >= quiet_from or hour < quiet_to
    return quiet_from <= hour < quiet_to


def _default_speak_command() -> str | None:
    if sys.platform.startswith("win"):
        return _WINDOWS_DEFAULT_SPEAK
    if sys.platform == "darwin":
        return _MACOS_DEFAULT_SPEAK
    return None


def _speak(text: str, speak_command: str | None) -> bool:
    """声の実体を鳴らす。**失敗しても例外を投げない**（False を返すだけ）。

    stdout/stderr は捕捉して捨てる（Stop hook から呼ばれても hook 自身の出力を汚さないため）。
    `speak_command` が無い（設定も OS 既定も無いプラットフォーム）ときだけ、文面を標準出力へ
    出す——それが「声」の代わりになる唯一の手段のため（v1 発話設計にはない道、manor 独自）。
    """
    if not speak_command:
        try:
            print(text)
        except Exception:
            return False
        return True

    escaped = text.replace("'", "''")
    cmd = speak_command.replace("{text}", escaped)
    try:
        argv = shlex.split(cmd)
    except ValueError:
        return False
    if not argv:
        return False
    try:
        result = subprocess.run(argv, shell=False, timeout=60, capture_output=True)
    except Exception:
        return False
    return result.returncode == 0


#: `manor notify`（声かけ）は執事の声で喋る（ADR-011 D11「人格までは切り替えない」の
#: 対になる決め——執事以外の担当が代わりに声をかける設計は無い）。
_NOTIFY_AGENT = "butler"


def _speak_dispatch(home: Path, text: str, speak_command: str | None) -> bool:
    """発話の門を `voice.speak()` に一本化する（ADR-008 D8）。

    1. `[voice] engine` が設定されていれば `voice.speak(home, text, agent="butler")`
       （ADR-011 D11。消音の判定もその中で行う——「消したのに喋った」を作らないため、
       消音の判定は voice の中に1箇所だけ置く）
    2. 未設定なら従来どおり `speak_command`／OS 既定（`_speak`）。**この経路も
       `voice.is_muted()` を尊重する**

    `voice` は本モジュールを import する（`voice.warm()` が `all_phrases()` を使うため）
    ので、循環 import を避けるためここで遅延 import する。
    """
    from . import voice as voice_mod

    try:
        if voice_mod.is_muted(home):
            return False
        if voice_mod.is_voice_configured(home):
            return voice_mod.speak(home, text, agent=_NOTIFY_AGENT)
    except Exception:
        return False
    return _speak(text, speak_command)


def run(
    home: Path,
    *,
    force: bool = False,
    quiet: bool = False,
    dry_run: bool = False,
    now: str | None = None,
) -> dict[str, object]:
    """判定して（条件を満たせば）鳴らす。CLI と Stop hook の両方から呼ぶ。

    戻り値: `pending` / `last` / `should`（条件を満たしたか。静穏時間での見送りも反映）／
    `spoke`（実際に鳴らした・出力したか）／ `reason` / `text`。加えて `hour` / `quiet` /
    `dry_run` / `force` も含む（CLI 表示用）。

    **stdout には何も書かない**（`speak_command` が無いプラットフォームでの `print` を除く。
    §_speak）。Stop hook から呼ばれても hook の出力契約を壊さないため。
    """
    home = Path(home)
    pending = _pending_count(home)
    if pending is None:
        return {
            "pending": 0,
            "last": None,
            "should": False,
            "spoke": False,
            "reason": "no_db",
            "text": "",
        }

    cfg = _load_notify_config(home)
    quiet_from = _as_int(cfg.get("quiet_from"), 22)
    quiet_to = _as_int(cfg.get("quiet_to"), 7)
    speak_command = cfg.get("speak_command")
    if not isinstance(speak_command, str) or not speak_command.strip():
        speak_command = _default_speak_command()

    last = _read_state(home)
    first_run = last is None
    increased = (not first_run) and pending > last

    now_str = now if now is not None else util.now()
    hour = _hour_of(now_str)
    in_quiet = _in_quiet_hours(hour, quiet_from, quiet_to)

    should = force or increased
    if should and in_quiet and not force:
        should = False
        reason = "quiet_hours"
    elif force:
        reason = "forced"
    elif should:
        reason = "increased"
    elif first_run:
        reason = "first_run"
    elif pending == last:
        reason = "no_change"
    elif pending < last:  # type: ignore[operator]
        reason = "decreased"
    else:
        reason = "other"

    text = _phrase_for(pending)

    spoke = False
    if should and not quiet and not dry_run:
        spoke = _speak_dispatch(home, text, speak_command)

    if not dry_run:
        _write_state(home, pending, now_str)

    return {
        "pending": pending,
        "last": last,
        "should": should,
        "spoke": spoke,
        "reason": reason,
        "text": text,
        "hour": hour,
        "quiet": quiet,
        "dry_run": dry_run,
        "force": force,
    }


def format_message(result: dict[str, object]) -> str:
    """v1 `notify.ps1` と同じ体裁の、日本語1行。`no_db` のときは空文字（黙る）。"""
    reason = str(result.get("reason", ""))
    pending = result.get("pending")
    last = result.get("last")

    if reason == "no_db":
        return ""
    if reason == "quiet_hours":
        return i18n.t("notify.message.quiet_hours", pending=pending, hour=result.get("hour"))
    if result.get("should"):
        if result.get("spoke"):
            return i18n.t("notify.message.spoke", pending=pending)
        return i18n.t("notify.message.would_speak", pending=pending, text=result.get("text"))
    why_key = {
        "first_run": "notify.message.why_first_run",
        "no_change": "notify.message.why_no_change",
        "decreased": "notify.message.why_decreased",
    }.get(reason, "notify.message.why_other")
    why = i18n.t(why_key)
    last_disp = last if last is not None else i18n.t("common.dash")
    return i18n.t("notify.message.silent", pending=pending, last=last_disp, why=why)


# --- CLI（`python -m manor.notify`。`manor notify` への配線は cli.py 側の仕事） -------------


def _cmd_notify(conn, home, args) -> object:  # noqa: ANN001 - cli.py の (conn, home, args) 規約
    result = run(home, force=args.force, quiet=args.quiet, dry_run=args.dry_run)
    if args.json:
        return result
    return format_message(result)


def register(subparsers: argparse._SubParsersAction) -> None:
    """`manor notify` を足す。`cli.py` の `register(subparsers)` 規約（ADR-001 §11）に合わせる。

    配線（`build_parser()` からこれを呼ぶ）は本モジュールの担当範囲外——`cli.py` は触らない。
    """
    p = subparsers.add_parser("notify", help=i18n.t("cli.notify.help"))
    p.add_argument("--force", action="store_true", help=i18n.t("cli.notify.force.help"))
    p.add_argument("--quiet", action="store_true", help=i18n.t("cli.notify.quiet.help"))
    p.add_argument("--dry-run", action="store_true", dest="dry_run", help=i18n.t("cli.notify.dry_run.help"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_notify, is_write=False)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="manor.notify", description=i18n.t("cli.notify.help"))
    parser.add_argument("--force", action="store_true", help=i18n.t("cli.notify.force.help"))
    parser.add_argument("--quiet", action="store_true", help=i18n.t("cli.notify.quiet.help"))
    parser.add_argument("--dry-run", action="store_true", dest="dry_run", help=i18n.t("cli.notify.dry_run.help"))
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass
    args = _build_arg_parser().parse_args(argv)
    home = util.manor_home()
    result = run(home, force=args.force, quiet=args.quiet, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        msg = format_message(result)
        if msg:
            print(msg)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
