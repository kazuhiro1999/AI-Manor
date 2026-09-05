"""`face/open`・`face/mouth`・`face/pin`（ADR-011 D5・D6・D7）の試験。**合成データのみ**——
Chrome・VOICEVOX・Windows API の実物は一切呼ばない。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from manor import face as face_mod
from manor import face_pin as face_pin_mod
from manor import voice
from manor.web import app as web_app_mod


def make_client(home: Path, *, read_only: bool = False) -> TestClient:
    return TestClient(web_app_mod.create_app(home, read_only=read_only))


def _passcode_client(home: Path) -> TestClient:
    from manor.web import config as web_config

    web_config.set_passcode(home, "himitsu-desu")
    return TestClient(web_app_mod.create_app(home, host="0.0.0.0"))


# --- POST /api/v1/face/open（D5） -----------------------------------------------------------


def test_open_returns_app_when_chrome_found(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_mod, "find_chrome", lambda: r"C:\fake\chrome.exe")
    monkeypatch.setattr(face_mod, "compute_position", lambda *a, **k: None)
    monkeypatch.setattr(face_mod.subprocess, "Popen", lambda cmd, *a, **k: None)
    client = make_client(home)
    res = client.post("/api/v1/face/open", json={"agent": "butler"})
    assert res.status_code == 200
    assert res.json() == {"opened": True, "method": "app", "reason": ""}


def test_open_returns_false_when_chrome_absent_and_does_not_raise(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(face_mod, "find_chrome", lambda: None)
    client = make_client(home)
    res = client.post("/api/v1/face/open", json={"agent": "butler"})
    assert res.status_code == 200
    body = res.json()
    assert body["opened"] is False
    assert body["method"] == "none"
    assert body["reason"]


def test_open_returns_false_when_chrome_popen_fails(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_mod, "find_chrome", lambda: r"C:\fake\chrome.exe")
    monkeypatch.setattr(face_mod, "compute_position", lambda *a, **k: None)

    def fake_popen(cmd, *a, **k):  # noqa: ANN001, ANN002, ANN003
        raise OSError("no such file")

    monkeypatch.setattr(face_mod.subprocess, "Popen", fake_popen)
    client = make_client(home)
    res = client.post("/api/v1/face/open", json={"agent": "butler"})
    assert res.status_code == 200
    body = res.json()
    assert body["opened"] is False
    assert "起動できませんでした" in body["reason"]


def test_open_does_not_fall_back_to_default_browser(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D5: Chrome が無いとき、サーバはここで既定ブラウザへ落ちない
    （落ちるとヘッダ付きの窓に戻ってしまう。落ちるのは画面側の JS の役目）。"""
    monkeypatch.setattr(face_mod, "find_chrome", lambda: None)
    called = {"webbrowser": False}
    monkeypatch.setattr(face_mod.webbrowser, "open", lambda url: called.update(webbrowser=True))
    client = make_client(home)
    client.post("/api/v1/face/open", json={"agent": "butler"})
    assert called["webbrowser"] is False


def test_open_default_agent_is_butler(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_mod, "find_chrome", lambda: None)
    client = make_client(home)
    res = client.post("/api/v1/face/open", json={})
    assert res.status_code == 200


def test_open_unknown_agent_is_404(home: Path) -> None:
    client = make_client(home)
    res = client.post("/api/v1/face/open", json={"agent": "nosuchagent"})
    assert res.status_code == 404


