"""通話(小窓で話しかける。ADR-008 §8 D17〜D23)の試験。**合成データのみ・`claude` は起動しない**

——`subprocess.run` を monkeypatch し、実際の CLI は一度も呼ばない。

2026-09-04・主人の訂正で設計が変わった: この窓は隔離された雑談の口ではなく、manor の
ワークスペースで起こす「小さな執事のセッション」(D21)。`--permission-mode manual` ＋
`--allowed-tools` で `manor …` コマンド・`mcp__*`・`Read`/`Glob`/`Grep` だけを事前承認する
(`tests/behavior/run.py` と同じ実績のある形)。`Write`/`Edit`・裸の `Bash` は含めない。
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pytest

from manor import talk_session

#: 押せる時間帯（01:00-07:30 の外）に固定する。G1: 時刻・日付に依る歯止めの試験は
#: 必ず時計を固定して書く（`butler/GROWTH.md` G1）。
NOON = "2026-09-04T12:00:00"
NIGHT = "2026-09-04T02:00:00"


def _fix_clock(monkeypatch: pytest.MonkeyPatch, when: str = NOON) -> None:
    monkeypatch.setenv("MANOR_NOW", when)


def _fake_claude_json(*, result: str, cost: float = 0.01, turns: int = 1) -> str:
    return json.dumps(
        {
            "is_error": False,
            "result": result,
            "total_cost_usd": cost,
            "num_turns": turns,
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        }
    )


def _mock_success(monkeypatch: pytest.MonkeyPatch, *, reply: str = "承知いたしました。") -> Mock:
    proc = Mock(returncode=0, stdout=_fake_claude_json(result=reply), stderr="")
    run_mock = Mock(return_value=proc)
    monkeypatch.setattr(talk_session.subprocess, "run", run_mock)
    return run_mock


def _mock_failure(monkeypatch: pytest.MonkeyPatch, exc: Exception | None = None) -> Mock:
    run_mock = Mock(side_effect=exc or OSError("claude が見つかりません"))
    monkeypatch.setattr(talk_session.subprocess, "run", run_mock)
    return run_mock


def _mute_voice(monkeypatch: pytest.MonkeyPatch) -> Mock:
    speak_mock = Mock(return_value=True)
    monkeypatch.setattr(talk_session.voice, "speak", speak_mock)
    return speak_mock


# --- state（D17。残り回数を隠さない） ---------------------------------------------------------


def test_state_available_by_default(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fix_clock(monkeypatch)
    st = talk_session.state(home)
    assert st == {
        "available": True,
        "limit": talk_session.DEFAULT_LIMIT,
        "used": 0,
        "remaining": talk_session.DEFAULT_LIMIT,
        "reason": None,
        "lock": talk_session.DEFAULT_LOCK,
        "message": "",
    }


def test_state_at_limit_is_unavailable_with_message(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fix_clock(monkeypatch)
    (home / "config.toml").write_text("[talk]\nlimit = 1\n", encoding="utf-8")
    talk_session.bump_usage(home, date(2026, 9, 4))
    st = talk_session.state(home)
    assert st["available"] is False
    assert st["reason"] == "budget"
    assert st["message"] == talk_session.OUT_OF_BUDGET
    assert st["message"]  # 沈黙しない


def test_state_locked_hours_is_unavailable(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fix_clock(monkeypatch, NIGHT)
    st = talk_session.state(home)
    assert st["available"] is False
    assert st["reason"] == "night"
    assert st["message"] == talk_session.NIGHT_LOCKED


def test_state_limit_zero_disables_entirely(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fix_clock(monkeypatch)
    (home / "config.toml").write_text("[talk]\nlimit = 0\n", encoding="utf-8")
    st = talk_session.state(home)
    assert st["available"] is False
    assert st["limit"] == 0
    assert st["reason"] == "disabled"
    assert st["message"] == talk_session.DISABLED_MESSAGE


def test_state_custom_lock_window(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (home / "config.toml").write_text('[talk]\nlock = "12:00-13:00"\n', encoding="utf-8")
    _fix_clock(monkeypatch, "2026-09-04T12:30:00")
    assert talk_session.state(home)["available"] is False
    _fix_clock(monkeypatch, "2026-09-04T14:00:00")
    assert talk_session.state(home)["available"] is True


# --- ask: 数える順番（D17「先に数える」） -----------------------------------------------------


def test_counter_increments_before_call_even_on_failure(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fix_clock(monkeypatch)
    _mock_failure(monkeypatch)
    _mute_voice(monkeypatch)

    result = talk_session.ask(home, "こんにちは")

    assert result["ok"] is False
    assert result["used"] == 1  # 失敗しても数えたぶんは戻らない（D17）
    assert talk_session.read_usage(home, date(2026, 9, 4)) == 1


def test_refuses_at_limit_without_invoking_claude(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fix_clock(monkeypatch)
    (home / "config.toml").write_text("[talk]\nlimit = 1\n", encoding="utf-8")
    talk_session.bump_usage(home, date(2026, 9, 4))
    run_mock = Mock()
    monkeypatch.setattr(talk_session.subprocess, "run", run_mock)
    speak_mock = _mute_voice(monkeypatch)

    result = talk_session.ask(home, "こんにちは")

    assert result["ok"] is False
    assert result["reply"] == talk_session.OUT_OF_BUDGET
    run_mock.assert_not_called()
    # D17: 打ち止め・時間外は「黙らない」——理由は声にも出る。
    speak_mock.assert_called_once_with(home, talk_session.OUT_OF_BUDGET, agent=None)
    # 既に打ち止めなので、これ以上は数えない。
    assert talk_session.read_usage(home, date(2026, 9, 4)) == 1


def test_refuses_during_locked_hours_without_invoking_claude(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fix_clock(monkeypatch, NIGHT)
    run_mock = Mock()
    monkeypatch.setattr(talk_session.subprocess, "run", run_mock)
    _mute_voice(monkeypatch)

    result = talk_session.ask(home, "こんにちは")

    assert result["ok"] is False
    assert result["reply"] == talk_session.NIGHT_LOCKED
    run_mock.assert_not_called()
    assert talk_session.read_usage(home, date(2026, 9, 4)) == 0


def test_limit_zero_refuses_without_invoking_claude(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fix_clock(monkeypatch)
    (home / "config.toml").write_text("[talk]\nlimit = 0\n", encoding="utf-8")
    run_mock = Mock()
    monkeypatch.setattr(talk_session.subprocess, "run", run_mock)
    _mute_voice(monkeypatch)

    result = talk_session.ask(home, "こんにちは")

    assert result["ok"] is False
    run_mock.assert_not_called()


# --- 失敗は声に出さない。成功は声に出す（D17） -------------------------------------------------


def test_failure_returns_ok_false_and_does_not_speak(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fix_clock(monkeypatch)
    _mock_failure(monkeypatch)
    speak_mock = _mute_voice(monkeypatch)

    result = talk_session.ask(home, "こんにちは")

    assert result["ok"] is False
    assert result["reply"]
    speak_mock.assert_not_called()


def test_timeout_is_a_failure_and_does_not_speak(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess as subprocess_mod

    _fix_clock(monkeypatch)
    _mock_failure(monkeypatch, subprocess_mod.TimeoutExpired(cmd=["claude"], timeout=180))
    speak_mock = _mute_voice(monkeypatch)

    result = talk_session.ask(home, "こんにちは")

    assert result["ok"] is False
    speak_mock.assert_not_called()


def test_success_speaks_once_and_records_a_run_row(home: Path, conn, monkeypatch: pytest.MonkeyPatch) -> None:
    _fix_clock(monkeypatch)
    _mock_success(monkeypatch, reply="かしこまりました、主人。")
    speak_mock = _mute_voice(monkeypatch)

    result = talk_session.ask(home, "調子はどうですか")

    assert result["ok"] is True
    assert result["reply"] == "かしこまりました、主人。"
    assert result["used"] == 1
    assert result["remaining"] == talk_session.DEFAULT_LIMIT - 1
    speak_mock.assert_called_once_with(home, "かしこまりました、主人。", agent=None)

    rows = [dict(r) for r in conn.execute("SELECT * FROM run WHERE kind = 'talk'").fetchall()]
    assert len(rows) == 1
    assert rows[0]["exit_reason"] == "done"
    assert rows[0]["ended_at"] is not None


def test_claude_nonzero_exit_is_a_failure_and_records_it(home: Path, conn, monkeypatch: pytest.MonkeyPatch) -> None:
    _fix_clock(monkeypatch)
    proc = Mock(returncode=1, stdout="", stderr="line1\nboom")
    monkeypatch.setattr(talk_session.subprocess, "run", Mock(return_value=proc))
    speak_mock = _mute_voice(monkeypatch)

    result = talk_session.ask(home, "こんにちは")

    assert result["ok"] is False
    assert "boom" in result["reply"]
    speak_mock.assert_not_called()
    rows = [dict(r) for r in conn.execute("SELECT * FROM run WHERE kind = 'talk'").fetchall()]
    assert len(rows) == 1
    assert rows[0]["exit_reason"] == "failed"


def test_empty_text_is_refused_without_counting(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fix_clock(monkeypatch)
    run_mock = Mock()
    monkeypatch.setattr(talk_session.subprocess, "run", run_mock)
    result = talk_session.ask(home, "   ")
    assert result["ok"] is False
    run_mock.assert_not_called()
    assert talk_session.read_usage(home, date(2026, 9, 4)) == 0


# --- D21: manor のワークスペースで起こす（隔離しない） ------------------------------------------


def test_ask_runs_in_the_manor_workspace(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`cwd` はリポジトリのルート——`CLAUDE.md`・hooks・`butler/policy.toml` がそのまま
    効くための前提（`%TEMP%` へ隔離していた旧設計をやめた）。
    """
    _fix_clock(monkeypatch)
    run_mock = _mock_success(monkeypatch)
    _mute_voice(monkeypatch)

    talk_session.ask(home, "タスクを追加して")

    kwargs = run_mock.call_args.kwargs
    assert Path(kwargs["cwd"]) == talk_session.util.repo_root()


