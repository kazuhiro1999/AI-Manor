"""夜勤のあとに PC を眠りへ戻す段（v1 `apps/night-shift/sleep-back.ps1` の移植。T6・2026-09-06）。

**危ないのは「眠らせすぎ」のほう**なので、試験はそちら側に厚くしてある——
主人が自分で起こした PC を眠らせない、**人がいる間は眠らせない**、判定できないときは眠らせない。

門は2つある（検分 S1・2026-09-06）:

1. 自分のウェイクタイマーで起きたか（`powercfg /lastwake`）
2. いま人がいないか（`GetLastInputInfo`）——1 だけでは足りない。02:00 にタイマーで起き、
   主人が 06:00 に使い始め、夜勤が 06:30 に終わる場面で `/lastwake` の答えは変わらない

実際に `SetSuspendState` を呼ぶ経路は試験しない（試験機が眠ってしまう）。
`winps.run` と `subprocess.Popen` を差し替えて、**何を呼ぼうとしたか**だけを見る。
"""

from __future__ import annotations

import base64

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

#: 長く放置されている（＝人がいない）。単位はミリ秒。
_IDLE_LONG = str(45 * 60 * 1000)
#: たった今まで触られていた（＝人がいる）。
_IDLE_SHORT = str(30 * 1000)


@pytest.fixture
def on_windows(monkeypatch: pytest.MonkeyPatch):
    """`sys.platform` を Windows に見せる（判定の中身を、どの OS でも回せるように）。"""
    monkeypatch.setattr(runner.sys, "platform", "win32")


@pytest.fixture
def no_suspend(monkeypatch: pytest.MonkeyPatch):
    """`SetSuspendState` を撃たせない。**呼ばれたかどうか**だけ記録する。

    既定では「投げた直後もまだ生きている」＝要求できた形を返す（`poll()` が `None`）。
    `set_exit(code, err)` で「窓の中で終わっていた」形に切り替えられる——
    **終了コードが 0 でも失敗**である点が S10 の主題（`SetSuspendState` は拒否されると
    `False` を返すだけで、PowerShell の終了コードは 0 のまま）。
    """
    state: dict[str, object] = {"code": None, "err": b"", "out": b""}

    class _Calls(list):
        """呼ばれた argv の記録。`set_exit` で「窓の中で終わっていた」形へ切り替える
        （素の `list` には属性を足せないので、薄く包む）。"""

        def set_exit(self, code: int, err: bytes = b"", out: bytes = b"") -> None:
            state["code"] = code
            state["err"] = err
            state["out"] = out

    calls = _Calls()

    class _FakeStream:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def read(self) -> bytes:
            return self._data

    class _FakeProc:
        pid = 1234

        def __init__(self) -> None:
            # **`stdout` も持たせる。** 持たせないと、本物が stdout を読む行で
            # AttributeError になり、それが握り潰されて**たまたま通ってしまう**
            # （偽物が本物より痩せていると、試験は何も守らない）。
            self.stderr = _FakeStream(state["err"])  # type: ignore[arg-type]
            self.stdout = _FakeStream(state["out"])  # type: ignore[arg-type]

        def poll(self):
            return state["code"]

    def fake_popen(argv, **_kwargs):
        calls.append(list(argv))
        return _FakeProc()

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    # 生死の確認で本当に待たない（試験を遅くしない）
    monkeypatch.setattr(runner.time, "sleep", lambda _s: None)
    return calls


@pytest.fixture
def winps_answers(monkeypatch: pytest.MonkeyPatch):
    """`winps.run` を、**渡されたスクリプトの中身で振り分ける**偽物に差し替える。

    `sleep_back` は `powercfg /lastwake` と `GetLastInputInfo` の2つを呼ぶので、
    1つの戻り値を返す差し替えでは、片方が必ず「読めません」になってしまう。
    """

    def _set(*, lastwake: str = _NOT_WOKEN_AT_ALL, lastwake_code: int = 0,
             idle: str = _IDLE_LONG, idle_code: int = 0) -> None:
        def fake(script: str, *, timeout: int):
            if "GetLastInputInfo" in script:
                return (idle_code, idle, "")
            return (lastwake_code, lastwake, "")

        monkeypatch.setattr(runner.winps, "run", fake)

    return _set


