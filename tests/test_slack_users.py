"""Slack の利用者ごとの入口・出口（ADR-014 D5・段C）の試験。**実 Slack へは繋がない**
（`slack_mod._slack_api` を必ず差し替える。`tests/test_slack.py` と同じ道具立て）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from manor import slack as slack_mod
from manor import task as task_mod
from manor import user as user_mod
from manor.web import config as web_config


@pytest.fixture
def leak_terms(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def _set(terms: list[str]) -> Path:
        path = tmp_path / "leak-terms.txt"
        path.write_text("\n".join(terms) + "\n" if terms else "", encoding="utf-8")
        monkeypatch.setenv("MANOR_LEAK_TERMS", str(path))
        return path

    return _set


def _fix_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANOR_TODAY", "2026-09-04")
    monkeypatch.setenv("MANOR_NOW", "2026-09-04T09:00:00")


def _write_top_level_channel(home: Path, *, channel: str = "C123456") -> None:
    Path(home).mkdir(parents=True, exist_ok=True)
    (Path(home) / "config.toml").write_text(f"[slack]\nchannel = '{channel}'\n", encoding="utf-8")


def _set_member_channel(home: Path, user_id: str, channel: str) -> None:
    web_config.update_section(home, "slack", {"users": {user_id: {"channel": channel}}})


def _fake_post(calls: list[dict[str, object]]):
    counter = {"n": 0}

    def fake_api(method, token, *, params=None, timeout=0):  # noqa: ANN001
        assert method == "chat.postMessage"
        counter["n"] += 1
        ts = f"1700000000.{counter['n']:06d}"
        calls.append({"params": params, "ts": ts})
        return {"ok": True, "ts": ts, "channel": params["channel"]}

    return fake_api


# --- 後方互換: master だけ・最上位 channel のとき、brief は今までどおり1本 -------------------


def test_brief_with_only_top_level_channel_is_unchanged(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch, leak_terms
):
    """利用者が master 1人でチャンネルが最上位に1つだけなら、送る内容も返り値の主要キー
    （sent/channel/ts）も今までと同じ（ADR-014 D5「相手の欄が空なら便は1本のまま」）。
    """
    leak_terms([])
    _fix_clock(monkeypatch)
    _write_top_level_channel(home, channel="C-TOP-LEVEL")
    monkeypatch.setattr(slack_mod, "bot_token", lambda: "xoxb-test-token")
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(slack_mod, "_slack_api", _fake_post(calls))

    result = slack_mod.brief(home, generate=False, dry_run=False)

    assert result["sent"] is True
    assert result["channel"] == "C-TOP-LEVEL"
    assert "ts" in result
    assert "results" not in result
    assert len(calls) == 1  # まとめのみ（open decision 無し）
    assert calls[0]["params"]["channel"] == "C-TOP-LEVEL"


def test_channel_id_reads_through_to_top_level_for_principal_only(home: Path, conn) -> None:
    _write_top_level_channel(home, channel="C-TOP-LEVEL")
    principal = user_mod.principal_id(conn)
    member = user_mod.add(conn, "相方")
    conn.commit()

    assert slack_mod.channel_id(home, principal) == "C-TOP-LEVEL"
    assert slack_mod.channel_id(home, member) == ""  # 引き継がない


# --- member にチャンネルがあれば2便・中身がその人の机 ------------------------------------


def test_brief_sends_one_message_per_user_with_a_channel(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch, leak_terms
):
    leak_terms([])
    _fix_clock(monkeypatch)
    principal = user_mod.principal_id(conn)
    member = user_mod.add(conn, "相方")
    conn.commit()
    _write_top_level_channel(home, channel="C-MASTER")  # principal はこの読み替えで拾う
    _set_member_channel(home, member, "C-MEMBER")
    monkeypatch.setattr(slack_mod, "bot_token", lambda: "xoxb-test-token")
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(slack_mod, "_slack_api", _fake_post(calls))

    result = slack_mod.brief(home, generate=False, dry_run=False)

    assert result["ok"] is True
    assert result["sent"] is True
    results = result["results"]
    by_user = {r["user_id"]: r for r in results}
    assert set(by_user) == {principal, member}
    assert by_user[principal]["channel"] == "C-MASTER"
    assert by_user[member]["channel"] == "C-MEMBER"
    sent_channels = {c["params"]["channel"] for c in calls}
    assert sent_channels == {"C-MASTER", "C-MEMBER"}


def test_member_board_excludes_butler_tasks_and_night_section(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """執事の利用者に Slack チャンネルは持たせず、夜勤の報告は主人の便にだけ載る
    （ADR-014 D5）。ここでは `brief_data` を直接読んで確かめる。
    """
    _fix_clock(monkeypatch)
    principal = user_mod.principal_id(conn)
    member = user_mod.add(conn, "相方")
    conn.commit()

    task_mod.add(conn, "執事の件", section="B", user=user_mod.BUTLER_ID)
    task_mod.add(conn, "相方の件", section="B", user=member)
    conn.commit()

    principal_data = slack_mod.brief_data(conn, home, user_id=principal)
    member_data = slack_mod.brief_data(conn, home, user_id=member)

    principal_titles = {t["title"] for t in principal_data["section_b"]}
    member_titles = {t["title"] for t in member_data["section_b"]}
    assert "執事の件" not in principal_titles
    assert "執事の件" not in member_titles
    assert "相方の件" in member_titles
    assert "相方の件" not in principal_titles

    # 夜勤の3つは principal のときだけ入る。
    assert principal_data["night"] is not None
    assert principal_data["night_health"] is not None
    assert principal_data["night_pending"] is not None
    assert member_data["night"] is None
    assert member_data["night_health"] is None
    assert member_data["night_pending"] is None

    # None でも本文の組み立ては壊れない。
    text = slack_mod.format_mechanical_brief(member_data)
    assert isinstance(text, str) and text


# --- intake: member のチャンネルの #task が member の件になる ---------------------------


def test_intake_task_goes_to_the_channel_owners_user(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch, leak_terms
) -> None:
    leak_terms([])
    _fix_clock(monkeypatch)
    member = user_mod.add(conn, "相方")
    conn.commit()
    _set_member_channel(home, member, "C-MEMBER")
    monkeypatch.setattr(slack_mod, "bot_token", lambda: "xoxb-test-token")

    def fake_api(method, token, *, params=None, timeout=0):  # noqa: ANN001
        params = params or {}
        if method == "conversations.history":
            if params.get("channel") == "C-MEMBER":
                return {"ok": True, "messages": [{"ts": "4000.0001", "text": "#task 相方のタスク"}]}
            return {"ok": True, "messages": []}
        if method == "chat.postMessage":
            return {"ok": True, "ts": "4000.0002"}
        raise AssertionError(f"想定外の method です: {method}")

    monkeypatch.setattr(slack_mod, "_slack_api", fake_api)
    # `#task` の自由文分解は既定で呼ばせない（本文そのまま起票の経路を通す）。
    monkeypatch.setattr(slack_mod, "extract_task", lambda body, **kw: {"ok": False, "reason": "（試験）"})

    result = slack_mod.intake(home)

    assert result["ok"] is True
    tasks = task_mod.list_tasks(conn, user_id=member)
    assert any(t["title"].startswith("相方のタスク") for t in tasks)
    principal_tasks = task_mod.list_tasks(conn, user_id=user_mod.principal_id(conn))
    assert not any(t["title"].startswith("相方のタスク") for t in principal_tasks)
