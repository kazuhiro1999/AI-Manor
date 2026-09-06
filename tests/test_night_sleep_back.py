"""夜勤のあとに PC を眠りへ戻す段（v1 `apps/night-shift/sleep-back.ps1` の移植。T6・2026-09-06）。

**危ないのは「眠らせすぎ」のほう**なので、試験はそちら側に厚くしてある——
主人が自分で起こした PC を勝手に眠らせない、判定できないときは眠らせない。

実際に `SetSuspendState` を呼ぶ経路は試験しない（試験機が眠ってしまう）。
`winps.run` と `subprocess.Popen` を差し替えて、**何を呼ぼうとしたか**だけを見る。
"""

from __future__ import annotations

import pytest

from manor import winps
from manor.night import runner


#: `powercfg /lastwake` の実物に近い出力（日本語環境。文言は OS の言語で変わる）。
_WOKEN_BY_TASK = """スリープ状態の解除履歴カウント - 1
スリープ状態の解除履歴 [0]
  スリープ状態の解除ソース数 - 1
  スリープ状態の解除ソース [0]
    種類: スリープ状態の解除タイマー
    所有者: [PROCESS] \\Device\\HarddiskVolume3\\Windows\\System32\\svchost.exe
    所有者から提供された理由: Windows は 'NT TASK\\manor-night' スケジュール タスクを実行します。
"""

_WOKEN_BY_SOMETHING_ELSE = """スリープ状態の解除履歴カウント - 1
スリープ状態の解除履歴 [0]
    種類: デバイス
    インスタンス パス: USB\\VID_046D
"""

_NOT_WOKEN_AT_ALL = "スリープ状態の解除履歴カウント - 0\n"


@pytest.fixture
def on_windows(monkeypatch: pytest.MonkeyPatch):
    """`sys.platform` を Windows に見せる（判定の中身を、どの OS でも回せるように）。"""
    monkeypatch.setattr(runner.sys, "platform", "win32")


@pytest.fixture
def no_suspend(monkeypatch: pytest.MonkeyPatch):
    """`SetSuspendState` を撃たせない。**呼ばれたかどうか**だけ記録する。"""
    calls: list[list[str]] = []

    def fake_popen(argv, **_kwargs):
        calls.append(list(argv))

        class _P:
            pid = 1234

        return _P()

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    return calls


def _lastwake(monkeypatch: pytest.MonkeyPatch, output: str, *, code: int = 0) -> None:
    monkeypatch.setattr(runner.winps, "run", lambda script, *, timeout: (code, output, ""))


def test_sleeps_when_our_own_task_woke_the_machine(
    monkeypatch: pytest.MonkeyPatch, on_windows, no_suspend
) -> None:
    _lastwake(monkeypatch, _WOKEN_BY_TASK)

    result = runner.sleep_back()

    assert result["slept"] is True
    assert len(no_suspend) == 1


def test_does_not_sleep_when_the_master_woke_the_machine(
    monkeypatch: pytest.MonkeyPatch, on_windows, no_suspend
) -> None:
    """**主人が自分で起こした PC を勝手に眠らせない。** v1 の判断をそのまま引き継ぐ。"""
    _lastwake(monkeypatch, _WOKEN_BY_SOMETHING_ELSE)

    result = runner.sleep_back()

    assert result["slept"] is False
    assert no_suspend == []


def test_does_not_sleep_when_there_was_no_wake_at_all(
    monkeypatch: pytest.MonkeyPatch, on_windows, no_suspend
) -> None:
    _lastwake(monkeypatch, _NOT_WOKEN_AT_ALL)

    assert runner.sleep_back()["slept"] is False
    assert no_suspend == []


def test_does_not_sleep_when_lastwake_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, on_windows, no_suspend
) -> None:
    """読めなかったら「分からない」。**分からないときは眠らせない。**"""
    _lastwake(monkeypatch, "", code=1)

    result = runner.sleep_back()

    assert result["slept"] is False
    assert no_suspend == []
    assert runner.woken_by_task()["woken"] is None


