"""委譲の型（ADR-001 §8）。指示書の見出し10個・報告の必須見出し・accept/reject。"""

from __future__ import annotations

from pathlib import Path

import pytest

from manor import handoff as handoff_mod
from manor import task as task_mod
from manor.errors import ManorError

REQUIRED_BRIEF_HEADINGS = (
    "## 役割とスコープ",
    "## 背景",
    "## 目的 / 今の状態 / 次の一手",
    "## Project Policy",
    "## 制約",
    "## 関連（グラフから）",
    "## 成果物と置き場",
    "## 検証要件",
    "## 定型",
    "## 報告の型",
)

GOOD_REPORT = """\
## やったこと
やった

## 証跡
証拠

## やっていないこと
なし

## 曖昧だった点
なし

## 主人にしか決められないこと
なし
"""


def test_brief_has_all_ten_headings(conn, home: Path):
    tid = task_mod.add(conn, "献立を考える", goal="夕食")
    result = handoff_mod.new(conn, home, tid, "chef", scope="src/manor/staff/chef/**")
    brief = result["brief"]
    for heading in REQUIRED_BRIEF_HEADINGS:
        assert heading in brief, f"見出しが無い: {heading}"
    assert brief.count("## ") >= len(REQUIRED_BRIEF_HEADINGS)


def test_new_writes_file_and_sets_task_doing(conn, home: Path):
    tid = task_mod.add(conn, "献立を考える")
    result = handoff_mod.new(conn, home, tid, "chef")
    assert Path(result["path"]).is_file()
    row = conn.execute("SELECT status, owner FROM task WHERE id = ?", (tid,)).fetchone()
    assert row["status"] == "doing"
    assert row["owner"] == "chef"


def test_new_links_delegated_to_agent(conn, home: Path):
    tid = task_mod.add(conn, "献立を考える")
    handoff_mod.new(conn, home, tid, "chef")
    edge = conn.execute(
        "SELECT * FROM edge WHERE src = ? AND rel = 'delegated_to'", (tid,)
    ).fetchone()
    assert edge is not None
    assert edge["dst"] == "A:chef"


def test_report_missing_headings_rejected(conn, home: Path):
    tid = task_mod.add(conn, "献立を考える")
    result = handoff_mod.new(conn, home, tid, "chef")
    with pytest.raises(ManorError) as excinfo:
        handoff_mod.report(conn, result["id"], "## やったこと\nやった\n")
    msg = excinfo.value.message_ja
    assert "証跡" in msg
    assert "やっていないこと" in msg
    assert "曖昧だった点" in msg
    assert "主人にしか決められないこと" in msg


def test_report_all_headings_accepted(conn, home: Path):
    tid = task_mod.add(conn, "献立を考える")
    result = handoff_mod.new(conn, home, tid, "chef")
    out = handoff_mod.report(conn, result["id"], GOOD_REPORT)
    assert out["id"] == result["id"]
    row = conn.execute("SELECT report FROM handoff WHERE id = ?", (result["id"],)).fetchone()
    assert row["report"] == GOOD_REPORT


def test_accept_returns_owner_to_butler(conn, home: Path):
    tid = task_mod.add(conn, "献立を考える")
    result = handoff_mod.new(conn, home, tid, "chef")
    handoff_mod.report(conn, result["id"], GOOD_REPORT)
    out = handoff_mod.accept(conn, result["id"], note="良い")
    assert out["verdict"] == "accepted"
    row = conn.execute("SELECT status, owner FROM task WHERE id = ?", (tid,)).fetchone()
    assert row["owner"] == "butler"
    assert row["status"] == "doing"


def test_reject_requires_note(conn, home: Path):
    tid = task_mod.add(conn, "献立を考える")
    result = handoff_mod.new(conn, home, tid, "chef")
    with pytest.raises(ManorError):
        handoff_mod.reject(conn, result["id"], note="")
    out = handoff_mod.reject(conn, result["id"], note="やり直し")
    assert out["verdict"] == "rejected"


def test_reject_returns_task_to_butler_on_hold(conn, home: Path):
    """ADR-001 §13 裁定5: 却下したら owner は執事へ戻り、hold に置かれ、理由が残る。"""
    tid = task_mod.add(conn, "献立を考える")
    result = handoff_mod.new(conn, home, tid, "chef")
    before = task_mod.show(conn, tid)
    assert before["owner"] == "chef" and before["status"] == "doing"
    handoff_mod.reject(conn, result["id"], note="証跡が無い")
    after = task_mod.show(conn, tid)
    assert after["owner"] == "butler"
    assert after["status"] == "hold"
    assert "証跡が無い" in after["status_note"]
    open_list = handoff_mod.list_handoffs(conn, open_only=True)
    assert not any(h["id"] == result["id"] for h in open_list)


def test_list_handoffs_open_filter(conn, home: Path):
    tid = task_mod.add(conn, "献立を考える")
    result = handoff_mod.new(conn, home, tid, "chef")
    open_list = handoff_mod.list_handoffs(conn, open_only=True)
    assert any(h["id"] == result["id"] for h in open_list)
    handoff_mod.accept(conn, result["id"])
    open_list_after = handoff_mod.list_handoffs(conn, open_only=True)
    assert not any(h["id"] == result["id"] for h in open_list_after)
