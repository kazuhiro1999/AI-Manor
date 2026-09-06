"""`manor slack morning`（朝の定例）の表示の試験。

**この経路の出力は `%LOCALAPPDATA%\\manor\\jobs\\manor-morning.log` にしか残らない。**
だから「取り込めなかった返信」「エラー」を出さないと、**どこにも残らない**
（検分 S5・2026-09-06。それまでは `_cmd_inbox` の表示を削った写しになっていた）。
"""

from __future__ import annotations

import argparse

import pytest

from manor import slack as slack_mod


def _args(**kw: object) -> argparse.Namespace:
    base = {"json": False, "dry_run": False, "no_generate": True}
    base.update(kw)
    return argparse.Namespace(**base)


def _morning_result(inbox: dict[str, object]) -> dict[str, object]:
    return {
        "voice_restored": False,
        "inbox": inbox,
        "brief": {"sent": True, "channel": "C1", "ts": "1.1"},
        "ok": True,
    }


def test_morning_reports_replies_it_could_not_map(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """**いちばん残すべき行。** 取り込めなかった返信は主人の判断が要る。"""
    monkeypatch.setattr(
        slack_mod, "morning",
        lambda home, **kw: _morning_result({
            "ok": True, "ruled": [], "errors": [],
            "unmapped": [{"channel": "C1", "ts": "2.2", "reason": "どの判断への返信か決められません"}],
        }),
    )

    slack_mod._cmd_morning(_args())

    out = capsys.readouterr().out
    assert "2.2" in out
    assert "決められません" in out


def test_morning_reports_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        slack_mod, "morning",
        lambda home, **kw: _morning_result({
            "ok": True, "ruled": [], "unmapped": [],
            "errors": [{"channel": "C1", "ts": "3.3", "reason": "channel_not_found"}],
        }),
    )

    slack_mod._cmd_morning(_args())

    assert "channel_not_found" in capsys.readouterr().out


def test_morning_says_when_there_were_no_new_replies(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """**黙って終わらせない。** 何も出ないと「動いていない」と見分けがつかない。"""
    monkeypatch.setattr(
        slack_mod, "morning",
        lambda home, **kw: _morning_result({"ok": True, "ruled": [], "unmapped": [], "errors": []}),
    )

    slack_mod._cmd_morning(_args())

    assert capsys.readouterr().out.strip() != ""


def test_morning_reports_a_failed_intake_of_replies(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """取り込みが失敗しても送信は続ける——が、**失敗したことは必ず出す**。"""
    monkeypatch.setattr(
        slack_mod, "morning",
        lambda home, **kw: _morning_result({"ok": False, "reason": "bot_token が未設定です"}),
    )

    slack_mod._cmd_morning(_args())

    assert "bot_token" in capsys.readouterr().out


def test_inbox_and_morning_print_the_same_lines(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """表示は1本に寄せてある（`_print_inbox_result`）。**片方だけ痩せる形を作らない。**"""
    inbox_result = {
        "ok": True, "ruled": [], "errors": [],
        "unmapped": [{"channel": "C1", "ts": "4.4", "reason": "id がありません"}],
    }
    monkeypatch.setattr(slack_mod, "inbox", lambda home, **kw: inbox_result)
    slack_mod._cmd_inbox(_args())
    from_inbox = capsys.readouterr().out

    monkeypatch.setattr(slack_mod, "morning", lambda home, **kw: _morning_result(inbox_result))
    slack_mod._cmd_morning(_args())
    from_morning = capsys.readouterr().out

    for line in from_inbox.splitlines():
        assert line in from_morning
