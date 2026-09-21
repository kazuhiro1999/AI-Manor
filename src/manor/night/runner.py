"""夜勤の機構 — v1 `AI執事/apps/night-shift/{run-night.ps1, night-prompt.txt}` の移植。

v1 は読み取り専用で参照した（1文字も v1 側を変更していない）。**主人の要望（2026-09-02）**:
「夜間タスクの仕組みは引き継ぎたい。ただし今は v1 が現役なので、こちらからはトリガーしない」
——このため `install()` は OS のスケジューラへ登録するコマンドを**組んで見せるだけ**で、
`execute=True`（CLI では `--yes`）を渡さない限り実際には登録しない。

v1 README が指摘していたとおり、機構は散文（プロンプトに「〜してください」と書くだけ）では
守られない。ここでは次を**機械の側で**やる（散文はプロンプト側の努力目標に留める）:

  1. **起動の門**   — 締切まで `min_minutes` 未満なら、そもそも起動しない
  2. **時刻の注入** — 「いま何時か・締切まで何分か」を実測してプロンプトの先頭に差し込む
     （執事に時計を読ませない。読み違いは実際に起きる — v1 README 参照）
  3. **打ち切り**   — 締切＋猶予（`grace_minutes`）を過ぎても走っていたら子プロセスを殺す
     （Windows: `taskkill /T /F`。他: プロセスグループへ `SIGTERM`）
  4. **ロック**     — 二重起動しない。ロックの持ち主 PID が死んでいれば古いロックを捨てる
  5. **利用上限で落ちたら1度だけ再開** — リセット時刻が読めて、締切に間に合うときだけ待って
     再開する。読めなければ**推測で埋めず**、その晩はそこで終える
  6. **消音**       — `home/notify-state.json` には一切触れない。子プロセスの環境に
     `MANOR_HOOKS=off` を必ず立てる（hooks 自体も無人セッションでは黙るが、二重に）
  7. **`claude -p` の絞り** — `--permission-mode dontAsk` ＋ 絞った `--allowed-tools` ＋
     `--strict-mcp-config`（MCP を1本も載せない。v1 B174 の裁定を踏襲）。
     **外部送信の道具は道具立てに無い**（allowed-tools に mcp__* / WebFetch / SendMessage 等が無い）

置き場は全部 `MANOR_HOME/night/` 配下（②。git 管理外。`home/README.md` 参照）:

    tasks.md              指示書。ここに書かれたものだけをやる。空なら何もしない
    reports/<日付>.md      作業報告（board が読む）
    logs/<YYYY-MM>.log     実行ログ
    night.lock             PID + 開始時刻（生存確認つき）
    last-run.json          最後の実行の記録
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import tempfile
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .. import db, runlog, util, winps
from . import plan, progress

# --- 置き場 ------------------------------------------------------------------

NIGHT_DIRNAME = "night"
TASKS_FILE_NAME = "tasks.md"
REPORTS_DIR_NAME = "reports"
LOGS_DIR_NAME = "logs"
LOCK_FILE_NAME = "night.lock"
LAST_RUN_FILE_NAME = "last-run.json"
#: `--dry-run` の記録。**本番の `last-run.json` は塗り替えない**（検分 S11）。
LAST_RUN_DRY_FILE_NAME = "last-run.dry.json"
#: 一時停止の記録（N8。主人 2026-09-14「9/12 に止めてと言い、戻す段取りが無かった」）。
PAUSE_FILE_NAME = "pause.json"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

DEFAULT_DEADLINE = "06:30"
DEFAULT_MIN_MINUTES = 20
DEFAULT_GRACE_MINUTES = 15
DEFAULT_LOCK_MAX_MIN = 180
DEFAULT_MODEL = "sonnet"
#: 1回の `claude -p` に許すターン数（主人の裁定 D15・2026-09-11 に 80 → 200）。
#:
#: **80 では一覧の最後まで届かなかった。** 2026-09-10・09-11 と2晩続けて打ち切られ、
#: 最後に置いた指示（意見箱）へ2晩とも着手できずに終わっている（実測:
#: `terminal_reason=max_turns` / `errors=["Reached maximum number of turns (80)"]`）。
#: 締切は 269 分あるのに、実際に使えていたのは **14分45秒**——止めていたのは時計ではなく
#: この数字だった。
#:
#: **費用の心配は要らない**（主人のご指摘 2026-09-11）。`claude -p` は API の従量課金では
#: なく主人のプランの枠を使う。結果 JSON の `total_cost_usd` は `"costBasis": "list"`
#: ——**定価に換算した目安**であって、請求ではない。効いてくるのは5時間枠のほうなので、
#: 際限なく上げるのではなく、一晩ぶんが枠を食い潰さない範囲に置く。
DEFAULT_MAX_TURNS = 200
DEFAULT_TASK_NAME = "manor-night"

#: 夜勤に持たせる道具。**主人の裁定（D11・D12・2026-09-09）で広げました**:
#:
#: > 夜間タスクも特にツールを制限する必要性はない。git push などの HG さえ不正であれば問題ない。（D11）
#: > 夜間タスクも Web サーチなどの道具を使用可にする。（D12）
#:
#: ⚠ **それまでの設計は間違っていました。** `WebFetch` / `WebSearch` を外していたのは
#: POLICY の lethal trifecta 判定器に沿った判断でしたが、`tasks.md` の N2①（外部の視点を
#: 毎晩1件採り入れる）は**外部の URL を読む作業そのもの**で、道具立てと指示が
#: 真正面から矛盾していました。**5晩ぶん（E1〜E25）は昼のセッションで書かれたもの**で、
#: 夜勤は一度も自力で回せていません。2026-09-09 の夜勤が自分でそれに気づき、
#: T39 / D12 として上げてきました。
ALLOWED_TOOLS: list[str] = [
    "Read",
    "Glob",
    "Grep",
    "Bash",
    "Edit",
    "Write",
    "WebFetch",
    "WebSearch",
    "TodoWrite",
]

#: **本当に塞ぐもの**（`--disallowed-tools` に渡る。主人の裁定 D11「git push などの HG さえ
#: 不正であれば問題ない」）。
#:
#: ⚠ **`--allowed-tools` は「これ以外を禁止」ではありません。** 2026-09-08 に対話セッションから
#: 実測して「絞りが効いていない」と主人へご報告しましたが、**夜勤の実環境では効いていました**
#: （2026-09-09 の `permission_denials` に `Bash`・`WebSearch`・`WebFetch` が実際に並んだ）。
#: 起動元によって信頼状態が違い、対話セッションでの実測が夜勤の実態を表していなかった
#: ——**測る場所を間違えると、結論ごと間違えます**。
#:
#: いずれにせよ**歯止めは「命じる」ではなく「渡さない」側に置く**のが筋なので、
#: HG に当たるものは名指しで塞ぎます。
DISALLOWED_TOOLS: list[str] = [
    "Bash(git push:*)",
    "Bash(git remote:*)",
    "Bash(git reset --hard:*)",
    "Bash(git rebase:*)",
    "Bash(rm -rf:*)",
    "Bash(curl:*)",
    "Bash(wget:*)",
    "Bash(Invoke-WebRequest:*)",
    "Bash(Invoke-RestMethod:*)",
]

#: 何かの拍子に**許可の側**へ紛れ込んでいないかを試験で機械的に確かめるための禁句。
#: ⚠ MCP は `--strict-mcp-config` で丸ごと落としているので、名前が現れること自体が異常。
FORBIDDEN_TOOL_MARKERS: tuple[str, ...] = (
    "mcp__",
    "SendMessage",
    # git は add/commit まで許す（執事の裁定 2026-09-02。v1 の 1タスク1コミットを機能させる）。
    # 不可逆な git と外部への送信は `DISALLOWED_TOOLS` で塞ぐ。
    "git push",
    "git remote",
    "git reset --hard",
    "git rebase",
)


def night_dir(home: Path) -> Path:
    return Path(home) / NIGHT_DIRNAME


def tasks_path(home: Path) -> Path:
    return night_dir(home) / TASKS_FILE_NAME


def reports_dir(home: Path) -> Path:
    return night_dir(home) / REPORTS_DIR_NAME


def logs_dir(home: Path) -> Path:
    return night_dir(home) / LOGS_DIR_NAME


def pause_path(home: Path) -> Path:
    return night_dir(home) / PAUSE_FILE_NAME


def read_pause(home: Path, *, today: str | None = None) -> dict[str, Any] | None:
    """停止中なら `{"until", "reason", "paused_at"}` を返す。**`until` を過ぎていれば
    自動解除**（`None` を返す。ファイルは消さない——`resume` を呼ばなくても、次の判定は
    毎回ここを通るので古い記録が悪さをしない）。壊れたファイルも `None`（安全側）。
    """
    p = pause_path(home)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict) or not _DATE_RE.match(str(data.get("until") or "")):
        return None
    ref = today or util.today()
    if str(data["until"]) < ref:
        return None
    return data


def pause(home: Path, *, until: str, reason: str) -> dict[str, Any]:
    """`manor night pause --until <YYYY-MM-DD> --reason "…"`。主人が「今夜は止めて」と
    言われたときの一時停止を、次の執事が起動時に必ず見る場所（DB→射影の代わりに、
    ここでは `pause.json`→`status`/`review`/`active` の3か所）へ映す。
    """
    if not _DATE_RE.match(until):
        raise ValueError(f"until は YYYY-MM-DD 形式にしてください: {until!r}")
    data = {"until": until, "reason": reason, "paused_at": datetime.now().isoformat()}
    p = pause_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def resume(home: Path) -> dict[str, Any]:
    """一時停止を解く。**`until` を待たずに戻したいとき**の口（自動解除は `read_pause` が
    `until` を見て行うので、こちらは明示的な早期解除専用）。
    """
    p = pause_path(home)
    was_paused = p.is_file()
    if was_paused:
        p.unlink()
    return {"was_paused": was_paused}


def lock_path(home: Path) -> Path:
    return night_dir(home) / LOCK_FILE_NAME


def last_run_path(home: Path, *, dry_run: bool = False) -> Path:
    """最後の実行の記録。**dry-run は別のファイルへ書く**（検分 S11・2026-09-06）。

    以前は `--dry-run` も同じ `last-run.json` を塗り替えていたので、下見のつもりで
    1回叩くと `manor night status` が「前回は dry_run」と答えるようになった——
    **本番が動いたかどうかを見る唯一の記録が、下見で消えていた。**

    消すのではなく分けたのは、「dry-run が何を返したか」も見たいことがあるため。
    """
    name = LAST_RUN_DRY_FILE_NAME if dry_run else LAST_RUN_FILE_NAME
    return night_dir(home) / name


def prompt_template_path() -> Path:
    return Path(__file__).resolve().parent / "prompt.txt"


# --- 時計（純粋関数。時刻の解釈は昼間に確かめられなければ、夜になって初めて
#     知ることになる——v1 README と同じ理由でここを分離してある） -----------------

_HHMM_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")
_RESET_RE = re.compile(r"resets?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.IGNORECASE)
_LIMIT_RE = re.compile(r'"api_error_status"\s*:\s*429|(session|usage)\s+limit', re.IGNORECASE)
_TASK_LINE_RE = re.compile(r"^\|\s*(~~)?[A-Za-z]+\d+", re.MULTILINE)
_RESULT_FIELD_RE = re.compile(r'"result"\s*:\s*"((?:[^"\\]|\\.)*)"')


def parse_now(value: str | None) -> datetime:
    """`--now` の値を読む。`HH:MM` は「今日」（`util.today()`。試験は `MANOR_TODAY` で固定）の
    その時刻として、それ以外は ISO 8601 として読む。空文字・None なら実時刻。"""
    if not value or not value.strip():
        return datetime.now()
    v = value.strip()
    m = _HHMM_RE.match(v)
    if m:
        y, mo, d = (int(x) for x in util.today().split("-"))
        return datetime(y, mo, d, int(m.group(1)), int(m.group(2)))
    try:
        return datetime.fromisoformat(v)
    except ValueError as exc:
        raise ValueError(f"--now の書き方が読めません: {value!r}") from exc


def get_deadline_at(from_dt: datetime, hhmm: str) -> datetime:
    """基準時刻から見た「次の HH:MM」。02:00 に見た 06:30 は同じ日、23:00 に見た 06:30 は
    翌日。**同時刻ちょうどは「もう過ぎた」扱い**にする（v1 と同じ）。"""
    m = _HHMM_RE.match(hhmm)
    if not m:
        raise ValueError(f"締切の書き方が読めません: {hhmm!r}")
    at = from_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    at += timedelta(hours=int(m.group(1)), minutes=int(m.group(2)))
    if at <= from_dt:
        at += timedelta(days=1)
    return at


def get_reset_at(text: str, from_dt: datetime) -> datetime | None:
    """`You've hit your session limit · resets 3:40am (Etc/GMT-9)` から復帰時刻を読む。
    読めなければ `None`（**読めないものを推測で埋めない**——待ちすぎて晩を潰す）。"""
    if not text or not text.strip():
        return None
    m = _RESET_RE.search(text)
    if not m:
        return None
    h = int(m.group(1))
    mi = int(m.group(2)) if m.group(2) else 0
    ap = (m.group(3) or "").lower()
    if h > 23 or mi > 59:
        return None
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    at = from_dt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(hours=h, minutes=mi)
    if at <= from_dt:
        at += timedelta(days=1)
    return at


def is_session_limit(raw: str) -> bool:
    """利用上限で落ちたか。**文言に頼りきらない**——429 も見る。"""
    if not raw or not raw.strip():
        return False
    return bool(_LIMIT_RE.search(raw))


def count_task_lines(tasks_body: str) -> int:
    """指示行らしき行の本数。**指示行の記号は `N1` `M3` のように「英字＋数字」で始まる**
    （v1 B188 是正）。0 なら「空」— `run()` は claude を呼ばずに終える。"""
    return len(_TASK_LINE_RE.findall(tasks_body or ""))


RESUME_NOTE = """
> **これは再開です。** 前回は**利用上限**で落ち、復帰を待って開始し直しました。
> **同じ晩の続きです**——`home/night/reports/` の今日の報告を先に見て、
> **書きかけがあればそこから**。最初からやり直さないこと。
> **報告には「一度落ちて再開した」ことを書いてください**（見積りが狂った理由になります）。
"""

_CLOCK_TEMPLATE = """## いまの時刻 — **機械が測って入れています。推測しないでください**

