"""`manor face`（ADR-008 D4）。Chrome も webbrowser も実物は呼ばない——差し替える。"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from manor import face as face_mod
from manor.errors import ManorError


def _args(**overrides) -> argparse.Namespace:
    base = dict(
        agent="butler", port=face_mod.DEFAULT_PORT, corner="BottomRight",
        width=280, height=340, dry_run=False, json=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


@pytest.fixture(autouse=True)
def _fixed_screen_size(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """`tkinter` の実物（画面サイズの取得）は呼ばない——CI・headless 環境で無くても
    テストが遅くなったり落ちたりしないように、既定で固定値に差し替える。位置合わせ自体を
    確かめる試験だけ `compute_position`/`_screen_size` を明示的に差し替え直す。
    """
    if "position" in request.node.name:
        return
    monkeypatch.setattr(face_mod, "_screen_size", lambda: (1920, 1080))


# --- 語彙検査 -----------------------------------------------------------------------------


def test_unknown_agent_is_manor_error_code_2() -> None:
    with pytest.raises(ManorError) as exc_info:
        face_mod.open_face(agent="nosuchagent", dry_run=True)
    assert exc_info.value.code == 2


def test_known_agents_are_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_mod, "find_chrome", lambda: None)
    for agent in ("butler", "chef", "housekeeper", "steward", "secretary", "qa", "auditor"):
        result = face_mod.open_face(agent=agent, dry_run=True)
        assert result["agent"] == agent
        assert f"agent={agent}" in result["url"]


def test_cmd_face_prints_message_and_returns_exit_code_2_for_unknown_agent(capsys: pytest.CaptureFixture) -> None:
    # needs_db=False の経路（cli.main）は ManorError を捕まえてメッセージを出し、code を返す。
    # ここでは cli 全体は回さず、face._cmd_face が ManorError をそのまま外へ投げることだけ確かめる。
    with pytest.raises(ManorError):
        face_mod._cmd_face(_args(agent="nosuchagent"))


# --- --dry-run: URL を出すだけで何も開かない -------------------------------------------------


def test_dry_run_does_not_call_webbrowser_or_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, object] = {"webbrowser": False, "popen": False}

    def fake_open(url: str) -> bool:
        calls["webbrowser"] = True
        return True

    def fake_popen(cmd, *a, **kw):  # type: ignore[no-untyped-def]
        calls["popen"] = True
        raise AssertionError("subprocess.Popen should not be called on --dry-run")

    monkeypatch.setattr(face_mod.webbrowser, "open", fake_open)
    monkeypatch.setattr(face_mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(face_mod, "find_chrome", lambda: r"C:\fake\chrome.exe")

    result = face_mod.open_face(agent="chef", dry_run=True)

    assert calls == {"webbrowser": False, "popen": False}
    assert result["url"] == "http://127.0.0.1:8789/face?agent=chef"
    assert result["command"] is not None
    assert result["command"][0] == r"C:\fake\chrome.exe"


def test_cmd_face_dry_run_prints_url(capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_mod, "find_chrome", lambda: None)
    rc = face_mod._cmd_face(_args(agent="chef", dry_run=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "http://127.0.0.1:8789/face?agent=chef" in out


# --- Chrome が無いとき: 既定ブラウザへ落ちる。例外は投げない -----------------------------------


def test_chrome_absent_falls_back_to_webbrowser_without_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(face_mod, "find_chrome", lambda: None)
    monkeypatch.setattr(face_mod.webbrowser, "open", lambda url: opened.append(url) or True)

    result = face_mod.open_face(agent="butler", dry_run=False)

    assert opened == [result["url"]]
    assert result["command"] is None
    assert any("Chrome が見つからない" in n for n in result["notes"])


def test_chrome_popen_failure_falls_back_without_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(face_mod, "find_chrome", lambda: r"C:\fake\chrome.exe")

    def fake_popen(cmd, *a, **kw):  # type: ignore[no-untyped-def]
        raise OSError("no such file")

    monkeypatch.setattr(face_mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(face_mod.webbrowser, "open", lambda url: opened.append(url) or True)

    result = face_mod.open_face(agent="butler", dry_run=False)

    assert opened == [result["url"]]
    assert any("起動できませんでした" in n for n in result["notes"])


def test_webbrowser_open_failure_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_mod, "find_chrome", lambda: None)

    def fake_open(url: str) -> bool:
        raise RuntimeError("no display")

    monkeypatch.setattr(face_mod.webbrowser, "open", fake_open)

    result = face_mod.open_face(agent="butler", dry_run=False)  # must not raise
    assert any("開けませんでした" in n for n in result["notes"])


# --- 位置合わせ（best-effort。tkinter が無ければ諦める） ---------------------------------------


def test_compute_position_bottom_right(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_mod, "_screen_size", lambda: (1920, 1080))
    pos = face_mod.compute_position("BottomRight", width=280, height=340, margin=16)
    assert pos == (1920 - 280 - 16, 1080 - 340 - 16)


def test_compute_position_top_left(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_mod, "_screen_size", lambda: (1920, 1080))
    pos = face_mod.compute_position("TopLeft", width=280, height=340, margin=16)
    assert pos == (16, 16)


def test_compute_position_none_when_screen_size_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_mod, "_screen_size", lambda: None)
    assert face_mod.compute_position("BottomRight", width=280, height=340) is None


def test_open_face_notes_when_position_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(face_mod, "find_chrome", lambda: None)
    monkeypatch.setattr(face_mod, "_screen_size", lambda: None)
    result = face_mod.open_face(agent="butler", dry_run=True)
    assert any("位置" in n for n in result["notes"])


# --- URL の組み立て -----------------------------------------------------------------------


def test_build_url_default_port() -> None:
    assert face_mod.build_url("butler") == "http://127.0.0.1:8789/face?agent=butler"


def test_build_url_custom_port() -> None:
    assert face_mod.build_url("chef", port=9000) == "http://127.0.0.1:9000/face?agent=chef"


# --- Chrome の探索 ------------------------------------------------------------------------


def test_find_chrome_uses_program_files_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_chrome = tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe"
    fake_chrome.parent.mkdir(parents=True)
    fake_chrome.write_text("")
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert face_mod.find_chrome() == str(fake_chrome)


def test_find_chrome_none_when_nothing_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(face_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(face_mod.Path, "is_file", lambda self: False)
    assert face_mod.find_chrome() is None
