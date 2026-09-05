"""主人のプロフィール／初回セットアップ（ADR-007）の試験。**合成データのみ**
（架空の家庭。人名は入らない）。

`profile.py` の関数レベルと `manor profile ...` / `manor setup ...`（CLI）の両方を確かめる。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from manor import cli
from manor import policy
from manor import profile as profile_mod
from manor import project as project_mod
from manor import task as task_mod
from manor.errors import ManorError

# --- 語彙 ---------------------------------------------------------------------------


def test_purposes_and_presets_are_ordered_dicts() -> None:
    assert profile_mod.PURPOSES["tasks"] == "タスク・プロジェクトの管理"
    assert set(profile_mod.PURPOSES) == {"tasks", "kitchen", "money", "house", "secretary"}
    assert set(profile_mod.PRESETS) == {"careful", "standard", "fast"}


# --- set_many / get_all --------------------------------------------------------------


def test_set_many_roundtrip(conn) -> None:
    profile_mod.set_many(conn, {"master.callname": "旦那様", "butler.callname": "セバスチャン"})
    data = profile_mod.get_all(conn)
    assert data["master.callname"] == "旦那様"
    assert data["butler.callname"] == "セバスチャン"


def test_set_many_rejects_unknown_key(conn) -> None:
    with pytest.raises(ManorError) as excinfo:
        profile_mod.set_many(conn, {"よそ者.key": "x"})
    assert excinfo.value.code == 2


def test_set_many_purposes_serializes_json_list(conn) -> None:
    profile_mod.set_many(conn, {"purposes": ["tasks", "kitchen"]})
    data = profile_mod.get_all(conn)
    assert data["purposes"] == '["tasks", "kitchen"]'
    assert profile_mod.purposes_of(conn) == ["tasks", "kitchen"]


def test_set_many_purposes_rejects_unknown_id(conn) -> None:
    with pytest.raises(ManorError) as excinfo:
        profile_mod.set_many(conn, {"purposes": ["research", "よそ者"]})
    assert excinfo.value.code == 2


def test_set_many_purposes_rejects_non_list(conn) -> None:
    with pytest.raises(ManorError):
        profile_mod.set_many(conn, {"purposes": "tasks"})


def test_get_all_empty_when_nothing_set(conn) -> None:
    assert profile_mod.get_all(conn) == {}


# --- is_setup_done / summary_line / status --------------------------------------------


def test_is_setup_done_false_then_true(conn) -> None:
    assert profile_mod.is_setup_done(conn) is False
    profile_mod.set_many(conn, {"setup.completed_at": "2026-09-03T10:00:00"})
    assert profile_mod.is_setup_done(conn) is True


def test_summary_line_empty_when_callname_unset(conn) -> None:
    assert profile_mod.summary_line(conn) == ""
    profile_mod.set_many(conn, {"butler.callname": "セバスチャン"})
    assert profile_mod.summary_line(conn) == ""  # callname が無ければ空のまま


def test_summary_line_with_callname_and_purposes(conn) -> None:
    profile_mod.set_many(
        conn,
        {
            "master.callname": "旦那様",
            "butler.callname": "セバスチャン",
            "purposes": ["tasks", "kitchen"],
        },
    )
    line = profile_mod.summary_line(conn)
    assert line.startswith("主人の呼び名: 旦那様")
    assert "執事: セバスチャン" in line
    assert "タスク・プロジェクトの管理" in line and "料理・買い物" in line


def test_summary_line_defaults_butler_name(conn) -> None:
    profile_mod.set_many(conn, {"master.callname": "旦那様"})
    line = profile_mod.summary_line(conn)
    assert "執事: 執事" in line  # butler.callname 未設定は既定「執事」


def test_status_shape(conn) -> None:
    st = profile_mod.status(conn)
    assert st == {"done": False, "completed_at": None, "profile": {}}
    profile_mod.set_many(conn, {"setup.completed_at": "2026-09-03T10:00:00"})
    st = profile_mod.status(conn)
    assert st["done"] is True
    assert st["completed_at"] == "2026-09-03T10:00:00"


# --- apply_setup: 正常系 ---------------------------------------------------------------


def test_apply_setup_creates_profile_project_task(conn) -> None:
    answers = {
        "callname": "旦那様",
        "butler_name": "セバスチャン",
        "purposes": ["tasks", "kitchen"],
        "note": "博士論文と家事の両立",
        "projects": [{"code": "paper", "name": "博士論文", "preset": "careful"}],
        "tasks": [{"title": "章立てを書く", "project_code": "paper", "cls": "research"}],
    }
    result = profile_mod.apply_setup(conn, answers)
    conn.commit()

    assert len(result["created"]["projects"]) == 1
    assert len(result["created"]["tasks"]) == 1
    assert profile_mod.is_setup_done(conn) is True

    project_row = project_mod.resolve(conn, "paper")
    assert project_row["preset"] == "careful"

    task_id = result["created"]["tasks"][0]
    task_row = task_mod.show(conn, task_id)
    assert task_row["project_id"] == project_row["id"]
    # cls=research の既定は L3。project の preset=careful が1段下げる（policy.resolve と同じ計算）。
    assert task_row["level"] == policy.resolve("research", "careful")


def test_apply_setup_without_projects_or_tasks(conn) -> None:
    result = profile_mod.apply_setup(conn, {"callname": "旦那様"})
    conn.commit()
    assert result["created"] == {"projects": [], "tasks": []}
    assert profile_mod.get_all(conn)["master.callname"] == "旦那様"
    assert profile_mod.get_all(conn)["butler.callname"] == "執事"


def test_apply_setup_rerun_overwrites_profile_and_adds_more(conn) -> None:
    profile_mod.apply_setup(conn, {"callname": "旦那様"})
    conn.commit()
    result2 = profile_mod.apply_setup(
        conn, {"callname": "旦那様様", "projects": [{"code": "paper", "name": "論文"}]}
    )
    conn.commit()
    assert profile_mod.get_all(conn)["master.callname"] == "旦那様様"
    assert len(result2["created"]["projects"]) == 1


# --- apply_setup: 原子性・検証 -----------------------------------------------------------


def test_apply_setup_defaults_callname_when_empty(conn) -> None:
    """§6 D8: callname はもう必須ではない——空／未指定なら既定「ご主人様」。"""
    result = profile_mod.apply_setup(conn, {})
    conn.commit()
    assert result["profile"]["master.callname"] == "ご主人様"

    result2 = profile_mod.apply_setup(conn, {"callname": "  "})
    conn.commit()
    assert result2["profile"]["master.callname"] == "ご主人様"


def test_apply_setup_unknown_task_class_rolls_back_everything(conn) -> None:
    """D2「どれか1つでも失敗すれば全部戻す」——project が先に作られていても、
    後続の task が語彙外の class で失敗すれば、呼び出し側の rollback で project も消える
    （atomicity。ADR-007 §4 の試験観点）。
    """
    answers = {
        "callname": "旦那様",
        "projects": [{"code": "paper", "name": "博士論文"}],
        "tasks": [{"title": "だめなタスク", "project_code": "paper", "cls": "よそ者クラス"}],
    }
    with pytest.raises(ManorError) as excinfo:
        profile_mod.apply_setup(conn, answers)
    assert excinfo.value.code == 2
    conn.rollback()  # web/CLI と同じく、呼び出し側が失敗時に rollback する

    assert conn.execute("SELECT 1 FROM project WHERE code = 'paper'").fetchone() is None
    assert profile_mod.get_all(conn) == {}  # profile の書き込みも戻っている
    assert profile_mod.is_setup_done(conn) is False


def test_apply_setup_missing_project_code(conn) -> None:
    with pytest.raises(ManorError) as excinfo:
        profile_mod.apply_setup(conn, {"callname": "旦那様", "projects": [{"name": "名前だけ"}]})
    assert "projects[0].code" in excinfo.value.message_ja


def test_apply_setup_missing_task_title(conn) -> None:
    with pytest.raises(ManorError) as excinfo:
        profile_mod.apply_setup(
            conn, {"callname": "旦那様", "tasks": [{"cls": "research"}]}
        )
    assert "tasks[0].title" in excinfo.value.message_ja


def test_apply_setup_hg_fixed_class_requires_recommendation(conn) -> None:
    """HG 固定クラスはウィザードの選択肢に出さない設計だが、機構としても
    `task.add` 自身が `recommendation` 無しでは拒否する（二重の安全）。
    """
    answers = {
        "callname": "旦那様",
        "tasks": [{"title": "外部送信", "cls": "external_send"}],
    }
    with pytest.raises(ManorError):
        profile_mod.apply_setup(conn, answers)


# --- apply_setup: kitchen（§6 D9） -------------------------------------------------------


def test_apply_setup_kitchen_writes_chef_taste(conn) -> None:
    answers = {
        "callname": "旦那様",
        "kitchen": {"household_size": 2, "allergies": "えび、そば", "dislikes": ""},
    }
    profile_mod.apply_setup(conn, answers)
    conn.commit()

    row = conn.execute("SELECT value FROM chef_taste WHERE key = 'household_size'").fetchone()
    assert row["value"] == "2"
    row = conn.execute("SELECT value FROM chef_taste WHERE key = 'allergies'").fetchone()
    assert row["value"] == "えび、そば"
    # 空文字は無視する（dislikes は書かない）
    assert conn.execute("SELECT 1 FROM chef_taste WHERE key = 'dislikes'").fetchone() is None
    # chef_taste は部下の表——profile には持たない（真実を2箇所にしない）
    assert "kitchen" not in profile_mod.get_all(conn)


def test_apply_setup_kitchen_does_not_overwrite_existing_value(conn) -> None:
    """D8: 既に値がある鍵は上書きしない。"""
    conn.execute(
        "INSERT INTO chef_taste (key, value, updated_at) VALUES ('allergies', '牛乳', '2026-01-01T00:00:00')"
    )
    conn.commit()

    profile_mod.apply_setup(conn, {"callname": "旦那様", "kitchen": {"allergies": "えび"}})
    conn.commit()

    row = conn.execute("SELECT value FROM chef_taste WHERE key = 'allergies'").fetchone()
    assert row["value"] == "牛乳"


def test_apply_setup_kitchen_installs_chef_schema_if_missing(conn) -> None:
    """chef のスキーマがまだ当たっていない home でも、kitchen 答えがあれば
    `db.ensure_staff_schema` で当ててから書く（DDL を手書きしない）。
    """
    conn.execute("DROP TABLE chef_taste")
    conn.commit()

    profile_mod.apply_setup(conn, {"callname": "旦那様", "kitchen": {"household_size": 3}})
    conn.commit()

    row = conn.execute("SELECT value FROM chef_taste WHERE key = 'household_size'").fetchone()
    assert row["value"] == "3"


def test_apply_setup_without_kitchen_answers_does_not_touch_chef_taste(conn) -> None:
    profile_mod.apply_setup(conn, {"callname": "旦那様"})
    conn.commit()
    assert conn.execute("SELECT 1 FROM chef_taste").fetchone() is None


# --- apply_setup: money（§6 D9） ---------------------------------------------------------


def test_apply_setup_money_app_and_default_currency(conn) -> None:
    result = profile_mod.apply_setup(conn, {"callname": "旦那様", "money": {"app": "zaim"}})
    conn.commit()
    assert result["profile"]["money.app"] == "zaim"
    assert result["profile"]["money.currency"] == "JPY"  # 既定


def test_apply_setup_money_app_none_and_explicit_currency(conn) -> None:
    result = profile_mod.apply_setup(
        conn, {"callname": "旦那様", "money": {"app": "none", "currency": "usd"}}
    )
    conn.commit()
    assert result["profile"]["money.app"] == "none"
    assert result["profile"]["money.currency"] == "USD"  # 大文字化


def test_apply_setup_money_unknown_app_rolls_back_everything(conn) -> None:
    answers = {
        "callname": "旦那様",
        "projects": [{"code": "paper", "name": "博士論文"}],
        "money": {"app": "よそ者アプリ"},
    }
    with pytest.raises(ManorError) as excinfo:
        profile_mod.apply_setup(conn, answers)
    assert excinfo.value.code == 2
    conn.rollback()

    assert profile_mod.get_all(conn) == {}
    assert conn.execute("SELECT 1 FROM project WHERE code = 'paper'").fetchone() is None


def test_apply_setup_money_bad_currency_is_rejected(conn) -> None:
    with pytest.raises(ManorError) as excinfo:
        profile_mod.apply_setup(conn, {"callname": "旦那様", "money": {"currency": "円"}})
    assert excinfo.value.code == 2


def test_apply_setup_without_money_answers_does_not_touch_profile(conn) -> None:
    result = profile_mod.apply_setup(conn, {"callname": "旦那様"})
    conn.commit()
    assert "money.app" not in result["profile"]
    assert "money.currency" not in result["profile"]


# --- CLI（`manor profile ...` `manor setup ...`） ---------------------------------------


def test_cli_profile_show_set_roundtrip(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()

    assert cli.main(["profile", "set", "master.callname", "旦那様", "--json"]) == 0
    capsys.readouterr()

    assert cli.main(["profile", "show", "--json"]) == 0
    out = capsys.readouterr().out
    assert "旦那様" in out


def test_cli_profile_set_purposes_json(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    assert cli.main(["profile", "set", "purposes", '["tasks","kitchen"]', "--json"]) == 0
    capsys.readouterr()
    assert cli.main(["profile", "show", "--json"]) == 0
    out = capsys.readouterr().out
    assert "tasks" in out and "kitchen" in out


def test_cli_profile_set_unknown_key_is_exit_2(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    code = cli.main(["profile", "set", "よそ者.key", "x"])
    assert code == 2


def test_cli_setup_status_before_and_after(
    home_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()

    assert cli.main(["setup", "--status", "--json"]) == 0
    out = capsys.readouterr().out
    assert '"done": false' in out

    answers_path = tmp_path / "answers.json"
    answers_path.write_text('{"callname": "旦那様"}', encoding="utf-8")
    assert cli.main(["setup", "--answers", str(answers_path), "--json"]) == 0
    capsys.readouterr()

    assert cli.main(["setup", "--status", "--json"]) == 0
    out = capsys.readouterr().out
    assert '"done": true' in out

    # render も走っている（is_write=True）ので射影ができている
    assert (home_path / "projections" / "PROFILE.md").is_file()


def test_cli_setup_accepts_an_answers_file_with_a_bom(
    home_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """答えのファイルに BOM が付いていても読める。

    Windows の PowerShell（`Out-File`・`>`）が既定で書く JSON には BOM が付く。
    `utf-8` で読むと「Unexpected UTF-8 BOM」で落ちていた——中身は正しいのに使えない
    （2026-09-05 実測。主人の PC は Windows なので、これは想定される作り方）。
    """
    assert cli.main(["init"]) == 0
    capsys.readouterr()

    answers_path = tmp_path / "answers-bom.json"
    answers_path.write_text('{"callname": "旦那様"}', encoding="utf-8-sig")
    assert answers_path.read_bytes().startswith(b"\xef\xbb\xbf")

    assert cli.main(["setup", "--answers", str(answers_path), "--json"]) == 0
    capsys.readouterr()
    assert cli.main(["setup", "--status", "--json"]) == 0
    assert '"done": true' in capsys.readouterr().out