def test_dry_run_never_suspends(
    monkeypatch: pytest.MonkeyPatch, on_windows, no_suspend
) -> None:
    _lastwake(monkeypatch, _WOKEN_BY_TASK)

    result = runner.sleep_back(dry_run=True)

    assert result["slept"] is False
    assert no_suspend == []


def test_non_windows_does_nothing(monkeypatch: pytest.MonkeyPatch, no_suspend) -> None:
    monkeypatch.setattr(runner.sys, "platform", "darwin")

    assert runner.woken_by_task()["woken"] is None
    assert runner.sleep_back()["slept"] is False
    assert no_suspend == []


# --- `run()` の包み方（眠らせない場合が2つある） --------------------------------------------


def test_run_does_not_sleep_when_another_run_holds_the_lock(
    home, monkeypatch: pytest.MonkeyPatch, on_windows, no_suspend
) -> None:
    """`locked` は「別の実行が働いている最中」。**その機械を眠らせてはいけない。**"""
    _lastwake(monkeypatch, _WOKEN_BY_TASK)
    monkeypatch.setattr(runner, "_run_impl", lambda *a, **k: {"status": "locked", "reason": "二重起動"})

    result = runner.run(home, sleep_back_after=True)

    assert result["status"] == "locked"
    assert "sleep_back" not in result
    assert no_suspend == []


def test_run_does_not_sleep_when_the_body_blew_up(
    home, monkeypatch: pytest.MonkeyPatch, on_windows, no_suspend
) -> None:
    """例外で落ちたときも眠らせない——**起きたままのほうが安全側**。"""
    _lastwake(monkeypatch, _WOKEN_BY_TASK)

    def boom(*_a: object, **_k: object) -> dict[str, object]:
        raise RuntimeError("夜勤の途中で落ちました")

    monkeypatch.setattr(runner, "_run_impl", boom)

    with pytest.raises(RuntimeError):
        runner.run(home, sleep_back_after=True)

    assert no_suspend == []


def test_run_sleeps_after_a_normal_night(
    home, monkeypatch: pytest.MonkeyPatch, on_windows, no_suspend
) -> None:
    _lastwake(monkeypatch, _WOKEN_BY_TASK)
    monkeypatch.setattr(runner, "_run_impl", lambda *a, **k: {"status": "done"})

    result = runner.run(home, sleep_back_after=True)

    assert result["sleep_back"]["slept"] is True
    assert len(no_suspend) == 1


def test_run_without_the_flag_never_sleeps(
    home, monkeypatch: pytest.MonkeyPatch, on_windows, no_suspend
) -> None:
    _lastwake(monkeypatch, _WOKEN_BY_TASK)
    monkeypatch.setattr(runner, "_run_impl", lambda *a, **k: {"status": "done"})

    result = runner.run(home)

    assert "sleep_back" not in result
    assert no_suspend == []


# --- 登録するコマンドに意図が出ているか -----------------------------------------------------


def test_install_command_carries_the_flag_only_when_asked() -> None:
    """**設定ファイルに隠さない。** `schtasks /Query` を見れば分かるようにする。"""
    plain = runner.build_install_command(at="02:00")
    with_flag = runner.build_install_command(at="02:00", sleep_back_after=True)

    assert "--sleep-back" not in plain
    assert "-m manor.night run --sleep-back" in with_flag


def test_winps_encodes_the_suspend_script_without_the_active_code_page() -> None:
    """`-EncodedCommand`（UTF-16LE の base64）で渡していることの確認——
    `-Command` に渡すと英語ロケールの Windows で非 ASCII が `?` に落ちる（winps の docstring）。
    """
    encoded = winps.encode_command("[Manor.Power]::SetSuspendState($false, $false, $false)")
    assert "SetSuspendState" not in encoded  # 生の文字列がそのまま argv に出ていない