def test_ask_passes_home_as_manor_home_env(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """子プロセスの `MANOR_HOME` を明示的に上書きする（`talk.py` `run()` と同じ作法）
    ——`home` 引数と実行時の環境変数がずれても、子プロセスは正しい `home/` を見る。
    """
    _fix_clock(monkeypatch)
    monkeypatch.setenv("MANOR_HOME", "C:/どこか別の場所")  # わざとずらす
    run_mock = _mock_success(monkeypatch)
    _mute_voice(monkeypatch)

    talk_session.ask(home, "こんにちは")

    kwargs = run_mock.call_args.kwargs
    assert kwargs["env"]["MANOR_HOME"] == str(home)


def test_ask_does_not_force_manor_hooks_off(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D21: hooks がそのまま効くのが要点——夜勤専用の `MANOR_HOOKS=off` はここでは立てない。"""
    _fix_clock(monkeypatch)
    monkeypatch.delenv("MANOR_HOOKS", raising=False)
    run_mock = _mock_success(monkeypatch)
    _mute_voice(monkeypatch)

    talk_session.ask(home, "こんにちは")

    kwargs = run_mock.call_args.kwargs
    assert "MANOR_HOOKS" not in kwargs["env"]


# --- 道具: manor のコマンドだけ事前承認する。Write/Edit は入れない（D21・2026-09-04 実機検分） --
#
# `claude -p` は非対話——事前承認の無い道具は許可待ちのまま拒否される（実機で確認済み:
# `--permission-mode dontAsk` のまま `--disallowed-tools` を削るだけでは足りず、
# `manor task add` を頼んでも「許可設定によりブロックされています」と拒否された）。
# `tests/behavior/run.py`（51〜66行・195〜206行）と同じ実績のある形で固定する。


def test_build_command_uses_permission_mode_manual(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fix_clock(monkeypatch)
    run_mock = _mock_success(monkeypatch)
    _mute_voice(monkeypatch)

    talk_session.ask(home, "在庫を確認して")

    cmd = run_mock.call_args.args[0]
    idx = cmd.index("--permission-mode")
    assert cmd[idx + 1] == "manual"


def test_build_command_pre_approves_manor_bash_patterns_and_mcp(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fix_clock(monkeypatch)
    run_mock = _mock_success(monkeypatch)
    _mute_voice(monkeypatch)

    talk_session.ask(home, "牛乳を買うタスクを追加して")

    cmd = run_mock.call_args.args[0]
    idx = cmd.index("--allowed-tools")
    allow_slice = cmd[idx + 1 : cmd.index("--disallowed-tools")]
    assert set(allow_slice) == set(talk_session.ALLOWED_TOOLS)
    assert "Bash(manor:*)" in allow_slice
    assert "Bash(uv run manor:*)" in allow_slice
    assert "Bash(uv run --no-sync manor:*)" in allow_slice
    assert "mcp__*" in allow_slice  # カレンダー等。主人の明示
    assert "Read" in allow_slice
    assert "Glob" in allow_slice
    assert "Grep" in allow_slice


def test_build_command_does_not_pre_approve_write_or_edit(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """射影も規則も、窓から書き換える用事は無い（`manor …` を通せば DB に入る。射影は
    hook が別途拒む）。`Write`/`Edit` は事前承認一覧に無い——`--permission-mode manual`
    のもとでは非対話ゆえに実質使えない。任意のシェルを開ける裸の `Bash` も同様に含めない
    ——`Bash(manor:*)` 系のパターンだけを事前承認する。
    """
    _fix_clock(monkeypatch)
    run_mock = _mock_success(monkeypatch)
    _mute_voice(monkeypatch)

    talk_session.ask(home, "こんにちは")

    cmd = run_mock.call_args.args[0]
    idx = cmd.index("--allowed-tools")
    allow_slice = cmd[idx + 1 : cmd.index("--disallowed-tools")]
    assert "Write" not in allow_slice
    assert "Edit" not in allow_slice
    assert "Bash" not in allow_slice


def test_only_the_meaningless_tools_are_explicitly_denied(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fix_clock(monkeypatch)
    run_mock = _mock_success(monkeypatch)
    _mute_voice(monkeypatch)

    talk_session.ask(home, "在庫を確認して")

    cmd = run_mock.call_args.args[0]
    idx = cmd.index("--disallowed-tools")
    deny_slice = cmd[idx + 1 : cmd.index("--no-session-persistence")]
    assert set(deny_slice) == set(talk_session.DENY_TOOLS)
    for meaningless in (
        "Agent", "Task", "CronCreate", "CronDelete", "CronList",
        "ScheduleWakeup", "RemoteTrigger", "Monitor", "SendMessage", "PushNotification",
    ):
        assert meaningless in deny_slice


def test_mcp_is_not_blocked_by_strict_mcp_config(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--strict-mcp-config` は付けない——カレンダー等の MCP を使ってよい（主人の明示）。"""
    _fix_clock(monkeypatch)
    run_mock = _mock_success(monkeypatch)
    _mute_voice(monkeypatch)

    talk_session.ask(home, "明日の予定を教えて")

    cmd = run_mock.call_args.args[0]
    assert "--strict-mcp-config" not in cmd


def test_talk_env_prepends_venv_scripts_to_path(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """nested session の Bash から裸の `manor ...` が解決するように、venv の `Scripts` を
    PATH の先頭に足す（`tests/behavior/run.py` と同じ実測済みの対策）。
    """
    _fix_clock(monkeypatch)
    run_mock = _mock_success(monkeypatch)
    _mute_voice(monkeypatch)

    talk_session.ask(home, "こんにちは")

    env = run_mock.call_args.kwargs["env"]
    assert env["PATH"].split(os.pathsep)[0] == talk_session._VENV_SCRIPTS_POSIX


# --- argv に % ・改行を載せない（Windows の claude.CMD が cmd.exe に再解釈する。実測で確認） ----
# いまの系統プロンプトは時刻・時間帯だけ（機械生成）で混入経路が無いが、対策自体は
# 主人の指示で残してある——将来また混入経路が増えても効く安全網であることを確かめる。


def test_check_argv_rejects_percent() -> None:
    with pytest.raises(talk_session.TalkError):
        talk_session._check_argv(["claude", "-p", "--append-system-prompt", "50%引きセール"])


def test_check_argv_rejects_newline() -> None:
    with pytest.raises(talk_session.TalkError):
        talk_session._check_argv(["claude", "-p", "--append-system-prompt", "1行目\n2行目"])


def test_check_argv_allows_clean_args() -> None:
    talk_session._check_argv(["claude", "-p", "--append-system-prompt", "ふつうの日本語です。"])


def test_safe_for_argv_replaces_percent_with_fullwidth() -> None:
    assert talk_session._safe_for_argv("50%引きセール") == "50％引きセール"


def test_ask_fails_gracefully_instead_of_leaking_when_percent_slips_through(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_system_prompt` を素通りする経路が万一あっても、最後の網（`_check_argv`）が
    呼び出しそのものを止める（黙って環境変数が漏れるより、通話が1回不通になるほうが害が小さい）。
    """
    _fix_clock(monkeypatch)
    monkeypatch.setattr(talk_session, "_now_line", lambda now: "いまは %USERNAME% ごろです。")
    run_mock = Mock()
    monkeypatch.setattr(talk_session.subprocess, "run", run_mock)
    speak_mock = _mute_voice(monkeypatch)

    result = talk_session.ask(home, "こんにちは")

    assert result["ok"] is False
    run_mock.assert_not_called()  # claude はそもそも起動しない
    speak_mock.assert_not_called()  # 失敗は声に出さない


# --- 会話は残さない ------------------------------------------------------------------------


def test_conversation_text_is_not_persisted_anywhere_under_home(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fix_clock(monkeypatch)
    marker_in = "ひみつの合言葉インプットXYZ77"
    marker_out = "ひみつの合言葉アウトプットABC88"
    _mock_success(monkeypatch, reply=marker_out)
    _mute_voice(monkeypatch)

    result = talk_session.ask(home, marker_in)
    assert result["ok"] is True

    needle_in = marker_in.encode("utf-8")
    needle_out = marker_out.encode("utf-8")
    for p in home.rglob("*"):
        if not p.is_file():
            continue
        try:
            data = p.read_bytes()
        except OSError:
            continue
        assert needle_in not in data, f"{p} に入力の会話文が残っている"
        assert needle_out not in data, f"{p} に返事の会話文が残っている"


def test_usage_file_only_holds_date_and_count(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fix_clock(monkeypatch)
    _mock_success(monkeypatch)
    _mute_voice(monkeypatch)
    talk_session.ask(home, "こんにちは、今日の天気は？")

    payload = json.loads(talk_session.usage_path(home).read_bytes())
    assert payload == {"2026-09-04": 1}


# --- 開閉（LLM は呼ばない） ------------------------------------------------------------------


def test_open_session_available_returns_greeting_without_calling_claude(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fix_clock(monkeypatch)
    run_mock = Mock()
    monkeypatch.setattr(talk_session.subprocess, "run", run_mock)
    _mute_voice(monkeypatch)

    result = talk_session.open_session(home)

    assert result["available"] is True
    assert result["text"]
    assert len(result["lines"]) == 2
    run_mock.assert_not_called()
    # 未設定の [voice] では温めようがない。
    assert result["warming"] is False


def test_open_session_speaks_the_greeting_in_the_agents_voice(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """挨拶もその窓の担当の声（ADR-011 D11 追補）——姿が家政婦なのに最初のひとことだけ
    執事の声、では揃わない。
    """
    _fix_clock(monkeypatch, NOON)
    speak_mock = _mute_voice(monkeypatch)
    monkeypatch.setattr(talk_session, "_warm_engine_async", lambda home: False)

    result = talk_session.open_session(home, agent="housekeeper")

    assert result["available"] is True
    assert speak_mock.call_args.kwargs["agent"] == "housekeeper"


def test_open_session_without_an_agent_uses_the_default_voice(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """担当を渡さない古い呼び出しはこれまでどおり既定の声（`agent=None`）。"""
    _fix_clock(monkeypatch, NOON)
    speak_mock = _mute_voice(monkeypatch)
    monkeypatch.setattr(talk_session, "_warm_engine_async", lambda home: False)

    talk_session.open_session(home)

    assert speak_mock.call_args.kwargs["agent"] is None


def test_open_session_when_locked_speaks_the_reason(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fix_clock(monkeypatch, NIGHT)
    speak_mock = _mute_voice(monkeypatch)

    result = talk_session.open_session(home)

    assert result["available"] is False
    assert result["text"] == talk_session.NIGHT_LOCKED
    speak_mock.assert_called_once_with(home, talk_session.NIGHT_LOCKED, agent=None)


def test_close_session_without_starting_engine_does_nothing(home: Path) -> None:
    talk_session._engine_started_by_us = False
    assert talk_session.close_session(home) == {"stopped": False}


def test_close_session_stops_only_what_it_started(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    talk_session._engine_started_by_us = True
    stop_mock = Mock(return_value={"stopped": True})
    monkeypatch.setattr(talk_session.voice, "engine_stop", stop_mock)

    result = talk_session.close_session(home)

    assert result == {"stopped": True}
    stop_mock.assert_called_once_with(home)
    assert talk_session._engine_started_by_us is False


# --- D22: 系統プロンプトは時刻と時間帯だけ（旧 D19 の人格・要対応件数・予定は撤回） --------------


def test_system_prompt_carries_only_time_and_time_band(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fix_clock(monkeypatch, "2026-09-04T15:00:00")
    run_mock = _mock_success(monkeypatch)
    _mute_voice(monkeypatch)

    talk_session.ask(home, "こんにちは")

    cmd = run_mock.call_args.args[0]
    idx = cmd.index("--append-system-prompt")
    system_prompt_text = cmd[idx + 1]
    assert "15時ごろ" in system_prompt_text
    assert "午後" in system_prompt_text
    # 旧設計（隔離された雑談の口）の名残が無いこと——人格・要対応件数・予定は渡さない。
    assert "執事です" not in system_prompt_text
    assert "要対応" not in system_prompt_text
    assert "予定" not in system_prompt_text


def test_now_line_drops_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime

    line = talk_session._now_line(datetime(2026, 9, 4, 15, 37))
    assert "15時" in line
    assert "37分" not in line
    assert "分は持っていない" in line or "分か" in line  # 分は分からないと答える一節を含む


# --- ADR-011 D11: 声を出す側は担当を渡す --------------------------------------------------------


def test_ask_speaks_with_the_passed_agent(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fix_clock(monkeypatch)
    _mock_success(monkeypatch, reply="かしこまりました。")
    speak_mock = _mute_voice(monkeypatch)

    talk_session.ask(home, "在庫を確認して", agent="housekeeper")

    speak_mock.assert_called_once_with(home, "かしこまりました。", agent="housekeeper")


def test_ask_without_agent_passes_none_through(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fix_clock(monkeypatch)
    _mock_success(monkeypatch, reply="かしこまりました。")
    speak_mock = _mute_voice(monkeypatch)

    talk_session.ask(home, "こんにちは")

    speak_mock.assert_called_once_with(home, "かしこまりました。", agent=None)


def test_refusal_message_is_also_spoken_with_the_passed_agent(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """打ち止め・時間外の理由も、窓の担当の声で伝える（同じ小窓なので声も揃える）。"""
    _fix_clock(monkeypatch, NIGHT)
    run_mock = Mock()
    monkeypatch.setattr(talk_session.subprocess, "run", run_mock)
    speak_mock = _mute_voice(monkeypatch)

    talk_session.ask(home, "こんにちは", agent="chef")

    run_mock.assert_not_called()
    speak_mock.assert_called_once_with(home, talk_session.NIGHT_LOCKED, agent="chef")


def test_ask_uses_the_agents_speaker_for_synthesis(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`voice.speak` を差し替えず、実際に合成へ渡る話者IDを見る（D11 の実体は
    `voice._speaker_for` が解決すること——ここでは配線だけを実測する）。"""
    from manor import voice as voice_mod

    _fix_clock(monkeypatch)
    _mock_success(monkeypatch, reply="かしこまりました、家政婦です。")

    (home / "config.toml").write_text(
        "[voice]\nengine = 'voicevox'\nhost = '127.0.0.1'\nport = 50021\nspeaker = 13\n"
        "\n[voice.speakers]\nhousekeeper = 46\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        voice_mod, "engine_status", lambda home: {"configured": True, "running": True}
    )
    seen_speakers: list[int] = []

    def fake_audio_query(cfg, text, speaker):  # noqa: ANN001
        seen_speakers.append(speaker)
        return b'{"q": true}'

    monkeypatch.setattr(voice_mod, "_audio_query", fake_audio_query)
    monkeypatch.setattr(voice_mod, "_synthesis", lambda cfg, q, speaker: b"RIFF....data")
    monkeypatch.setattr(voice_mod, "_play", lambda path: True)

    talk_session.ask(home, "在庫を確認して", agent="housekeeper")

    assert seen_speakers == [46]


def test_ask_without_agent_uses_the_default_speaker(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from manor import voice as voice_mod

    _fix_clock(monkeypatch)
    _mock_success(monkeypatch, reply="かしこまりました。")

    (home / "config.toml").write_text(
        "[voice]\nengine = 'voicevox'\nhost = '127.0.0.1'\nport = 50021\nspeaker = 13\n"
        "\n[voice.speakers]\nhousekeeper = 46\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        voice_mod, "engine_status", lambda home: {"configured": True, "running": True}
    )
    seen_speakers: list[int] = []

    def fake_audio_query(cfg, text, speaker):  # noqa: ANN001
        seen_speakers.append(speaker)
        return b'{"q": true}'

    monkeypatch.setattr(voice_mod, "_audio_query", fake_audio_query)
    monkeypatch.setattr(voice_mod, "_synthesis", lambda cfg, q, speaker: b"RIFF....data")
    monkeypatch.setattr(voice_mod, "_play", lambda path: True)

    talk_session.ask(home, "こんにちは")  # agent 省略

    assert seen_speakers == [13]


# --- CLI 語彙（`manor talk-session` は `manor talk <agent>` を壊さない） -----------------------


def test_cli_registers_alongside_existing_talk_command() -> None:
    import argparse

    from manor import talk as talk_mod

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="group")
    talk_mod.register(sub)
    talk_session.register(sub)

    args = parser.parse_args(["talk", "butler"])
    assert args.group == "talk"
    assert args.name == "butler"

    args = parser.parse_args(["talk-session", "state", "--json"])
    assert args.group == "talk-session"
    assert args.verb == "state"


def test_cli_ask_accepts_agent_flag() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="group")
    talk_session.register(sub)

    args = parser.parse_args(["talk-session", "ask", "こんにちは", "--agent", "housekeeper"])
    assert args.agent == "housekeeper"