# --- 門1: どう起きたか -----------------------------------------------------------------------


def test_sleeps_when_woken_by_our_task_and_nobody_is_there(
    winps_answers, on_windows, no_suspend
) -> None:
    winps_answers(lastwake=_WOKEN_BY_TASK, idle=_IDLE_LONG)

    result = runner.sleep_back()

    assert result["requested"] is True
    assert len(no_suspend) == 1


def test_does_not_sleep_when_the_master_woke_the_machine(
    winps_answers, on_windows, no_suspend
) -> None:
    """**主人が自分で起こした PC を勝手に眠らせない。** v1 の判断をそのまま引き継ぐ。"""
    winps_answers(lastwake=_WOKEN_BY_SOMETHING_ELSE, idle=_IDLE_LONG)

    result = runner.sleep_back()

    assert result["requested"] is False
    assert no_suspend == []


def test_does_not_sleep_when_there_was_no_wake_at_all(
    winps_answers, on_windows, no_suspend
) -> None:
    winps_answers(lastwake=_NOT_WOKEN_AT_ALL, idle=_IDLE_LONG)

    assert runner.sleep_back()["requested"] is False
    assert no_suspend == []


def test_does_not_sleep_when_lastwake_cannot_be_read(
    winps_answers, on_windows, no_suspend
) -> None:
    """読めなかったら「分からない」。**分からないときは眠らせない。**"""
    winps_answers(lastwake="", lastwake_code=1, idle=_IDLE_LONG)

    result = runner.sleep_back()

    assert result["requested"] is False
    assert no_suspend == []
    assert runner.woken_by_task()["woken"] is None


# --- 門2: いま人がいるか（検分 S1） -----------------------------------------------------------


def test_does_not_sleep_while_someone_is_using_the_machine(
    winps_answers, on_windows, no_suspend
) -> None:
    """**これが S1 の本体。** タイマーで起きた朝に主人が使い始めていても、
    `/lastwake` の答えは「タスクが起こした」のまま——そこで眠らせると画面が落ちる。
    """
    winps_answers(lastwake=_WOKEN_BY_TASK, idle=_IDLE_SHORT)

    result = runner.sleep_back()

    assert result["requested"] is False
    assert "操作があります" in result["reason"]
    assert no_suspend == []


def test_does_not_sleep_when_idle_time_cannot_be_read(
    winps_answers, on_windows, no_suspend
) -> None:
    winps_answers(lastwake=_WOKEN_BY_TASK, idle="", idle_code=1)

    result = runner.sleep_back()

    assert result["requested"] is False
    assert no_suspend == []


def test_does_not_sleep_when_getlastinputinfo_reports_failure(
    winps_answers, on_windows, no_suspend
) -> None:
    """API 自体が失敗を返したとき（`-1`）も「分からない」側へ倒す。"""
    winps_answers(lastwake=_WOKEN_BY_TASK, idle="-1")

    assert runner.sleep_back()["requested"] is False
    assert no_suspend == []
    assert runner.idle_seconds()["seconds"] is None


def test_the_two_gates_are_reported_separately(winps_answers, on_windows, no_suspend) -> None:
    """どちらの門で止まったか、**後から分かる**こと。"""
    winps_answers(lastwake=_WOKEN_BY_TASK, idle=_IDLE_SHORT)

    result = runner.sleep_back()

    assert result["checks"]["woken"]["woken"] is True
    assert result["checks"]["idle"]["seconds"] == 30.0


def test_idle_threshold_is_adjustable(winps_answers, on_windows, no_suspend) -> None:
    winps_answers(lastwake=_WOKEN_BY_TASK, idle=_IDLE_SHORT)

    # 30 秒の無操作でも、しきい値が 10 秒なら「人はいない」
    assert runner.sleep_back(idle_minutes=10 / 60)["requested"] is True


# --- 要求の生死を確かめる（検分 S2） ----------------------------------------------------------


