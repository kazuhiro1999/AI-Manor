"""`manor shortcut create|remove|status` — デスクトップの起動ショートカット（ADR-011 D8）。

主人の実測の困りごと: 「サーバーを再起動して更新する際に毎回コマンドプロンプトで
`uv run ...` するのは面倒」。ここが直す。

デスクトップに置くのは**小さな入口**だけ。中身（既存サーバがあれば止める → web/ を
ビルド → サーバを起動 → ブラウザを開く、を1つにしたもの）は主人の環境の中に書く
**ランチャースクリプト**に持たせる:

- Windows: `%LOCALAPPDATA%/manor/launch-manor.cmd`
- macOS: `~/.manor/launch-manor.command`
- それ以外: `~/.local/share/manor/launch-manor.sh`

**OS 固有スクリプトはリポジトリに置かない**——生成するのは `create()` を呼んだときだけ
（ADR-011 D8）。デスクトップの実体はこのランチャーを指すだけの薄い入口:

- Windows: `.lnk`（PowerShell の `WScript.Shell` で作る。実行ファイルそのものではない
  ので Target をランチャーへ向ける）
- macOS: `.command`（それ自体が実行可能。中身はランチャーを `exec` するだけ）
- それ以外: `.desktop`（`Exec=` がランチャーを指す）

`web/_install.py`（`manor web install`）や `night.runner.install` と同じ精神——
危険な操作の前には必ず内容を見せる——だが、ここは「見せるだけで登録しない」
`--yes` 型ではない（ファイルを書くこと自体が主目的のコマンドなので）。代わりに
`--dry-run` で「何をどこに書くか」を先に確認できるようにした。

**この module は例外を投げない。** 失敗はすべて `{"ok": False, "reason": "..."}` に
畳んで返す——CLI 側はそれを1行と終了コード1にするだけ（`face.py` と同じ「開けない・
効かないは1行で済ませ、落とさない」流儀）。

試験は本物の Desktop / `%LOCALAPPDATA%` に触れない——`MANOR_SHORTCUT_DIR` と
`MANOR_DESKTOP_DIR` を立てればそちらを使う（`tests/conftest.py` の `home_path` が
既定でこの2つを一時ディレクトリへ向けている）。
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

from . import branding
from . import i18n
from . import util
from . import winps

#: `manor web serve` の既定ポート（`src/manor/web/__init__.py` と同じ値。`face.py` と
#: 同じ理由でここでも複製する——web パッケージの依存を強制しないため）。
DEFAULT_PORT = 8789

#: デスクトップに出す名前（ADR-011 D8）。拡張子は OS ごとに変わる。
#: 主人の指示（2026-09-05）で「を開く」を外した——机の上のアイコンは名前だけでよい。
SHORTCUT_LABEL = branding.APP_NAME

#: 以前の名前で作られたショートカット。**改名しても古い方が残ると机に2つ並ぶ**ので、
#: `create` のたびに片付ける（主人の机の上のものに触るので、ここに挙げた名前だけ）。
LEGACY_SHORTCUT_LABELS = ("manor を開く", "AI Monor を開く", "AI Manor を開く")

#: `.lnk` に持たせる絵のファイル名。ランチャーと同じ場所へ複製してから指す——
#: リポジトリの中を指すと、フォルダを動かした瞬間に机の上の絵が壊れる。
_ICON_FILENAME = "manor.ico"

#: 試験用の置き場の差し替え（`tests/conftest.py` 参照）。
ENV_SHORTCUT_DIR = "MANOR_SHORTCUT_DIR"
ENV_DESKTOP_DIR = "MANOR_DESKTOP_DIR"


def _kind() -> str:
    """`"windows"` / `"darwin"` / `"other"`。試験は `sys.platform` を差し替えて確かめる。"""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    return "other"


# --- 置き場 ------------------------------------------------------------------------


def launcher_dir() -> Path:
    """ランチャースクリプトを書く場所。`MANOR_SHORTCUT_DIR` があれば最優先（試験用）。"""
    override = os.environ.get(ENV_SHORTCUT_DIR, "").strip()
    if override:
        return Path(override)
    kind = _kind()
    if kind == "windows":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        if not base:
            base = str(Path.home() / "AppData" / "Local")
        return Path(base) / "manor"
    if kind == "darwin":
        return Path.home() / ".manor"
    return Path.home() / ".local" / "share" / "manor"


def _launcher_filename() -> str:
    kind = _kind()
    if kind == "windows":
        return "launch-manor.cmd"
    if kind == "darwin":
        return "launch-manor.command"
    return "launch-manor.sh"


def launcher_path() -> Path:
    return launcher_dir() / _launcher_filename()


#: cmd を非表示で実行する VBScript シムのファイル名（Windows のみ）。ランチャーと同じ場所に置く。
_VBS_FILENAME = "launch-manor-hidden.vbs"


def icon_path() -> Path:
    """`.lnk` が指す絵の置き場（ランチャーと同じ場所）。"""
    return launcher_dir() / _ICON_FILENAME


def source_icon_path() -> Path:
    """リポジトリの中の元の絵（`web/public/favicon.ico`）。"""
    return util.repo_root() / "web" / "public" / "favicon.ico"


def vbs_path() -> Path:
    """cmd 窓を消すための VBScript シムの置き場（Windows のみ意味を持つ）。"""
    return launcher_dir() / _VBS_FILENAME


def _desktop_dir_windows() -> Path:
    """ロケール依存のデスクトップ名（例: 日本語環境でも `Desktop` フォルダ名自体は英語だが、
    念のため）に振り回されないよう、まず `[Environment]::GetFolderPath('Desktop')` を試す。
    PowerShell が呼べない・失敗したときは `USERPROFILE\\Desktop` へ落ちる。
    """
    try:
        # 戻り値に日本語の利用者名が入りうるので `winps` 経由（出力を UTF-8 に固定する）。
        code, out, _err = winps.run("[Environment]::GetFolderPath('Desktop')", timeout=10)
        out = out.strip()
        if code == 0 and out:
            return Path(out)
    except Exception:  # noqa: BLE001 - フォールバックへ落ちるだけ。ここで止めない
        pass
    userprofile = os.environ.get("USERPROFILE", "").strip()
    if userprofile:
        return Path(userprofile) / "Desktop"
    return Path.home() / "Desktop"


def desktop_dir() -> Path:
    """デスクトップの場所。`MANOR_DESKTOP_DIR` があれば最優先（試験は本物のデスクトップに
    触れない）。
    """
    override = os.environ.get(ENV_DESKTOP_DIR, "").strip()
    if override:
        return Path(override)
    if _kind() == "windows":
        return _desktop_dir_windows()
    return Path.home() / "Desktop"


def _shortcut_filename(label: str = "") -> str:
    """ショートカットのファイル名。`label` を省くと今の表示名（`SHORTCUT_LABEL`）。

    引数を取るのは、**以前の表示名で作られたものを同じ規則で組み立てて片付ける**ため
    （`_remove_legacy_shortcuts`）。拡張子の規則を2か所に書かない。
    """
    label = label or SHORTCUT_LABEL
    kind = _kind()
    if kind == "windows":
        return f"{label}.lnk"
    if kind == "darwin":
        return f"{label}.command"
    return f"{label}.desktop"


def shortcut_path() -> Path:
    return desktop_dir() / _shortcut_filename()


# --- ランチャースクリプトの中身 -------------------------------------------------------


def _python_exe(repo_root: Path) -> str:
    """`_install.py`/`night/runner.py` と同じ約束: `.venv` の中の python を直接呼ぶ。"""
    if _kind() == "windows":
        return str(repo_root / ".venv" / "Scripts" / "python.exe")
    return str(repo_root / ".venv" / "bin" / "python")


def launcher_script(*, port: int = DEFAULT_PORT, repo_root: Path | None = None) -> str:
    """ランチャースクリプトの中身。**止める → ビルド → 起動 → 待ち受けるまで待つ →
    ブラウザを開く**の順（ADR-011 D8）。「止めてから起こす」が主人の困りごとそのもの
    ——ビルドやサーバ起動より前に必ず置く。

    **待ちは固定秒にしない。** 以前は 2 秒待って開いていたが、起動はそれより長くかかり、
    ブラウザが真っ白なまま開いていた（主人の実測 2026-09-05「最初だけ表示されない」）。
    実際にポートが待ち受ける（他OSは `/api/v1/health` が応える）まで、最大60秒待つ。
    """
    repo = Path(repo_root) if repo_root is not None else util.repo_root()
    python_exe = _python_exe(repo)
    url = f"http://127.0.0.1:{port}/"
    kind = _kind()

    if kind == "windows":
        return (
            "@echo off\r\n"
            "rem manor の起動ランチャー。`manor shortcut create` が生成します。\r\n"
            "rem 手で編集しても次回の `manor shortcut create` で上書きされます（ADR-011 D8）。\r\n"
            "setlocal\r\n"
            f'set "PORT={port}"\r\n'
            f'set "REPO={repo}"\r\n'
            f'set "PYTHON={python_exe}"\r\n'
            "\r\n"
            "rem 既にこのポートで待ち受けているサーバがあれば止める（更新を反映するため）\r\n"
            'for /f "tokens=5" %%P in (\'netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"\') do (\r\n'
            "    taskkill /F /PID %%P >nul 2>&1\r\n"
            ")\r\n"
            "\r\n"
            'cd /d "%REPO%"\r\n'
            "\r\n"
            "rem web/ を最新にする（失敗しても続行。無くても manor web serve 自体は動く）\r\n"
            '"%PYTHON%" -m manor.web build >nul 2>&1\r\n'
            "\r\n"
            "rem `/B` で新しいコンソール窓を開かずに同じ（隠れた）窓の中で動かす——\r\n"
            "rem `/B` を付けない `start` は既定でサーバ専用の新しい窓（Windows Terminal 等）を\r\n"
            "rem 開いてしまい、これが『残り続けるコンソール窓』の正体だった\r\n"
            'start /B "" "%PYTHON%" -m manor.web serve --port %PORT%\r\n'
            "\r\n"
            "rem サーバが実際に待ち受けるまで待つ。**固定待ちにしない**——2秒では\r\n"
            "rem 起動が間に合わず、ブラウザが真っ白なまま開いていた（主人の実測\r\n"
            "rem 2026-09-05「最初だけ表示されない」）。最大60秒で諦めて開く。\r\n"
            "set /a TRIES=0\r\n"
            ":manor_wait\r\n"
            'netstat -ano | findstr /R /C:":%PORT% .*LISTENING" >nul 2>&1\r\n'
            "if not errorlevel 1 goto manor_ready\r\n"
            "set /a TRIES+=1\r\n"
            "if %TRIES% GEQ 60 goto manor_ready\r\n"
            "timeout /t 1 /nobreak >nul\r\n"
            "goto manor_wait\r\n"
            ":manor_ready\r\n"
            f'start "" "{url}"\r\n'
        )
    if kind == "darwin":
        return (
            "#!/bin/bash\n"
            "# manor の起動ランチャー。`manor shortcut create` が生成します。\n"
            "# 手で編集しても次回の `manor shortcut create` で上書きされます（ADR-011 D8）。\n"
            f"PORT={port}\n"
            f'REPO="{repo}"\n'
            f'PYTHON="{python_exe}"\n'
            "\n"
            "# 既にこのポートで待ち受けているサーバがあれば止める（更新を反映するため）\n"
            'PIDS=$(lsof -ti "tcp:$PORT" 2>/dev/null)\n'
            'if [ -n "$PIDS" ]; then\n'
            "  kill -9 $PIDS 2>/dev/null\n"
            "fi\n"
            "\n"
            'cd "$REPO" || exit 1\n'
            "\n"
            "# web/ を最新にする（失敗しても続行。無くても manor web serve 自体は動く）\n"
            '"$PYTHON" -m manor.web build >/dev/null 2>&1\n'
            "\n"
            'nohup "$PYTHON" -m manor.web serve --port "$PORT" >/dev/null 2>&1 &\n'
            "\n"
            "# サーバが実際に応えるまで待つ（固定待ちだと起動が間に合わず真っ白になる）\n"
            "TRIES=0\n"
            'while [ "$TRIES" -lt 60 ]; do\n'
            '  if curl -s -o /dev/null "http://127.0.0.1:$PORT/api/v1/health"; then break; fi\n'
            "  TRIES=$((TRIES + 1))\n"
            "  sleep 1\n"
            "done\n"
            f'open "{url}"\n'
        )
    return (
        "#!/bin/sh\n"
        "# manor の起動ランチャー。`manor shortcut create` が生成します。\n"
        "# 手で編集しても次回の `manor shortcut create` で上書きされます（ADR-011 D8）。\n"
        f"PORT={port}\n"
        f'REPO="{repo}"\n'
        f'PYTHON="{python_exe}"\n'
        "\n"
        "# 既にこのポートで待ち受けているサーバがあれば止める（更新を反映するため）\n"
        "if command -v fuser >/dev/null 2>&1; then\n"
        '  PIDS=$(fuser -n tcp "$PORT" 2>/dev/null)\n'
        '  if [ -n "$PIDS" ]; then\n'
        "    kill -9 $PIDS 2>/dev/null\n"
        "  fi\n"
        "fi\n"
        "\n"
        'cd "$REPO" || exit 1\n'
        "\n"
        "# web/ を最新にする（失敗しても続行。無くても manor web serve 自体は動く）\n"
        '"$PYTHON" -m manor.web build >/dev/null 2>&1\n'
        "\n"
        'nohup "$PYTHON" -m manor.web serve --port "$PORT" >/dev/null 2>&1 &\n'
        "\n"
        "# サーバが実際に応えるまで待つ（固定待ちだと起動が間に合わず真っ白になる）\n"
        "TRIES=0\n"
        'while [ "$TRIES" -lt 60 ]; do\n'
        '  if command -v curl >/dev/null 2>&1 && curl -s -o /dev/null "http://127.0.0.1:$PORT/api/v1/health"; then break; fi\n'
        "  TRIES=$((TRIES + 1))\n"
        "  sleep 1\n"
        "done\n"
        f'( command -v xdg-open >/dev/null 2>&1 && xdg-open "{url}" >/dev/null 2>&1 & )\n'
    )


# --- デスクトップの薄い入口 ------------------------------------------------------------


def _wscript_exe_path() -> str:
    """`wscript.exe` のフルパス。`%WINDIR%`（無ければ `%SystemRoot%`）配下の `System32`。

    `.lnk` の Target は実行ファイルそのものを指す約束——`wscript.exe` を経由することで
    cmd 窓を出さずに VBScript シムを走らせる（下の `_vbs_script` 参照）。
    """
    windir = os.environ.get("WINDIR", "").strip() or os.environ.get("SystemRoot", "").strip()
    if not windir:
        windir = r"C:\Windows"
    return str(Path(windir) / "System32" / "wscript.exe")


def _vbs_script(*, launcher: Path) -> str:
    """cmd 窓を出さずにランチャーを走らせる VBScript シム。

    `WScript.Shell.Run` の第2引数 `0` = 窓を隠す、第3引数 `False` = 完了を待たない
    （待つとダブルクリックしたエクスプローラ側がブロックされる）。VBScript の文字列
    リテラルは `"` を2つ重ねてエスケープする（パスに空白があっても正しく1つの
    引数として渡るように、パス全体を `"` で囲む）。

    **実機で踏んだ罠**: この文字列を UTF-8（BOM 無し）でそのまま書くと、日本語の
    コメントを含む行を Windows Script Host が既定の ANSI コードページ（日本語環境
    なら Shift_JIS）で読み込もうとして文字化けし、`wscript`/`cscript` がダイアログも
    エラーも出さずに **何もせず終わる**（`Run` まで辿り着かない）。ここで書き込む側
    （`_create` 内）は必ず `encoding="utf-16"`（BOM 付き UTF-16LE。Windows Script
    Host が正しく認識する形式）で保存すること——UTF-8 のままでは壊れる。
    """
    quoted = str(launcher).replace('"', '""')
    return (
        "' manor の起動ランチャーを cmd 窓を出さずに実行するシムです。\r\n"
        "' `manor shortcut create` が生成します。手で編集しても次回の\r\n"
        "' `manor shortcut create` で上書きされます（ADR-011 D8）。\r\n"
        f'CreateObject("WScript.Shell").Run """{quoted}""", 0, False\r\n'
    )


def _windows_lnk_script(*, lnk_path: Path, vbs_path: Path, workdir: Path, icon: Path | None = None) -> str:
    """`.lnk` を作る PowerShell（ADR-011 D8: `WScript.Shell` の `CreateShortcut`）。

    Target は `wscript.exe`、Arguments が VBScript シムを指す——`.lnk` が直接 cmd を
    指すと Windows がコンソール窓を開いてしまうため（主人の実測の困りごと）。
    `WindowStyle = 7`（最小化）は二重の備え。PowerShell の二重引用符リテラルの中に
    `"` を入れるにはバッククォートでエスケープする（`` `" ``）——パスの空白はこれで
    壊れない。
    """
    wscript = _wscript_exe_path()
    return (
        "$shell = New-Object -ComObject WScript.Shell\n"
        f'$lnk = $shell.CreateShortcut("{lnk_path}")\n'
        f'$lnk.TargetPath = "{wscript}"\n'
        f'$lnk.Arguments = "//nologo `"{vbs_path}`""\n'
        f'$lnk.WorkingDirectory = "{workdir}"\n'
        "$lnk.WindowStyle = 7\n"
        f'$lnk.Description = "{SHORTCUT_LABEL}"\n'
        # 絵を指定しないと wscript.exe の絵が机に出る（何の道具か分からない）。
        + (f'$lnk.IconLocation = "{icon},0"\n' if icon is not None else "")
        + "$lnk.Save()\n"
    )


