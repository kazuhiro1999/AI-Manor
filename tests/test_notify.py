"""notify（声かけ）: v1 `notify.ps1` の移植。声の実体は python -c 相当の無害なコマンドでモックする。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from manor import decision as decision_mod
from manor import hooks
from manor import notify
from manor import task as task_mod
from manor import voice as voice_mod


def _open_decision(conn, title: str = "判断待ち") -> str:
    tid = task_mod.add(conn, f"タスク: {title}")
    return decision_mod.ask(conn, title, task_id=tid, recommend="承認", background="")


def _write_notify_config(home: Path, **kwargs: object) -> None:
    lines = ["[notify]"]
    for key, value in kwargs.items():
        lines.append(f"{key} = {json.dumps(value)}" if isinstance(value, str) else f"{key} = {value}")
    (home / "config.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_voice_config(home: Path, **kwargs: object) -> None:
    """`[voice]` だけを書く（ADR-008 D8 の試験用。`[notify]` は上書きしない）。"""
    cfg = {"engine": "voicevox", "host": "127.0.0.1", "port": 50021, "speaker": 13}
    cfg.update(kwargs)
    lines = ["[voice]"]
    for key, value in cfg.items():
        lines.append(f"{key} = '{value}'" if isinstance(value, str) else f"{key} = {value}")
    (home / "config.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _speak_marker_command(marker: Path) -> str:
    """呼ばれたら `marker` に受け取った文面を書き込むだけの、無害なコマンド。"""
    script = marker.parent / "speak_marker.py"
    script.write_text(
        "import sys, pathlib\n"
        "pathlib.Path(sys.argv[1]).write_text(sys.argv[2], encoding='utf-8')\n",
        encoding="utf-8",
    )
    return f'"{sys.executable}" "{script}" "{marker}" {{text}}'


# --- 文面（v1 phrases.txt と一字一句） --------------------------------------------------


@pytest.mark.parametrize(
    "pending,expected",
    [
        (0, "要対応の件はございません。"),
        (1, "要対応の件が、1件ございます。"),
        (2, "要対応の件が、2件ございます。"),
        (3, "要対応の件が、3件ございます。"),
        (4, "要対応の件が、4件以上ございます。"),
        (9, "要対応の件が、4件以上ございます。"),
    ],
)
def test_phrase_for_matches_v1_wording(pending: int, expected: str):
    assert notify._phrase_for(pending) == expected


# --- 増減の判定 ---------------------------------------------------------------------------


def test_first_run_does_not_speak_but_writes_state(conn, home: Path, monkeypatch):
    monkeypatch.setenv("MANOR_NOW", "2026-09-02T12:00:00")
    _open_decision(conn)
    conn.commit()

    result = notify.run(home)

    assert result["pending"] == 1
    assert result["last"] is None
    assert result["reason"] == "first_run"
    assert result["should"] is False
    assert result["spoke"] is False

    state = json.loads((home / "notify-state.json").read_text(encoding="utf-8"))
    assert state["last_pending"] == 1
    assert state["checked_at"] == "2026-09-02T12:00:00"


def test_increase_rings(conn, home: Path, monkeypatch):
    monkeypatch.setenv("MANOR_NOW", "2026-09-02T12:00:00")
    marker = home / "spoke.txt"
    _write_notify_config(home, speak_command=_speak_marker_command(marker))

    _open_decision(conn, "第一号")
    conn.commit()
    notify.run(home)  # 初回。状態を1にするだけ
    assert not marker.exists()

    _open_decision(conn, "第二号")
    conn.commit()
    result = notify.run(home)

    assert result["pending"] == 2
    assert result["last"] == 1
    assert result["reason"] == "increased"
    assert result["should"] is True
    assert result["spoke"] is True
    assert marker.read_text(encoding="utf-8") == "要対応の件が、2件ございます。"


def test_same_count_does_not_ring(conn, home: Path, monkeypatch):
    monkeypatch.setenv("MANOR_NOW", "2026-09-02T12:00:00")
    marker = home / "spoke.txt"
    _write_notify_config(home, speak_command=_speak_marker_command(marker))

    _open_decision(conn)
    conn.commit()
    notify.run(home)  # 初回
    result = notify.run(home)  # 変化なし

    assert result["pending"] == result["last"] == 1
    assert result["reason"] == "no_change"
    assert result["should"] is False
    assert result["spoke"] is False
    assert not marker.exists()


def test_decreased_does_not_ring(conn, home: Path, monkeypatch):
    monkeypatch.setenv("MANOR_NOW", "2026-09-02T12:00:00")
    marker = home / "spoke.txt"
    _write_notify_config(home, speak_command=_speak_marker_command(marker))

    d1 = _open_decision(conn, "一号")
    _open_decision(conn, "二号")
    conn.commit()
    notify.run(home)  # 初回。last=2

    decision_mod.rule(conn, d1, "approved", ruling="よし")
    conn.commit()
    result = notify.run(home)  # pending=1 < last=2

    assert result["pending"] == 1
    assert result["last"] == 2
    assert result["reason"] == "decreased"
    assert result["should"] is False
    assert not marker.exists()


# --- 静穏時間 ------------------------------------------------------------------------------


def test_quiet_hours_blocks_but_state_still_updates(conn, home: Path, monkeypatch):
    marker = home / "spoke.txt"
    _write_notify_config(home, speak_command=_speak_marker_command(marker))

    monkeypatch.setenv("MANOR_NOW", "2026-09-02T08:00:00")
    _open_decision(conn, "朝")
    conn.commit()
    notify.run(home)  # 初回。last=1

    _open_decision(conn, "深夜分")
    conn.commit()
    monkeypatch.setenv("MANOR_NOW", "2026-09-02T23:30:00")  # 既定の静穏時間 22-07
    result = notify.run(home)

    assert result["pending"] == 2
    assert result["reason"] == "quiet_hours"
    assert result["should"] is False
    assert result["spoke"] is False
    assert not marker.exists()

    state = json.loads((home / "notify-state.json").read_text(encoding="utf-8"))
    assert state["last_pending"] == 2


def test_quiet_hours_configurable_via_config_toml(conn, home: Path, monkeypatch):
    _write_notify_config(home, quiet_from=13, quiet_to=14)
    monkeypatch.setenv("MANOR_NOW", "2026-09-02T13:30:00")
    _open_decision(conn, "一号")
    conn.commit()
    notify.run(home)
    _open_decision(conn, "二号")
    conn.commit()

    result = notify.run(home)
    assert result["reason"] == "quiet_hours"


def test_force_rings_even_in_quiet_hours(conn, home: Path, monkeypatch):
    marker = home / "spoke.txt"
    _write_notify_config(home, speak_command=_speak_marker_command(marker))
    monkeypatch.setenv("MANOR_NOW", "2026-09-02T23:30:00")

    _open_decision(conn)
    conn.commit()
    result = notify.run(home, force=True)

    assert result["should"] is True
    assert result["spoke"] is True
    assert result["reason"] == "forced"
    assert marker.exists()


# --- --dry-run / --quiet ------------------------------------------------------------------


def test_dry_run_does_not_write_state_or_speak(conn, home: Path, monkeypatch):
    marker = home / "spoke.txt"
    _write_notify_config(home, speak_command=_speak_marker_command(marker))
    monkeypatch.setenv("MANOR_NOW", "2026-09-02T12:00:00")

    _open_decision(conn)
    conn.commit()
    result = notify.run(home, dry_run=True)

    assert result["spoke"] is False
    assert not (home / "notify-state.json").exists()
    assert not marker.exists()


def test_force_dry_run_does_not_actually_speak(conn, home: Path, monkeypatch):
    """検証手順の対比: `--force --dry-run` は鳴らさない。`--force` だけが実際に鳴らす。"""
    marker = home / "spoke.txt"
    _write_notify_config(home, speak_command=_speak_marker_command(marker))
    monkeypatch.setenv("MANOR_NOW", "2026-09-02T23:30:00")  # 静穏時間中でも force は無視するはず

    _open_decision(conn)
    conn.commit()
    result = notify.run(home, force=True, dry_run=True)

    assert result["should"] is True
    assert result["spoke"] is False
    assert not marker.exists()
    assert not (home / "notify-state.json").exists()


def test_quiet_flag_suppresses_speaking_but_state_still_updates(conn, home: Path, monkeypatch):
    marker = home / "spoke.txt"
    _write_notify_config(home, speak_command=_speak_marker_command(marker))
    monkeypatch.setenv("MANOR_NOW", "2026-09-02T12:00:00")

    _open_decision(conn, "一号")
    conn.commit()
    notify.run(home)  # 初回

    _open_decision(conn, "二号")
    conn.commit()
    result = notify.run(home, quiet=True)

    assert result["should"] is True
    assert result["spoke"] is False
    assert not marker.exists()
    state = json.loads((home / "notify-state.json").read_text(encoding="utf-8"))
    assert state["last_pending"] == 2


# --- config の speak_command --------------------------------------------------------------


def test_speak_command_receives_text_placeholder(tmp_path: Path):
    marker = tmp_path / "out.txt"
    cmd = _speak_marker_command(marker)

    ok = notify._speak("要対応の件が、4件以上ございます。", cmd)

    assert ok is True
    assert marker.read_text(encoding="utf-8") == "要対応の件が、4件以上ございます。"


def test_speak_returns_false_on_bad_command():
    assert notify._speak("なにか", "この実行ファイルは存在しないはず_xyz --say {text}") is False


# --- 発話の門は voice.speak() に一本化する（ADR-008 D8） --------------------------------------


def test_speak_dispatch_uses_voice_when_configured(conn, home: Path, monkeypatch):
    """`[voice]` が設定されていれば `voice.speak()` を使う。旧経路（speak_command）は
    呼ばれない。"""
    marker = home / "spoke.txt"
    _write_voice_config(home)
    calls: list[tuple[str, object]] = []

    def fake_voice_speak(h, text, *, agent=None):  # noqa: ANN001
        calls.append((text, agent))
        return True

    monkeypatch.setattr(voice_mod, "speak", fake_voice_speak)

    def _boom(*_a, **_kw):  # noqa: ANN002, ANN003
        raise AssertionError("旧経路（speak_command）を呼んではいけない")

    monkeypatch.setattr(notify, "_speak", _boom)

    monkeypatch.setenv("MANOR_NOW", "2026-09-02T12:00:00")
    _open_decision(conn, "一号")
    conn.commit()
    notify.run(home)  # 初回
    _open_decision(conn, "二号")
    conn.commit()

    result = notify.run(home)

    assert result["spoke"] is True
    # ADR-011 D11: notify（声かけ）は執事の声で喋る。
    assert calls == [("要対応の件が、2件ございます。", "butler")]
    assert not marker.exists()


def test_speak_dispatch_respects_mute_when_voice_configured(conn, home: Path, monkeypatch):
    """`[voice]` が設定されていても、消音中なら鳴らさない。HTTP も呼ばない。"""
    _write_voice_config(home)
    voice_mod.mute(home)

    def _boom(*_a, **_kw):  # noqa: ANN002, ANN003
        raise AssertionError("消音中は HTTP を呼んではいけない")

    monkeypatch.setattr(voice_mod.urllib.request, "urlopen", _boom)

    monkeypatch.setenv("MANOR_NOW", "2026-09-02T12:00:00")
    _open_decision(conn, "一号")
    conn.commit()
    notify.run(home)  # 初回
    _open_decision(conn, "二号")
    conn.commit()

    result = notify.run(home)

    assert result["should"] is True  # 判定自体は満たしている
    assert result["spoke"] is False  # だが消音中なので鳴らさない


def test_speak_dispatch_respects_mute_via_old_path_when_voice_unconfigured(conn, home: Path, monkeypatch):
    """`[voice]` が未設定でも、消音の印があれば旧経路（speak_command）も鳴らさない
    （D8: 「消したのに喋った」を作らない。判定は voice の中に1箇所だけ）。"""
    marker = home / "spoke.txt"
    _write_notify_config(home, speak_command=_speak_marker_command(marker))
    voice_mod.mute(home)

    monkeypatch.setenv("MANOR_NOW", "2026-09-02T12:00:00")
    _open_decision(conn, "一号")
    conn.commit()
    notify.run(home)  # 初回
    _open_decision(conn, "二号")
    conn.commit()

    result = notify.run(home)

    assert result["should"] is True
    assert result["spoke"] is False
    assert not marker.exists()


def test_speak_dispatch_falls_back_to_old_path_when_voice_unconfigured(conn, home: Path, monkeypatch):
    """`[voice]` が未設定なら従来どおり `speak_command`（旧経路）を使う。`voice.speak` は
    呼ばれない。"""
    marker = home / "spoke.txt"
    _write_notify_config(home, speak_command=_speak_marker_command(marker))

    def _boom(*_a, **_kw):  # noqa: ANN002, ANN003
        raise AssertionError("[voice] が未設定なら voice.speak を呼んではいけない")

    monkeypatch.setattr(voice_mod, "speak", _boom)

    monkeypatch.setenv("MANOR_NOW", "2026-09-02T12:00:00")
    _open_decision(conn, "一号")
    conn.commit()
    notify.run(home)  # 初回
    _open_decision(conn, "二号")
    conn.commit()

    result = notify.run(home)

    assert result["spoke"] is True
    assert marker.read_text(encoding="utf-8") == "要対応の件が、2件ございます。"


# --- all_phrases()（ADR-008 D11: voice.warm() が使う文面の出どころ） ----------------------------


def test_all_phrases_matches_phrase_table():
    expected = sorted(set(notify._PHRASES.values()) | {notify._PHRASE_MANY})
    assert notify.all_phrases() == expected
    assert "要対応の件はございません。" in notify.all_phrases()
    assert "要対応の件が、4件以上ございます。" in notify.all_phrases()


# --- DB 無し ------------------------------------------------------------------------------


def test_no_db_does_not_crash_and_writes_nothing(tmp_path: Path):
    missing_home = tmp_path / "no_db_here"
    result = notify.run(missing_home)

    assert result["reason"] == "no_db"
    assert result["should"] is False
    assert result["spoke"] is False
    assert result["pending"] == 0
    assert not missing_home.exists() or not (missing_home / "notify-state.json").exists()


def test_no_db_cli_main_does_not_crash(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("MANOR_HOME", str(tmp_path / "still_no_db"))
    code = notify.main(["--quiet"])
    assert code == 0
    assert capsys.readouterr().out == ""


# --- format_message（v1 と同じ文面） --------------------------------------------------------


def test_format_message_no_db_is_silent():
    assert notify.format_message({"reason": "no_db", "pending": 0}) == ""


def test_format_message_quiet_hours():
    result = {"reason": "quiet_hours", "pending": 3, "hour": 23}
    assert (
        notify.format_message(result)
        == "要対応が 3 件に増えましたが、この時間帯は鳴らしません（23時）。"
    )


def test_format_message_spoke():
    result = {"reason": "increased", "pending": 2, "should": True, "spoke": True}
    assert notify.format_message(result) == "声をかけました（2 件）。"


def test_format_message_ready_but_not_spoken():
    result = {
        "reason": "increased",
        "pending": 2,
        "should": True,
        "spoke": False,
        "text": "要対応の件が、2件ございます。",
    }
    assert (
        notify.format_message(result)
        == "鳴らす条件を満たしました（2 件）。文面: 要対応の件が、2件ございます。"
    )


def test_format_message_first_run():
    result = {"reason": "first_run", "pending": 1, "last": None, "should": False}
    assert notify.format_message(result) == "要対応 1 件（前回 — 件）。初回のため声はかけません。"


def test_format_message_no_change():
    result = {"reason": "no_change", "pending": 2, "last": 2, "should": False}
    assert notify.format_message(result) == "要対応 2 件（前回 2 件）。増えていないため声はかけません。"


def test_format_message_decreased():
    result = {"reason": "decreased", "pending": 1, "last": 2, "should": False}
    assert notify.format_message(result) == "要対応 1 件（前回 2 件）。減ったため声はかけません。"


# --- CLI（`python -m manor.notify`） --------------------------------------------------------


def test_main_json_outputs_result(conn, home: Path, monkeypatch, capsys):
    monkeypatch.setenv("MANOR_NOW", "2026-09-02T12:00:00")
    _open_decision(conn)
    conn.commit()

    code = notify.main(["--quiet", "--json"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["pending"] == 1
    assert out["reason"] == "first_run"


def test_main_quiet_reports_ready_on_increase(conn, home: Path, monkeypatch, capsys):
    monkeypatch.setenv("MANOR_NOW", "2026-09-02T12:00:00")
    _open_decision(conn, "一号")
    conn.commit()
    notify.main(["--quiet"])
    capsys.readouterr()

    _open_decision(conn, "二号")
    conn.commit()
    code = notify.main(["--quiet"])

    assert code == 0
    assert "鳴らす条件を満たしました（2 件）" in capsys.readouterr().out


# --- Stop hook との連携 -----------------------------------------------------------------


def test_hooks_stop_calls_notify_when_attended(conn, home: Path, monkeypatch):
    monkeypatch.setenv("MANOR_NOW", "2026-09-02T12:00:00")
    _open_decision(conn)
    conn.commit()

    out = hooks.stop({}, home)

    assert (home / "notify-state.json").is_file()
    # 声かけの結果は systemMessage に足さない（v1 と同じで黙って鳴らすだけ）
    assert out == ""


def test_hooks_stop_does_not_call_notify_when_unattended(conn, home: Path, monkeypatch):
    monkeypatch.setenv("MANOR_NOW", "2026-09-02T12:00:00")
    _open_decision(conn)
    conn.commit()

    out = hooks.stop({"permission_mode": "dontAsk"}, home)

    assert out == ""
    assert not (home / "notify-state.json").exists()


def test_hooks_stop_skips_notify_when_hooks_off(conn, home: Path, monkeypatch):
    monkeypatch.setenv("MANOR_NOW", "2026-09-02T12:00:00")
    monkeypatch.setenv("MANOR_HOOKS", "off")
    _open_decision(conn)
    conn.commit()

    hooks.stop({}, home)

    assert not (home / "notify-state.json").exists()


def test_hooks_stop_survives_notify_raising(conn, home: Path, monkeypatch):
    monkeypatch.setenv("MANOR_NOW", "2026-09-02T12:00:00")
    _open_decision(conn)
    conn.commit()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(notify, "run", _boom)
    out = hooks.stop({}, home)
    assert out == ""
