"""`face/talk`（小窓の通話。ADR-008 §8 D17〜D20）の試験。**合成データのみ・`claude` は起動しない**。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from manor import talk_session
from manor.web import app as web_app_mod

NOON = "2026-09-04T12:00:00"


def make_client(home: Path, *, read_only: bool = False) -> TestClient:
    return TestClient(web_app_mod.create_app(home, read_only=read_only))


def _passcode_client(home: Path) -> TestClient:
    """ループバック以外から来た体で作る（`tests/web/test_face.py` と同じ手口）。"""
    from manor.web import config as web_config

    web_config.set_passcode(home, "himitsu-desu")
    return TestClient(web_app_mod.create_app(home, host="0.0.0.0"))


def _mock_success(monkeypatch: pytest.MonkeyPatch, *, reply: str = "承知いたしました。") -> None:
    fake = json.dumps(
        {
            "is_error": False,
            "result": reply,
            "total_cost_usd": 0.01,
            "num_turns": 1,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
    )
    proc = Mock(returncode=0, stdout=fake, stderr="")
    monkeypatch.setattr(talk_session.subprocess, "run", Mock(return_value=proc))
    monkeypatch.setattr(talk_session.voice, "speak", Mock(return_value=True))


# --- 形（フロント担当が ADR D18 の型として当てにできるもの） -----------------------------------


def test_get_talk_state_shape(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANOR_NOW", NOON)
    client = make_client(home)
    res = client.get("/api/v1/face/talk")
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"available", "limit", "used", "remaining", "reason", "lock", "message"}
    assert body["available"] is True
    assert body["used"] == 0
    assert body["message"] == ""


def test_post_talk_open_shape(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANOR_NOW", NOON)
    client = make_client(home)
    res = client.post("/api/v1/face/talk/open")
    assert res.status_code == 200
    body = res.json()
    for key in ("available", "limit", "used", "remaining", "message", "text", "lines", "spoke", "warming"):
        assert key in body
    assert body["text"]
    assert isinstance(body["lines"], list) and len(body["lines"]) == 2


def test_post_talk_open_passes_the_agent_through(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """挨拶もその窓の担当の声（ADR-011 D11 追補）。body の `agent` が `open_session` へ届く。"""
    monkeypatch.setenv("MANOR_NOW", NOON)
    seen: dict[str, object] = {}

    def fake_open(home_arg: Path, *, agent: str | None = None, play_here: bool = False) -> dict[str, object]:
        seen["agent"] = agent
        return {"available": True, "text": "", "lines": [], "spoke": False, "warming": False}

    monkeypatch.setattr(talk_session, "open_session", fake_open)
    client = make_client(home)

    assert client.post("/api/v1/face/talk/open", json={"agent": "housekeeper"}).status_code == 200
    assert seen["agent"] == "housekeeper"


def test_post_talk_open_without_a_body_defaults_to_the_butler(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`agent` を送らない古い呼び出しでも壊れない（既定は執事）。"""
    monkeypatch.setenv("MANOR_NOW", NOON)
    seen: dict[str, object] = {}

    def fake_open(home_arg: Path, *, agent: str | None = None, play_here: bool = False) -> dict[str, object]:
        seen["agent"] = agent
        return {"available": True, "text": "", "lines": [], "spoke": False, "warming": False}

    monkeypatch.setattr(talk_session, "open_session", fake_open)
    client = make_client(home)

    assert client.post("/api/v1/face/talk/open").status_code == 200
    assert seen["agent"] == "butler"