def _macos_shortcut_script(*, launcher: Path) -> str:
    return "#!/bin/bash\n" f'exec "{launcher}" "$@"\n'


def _linux_desktop_entry(*, launcher: Path) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={SHORTCUT_LABEL}\n"
        f'Exec="{launcher}"\n'
        "Terminal=false\n"
        "Categories=Utility;\n"
    )


def _make_executable(path: Path) -> None:
    try:
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass  # best effort。実行属性が付かなくても後続の判定（ファイルの有無）には影響しない


# --- create / status / remove -------------------------------------------------------


def create(*, port: int = DEFAULT_PORT, dry_run: bool = False) -> dict[str, Any]:
    """`manor shortcut create` の本体。**例外を投げない**——失敗は `ok: False` と
    `reason` に畳んで返す。
    """
    try:
        return _create(port=port, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001 - 「作れませんでした」の1行で済ませる
        return {"ok": False, "reason": f"作成に失敗しました: {exc}"}


def _remove_legacy_shortcuts() -> list[str]:
    """以前の表示名で作ったショートカットを消す（改名で机に2つ並ばないように）。

    **消すのは `LEGACY_SHORTCUT_LABELS` に挙げた名前だけ。** 主人が自分で作った
    別のショートカットには触らない。無ければ何もしない。
    """
    removed: list[str] = []
    current = shortcut_path()
    for label in LEGACY_SHORTCUT_LABELS:
        old_path = desktop_dir() / _shortcut_filename(label)
        if old_path == current or not old_path.is_file():
            continue
        try:
            old_path.unlink()
            removed.append(str(old_path))
        except OSError:
            pass  # 消せなくても作成自体は続ける（机に2つ並ぶだけ）
    return removed


def _install_icon() -> Path | None:
    """絵をランチャーと同じ場所へ複製し、その場所を返す。元が無ければ `None`。

    リポジトリの中を直に指さないのは、フォルダを動かした瞬間に机の上の絵が壊れる
    ため（`.lnk` は絵の位置を絶対パスで覚える）。
    """
    src = source_icon_path()
    if not src.is_file():
        return None
    dst = icon_path()
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        return dst
    except OSError:
        return None


def _create(*, port: int, dry_run: bool) -> dict[str, Any]:
    repo = util.repo_root()
    kind = _kind()
    lpath = launcher_path()
    spath = shortcut_path()
    script = launcher_script(port=port, repo_root=repo)
    vpath = vbs_path() if kind == "windows" else None
    vbs_script = _vbs_script(launcher=lpath) if kind == "windows" else None

    if dry_run:
        result: dict[str, Any] = {
            "ok": True,
            "dry_run": True,
            "platform": kind,
            "port": port,
            "launcher_path": str(lpath),
            "launcher_script": script,
            "shortcut_path": str(spath),
        }
        if kind == "windows":
            result["vbs_path"] = str(vpath)
            result["vbs_script"] = vbs_script
        return result

    lpath.parent.mkdir(parents=True, exist_ok=True)
    lpath.write_text(script, encoding="utf-8", newline="")
    if kind != "windows":
        _make_executable(lpath)

    spath.parent.mkdir(parents=True, exist_ok=True)
    legacy_removed = _remove_legacy_shortcuts()

    if kind == "windows":
        assert vpath is not None and vbs_script is not None  # for type-checkers; kind == windows 確定
        vpath.parent.mkdir(parents=True, exist_ok=True)
        # UTF-8（BOM無し）で書くと、日本語コメントを含む行を Windows Script Host が
        # ANSI コードページ（日本語環境なら Shift_JIS）で誤読し、エラーも出さずに
        # Run まで辿り着かず終わる（実機で踏んだ罠。`_vbs_script` の docstring 参照）。
        # `utf-16` は Python では既定で BOM 付き UTF-16LE になる——WSH が正しく読める形式。
        vpath.write_text(vbs_script, encoding="utf-16", newline="")
        icon = _install_icon()
        ps = _windows_lnk_script(lnk_path=spath, vbs_path=vpath, workdir=repo, icon=icon)
        # **`-Command` に渡さない。** ショートカットの名前に日本語が入るので、英語ロケールの
        # Windows では `?` に落ちてファイル名として不正になる（`winps` の docstring 参照）。
        code, out, err = winps.run(ps, timeout=20)
        if code != 0 or not spath.is_file():
            detail = (err or out).strip()[:400] or "理由不明"
            return {"ok": False, "reason": f"ショートカットの作成に失敗しました: {detail}"}
    elif kind == "darwin":
        spath.write_text(_macos_shortcut_script(launcher=lpath), encoding="utf-8")
        _make_executable(spath)
    else:
        spath.write_text(_linux_desktop_entry(launcher=lpath), encoding="utf-8")
        _make_executable(spath)

    result = {
        "ok": True,
        "legacy_removed": legacy_removed,
        "dry_run": False,
        "platform": kind,
        "port": port,
        "launcher_path": str(lpath),
        "shortcut_path": str(spath),
    }
    if kind == "windows":
        result["vbs_path"] = str(vpath)
    return result


def status() -> dict[str, Any]:
    """ランチャーとショートカットの有無（`manor shortcut status`）。存在確認だけなので
    例外は起きにくいが、念のため呼び出し側と同じ形で畳む。
    """
    try:
        lpath = launcher_path()
        spath = shortcut_path()
        kind = _kind()
        result: dict[str, Any] = {
            "ok": True,
            "platform": kind,
            "launcher": {"path": str(lpath), "exists": lpath.is_file()},
            "shortcut": {"path": str(spath), "exists": spath.is_file()},
        }
        if kind == "windows":
            vpath = vbs_path()
            result["vbs"] = {"path": str(vpath), "exists": vpath.is_file()}
        return result
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"確認に失敗しました: {exc}"}