- **現在: {now}**
- **締切: {deadline} — 残り {left} 分**

この2行は `manor night run` が実測して差し込んだものです。**`home/night/tasks.md` の締切より、\
こちらが正。** 締切を過ぎても走っていた場合、**{grace} 分の猶予のあとに機械が打ち切ります**——\
そこで殺されると作業報告が残らないので、**締切の手前で自分から畳んでください**。
{resume}
---

"""


def build_clock_block(
    at: datetime, until: datetime, grace_minutes: int, resumed_from: datetime | None
) -> str:
    """プロンプトの先頭に差す「いま何時か」。**執事に時計を読ませない。**"""
    left = int((until - at).total_seconds() // 60)
    resume = RESUME_NOTE if resumed_from is not None else ""
    return _CLOCK_TEMPLATE.format(
        now=at.strftime("%Y-%m-%d(%a) %H:%M"),
        deadline=until.strftime("%H:%M"),
        left=left,
        grace=grace_minutes,
        resume=resume,
    )


# --- ロック ------------------------------------------------------------------


def _pid_alive(pid: int) -> bool:
    """PID が生きているか。Windows は `tasklist`（`os.kill(pid, 0)` は Windows では
    ハンドルが残っていると死んだ後も偽陽性を返すことが実測で分かっている）。"""
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        try:
            proc = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception:
            return False
        return bool(re.search(rf"\b{pid}\b", proc.stdout or ""))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def acquire_lock(home: Path, *, lock_max_min: int = DEFAULT_LOCK_MAX_MIN) -> dict[str, Any]:
    """ロックを取る。**二重起動しない。** 持ち主 PID が死んでいれば古いロックを捨てて取る
    （異常終了が翌晩を巻き添えにしない）。"""
    lp = lock_path(home)
    discarded: str | None = None
    if lp.is_file():
        age_min = (datetime.now().timestamp() - lp.stat().st_mtime) / 60
        owner_raw = lp.read_text(encoding="utf-8").strip()
        alive = owner_raw.isdigit() and _pid_alive(int(owner_raw))
        if alive and age_min < lock_max_min:
            return {
                "ok": False,
                "reason": f"先行実行が動作中のため中止します（PID {owner_raw} / {age_min:.0f} 分前）",
                "owner": owner_raw,
                "discarded": None,
            }
        discarded = f"残っていたロックを破棄します（PID {owner_raw} は不在 / {age_min:.0f} 分前）"
        lp.unlink()
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text(str(os.getpid()), encoding="utf-8")
    return {"ok": True, "reason": "", "owner": str(os.getpid()), "discarded": discarded}


def release_lock(home: Path) -> None:
    lp = lock_path(home)
    if lp.is_file():
        try:
            lp.unlink()
        except OSError:
            pass


# --- ログ --------------------------------------------------------------------


class NightLog:
    """`home/night/logs/<YYYY-MM>.log` に1行ずつ追記する（実時刻・実ファイル名。
    `--now` の偽装とは独立——ログの日付を偽装すると翌朝の実ファイルが分からなくなる）。"""

    def __init__(self, home: Path, *, echo: bool = True) -> None:
        self._dir = logs_dir(home)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._echo = echo
        self.lines: list[str] = []

    def write(self, level: str, msg: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"{ts} [{level}] {msg}"
        self.lines.append(line)
        path = self._dir / f"{datetime.now().strftime('%Y-%m')}.log"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        if self._echo:
            print(line)


# --- exec コマンドの組み立て ---------------------------------------------------


def default_exec_argv(
    *, model: str = DEFAULT_MODEL, max_turns: int = DEFAULT_MAX_TURNS, claude_bin: str | None = None
) -> list[str]:
    """本物の `claude -p` を呼ぶときの既定コマンド。`--exec` が渡されたときは使わない
    （`--exec` は v1 の `-Exec` と同じく**丸ごと置き換え**——テストのモックはこちら経由）。"""
    # `talk.py` の `build_command` と同じ解決順（`shutil.which` -> 素の "claude"）。
    exe = claude_bin or shutil.which("claude") or "claude"
    return [
        exe,
        "-p",
        "--output-format",
        "json",
        "--permission-mode",
        "dontAsk",
        "--strict-mcp-config",
        "--max-turns",
        str(max_turns),
        "--model",
        model,
        "--allowed-tools",
        *ALLOWED_TOOLS,
        # **塞ぐ側を明示する**（主人の裁定 D11・2026-09-09）。`--allowed-tools` は
        # 「これ以外を禁止」ではないので、HG に当たるものはここで名指しする。
        "--disallowed-tools",
        *DISALLOWED_TOOLS,
    ]


def build_exec_argv(exec_cmd: str | None, *, model: str, max_turns: int) -> list[str]:
    if exec_cmd:
        return shlex.split(exec_cmd)
    return default_exec_argv(model=model, max_turns=max_turns)


# --- 打ち切り（子プロセスの起動・待機・kill） -------------------------------------


def _kill_tree(pid: int) -> None:
    """締切＋猶予を過ぎても走っていたら殺す。Windows は `taskkill /T /F`（子孫ごと）、
    他は `SIGTERM`（プロセスグループへ。無ければ本体だけ）。"""
    if sys.platform.startswith("win"):
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return
    import signal

    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except Exception:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass


def _run_child(
    argv: list[str], *, cwd: Path, env: dict[str, str], prompt: str, timeout_seconds: float
) -> dict[str, Any]:
    """1本の子プロセスを起動し、`timeout_seconds` 待って、超えたら殺す。

    **プロンプトはパイプではなく一時ファイルから渡す**（2026-09-07）。

    以前は `communicate(input=prompt, timeout=...)` に渡していた。ところが Windows の
    CPython は **stdin を呼び出しスレッドで同期的に書く**（stdout/stderr だけが別スレッド）
    ——子がプロンプトを読まないまま stdin のバッファが埋まると、**`timeout` が効かず
    そこで止まる**。実測（2026-09-07）:

        プロンプト 1 字     → 1.4 秒で打ち切り
        プロンプト 2,735 字 → 30.4 秒（＝子が自分で終わるまで待った）
        プロンプト 64 KB    → 30.4 秒（同上）

    つまり `tasks.md` が約束している「締切＋15分で機械が強制的に打ち切ります」に穴が
    あった。本番の子（`claude -p`）は stdin を読むので普段は表に出ないが、**読む前に
    固まった子は打ち切れない**——打ち切りは最後の安全網なので、そこが条件付きでは困る。

    一時ファイルを stdin に与えれば書き込みは OS が面倒を見る。こちらは stdout/stderr を
    読むだけになり、`timeout` が素直に効く。子が受け取る中身は一字も変わらない。
    """
    popen_kwargs: dict[str, Any] = dict(
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if not sys.platform.startswith("win"):
        popen_kwargs["start_new_session"] = True

    killed = False
    with tempfile.TemporaryDirectory(prefix="manor-night-") as tmpdir:
        prompt_path = Path(tmpdir) / "prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        with prompt_path.open("r", encoding="utf-8") as stdin_file:
            proc = subprocess.Popen(argv, stdin=stdin_file, **popen_kwargs)
            try:
                stdout, stderr = proc.communicate(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                killed = True
                _kill_tree(proc.pid)
                try:
                    stdout, stderr = proc.communicate(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    try:
                        stdout, stderr = proc.communicate(timeout=5)
                    except Exception:
                        stdout, stderr = "", ""
    code = 124 if killed else (proc.returncode if proc.returncode is not None else 125)
    return {"code": code, "killed": killed, "stdout": stdout or "", "stderr": stderr or ""}


# --- run 表への記録（ADR-006 D10: 夜勤は `claude -p` の起動ごとに1行。再開も別行） ----------
#
# **観測は実行を止めない**——`run` 表・DB スキーマがまだ無い home（試験の `home_path` の
# ように `db.init()` を経ていない場合）でも、夜勤そのものは動く。`sqlite3.Error` は
# ここで飲み込む（ロギングの失敗で夜勤を止めたら本末転倒）。


def _runlog_start(home: Path, *, ref: str, model: str) -> tuple[sqlite3.Connection | None, int | None]:
    try:
        conn = db.connect(home)
        run_id = runlog.start(conn, "night", ref=ref, model=model)
        conn.commit()
        return conn, run_id
    except sqlite3.Error:
        return None, None


#: 失敗のときに残す証拠の置き場（`MANOR_HOME/night/failures/`。④・git 管理外）と、残す本数。
#: **溜め続けない**（`manor-slack-inbox.log` で踏んだのと同じ形。検分 S6）。
FAILURE_DIR_NAME = "failures"
FAILURE_KEEP = 30
#: 標準出力・標準エラーを何文字ずつ残すか。**丸ごとは残さない**（②が混ざりうるので短く切る）。
FAILURE_TAIL_CHARS = 4000


def write_failure_dump(
    home: Path, *, attempt: int, argv: list[str], stdout: str, stderr: str, diag: dict[str, Any]
) -> Path | None:
    """落ちた理由の**現物**を1件のファイルに残す（`night/failures/<日時>.json`）。

    **標準出力と標準エラーを混ぜない。** それまでは `raw = stdout + stderr` に畳んでから
    正規表現をかけていたので、「どちらが言ったのか」が消えていた——`claude` の失敗には
    stderr にしか出ないものがある（実測: `this workspace has not been trusted` で
    `.claude/settings.json` の許可が 49 件無視される）。

    失敗しても夜勤を止めない（**観測は実行を止めない**。`_runlog_start` と同じ姿勢）。
    """
    try:
        d = Path(home) / "night" / FAILURE_DIR_NAME
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{datetime.now():%Y-%m-%d_%H%M%S}.json"
        path.write_text(
            json.dumps(
                {
                    "at": util.now(),
                    "attempt": attempt,
                    "argv": argv,
                    "diagnosis": diag,
                    "stdout_tail": (stdout or "")[-FAILURE_TAIL_CHARS:],
                    "stderr_tail": (stderr or "")[-FAILURE_TAIL_CHARS:],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        for old in sorted(d.glob("*.json"))[:-FAILURE_KEEP]:
            old.unlink(missing_ok=True)
        return path
    except OSError:
        return None


def _runlog_finish(
    conn: sqlite3.Connection | None,
    run_id: int | None,
    *,
    code: int,
    killed: bool,
    raw: str,
    parsed: dict[str, Any] | None,
    why: str,
) -> None:
    if conn is None or run_id is None:
        return
    try:
        if killed:
            runlog.finish(conn, run_id, exit_reason="killed", note="締切＋猶予を過ぎて打ち切り")
        elif code == 0 and not killed and isinstance(parsed, dict):
            info = runlog.from_claude_result(parsed)
            runlog.finish(
                conn, run_id,
                usage=info["usage"], cost=info["cost"], turns=info["turns"],
                exit_reason=info["exit_reason"],
            )
        elif code == 0 and not killed:
            runlog.finish(conn, run_id, exit_reason="done", note="結果JSONを解釈できず")
        else:
            # **失敗でも数字を捨てない。** それまでは非0で終わると turns も cost も
            # usage も `None` になり、「何ターンまで行って落ちたのか」が分からなかった
            # ——`--max-turns` に当たったのかを、翌朝に判定する材料が消えていた
            # （2026-09-10 の主人のご指摘）。
            info = runlog.from_claude_result(parsed) if isinstance(parsed, dict) else None
            runlog.finish(
                conn, run_id,
                exit_reason="limit" if is_session_limit(raw) else "failed",
                note=why,
                **(
                    {"usage": info["usage"], "cost": info["cost"], "turns": info["turns"]}
                    if info is not None
                    else {}
                ),
            )
        conn.commit()
    except sqlite3.Error:
        pass
    finally:
        conn.close()


# --- 消音（ADR-008 D10: 夜勤は「戻す機会を3つ」持つ） ----------------------------------------
#
# (1) 開始時にまず restore() を呼んでから消音する (2) finally で restore() (3) 翌朝の
# 最初の実行でも restore()——(3) は (1) が毎回の run() 冒頭で走ることで自動的に満たされる。
# **同じ関数を呼ぶ**。声の失敗は夜勤を止めない（try/except で包み、例外を外へ出さない）。


def _voice_restore_safely(home: Path) -> None:
    try:
        from .. import voice as voice_mod

        voice_mod.restore(home)
    except Exception:  # noqa: BLE001 - 声は落ちてよいが、夜勤は落とさない
        pass


def _voice_mute_safely(home: Path) -> None:
    try:
        from .. import voice as voice_mod

        voice_mod.mute(home, by_night=True)
    except Exception:  # noqa: BLE001 - 声は落ちてよいが、夜勤は落とさない
        pass


# --- 本体（`manor night run`） -------------------------------------------------


def run(
    home: Path,
    *,
    repo_root: Path | None = None,
    deadline: str = DEFAULT_DEADLINE,
    min_minutes: int = DEFAULT_MIN_MINUTES,
    grace_minutes: int = DEFAULT_GRACE_MINUTES,
    dry_run: bool = False,
    exec_cmd: str | None = None,
    now: str | None = None,
    model: str = DEFAULT_MODEL,
    max_turns: int = DEFAULT_MAX_TURNS,
    no_resume: bool = False,
    lock_max_min: int = DEFAULT_LOCK_MAX_MIN,
    echo: bool = True,
    sleep_back_after: bool = False,
    diary_after: bool = False,
    check_gate_after: bool = False,
) -> dict[str, Any]:
    """`manor night run` の入口。D10: まず戻し、それから消音する。**声の失敗（VOICEVOX 未設定
    含む）で夜勤自体は止めない**——本体（`_run_impl`）は変えず、その前後を薄く包むだけ。

    **停止中（N8）は、ここで真っ先に降りる。** ロックも声も `--diary` も触らない——
    「今夜は何もしない」を字面どおりにする。記録だけは必ず残す（`status: paused`）。
    """
    home = Path(home)
    # `--now` で偽装された日付で判定する（テスト・検証用。実運用では実時刻と同じ）。
    pause_info = read_pause(home, today=parse_now(now).date().isoformat())
    if pause_info is not None:
        NightLog(home, echo=echo).write(
            "INFO",
            f"夜勤は停止中です（〜{pause_info['until']}・{pause_info['reason']}）。今夜は何もしません",
        )
        now_iso = datetime.now().isoformat()
        result: dict[str, Any] = {
            "status": "paused",
            "started_at": now_iso,
            "ended_at": now_iso,
            "pause": pause_info,
        }
        _write_last_run(home, result, dry_run=dry_run)
        return result
    _voice_restore_safely(home)
    _voice_mute_safely(home)
    result: dict[str, Any] = {}
    try:
        result = _run_impl(
            home,
            repo_root=repo_root,
            deadline=deadline,
            min_minutes=min_minutes,
            grace_minutes=grace_minutes,
            dry_run=dry_run,
            exec_cmd=exec_cmd,
            now=now,
            model=model,
            max_turns=max_turns,
            no_resume=no_resume,
            lock_max_min=lock_max_min,
            echo=echo,
            check_gate_after=check_gate_after,
        )
        # **日誌は夜勤の一部にする**（2026-09-08・主人のご要望「日誌を書くのは朝ではなく
        # 夜間タスクの1つにできませんか。トークン消費の面でも朝より夜中のほうがいい」）。
        # ⚠ **`claude` に Notion を触らせない。** 本文の生成は独立した `claude -p`
        # （道具を持たない）で、投函するのは機械です——夜勤の歯止め「Notion へは夜間に
        # 書かない」は claude への指示であって、定例5つの常時ご許可（D6）はそのまま効きます。
        # 夜勤が落ちた晩は日誌も出ませんが、それは朝の点検（`health`）が鳴らします。
        if diary_after and not dry_run and result and result.get("status") not in ("locked", "empty"):
            result["diary"] = _write_diary_safely(home, echo=echo)
        return result
    finally:
        _voice_restore_safely(home)
        # **いちばん最後に眠る。** 声を戻し、記録を書き終えてから——`SetSuspendState` の
        # あとに置いたものは翌朝まで動かない。
        #
        # 眠らせない場合が2つある: ①`locked`（別の実行が働いている最中） ②`result` が
        # 空（`_run_impl` が例外で落ちた）。**どちらも「起きたまま」のほうが安全側**。
        if sleep_back_after and result and result.get("status") != "locked":
            sb = sleep_back(dry_run=dry_run)
            NightLog(home, echo=echo).write(
                "INFO",
                f"スリープを要求しました（{sb.get('reason')}）" if sb.get("requested")
                else f"眠りません: {sb.get('reason')}",
            )
            result["sleep_back"] = sb
            _write_last_run(home, result, dry_run=dry_run)


def _write_diary_safely(home: Path, *, echo: bool = True) -> dict[str, Any]:
    """前日ぶんの執事日誌を投函する。**日誌の失敗で夜勤を失敗にしない**（声と同じ扱い）。"""
    log = NightLog(home, echo=echo)
    try:
        from .. import notion as notion_mod
        from .. import util as util_mod

        yesterday = (datetime.fromisoformat(util_mod.today()) - timedelta(days=1)).date().isoformat()
        out = notion_mod.diary(home, date=yesterday, generate=True)
        if out.get("posted"):
            log.write("INFO", f"日誌を投函しました（{yesterday}）")
        else:
            log.write("INFO", f"日誌は投函しませんでした（{yesterday}: {out.get('reason') or out.get('note') or '既にあります'}）")
        return {"ok": True, "date": yesterday, "result": out}
    except Exception as exc:  # noqa: BLE001
        log.write("WARN", f"日誌を書けませんでした: {exc}")
        return {"ok": False, "reason": str(exc)}


#: 一晩に設ける席の上限。**止めるのは時計と台帳**で、これは暴走の最後の歯止め。
MAX_SITTINGS: int = 12
#: 同じ指示に充てる席の数。これを超えても宣言が無ければ `stuck` にして次へ回す
#: （空回りの歯止め。同じ1本に一晩ぶんを溶かさない）。
MAX_SITTINGS_PER_ITEM: int = 2

_PLAN_TEMPLATE = """## 今夜の残り — **機械が数えています。推測しないでください**