def test_open_read_only_still_works(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """窓を開くのは書き込みではない（ADR-011 D5）。`--read-only` でも塞がない。"""
    monkeypatch.setattr(face_mod, "find_chrome", lambda: None)
    client = make_client(home, read_only=True)
    res = client.post("/api/v1/face/open", json={"agent": "butler"})
    assert res.status_code == 200


# --- GET /api/v1/face/mouth（D6） -----------------------------------------------------------


def test_mouth_absent_is_id_null(home: Path) -> None:
    client = make_client(home)
    res = client.get("/api/v1/face/mouth")
    assert res.status_code == 200
    assert res.json() == {"id": None}


def test_mouth_reflects_published_schedule(home: Path) -> None:
    cues = [{"at_ms": 0, "viseme": "aa", "weight": 0.3}]
    voice._publish_mouth(home, cues)
    client = make_client(home)
    res = client.get("/api/v1/face/mouth")
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == 1
    assert body["cues"] == cues
    assert isinstance(body["started_at"], float)


def test_mouth_id_increments_across_publishes(home: Path) -> None:
    voice._publish_mouth(home, [])
    voice._publish_mouth(home, [])
    client = make_client(home)
    assert client.get("/api/v1/face/mouth").json()["id"] == 2


# --- GET/POST /api/v1/face/pin（D7） ---------------------------------------------------------


def test_pin_get_unsupported_off_windows(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_pin_mod, "supported", lambda: False)
    client = make_client(home)
    res = client.get("/api/v1/face/pin", params={"agent": "butler"})
    assert res.status_code == 200
    assert res.json() == {"supported": False, "pinned": False}


def test_pin_get_supported_reads_status(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_pin_mod, "supported", lambda: True)
    monkeypatch.setattr(face_pin_mod, "is_pinned", lambda title: True)
    client = make_client(home)
    res = client.get("/api/v1/face/pin", params={"agent": "butler"})
    assert res.status_code == 200
    assert res.json() == {"supported": True, "pinned": True}


def test_pin_get_supported_but_window_not_found_is_pinned_false(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(face_pin_mod, "supported", lambda: True)
    monkeypatch.setattr(face_pin_mod, "is_pinned", lambda title: None)
    client = make_client(home)
    res = client.get("/api/v1/face/pin", params={"agent": "butler"})
    assert res.json() == {"supported": True, "pinned": False}


def test_pin_post_toggles_on(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_pin_mod, "supported", lambda: True)
    monkeypatch.setattr(face_pin_mod, "set_pinned", lambda title, pinned: pinned)
    client = make_client(home)
    res = client.post("/api/v1/face/pin", json={"agent": "butler", "pinned": True})
    assert res.status_code == 200
    assert res.json() == {"supported": True, "pinned": True}


def test_pin_post_window_not_found_returns_reason(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`set_pinned` が `None`（窓が見つからない）のとき、`pinned` は変えず `reason` を添える
    ——ブラウザの実窓の題名は完全一致しないことが多く、ページが「なぜ効かないか」を
    言えるようにする（`face_pin._title_matches` の緩和と対の変更）。
    """
    monkeypatch.setattr(face_pin_mod, "supported", lambda: True)
    monkeypatch.setattr(face_pin_mod, "set_pinned", lambda title, pinned: None)
    client = make_client(home)
    res = client.post("/api/v1/face/pin", json={"agent": "butler", "pinned": True})
    assert res.status_code == 200
    body = res.json()
    assert body["supported"] is True
    assert body["pinned"] is False
    assert body["reason"] == "窓が見つかりません"


def test_pin_post_unsupported_off_windows(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_pin_mod, "supported", lambda: False)
    client = make_client(home)
    res = client.post("/api/v1/face/pin", json={"agent": "butler", "pinned": True})
    assert res.json() == {"supported": False, "pinned": False}


def test_pin_uses_agent_label_as_window_title(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """窓は担当の日本語名（agent_meta）で探す——ダッシュボードや他の担当とは題名が違うので
    取り違えない（ADR-011 D7）。"""
    seen: dict[str, str] = {}
    monkeypatch.setattr(face_pin_mod, "supported", lambda: True)
    monkeypatch.setattr(
        face_pin_mod, "is_pinned", lambda title: seen.setdefault("title", title) or False
    )
    client = make_client(home)
    client.get("/api/v1/face/pin", params={"agent": "chef"})
    assert seen["title"] == "料理長"


def test_pin_get_unknown_agent_is_404(home: Path) -> None:
    client = make_client(home)
    res = client.get("/api/v1/face/pin", params={"agent": "nosuchagent"})
    assert res.status_code == 404


def test_pin_post_unknown_agent_is_404(home: Path) -> None:
    client = make_client(home)
    res = client.post("/api/v1/face/pin", json={"agent": "nosuchagent", "pinned": True})
    assert res.status_code == 404


def test_pin_read_only_still_works(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ピン留めは書き込みではない（home/ に何も残らない）。`--read-only` でも塞がない。"""
    monkeypatch.setattr(face_pin_mod, "supported", lambda: True)
    monkeypatch.setattr(face_pin_mod, "set_pinned", lambda title, pinned: pinned)
    client = make_client(home, read_only=True)
    res = client.post("/api/v1/face/pin", json={"agent": "butler", "pinned": True})
    assert res.status_code == 200


# --- 認証: /api/v1/... の一般規則で既にカバーされている ----------------------------------------


def test_all_three_routes_are_guarded_paths() -> None:
    for path in ("/api/v1/face/open", "/api/v1/face/mouth", "/api/v1/face/pin"):
        assert web_app_mod._is_guarded_path(path) is True
        assert path not in web_app_mod._PUBLIC_API_PATHS


def test_routes_require_auth_on_non_loopback(home: Path) -> None:
    client = _passcode_client(home)
    assert client.post("/api/v1/face/open", json={"agent": "butler"}).status_code == 401
    assert client.get("/api/v1/face/mouth").status_code == 401
    assert client.get("/api/v1/face/pin", params={"agent": "butler"}).status_code == 401
    assert client.post("/api/v1/face/pin", json={"agent": "butler", "pinned": True}).status_code == 401


def test_routes_pass_after_login(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_mod, "find_chrome", lambda: None)
    monkeypatch.setattr(face_pin_mod, "supported", lambda: False)
    client = _passcode_client(home)
    login = client.post("/api/v1/auth/login", json={"passcode": "himitsu-desu"})
    assert login.status_code == 200
    assert client.post("/api/v1/face/open", json={"agent": "butler"}).status_code == 200
    assert client.get("/api/v1/face/mouth").status_code == 200
    assert client.get("/api/v1/face/pin", params={"agent": "butler"}).status_code == 200
