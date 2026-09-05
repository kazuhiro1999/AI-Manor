"""hooks（ADR-001 §7）。4イベントの出力形・無人モード・MANOR_HOOKS=off・壊れた入力。"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from manor import db as db_mod
from manor import hooks
from manor import profile as profile_mod
from manor import task as task_mod


# --- is_protected_path: パスの表記ゆれ ------------------------------------------


@pytest.mark.parametrize(
    "raw_path",
    [
        r"C:\x\home\projections\QUEUE.md",  # 絶対・バックスラッシュ
        "C:/x/home/projections/QUEUE.md",  # 絶対・スラッシュ
        r"home\projections\QUEUE.md",  # 相対・バックスラッシュ
        "home/projections/QUEUE.md",  # 相対・スラッシュ
    ],
)
def test_is_protected_path_projections_all_notations(raw_path: str):
    assert hooks.is_protected_path(raw_path) is True


@pytest.mark.parametrize(
    "raw_path",
    [
        r"C:\x\home\STATE.md",
        "home/STATE.md",
        r"home\STATE.md",
    ],
)
def test_is_protected_path_state_md(raw_path: str):
    assert hooks.is_protected_path(raw_path) is True


@pytest.mark.parametrize(
    "raw_path",
    [r"C:\x\home\manor.db", "home/manor.db", "manor.db"],
)
def test_is_protected_path_db_file(raw_path: str):
    assert hooks.is_protected_path(raw_path) is True


def test_is_protected_path_unrelated_file_is_false():
    assert hooks.is_protected_path("src/manor/task.py") is False
    assert hooks.is_protected_path("") is False


# --- pre-tool-use ----------------------------------------------------------------


def test_pre_tool_use_denies_edit_on_projection():
    payload = {"tool_name": "Edit", "tool_input": {"file_path": "home/projections/QUEUE.md"}}
    out = hooks.pre_tool_use(payload)
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_pre_tool_use_allows_unrelated_edit():
    payload = {"tool_name": "Edit", "tool_input": {"file_path": "src/manor/task.py"}}
    assert hooks.pre_tool_use(payload) == ""


def test_pre_tool_use_ignores_non_edit_tools():
    payload = {"tool_name": "Bash", "tool_input": {"file_path": "home/projections/QUEUE.md"}}
    assert hooks.pre_tool_use(payload) == ""


# --- session-start -----------------------------------------------------------------


def test_session_start_outputs_active_projection(home: Path):
    conn = db_mod.connect(home)
    task_mod.add(conn, "設計")
    conn.commit()
    conn.close()
    out = hooks.session_start({}, home)
    assert "起動時の射影" in out
    assert "設計" in out


def test_session_start_prefixes_profile_summary_when_set(home: Path):
    """ADR-007 D3: `profile.summary_line` を本文（`manor active` と同じ内容）の前に置く。"""
    conn = db_mod.connect(home)
    task_mod.add(conn, "設計")
    profile_mod.set_many(conn, {"master.callname": "旦那様", "butler.callname": "セバスチャン"})
    conn.commit()
    conn.close()

    out = hooks.session_start({}, home)
    marker_end = out.index("\n\n") + 2
    assert out.startswith(hooks.ALREADY_INJECTED)
    body = out[marker_end:]
    assert body.startswith("主人の呼び名: 旦那様 ／ 執事: セバスチャン")
    assert "設計" in out


def test_session_start_no_profile_prefix_when_unset(home: Path):
    conn = db_mod.connect(home)
    task_mod.add(conn, "設計")
    conn.commit()
    conn.close()

    out = hooks.session_start({}, home)
    marker_end = out.index("\n\n") + 2
    body = out[marker_end:]
    assert not body.startswith("主人の呼び名:")


def test_session_start_silent_when_unattended(home: Path):
    out = hooks.session_start({"permission_mode": "dontAsk"}, home)
    assert out == ""
    out2 = hooks.session_start({"permission_mode": "bypassPermissions"}, home)
    assert out2 == ""


def test_session_start_silent_without_db(tmp_path: Path):
    missing_home = tmp_path / "no_home_here"
    out = hooks.session_start({}, missing_home)
    assert out == ""


# --- stop ----------------------------------------------------------------------------


def test_stop_reports_doing_owned_by_butler(conn, home: Path):
    task_mod.add(conn, "掃除")
    tid = [r["id"] for r in conn.execute("SELECT id FROM task").fetchall()][0]
    task_mod.status(conn, tid, "doing", owner="butler")
    conn.commit()
    out = hooks.stop({}, home)
    assert out != ""
    data = json.loads(out)
    assert "doing" in data["hookSpecificOutput"]["systemMessage"] or tid in data["hookSpecificOutput"]["systemMessage"]


def test_stop_silent_when_nothing_to_report(home: Path):
    out = hooks.stop({}, home)
    assert out == ""


def test_stop_silent_when_unattended(conn, home: Path):
    tid = task_mod.add(conn, "掃除")
    task_mod.status(conn, tid, "doing", owner="butler")
    conn.commit()
    out = hooks.stop({"permission_mode": "dontAsk"}, home)
    assert out == ""


def test_stop_reports_blocked_ready(conn, home: Path):
    blocker = task_mod.add(conn, "先にやること")
    tid = task_mod.add(conn, "本題", depends_on=[blocker])
    task_mod.status(conn, blocker, "doing")
    task_mod.status(conn, blocker, "done")
    task_mod.status(conn, tid, "waiting", note=f"{blocker} 待ち")
    conn.commit()
    out = hooks.stop({}, home)
    assert out != ""
    data = json.loads(out)
    assert tid in data["hookSpecificOutput"]["systemMessage"]


# --- pre-compact -----------------------------------------------------------------------


def test_pre_compact_outputs_pointer(home: Path):
    out = hooks.pre_compact({}, home)
    data = json.loads(out)
    assert "manor active" in data["hookSpecificOutput"]["additionalContext"]


def test_pre_compact_silent_when_unattended(home: Path):
    out = hooks.pre_compact({"permission_mode": "bypassPermissions"}, home)
    assert out == ""


# --- 入口（main）: MANOR_HOOKS=off・壊れた stdin ------------------------------------------


def test_read_payload_handles_empty_stdin():
    assert hooks.read_payload(io.StringIO("")) == {}


def test_read_payload_handles_broken_json():
    assert hooks.read_payload(io.StringIO("{not json")) == {}


def test_read_payload_handles_non_dict_json():
    assert hooks.read_payload(io.StringIO("[1, 2, 3]")) == {}


def test_main_returns_zero_on_broken_stdin(monkeypatch, home: Path):
    monkeypatch.setattr("sys.stdin", io.StringIO("{not json"))
    code = hooks.main(["pre-tool-use"])
    assert code == 0


def test_main_silences_all_events_with_manor_hooks_off(monkeypatch, home: Path, capsys):
    monkeypatch.setenv("MANOR_HOOKS", "off")
    payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "home/projections/QUEUE.md"}})
    for event in ("session-start", "pre-tool-use", "stop", "pre-compact"):
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        code = hooks.main([event])
        assert code == 0
    captured = capsys.readouterr()
    assert captured.out == ""


def test_main_no_args_returns_zero():
    assert hooks.main([]) == 0


def test_main_unknown_event_returns_zero_silently(monkeypatch, home: Path, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    code = hooks.main(["something-else"])
    assert code == 0
    assert capsys.readouterr().out == ""


# --- is_protected_path: MANOR_HOME を実体で解決する（QA 指摘 2026-09-02） ------------


def test_is_protected_path_resolves_manor_home_without_home_in_name(tmp_path, monkeypatch):
    """`MANOR_HOME` が `home` という名を含まないフォルダでも、射影の実体は守られる。"""
    alt = tmp_path / "qa_run1"
    (alt / "projections").mkdir(parents=True)
    target = alt / "projections" / "QUEUE.md"
    target.write_bytes(b"x")
    monkeypatch.setenv("MANOR_HOME", str(alt))
    assert hooks.is_protected_path(str(target)) is True
    assert hooks.is_protected_path(str(alt / "STATE.md")) is True
    assert hooks.is_protected_path(str(alt / "USER.md")) is False


def test_is_protected_path_relative_with_cwd(tmp_path, monkeypatch):
    alt = tmp_path / "elsewhere"
    (alt / "projections").mkdir(parents=True)
    monkeypatch.setenv("MANOR_HOME", str(alt))
    assert hooks.is_protected_path("projections/QUEUE.md", cwd=str(alt)) is True
    assert hooks.is_protected_path("projections/QUEUE.md", cwd=str(tmp_path)) is False


def test_pre_tool_use_denies_resolved_projection_via_payload_cwd(tmp_path, monkeypatch):
    alt = tmp_path / "somewhere"
    (alt / "projections").mkdir(parents=True)
    monkeypatch.setenv("MANOR_HOME", str(alt))
    payload = {
        "tool_name": "Edit",
        "cwd": str(alt),
        "tool_input": {"file_path": "projections/PROJECTS.md"},
    }
    out = hooks.pre_tool_use(payload)
    assert '"permissionDecision": "deny"' in out
