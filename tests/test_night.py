"""夜勤（`manor.night`）: v1 `night-shift` の移植。**`claude` は呼ばない。**

`--exec` で本物の `claude -p` を差し替えられる作りなので、ここでは `python -c` の無害な
コマンド、または小さなマーカースクリプトだけを走らせる。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from manor import cli
from manor import db as db_mod
from manor import runlog as runlog_mod
from manor import voice as voice_mod
from manor.night import runner


# --- 時計（純粋関数） ----------------------------------------------------------------


def test_get_deadline_at_same_day():
    base = datetime(2026, 8, 29, 2, 0)
    assert runner.get_deadline_at(base, "06:30") == datetime(2026, 8, 29, 6, 30)


def test_get_deadline_at_rolls_to_next_day_when_already_past():
    base = datetime(2026, 8, 29, 7, 0)
    assert runner.get_deadline_at(base, "06:30") == datetime(2026, 8, 30, 6, 30)


def test_get_deadline_at_exact_match_counts_as_past():
    base = datetime(2026, 8, 29, 6, 30)
    assert runner.get_deadline_at(base, "06:30") == datetime(2026, 8, 30, 6, 30)


def test_get_deadline_at_rejects_bad_format():
    with pytest.raises(ValueError):
        runner.get_deadline_at(datetime(2026, 8, 29), "6h30")


def test_get_reset_at_am():
    base = datetime(2026, 8, 29, 2, 0)
    got = runner.get_reset_at("You've hit your session limit · resets 3:40am (Etc/GMT-9)", base)
    assert got == datetime(2026, 8, 29, 3, 40)


def test_get_reset_at_without_minutes():
    base = datetime(2026, 8, 29, 2, 0)
    assert runner.get_reset_at("resets 11pm", base) == datetime(2026, 8, 29, 23, 0)


def test_get_reset_at_rolls_to_next_day_when_past():
    base = datetime(2026, 8, 29, 2, 0)
    assert runner.get_reset_at("resets 1am", base) == datetime(2026, 8, 30, 1, 0)


def test_get_reset_at_unreadable_returns_none():
    assert runner.get_reset_at("Overloaded", datetime(2026, 8, 29)) is None
    assert runner.get_reset_at("", datetime(2026, 8, 29)) is None


def test_is_session_limit_detects_429():
    assert runner.is_session_limit('{"api_error_status":429}') is True


def test_is_session_limit_detects_wording():
    assert runner.is_session_limit("You've hit your session limit") is True


def test_is_session_limit_false_for_ordinary_failure():
    assert runner.is_session_limit('{"is_error":true,"result":"boom"}') is False


def test_count_task_lines_counts_alpha_numeric_rows():
    body = "\n".join(
        [
            "# 今夜の作業指示",
            "説明文はここ",
            "| N1 | タスクA | 30分 |",
            "| M3 | タスクB | 20分 |",
            "| ~~N0~~ | 済のタスク | - |",
            "普通の行はカウントしない",
        ]
    )
    assert runner.count_task_lines(body) == 3


def test_count_task_lines_empty_body_is_zero():
    assert runner.count_task_lines("") == 0
    assert runner.count_task_lines("# 今夜の作業指示\n\n（何も無い）\n") == 0


def test_build_clock_block_contains_now_deadline_remaining():
    at = datetime(2026, 8, 29, 2, 0)
    until = datetime(2026, 8, 29, 6, 30)
    block = runner.build_clock_block(at, until, 15, None)
    assert "現在: 2026-08-29" in block
    assert "02:00" in block
    assert "締切: 06:30" in block
    assert "残り 270 分" in block
    assert "これは再開です" not in block


def test_build_clock_block_resume_note_when_resumed():
    at = datetime(2026, 8, 29, 4, 0)
    until = datetime(2026, 8, 29, 6, 30)
    block = runner.build_clock_block(at, until, 15, datetime(2026, 8, 29, 3, 40))
    assert "これは再開です" in block
    assert "書きかけがあればそこから" in block


# --- allowed-tools（外部送信系が無いこと） ---------------------------------------------


def test_default_exec_argv_has_no_external_send_tools():
    """禁句が**許可の側**へ紛れ込んでいないこと。

    ⚠ 2026-09-09 に意味が変わった。それまでは argv 全体を1本の文字列にして禁句を
    探していたが、主人の裁定（D11）で `--disallowed-tools` を足したので、
    **`git push` は塞ぐ側に「意図的に」現れる**。全体を見ると、正しく塞いだ日に赤くなる。
    見るのは許可の側だけにし、塞ぐ側は次の試験で「入っていること」を確かめる。
    """
    argv = runner.default_exec_argv()
    allowed = argv[argv.index("--allowed-tools") + 1 : argv.index("--disallowed-tools")]
    joined = " ".join(allowed)
    for marker in runner.FORBIDDEN_TOOL_MARKERS:
        assert marker not in joined, f"許可リストに紛れ込んでいます: {marker}"
    assert "--strict-mcp-config" in argv
    assert "--permission-mode" in argv and "dontAsk" in argv


def test_default_exec_argv_blocks_the_human_gate_tools():
    """HG に当たるものが**塞ぐ側に必ずある**こと（主人の裁定 D11・2026-09-09）。

    > 夜間タスクも特にツールを制限する必要性はない。git push などの HG さえ不正であれば問題ない。

    ⚠ **「命じてある」だけでは歯止めになりません。** `tasks.md` に「外部送信はしない」と
    書いてあっても、道具が手元にあれば通ります（台帳 E9・B174 で執事自身が指摘した形）。
    ここが空になった日は、その歯止めが文章だけに戻った日です。
    """
    argv = runner.default_exec_argv()
    blocked = " ".join(argv[argv.index("--disallowed-tools") + 1 :])
    for must in ("git push", "git reset --hard", "curl", "rm -rf"):
        assert must in blocked, f"塞ぐ側から消えています: {must}"


def test_night_can_reach_the_web_now():
    """外部の視点（`tasks.md` N2①）を回すのに要る道具が手元にあること。

    ⚠ 2026-09-09 の夜勤が「**手段が無い**」と報告して初めて分かった——毎晩やれと
    命じていた仕事が、道具立てと真正面から矛盾していた（T39 → D12）。
    主人の裁定「夜間タスクも Web サーチなどの道具を使用可にする」。
    """
    argv = runner.default_exec_argv()
    allowed = argv[argv.index("--allowed-tools") + 1 : argv.index("--disallowed-tools")]
    assert "WebSearch" in allowed
    assert "WebFetch" in allowed


def test_build_exec_argv_uses_exec_override_wholesale():
    argv = runner.build_exec_argv('python -c "print(1)"', model="sonnet", max_turns=80)
    assert argv[0] in ("python", sys.executable) or argv[0].endswith("python") or argv[0].endswith(
        "python.exe"
    )
    assert "--allowed-tools" not in argv  # 丸ごと置き換わっている


# --- ロック ------------------------------------------------------------------------


def test_acquire_lock_then_reject_second(home_path: Path):
    first = runner.acquire_lock(home_path)
    assert first["ok"] is True
    second = runner.acquire_lock(home_path)
    assert second["ok"] is False
    assert "先行実行が動作中" in second["reason"]


def test_acquire_lock_discards_dead_pid(home_path: Path):
    lp = runner.lock_path(home_path)
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text("999999999", encoding="utf-8")  # まず存在しない PID

    result = runner.acquire_lock(home_path)

    assert result["ok"] is True
    assert result["discarded"] is not None
    assert lp.read_text(encoding="utf-8").strip().isdigit()


def test_release_lock_removes_file(home_path: Path):
    runner.acquire_lock(home_path)
    assert runner.lock_path(home_path).is_file()
    runner.release_lock(home_path)
    assert not runner.lock_path(home_path).is_file()


def test_run_returns_locked_status_when_already_locked(home_path: Path):
    runner.acquire_lock(home_path)  # 自分自身のPIDで先行ロックを作る（生存している）
    (runner.night_dir(home_path) / "tasks.md").parent.mkdir(parents=True, exist_ok=True)
    (runner.night_dir(home_path) / "tasks.md").write_text("| N1 | x | 10分 |\n", encoding="utf-8")

    result = runner.run(home_path, exec_cmd='python -c "print(1)"', now="01:00")

    assert result["status"] == "locked"
    runner.release_lock(home_path)  # 後始末


# --- 起動の門 ------------------------------------------------------------------------


def _write_tasks(home_path: Path, body: str = "| N1 | 例のタスク | 30分 |\n") -> None:
    p = runner.tasks_path(home_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def test_run_does_not_launch_when_remain_below_min_minutes(home_path: Path, tmp_path: Path):
    _write_tasks(home_path)
    marker = tmp_path / "launched.txt"
    exec_cmd = _marker_command(marker)

    result = runner.run(
        home_path,
        now="06:20",
        deadline="06:30",
        min_minutes=20,
        exec_cmd=exec_cmd,
        echo=False,
    )

    assert result["status"] == "too_late"
    assert not marker.exists()


def test_run_launches_when_remain_meets_min_minutes(home_path: Path, tmp_path: Path):
    _write_tasks(home_path)
    marker = tmp_path / "launched.txt"
    exec_cmd = _marker_command(marker, exit_code=0, result_text="done")

    result = runner.run(
        home_path,
        now="06:00",
        deadline="06:30",
        min_minutes=20,
        exec_cmd=exec_cmd,
        echo=False,
    )

    assert result["status"] == "done"
    assert marker.exists()


# --- tasks.md が空 -------------------------------------------------------------------


def test_run_does_nothing_when_tasks_missing(home_path: Path, tmp_path: Path):
    marker = tmp_path / "launched.txt"
    exec_cmd = _marker_command(marker)

    result = runner.run(home_path, now="01:00", exec_cmd=exec_cmd, echo=False)

    assert result["status"] == "empty"
    assert not marker.exists()


def test_run_does_nothing_when_tasks_empty_body(home_path: Path, tmp_path: Path):
    _write_tasks(home_path, "# 今夜の作業指示\n\n（何も無い）\n")
    marker = tmp_path / "launched.txt"
    exec_cmd = _marker_command(marker)

    result = runner.run(home_path, now="01:00", exec_cmd=exec_cmd, echo=False)

    assert result["status"] == "empty"
    assert not marker.exists()


# --- dry-run -------------------------------------------------------------------------


def test_run_dry_run_shows_clock_and_command_without_launching(home_path: Path, tmp_path: Path):
    _write_tasks(home_path)
    marker = tmp_path / "launched.txt"
    exec_cmd = _marker_command(marker)

    result = runner.run(
        home_path,
        now="01:00",
        deadline="06:30",
        dry_run=True,
        exec_cmd=exec_cmd,
        echo=False,
    )

    assert result["status"] == "dry_run"
    assert not marker.exists()
    preview = "\n".join(result["preview_lines"])
    assert "現在:" in preview
    assert "締切:" in preview
    assert "残り" in preview
    assert result["command"] == runner.build_exec_argv(exec_cmd, model="sonnet", max_turns=80)


# --- 打ち切り（締切＋猶予） ------------------------------------------------------------


def test_run_child_kills_slow_process_on_timeout(tmp_path: Path):
    argv = [sys.executable, "-c", "import time; time.sleep(30)"]
    result = runner._run_child(
        argv, cwd=tmp_path, env=_child_env(tmp_path), prompt="", timeout_seconds=1
    )
    assert result["killed"] is True
    assert result["code"] == 124


def test_run_child_returns_fast_process_result(tmp_path: Path):
    argv = [sys.executable, "-c", "print('{\"is_error\": false, \"num_turns\": 2}')"]
    result = runner._run_child(
        argv, cwd=tmp_path, env=_child_env(tmp_path), prompt="", timeout_seconds=30
    )
    assert result["killed"] is False
    assert result["code"] == 0
    assert "num_turns" in result["stdout"]


def test_run_marks_status_failed_when_child_is_killed(home_path: Path, monkeypatch: pytest.MonkeyPatch):
    """打ち切られた子の扱い（status=failed・last-run に印）を**決定的に**確かめる。

    以前は本物の子プロセス（30秒眠る）を1秒の締切で殺していたが、**同じ機械で他の重い
    処理が走っていると子の起動そのものが不安定**で、3度にわたって偽陽性を出した
    （`butler/GROWTH.md` G3 の関門の揺れと同じ型——確率的なものを判定の根拠にしない）。
    打ち切りの**判定**はここで決定的に、打ち切りの**機構**（本当に殺せるか）は下の
    `test_run_child_kills_a_child_that_overruns` で別に見る。
    """
    _write_tasks(home_path)

    def fake_child(argv, *, cwd, env, prompt, timeout_seconds):  # noqa: ANN001
        return {"code": 124, "killed": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(runner, "_run_child", fake_child)

    result = runner.run(
        home_path,
        now="2026-08-29T02:00:00",
        deadline="06:30",
        min_minutes=0,
        grace_minutes=0,
        exec_cmd="dummy",
        no_resume=True,
        echo=False,
    )

    assert result["killed"] is True
    assert result["status"] == "failed"
    last_run = json.loads(runner.last_run_path(home_path).read_text(encoding="utf-8"))
    assert last_run["killed"] is True


def test_run_child_kills_a_child_that_overruns(tmp_path: Path):
    """打ち切りの**機構**だけを、小さく速く確かめる（本物の子プロセス1本・0.5秒）。

    上の試験と役割が違う: こちらは「本当に殺せるか」だけを見る。夜勤の組み立て
    （締切の計算・記録・再開）は通さないので、失敗したときに疑う場所が1つに絞れる。
    """
    child = runner._run_child(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        env=dict(os.environ),
        prompt="",
        timeout_seconds=0.5,
    )
    assert child["killed"] is True
    assert child["code"] == 124


# --- MANOR_HOOKS=off が子の環境に入る ---------------------------------------------------


def test_run_sets_manor_hooks_off_in_child_env(home_path: Path, tmp_path: Path):
    _write_tasks(home_path)
    dump = tmp_path / "env.json"
    script = tmp_path / "dump_env.py"
    script.write_text(
        "import json, os, sys, pathlib\n"
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({'MANOR_HOOKS': os.environ.get('MANOR_HOOKS'),"
        " 'MANOR_HOME': os.environ.get('MANOR_HOME')}))\n"
        "print('{\"is_error\": false, \"num_turns\": 1}')\n",
        encoding="utf-8",
    )
    exec_cmd = f'"{sys.executable}" "{script}" "{dump}"'

    result = runner.run(home_path, now="01:00", exec_cmd=exec_cmd, echo=False)

    assert result["status"] == "done"
    env_seen = json.loads(dump.read_text(encoding="utf-8"))
    assert env_seen["MANOR_HOOKS"] == "off"
    assert env_seen["MANOR_HOME"] == str(home_path)


# --- ログ・last-run.json が残る -------------------------------------------------------


def test_run_writes_log_and_last_run_json(home_path: Path):
    _write_tasks(home_path)
    exec_cmd = f'"{sys.executable}" -c "print(1)"'

    result = runner.run(home_path, now="01:00", exec_cmd=exec_cmd, echo=False)

    assert result["status"] == "done"
    log_files = list(runner.logs_dir(home_path).glob("*.log"))
    assert len(log_files) == 1
    assert log_files[0].read_text(encoding="utf-8").strip() != ""

    last_run = json.loads(runner.last_run_path(home_path).read_text(encoding="utf-8"))
    assert last_run["status"] == "done"
    assert "started_at" in last_run and "ended_at" in last_run


# --- 歯止め（試験・コミット）を機構で確かめる（T40） --------------------------------


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, capture_output=True, check=True)
    # `home_path` fixture が `tmp_path/home` を作るので、実物のリポジトリと同じく無視する
    # （さもないと `home/manor.db` 等が untracked として拾われ、試験が実態と合わなくなる）。
    (path / ".gitignore").write_text("home/\n", encoding="utf-8")
    (path / "README.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, capture_output=True, check=True)


def test_check_gate_is_quiet_when_nothing_changed(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)

    gate = runner.check_gate(tmp_path)

    assert gate["uncommitted"] == []
    assert gate["tests"] is None


def test_check_gate_skips_tests_when_only_non_code_files_changed(tmp_path: Path) -> None:
    """`docs/` だけの変更では pytest を走らせない（重いので、対象外の変更で待たせない）。"""
    _init_git_repo(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "note.md").write_text("y", encoding="utf-8")

    gate = runner.check_gate(tmp_path)

    assert gate["uncommitted"]
    assert gate["tests"] is None


def test_check_gate_runs_tests_when_src_changed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "x.py").write_text("1", encoding="utf-8")
    captured: dict = {}
    real_run = subprocess.run

    def fake_run(argv, **kwargs):
        if argv[0] == "uv":
            captured["argv"] = argv
            return subprocess.CompletedProcess(argv, 1, stdout="FAILED tests\n", stderr="")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    gate = runner.check_gate(tmp_path)

    assert gate["uncommitted"]
    assert captured["argv"][:2] == ["uv", "run"]
    assert gate["tests"]["exit_code"] == 1
    assert "FAILED" in gate["tests"]["tail"]


def test_run_includes_gate_only_when_asked(home_path: Path, tmp_path: Path) -> None:
    """既定（`check_gate_after=False`）では走らない——**テストの安全側**。
    `runner.run()` の `repo_root` は既定で本物の開発リポジトリを指すので、これが既定で
    有効だと、この関数を呼ぶだけの試験（本ファイルの大半）が本物の pytest を子として
    起動してしまう（自己言及ループの手前）。CLI 側（`night/__init__.py`）だけが明示的に
    `True` を渡す。"""
    _write_tasks(home_path)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)
    exec_cmd = _marker_command(tmp_path / "launched.txt", exit_code=0, result_text="done")

    quiet = runner.run(
        home_path, repo_root=repo_root, now="06:00", deadline="06:30", min_minutes=20,
        exec_cmd=exec_cmd, echo=False,
    )
    assert "gate" not in quiet

    asked = runner.run(
        home_path, repo_root=repo_root, now="06:00", deadline="06:30", min_minutes=20,
        exec_cmd=exec_cmd, echo=False, check_gate_after=True,
    )
    assert asked["gate"]["uncommitted"] == []
    assert asked["gate"]["tests"] is None


# --- run 表への記録（ADR-006 D10） ---------------------------------------------------


def _run_rows(home_: Path) -> list[dict]:
    conn = db_mod.connect(home_)
    try:
        return runlog_mod.list_runs(conn, kind="night")
    finally:
        conn.close()


def test_run_writes_run_table_row_on_success(home: Path, tmp_path: Path):
    """`db.init()` 済みの home（`home` fixture）で夜勤を1本回すと、`run` 表に
    kind='night' の1行が入り、usage/cost/turns/exit_reason が埋まる
    （ADR-006 §3 検証3: `python -m manor.night run --exec <...>` の型）。
    """
    _write_tasks(home)
    script = tmp_path / "fake_claude.py"
    script.write_text(
        "import json\n"
        "print(json.dumps({\n"
        "    'is_error': False, 'num_turns': 3, 'total_cost_usd': 0.02,\n"
        "    'usage': {'input_tokens': 10, 'output_tokens': 5},\n"
        "}))\n",
        encoding="utf-8",
    )
    exec_cmd = f'"{sys.executable}" "{script}"'

    result = runner.run(home, now="01:00", exec_cmd=exec_cmd, echo=False)

    assert result["status"] == "done"
    rows = _run_rows(home)
    # **席ごとに1行**（2026-09-11 の作り直し。それまでは一晩＝1回だったので1行だった）。
    # この偽の `claude` は何も宣言しないので、運転側が2席目を設けて空回りを見切る——
    # 数を固定せず、**1席目の中身**を見る（ADR-006 §3 検証3 が見たいのはそちら）。
    assert rows, "run 表に1行も入っていない"
    row = rows[-1]
    assert row["exit_reason"] == "done"
    assert row["turns"] == 3
    assert row["cost_usd"] == 0.02
    assert row["input_tokens"] == 10
    assert row["output_tokens"] == 5
    assert row["ended_at"] is not None
    assert row["ref"]  # 締切の日付が入る


def test_run_writes_run_table_row_with_killed_exit_reason(home: Path):
    _write_tasks(home)
    exec_cmd = f'"{sys.executable}" -c "import time; time.sleep(30)"'

    result = runner.run(
        home,
        now="2026-08-29T06:29:59",
        deadline="06:30",
        min_minutes=0,
        grace_minutes=0,
        exec_cmd=exec_cmd,
        no_resume=True,
        echo=False,
    )

    assert result["killed"] is True
    rows = _run_rows(home)
    assert len(rows) == 1
    assert rows[0]["exit_reason"] == "killed"


def test_run_without_db_schema_does_not_crash(home_path: Path):
    """`db.init()` を経ていない home（`run` 表が無い）でも夜勤そのものは動く
    （観測の失敗は実行を止めない。ADR-006 D10 の実装メモ）。"""
    _write_tasks(home_path)
    exec_cmd = f'"{sys.executable}" -c "print(1)"'

    result = runner.run(home_path, now="01:00", exec_cmd=exec_cmd, echo=False)

    assert result["status"] == "done"  # 例外を投げずに完走した（スキーマ無しでも夜勤は止まらない）


# --- 消音（ADR-008 D10: 夜勤は「戻す機会を3つ」持つ） ----------------------------------------


def test_run_mutes_with_by_night_flag_and_restores_in_finally(home_path: Path, tmp_path: Path):
    _write_tasks(home_path)
    exec_cmd = _marker_command(tmp_path / "launched.txt", exit_code=0, result_text="done")

    assert voice_mod.state(home_path) == {"muted": False, "muted_by_night": False}

    result = runner.run(
        home_path, now="06:00", deadline="06:30", min_minutes=20, exec_cmd=exec_cmd, echo=False
    )

    assert result["status"] == "done"
    # 正常終了の finally で restore() が呼ばれ、両方 False に戻っている
    assert voice_mod.state(home_path) == {"muted": False, "muted_by_night": False}


def test_run_mutes_by_night_during_the_run(home_path: Path, tmp_path: Path, monkeypatch):
    """本体（`_run_impl`）の実行中は `muted_by_night=True` になっている（`finally` の
    `restore()` で戻る前の状態を、`_run_impl` をモックして覗き見る）。"""
    seen: dict[str, object] = {}

    def fake_run_impl(home, **kwargs):  # noqa: ANN001, ANN003
        seen["state_during_run"] = voice_mod.state(home)
        return {"status": "done"}

    monkeypatch.setattr(runner, "_run_impl", fake_run_impl)

    runner.run(home_path)

    assert seen["state_during_run"] == {"muted": True, "muted_by_night": True}
    # run() を抜けたあとは restore() で戻っている
    assert voice_mod.state(home_path) == {"muted": False, "muted_by_night": False}


def test_run_restores_voice_when_body_raises(home_path: Path, monkeypatch):
    """本体が例外を投げても、`finally` の `restore()` は必ず走る（消音していなかった
    状態からは、元どおり消音していない状態に戻る）。"""

    def _boom(home, **kwargs):  # noqa: ANN001, ANN003
        raise RuntimeError("boom")

    monkeypatch.setattr(runner, "_run_impl", _boom)

    with pytest.raises(RuntimeError):
        runner.run(home_path)

    assert voice_mod.state(home_path) == {"muted": False, "muted_by_night": False}


# --- D9 の不変条件: 主人が自分で消した消音は、夜勤をまたいでも決して戻さない -------------------
#
# 夜勤の `mute(by_night=True)` は「既に手動で消音されている」ときは `muted_by_night` を
# 立てない（`voice.mute` 側の分岐。ADR-008 D9）。だから `finally` の `restore()` は
# 「何もしない」になり、手動消音がそのまま残る。次の3本で固定する。


def test_run_preserves_manual_mute_across_a_run(home_path: Path, tmp_path: Path):
    """1. 手動 off → 夜勤が回る → 夜勤の finally の restore() のあとも muted=True のまま。"""
    _write_tasks(home_path)
    voice_mod.mute(home_path)  # 主人が手で消音（by_night=False）
    exec_cmd = _marker_command(tmp_path / "launched.txt", exit_code=0, result_text="done")

    result = runner.run(
        home_path, now="06:00", deadline="06:30", min_minutes=20, exec_cmd=exec_cmd, echo=False
    )

    assert result["status"] == "done"
    assert voice_mod.state(home_path) == {"muted": True, "muted_by_night": False}


def test_run_mutes_by_night_only_when_not_already_muted(home_path: Path, monkeypatch):
    """2. 消音していない状態 → 夜勤が回る → 夜勤中は muted=True かつ muted_by_night=True、
    終了後は muted=False（`test_run_mutes_by_night_during_the_run` と同じ検証を、
    D9 の不変条件の対比として明示的にここにも置く）。"""
    seen: dict[str, object] = {}

    def fake_run_impl(home, **kwargs):  # noqa: ANN001, ANN003
        seen["state_during_run"] = voice_mod.state(home)
        return {"status": "done"}

    monkeypatch.setattr(runner, "_run_impl", fake_run_impl)

    runner.run(home_path)

    assert seen["state_during_run"] == {"muted": True, "muted_by_night": True}
    assert voice_mod.state(home_path) == {"muted": False, "muted_by_night": False}


def test_run_preserves_manual_mute_when_body_raises(home_path: Path, monkeypatch):
    """3. 手動 off → 夜勤が例外で落ちる → やはり muted=True のまま。"""
    voice_mod.mute(home_path)  # 主人が手で消音（by_night=False）

    def _boom(home, **kwargs):  # noqa: ANN001, ANN003
        raise RuntimeError("boom")

    monkeypatch.setattr(runner, "_run_impl", _boom)

    with pytest.raises(RuntimeError):
        runner.run(home_path)

    assert voice_mod.state(home_path) == {"muted": True, "muted_by_night": False}


def test_run_survives_voice_module_failure(home_path: Path, tmp_path: Path, monkeypatch):
    """声の機構がまるごと壊れていても（例: `voice.mute` が例外を投げる）、夜勤は止まらない。"""
    _write_tasks(home_path)
    exec_cmd = _marker_command(tmp_path / "launched.txt", exit_code=0, result_text="done")

    def _boom(home, **kwargs):  # noqa: ANN001, ANN003
        raise RuntimeError("voice が壊れている")

    monkeypatch.setattr(voice_mod, "mute", _boom)
    monkeypatch.setattr(voice_mod, "restore", _boom)

    result = runner.run(
        home_path, now="06:00", deadline="06:30", min_minutes=20, exec_cmd=exec_cmd, echo=False
    )

    assert result["status"] == "done"  # 声が壊れていても夜勤自体は完走する


# --- status ----------------------------------------------------------------------


def test_status_reports_no_lock_and_no_last_run(home_path: Path):
    data = runner.status(home_path)
    assert data["lock"]["locked"] is False
    assert data["last_run"] is None


def test_status_reports_last_run_after_a_run(home_path: Path):
    _write_tasks(home_path)
    runner.run(home_path, now="01:00", exec_cmd=f'"{sys.executable}" -c "print(1)"', echo=False)

    data = runner.status(home_path)
    assert data["last_run"]["status"] == "done"


# --- pause / resume（N8: 一時停止の仕組み） ------------------------------------------


def test_read_pause_is_none_when_never_paused(home_path: Path):
    assert runner.read_pause(home_path) is None


def test_pause_then_read_pause_returns_it(home_path: Path):
    runner.pause(home_path, until="2026-09-20", reason="主人のご指示")
    data = runner.read_pause(home_path, today="2026-09-15")
    assert data is not None
    assert data["until"] == "2026-09-20"
    assert data["reason"] == "主人のご指示"


def test_read_pause_includes_the_until_day_itself(home_path: Path):
    runner.pause(home_path, until="2026-09-15", reason="x")
    assert runner.read_pause(home_path, today="2026-09-15") is not None


def test_read_pause_auto_clears_the_day_after_until(home_path: Path):
    runner.pause(home_path, until="2026-09-14", reason="x")
    assert runner.read_pause(home_path, today="2026-09-15") is None


def test_pause_rejects_bad_date_format(home_path: Path):
    with pytest.raises(ValueError):
        runner.pause(home_path, until="9/20", reason="x")


def test_cli_pause_tonight_resolves_to_tomorrow(home_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    """T75: 夜勤は深夜に走るので「今夜だけ」止めるには翌日の日付を until にする必要がある
    （2026-09-20に主人が --until 当日を指定して今夜が止まらなかった）。--tonight はそれを
    日付計算せずに指定できる口。
    """
    monkeypatch.setenv("MANOR_TODAY", "2026-09-20")
    assert cli.main(["night", "pause", "--tonight", "--reason", "x", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["until"] == "2026-09-21"


def test_cli_pause_requires_until_or_tonight(home_path: Path):
    with pytest.raises(SystemExit):
        cli.main(["night", "pause", "--reason", "x"])


def test_resume_clears_pause_before_until(home_path: Path):
    runner.pause(home_path, until="2026-09-20", reason="x")
    result = runner.resume(home_path)
    assert result["was_paused"] is True
    assert runner.read_pause(home_path, today="2026-09-15") is None


def test_resume_when_not_paused_reports_that(home_path: Path):
    result = runner.resume(home_path)
    assert result["was_paused"] is False


def test_run_does_not_launch_while_paused(home_path: Path, tmp_path: Path):
    _write_tasks(home_path)
    runner.pause(home_path, until="2026-09-20", reason="主人「今夜は止めて」")
    marker = tmp_path / "launched.txt"
    exec_cmd = _marker_command(marker)

    result = runner.run(home_path, now="2026-09-15T01:00:00", exec_cmd=exec_cmd, echo=False)

    assert result["status"] == "paused"
    assert result["pause"]["until"] == "2026-09-20"
    assert not marker.exists()


def test_run_while_paused_still_writes_last_run(home_path: Path, tmp_path: Path):
    _write_tasks(home_path)
    runner.pause(home_path, until="2026-09-20", reason="x")

    runner.run(home_path, now="2026-09-15T01:00:00", exec_cmd=_marker_command(tmp_path / "m.txt"), echo=False)

    data = runner.status(home_path)
    assert data["last_run"]["status"] == "paused"


def test_run_launches_again_the_day_after_until(home_path: Path, tmp_path: Path):
    _write_tasks(home_path)
    runner.pause(home_path, until="2026-09-14", reason="x")
    marker = tmp_path / "launched.txt"
    exec_cmd = _marker_command(marker, exit_code=0, result_text="done")

    result = runner.run(home_path, now="2026-09-15T01:00:00", exec_cmd=exec_cmd, echo=False)

    assert result["status"] == "done"
    assert marker.exists()


def test_status_shows_pause_info(home_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANOR_TODAY", "2026-09-15")  # until より前に固定（さもないと日付が進むと自動解除されて落ちる）
    runner.pause(home_path, until="2026-09-20", reason="主人のご指示")
    data = runner.status(home_path)
    assert data["pause"]["until"] == "2026-09-20"
    text = runner.format_status(data)
    assert "停止中" in text
    assert "2026-09-20" in text


def test_status_shows_tonight_off_when_until_is_tomorrow(home_path: Path, monkeypatch: pytest.MonkeyPatch):
    """T75: --tonight（＝翌日の日付）で止めたときは、日付でなく『今夜は休み』と読める形にする。"""
    monkeypatch.setenv("MANOR_TODAY", "2026-09-20")
    runner.pause(home_path, until="2026-09-21", reason="来客のため")
    text = runner.format_status(runner.status(home_path))
    assert "今夜は休み" in text
    assert "来客のため" in text


def test_format_review_shows_pause_info_first():
    result = {"date": "2026-09-15", "pause": {"until": "2026-09-20", "reason": "x"}, "health": {"ok": True, "reasons": []}, "items": {"found": False}}
    text = runner.format_review(result)
    lines = text.splitlines()
    assert "停止中" in lines[1]


# --- report ------------------------------------------------------------------------


def test_report_no_reports_dir_lists_nothing(home_path: Path):
    result = runner.report(home_path)
    assert result["found"] is False
    assert result["available"] == []


def test_report_missing_date_reports_available(home_path: Path):
    runner.reports_dir(home_path).mkdir(parents=True)
    (runner.reports_dir(home_path) / "2026-08-27.md").write_text("# 報告\n", encoding="utf-8")

    result = runner.report(home_path, "2026-08-28")

    assert result["found"] is False
    assert result["available"] == ["2026-08-27"]
    assert "2026-08-28 の作業報告はありません" in result["text"]


def test_report_found_returns_text(home_path: Path):
    runner.reports_dir(home_path).mkdir(parents=True)
    (runner.reports_dir(home_path) / "2026-08-27.md").write_text(
        "# 夜勤の作業報告 2026-08-27\n", encoding="utf-8"
    )

    result = runner.report(home_path, "2026-08-27")

    assert result["found"] is True
    assert "夜勤の作業報告" in result["text"]


# --- install / uninstall（表示だけ。絶対に実行しない） -----------------------------------


def test_install_default_does_not_execute(home_path: Path):
    result = runner.install(at="01:00")
    assert result["executed"] is False
    assert "manor-night" in result["command"]
    assert "run" in result["command"]


def test_uninstall_default_does_not_execute():
    result = runner.uninstall()
    assert result["executed"] is False
    assert "manor-night" in result["command"]


# --- 再開（利用上限で1度だけ） --------------------------------------------------------


def test_run_resumes_once_after_session_limit(home_path: Path, tmp_path: Path, monkeypatch):
    """1回目は利用上限で落ち、2回目で成功する。`time.sleep` はモックして待たない。"""
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)

    _write_tasks(home_path)
    # 時計を固定する（2026-09-02 の 22 時台に実行して落ちた: 実時刻＋2時間が日付をまたぎ、
    # 締切が「今日の早朝」に化けて `failed_no_time` になった。v1 の教訓「昼に緑でも夜に赤い」）。
    # runner は `datetime.now()` を直接呼ぶので、module の `datetime` を差し替える。
    fixed = datetime(2026, 9, 2, 1, 0, 0)

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            return fixed if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr(runner, "datetime", _FixedDateTime)
    now = fixed
    # 5分のマージン: 子プロセス起動のオーバーヘッドがあっても「もう過ぎた」判定に落ちないため。
    # `time.sleep` はモック済みなので、実際に5分待つわけではない。
    reset_at = now + timedelta(minutes=5)
    script = tmp_path / "resume_mock.py"
    counter = tmp_path / "count.txt"
    script.write_text(
        "import sys, pathlib, json\n"
        "counter = pathlib.Path(sys.argv[1])\n"
        "n = int(counter.read_text()) + 1 if counter.exists() else 1\n"
        "counter.write_text(str(n))\n"
        # Windows の素の python.exe は既定でロケール（cp932 等）で stdin を読むため、\n"
        "stdin_text = sys.stdin.buffer.read().decode('utf-8', errors='replace')\n"
        "pathlib.Path(sys.argv[1] + f'.prompt{n}.txt').write_text(stdin_text, encoding='utf-8')\n"
        "if n == 1:\n"
        "    print(json.dumps({'is_error': True, 'result': "
        "f'session limit. resets {sys.argv[2]}:{sys.argv[3]}'}))\n"
        "    sys.exit(1)\n"
        "print(json.dumps({'is_error': False, 'num_turns': 3}))\n",
        encoding="utf-8",
    )
    exec_cmd = (
        f'"{sys.executable}" "{script}" "{counter}" '
        f"{reset_at.hour} {reset_at.minute:02d}"
    )

    result = runner.run(
        home_path,
        now=(now + timedelta(hours=2)).strftime("%H:%M"),
        deadline=(now + timedelta(hours=4)).strftime("%H:%M"),
        min_minutes=1,
        exec_cmd=exec_cmd,
        echo=False,
    )

    assert result["status"] == "done"
    assert result["resumed"] is True
    assert result["attempts"] == 2
    prompt2 = Path(f"{counter}.prompt2.txt").read_text(encoding="utf-8")
    assert "これは再開です" in prompt2


# --- ヘルパ ------------------------------------------------------------------------


def _marker_command(marker: Path, *, exit_code: int = 0, result_text: str = "ok") -> str:
    """呼ばれたら `marker` を作るだけの、無害なコマンド（本物の `claude` の代わり）。"""
    script = marker.parent / f"{marker.stem}_marker.py"
    script.write_text(
        "import sys, pathlib, json\n"
        f"pathlib.Path(r'{marker}').write_text('called')\n"
        f"print(json.dumps({{'is_error': False, 'num_turns': 1, 'result': {result_text!r}}}))\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    return f'"{sys.executable}" "{script}"'


def _child_env(tmp_path: Path) -> dict:
    import os

    env = dict(os.environ)
    env["MANOR_HOME"] = str(tmp_path)
    return env


def test_kill_fires_on_time_even_with_a_full_size_prompt(home: Path):
    """**打ち切りはプロンプトの大きさに左右されない。**

    2026-09-07 の実測: `communicate(input=prompt, timeout=...)` に渡していたとき、
    Windows の CPython は stdin を呼び出しスレッドで同期的に書くため、子が読まないまま
    バッファが埋まると `timeout` が効かなかった。

        プロンプト 1 字     → 1.4 秒で打ち切り
        プロンプト 2,735 字 → 30.4 秒（＝子が自分で終わるまで待った）

    `tasks.md` が約束する「締切＋15分で機械が強制的に打ち切ります」に穴があった。
    プロンプトを一時ファイルから渡すことで、`timeout` が素直に効くようにした。

    ここは**実時間を測る**——「打ち切ったつもり」ではなく「実際に早く戻ること」が
    確かめたいこと。子は 30 秒眠るので、素通しなら 30 秒かかる。
    """
    argv = runner.build_exec_argv(
        f'"{sys.executable}" -c "import time; time.sleep(30)"', model="sonnet", max_turns=4
    )
    big_prompt = "あ" * 20000  # 本番の雛形（約 2,700 字）より十分大きく

    started = time.time()
    child = runner._run_child(
        argv, cwd=Path("."), env=dict(os.environ), prompt=big_prompt, timeout_seconds=1.0
    )
    elapsed = time.time() - started

    assert child["killed"] is True
    assert elapsed < 15, f"打ち切りに {elapsed:.1f} 秒かかりました（子の寿命 30 秒を待っています）"


def test_the_child_still_receives_the_whole_prompt(home: Path):
    """一時ファイル経由にしても、子が受け取るバイト列は変わらないこと。

    **文字数ではなくバイト数で見る。** 子が stdin をどの符号化で読むかは子の勝手
    （Windows の素の python は cp932 で読む）で、こちらが保証できるのは
    「同じバイト列を渡すこと」まで——実際 `print(len(sys.stdin.read()))` で書いたら
    5,000 字が 7,500 と返り、渡した中身ではなく子の復号を測っていた（2026-09-07）。
    """
    argv = runner.build_exec_argv(
        f'"{sys.executable}" -c "import sys; print(len(sys.stdin.buffer.read()))"',
        model="sonnet", max_turns=4,
    )
    prompt = "あ" * 5000

    child = runner._run_child(
        argv, cwd=Path("."), env=dict(os.environ), prompt=prompt, timeout_seconds=30.0
    )

    assert child["killed"] is False
    assert child["stdout"].strip() == str(len(prompt.encode("utf-8")))
