"""振る舞いの回帰試験（自然言語 -> DB）を実際に回す。

「AI執事」プロジェクトの `03_design/振る舞いの回帰試験.md`（v1 設計。このリポジトリの
外にある。④環境固有につきパスはここに書かない）の方針を、manor（DB が唯一の書き手）
向けに実装したもの。**文章の一致は見ない。**
`claude -p` を実際に1本流し、**DB の副作用**（task/decision/project/edge/task_event の
増減、射影ファイルの sha256）で判定する。

使い方:
    uv run python tests/behavior/run.py [--only S1,S3] [--repeat 1]
        [--model sonnet] [--max-turns 15] [--keep] [--include-flaky]

このファイルと `scenarios/*.toml` 以外は触らない（pytest からは collect されない
——ファイル名を `test_` で始めていない）。

安全上の注意:
- `MANOR_HOME` を必ず一時フォルダへ向ける（本物の `home/` には一切触れない）。
- `claude -p` の `--allowed-tools` は `Bash(manor:*)` 系と `Read`/`Glob`/`Grep`
  （射影編集を試すシナリオだけ `Edit`）に絞り、`--disallowed-tools` で
  Write/MultiEdit/NotebookEdit と外部送信系（mcp__* / WebFetch / WebSearch /
  Task / SendMessage）を明示的に塞ぐ。`--permission-mode manual` は stream-json
  上では `permissionMode: "default"` として現れる（`claude --help` に "default"
  という選択肢は無い。曖昧だった点として README に記す）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import tomllib
from datetime import datetime
from pathlib import Path

from manor import db as db_mod
from manor import runlog as runlog_mod

REPO_ROOT = Path(__file__).resolve().parents[2]
SCEN_DIR = Path(__file__).resolve().parent / "scenarios"
BEHAVIOR_HOME = REPO_ROOT / "home" / "behavior"
REAL_HOME_DB = REPO_ROOT / "home" / "manor.db"
VENV_SCRIPTS = REPO_ROOT / ".venv" / "Scripts"

DEFAULT_ALLOWED = [
    "Bash(manor:*)",
    "Bash(uv run manor:*)",
    "Bash(uv run --no-sync manor:*)",
    "Read",
    "Glob",
    "Grep",
]
DEFAULT_DISALLOWED = [
    "Write",
    "MultiEdit",
    "NotebookEdit",
    "mcp__*",
    "WebFetch",
    "WebSearch",
    "Task",
    "SendMessage",
    "Edit",
]

WRITE_VERB_RE = re.compile(
    r"\b(task\s+(add|status|set|done|dup|link|unlink)"
    r"|project\s+(add|set)"
    r"|decision\s+(ask|rule)"
    r"|milestone\s+add"
    r"|note\s+add"
    r"|handoff\s+(new|accept|reject))\b"
)
CONTEXT_READ_RE = re.compile(r"\b(ctx\s+T\d+|task\s+show\s+T\d+)\b")


def _posix(p: Path) -> str:
    s = str(p)
    if len(s) >= 2 and s[1] == ":":
        s = "/" + s[0].lower() + s[2:]
    return s.replace("\\", "/")


VENV_SCRIPTS_POSIX = _posix(VENV_SCRIPTS)


# --- シナリオの読み込み -----------------------------------------------------------


def load_scenarios(only: list[str] | None) -> list[dict]:
    ids = sorted(p.stem for p in SCEN_DIR.glob("S*.toml"))
    if only:
        wanted = set(only)
        missing = wanted - set(ids)
        if missing:
            raise SystemExit(f"--only に無いシナリオ: {sorted(missing)}")
        ids = [i for i in ids if i in wanted]
    out = []
    for sid in ids:
        with (SCEN_DIR / f"{sid}.toml").open("rb") as fh:
            data = tomllib.load(fh)
        assert data.get("id") == sid, f"{sid}.toml の id が一致しません: {data.get('id')!r}"
        out.append(data)
    return out


# --- manor CLI / claude CLI の呼び出し ---------------------------------------------


def run_manor(home: Path, args: list[str], *, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["MANOR_HOME"] = str(home)
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv が PATH にありません")
    cmd = [uv, "run", "--no-sync", "manor", *args]
    proc = subprocess.run(
        cmd, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, encoding="utf-8", timeout=timeout
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"manor {' '.join(args)} が失敗しました (rc={proc.returncode}):\n{proc.stdout}\n{proc.stderr}")
    return proc


def run_manor_check(home: Path) -> dict:
    proc = run_manor(home, ["check", "--json"], check=False)
    try:
        return json.loads(proc.stdout)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "results": {}, "_parse_error": str(exc), "_stdout": proc.stdout, "_stderr": proc.stderr}


def run_hook_probe(home: Path, *, tool_name: str, file_path: str) -> dict:
    """`python -m manor.hooks pre-tool-use` を直接叩き、deny されるかを機械的に確かめる。

    LLM が実際に Edit を試みるかどうかとは独立に、hook の機構そのものを検証する。
    """
    env = dict(os.environ)
    env["MANOR_HOME"] = str(home)
    uv = shutil.which("uv")
    payload = json.dumps(
        {
            "tool_name": tool_name,
            "tool_input": {"file_path": file_path},
            "cwd": _posix(REPO_ROOT),
            "permission_mode": "default",
        }
    )
    cmd = [uv, "run", "--no-sync", "python", "-m", "manor.hooks", "pre-tool-use"]
    proc = subprocess.run(
        cmd, cwd=str(REPO_ROOT), env=env, input=payload, capture_output=True, text=True, encoding="utf-8", timeout=30
    )
    raw = (proc.stdout or "").strip()
    denied = False
    parsed: object = None
    if raw:
        try:
            parsed = json.loads(raw)
            denied = parsed.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
    return {"denied": denied, "raw": raw, "stderr": proc.stderr}


def run_claude(
    prompt: str,
    home: Path,
    *,
    allowed_tools: list[str],
    disallowed_tools: list[str],
    model: str,
    max_turns: int,
    timeout: int = 300,
) -> dict:
    exe = shutil.which("claude")
    if exe is None:
        raise RuntimeError("claude CLI が PATH にありません")
    env = dict(os.environ)
    env["MANOR_HOME"] = str(home)
    # CLAUDE.md は `manor task add` のように uv run を介さない裸のコマンドを使う。
    # venv の Scripts を PATH の先頭に足して、nested session の Bash からも
    # `manor ...` が素で解決するようにする（そうしないと command not found になる。
    # 実測で確認した——曖昧だった点として README に記す）。
    env["PATH"] = VENV_SCRIPTS_POSIX + os.pathsep + env.get("PATH", "")
    cmd = [
        exe,
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        model,
        "--max-turns",
        str(max_turns),
        "--permission-mode",
        "manual",
        "--allowed-tools",
        *allowed_tools,
        "--disallowed-tools",
        *disallowed_tools,
    ]
    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, encoding="utf-8", timeout=timeout
        )
        return {
            "timed_out": False,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "returncode": proc.returncode,
            "elapsed": time.time() - t0,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "timed_out": True,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "returncode": None,
            "elapsed": time.time() - t0,
        }


# --- stream-json の解析 -----------------------------------------------------------


def parse_stream(stdout_text: str) -> dict:
    tool_calls: list[dict] = []
    permission_denials: list[dict] = []
    hook_events: list[dict] = []
    response_text = ""
    result_meta: dict = {}
    first_tool_use_raw_line: str | None = None
    for line in stdout_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        t = obj.get("type")
        if t == "assistant":
            content = obj.get("message", {}).get("content", [])
            for block in content:
                if block.get("type") == "tool_use":
                    tool_calls.append({"name": block.get("name", ""), "input": block.get("input", {})})
                    if first_tool_use_raw_line is None:
                        first_tool_use_raw_line = line
        elif t == "system":
            if obj.get("subtype") == "permission_denied":
                permission_denials.append(
                    {
                        "tool_name": obj.get("tool_name"),
                        "reason": obj.get("decision_reason"),
                        "message": obj.get("message"),
                    }
                )
            if obj.get("hook_event") == "PreToolUse":
                hook_events.append(obj)
        elif t == "result":
            response_text = obj.get("result", "") or ""
            result_meta = obj
    return {
        "tool_calls": tool_calls,
        "permission_denials": permission_denials,
        "hook_events": hook_events,
        "response_text": response_text,
        "result_meta": result_meta,
        "first_tool_use_raw_line": first_tool_use_raw_line,
    }


# --- DB スナップショット -----------------------------------------------------------


def snapshot(home: Path) -> dict:
    db_path = home / "manor.db"
    if not db_path.is_file():
        return {"tasks": {}, "decisions": {}, "projects": {}, "edges": [], "events": 0}
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        tasks = {
            r["id"]: dict(r)
            for r in conn.execute("SELECT t.*, n.title AS title FROM task t JOIN node n ON n.id = t.id")
        }
        decisions = {
            r["id"]: dict(r)
            for r in conn.execute("SELECT d.*, n.title AS title FROM decision d JOIN node n ON n.id = d.id")
        }
        projects = {
            r["id"]: dict(r)
            for r in conn.execute("SELECT p.*, n.title AS title FROM project p JOIN node n ON n.id = p.id")
        }
        edges = [dict(r) for r in conn.execute("SELECT * FROM edge")]
        events = conn.execute("SELECT COUNT(*) c FROM task_event").fetchone()["c"]
    finally:
        conn.close()
    return {"tasks": tasks, "decisions": decisions, "projects": projects, "edges": edges, "events": events}


def _id_sort_key(node_id: str) -> int:
    try:
        return int(node_id[1:])
    except ValueError:
        return 0


# --- expect 式の評価 ---------------------------------------------------------------


def build_context(
    *,
    pre: dict,
    post: dict,
    home: Path,
    response_text: str,
    tool_calls: list[dict],
    check_result: dict,
    hook_probe: dict | None,
) -> dict:
    conn = sqlite3.connect(str(home / "manor.db"))
    conn.row_factory = sqlite3.Row

    def task(tid: str) -> dict:
        return post["tasks"].get(tid, {})

    def task_events(tid: str) -> list[dict]:
        rows = conn.execute(
            "SELECT at, from_status, to_status, note, actor FROM task_event WHERE task_id = ? ORDER BY id",
            (tid,),
        ).fetchall()
        return [dict(r) for r in rows]

    new_task_ids = sorted(set(post["tasks"]) - set(pre["tasks"]), key=_id_sort_key)
    new_decision_ids = sorted(set(post["decisions"]) - set(pre["decisions"]), key=_id_sort_key)
    new_project_ids = sorted(set(post["projects"]) - set(pre["projects"]), key=_id_sort_key)

    bash_commands = [tc["input"].get("command", "") for tc in tool_calls if tc["name"] == "Bash"]
    write_commands_attempted = [c for c in bash_commands if WRITE_VERB_RE.search(c)]
    context_read_used = any(CONTEXT_READ_RE.search(c) for c in bash_commands)

    no_write = (
        post["events"] == pre["events"]
        and len(post["tasks"]) == len(pre["tasks"])
        and len(post["decisions"]) == len(pre["decisions"])
        and len(post["projects"]) == len(pre["projects"])
        and len(post["edges"]) == len(pre["edges"])
        and all(pre["tasks"][t]["status"] == post["tasks"].get(t, {}).get("status") for t in pre["tasks"])
    )

    sha_ok = not check_result.get("results", {}).get("C7")
    check_ok = bool(check_result.get("ok"))

    ctx = dict(
        pre=pre,
        post=post,
        task=task,
        task_events=task_events,
        new_task_ids=new_task_ids,
        new_decision_ids=new_decision_ids,
        new_project_ids=new_project_ids,
        response_text=response_text,
        tool_calls=tool_calls,
        bash_commands=bash_commands,
        write_commands_attempted=write_commands_attempted,
        context_read_used=context_read_used,
        no_write=no_write,
        check_result=check_result,
        check_ok=check_ok,
        sha_ok=sha_ok,
        edit_actually_modified_file=not sha_ok,
        len=len,
        any=any,
        all=all,
        sorted=sorted,
        sum=sum,
        bool=bool,
        str=str,
    )
    if hook_probe is not None:
        ctx["hook_probe_denied"] = hook_probe["denied"]
    ctx["_conn"] = conn  # 後始末用（下で閉じる）
    return ctx


def eval_expect(expr: str, ctx: dict) -> tuple[bool, str | None]:
    # globals と locals を分けると、式の中の内包表記・ジェネレータ式（例:
    # `any(... for k in ...)`）の本体からは locals が見えず NameError になる
    # （CPython の既知の挙動）。1つの辞書だけを渡し、両方兼ねさせる。
    ns = {k: v for k, v in ctx.items() if k != "_conn"}
    try:
        result = eval(expr, ns)  # noqa: S307　— シナリオ TOML は全部自分で書いたもの
        return bool(result), None
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


# --- 1シナリオ・1回ぶんの実行 -------------------------------------------------------


def run_one(
    scenario: dict,
    run_index: int,
    *,
    model: str,
    max_turns: int,
    keep: bool,
    out_dir: Path,
) -> dict:
    sid = scenario["id"]
    sandbox = Path(tempfile.mkdtemp(prefix=f"manor_behavior_{sid}_"))
    run_out = out_dir / sid / f"run{run_index}"
    run_out.mkdir(parents=True, exist_ok=True)

    log: list[str] = []
    try:
        run_manor(sandbox, ["init"])
        for step in scenario.get("setup", []):
            run_manor(sandbox, step["args"])

        pre = snapshot(sandbox)

        allow_edit = bool(scenario.get("allow_edit", False))
        if allow_edit:
            allowed = DEFAULT_ALLOWED + ["Edit"]
            disallowed = [t for t in DEFAULT_DISALLOWED if t != "Edit"]
        else:
            allowed = list(DEFAULT_ALLOWED)
            disallowed = list(DEFAULT_DISALLOWED)

        claude_result = run_claude(
            scenario["prompt"],
            sandbox,
            allowed_tools=allowed,
            disallowed_tools=disallowed,
            model=model,
            max_turns=max_turns,
        )
        (run_out / "transcript.jsonl").write_text(claude_result["stdout"], encoding="utf-8")
        if claude_result["stderr"]:
            (run_out / "stderr.txt").write_text(claude_result["stderr"], encoding="utf-8")

        parsed = parse_stream(claude_result["stdout"])
        post = snapshot(sandbox)
        check_result = run_manor_check(sandbox)

        hook_probe = None
        if "hook_probe" in scenario:
            hp = scenario["hook_probe"]
            hook_probe = run_hook_probe(sandbox, tool_name=hp["tool_name"], file_path=hp["file_path"])

        ctx = build_context(
            pre=pre,
            post=post,
            home=sandbox,
            response_text=parsed["response_text"],
            tool_calls=parsed["tool_calls"],
            check_result=check_result,
            hook_probe=hook_probe,
        )
        conn = ctx.pop("_conn")

        expect_results = []
        all_pass = True
        for expr in scenario.get("expect", []):
            ok, err = eval_expect(expr, ctx)
            if not ok:
                all_pass = False
            expect_results.append({"expr": expr, "ok": ok, "error": err})
        conn.close()

        verdict = "TIMEOUT" if claude_result["timed_out"] else ("PASS" if all_pass else "FAIL")

        # ADR-006 D10: 振る舞い試験は各シナリオ1行を `run` 表へ（kind='behavior', ref=<sid>）。
        # **サンドボックス home へ**（本物の MANOR_HOME には一切触れない——このファイル冒頭の
        # 安全上の注意のとおり）。ロギングの失敗で判定そのものを止めない。
        try:
            log_conn = db_mod.connect(sandbox)
            try:
                run_id = runlog_mod.start(log_conn, "behavior", ref=sid, model=model)
                if claude_result["timed_out"]:
                    exit_reason = "timeout"
                    usage, cost, turns = None, None, None
                else:
                    info = runlog_mod.from_claude_result(parsed["result_meta"] or {})
                    exit_reason = info["exit_reason"]
                    usage, cost, turns = info["usage"], info["cost"], info["turns"]
                runlog_mod.finish(
                    log_conn, run_id,
                    usage=usage, cost=cost, turns=turns, exit_reason=exit_reason,
                    note=f"run_index={run_index} verdict={verdict}",
                )
                log_conn.commit()
            finally:
                log_conn.close()
        except Exception:  # noqa: BLE001 - 観測の失敗で振る舞い試験そのものを止めない
            pass

        judgement = {
            "id": sid,
            "run_index": run_index,
            "verdict": verdict,
            "timed_out": claude_result["timed_out"],
            "elapsed_sec": round(claude_result["elapsed"], 1),
            "returncode": claude_result["returncode"],
            "expect_results": expect_results,
            "new_task_ids": ctx["new_task_ids"],
            "new_decision_ids": ctx["new_decision_ids"],
            "tool_calls_summary": [
                {"name": tc["name"], "input": tc["input"]} for tc in parsed["tool_calls"]
            ],
            "bash_commands": ctx["bash_commands"],
            "permission_denials": parsed["permission_denials"],
            "hook_probe": hook_probe,
            "response_text": parsed["response_text"],
            "result_meta": {
                k: parsed["result_meta"].get(k)
                for k in ("total_cost_usd", "num_turns", "duration_ms", "subtype", "is_error")
            },
            "check_result": check_result,
        }
        (run_out / "judgement.json").write_text(
            json.dumps(judgement, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # DB のコピー（*.db / *.db-wal / *.db-shm）を保存する。
        db_copy_dir = run_out / "db_copy"
        db_copy_dir.mkdir(exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            src = sandbox / f"manor.db{suffix}"
            if src.is_file():
                shutil.copy2(src, db_copy_dir / src.name)

        judgement["_first_tool_use_raw_line"] = parsed["first_tool_use_raw_line"]
        return judgement
    finally:
        if not keep:
            shutil.rmtree(sandbox, ignore_errors=True)
        else:
            log.append(f"sandbox 保持: {sandbox}")
            print(f"  [--keep] sandbox: {sandbox}")


# --- 全体オーケストレーション --------------------------------------------------------


def real_home_state() -> dict:
    if REAL_HOME_DB.is_file():
        return {"exists": True, "mtime": REAL_HOME_DB.stat().st_mtime}
    return {"exists": False, "mtime": None}


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="manor 振る舞いの回帰試験")
    ap.add_argument("--only", help="カンマ区切りのシナリオ id（例: S1,S3）")
    ap.add_argument("--repeat", type=int, default=None, help="全シナリオに一律で適用する回数（既定はシナリオごと。S1/S8 は既定3回）")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--max-turns", type=int, default=15)
    ap.add_argument("--keep", action="store_true", help="sandbox 一時フォルダを残す")
    ap.add_argument("--include-flaky", action="store_true", help="ぶれると分かっているシナリオも含める（現状は未使用）")
    args = ap.parse_args(argv)

    only = [s.strip() for s in args.only.split(",")] if args.only else None
    scenarios = load_scenarios(only)

    before = real_home_state()

    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    out_dir = BEHAVIOR_HOME / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    all_judgements: list[dict] = []
    first_tool_use_raw_line: str | None = None

    print(f"=== manor 振る舞いの回帰試験 {ts} ===")
    print(f"model={args.model} max_turns={args.max_turns} 対象={[s['id'] for s in scenarios]}")
    print()

    for scenario in scenarios:
        sid = scenario["id"]
        repeat = args.repeat if args.repeat is not None else int(scenario.get("repeat", 1))
        for run_index in range(1, repeat + 1):
            print(f"--- {sid} run{run_index}/{repeat}: {scenario['prompt']!r}")
            t0 = time.time()
            judgement = run_one(
                scenario,
                run_index,
                model=args.model,
                max_turns=args.max_turns,
                keep=args.keep,
                out_dir=out_dir,
            )
            if first_tool_use_raw_line is None and judgement.get("_first_tool_use_raw_line"):
                first_tool_use_raw_line = judgement["_first_tool_use_raw_line"]
            all_judgements.append(judgement)
            dt = time.time() - t0
            print(f"    -> {judgement['verdict']}（{dt:.1f}秒）")
            for er in judgement["expect_results"]:
                mark = "OK" if er["ok"] else "NG"
                extra = f"  ({er['error']})" if er["error"] else ""
                print(f"       [{mark}] {er['expr']}{extra}")

    after = real_home_state()

    # --- 安定性（S1/S8 が対象に含まれていれば） ---------------------------------
    stability = {}
    for sid in ("S1", "S8"):
        runs = [j for j in all_judgements if j["id"] == sid]
        if len(runs) >= 2:
            verdicts = {j["verdict"] for j in runs}
            stability[sid] = {"runs": len(runs), "verdicts": sorted(verdicts), "stable": len(verdicts) == 1}

    # --- サマリ表 --------------------------------------------------------------
    print()
    print("=== サマリ ===")
    header = f"{'id':<5} {'run':<4} {'verdict':<8} {'秒':>6} {'$':>8} {'turns':>6} {'tools':<40}"
    print(header)
    print("-" * len(header))
    summary_rows = []
    for j in all_judgements:
        tool_names = ",".join(sorted({tc["name"] for tc in j["tool_calls_summary"]})) or "(なし)"
        cost = j["result_meta"].get("total_cost_usd")
        turns = j["result_meta"].get("num_turns")
        row = {
            "id": j["id"],
            "run": j["run_index"],
            "verdict": j["verdict"],
            "elapsed_sec": j["elapsed_sec"],
            "cost_usd": cost,
            "num_turns": turns,
            "tools": tool_names,
        }
        summary_rows.append(row)
        cost_s = f"{cost:.3f}" if isinstance(cost, (int, float)) else "?"
        turns_s = str(turns) if turns is not None else "?"
        print(f"{row['id']:<5} {row['run']:<4} {row['verdict']:<8} {row['elapsed_sec']:>6} {cost_s:>8} {turns_s:>6} {tool_names:<40}")

    print()
    print("=== 安定性（3回一致するか） ===")
    if stability:
        for sid, info in stability.items():
            print(f"{sid}: {info['runs']}回 -> {info['verdicts']} {'(安定)' if info['stable'] else '(ぶれる!)'}")
    else:
        print("（S1/S8 は今回の対象に含まれていません）")

    print()
    print("=== 本物の home/ ===")
    print(f"実行前: {before}")
    print(f"実行後: {after}")
    untouched = before == after
    print("本物の home/manor.db は変化していません。" if untouched else "!!! 本物の home/ が変化しています !!!")

    total_cost = sum(
        j["result_meta"].get("total_cost_usd") or 0
        for j in all_judgements
        if isinstance(j["result_meta"].get("total_cost_usd"), (int, float))
    )
    print()
    print(f"合計費用（概算）: ${total_cost:.3f}")

    summary = {
        "timestamp": ts,
        "model": args.model,
        "max_turns": args.max_turns,
        "rows": summary_rows,
        "stability": stability,
        "real_home_before": before,
        "real_home_after": after,
        "real_home_untouched": untouched,
        "total_cost_usd": total_cost,
        "first_tool_use_raw_line": first_tool_use_raw_line,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print(f"結果一式: {out_dir}")
    if first_tool_use_raw_line:
        print()
        print("=== 1本目の tool_use 生JSON行 ===")
        print(first_tool_use_raw_line)

    any_fail = any(j["verdict"] != "PASS" for j in all_judgements)
    return 1 if any_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
