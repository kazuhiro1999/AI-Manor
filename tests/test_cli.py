"""CLI の一連（`cli.main([...])` を subprocess ではなく直接回す。ADR-001 §5）。"""

from __future__ import annotations

import json
from pathlib import Path

from manor import cli


def test_init_then_task_add_status_show_active_check(home_path: Path, capsys):
    assert cli.main(["init"]) == 0
    capsys.readouterr()

    assert cli.main(["project", "add", "demo", "デモ計画", "--preset", "fast"]) == 0
    capsys.readouterr()

    assert cli.main(["task", "add", "設計を書く", "--project", "demo", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    task_id = out["id"]
    assert task_id.startswith("T")

    assert cli.main(["task", "status", task_id, "doing", "--json"]) == 0
    status_out = json.loads(capsys.readouterr().out)
    assert status_out["status"] == "doing"

    assert cli.main(["task", "show", task_id, "--json"]) == 0
    show_out = json.loads(capsys.readouterr().out)
    assert show_out["id"] == task_id
    assert show_out["status"] == "doing"

    assert cli.main(["active", "--json"]) == 0
    active_out = json.loads(capsys.readouterr().out)
    assert any(t["id"] == task_id for t in active_out["section_b"])

    assert cli.main(["check", "--json"]) == 0
    check_out = json.loads(capsys.readouterr().out)
    assert check_out["ok"] is True

    assert cli.main(["task", "done", task_id, "--note", "完了"]) == 0
    capsys.readouterr()

    assert cli.main(["render", "--json"]) == 0
    render_out = json.loads(capsys.readouterr().out)
    assert "projections/QUEUE.md" in render_out["written"]

    assert cli.main(["check"]) == 0
    capsys.readouterr()


def test_init_is_idempotent(home_path: Path, capsys):
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    assert (home_path / "manor.db").is_file()


def test_missing_db_gives_exit_1(home_path: Path, capsys):
    code = cli.main(["task", "list"])
    assert code == 1
    out = capsys.readouterr().out
    assert "init" in out


def test_task_add_unknown_class_is_exit_2(home_path: Path, capsys):
    cli.main(["init"])
    capsys.readouterr()
    code = cli.main(["task", "add", "何か", "--class", "no_such_class"])
    assert code == 2


def test_task_status_invalid_vocab_is_exit_2(home_path: Path, capsys):
    cli.main(["init"])
    capsys.readouterr()
    cli.main(["task", "add", "何か", "--json"])
    tid = json.loads(capsys.readouterr().out)["id"]
    code = cli.main(["task", "status", tid, "not_a_status"])
    assert code == 2


def test_import_v1_requires_queue_and_projects(home_path: Path, capsys):
    """段3で import-v1 を実装したので、以前のスタブ（未実装）試験を差し替える。

    ADR-003 §4 の CLI 契約は `--queue` と `--projects` を必須としている。
    片方でも欠けたら（ManorError, code=2）で止まることだけをここでは確かめる。
    詳しい取り込みの試験は tests/test_import_v1.py にある。
    """
    cli.main(["init"])
    capsys.readouterr()
    code = cli.main(["import-v1"])
    assert code == 2
    out = capsys.readouterr().out
    assert "--queue" in out and "--projects" in out


def test_decision_and_handoff_round_trip(home_path: Path, capsys):
    cli.main(["init"])
    capsys.readouterr()
    cli.main(["task", "add", "GPU購入", "--json"])
    tid = json.loads(capsys.readouterr().out)["id"]

    code = cli.main(
        [
            "decision",
            "ask",
            "GPUを買うか",
            "--task",
            tid,
            "--recommend",
            "買う",
            "--background",
            "実験が遅い",
            "--json",
        ]
    )
    assert code == 0
    did = json.loads(capsys.readouterr().out)["id"]

    assert cli.main(["decision", "rule", did, "approved", "--ruling", "よし"]) == 0
    capsys.readouterr()

    assert (
        cli.main(["handoff", "new", tid, "--to", "chef", "--scope", "x/**", "--json"]) == 0
    )
    hid = json.loads(capsys.readouterr().out)["id"]

    assert cli.main(["handoff", "list", "--json"]) == 0
    handoffs = json.loads(capsys.readouterr().out)
    assert any(h["id"] == hid for h in handoffs)


def test_ctx_command_outputs_markdown_by_default(home_path: Path, capsys):
    cli.main(["init"])
    capsys.readouterr()
    cli.main(["task", "add", "設計", "--json"])
    tid = json.loads(capsys.readouterr().out)["id"]
    assert cli.main(["ctx", tid]) == 0
    out = capsys.readouterr().out
    assert tid in out