def test_reports_failure_when_the_request_dies_immediately(
    winps_answers, on_windows, no_suspend
) -> None:
    """**投げっぱなしにしない。** すぐ非0で死んでいたら失敗として理由を残す。"""
    winps_answers(lastwake=_WOKEN_BY_TASK, idle=_IDLE_LONG)
    no_suspend.set_exit(1, "SetSuspendState が見つかりません".encode("utf-8"))

    result = runner.sleep_back()

    assert result["requested"] is False
    assert "スリープ要求が失敗しました" in result["reason"]
    assert "SetSuspendState" in result["reason"]


def test_exit_zero_within_the_window_is_still_a_failure(
    winps_answers, on_windows, no_suspend
) -> None:
    """**これが S10 の本体。**

    `SetSuspendState` は真偽値を返す関数で、拒否されると `False` を返して普通に終わる
    ——PowerShell はそれを出力するだけなので**終了コードは 0 のまま**（実測 2026-09-06:
    `powershell -Command '$false'` の終了コードは 0）。`code != 0` だけを見ていると、
    **拒否が「要求できた」として記録される。**

    眠れたのなら、この窓の中で戻ってくるはずがない——`code is not None` そのものが
    「眠れなかった」証拠になる。
    """
    winps_answers(lastwake=_WOKEN_BY_TASK, idle=_IDLE_LONG)
    no_suspend.set_exit(0)

    result = runner.sleep_back()

    assert result["requested"] is False
    assert "眠れていません" in result["reason"]


def test_refusal_is_named_as_such(winps_answers, on_windows, no_suspend) -> None:
    """スクリプト側でも真偽を見て、拒否なら専用の終了コードで落とす。"""
    winps_answers(lastwake=_WOKEN_BY_TASK, idle=_IDLE_LONG)
    no_suspend.set_exit(runner.SLEEP_BACK_REFUSED_CODE)

    result = runner.sleep_back()

    assert result["requested"] is False
    assert "拒否されました" in result["reason"]


def test_the_script_checks_the_return_value(winps_answers, on_windows, no_suspend) -> None:
    """スクリプトの中身そのものの確認——真偽を見ずに投げていないこと。"""
    winps_answers(lastwake=_WOKEN_BY_TASK, idle=_IDLE_LONG)

    runner.sleep_back()

    encoded = no_suspend[0][-1]
    script = base64.b64decode(encoded).decode("utf-16-le")
    assert "if (-not [Manor.Power]::SetSuspendState" in script
    assert f"exit {runner.SLEEP_BACK_REFUSED_CODE}" in script


def test_still_alive_after_the_window_means_the_request_went_through(
    winps_answers, on_windows, no_suspend
) -> None:
    """3通りの3つめ: 生き続けている＝眠りに入った（この場で確かめられる唯一の形）。"""
    winps_answers(lastwake=_WOKEN_BY_TASK, idle=_IDLE_LONG)

    assert runner.sleep_back()["requested"] is True


def test_does_not_claim_to_have_slept(winps_answers, on_windows, no_suspend) -> None:
    """**確かめていないことを、確かめたように言わない。** 返すのは「要求した」まで。"""
    winps_answers(lastwake=_WOKEN_BY_TASK, idle=_IDLE_LONG)

    result = runner.sleep_back()

    assert "slept" not in result
    assert "確かめられません" in result["reason"]


def test_dry_run_never_suspends(winps_answers, on_windows, no_suspend) -> None:
    winps_answers(lastwake=_WOKEN_BY_TASK, idle=_IDLE_LONG)

    result = runner.sleep_back(dry_run=True)

    assert result["requested"] is False
    assert no_suspend == []


def test_non_windows_does_nothing(monkeypatch: pytest.MonkeyPatch, no_suspend) -> None:
    monkeypatch.setattr(runner.sys, "platform", "darwin")

    assert runner.woken_by_task()["woken"] is None
    assert runner.idle_seconds()["seconds"] is None
    assert runner.sleep_back()["requested"] is False
    assert no_suspend == []


# --- `run()` の包み方（眠らせない場合が2つある） --------------------------------------------