これは **{n} 席目**です。下が**まだ済んでいない指示**——上から順に取ってください。

{rows}
{settled}
**1本終えるたびに、必ず宣言してください:**

    uv run manor night done <記号> --note "何をしたか一言"

進められないと分かったら（材料が無い・主人の裁定待ち など）:

    uv run manor night stuck <記号> --why "何が足りないか"

**宣言しない限り、その指示は「まだ」のままです**——次の席が同じものをもう一度やります。
逆に、宣言してあれば、この席が途中で尽きても**次の席が続きから**始めます。

⚠ **席は尽きます。それは事故ではありません。** ターン上限で切れたら、機械が次の席を
設けて残りを渡します。だからこそ**1本終えるたびにコミットと宣言**をしてください——
尽きた瞬間に失われるのは、**宣言していない分だけ**です。

---

"""


def build_plan_block(remaining: list["plan.Item"], settled: set[str], sitting_no: int) -> str:
    """プロンプトに差す「今夜の残り」。**執事に台帳を数えさせない。**

    `tasks.md` は主人が書く指示書で、そこには「済んだかどうか」が書かれていない
    （主人は消し忘れるし、消させるのも筋が違う）。**どこまで進んだかは機械が持ち、
    席のたびに渡す。**
    """
    rows = "\n".join(f"- **{i.id}** {i.title}" for i in remaining) or "- （残りはありません）"
    done_line = (
        f"\n**もう片付いているので手を付けないもの**: {', '.join(sorted(settled))}\n"
        if settled
        else ""
    )
    return _PLAN_TEMPLATE.format(n=sitting_no, rows=rows, settled=done_line)


def _run_sitting(
    home: Path,
    *,
    log: "NightLog",
    argv: list[str],
    prompt: str,
    repo_root: Path,
    deadline_at: datetime,
    grace_minutes: int,
    model: str,
    ref: str,
) -> dict[str, Any]:
    """**一席** — `claude -p` を1回だけ動かし、終わり方を種別に畳んで返す。

    ここは「一晩をどう運ぶか」を知らない。知っているのは「1回動かして、どう終わったか」
    だけ——待つ／諦める／次を設けるの判断は `_conduct` の仕事である（2026-09-11 の
    作り直しで分けた。それまでは利用上限の待機が内側のループに埋まっていて、
    「席が尽きた」と「枠が尽きた」を同じ場所で扱っていた）。

    種別: `completed`（自分で終えた）／`max_turns`（ターン上限で切れた＝**区切り**）／
    `usage_limit`（利用上限）／`killed`（締切＋猶予で打ち切り）／`failed`（それ以外）。
    """
    env = dict(os.environ)
    env["MANOR_HOME"] = str(home)
    env["MANOR_HOOKS"] = "off"

    run_conn, run_id = _runlog_start(home, ref=ref, model=model)
    timeout_seconds = max(
        (deadline_at - datetime.now()).total_seconds() + grace_minutes * 60, 1.0
    )
    child = _run_child(argv, cwd=repo_root, env=env, prompt=prompt, timeout_seconds=timeout_seconds)

    code = child["code"]
    killed = child["killed"]
    raw = (child["stdout"] or "") + "\n" + (child["stderr"] or "")

    parsed: dict[str, Any] | None = None
    if child["stdout"].strip():
        try:
            candidate = json.loads(child["stdout"])
            parsed = candidate if isinstance(candidate, dict) else None
        except Exception:  # noqa: BLE001 - 解釈できないことも「結果」の1つ
            parsed = None

    why_m = _RESULT_FIELD_RE.search(raw)
    why = why_m.group(1) if why_m else "（理由不明）"
    _runlog_finish(run_conn, run_id, code=code, killed=killed, raw=raw, parsed=parsed, why=why)

    diag = runlog.diagnose(parsed, code=code, killed=killed)

    # ⚠ 拒まれた道具を捨てない（2026-09-08・主人のご要望）。
    denials: list[Any] = []
    if isinstance(parsed, dict) and isinstance(parsed.get("permission_denials"), list):
        denials = parsed["permission_denials"]
        if denials:
            names = sorted({str(d.get("tool_name") or "?") for d in denials if isinstance(d, dict)})
            log.write("WARN", f"道具を {len(denials)} 回拒まれました: {', '.join(names)}")

    if killed:
        kind = "killed"
    elif code == 0:
        kind = "completed"
    elif is_session_limit(raw):
        kind = "usage_limit"
    elif diag.get("terminal_reason") == "max_turns":
        kind = "max_turns"
    else:
        kind = "failed"

    out: dict[str, Any] = {
        "kind": kind, "code": code, "killed": killed, "why": why,
        "diagnosis": diag, "raw": raw, "permission_denials": denials,
        "turns": diag.get("num_turns") if isinstance(diag.get("num_turns"), int) else None,
    }

    if kind == "completed":
        if isinstance(parsed, dict) and parsed.get("is_error"):
            log.write("WARN", f"claude がエラーを返しました: {parsed.get('result')}")
        elif isinstance(parsed, dict):
            log.write("INFO", f"この席は自分で終えました（{parsed.get('num_turns', '?')} ターン）")
        else:
            log.write("WARN", "結果JSONを解釈できませんでした（作業自体は行われた可能性があります）")
        return out

    if killed:
        log.write("WARN", f"締切＋猶予 {grace_minutes} 分を過ぎても終わらないため打ち切ります")

    # **理由を捨てない**（2026-09-10 の夜勤が「（理由不明）」だけ残して落ちた）。
    reason = diag.get("terminal_reason") or diag.get("subtype")
    tail = f"（{reason}）" if reason else ""
    level = "INFO" if kind == "max_turns" else "ERROR"
    log.write(level, f"この席は終わりました (exit={code}){tail}: {why}")
    dump = write_failure_dump(
        home, attempt=1, argv=argv,
        stdout=child["stdout"] or "", stderr=child["stderr"] or "", diag=diag,
    )
    if dump is not None:
        out["failure_dump"] = str(dump)
        log.write(level, f"この席の出力を残しました: {dump.name}")
    return out


def _conduct(
    home: Path,
    *,
    log: "NightLog",
    items: list["plan.Item"],
    date: str,
    body: str,
    repo_root: Path,
    first_at: datetime,
    deadline_at: datetime,
    min_minutes: int,
    grace_minutes: int,
    exec_cmd: str | None,
    model: str,
    max_turns: int,
    no_resume: bool,
) -> dict[str, Any]:
    """**一晩** — 締切まで、残っている指示がある限り、席を1つずつ設ける。

    2026-09-11 の作り直し。それまでは「一晩＝`claude -p` 一回」で、セッションが尽きれば
    その晩が終わっていた。締切は 269 分あるのに使えたのは 14分45秒で、一覧の最後に置いた
    指示へ2晩とも届かなかった（`terminal_reason=max_turns`）。

    決めたこと:

    - **直列にする。並列にしない。** 足りないのはターンであって時計ではない（269 分中
      15 分しか使えていなかった）。並列化は**余っている時計を買って、足りている
      「衝突しない git の索引」を壊す**取引になる
    - **区切りは「席が尽きたとき」であって「指示1本ごと」ではない。** 席は入るだけ
      働く——小さい指示は自然に1席へまとまり、大きい指示には次の席が丸ごと充たる
    - **畳むかどうかを決めるのは時計。** 席が自分から終えても、締切まで余っていれば
      次の席を設ける（席自身の見立てではなく、機械の時計で決める）
    - **利用上限の待機は一晩の関心。** 一席は「上限に当たった」と言うだけで、待つかどうかは
      ここが決める
    """
    out: dict[str, Any] = {
        "sittings": 0, "resumed": False, "resumed_from": None,
        "exit_code": None, "killed": False, "attempts": 0,
    }
    attempts: dict[str, int] = {}
    resumed_from: datetime | None = None
    sitting_no = 0
    status = "done"
    # `--now` の偽装（試験用）と実時刻の差を保って、2席目以降も同じ基準で測る
    # （T69・2026-09-15実測: ここが実時刻そのものだったため、偽装した夕方〜夜に
    # 回すテストで「締切まで残り」が大きく負になり、2席目に進めなかった）。
    now_offset = datetime.now() - first_at

    while True:
        settled = progress.settled(home, date)
        remaining = [i for i in items if i.id not in settled]
        if not remaining:
            log.write("INFO", "今夜の指示は全部片付きました")
            break

        at = (datetime.now() - now_offset) if sitting_no else first_at
        left = int((deadline_at - at).total_seconds() // 60)
        if left < min_minutes:
            log.write("INFO", f"締切まで残り {left} 分（下限 {min_minutes} 分）。今夜はここまでにします")
            break
        if sitting_no >= MAX_SITTINGS:
            log.write("WARN", f"席を {MAX_SITTINGS} 回設けました。歯止めとしてここで止めます")
            break

        head = remaining[0]
        attempts[head.id] = attempts.get(head.id, 0) + 1
        sitting_no = progress.count_sitting(home, date)
        out["sittings"] = sitting_no
        out["attempts"] = sitting_no
        log.write(
            "INFO",
            f"{sitting_no} 席目（残り {len(remaining)} 本・{left} 分。先頭は {head.id}）",
        )

        prompt = (
            build_clock_block(at, deadline_at, grace_minutes, resumed_from)
            + build_plan_block(remaining, settled, sitting_no)
            + body
        )
        sitting = _run_sitting(
            home,
            log=log,
            argv=build_exec_argv(exec_cmd, model=model, max_turns=max_turns),
            prompt=prompt,
            repo_root=repo_root,
            deadline_at=deadline_at,
            grace_minutes=grace_minutes,
            model=model,
            # `run` 表の `ref` は**締切の日付**のまま（作り直しの前からそう）。台帳の日付
            # （＝報告と同じ「今日」）とは、深夜をまたぐ晩だけ食い違う——ここで揃えると
            # 既存の記録の読み方が変わるので、変えない。
            ref=deadline_at.strftime("%Y-%m-%d"),
        )
        out["exit_code"] = sitting["code"]
        out["killed"] = sitting["killed"]
        if sitting.get("diagnosis"):
            out["diagnosis"] = sitting["diagnosis"]
        if sitting.get("failure_dump"):
            out["failure_dump"] = sitting["failure_dump"]
        if sitting.get("permission_denials"):
            out["permission_denials"] = sitting["permission_denials"]
        resumed_from = None

        gained = progress.settled(home, date) - settled
        if gained:
            log.write("INFO", f"片付きました: {', '.join(sorted(gained))}")
        elif sitting["kind"] != "usage_limit" and attempts[head.id] >= MAX_SITTINGS_PER_ITEM:
            # **空回りの歯止め。** 宣言が無いまま2席を使った指示は、次の席へ回さない
            # ——1本に一晩ぶんを溶かすより、残りへ進むほうが主人の得になる。
            progress.mark(
                home, date, head.id, progress.STUCK,
                note=f"{attempts[head.id]} 席かけても済んだ宣言が無かったため、運転側で詰まりとしました",
            )
            log.write("WARN", f"{head.id} は {attempts[head.id]} 席かけても進みません。詰まりとして次へ回します")

        kind = sitting["kind"]
        if kind in ("completed", "max_turns"):
            continue

        if kind == "usage_limit":
            reset_at = get_reset_at(sitting["why"], datetime.now())
            if no_resume or reset_at is None:
                status = "failed"
                break
            if (deadline_at - reset_at).total_seconds() / 60 < min_minutes:
                log.write(
                    "WARN",
                    f"利用上限。復帰は {reset_at:%H:%M} で締切 {deadline_at:%H:%M} に間に合わないため、今夜はここまで",
                )
                status = "failed_no_time"
                break
            wait_sec = int((reset_at - datetime.now()).total_seconds()) + 120
            log.write(
                "WARN",
                f"利用上限に当たりました。{reset_at:%H:%M} の復帰まで {max(wait_sec, 0) // 60} 分待って続けます",
            )
            time.sleep(max(wait_sec, 0))
            resumed_from = reset_at
            out["resumed"] = True
            out["resumed_from"] = reset_at.isoformat()
            continue

        # killed / failed —— 続けない。**同じ壊れ方をもう一度させない**
        status = "failed"
        break

    out["status"] = status
    return out


def _run_impl(
    home: Path,
    *,
    repo_root: Path | None = None,
    deadline: str = DEFAULT_DEADLINE,
    min_minutes: int = DEFAULT_MIN_MINUTES,
    grace_minutes: int = DEFAULT_GRACE_MINUTES,
    dry_run: bool = False,
    exec_cmd: str | None = None,
    now: str | None = None,
    model: str = DEFAULT_MODEL,
    max_turns: int = DEFAULT_MAX_TURNS,
    no_resume: bool = False,
    lock_max_min: int = DEFAULT_LOCK_MAX_MIN,
    echo: bool = True,
    check_gate_after: bool = False,
) -> dict[str, Any]:
    home = Path(home)
    repo_root = Path(repo_root) if repo_root else util.repo_root()
    reports_dir(home).mkdir(parents=True, exist_ok=True)

    now_at = parse_now(now)
    deadline_at = get_deadline_at(now_at, deadline)
    log = NightLog(home, echo=echo)

    lock = acquire_lock(home, lock_max_min=lock_max_min)
    if lock.get("discarded"):
        log.write("WARN", lock["discarded"])
    if not lock["ok"]:
        log.write("WARN", lock["reason"])
        return {"status": "locked", "reason": lock["reason"]}

    result: dict[str, Any] = {
        "status": "unknown",
        "started_at": now_at.isoformat(),
        "deadline": deadline_at.isoformat(),
    }
    try:
        tp = tasks_path(home)
        tasks_body = tp.read_text(encoding="utf-8") if tp.is_file() else ""
        task_line_count = count_task_lines(tasks_body)
        result["task_line_count"] = task_line_count
        log.write("INFO", f"作業指示らしき行を {task_line_count} 本見つけました（済・参考表を含む）")
        if task_line_count == 0:
            log.write("INFO", "今夜の作業指示は空です（指示行 0 本）。何もせず終了します")
            result["status"] = "empty"
            return result

        template_path = prompt_template_path()
        body = template_path.read_text(encoding="utf-8") if template_path.is_file() else ""

        remain = int((deadline_at - now_at).total_seconds() // 60)
        result["remain_minutes"] = remain
        if remain < min_minutes:
            log.write(
                "INFO",
                f"締切 {deadline_at:%H:%M} まで残り {remain} 分（下限 {min_minutes} 分）。"
                "今夜は起動しません",
            )
            result["status"] = "too_late"
            return result

        argv_preview = build_exec_argv(exec_cmd, model=model, max_turns=max_turns)
        if dry_run:
            preview = build_clock_block(now_at, deadline_at, grace_minutes, None) + body
            result["status"] = "dry_run"
            result["preview_lines"] = preview.splitlines()[:40]
            result["command"] = argv_preview
            log.write("INFO", "DryRun のため claude を起動しません")
            return result

        log.write("INFO", f"夜勤を開始します（締切 {deadline_at:%H:%M} / 残り {remain} 分）")

        items = plan.live(plan.parse(tasks_body))
        date = progress.today()
        result["items"] = [i.id for i in items]
        result.update(
            _conduct(
                home,
                log=log,
                items=items,
                date=date,
                body=body,
                repo_root=repo_root,
                first_at=now_at,
                deadline_at=deadline_at,
                min_minutes=min_minutes,
                grace_minutes=grace_minutes,
                exec_cmd=exec_cmd,
                model=model,
                max_turns=max_turns,
                no_resume=no_resume,
            )
        )
        result["progress"] = progress.summary(home, date)
        return result
    finally:
        result["ended_at"] = datetime.now().isoformat()
        if check_gate_after:
            result["gate"] = check_gate(repo_root, log=log)
        release_lock(home)
        _write_last_run(home, result, dry_run=dry_run)


#: `git status --porcelain` の行のうち、これで始まるパスだけを「コードの変更」と数える
#: （`home/` や `docs/` の変更は歯止めの対象外——歯止めが守るのは①層のテスト・コミット）。
_GATE_CODE_PREFIXES = ("src/", "tests/")


def check_gate(repo_root: Path, *, log: NightLog | None = None) -> dict[str, Any]:
    """夜勤の終わりに、歯止め（`tasks.md`「テストを通してから終わる」「1タスク1コミット」）が
    守られたかを機械的に見る（T40・2026-09-19）。**Claude 自身の自己申告に頼らない安全網**。

    ⚠ 重いので、`src/`・`tests/` に未コミットの変更が残っている晩だけ pytest を走らせる
    （変更が無ければ、待つ理由が無い）。
    """
    gate: dict[str, Any] = {"uncommitted": [], "tests": None}
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root, capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        gate["error"] = f"git status に失敗: {exc}"
        return gate
    dirty = [line for line in status.stdout.splitlines() if line.strip()]
    gate["uncommitted"] = dirty
    code_touched = any(line[3:].strip().startswith(_GATE_CODE_PREFIXES) for line in dirty)
    if not code_touched:
        return gate
    if log is not None:
        log.write("INFO", "src/・tests/ に未コミットの変更が残っています。pytest で確かめます")
    try:
        proc = subprocess.run(
            ["uv", "run", "pytest", "-q"],
            cwd=repo_root, capture_output=True, text=True, timeout=1800, check=False,
        )
        gate["tests"] = {"exit_code": proc.returncode, "tail": (proc.stdout or "")[-2000:]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        gate["tests"] = {"exit_code": None, "error": str(exc)}
    return gate


# --- 眠りへ戻す（v1 `apps/night-shift/sleep-back.ps1` の移植。T6） ---------------

#: `powercfg /lastwake` と `SetSuspendState` を呼ぶときの待ち時間（秒）。
SLEEP_BACK_TIMEOUT = 30

#: **人がいると見なす無操作の短さ（分）。** これより最近に入力があれば眠らせない。
SLEEP_BACK_IDLE_MINUTES = 15

#: `SetSuspendState` を投げたあと、生死を確かめるまでの待ち（秒）。
#: 成功していればこの時点でプロセスは**まだ生きている**（機械が眠るまで戻らないため）。
SLEEP_BACK_POLL_SECONDS = 2.0

#: スリープを**拒否された**ときにスクリプトが返す終了コード。
#: `SetSuspendState` は真偽値を返すだけで、拒否されても PowerShell の終了コードは 0 の
#: まま（実測 2026-09-06）。だから偽なら明示的にこれで落とす——「0 だから成功」という
#: 読みが成り立たないことを、コードの側で分かる形にしておく。
SLEEP_BACK_REFUSED_CODE = 3


def woken_by_task(task_name: str = DEFAULT_TASK_NAME) -> dict[str, Any]:
    """直近の復帰が、このタスクのウェイクタイマーによるものか（`powercfg /lastwake`）。

    **タスク名で判定する。** `powercfg` の文言は OS の言語で変わる（v1 は日本語の
    文言に依存していた）が、タスク名は変わらない——`NT TASK\\manor-night` の形で
    **出るはず**。⚠ **実際のタイマー復帰でまだ確かめていない**（2026-09-06 時点。
    手元では復帰履歴が 0 件の状態でしか回せていない）。裏を取る材料は
    `home/night/last-run.json` の `sleep_back.detail` に生のまま残るようにしてある。

    読めなかったときは `None`（＝分からない）を返し、**眠らせない**。
    """
    if not sys.platform.startswith("win"):
        return {"woken": None, "reason": "windows 以外では判定しません"}
    code, out, err = winps.run("powercfg /lastwake", timeout=SLEEP_BACK_TIMEOUT)
    if code != 0:
        return {"woken": None, "reason": f"powercfg /lastwake を読めません: {(err or out).strip()[:200]}"}
    return {"woken": task_name.lower() in out.lower(), "detail": out.strip()[:400]}


#: `GetLastInputInfo` を呼ぶ PowerShell。`Add-Type` の C# はここに畳んである
#: （`-MemberDefinition` では構造体を同じ型に入れられないため、型ごと定義する）。
#: `TickCount` は約24.9日で一周するが、`uint` の引き算は一周をまたいでも正しい差を返す。
_IDLE_SCRIPT = """Add-Type @"
using System;
using System.Runtime.InteropServices;
public class ManorIdle {
  [StructLayout(LayoutKind.Sequential)]
  struct LASTINPUTINFO { public uint cbSize; public uint dwTime; }
  [DllImport("user32.dll")] static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);
  public static long IdleMs() {
    LASTINPUTINFO lii = new LASTINPUTINFO();
    lii.cbSize = (uint)Marshal.SizeOf(lii);
    if (!GetLastInputInfo(ref lii)) { return -1; }
    return (long)((uint)Environment.TickCount - lii.dwTime);
  }
}
"@
[ManorIdle]::IdleMs()
"""


def idle_seconds() -> dict[str, Any]:
    """最後の入力からの経過秒数（`GetLastInputInfo`）。分からなければ `None`。

    **`powercfg /lastwake` は「どう起きたか」しか言わない。** 02:00 にタイマーで起き、
    主人が 06:00 に起きて使い始め、夜勤が 06:30 に終わる——このとき `/lastwake` は
    まだ「タスクが起こした」と答えるので、それだけを見ると**主人の手元で画面が落ちる**。
    「いま人がいるか」は別に見なければならない（検分 S1・2026-09-06）。
    """
    if not sys.platform.startswith("win"):
        return {"seconds": None, "reason": "windows 以外では判定しません"}
    code, out, err = winps.run(_IDLE_SCRIPT, timeout=SLEEP_BACK_TIMEOUT)
    if code != 0:
        return {"seconds": None, "reason": f"GetLastInputInfo を読めません: {(err or out).strip()[:200]}"}
    try:
        ms = int(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"seconds": None, "reason": f"無操作時間を読み取れません: {out.strip()[:120]}"}
    if ms < 0:
        return {"seconds": None, "reason": "GetLastInputInfo が失敗を返しました"}
    return {"seconds": ms / 1000.0}


def sleep_back(
    *,
    task_name: str = DEFAULT_TASK_NAME,
    dry_run: bool = False,
    idle_minutes: float = SLEEP_BACK_IDLE_MINUTES,
) -> dict[str, Any]:
    """PC をスリープへ戻す。**門は2つあり、どちらも通ったときだけ眠らせる。**

    1. **自分のウェイクタイマーで起きたか**（`powercfg /lastwake`）——主人が自分で
       起こした PC を勝手に眠らせない。v1 から引き継いだ判断
    2. **いま人がいないか**（`GetLastInputInfo`）——1 だけでは足りない。タイマーで
       起きた朝に主人が使い始めていても、`/lastwake` の答えは変わらないため

    どちらで止まったかが後から分かるように、**止まった理由は別々の文字列**で返す。

    戻り値の `requested` は「**要求した**」であって「眠った」ではない。眠ったことは
    この場では確かめられない（確かめられるなら、それは眠っていない）——できるのは
    「要求を出したプロセスが、出した直後にまだ生きている」ことの確認まで。

    **休止（hibernate）ではなくスリープ（S3）。** 休止するとウェイクタイマーが効かず、
    翌日の夜勤が動かない。
    """
    checks: dict[str, Any] = {}

    probe = woken_by_task(task_name)
    checks["woken"] = probe
    if probe.get("woken") is not True:
        return {
            "requested": False,
            "reason": probe.get("reason", "この復帰は執事のタスクによるものではありません"),
            "checks": checks,
            "detail": probe.get("detail", ""),
        }

    idle = idle_seconds()
    checks["idle"] = idle
    if idle.get("seconds") is None:
        return {
            "requested": False,
            "reason": f"人がいるか分からないので眠りません（{idle.get('reason', '')}）",
            "checks": checks,
            "detail": probe.get("detail", ""),
        }
    if float(idle["seconds"]) < idle_minutes * 60:
        return {
            "requested": False,
            "reason": f"{int(float(idle['seconds']))} 秒前に操作があります（{idle_minutes:g} 分未満なので眠りません）",
            "checks": checks,
            "detail": probe.get("detail", ""),
        }

    if dry_run:
        return {
            "requested": False, "reason": "dry-run のため眠りません",
            "checks": checks, "detail": probe.get("detail", ""),
        }

    # `SetSuspendState` は**真偽値を返す関数**で、拒否されると `False` を返して
    # 普通に終わる。PowerShell はそれを出力するだけなので**終了コードは 0 のまま**
    # （実測 2026-09-06: `powershell -Command '$false'` の終了コードは 0）。
    # だから**スクリプトの側でも真偽を見て、拒否なら 3 で落とす**（検分 S10）。
    script = (
        "Add-Type -Namespace Manor -Name Power -MemberDefinition '"
        '[DllImport(\\"powrprof.dll\\", SetLastError = true)] '
        "public static extern bool SetSuspendState(bool hibernate, bool forceCritical, "
        "bool disableWakeEvent);'\n"
        "if (-not [Manor.Power]::SetSuspendState($false, $false, $false)) { exit "
        f"{SLEEP_BACK_REFUSED_CODE} }}\n"
    )
    # **完了は待たない。** `SetSuspendState` は機械が起きるまで戻らないので、
    # `subprocess.run` ＋ timeout で呼ぶと、実際には眠れているのに翌朝「時間切れで失敗」と
    # 記録されてしまう。かわりに**少しだけ待って生死を見る**（検分 S2）。
    argv = [
        "powershell", "-NoProfile", "-NonInteractive",
        "-EncodedCommand", winps.encode_command(script),
    ]
    try:
        proc = subprocess.Popen(  # noqa: S603 — argv 固定。文字列をシェルに渡していない
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
        )
    except Exception as exc:  # noqa: BLE001 — 眠れないことは、夜勤の失敗ではない
        return {
            "requested": False, "reason": f"スリープ要求を出せませんでした: {exc}",
            "checks": checks, "detail": probe.get("detail", ""),
        }

    time.sleep(SLEEP_BACK_POLL_SECONDS)
    code = proc.poll()
    # **終わっていたら、終了コードが何であれ失敗。** 眠れたのなら、この窓の中で
    # 戻ってくるはずがない——`code is not None` そのものが「眠れなかった」証拠になる
    # （検分 S10。それまでは `code != 0` だけを見ていたので、拒否＝出力 `False`・
    # 終了コード 0 が「要求した」として記録されていた）。
    if code is not None:
        out = err = ""
        try:
            err = (proc.stderr.read() or b"").decode("utf-8", errors="replace").strip()
            out = (proc.stdout.read() or b"").decode("utf-8", errors="replace").strip()
        except Exception:  # noqa: BLE001
            pass
        why = (
            "拒否されました（SetSuspendState が False）"
            if code == SLEEP_BACK_REFUSED_CODE
            else f"{SLEEP_BACK_POLL_SECONDS:g} 秒で終了しました（眠れていません）"
        )
        return {
            "requested": False,
            "reason": f"スリープ要求が失敗しました: {why} (exit={code}) {(err or out)[:300]}".strip(),
            "checks": checks, "detail": probe.get("detail", ""),
        }
    return {
        "requested": True,
        "reason": "スリープを要求しました（眠ったかどうかは、この場では確かめられません）",
        "checks": checks,
        "detail": probe.get("detail", ""),
    }


def _write_last_run(home: Path, result: dict[str, Any], *, dry_run: bool = False) -> None:
    payload = {k: v for k, v in result.items() if k != "preview_lines"}
    try:
        night_dir(home).mkdir(parents=True, exist_ok=True)
        last_run_path(home, dry_run=dry_run).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


# --- status --------------------------------------------------------------------


def _query_scheduled_task(task_name: str) -> dict[str, Any]:
    if sys.platform.startswith("win"):
        try:
            proc = subprocess.run(
                ["schtasks", "/Query", "/TN", task_name],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception:
            return {"platform": "windows", "registered": None, "detail": "schtasks を呼べませんでした"}
        registered = proc.returncode == 0
        detail = (proc.stdout or proc.stderr or "").strip()[:400]
        # ⚠ **無効化されていても `registered` は True**（登録は残っている）。
        # 2026-09-12 に主人が「今夜は夜勤なし」と仰って無効化したとき、`status` は
        # 「登録: あり」と言い続けた——**戻し忘れても誰も気づかない**形だった。
        # `schtasks /Query` の出力からロケール依存の「状態」行を読むのではなく、
        # PowerShell の `State`（`Ready`/`Running`/`Disabled`。言語に依らない）を見る。
        enabled: bool | None = None
        if registered:
            code, out, _ = winps.run(
                f"(Get-ScheduledTask -TaskName '{task_name}').State", timeout=10
            )
            state = (out or "").strip()
            if code == 0 and state:
                enabled = state != "Disabled"
        return {"platform": "windows", "registered": registered, "enabled": enabled, "detail": detail}
    return {"platform": sys.platform, "registered": None, "enabled": None, "detail": "このOSでは自動確認していません"}


def _read_last_run(home: Path, *, dry_run: bool = False) -> dict[str, Any] | None:
    """`last-run.json` を読む（壊れていたら None）。`status()` と `health()` の共通の口。"""
    path = last_run_path(home, dry_run=dry_run)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def status(home: Path, *, task_name: str = DEFAULT_TASK_NAME) -> dict[str, Any]:
    home = Path(home)
    lp = lock_path(home)
    lock_info: dict[str, Any] = {"locked": False}
    if lp.is_file():
        owner_raw = lp.read_text(encoding="utf-8").strip()
        age_min = (datetime.now().timestamp() - lp.stat().st_mtime) / 60
        alive = owner_raw.isdigit() and _pid_alive(int(owner_raw))
        lock_info = {
            "locked": True,
            "pid": owner_raw,
            "alive": alive,
            "age_minutes": round(age_min, 1),
        }

    # **`last_run` は本番だけ。** dry-run は別枠で見せる（S11）——混ぜると
    # 「昨夜ちゃんと走ったか」を答えられなくなる。
    return {
        "lock": lock_info,
        "last_run": _read_last_run(home),
        "last_dry_run": _read_last_run(home, dry_run=True),
        "scheduled": _query_scheduled_task(task_name),
        "pause": read_pause(home),
    }


def format_status(data: dict[str, Any]) -> str:
    lines: list[str] = []
    pause_info = data.get("pause")
    if pause_info:
        until = str(pause_info.get("until"))
        # T75: --tonight で立てた停止（＝翌日の日付）は「今夜は休み」と読めるほうが分かりやすい。
        tonight_until = (datetime.strptime(util.today(), "%Y-%m-%d") + timedelta(days=1)).date().isoformat()
        if until == tonight_until:
            lines.append(f"夜勤: 今夜は休み（{pause_info.get('reason')}）")
        else:
            lines.append(f"夜勤: 停止中（〜{until}・{pause_info.get('reason')}）")
    lock = data.get("lock", {})
    if lock.get("locked"):
        alive = "生存" if lock.get("alive") else "不在"
        lines.append(f"ロック: PID {lock.get('pid')}（{alive}・{lock.get('age_minutes')}分前）")
    else:
        lines.append("ロック: なし")

    last_run = data.get("last_run")
    if last_run:
        lines.append(
            f"最後の実行: {last_run.get('status')}"
            f"（開始 {last_run.get('started_at')} / 終了 {last_run.get('ended_at')}）"
        )
    else:
        lines.append("最後の実行: （記録なし）")

    dry = data.get("last_dry_run")
    if dry:
        lines.append(
            f"最後の下見（--dry-run）: {dry.get('status')}"
            f"（{dry.get('started_at')}）— 本番の記録とは別に持っています"
        )

    sched = data.get("scheduled", {})
    if sched.get("registered") is True and sched.get("enabled") is False:
        lines.append("登録: あり——⚠ 無効化されています（今夜は動きません）")
    elif sched.get("registered") is True:
        lines.append("登録: あり（schtasks）")
    elif sched.get("registered") is False:
        lines.append("登録: なし")
    else:
        lines.append(f"登録: 未確認（{sched.get('detail', '')}）")
    return "\n".join(lines)


# --- install / uninstall（**組んで見せるだけ**。`execute=True` を渡さない限り登録しない） -------


def build_install_command(
    *,
    at: str,
    repo_root: Path | None = None,
    task_name: str = DEFAULT_TASK_NAME,
    sleep_back_after: bool = False,
    diary_after: bool = False,
) -> str:
    repo = Path(repo_root) if repo_root else util.repo_root()
    # **登録するコマンドに書く**（設定ファイルへ隠さない）。`schtasks /Query` を見れば
    # 「この機械の夜勤は終わったら眠る」と分かるほうが、後から読む人に親切。
    # ⚠ **引数を書き写さない。** 起動口が2つあり（`manor night` と `python -m manor.night`）、
    # ここで組む文字列は**スケジューラに登録されるほう**を指す（2026-09-06 に食い違って
    # 夜勤が丸ごと落ちかけた・G15）。
    verb = "run"
    if sleep_back_after:
        verb += " --sleep-back"
    if diary_after:
        verb += " --diary"
    if sys.platform.startswith("win"):
        python_exe = repo / ".venv" / "Scripts" / "python.exe"
        # `/TR` の中の `"` は `\"` で逃がす。**逃がさないと、空白を含むパス
        # （`...\AI Agents\manor`）で schtasks が「無効な引数」で落ちる**——実測 2026-09-06。
        inner = f'\\"{python_exe}\\" -m manor.night {verb}'
        tr = f'cmd /c cd /d \\"{repo}\\" && {inner}'
        return f'schtasks /Create /SC DAILY /ST {at} /TN "{task_name}" /TR "{tr}" /F'
    python_exe = repo / ".venv" / "bin" / "python"
    hh, mm = at.split(":")
    return (
        "# launchd/cron 雛形（macOS/Linux。schtasks に相当する自動登録は無い。手で組み込む）\n"
        f'{int(mm)} {int(hh)} * * * cd "{repo}" && "{python_exe}" -m manor.night {verb}  '
        f"# {task_name}"
    )


def build_uninstall_command(*, task_name: str = DEFAULT_TASK_NAME) -> str:
    if sys.platform.startswith("win"):
        return f'schtasks /Delete /TN "{task_name}" /F'
    return f'crontab -l | grep -v "{task_name}" | crontab -   # launchd は unload の上 plist を rm'


def install(
    *,
    at: str = "01:00",
    execute: bool = False,
    repo_root: Path | None = None,
    task_name: str = DEFAULT_TASK_NAME,
    sleep_back_after: bool = False,
    diary_after: bool = False,
) -> dict[str, Any]:
    cmd = build_install_command(
        at=at, repo_root=repo_root, task_name=task_name,
        sleep_back_after=sleep_back_after, diary_after=diary_after,
    )
    result: dict[str, Any] = {"command": cmd, "executed": False, "ok": None}
    if execute:
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=30  # noqa: S602
        )
        # **返り値を見る。** 見ないと、schtasks が「無効な引数」で落ちても
        # 「登録しました」と言ってしまう（実測 2026-09-06）——夜勤が丸ごと動かない朝を作る。
        result.update(
            executed=True,
            ok=proc.returncode == 0,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )
    return result


def uninstall(*, execute: bool = False, task_name: str = DEFAULT_TASK_NAME) -> dict[str, Any]:
    cmd = build_uninstall_command(task_name=task_name)
    result: dict[str, Any] = {"command": cmd, "executed": False, "ok": None}
    if execute:
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=30  # noqa: S602
        )
        result.update(
            executed=True,
            ok=proc.returncode == 0,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )
    return result


# --- report ----------------------------------------------------------------------


def report(home: Path, date: str | None = None) -> dict[str, Any]:
    home = Path(home)
    rd = reports_dir(home)
    available = sorted(p.stem for p in rd.glob("*.md")) if rd.is_dir() else []

    if date:
        path = rd / f"{date}.md"
        if not path.is_file():
            text = f"{date} の作業報告はありません。"
            text += (
                "\n利用できる日付: " + ", ".join(available)
                if available
                else "\n（まだ1件もありません）"
            )
            return {"found": False, "date": date, "text": text, "available": available}
        return {
            "found": True,
            "date": date,
            "text": path.read_text(encoding="utf-8"),
            "available": None,
        }

    if not available:
        return {
            "found": False,
            "date": None,
            "text": "夜勤の作業報告はまだありません。",
            "available": [],
        }
    text = "利用できる日付:\n" + "\n".join(f"  {d}" for d in available)
    return {"found": False, "date": None, "text": text, "available": available}


def health(home: Path, *, within_hours: float = 24.0) -> dict[str, Any]:
    """昨夜ちゃんと走ったかを1つの答えにする（2026-09-08 新設）。

    ⚠ **材料は最初からありました**——`last_run` は `status()` が返しており、その docstring は
    「混ぜると**昨夜ちゃんと走ったか**を答えられなくなる」とまで書いています。
    **朝の便が、それを一度も読んでいませんでした**（主人のご質問「夜間タスクのチェックは
    朝に自動実行するようになってますか？」）。ブリーフィングの【昨夜の作業】は報告ファイルが
    **あれば**読む形で、無い場合の1行（「昨夜の自動作業はありません」）は
    **①起動しなかった ②起動したが報告を書く前に落ちた ③指示が空だった**を区別しません。
    9/5 に夜勤がプロセスごと消えた晩、気づいたのは**翌々日の夜勤自身**でした。

    台帳 E1（**不在のほうを信号にする**）と E13（**走ったが何もしていない**を別に見る）が
    指していたのはこの形です。

    ⚠ **所要時間の異常（E13 の②）は入れていません。** 「何秒なら異常か」の根拠がまだ無く、
    根拠のない閾値は誤検出を生みます（B99「誤検出を出す検査は入れない」）。
    **再燃条件**: 「正常なのに短く終わった晩」と「落ちて短く終わった晩」が
    `last_run` の他の欄で見分けられないと分かったとき。
    """
    home = Path(home)
    info = _read_last_run(home)
    reasons: list[str] = []

    # **登録が無効になっていたら、記録の新旧より先に言う**（戻し忘れの守り。2026-09-12）
    sched = _query_scheduled_task(DEFAULT_TASK_NAME)
    if sched.get("registered") and sched.get("enabled") is False:
        reasons.append("夜勤の登録が無効になっています（意図して止めたなら、戻すのを忘れないでください）")

    if info is None:
        return {"ok": False, "reasons": ["夜勤の記録がありません（起動していない可能性）"], "last_run": None}

    started = str(info.get("started_at") or "")
    try:
        age_h = (datetime.now() - datetime.fromisoformat(started)).total_seconds() / 3600
    except ValueError:
        age_h = None
    if age_h is None:
        reasons.append("最後の実行の時刻を読めません")
    elif age_h > within_hours:
        reasons.append(f"昨夜は動いていません（最後の実行は {started[:16]}）")

    state = str(info.get("status") or "")
    if state and state != "done":
        reasons.append(f"夜勤は `{state}` で終わっています")
    if info.get("killed"):
        reasons.append("締切で打ち切られました")
    # ⚠ **終了コードは「最後の席」のもの**（2026-09-11 の作り直し以降）。ターン上限で
    # 切れた席は 1 を返すが、それは事故ではなく区切り——晩そのものは続いている。
    # ここで見るのは「最後の席まで壊れていたか」だけなので、`status` が `done` のときは
    # 数えない（数えると、正常に片付いた晩が毎朝赤くなる）。
    exit_code = info.get("exit_code")
    if state != "done" and isinstance(exit_code, int) and exit_code != 0:
        reasons.append(f"終了コードが {exit_code} です")

    # ⚠ **席の数は異常ではない**（作り直し以降、席が複数あるのが普通の晩）。
    # 「一度落ちて再開した」の合図は `resumed` のほうへ移した——それまでは `attempts > 1`
    # を再開の印にしていたが、いまは席の数そのものなので、鳴らすと毎晩鳴る。
    if info.get("resumed"):
        back = str(info.get("resumed_from") or "")[11:16]
        reasons.append(f"利用上限で一度止まり、{back or '復帰'} を待って続けました")

    # **台帳と突き合わせる。** `status` は「晩がどう終わったか」しか言わない——
    # 全部が詰まっていても、運転そのものは最後まで走れば `done` になる。
    # **何が片付いたか**は台帳が持っている（`night/progress.py`）。
    prog = info.get("progress")
    if isinstance(prog, dict):
        stuck = prog.get("stuck") or []
        doing = prog.get("doing") or []
        items = info.get("items") or []
        if stuck:
            reasons.append(f"進められなかった指示があります: {', '.join(map(str, stuck))}")
        if doing:
            reasons.append(f"取りかかったまま終わった指示があります: {', '.join(map(str, doing))}")
        if items and not prog.get("done"):
            reasons.append("今夜は1本も片付いていません")

    denials = info.get("permission_denials")
    if isinstance(denials, list) and denials:
        names = sorted({str(d.get("tool_name") or "?") for d in denials if isinstance(d, dict)})
        reasons.append(
            f"道具を {len(denials)} 回拒まれました（{', '.join(names)}）——"
            "許可を足すか、指示のほうを変える必要があります"
        )

    # 「done と言っているのに報告が無い」——status と成果物の食い違い
    if state == "done" and started[:10]:
        if not (reports_dir(home) / f"{started[:10]}.md").is_file():
            reasons.append(f"完了と記録されていますが、{started[:10]} の作業報告がありません")

    # **歯止め（試験・コミット）が守られたか**（T40・2026-09-19）。`check_gate()` が
    # 晩の終わりに書いた記録を読むだけ——ここでは何も実行しない（毎朝の点検は軽くあるべき）。
    gate = info.get("gate")
    if isinstance(gate, dict):
        uncommitted = gate.get("uncommitted") or []
        if uncommitted:
            reasons.append(
                f"夜勤が未コミットの変更を残したまま終わっています（{len(uncommitted)}件）"
            )
        tests = gate.get("tests")
        if isinstance(tests, dict) and tests.get("exit_code") not in (None, 0):
            reasons.append("夜勤の終わりに試験が赤でした")

    return {"ok": not reasons, "reasons": reasons, "last_run": info}


#: 報告の中で「どこまで」を答える行。夜勤の書式（`tasks.md` が要求する4欄）に合わせる。
_REPORT_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
_REPORT_STATE_RE = re.compile(r"^[-*]\s*\**\s*どこまで\s*\**\s*[:：]\s*(.+?)\s*$")

#: 「片付かなかった」と読む語。⚠ **語で判定するのは脆い**（B188: 記号 `N` 決め打ちで
#: 黙った前例がある）。夜勤の書式が変わったら効かなくなるので、`review()` は
#: 「節は見つかったが状態行が1つも無い」も異常として返す。
_PENDING_WORDS: tuple[str, ...] = ("保留", "できません", "できなかった", "断念", "見送り", "未完")


def pending_items(home: Path, date: str) -> dict[str, Any]:
    """その日の作業報告から「片付かなかった節」を拾う（2026-09-08・主人のご要望）。

    主人は毎朝ご自分で夜勤の様子を執事にお尋ねになり、保留を処理させていました。
    **その往復をなくすのがこの関数の目的**です。報告は `## <見出し>` ごとに
    「どこまで」の1行を持つので、そこだけを読みます——散文全体を `claude` に
    読ませる必要はありません（安く・確実に・毎朝回せる形にする）。
    """
    rep = report(home, date)
    if not rep.get("found"):
        return {"found": False, "date": date, "pending": [], "sections": 0, "states": 0}

    pending: list[dict[str, str]] = []
    heading = ""
    sections = 0
    states = 0
    for line in str(rep.get("text") or "").splitlines():
        m = _REPORT_HEADING_RE.match(line)
        if m:
            heading = m.group(1)
            sections += 1
            continue
        sm = _REPORT_STATE_RE.match(line.strip())
        if not sm:
            continue
        states += 1
        state = sm.group(1)
        if any(w in state for w in _PENDING_WORDS):
            pending.append({"heading": heading, "state": state})
    return {
        "found": True,
        "date": date,
        "pending": pending,
        "sections": sections,
        "states": states,
    }


def _streak_path(home: Path) -> Path:
    return night_dir(Path(home)) / "pending-streak.json"


def review(home: Path, *, date: str | None = None, record: bool = True) -> dict[str, Any]:
    """朝の点検。**走ったか**（`health`）と**何が片付かなかったか**（`pending_items`）を
    1つの答えにし、**同じ保留が何晩続いているか**を数える。

    連続を数えるのは、1晩の保留は普通のこと（時間切れ・順番待ち）だが、
    **3晩続く保留は自力で外れない詰まり**だからです——そこで初めて主人にお伺いする
    価値が出ます。台帳 E13「走ったが何もしていない、を別に見る」と同じ考えで、
    **不在ではなく“変わらなさ”を信号にします**。

    `record=False` なら数えるだけで書きません（下見用）。
    """
    home = Path(home)
    target = date or util.today()
    result: dict[str, Any] = {
        "date": target,
        "health": health(home),
        "items": pending_items(home, target),
        "pause": read_pause(home, today=target),
    }

    path = _streak_path(home)
    try:
        previous = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except Exception:  # noqa: BLE001
        previous = {}
    if not isinstance(previous, dict):
        previous = {}
    old_streaks = previous.get("streaks") if isinstance(previous.get("streaks"), dict) else {}

    # ⚠ **同じ朝に2度回しても増えない**（冪等）。この関数は `manor night review` と
    # `manor slack morning` の両方から呼ばれるので、数え上げが呼び出し回数に依存すると
    # 連続日数が水増しされ、2晩目の朝に「3晩続いた」と主人へ伺いを立ててしまう。
    same_day = previous.get("date") == target
    streaks: dict[str, int] = {}
    for item in result["items"]["pending"]:
        key = str(item["heading"])
        seen = int(old_streaks.get(key, 0))
        streaks[key] = seen if same_day else seen + 1
        item["nights"] = streaks[key]

    result["streaks"] = streaks
    result["stuck"] = sorted(k for k, v in streaks.items() if v >= 3)

    # 書式が変わって読めなくなったことに気づけるように（B188 の再発防止）
    if result["items"]["found"] and result["items"]["sections"] and not result["items"]["states"]:
        result["health"]["reasons"].append(
            "作業報告に「どこまで」の行が1つもありません（報告の書式が変わった可能性）"
        )
        result["health"]["ok"] = False

    if record and previous.get("date") != target:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"date": target, "streaks": streaks}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return result


def format_review(result: dict[str, Any]) -> str:
    """`manor night review` の人が読む形。**異常が先、保留が次**（走ったかのほうが大事）。"""
    lines: list[str] = [f"朝の点検 {result.get('date')}"]
    pause_info = result.get("pause")
    if pause_info:
        lines.append(f"  夜勤: 停止中（〜{pause_info.get('until')}・{pause_info.get('reason')}）")
    health_info = dict(result.get("health") or {})
    reasons = list(health_info.get("reasons") or [])
    if reasons:
        for r in reasons:
            lines.append(f"  注意: {r}")
    else:
        lines.append("  夜勤は正常に終わっています")

    items = dict(result.get("items") or {})
    pending = list(items.get("pending") or [])
    if not items.get("found"):
        lines.append("  作業報告がありません")
    elif not pending:
        lines.append("  片付かなかった件はありません")
    else:
        lines.append(f"  片付かなかった件: {len(pending)}")
        for item in pending:
            nights = item.get("nights")
            tail = f"（{nights}晩連続）" if isinstance(nights, int) and nights > 1 else ""
            lines.append(f"    - {item.get('heading')}{tail}: {item.get('state')}")
    stuck = list(result.get("stuck") or [])
    if stuck:
        lines.append(f"  3晩以上そのまま: {', '.join(stuck)}")
    for did in list(result.get("asked") or []):
        lines.append(f"  {did} として主人にお伺いを立てました")
    return "\n".join(lines)
