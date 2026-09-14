"""意見箱（`#idea` / `#improve`）と、日誌を夜へ寄せる件（2026-09-08・主人のご要望）。

> 意見箱を読んで改善するルートはまだ生きてますか？
> 日誌書くのは朝じゃなくて夜間タスクの1つにできませんか？（トークン消費の面でも夜中）

⚠ 意見箱は**生きていませんでした**。v2 への移行時に `INTAKE_PREFIXES` から `#idea` が
落ち、コメントに「v2 に受け皿が無いので入れていない」と書かれたまま、受け皿を作る話が
消えていました。主人がお尋ねになるまで、死んでいることに誰も気づいていません。
"""

from __future__ import annotations

from pathlib import Path

from manor import cli
from manor import db
from manor import slack as slack_mod
from manor.night import runner


def test_idea_and_improve_are_taken_in() -> None:
    assert slack_mod.parse_intake("#idea 板が見づらい") == {"kind": "idea", "body": "板が見づらい"}
    assert slack_mod.parse_intake("#improve 通知が多い") == {"kind": "idea", "body": "通知が多い"}
    # 接頭辞が無ければ黙る（雑談に反応しない）
    assert slack_mod.parse_intake("板が見づらいですね") is None


def test_idea_lands_as_a_triage_item_not_in_the_todo_pile(home_path: Path) -> None:
    """改善要望は `hold`（仕分け待ち）で入る。

    ⚠ `todo` で入れると板の未着手に紛れ、題名だけが並ぶ——主人がご自分で起こされた
    T26 が実際にそうなり、「どこにいったか分からなくなった」と仰った（2026-09-08）。
    """
    assert cli.main(["init"]) == 0
    conn = db.connect(home_path)

    task_id = slack_mod._create_from_intake(conn, kind="idea", body="板が見づらい\n詳しくは…")
    conn.commit()

    row = conn.execute(
        "SELECT t.status, t.owner, n.title, n.body FROM task t JOIN node n ON n.id = t.id WHERE t.id = ?",
        (task_id,),
    ).fetchone()
    assert row["status"] == "hold", "仕分け待ちになっていない"
    assert row["owner"] == "master", "主人の要望なのに執事の持ち物になっている"
    assert row["title"] == "板が見づらい"
    assert "詳しくは" in str(row["body"]), "本文を丸ごと残していない"


def test_idea_guesses_the_project_from_its_title(home_path: Path) -> None:
    """D16「意見箱も同様に」: 本文にプロジェクトのタイトルの語が出てきたら project_id を埋める。"""
    from manor import project as project_mod

    assert cli.main(["init"]) == 0
    conn = db.connect(home_path)
    project_mod.add(conn, "vra", "VRAcademy 2台同期収録（会社）", kind="会社")
    conn.commit()

    task_id = slack_mod._create_from_intake(
        conn, kind="idea", body="VRAcademyの意見箱への導線を増やしてほしい"
    )
    conn.commit()

    row = conn.execute("SELECT project_id FROM task WHERE id = ?", (task_id,)).fetchone()
    project_row = conn.execute("SELECT id FROM project WHERE code = 'vra'").fetchone()
    assert row["project_id"] == project_row["id"]


def test_install_command_carries_the_diary_flag() -> None:
    """登録するコマンドに `--diary` が出ること（設定ファイルへ隠さない）。"""
    cmd = runner.build_install_command(at="02:00", sleep_back_after=True, diary_after=True)
    assert "run --sleep-back --diary" in cmd

    plain = runner.build_install_command(at="02:00")
    assert "--diary" not in plain
    assert "--sleep-back" not in plain


def test_diary_is_skipped_when_the_night_did_not_run(home_path: Path, monkeypatch) -> None:
    """夜勤が動かなかった晩（`locked` / `empty`）は日誌を書かない。

    書いてしまうと「何もしていない日の日誌」が毎晩1本ずつ Notion に積まれる。
    """
    called: list[str] = []
    monkeypatch.setattr(runner, "_write_diary_safely", lambda home, echo=True: called.append("x"))
    monkeypatch.setattr(runner, "_run_impl", lambda home, **kw: {"status": "empty"})
    monkeypatch.setattr(runner, "_voice_restore_safely", lambda home: None)
    monkeypatch.setattr(runner, "_voice_mute_safely", lambda home: None)

    runner.run(home_path, diary_after=True)
    assert called == [], "指示が空の晩にも日誌を書こうとしている"