def test_run_does_not_sleep_when_another_run_holds_the_lock(
    home, monkeypatch: pytest.MonkeyPatch, winps_answers, on_windows, no_suspend
) -> None:
    """`locked` は「別の実行が働いている最中」。**その機械を眠らせてはいけない。**"""
    winps_answers(lastwake=_WOKEN_BY_TASK, idle=_IDLE_LONG)
    monkeypatch.setattr(runner, "_run_impl", lambda *a, **k: {"status": "locked", "reason": "二重起動"})

    result = runner.run(home, sleep_back_after=True)

    assert result["status"] == "locked"
    assert "sleep_back" not in result
    assert no_suspend == []


def test_run_does_not_sleep_when_the_body_blew_up(
    home, monkeypatch: pytest.MonkeyPatch, winps_answers, on_windows, no_suspend
) -> None:
    """例外で落ちたときも眠らせない——**起きたままのほうが安全側**。"""
    winps_answers(lastwake=_WOKEN_BY_TASK, idle=_IDLE_LONG)

    def boom(*_a: object, **_k: object) -> dict[str, object]:
        raise RuntimeError("夜勤の途中で落ちました")

    monkeypatch.setattr(runner, "_run_impl", boom)

    with pytest.raises(RuntimeError):
        runner.run(home, sleep_back_after=True)

    assert no_suspend == []


def test_run_sleeps_after_a_normal_night(
    home, monkeypatch: pytest.MonkeyPatch, winps_answers, on_windows, no_suspend
) -> None:
    winps_answers(lastwake=_WOKEN_BY_TASK, idle=_IDLE_LONG)
    monkeypatch.setattr(runner, "_run_impl", lambda *a, **k: {"status": "done"})

    result = runner.run(home, sleep_back_after=True)

    assert result["sleep_back"]["requested"] is True
    assert len(no_suspend) == 1


def test_run_without_the_flag_never_sleeps(
    home, monkeypatch: pytest.MonkeyPatch, winps_answers, on_windows, no_suspend
) -> None:
    winps_answers(lastwake=_WOKEN_BY_TASK, idle=_IDLE_LONG)
    monkeypatch.setattr(runner, "_run_impl", lambda *a, **k: {"status": "done"})

    result = runner.run(home)

    assert "sleep_back" not in result
    assert no_suspend == []


# --- 下見が本番の記録を汚さない（検分 S11） -------------------------------------------------


def test_dry_run_does_not_overwrite_the_real_last_run(home) -> None:
    """**`--dry-run` を1回叩いたら `status` が「前回は dry_run」と答える**のはおかしい。

    本番が動いたかどうかを見る唯一の記録が、下見で消えていた（検分中に実際に踏まれた）。
    """
    runner._write_last_run(home, {"status": "done", "started_at": "本番"})
    runner._write_last_run(home, {"status": "dry_run", "started_at": "下見"}, dry_run=True)

    status = runner.status(home)

    assert status["last_run"]["status"] == "done"
    assert status["last_run"]["started_at"] == "本番"
    # 下見のほうも残っている（何を返したかを後から見たいことがある）
    assert status["last_dry_run"]["status"] == "dry_run"


def test_dry_run_writes_to_its_own_file(home) -> None:
    assert runner.last_run_path(home).name == "last-run.json"
    assert runner.last_run_path(home, dry_run=True).name == "last-run.dry.json"


def test_a_real_dry_run_leaves_the_real_record_untouched(home) -> None:
    """通しで確かめる。**`tasks.md` が空なので `claude` は呼ばれない**（起動の門）。"""
    runner._write_last_run(home, {"status": "done", "started_at": "きのうの本番"})
    runner.tasks_path(home).parent.mkdir(parents=True, exist_ok=True)
    runner.tasks_path(home).write_text("# 空の指示書\n", encoding="utf-8")

    result = runner.run(home, dry_run=True)

    assert result["status"] == "empty"
    assert runner.status(home)["last_run"]["started_at"] == "きのうの本番"


def test_status_says_there_is_no_real_run_yet_even_after_a_dry_run(home) -> None:
    """本番が一度も走っていないなら、下見を何回叩いても「記録なし」のまま。"""
    runner._write_last_run(home, {"status": "dry_run"}, dry_run=True)

    assert runner.status(home)["last_run"] is None
    assert "最後の実行: （記録なし）" in runner.format_status(runner.status(home))


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