def remove() -> dict[str, Any]:
    """`manor shortcut remove`。**無いものを消そうとしてもエラーにしない**（D「missing
    files are not an error」）。
    """
    try:
        return _remove()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"削除に失敗しました: {exc}"}


def _remove() -> dict[str, Any]:
    lpath = launcher_path()
    spath = shortcut_path()
    kind = _kind()
    targets = [("launcher", lpath), ("shortcut", spath)]
    if kind == "windows":
        targets.append(("vbs", vbs_path()))
        targets.append(("icon", icon_path()))
    removed: list[str] = []
    notes: list[str] = []
    for label, path in targets:
        if path.is_file():
            path.unlink()
            removed.append(label)
        else:
            notes.append(f"{label} は元々ありません（{path}）")
    result = {
        "ok": True,
        "removed": removed,
        "notes": notes,
        "launcher_path": str(lpath),
        "shortcut_path": str(spath),
    }
    if kind == "windows":
        result["vbs_path"] = str(vbs_path())
    return result


# --- CLI 配線 ----------------------------------------------------------------------


def register(subparsers: "argparse._SubParsersAction") -> None:
    p = subparsers.add_parser(
        "shortcut", help=i18n.t("cli.shortcut.help")
    )
    sub = p.add_subparsers(dest="verb")

    c = sub.add_parser("create", help=i18n.t("cli.shortcut.create.help"))
    c.add_argument("--port", type=int, default=DEFAULT_PORT, help=i18n.t("cli.shortcut.create.port.help", port=DEFAULT_PORT))
    c.add_argument(
        "--dry-run", action="store_true", dest="dry_run", help=i18n.t("cli.shortcut.create.dry_run.help")
    )
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=_cmd_create, is_write=False, needs_db=False)

    r = sub.add_parser("remove", help=i18n.t("cli.shortcut.remove.help"))
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=_cmd_remove, is_write=False, needs_db=False)

    s = sub.add_parser("status", help=i18n.t("cli.shortcut.status.help"))
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=_cmd_status, is_write=False, needs_db=False)