def test_post_talk_ask_shape_on_success(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANOR_NOW", NOON)
    _mock_success(monkeypatch, reply="かしこまりました。")
    client = make_client(home)
    res = client.post("/api/v1/face/talk", json={"text": "こんにちは", "history": []})
    assert res.status_code == 200
    body = res.json()
    # audio_id（2026-09-07）: ブラウザが自分で声を鳴らすための鍵。
    assert set(body) == {"ok", "reply", "seconds", "remaining", "used", "limit", "audio_id"}
    assert body["ok"] is True
    assert body["reply"] == "かしこまりました。"
    assert body["used"] == 1


def test_post_talk_ask_accepts_history(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANOR_NOW", NOON)
    _mock_success(monkeypatch)
    client = make_client(home)
    res = client.post(
        "/api/v1/face/talk",
        json={
            "text": "続きだけど",
            "history": [
                {"role": "user", "text": "さっきの件だけど"},
                {"role": "butler", "text": "承知いたしました。"},
            ],
        },
    )
    assert res.status_code == 200
    assert res.json()["ok"] is True


def test_post_talk_ask_passes_agent_to_talk_session(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-011 D11: 小窓の担当をそのまま `talk_session.ask` へ渡す。"""
    monkeypatch.setenv("MANOR_NOW", NOON)
    _mock_success(monkeypatch)
    seen: dict[str, object] = {}
    real_ask = talk_session.ask

    def spy(home, text, history=None, *, agent=None, **kw):  # noqa: ANN001
        seen["agent"] = agent
        return real_ask(home, text, history, agent=agent, **kw)

    monkeypatch.setattr(talk_session, "ask", spy)
    client = make_client(home)
    res = client.post("/api/v1/face/talk", json={"text": "在庫を確認して", "agent": "housekeeper"})
    assert res.status_code == 200
    assert seen["agent"] == "housekeeper"


def test_post_talk_ask_defaults_agent_to_butler_when_omitted(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MANOR_NOW", NOON)
    _mock_success(monkeypatch)
    seen: dict[str, object] = {}
    real_ask = talk_session.ask

    def spy(home, text, history=None, *, agent=None, **kw):  # noqa: ANN001
        seen["agent"] = agent
        return real_ask(home, text, history, agent=agent, **kw)

    monkeypatch.setattr(talk_session, "ask", spy)
    client = make_client(home)
    res = client.post("/api/v1/face/talk", json={"text": "こんにちは"})  # agent 省略
    assert res.status_code == 200
    assert seen["agent"] == "butler"


def test_post_talk_ask_failure_shape(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANOR_NOW", NOON)
    monkeypatch.setattr(talk_session.subprocess, "run", Mock(side_effect=OSError("boom")))
    speak_mock = Mock(return_value=True)
    monkeypatch.setattr(talk_session.voice, "speak", speak_mock)
    client = make_client(home)
    res = client.post("/api/v1/face/talk", json={"text": "こんにちは"})
    assert res.status_code == 200  # 業務上の失敗は 200 + ok:false（HTTP エラーにはしない）
    body = res.json()
    assert body["ok"] is False
    assert body["reply"]
    speak_mock.assert_not_called()


def test_post_talk_close_shape(home: Path) -> None:
    talk_session._engine_started_by_us = False
    client = make_client(home)
    res = client.post("/api/v1/face/talk/close")
    assert res.status_code == 200
    assert res.json() == {"stopped": False}


# --- GET は読み取り専用、他3つは require_writable ------------------------------------------


def test_read_only_blocks_the_three_write_routes_but_not_get(home: Path) -> None:
    client = make_client(home, read_only=True)
    assert client.get("/api/v1/face/talk").status_code == 200
    assert client.post("/api/v1/face/talk/open").status_code == 403
    assert client.post("/api/v1/face/talk", json={"text": "こんにちは"}).status_code == 403
    assert client.post("/api/v1/face/talk/close").status_code == 403


# --- 認証: /api/v1/... の一般規則で既にカバーされている ----------------------------------------


def test_talk_routes_require_auth_on_non_loopback(home: Path) -> None:
    client = _passcode_client(home)
    assert client.get("/api/v1/face/talk").status_code == 401
    assert client.post("/api/v1/face/talk/open").status_code == 401
    assert client.post("/api/v1/face/talk", json={"text": "こんにちは"}).status_code == 401
    assert client.post("/api/v1/face/talk/close").status_code == 401


def test_talk_routes_pass_after_login(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANOR_NOW", NOON)
    client = _passcode_client(home)
    login = client.post("/api/v1/auth/login", json={"passcode": "himitsu-desu"})
    assert login.status_code == 200
    assert client.get("/api/v1/face/talk").status_code == 200


def test_is_guarded_path_covers_all_four_routes() -> None:
    """`_is_guarded_path` の `/api/v1/...` 一般規則がここも守っていることを直接確かめる
    （`face_talk.py` の register 内コメント参照）。
    """
    for path in (
        "/api/v1/face/talk",
        "/api/v1/face/talk/open",
        "/api/v1/face/talk/close",
    ):
        assert web_app_mod._is_guarded_path(path) is True
        assert path not in web_app_mod._PUBLIC_API_PATHS


# --- スマホでも声が聞こえる（主人のご指摘 2026-09-07） -----------------------------------


def test_ask_with_play_here_does_not_play_on_the_server(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`play_here=True` なら**サーバ機のスピーカーからは鳴らさない**。

    `voice._play` は PowerShell / afplay を叩く——主人がスマホから話していると、
    声は留守の PC から出て手元では無音だった（小窓をサーバ機に開いてしまうのと同じ形）。
    """
    calls: list[bool] = []

    def fake_speak_detail(_home, _text, *, agent=None, play=True):
        calls.append(play)
        return {"ok": True, "reason": "", "cached": True, "wav": "x.wav", "audio_id": "a" * 64}

    # T50: talk_session.ask() は既定で実時刻（_now()）を見る。夜間（静穏時間）に
    # 回すと `available=False` で早期return し、speak_detail が一度も呼ばれない
    # （calls == [] のまま）。T7 と同じ族——時刻に依る試験は時計を固定する。
    monkeypatch.setenv("MANOR_NOW", NOON)
    _mock_success(monkeypatch)
    monkeypatch.setattr(talk_session.voice, "speak_detail", fake_speak_detail)

    talk_session.ask(home, "在庫は足りていますか", [], agent="housekeeper", play_here=True)
    assert calls == [False]  # play=False で呼ばれる＝サーバでは鳴らさない

    calls.clear()
    talk_session.ask(home, "在庫は足りていますか", [], agent="housekeeper")
    assert calls == [True]  # 既定は従来どおり


def test_voice_endpoint_rejects_anything_but_a_64_hex_key(home: Path) -> None:
    """鍵は sha256 の16進64桁だけ。`..` や区切りでキャッシュの外へ出さない。"""
    from manor import voice as voice_mod

    for bad in ["../../etc/passwd", "..", "abc", "", "g" * 64, "a" * 63, "A" * 64 + "/x"]:
        assert voice_mod.cached_wav_by_id(home, bad) is None


def test_open_with_play_here_does_not_play_on_the_server(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**挨拶にも `play_here` が効くこと。**

    2026-09-07 に `ask` へは通したが `open_session` へは通し忘れていた——スマホでは
    返事の声は聞こえるのに**最初の挨拶だけ無音**、という半端な状態になっていた
    （主人のご指摘「通話ボタンを押しても声が聞こえず、チャットを送るまで聞こえない」）。
    """
    monkeypatch.setenv("MANOR_NOW", NOON)
    calls: list[bool] = []

    def fake_speak_detail(_home, _text, *, agent=None, play=True):
        calls.append(play)
        return {"ok": True, "reason": "", "cached": True, "wav": "x.wav", "audio_id": "b" * 64}

    monkeypatch.setattr(talk_session.voice, "speak_detail", fake_speak_detail)
    monkeypatch.setattr(talk_session, "_warm_engine_async", lambda *a, **k: False)

    result = talk_session.open_session(home, agent="housekeeper", play_here=True)
    assert calls == [False]
    assert result["audio_id"] == "b" * 64

    calls.clear()
    talk_session.open_session(home, agent="housekeeper")
    assert calls == [True]  # 既定は従来どおりサーバ側で鳴らす
