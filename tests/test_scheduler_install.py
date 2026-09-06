"""スケジューラ登録（`manor night install` / `manor web install`）の検分。

2026-09-06 の切り替えで**同じ形の不具合を2つ**踏んだので、両方をここで押さえる。

1. **`/TR` の中の `"` を逃がしていなかった** — 空白を含むパス（`...\\AI Agents\\manor`）で
   schtasks が引数を割ってしまい「無効な引数」で落ちる。文字列を目で見るだけでは
   気づけないので、**cmd.exe に本当に渡して argv を数える**（Windows のときだけ）。
2. **落ちたことを見ていなかった** — 「登録しました」と言って終了コード0を返し、
   夜勤が丸ごと動かない朝を作る形（butler/GROWTH.md G14）。

night 側の是正が入ったとき、この2つはどちらも試験が無かった。web 側には同じ形が
残っていた——**片方だけ直すと、もう片方が黙って同じ朝を作る**ので、両方を並べて検算する。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from manor import i18n
from manor import web as web_mod
from manor import night as night_mod
from manor.night import runner
from manor.web import _install as web_install

# 空白を含むパス。**利用者名は書かない**（`tests/test_privacy_boundary.py` が
# `X:\Users\<誰か>` を④の印として弾く——2026-09-06 に実際に弾かれた）。
REPO_WITH_SPACES = Path(r"D:\Claude Workspace\AI Agents\manor")


# --- 1. `/TR` の逃がし --------------------------------------------------------------


@pytest.mark.parametrize(
    ("build", "module"),
    [
        (lambda: runner.build_install_command(at="02:00", repo_root=REPO_WITH_SPACES), runner),
        (lambda: web_install.build_install_command(repo_root=REPO_WITH_SPACES), web_install),
    ],
    ids=["night", "web"],
)
def test_install_command_escapes_quotes_inside_tr(build, module, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module.sys, "platform", "win32")
    cmd = build()
    # `/TR "..."` の中に入る `"` は必ず `\"`。素の `"` が混ざると schtasks が途中で切る。
    tr = cmd.split('/TR "', 1)[1].rsplit('" /F', 1)[0]
    assert '\\"' in tr
    assert '"' not in tr.replace('\\"', "")


@pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe の argv の割れ方を見る試験")
@pytest.mark.parametrize(
    "build",
    [
        lambda: runner.build_install_command(at="02:00"),
        lambda: web_install.build_install_command(),
    ],
    ids=["night", "web"],
)
def test_install_command_reaches_schtasks_as_one_argument(build, tmp_path: Path) -> None:
    """**文字列を目で見ない。** cmd.exe に本当に通し、`/TR` が1個の引数として届くか数える。

    ここが割れると schtasks は「無効な引数」を返す——2026-09-06 に実際に踏んだ形。
    """
    echo = tmp_path / "echoargs.py"
    echo.write_text("import json, sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
    cmd = build().replace("schtasks", f'"{sys.executable}" "{echo}"', 1)

    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)  # noqa: S602
    assert proc.returncode == 0, proc.stderr
    argv = json.loads(proc.stdout)

    tr = argv[argv.index("/TR") + 1]
    assert tr.startswith("cmd /c cd /d ")
    assert tr.endswith(" run") or tr.endswith("--port 8789")
    assert argv[-1] == "/F"  # `/F` が `/TR` に飲み込まれていない


# --- 2. 「できたか」で言葉を選ぶ -------------------------------------------------------


def _failed(returncode: int = 1, stderr: str = "ERROR: 無効な引数です") -> dict:
    return {
        "command": "schtasks ...", "executed": True, "ok": False,
        "returncode": returncode, "stdout": "", "stderr": stderr,
    }


@pytest.mark.parametrize("lang", ["ja", "en"])
def test_night_outcome_line_reports_the_failure_without_raising(lang: str) -> None:
    """**この行が例外で落ちてはいけない。**

    2026-09-06 の検分で実測: 差し込みを `t(key).format(...)` で渡していたため、
    `t()` の中の引数ゼロの `format` が先に走って I18nError で落ちていた——
    「失敗を隠さない」ために足した経路が、そこだけ落ちる形になっていた。
    """
    i18n.set_language(lang)
    try:
        line = night_mod._outcome_line(_failed(returncode=1), "install")
    finally:
        i18n.set_language("ja")
    assert "1" in line
    assert "無効な引数" in line


@pytest.mark.parametrize("lang", ["ja", "en"])
def test_web_outcome_line_reports_the_failure_without_raising(lang: str) -> None:
    i18n.set_language(lang)
    try:
        line = web_mod._outcome_line(_failed(returncode=1), "install", done_key="registered")
    finally:
        i18n.set_language("ja")
    assert "1" in line
    assert "無効な引数" in line


def test_night_outcome_line_says_done_only_when_ok() -> None:
    done = {"command": "x", "executed": True, "ok": True}
    assert night_mod._outcome_line(done, "install") == i18n.t("night.install.done")
    dry = {"command": "x", "executed": False, "ok": None}
    assert night_mod._outcome_line(dry, "install") == i18n.t("night.install.dry_run_note")


def test_web_outcome_line_says_registered_only_when_ok() -> None:
    done = {"command": "x", "executed": True, "ok": True}
    assert web_mod._outcome_line(done, "install", done_key="registered") == i18n.t("web.install.registered")
    dry = {"command": "x", "executed": False, "ok": None}
    assert web_mod._outcome_line(dry, "install", done_key="registered") == i18n.t("web.install.preview_only")


# --- 3. 終了コード --------------------------------------------------------------------


def test_night_install_exits_nonzero_when_registration_failed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(runner, "install", lambda **kw: _failed())
    from manor import cli

    assert cli.main(["night", "install", "--yes"]) == 1
    assert "無効な引数" in capsys.readouterr().out


def test_web_install_exits_nonzero_when_registration_failed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(web_install, "install", lambda **kw: _failed())
    from manor import cli

    assert cli.main(["web", "install", "--yes"]) == 1
    assert "無効な引数" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("argv", "target", "attr"),
    [
        (["night", "install"], runner, "install"),
        (["web", "install"], web_install, "install"),
    ],
    ids=["night", "web"],
)
def test_dry_run_still_exits_zero(
    argv: list[str], target, attr: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--yes` を渡していないときは「登録していません」で終了コード0（今までどおり）。"""
    monkeypatch.setattr(target, attr, lambda **kw: {"command": "x", "executed": False, "ok": None})
    from manor import cli

    assert cli.main(argv) == 0