def _cmd_create(args: "argparse.Namespace") -> int:
    result = create(port=args.port, dry_run=bool(args.dry_run))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok", False) else 1

    if not result.get("ok", False):
        print(result.get("reason", i18n.t("shortcut.cli.create.failed_default")))
        return 1

    if result.get("dry_run"):
        print(f"[dry-run] platform={result['platform']} port={result['port']}")
        print(i18n.t("shortcut.cli.create.dry_run.launcher", path=result["launcher_path"]))
        print(i18n.t("shortcut.cli.create.dry_run.script_header"))
        print(result["launcher_script"])
        print(i18n.t("shortcut.cli.create.dry_run.script_footer"))
        if "vbs_path" in result:
            print(i18n.t("shortcut.cli.create.dry_run.vbs", path=result["vbs_path"]))
        print(i18n.t("shortcut.cli.create.dry_run.shortcut", path=result["shortcut_path"]))
        return 0

    print(i18n.t("shortcut.cli.create.launcher", path=result["launcher_path"]))
    if "vbs_path" in result:
        print(i18n.t("shortcut.cli.create.vbs", path=result["vbs_path"]))
    print(i18n.t("shortcut.cli.create.shortcut", path=result["shortcut_path"]))
    print(i18n.t("shortcut.cli.create.done"))
    return 0


