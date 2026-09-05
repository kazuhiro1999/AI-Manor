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
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .. import db, runlog, util

# --- 置き場 ------------------------------------------------------------------

NIGHT_DIRNAME = "night"
TASKS_FILE_NAME = "tasks.md"
REPORTS_DIR_NAME = "reports"
LOGS_DIR_NAME = "logs"
LOCK_FILE_NAME = "night.lock"
LAST_RUN_FILE_NAME = "last-run.json"

DEFAULT_DEADLINE = "06:30"
DEFAULT_MIN_MINUTES = 20
DEFAULT_GRACE_MINUTES = 15
DEFAULT_LOCK_MAX_MIN = 180
DEFAULT_MODEL = "sonnet"
DEFAULT_MAX_TURNS = 80
DEFAULT_TASK_NAME = "manor-night"

#: 外部送信の道具は意図的に含めない（POLICY.md の lethal trifecta 判定器）。
#: `--strict-mcp-config` と合わせ、mcp__* / WebFetch / WebSearch / SendMessage 等は
#: そもそも道具立てに無い。
ALLOWED_TOOLS: list[str] = [
    "Read",
    "Glob",
    "Grep",
    "Bash(uv run manor:*)",
    "Bash(uv run --no-sync manor:*)",
    "Bash(manor:*)",
    "Bash(uv run pytest:*)",
    "Bash(git status:*)",
    "Bash(git diff:*)",
    "Bash(git add:*)",
    "Bash(git commit:*)",
    "Edit",
    "Write",
]

