"""ADR-025（他のPCのセッション同期）。送る側の道具・取り込み・紐づけ・偽の中継での往復。

実物の GAS には触れない。`_FakeRelay` が Code.js と同じ op を最小限まねる。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from manor import secrets as secrets_mod
from manor import task as task_mod
from manor.remote import relay, store
from manor.remote.client import manor_report as mr


# --- 送る側の道具 ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", [
    "https://github.com/Owner/Repo.git",
    "git@github.com:Owner/Repo.git",
    "ssh://git@github.com:22/Owner/Repo",
    "https://user@github.com/owner/repo/",
])
def test_normalize_remote_folds_spellings(url: str) -> None:
    assert mr.normalize_remote(url) == "github.com/owner/repo"


def test_repo_key_falls_back_to_folder() -> None:
    assert mr.repo_key(None, "C:/work/Sample-App") == "dir:sample-app"
    assert mr.normalize_remote("C:/local/path") is None


def _args(**kw) -> argparse.Namespace:
    base = dict(title=None, phase=None, progress=None, human_next=None, project=None, task=None, note=None)
    base.update(kw)
    return argparse.Namespace(**base)


def test_build_report_defaults_progress_from_phase_and_keeps_previous() -> None:
    first = mr.build_report(_args(title="x" * 60, phase="implementing", human_next="実機で確認"), None)
    assert first["progress"] == 50
    assert len(first["title"]) == mr.TITLE_MAX
    second = mr.build_report(_args(progress=62), first)
    assert second["phase"] == "implementing" and second["progress"] == 62
    assert second["title"] == first["title"] and second["human_next"] == "実機で確認"
    third = mr.build_report(_args(phase="testing"), second)
    assert third["progress"] == 85  # 段階が変わり、数字の申告が無ければ既定値


def _transcript(tmp_path: Path, tools: list[dict], reported: bool = False) -> str:
    lines = [
        {"type": "user", "message": {"content": "前の依頼"}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {}}]}},
        {"type": "user", "message": {"content": "今の依頼"}},
    ]
    for t in tools:
        lines.append({"type": "assistant", "message": {"content": [{"type": "tool_use", **t}]}})
        lines.append({"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}})
    if reported:
        lines.append({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash",
                      "input": {"command": "py manor_report.py progress --title a"}}]}})
    path = tmp_path / "t.jsonl"
    path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")
    return str(path)


def test_stop_nudges_only_after_work_without_report(tmp_path: Path) -> None:
    talk = mr.analyze_turn(_transcript(tmp_path, [{"name": "Read", "input": {}}]))
    assert talk["tools"] == 1 and not mr.should_nudge(talk, {})
    edit = mr.analyze_turn(_transcript(tmp_path, [{"name": "Write", "input": {}}]))
    assert edit["edited"] and mr.should_nudge(edit, {})
    many = mr.analyze_turn(_transcript(tmp_path, [{"name": "Bash", "input": {}}] * 5))
    assert mr.should_nudge(many, {})
    done = mr.analyze_turn(_transcript(tmp_path, [{"name": "Write", "input": {}}], reported=True))
    assert not mr.should_nudge(done, {})
    # 手元の状態で「このターンの後に報告した」と分かれば止めない
    assert not mr.should_nudge(edit, {"prompt_at": "2026-10-09T10:00:00+09:00",
                                      "progress_at": "2026-10-09T10:05:00+09:00"})


def test_merge_hooks_is_idempotent_and_keeps_others() -> None:
    cfg = {"command": '"py" "/x/manor_report.py"'}
    settings = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other"}]}]}}
    mr.merge_hooks(settings, cfg)
    mr.merge_hooks(settings, cfg)
    stop = settings["hooks"]["Stop"]
    assert [h["command"] for g in stop for h in g["hooks"]] == ["other", '"py" "/x/manor_report.py" hook Stop']
    assert set(settings["hooks"]) == {"Stop", "SessionStart", "UserPromptSubmit", "SessionEnd"}


def test_injection_names_session_and_tasks() -> None:
    cfg = {"machine": "LAB", "command": "py r.py"}
    entry = {"projects": [{"id": "P4", "name": "XR"}], "tasks": [{"id": "T80", "title": "onnx", "status": "todo"}]}
    text = mr.injection_text(cfg, "sess-1", {"remote": "github.com/o/r"}, entry)
    assert "sess-1" in text and "T80" in text and "py r.py progress --session sess-1" in text
    assert "まだどのプロジェクトにも" in mr.injection_text(cfg, "s", {"remote": "github.com/o/r"}, None)


# --- 取り込み ------------------------------------------------------------------------------


def _ev(kind: str, at: str, sid: str = "s1", report: dict | None = None, eid: str | None = None) -> dict:
    return {"v": 1, "event_id": eid or f"{sid}-{kind}-{at}", "kind": kind, "at": at, "machine": "LAB",
            "session_id": sid, "cwd": "C:/w/r", "repo": {"remote": "github.com/o/r", "branch": "main",
                                                          "key": "github.com/o/r"}, "report": report}


def _project(conn, code: str = "p9") -> str:
    from manor import project as project_mod

    project_mod.add(conn, code, "検証用")
    return conn.execute("SELECT id FROM project WHERE code=?", (code,)).fetchone()["id"]


def test_ingest_folds_activity_and_report(conn, monkeypatch) -> None:
    monkeypatch.setenv("MANOR_NOW", "2026-10-09T12:10:00")
    events = [
        _ev("session_start", "2026-10-09T12:00:00+09:00"),
        _ev("prompt", "2026-10-09T12:01:00+09:00"),
        _ev("progress", "2026-10-09T12:05:00+09:00", report={"title": "評価の統合", "phase": "implementing",
                                                            "progress": 40, "human_next": "", "note": "途中"}),
    ]
    out = store.ingest(conn, events)
    assert out["accepted"] == 3
    assert store.ingest(conn, events)["accepted"] == 0  # 重複は捨てる
    rows = store.list_sessions(conn)
    assert rows[0]["activity"] == "working" and rows[0]["progress"] == 40 and rows[0]["title"] == "評価の統合"
    store.ingest(conn, [_ev("stop", "2026-10-09T12:06:00+09:00")])
    assert store.list_sessions(conn)[0]["activity"] == "waiting"  # 主人の確認が要るものが無い
    monkeypatch.setenv("MANOR_NOW", "2026-10-09T13:00:00")
    assert store.list_sessions(conn)[0]["activity"] == "idle"


def test_review_comes_first_then_waiting_after_ack(conn, monkeypatch) -> None:
    monkeypatch.setenv("MANOR_NOW", "2026-10-09T12:10:00")
    store.ingest(conn, [
        _ev("prompt", "2026-10-09T12:09:00+09:00", sid="a"),
        _ev("progress", "2026-10-09T11:00:00+09:00", sid="b",
            report={"title": "A 段", "phase": "implemented", "human_next": "実機で確認", "next_action": "B 段に着手"}),
        _ev("stop", "2026-10-09T11:01:00+09:00", sid="b"),
    ])
    rows = store.list_sessions(conn)
    assert [(r["session_id"], r["activity"]) for r in rows] == [("b", "review"), ("a", "working")]  # 確認待ちが先
    assert rows[0]["next_action"] == "B 段に着手"  # 確認待ちは時間が経っても休止にしない
    store.ack(conn, "b")
    b = [r for r in store.list_sessions(conn) if r["session_id"] == "b"][0]
    assert b["activity"] == "idle" and b["human_next_done"]  # 完了後は指示待ち（古ければ休止）
    store.ingest(conn, [_ev("stop", "2026-10-09T12:09:30+09:00", sid="b")])
    b = [r for r in store.list_sessions(conn) if r["session_id"] == "b"][0]
    assert b["activity"] == "waiting"
    assert any("推奨の次: B 段に着手" in line for line in store.format_active(conn))


def test_link_directory_and_task_reflection(conn, monkeypatch) -> None:
    monkeypatch.setenv("MANOR_NOW", "2026-10-09T12:10:00")
    pid = _project(conn)
    tid = task_mod.add(conn, "onnx を統合", project=pid)
    store.link(conn, "https://github.com/O/R.git", pid)
    entry = store.build_directory(conn)["github.com/o/r"]
    assert entry["projects"][0]["id"] == pid and entry["tasks"][0]["id"] == tid

    out = store.ingest(conn, [_ev("progress", "2026-10-09T12:05:00+09:00",
                                  report={"title": "統合", "phase": "implemented", "progress": 75,
                                          "human_next": "実機で確認", "task": tid.lower()})])
    assert out["phase_changes"] == [("s1", tid, "", "implemented")]
    assert relay.reflect_tasks(conn, out["phase_changes"]) == [tid]
    now = conn.execute("SELECT now FROM task WHERE id=?", (tid,)).fetchone()["now"]
    assert "[LAB] 統合：一区切り（75%）" in now and "実機で確認" in now
    row = store.list_sessions(conn)[0]
    assert row["project_id"] == pid and row["linked"] and row["task_title"] == "onnx を統合"
    # 同じ段階の数字だけの変化では書かない
    again = store.ingest(conn, [_ev("progress", "2026-10-09T12:07:00+09:00", report={"progress": 80})])
    assert again["phase_changes"] == []


def test_unlinked_repo_is_listed_for_asking(conn, monkeypatch) -> None:
    monkeypatch.setenv("MANOR_NOW", "2026-10-09T12:10:00")
    store.ingest(conn, [_ev("prompt", "2026-10-09T12:09:00+09:00")])
    lines = store.format_active(conn)
    assert any("未紐づけのリポジトリ: github.com/o/r" in line for line in lines)


# --- 偽の中継で往復 ------------------------------------------------------------------------


class _FakeRelay:
    def __init__(self, admin: str, machine_token: str) -> None:
        self.admin = hashlib.sha256(admin.encode()).hexdigest()
        self.machines = {hashlib.sha256(machine_token.encode()).hexdigest(): "LAB"}
        self.events: list[dict] = []
        self.directory: dict = {}
        self.down = False

    def handle(self, req: dict) -> dict:
        tok = hashlib.sha256(str(req.get("token", "")).encode()).hexdigest()
        admin = tok == self.admin
        machine = self.machines.get(tok)
        if not admin and not machine:
            return {"ok": False, "error": "forbidden"}
        op = req.get("op")
        if op == "ping":
            return {"ok": True, "machine": machine}
        if op == "events":
            seen = {e["event_id"] for e in self.events}
            n = 0
            for e in req.get("events", []):
                if e["event_id"] in seen:
                    continue
                e = dict(e, machine=machine or e.get("machine"), seq=len(self.events) + 1)
                self.events.append(e)
                seen.add(e["event_id"])
                n += 1
            out = {"ok": True, "accepted": n}
            if req.get("directory_key"):
                out["directory"] = self.directory.get(req["directory_key"])
            return out
        if not admin:
            return {"ok": False, "error": "forbidden"}
        if op == "pull":
            since = int(req.get("since", 0))
            evs = [e for e in self.events if e["seq"] > since][: int(req.get("limit", 500))]
            last = evs[-1]["seq"] if evs else since
            return {"ok": True, "events": evs, "last_seq": last, "more": last < len(self.events)}
        if op == "set_directory":
            self.directory = req.get("entries", {})
            return {"ok": True}
        return {"ok": False, "error": "unknown_op"}


@pytest.fixture
def fake_relay(monkeypatch, tmp_path):
    fake = _FakeRelay("ADMIN", "MTOKEN")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            if fake.down:
                self.send_response(503)
                self.end_headers()
                return
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
            data = json.dumps(fake.handle(body)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):  # noqa: D401
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/exec"
    monkeypatch.setenv("MANOR_REPORT_HOME", str(tmp_path / "client"))
    monkeypatch.setenv("MANOR_REPORT_ENDPOINT", url)
    monkeypatch.setenv("MANOR_REPORT_TOKEN", "MTOKEN")
    monkeypatch.setenv("MANOR_REPORT_MACHINE", "名乗りの名")
    monkeypatch.setattr(mr.time, "sleep", lambda s: None)  # 裏の送信のやり直しを待たない
    yield fake, url
    server.shutdown()


def test_round_trip_through_fake_relay(conn, home, fake_relay, monkeypatch, capsys, tmp_path) -> None:
    fake, url = fake_relay
    secrets_mod.set("remote", "endpoint", url)
    secrets_mod.set("remote", "admin_token", "ADMIN")
    monkeypatch.setattr("manor.slack.scan_for_leak_terms", lambda text: {"ok": True})
    pid = _project(conn)
    store.link(conn, "github.com/o/r", pid)
    relay.push_directory_if_changed(conn, home)
    assert "github.com/o/r" in fake.directory

    # SessionStart: 紐づけ表を受け取り、文脈に入れる
    monkeypatch.setattr(mr, "git_info", lambda cwd: {"remote": "github.com/o/r", "branch": "main",
                                                     "key": "github.com/o/r"})
    hook_in = json.dumps({"session_id": "S-1", "cwd": str(tmp_path)})
    assert mr.run_hook("SessionStart", hook_in) == 0
    injected = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "S-1" in injected and pid in injected

    # hook は待たずに手元へ置き、裏の flush が送る（試験ではその場で走らせる）
    flushes = []
    monkeypatch.setattr(mr, "spawn_flush", lambda sid, key: flushes.append(
        mr.cmd_flush(argparse.Namespace(quiet=True, acks_for=sid, directory_key=key))))
    fake.down = True
    mr.run_hook("UserPromptSubmit", hook_in)
    assert flushes == [1] and len(mr._read_outbox()) == 1  # 落ちている間は溜まる
    fake.down = False
    rc = mr.cmd_progress(argparse.Namespace(session="S-1", title="統合", phase="testing", progress=None,
                                            human_next="ビルド", project=None, task=None, note=None))
    assert rc == 0 and mr._read_outbox() == []
    assert [e["kind"] for e in fake.events] == ["session_start", "prompt", "progress"]
    assert {e["machine"] for e in fake.events} == {"LAB"}  # 名乗りではなく鍵の持ち主の名

    out = relay.pull(conn, home)
    assert out["accepted"] == 3
    row = store.list_sessions(conn)[0]
    assert (row["machine"], row["title"], row["phase_label"], row["progress"], row["human_next"]) == \
        ("LAB", "統合", "試験中", 85, "ビルド")
    assert row["project_id"] == pid
    assert relay.pull(conn, home)["accepted"] == 0  # カーソルが進んでいる


def test_headless_runs_are_not_reported(monkeypatch, capsys) -> None:
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "sdk-cli")
    sent = []
    monkeypatch.setattr(mr, "send_events", lambda *a, **k: sent.append(a) or (True, {}))
    assert mr.run_hook("SessionStart", json.dumps({"session_id": "x", "cwd": "."})) == 0
    assert sent == [] and capsys.readouterr().out == ""
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "claude-desktop")
    assert not mr.is_headless()
    monkeypatch.setenv("MANOR_REPORT_DISABLE", "1")
    assert mr.is_headless()


def test_ack_marks_human_next_done_until_it_changes(conn, monkeypatch) -> None:
    monkeypatch.setenv("MANOR_NOW", "2026-10-09T12:10:00")
    store.ingest(conn, [_ev("progress", "2026-10-09T12:05:00+09:00",
                            report={"title": "a", "phase": "implemented", "human_next": "Drive を掃除"})])
    assert store.ack(conn, "s1") == "Drive を掃除"
    assert store.list_sessions(conn)[0]["human_next_done"] is True
    store.ingest(conn, [_ev("progress", "2026-10-09T12:08:00+09:00", report={"human_next": "ビルド"})])
    assert store.list_sessions(conn)[0]["human_next_done"] is False
    assert store.ack(conn, "nope") is None


def test_first_prompt_injects_and_delivers_acks(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.setenv("MANOR_REPORT_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "claude-desktop")
    monkeypatch.setattr(mr, "git_info", lambda cwd: {"remote": None, "branch": None, "key": "dir:x"})
    acks = [{"text": "Drive を掃除", "done_at": "2026-10-09T12:00:00"}]
    spawned = []
    monkeypatch.setattr(mr, "spawn_flush", lambda sid, key: spawned.append((sid, key)))
    # 裏の送信が前回受け取った「済んだ」が控えにある状態
    mr.save_state("S", {"acks_inbox": acks})
    payload = json.dumps({"session_id": "S", "cwd": str(tmp_path)})
    mr.run_hook("UserPromptSubmit", payload)
    assert spawned == [("S", "dir:x")] and len(mr._read_outbox()) == 1  # 待たずに手元へ置いて裏で送る
    ctx = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "【manor への進捗報告】" in ctx and "「Drive を掃除」を済ませた" in ctx
    mr.run_hook("UserPromptSubmit", payload)  # 2度目: 作法も同じ「済んだ」も繰り返さない
    assert capsys.readouterr().out == ""


def test_hooks_return_without_waiting_for_relay(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MANOR_REPORT_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    monkeypatch.setattr(mr, "git_info", lambda cwd: {"remote": None, "branch": None, "key": "dir:x"})
    monkeypatch.setattr(mr, "send_events", lambda *a, **k: pytest.fail("Stop で中継を待ってはいけない"))
    monkeypatch.setattr(mr, "spawn_flush", lambda sid, key: None)
    mr.save_state("S", {"injected": True})
    assert mr.run_hook("Stop", json.dumps({"session_id": "S", "cwd": str(tmp_path)})) == 0
    assert [e["kind"] for e in mr._read_outbox()] == ["stop"]


def test_hold_parks_review_until_unheld_or_new_human_next(conn, monkeypatch) -> None:
    monkeypatch.setenv("MANOR_NOW", "2026-10-09T12:10:00")
    store.ingest(conn, [
        _ev("progress", "2026-10-09T12:00:00+09:00", report={"title": "a", "phase": "implemented",
                                                            "human_next": "方針を判断"}),
        _ev("stop", "2026-10-09T12:01:00+09:00"),
    ])
    assert store.list_sessions(conn)[0]["activity"] == "review"
    assert store.ack(conn, "s1", kind="hold") == "方針を判断"
    row = store.list_sessions(conn)[0]
    assert (row["activity"], row["held"], row["human_next_done"]) == ("hold", True, False)
    monkeypatch.setenv("MANOR_NOW", "2026-10-09T18:00:00")
    assert store.list_sessions(conn)[0]["activity"] == "hold"  # 時間が経っても保留のまま
    assert store.unhold(conn, "s1") == 1
    assert store.list_sessions(conn)[0]["activity"] == "review"
    store.ack(conn, "s1", kind="hold")
    store.ingest(conn, [_ev("progress", "2026-10-09T17:59:00+09:00", report={"human_next": "別の確認"})])
    assert store.list_sessions(conn)[0]["activity"] == "review"  # 新しい確認事項が来れば外れる


def test_same_ack_is_told_once() -> None:
    state: dict = {}
    acks = [{"text": "X", "done_at": f"2026-10-09T12:0{i}:00"} for i in range(4)]
    assert mr.new_acks(state, acks) == ["X"]
    assert mr.new_acks(state, acks) == []


def test_close_until_next_prompt(conn, monkeypatch) -> None:
    monkeypatch.setenv("MANOR_NOW", "2026-10-09T12:10:00")
    store.ingest(conn, [_ev("prompt", "2026-10-09T12:00:00+09:00"), _ev("stop", "2026-10-09T12:05:00+09:00")])
    assert store.list_sessions(conn)[0]["activity"] == "waiting"
    assert store.close(conn, "s1") and not store.close(conn, "nope")
    row = store.list_sessions(conn)[0]
    assert (row["activity"], row["closed"]) == ("closed", True)
    assert store.format_active(conn) == []  # 起動時の射影には出さない
    monkeypatch.setenv("MANOR_NOW", "2026-10-20T12:00:00")
    assert store.list_sessions(conn) == []  # 7日を過ぎたら「古いものも出す」でだけ
    assert store.list_sessions(conn, include_ended=True)[0]["activity"] == "closed"
    monkeypatch.setenv("MANOR_NOW", "2026-10-21T09:00:30")
    store.ingest(conn, [_ev("prompt", "2026-10-21T09:00:00+09:00")])  # 続きを話しかけたら自然に戻る
    assert store.list_sessions(conn)[0]["activity"] == "working"
    store.ingest(conn, [_ev("stop", "2026-10-21T09:00:20+09:00")])
    assert store.list_sessions(conn)[0]["activity"] == "waiting"


def test_reopen_restores_previous_state(conn, monkeypatch) -> None:
    monkeypatch.setenv("MANOR_NOW", "2026-10-09T12:10:00")
    store.ingest(conn, [_ev("progress", "2026-10-09T12:00:00+09:00", report={"human_next": "確認"}),
                        _ev("stop", "2026-10-09T12:01:00+09:00")])
    store.close(conn, "s1")
    assert store.reopen(conn, "s1") == 1
    assert store.list_sessions(conn)[0]["activity"] == "review"


def test_spawn_flush_never_opens_a_console(monkeypatch) -> None:
    seen = {}
    monkeypatch.setattr(mr.os, "name", "nt")
    monkeypatch.setattr(mr.subprocess, "Popen", lambda args, **kw: seen.update(args=args, **kw))
    mr.spawn_flush("S", "dir:x")
    flags = seen["creationflags"]
    assert not flags & 0x00000008  # DETACHED_PROCESS（venv の中継ぎ越しに窓が開く）を使わない
    assert flags & 0x08000000  # CREATE_NO_WINDOW