def _cmd_remove(args: "argparse.Namespace") -> int:
    result = remove()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok", False) else 1

    if not result.get("ok", False):
        print(result.get("reason", i18n.t("shortcut.cli.remove.failed_default")))
        return 1

    if result["removed"]:
        print(i18n.t("shortcut.cli.remove.done", removed=", ".join(result["removed"])))
    else:
        print(i18n.t("shortcut.cli.remove.nothing"))
    for note in result["notes"]:
        print(note)
    return 0


def _cmd_status(args: "argparse.Namespace") -> int:
    result = status()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok", False) else 1

    if not result.get("ok", False):
        print(result.get("reason", i18n.t("shortcut.cli.status.failed_default")))
        return 1

    print(f"platform: {result['platform']}")
    launcher = result["launcher"]
    shortcut = result["shortcut"]
    exists_yes = i18n.t("shortcut.cli.status.exists_yes")
    exists_no = i18n.t("shortcut.cli.status.exists_no")
    print(i18n.t("shortcut.cli.status.launcher_line", exists=exists_yes if launcher["exists"] else exists_no, path=launcher["path"]))
    if "vbs" in result:
        vbs = result["vbs"]
        print(i18n.t("shortcut.cli.status.vbs_line", exists=exists_yes if vbs["exists"] else exists_no, path=vbs["path"]))
    print(i18n.t("shortcut.cli.status.shortcut_line", exists=exists_yes if shortcut["exists"] else exists_no, path=shortcut["path"]))
    return 0