#: 何かの拍子に紛れ込んでいないかを試験で機械的に確かめるための禁句リスト。
FORBIDDEN_TOOL_MARKERS: tuple[str, ...] = (
    "mcp__",
    "WebFetch",
    "WebSearch",
    "SendMessage",
    "Task",
    # git は add/commit まで許す（執事の裁定 2026-09-02。v1 の 1タスク1コミットを機能させる）。
    # 外部送信・不可逆な git だけを塞ぐ。
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


def lock_path(home: Path) -> Path:
    return night_dir(home) / LOCK_FILE_NAME


def last_run_path(home: Path) -> Path:
    return night_dir(home) / LAST_RUN_FILE_NAME


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

    `subprocess.Popen.communicate(timeout=...)` を使う——stdin へのプロンプト書き込みと
    stdout/stderr の読み取りを別スレッドでやってくれるので、子がプロンプトを読まなくても
    （このモジュールの試験のモックのように）デッドロックしない。
    """
    popen_kwargs: dict[str, Any] = dict(
        cwd=str(cwd),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if not sys.platform.startswith("win"):
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(argv, **popen_kwargs)
    killed = False
    try:
        stdout, stderr = proc.communicate(input=prompt, timeout=timeout_seconds)
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
        elif is_session_limit(raw):
            runlog.finish(conn, run_id, exit_reason="limit", note=why)
        else:
            runlog.finish(conn, run_id, exit_reason="failed", note=why)
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
) -> dict[str, Any]:
    """`manor night run` の入口。D10: まず戻し、それから消音する。**声の失敗（VOICEVOX 未設定
    含む）で夜勤自体は止めない**——本体（`_run_impl`）は変えず、その前後を薄く包むだけ。
    """
    home = Path(home)
    _voice_restore_safely(home)
    _voice_mute_safely(home)
    try:
        return _run_impl(
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
        )
    finally:
        _voice_restore_safely(home)


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

        attempt = 0
        resumed_from: datetime | None = None
        resumed = False
        final_code: int | None = None
        final_killed = False

        while True:
            attempt += 1
            at = now_at if attempt == 1 else datetime.now()
            left_seconds = (deadline_at - at).total_seconds()
            prompt = build_clock_block(at, deadline_at, grace_minutes, resumed_from) + body

            argv = build_exec_argv(exec_cmd, model=model, max_turns=max_turns)
            env = dict(os.environ)
            env["MANOR_HOME"] = str(home)
            env["MANOR_HOOKS"] = "off"

            run_conn, run_id = _runlog_start(home, ref=deadline_at.strftime("%Y-%m-%d"), model=model)

            timeout_seconds = max(left_seconds + grace_minutes * 60, 1.0)
            child = _run_child(
                argv, cwd=repo_root, env=env, prompt=prompt, timeout_seconds=timeout_seconds
            )
            code = child["code"]
            killed = child["killed"]
            raw = (child["stdout"] or "") + "\n" + (child["stderr"] or "")

            parsed: dict[str, Any] | None = None
            if child["stdout"].strip():
                try:
                    candidate = json.loads(child["stdout"])
                    parsed = candidate if isinstance(candidate, dict) else None
                except Exception:
                    parsed = None

            why_m = _RESULT_FIELD_RE.search(raw)
            why = why_m.group(1) if why_m else "（理由不明）"

            _runlog_finish(run_conn, run_id, code=code, killed=killed, raw=raw, parsed=parsed, why=why)

            if killed:
                log.write(
                    "WARN", f"締切＋猶予 {grace_minutes} 分を過ぎても終わらないため打ち切ります"
                )

            if code == 0 and not killed:
                if isinstance(parsed, dict) and parsed.get("is_error"):
                    log.write("WARN", f"claude がエラーを返しました: {parsed.get('result')}")
                elif isinstance(parsed, dict):
                    log.write("INFO", f"夜勤が完了しました（{parsed.get('num_turns', '?')} ターン）")
                else:
                    log.write("WARN", "結果JSONを解釈できませんでした（作業自体は行われた可能性があります）")
                final_code = code
                final_killed = killed
                break

            log.write("ERROR", f"claude が異常終了しました (exit={code}): {why}")

            reset_at = get_reset_at(why, datetime.now()) if is_session_limit(raw) else None
            if no_resume or attempt >= 2 or reset_at is None:
                final_code = code
                final_killed = killed
                result["status"] = "failed"
                break
            if (deadline_at - reset_at).total_seconds() / 60 < min_minutes:
                log.write(
                    "WARN",
                    f"利用上限。復帰は {reset_at:%H:%M} で締切 {deadline_at:%H:%M} に間に合わない"
                    "ため、今夜はここまで",
                )
                final_code = code
                final_killed = killed
                result["status"] = "failed_no_time"
                break

            wait_sec = int((reset_at - datetime.now()).total_seconds()) + 120
            log.write(
                "WARN",
                f"利用上限に当たりました。{reset_at:%H:%M} の復帰まで "
                f"{max(wait_sec, 0) // 60} 分待って再開します",
            )
            time.sleep(max(wait_sec, 0))
            resumed_from = reset_at
            resumed = True

        if result.get("status") not in ("failed", "failed_no_time"):
            result["status"] = "done"
        result["exit_code"] = final_code
        result["killed"] = final_killed
        result["attempts"] = attempt
        result["resumed"] = resumed
        result["resumed_from"] = resumed_from.isoformat() if resumed_from else None
        return result
    finally:
        result["ended_at"] = datetime.now().isoformat()
        release_lock(home)
        _write_last_run(home, result)


def _write_last_run(home: Path, result: dict[str, Any]) -> None:
    payload = {k: v for k, v in result.items() if k != "preview_lines"}
    try:
        night_dir(home).mkdir(parents=True, exist_ok=True)
        last_run_path(home).write_text(
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
        return {"platform": "windows", "registered": registered, "detail": detail}
    return {"platform": sys.platform, "registered": None, "detail": "このOSでは自動確認していません"}


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

    last_run: dict[str, Any] | None = None
    lrp = last_run_path(home)
    if lrp.is_file():
        try:
            last_run = json.loads(lrp.read_text(encoding="utf-8"))
        except Exception:
            last_run = None

    return {
        "lock": lock_info,
        "last_run": last_run,
        "scheduled": _query_scheduled_task(task_name),
    }


def format_status(data: dict[str, Any]) -> str:
    lines: list[str] = []
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

    sched = data.get("scheduled", {})
    if sched.get("registered") is True:
        lines.append("登録: あり（schtasks）")
    elif sched.get("registered") is False:
        lines.append("登録: なし")
    else:
        lines.append(f"登録: 未確認（{sched.get('detail', '')}）")
    return "\n".join(lines)


# --- install / uninstall（**組んで見せるだけ**。`execute=True` を渡さない限り登録しない） -------


def build_install_command(
    *, at: str, repo_root: Path | None = None, task_name: str = DEFAULT_TASK_NAME
) -> str:
    repo = Path(repo_root) if repo_root else util.repo_root()
    if sys.platform.startswith("win"):
        python_exe = repo / ".venv" / "Scripts" / "python.exe"
        inner = f'"{python_exe}" -m manor.night run'
        tr = f'cmd /c cd /d "{repo}" && {inner}'
        return f'schtasks /Create /SC DAILY /ST {at} /TN "{task_name}" /TR "{tr}" /F'
    python_exe = repo / ".venv" / "bin" / "python"
    hh, mm = at.split(":")
    return (
        "# launchd/cron 雛形（macOS/Linux。schtasks に相当する自動登録は無い。手で組み込む）\n"
        f'{int(mm)} {int(hh)} * * * cd "{repo}" && "{python_exe}" -m manor.night run  '
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
) -> dict[str, Any]:
    cmd = build_install_command(at=at, repo_root=repo_root, task_name=task_name)
    result: dict[str, Any] = {"command": cmd, "executed": False}
    if execute:
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=30  # noqa: S602
        )
        result.update(
            executed=True, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr
        )
    return result


def uninstall(*, execute: bool = False, task_name: str = DEFAULT_TASK_NAME) -> dict[str, Any]:
    cmd = build_uninstall_command(task_name=task_name)
    result: dict[str, Any] = {"command": cmd, "executed": False}
    if execute:
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=30  # noqa: S602
        )
        result.update(
            executed=True, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr
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
