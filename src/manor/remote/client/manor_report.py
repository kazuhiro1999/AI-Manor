#!/usr/bin/env python3
"""manor_report — 他のPCの Claude Code セッションから manor へ進捗を送る（ADR-025）。

**標準ライブラリだけ**で動く1本のファイル。manor を入れていないPCに置く。

- `hook <SessionStart|UserPromptSubmit|Stop|SessionEnd>`: Claude Code の hook から呼ばれる
  （stdin に hook の JSON）。活動の合図を送り、SessionStart では報告の作法を文脈へ注入し、
  Stop では「作業をしたのに報告していない」ターンを1回だけ止めて報告させる。
- `progress`: セッションの Claude が区切りで呼ぶ（見出し・段階・進捗・主人の次の一手）。
- `install`: 設定と `~/.claude/settings.json` の hooks を書く（何度流しても同じ結果）。
- `flush` / `ping` / `status`: 手元に溜まった分を送る・つながるか確かめる・状態を見る。

**セッションを止めない**: hook は短い時間で諦め、送れなかった分は手元
（`~/.manor-report/outbox.jsonl`）に溜めて次の機会に送る。例外は握りつぶして
`error.log` に1行残す。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

VERSION = 1

#: 段階（閉じた語彙）→ 進捗の既定値。`None` は「直前の値を引き継ぐ」。
PHASES: dict[str, int | None] = {
    "investigating": 10,
    "designing": 25,
    "implementing": 50,
    "fixing": 70,
    "implemented": 75,
    "testing": 85,
    "blocked": None,
    "done": 100,
}
PHASE_LABELS_JA: dict[str, str] = {
    "investigating": "調査中",
    "designing": "設計中",
    "implementing": "実装中",
    "fixing": "修正中",
    "implemented": "実装済",
    "testing": "試験中",
    "blocked": "止まっている",
    "done": "完了",
}

TITLE_MAX = 40
HUMAN_NEXT_MAX = 30
NOTE_MAX = 120

#: hook で待つ時間（秒）。SessionStart は紐づけ表も受け取るので少し長い。
HOOK_TIMEOUT = 2.5
START_TIMEOUT = 5.0
PROGRESS_TIMEOUT = 6.0

#: 1回で一緒に送る溜まりの上限・溜めておく上限。
FLUSH_BATCH = 200
OUTBOX_MAX = 5000

#: Stop の催促: このターンでファイルを変えたか、道具をこの回数以上使ったら「作業をした」。
WORK_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
WORK_TOOL_COUNT = 5
#: 末尾から読む transcript の量（バイト）。長い会話でも最後のターンが入れば足りる。
TRANSCRIPT_TAIL_BYTES = 4 * 1024 * 1024

#: `progress` で session を省いたとき、同じフォルダのセッションをこの時間まで遡って探す。
SESSION_GUESS_HOURS = 12

HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd")
KIND_OF_HOOK = {
    "SessionStart": "session_start",
    "UserPromptSubmit": "prompt",
    "Stop": "stop",
    "SessionEnd": "session_end",
}


# --- 置き場と設定 ---------------------------------------------------------------------------


def base_dir() -> Path:
    override = os.environ.get("MANOR_REPORT_HOME", "").strip()
    return Path(override) if override else Path.home() / ".manor-report"


def config_path() -> Path:
    return base_dir() / "config.json"


def load_config() -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    try:
        cfg = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    for key, env in (("endpoint", "MANOR_REPORT_ENDPOINT"), ("token", "MANOR_REPORT_TOKEN"),
                     ("machine", "MANOR_REPORT_MACHINE")):
        val = os.environ.get(env, "").strip()
        if val:
            cfg[key] = val
    cfg.setdefault("machine", socket.gethostname())
    return cfg


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _log_error(msg: str) -> None:
    try:
        base_dir().mkdir(parents=True, exist_ok=True)
        with (base_dir() / "error.log").open("a", encoding="utf-8") as f:
            f.write(f"{now_iso()} {msg}\n")
    except OSError:
        pass


def _clip(text: object, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


# --- リポジトリの見分け ---------------------------------------------------------------------


def normalize_remote(url: str | None) -> str | None:
    """git の remote を `host/owner/name`（小文字）にそろえる。分からなければ None。

    `https://github.com/o/n.git` / `git@github.com:o/n.git` / `ssh://git@host:22/o/n` は
    どれも同じ鍵になる（同じリポジトリを別の書き方で clone したPCがあっても1つに寄せる）。
    """
    if not url:
        return None
    u = url.strip().rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    m = re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://(?:[^@/]+@)?([^/:]+)(?::\d+)?/(.+)$", u)
    if m:
        return f"{m.group(1)}/{m.group(2)}".lower()
    m = re.match(r"^[^@/\s]+@([^:/\s]+):(.+)$", u)
    if m:
        return f"{m.group(1)}/{m.group(2).lstrip('/')}".lower()
    return None


def repo_key(remote: str | None, cwd: str | None) -> str | None:
    """紐づけに使う鍵。remote があればそれ、無ければ `dir:<フォルダ名>`。"""
    if remote:
        return remote
    if cwd:
        name = Path(cwd).name
        return f"dir:{name.lower()}" if name else None
    return None


def _git(cwd: str, *args: str) -> str | None:
    if not cwd or not Path(cwd).is_dir():
        return None
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        out = subprocess.run(
            ["git", "-C", cwd, *args], capture_output=True, text=True, timeout=1.5,
            encoding="utf-8", errors="replace", **kwargs,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def git_info(cwd: str) -> dict[str, str | None]:
    remote = normalize_remote(_git(cwd, "remote", "get-url", "origin"))
    branch = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    return {"remote": remote, "branch": branch, "key": repo_key(remote, cwd)}


# --- セッションごとの手元の状態 ---------------------------------------------------------------


def _state_path(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id)[:80]
    return base_dir() / "sessions" / f"{safe}.json"


def load_state(session_id: str) -> dict[str, Any]:
    try:
        return json.loads(_state_path(session_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(session_id: str, state: dict[str, Any]) -> None:
    path = _state_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def guess_session(cwd: str) -> str | None:
    """`progress` に --session が無いとき、同じフォルダで最近動いたセッションを選ぶ。"""
    folder = base_dir() / "sessions"
    if not folder.is_dir():
        return None
    target = os.path.normcase(os.path.abspath(cwd))
    best: tuple[float, str] | None = None
    limit = time.time() - SESSION_GUESS_HOURS * 3600
    for p in folder.glob("*.json"):
        try:
            st = json.loads(p.read_text(encoding="utf-8"))
            mtime = p.stat().st_mtime
        except (OSError, ValueError):
            continue
        if mtime < limit or st.get("ended"):
            continue
        scwd = st.get("cwd") or ""
        if scwd and os.path.normcase(os.path.abspath(scwd)) == target:
            if best is None or mtime > best[0]:
                best = (mtime, str(st.get("session_id") or p.stem))
    return best[1] if best else None


# --- 送る ------------------------------------------------------------------------------------


def make_event(kind: str, cfg: dict[str, Any], session_id: str, cwd: str,
               git: dict[str, str | None], report: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "v": VERSION,
        "event_id": uuid.uuid4().hex,
        "kind": kind,
        "at": now_iso(),
        "machine": cfg.get("machine"),
        "session_id": session_id,
        "cwd": cwd,
        "repo": {"remote": git.get("remote"), "branch": git.get("branch"), "key": git.get("key")},
        "report": report,
    }


def call(cfg: dict[str, Any], payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    """中継へ POST する。GAS は 302 で結果の置き場へ回すので、urllib が GET で追う
    （POST の本文は最初の要求で渡し終えている）。認証は本文の `token`（GAS は頭を読めない）。
    """
    endpoint = cfg.get("endpoint")
    if not endpoint or not cfg.get("token"):
        raise RuntimeError("設定がありません（install を先に）")
    body = dict(payload)
    body["token"] = cfg["token"]
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        endpoint, data=data, method="POST",
        headers={"Content-Type": "text/plain;charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 宛先は設定の固定値
        raw = resp.read().decode("utf-8", errors="replace")
    try:
        out = json.loads(raw)
    except ValueError as exc:
        raise RuntimeError(f"中継の応答が JSON ではありません: {raw[:120]!r}") from exc
    if not out.get("ok"):
        raise RuntimeError(f"中継が断りました: {out.get('error')}")
    return out


def _outbox() -> Path:
    return base_dir() / "outbox.jsonl"


class _Lock:
    """溜まりを読み書きする間だけの、ファイルによる簡単な鍵。取れなければ諦める
    （並んだ hook の片方は溜まりに触らず、自分の1件だけを送る）。
    """

    def __init__(self) -> None:
        self.path = base_dir() / "outbox.lock"
        self.held = False

    def __enter__(self) -> "_Lock":
        base_dir().mkdir(parents=True, exist_ok=True)
        for _ in range(5):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                self.held = True
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > 30:  # 落ちた誰かの残り
                        self.path.unlink()
                        continue
                except OSError:
                    pass
                time.sleep(0.05)
        return self

    def __exit__(self, *exc: object) -> None:
        if self.held:
            try:
                self.path.unlink()
            except OSError:
                pass


def _read_outbox() -> list[dict[str, Any]]:
    try:
        lines = _outbox().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    items = []
    for line in lines:
        try:
            items.append(json.loads(line))
        except ValueError:
            continue
    return items


def _write_outbox(items: list[dict[str, Any]]) -> None:
    items = items[-OUTBOX_MAX:]
    path = _outbox()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not items:
        try:
            path.unlink()
        except OSError:
            pass
        return
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), encoding="utf-8")
    os.replace(tmp, path)


def _append_outbox(events: list[dict[str, Any]]) -> None:
    path = _outbox()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def send_events(cfg: dict[str, Any], events: list[dict[str, Any]], timeout: float,
                directory_key: str | None = None) -> tuple[bool, dict[str, Any]]:
    """`events` と手元の溜まりを一緒に送る。送れなければ `events` を溜まりへ足す。

    戻り値: (送れたか, 中継の応答)。
    """
    with _Lock() as lock:
        pending = _read_outbox() if lock.held else []
        batch = pending[:FLUSH_BATCH] + events
        payload: dict[str, Any] = {"op": "events", "events": batch}
        if directory_key:
            payload["directory_key"] = directory_key
        try:
            out = call(cfg, payload, timeout)
        except (OSError, RuntimeError, urllib.error.URLError, ValueError) as exc:
            _log_error(f"送信に失敗（{len(events)}件を溜めます）: {exc}")
            if lock.held:
                _write_outbox(pending + events)
            else:
                _append_outbox(events)
            return False, {}
        if lock.held:
            _write_outbox(pending[FLUSH_BATCH:])
        return True, out


# --- 紐づけ表（SessionStart で受け取る） -----------------------------------------------------


def _directory_cache() -> Path:
    return base_dir() / "directory_cache.json"


def cache_directory(key: str, entry: dict[str, Any] | None) -> None:
    try:
        data = json.loads(_directory_cache().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data[key] = entry
    _directory_cache().parent.mkdir(parents=True, exist_ok=True)
    _directory_cache().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def cached_directory(key: str) -> dict[str, Any] | None:
    try:
        data = json.loads(_directory_cache().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entry = data.get(key)
    return entry if isinstance(entry, dict) else None


def self_command(cfg: dict[str, Any]) -> str:
    """セッションの Claude に打たせるコマンドの頭（Git Bash でも PowerShell でも通る形）。"""
    if cfg.get("command"):
        return str(cfg["command"])
    exe = Path(sys.executable).as_posix()
    script = Path(__file__).resolve().as_posix()
    return f'"{exe}" "{script}"'


def injection_text(cfg: dict[str, Any], session_id: str, git: dict[str, str | None],
                   entry: dict[str, Any] | None) -> str:
    """SessionStart で文脈に入れる文面。Claude が自分の session_id を知らないので、ここで渡す。"""
    lines = [
        "【manor への進捗報告】このセッションは、主人のタスク管理（manor）のダッシュボードに",
        f"映っています（PC: {cfg.get('machine')}・セッション番号: {session_id}）。",
    ]
    where = git.get("remote") or git.get("key") or "（不明）"
    projects = (entry or {}).get("projects") or []
    if projects:
        names = "・".join(f"{p.get('id')} {p.get('name')}" for p in projects)
        lines.append(f"このリポジトリ（{where}）は manor のプロジェクト {names} に紐づいています。")
        tasks = (entry or {}).get("tasks") or []
        if tasks:
            lines.append("未完了のタスク（報告の --task に使う。当たるものが無ければ付けない）:")
            for t in tasks[:15]:
                now = f" — 現在地: {t.get('now')}" if t.get("now") else ""
                lines.append(f"  {t.get('id')} [{t.get('status')}] {t.get('title')}{now}")
    else:
        lines.append(f"このリポジトリ（{where}）は manor でまだどのプロジェクトにも紐づいていません"
                     "（--project・--task は付けずに報告してください。紐づけは執事が主人に伺います）。")
    cmd = self_command(cfg)
    phases = " / ".join(f"{k}={v}" for k, v in PHASE_LABELS_JA.items())
    lines += [
        "",
        "作業の区切り（段階が変わったとき・作業をしたターンを終える前）に、次を1回実行してください:",
        f'  {cmd} progress --session {session_id} --title "<何関連の作業か・{TITLE_MAX}字以内>" '
        '--phase <段階> --progress <0-100> --human-next "<主人が次にすること・'
        f'{HUMAN_NEXT_MAX}字以内。無ければ空>" [--task <番号>] [--note "<一言>"]',
        f"段階: {phases}",
        "見出しは同じ作業の間は変えない。進捗はこの作業全体の到達度。主人の次の一手は、主人の手が要る"
        "こと（動作確認・ビルド・実機テスト・判断など）を具体的に。報告はダッシュボードに出るだけで、",
        "主人への返答の代わりにはなりません。",
    ]
    return "\n".join(lines)


# --- Stop の催促 --------------------------------------------------------------------------


def _read_tail(path: str) -> list[str]:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - TRANSCRIPT_TAIL_BYTES))
            raw = f.read()
    except OSError:
        return []
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if size > TRANSCRIPT_TAIL_BYTES and lines:
        lines = lines[1:]  # 切れた先頭行を捨てる
    return lines


def _is_human_prompt(entry: dict[str, Any]) -> bool:
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain"):
        return False
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        kinds = {c.get("type") for c in content if isinstance(c, dict)}
        return "tool_result" not in kinds and bool(kinds & {"text", "image"})
    return False


def analyze_turn(transcript_path: str | None) -> dict[str, Any]:
    """最後の主人の発言から後で、道具を何回使ったか・ファイルを変えたか・報告したか。"""
    result = {"tools": 0, "edited": False, "reported": False}
    if not transcript_path:
        return result
    entries = []
    for line in _read_tail(transcript_path):
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    start = 0
    for i in range(len(entries) - 1, -1, -1):
        if _is_human_prompt(entries[i]):
            start = i + 1
            break
    for e in entries[start:]:
        if e.get("type") != "assistant" or e.get("isSidechain"):
            continue
        content = (e.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for c in content:
            if not isinstance(c, dict) or c.get("type") != "tool_use":
                continue
            result["tools"] += 1
            name = c.get("name") or ""
            if name in WORK_TOOLS:
                result["edited"] = True
            cmd = json.dumps(c.get("input") or {}, ensure_ascii=False)
            if "manor_report" in cmd and "progress" in cmd:
                result["reported"] = True
    return result


def should_nudge(turn: dict[str, Any], state: dict[str, Any]) -> bool:
    did_work = turn["edited"] or turn["tools"] >= WORK_TOOL_COUNT
    if not did_work:
        return False
    if turn["reported"]:
        return False
    prompt_at = state.get("prompt_at") or ""
    progress_at = state.get("progress_at") or ""
    return not (progress_at and prompt_at and progress_at >= prompt_at)


def nudge_text(cfg: dict[str, Any], session_id: str) -> str:
    return (
        "manor のダッシュボードへの進捗報告がまだです。このターンの作業を反映して、"
        f"`{self_command(cfg)} progress --session {session_id} --title ... --phase ... "
        "--progress ... --human-next ...` を1回実行してから終えてください"
        "（主人への返答をもう一度書く必要はありません）。"
    )


# --- hook ----------------------------------------------------------------------------------


def run_hook(event_name: str, stdin_text: str) -> int:
    cfg = load_config()
    try:
        data = json.loads(stdin_text or "{}")
    except ValueError:
        data = {}
    session_id = str(data.get("session_id") or "")
    if not session_id:
        return 0
    cwd = str(data.get("cwd") or os.getcwd())
    state = load_state(session_id)
    if event_name == "SessionStart" or not state.get("repo"):
        git = git_info(cwd)
    else:
        git = state["repo"]
    state.update({"session_id": session_id, "cwd": cwd, "repo": git})
    kind = KIND_OF_HOOK.get(event_name)
    if kind is None:
        return 0

    if event_name == "Stop":
        if not data.get("stop_hook_active") and cfg.get("nudge", True):
            turn = analyze_turn(data.get("transcript_path"))
            if should_nudge(turn, state):
                save_state(session_id, state)
                print(json.dumps({"decision": "block", "reason": nudge_text(cfg, session_id)},
                                 ensure_ascii=False))
                return 0
        state["stop_at"] = now_iso()
    elif event_name == "UserPromptSubmit":
        state["prompt_at"] = now_iso()
    elif event_name == "SessionEnd":
        state["ended"] = True
    elif event_name == "SessionStart":
        state.setdefault("started_at", now_iso())
        state.pop("ended", None)
    save_state(session_id, state)

    event = make_event(kind, cfg, session_id, cwd, git)
    if event_name == "SessionStart":
        key = git.get("key")
        ok, out = send_events(cfg, [event], START_TIMEOUT, directory_key=key)
        entry = out.get("directory") if ok else None
        if ok and key:
            cache_directory(key, entry if isinstance(entry, dict) else None)
        elif key:
            entry = cached_directory(key)
        ctx = injection_text(cfg, session_id, git, entry if isinstance(entry, dict) else None)
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                 "additionalContext": ctx}}, ensure_ascii=False))
    else:
        send_events(cfg, [event], HOOK_TIMEOUT)
    return 0


# --- progress ------------------------------------------------------------------------------


def build_report(args: argparse.Namespace, previous: dict[str, Any] | None) -> dict[str, Any]:
    prev = previous or {}
    phase = args.phase or prev.get("phase")
    if phase and phase not in PHASES:
        raise SystemExit(f"--phase は次のどれか: {', '.join(PHASES)}")
    progress = args.progress
    if progress is None and args.phase and args.phase != prev.get("phase"):
        progress = PHASES.get(args.phase)
    if progress is None:
        progress = prev.get("progress")
    if progress is None and phase:
        progress = PHASES.get(phase)
    if progress is not None:
        progress = max(0, min(100, int(progress)))
    title = _clip(args.title, TITLE_MAX) if args.title else prev.get("title")
    human_next = (_clip(args.human_next, HUMAN_NEXT_MAX) if args.human_next is not None
                  else prev.get("human_next", ""))
    return {
        "title": title or "",
        "phase": phase or "",
        "progress": progress,
        "human_next": human_next or "",
        "project": args.project or prev.get("project"),
        "task": args.task or prev.get("task"),
        "note": _clip(args.note, NOTE_MAX) if args.note is not None else "",
    }


def cmd_progress(args: argparse.Namespace) -> int:
    cfg = load_config()
    cwd = os.getcwd()
    session_id = args.session or guess_session(cwd)
    if not session_id:
        print("セッション番号が分かりません。--session <番号> を付けてください"
              "（番号はセッション開始時の【manor への進捗報告】にあります）。", file=sys.stderr)
        return 1
    state = load_state(session_id)
    report = build_report(args, state.get("report"))
    git = state.get("repo") or git_info(state.get("cwd") or cwd)
    state.update({"session_id": session_id, "repo": git, "report": report, "progress_at": now_iso()})
    state.setdefault("cwd", cwd)
    save_state(session_id, state)
    event = make_event("progress", cfg, session_id, state.get("cwd") or cwd, git, report)
    ok, _ = send_events(cfg, [event], PROGRESS_TIMEOUT)
    label = PHASE_LABELS_JA.get(report["phase"], report["phase"] or "—")
    pct = "" if report["progress"] is None else f" {report['progress']}%"
    tail = "" if ok else "（中継に届かなかったので手元に溜めました。次の機会に送ります）"
    print(f"報告しました: {report['title']}｜{label}{pct}｜主人の次: {report['human_next'] or 'なし'}{tail}")
    return 0


# --- install / flush / ping / status -------------------------------------------------------


def _hook_command(cfg: dict[str, Any], event: str) -> str:
    return f"{self_command(cfg)} hook {event}"


def merge_hooks(settings: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """`~/.claude/settings.json` の hooks に manor_report の分を足す（前の分は外してから）。"""
    hooks = settings.setdefault("hooks", {})
    for event in HOOK_EVENTS:
        groups = hooks.get(event) or []
        kept = []
        for g in groups:
            inner = [h for h in (g.get("hooks") or []) if "manor_report" not in str(h.get("command", ""))]
            if inner:
                g = dict(g)
                g["hooks"] = inner
                kept.append(g)
        timeout = 15 if event in ("SessionStart", "Stop") else 10
        kept.append({"hooks": [{"type": "command", "command": _hook_command(cfg, event), "timeout": timeout}]})
        hooks[event] = kept
    return settings


def cmd_install(args: argparse.Namespace) -> int:
    base = base_dir()
    base.mkdir(parents=True, exist_ok=True)
    target = base / "manor_report.py"
    here = Path(__file__).resolve()
    if here != target.resolve():
        shutil.copyfile(here, target)
    cfg = load_config()
    cfg.update({"endpoint": args.endpoint, "token": args.token})
    if args.machine:
        cfg["machine"] = args.machine
    exe = Path(sys.executable).resolve().as_posix()
    cfg["command"] = f'"{exe}" "{target.resolve().as_posix()}"'
    config_path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(config_path(), 0o600)
    except OSError:
        pass

    settings_path = Path(args.claude_settings) if args.claude_settings else Path.home() / ".claude" / "settings.json"
    settings: dict[str, Any] = {}
    if settings_path.is_file():
        raw = settings_path.read_text(encoding="utf-8-sig")
        settings = json.loads(raw) if raw.strip() else {}
        shutil.copyfile(settings_path, settings_path.with_name(settings_path.name + ".bak-manor-report"))
    merge_hooks(settings, cfg)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"入れました: {target}")
    print(f"hooks を足しました: {settings_path}（元は .bak-manor-report に控えました）")
    return cmd_ping(args)


def cmd_ping(_args: argparse.Namespace) -> int:
    cfg = load_config()
    try:
        out = call(cfg, {"op": "ping"}, PROGRESS_TIMEOUT)
    except (OSError, RuntimeError, urllib.error.URLError) as exc:
        print(f"つながりません: {exc}", file=sys.stderr)
        return 1
    print(f"つながりました（PC名: {out.get('machine')}）")
    return 0


def cmd_flush(_args: argparse.Namespace) -> int:
    cfg = load_config()
    before = len(_read_outbox())
    ok, _ = send_events(cfg, [], PROGRESS_TIMEOUT)
    after = len(_read_outbox())
    print(f"溜まり {before} 件 → {after} 件" + ("" if ok else "（送れませんでした）"))
    return 0 if ok else 1


def cmd_status(_args: argparse.Namespace) -> int:
    cfg = load_config()
    print(json.dumps({
        "endpoint": bool(cfg.get("endpoint")), "machine": cfg.get("machine"),
        "outbox": len(_read_outbox()), "home": str(base_dir()),
    }, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="manor_report", description="manor へ進捗を送る（ADR-025）")
    sub = parser.add_subparsers(dest="cmd")

    h = sub.add_parser("hook")
    h.add_argument("event", choices=HOOK_EVENTS)

    p = sub.add_parser("progress")
    p.add_argument("--session")
    p.add_argument("--title")
    p.add_argument("--phase", choices=list(PHASES))
    p.add_argument("--progress", type=int)
    p.add_argument("--human-next", dest="human_next")
    p.add_argument("--project")
    p.add_argument("--task")
    p.add_argument("--note")

    i = sub.add_parser("install")
    i.add_argument("--endpoint", required=True)
    i.add_argument("--token", required=True)
    i.add_argument("--machine")
    i.add_argument("--claude-settings", dest="claude_settings")

    sub.add_parser("ping")
    sub.add_parser("flush")
    sub.add_parser("status")
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
    args = build_parser().parse_args(argv)
    if args.cmd == "hook":
        try:
            stdin_text = sys.stdin.buffer.read().decode("utf-8", errors="replace")
            return run_hook(args.event, stdin_text)
        except Exception as exc:  # noqa: BLE001 - hook はセッションを止めない
            _log_error(f"hook {args.event}: {exc!r}")
            return 0
    handlers = {"progress": cmd_progress, "install": cmd_install, "ping": cmd_ping,
                "flush": cmd_flush, "status": cmd_status}
    handler = handlers.get(args.cmd)
    if handler is None:
        build_parser().print_help()
        return 2
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
