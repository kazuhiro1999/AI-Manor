"""PowerShell の呼び方の試験（2026-09-05）。

**なぜ要るか**: `-Command "<文字列>"` は argv をアクティブなコードページで符号化するので、
英語ロケールの Windows では日本語が `?` に落ちる。「AI Manor を開く.lnk」が
「AI Manor ???.lnk」になり、`?` はファイル名に使えないので保存が失敗した
（GitHub Actions の windows-latest で実測）。**日本語環境では起きない**ので手元では見えない。
"""

from __future__ import annotations

import base64
import sys

import pytest

from manor import shortcut as shortcut_mod
from manor import winps


def test_encode_command_is_utf16le_base64_and_survives_japanese() -> None:
    """符号化はコードページを通らない（UTF-16LE の base64）。"""
    encoded = winps.encode_command("Write-Output 'AI Manor を開く'")

    decoded = base64.b64decode(encoded).decode("utf-16-le")
    assert "AI Manor を開く" in decoded, "日本語が落ちている"
    assert encoded.isascii(), "base64 が ASCII になっていない（argv で化ける）"


def test_encode_command_fixes_the_output_encoding() -> None:
    """戻り値も UTF-8 で受け取る——デスクトップの場所に日本語の利用者名が入りうる。"""
    decoded = base64.b64decode(winps.encode_command("Write-Output 1")).decode("utf-16-le")
    assert "OutputEncoding" in decoded and "UTF8" in decoded


def test_no_powershell_command_argument_remains_in_the_source() -> None:
    """**`-Command` へ戻さない。** 直したはずの箇所が元に戻っていないことを機構で守る。

    `manor notify` の `speak_command` は主人が `home/config.toml` で差し替えられる
    文字列テンプレートなので対象外（`winps` の docstring に理由を書いてある）。
    """
    from pathlib import Path

    from manor import util

    offenders: list[str] = []
    for path in sorted((util.repo_root() / "src" / "manor").rglob("*.py")):
        if path.name in {"winps.py", "notify.py"}:
            continue
        text = path.read_text(encoding="utf-8")
        if '"-Command"' in text:
            offenders.append(str(path.relative_to(util.repo_root())))
    assert offenders == [], f"-Command を直に使っている箇所がある: {offenders}"


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell は Windows のもの")
def test_run_round_trips_japanese_on_the_real_powershell() -> None:
    """実物の PowerShell で、日本語が往復すること。"""
    code, out, _err = winps.run("Write-Output 'AI Manor を開く'", timeout=30)

    assert code == 0
    assert "AI Manor を開く" in out, f"日本語が落ちた: {out!r}"


@pytest.mark.skipif(sys.platform != "win32", reason="`.lnk` は Windows のもの")
def test_lnk_script_carries_the_japanese_label_through_encoding() -> None:
    """ショートカットの名前（日本語を含む）が符号化を通っても壊れないこと。"""
    from pathlib import Path

    script = shortcut_mod._windows_lnk_script(
        lnk_path=Path("x.lnk"), vbs_path=Path("y.vbs"), workdir=Path("z"), icon=None
    )
    assert shortcut_mod.SHORTCUT_LABEL in script

    decoded = base64.b64decode(winps.encode_command(script)).decode("utf-16-le")
    assert shortcut_mod.SHORTCUT_LABEL in decoded
