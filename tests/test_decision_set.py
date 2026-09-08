"""裁定を下す前に書き直せること、詳細欄が長いと警告が出ること（2026-09-08）。

主人のご指摘「タスク起票の件ですが、退化しています。……この長い文章は読みたくありません」。
作法（詳細欄は4つ・それぞれ1〜2文）は v1 の `CLAUDE.md` にあったが、v2 への移行で
節ごと落ちていた。規則を戻すだけでは同じことが起きるので、**書いた瞬間に警告する**形に。
あわせて `manor decision set` を新設——書き直す道が無く、起票時の長文が主人の画面に
残り続けていた。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from manor import cli
from manor import db
from manor import decision as decision_mod
from manor import task as task_mod


def _open_decision(conn) -> tuple[str, str]:
    task_id = task_mod.add(conn, "対象のタスク")
    did = decision_mod.ask(
        conn, "決めること", task_id=task_id, recommend="最初の推奨", background="最初の背景"
    )
    conn.commit()
    return did, task_id


def test_decision_set_rewrites_open_decision(home_path: Path) -> None:
    assert cli.main(["init"]) == 0
    conn = db.connect(home_path)
    did, task_id = _open_decision(conn)

    decision_mod.set_fields(conn, did, recommend="短い推奨", background="短い背景")
    conn.commit()

    row = decision_mod.show(conn, did)
    assert row["recommendation"] == "短い推奨"
    assert row["background"] == "短い背景"


def test_decision_set_also_updates_the_task_recommendation(home_path: Path) -> None:
    """`ask` が task 側にも推奨を写しているので、書き直しも両方に効くこと。

    片方だけ直すと板と画面で食い違う（同じ値が2箇所にある形）。
    """
    assert cli.main(["init"]) == 0
    conn = db.connect(home_path)
    did, task_id = _open_decision(conn)

    decision_mod.set_fields(conn, did, recommend="短い推奨")
    conn.commit()

    row = conn.execute("SELECT recommendation FROM task WHERE id = ?", (task_id,)).fetchone()
    assert row["recommendation"] == "短い推奨", "task 側が古い推奨のまま残っている"


def test_decision_set_refuses_after_ruling(home_path: Path) -> None:
    """裁定済みは書き直せない（履歴を書き換えない）。"""
    assert cli.main(["init"]) == 0
    conn = db.connect(home_path)
    did, _ = _open_decision(conn)
    decision_mod.rule(conn, did, "approved", ruling="")
    conn.commit()

    with pytest.raises(Exception) as exc:
        decision_mod.set_fields(conn, did, recommend="あとから書き換え")
    assert "書き直せません" in str(exc.value) or "already" in str(exc.value).lower()


def test_long_fields_warn_but_still_write(
    home_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """目安を超えたら stderr に出るが、**書けている**（止めない）。"""
    assert cli.main(["init"]) == 0
    capsys.readouterr()

    long_goal = "あ" * 200
    assert cli.main(["task", "add", "短い題名", "--goal", long_goal, "--json"]) == 0
    captured = capsys.readouterr()

    assert "200" in captured.err, f"長さの警告が出ていない: {captured.err!r}"
    assert "120" in captured.err, "目安の値が出ていない"
    assert '"id"' in captured.out, "警告のせいで起票が止まっている"


def test_short_fields_stay_quiet(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    """目安の内側では黙る（誤検出を出す検査は入れない・B99）。"""
    assert cli.main(["init"]) == 0
    capsys.readouterr()

    assert cli.main(["task", "add", "短い題名", "--goal", "短い目的", "--json"]) == 0
    assert capsys.readouterr().err.strip() == ""
