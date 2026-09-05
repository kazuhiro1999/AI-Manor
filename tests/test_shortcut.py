"""`manor shortcut`（ADR-011 D8）。デスクトップの起動ショートカット。

本物の Desktop / `%LOCALAPPDATA%` には触れない——各試験が `MANOR_SHORTCUT_DIR` /
`MANOR_DESKTOP_DIR` を一時ディレクトリへ向ける（`apply_setup` 経由の試験は
`tests/conftest.py` の `home_path` が既定でこの2つを一時ディレクトリへ向けている）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manor import cli
from manor import profile as profile_mod
from manor import shortcut as shortcut_mod


@pytest.fixture
def isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """`MANOR_SHORTCUT_DIR` / `MANOR_DESKTOP_DIR` を一時ディレクトリへ向ける。"""
    launcher_dir = tmp_path / "launcher"
    desktop_dir = tmp_path / "desktop"
    monkeypatch.setenv(shortcut_mod.ENV_SHORTCUT_DIR, str(launcher_dir))
    monkeypatch.setenv(shortcut_mod.ENV_DESKTOP_DIR, str(desktop_dir))
    return launcher_dir, desktop_dir


# --- launcher_script: 中身（止める→ビルド→起動→開く。ポートを埋め込む） -----------------------


def test_launcher_script_windows_contains_stop_before_start_and_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shortcut_mod.sys, "platform", "win32")
    script = shortcut_mod.launcher_script(port=9999, repo_root=Path(r"C:\fake\repo"))
    assert "9999" in script
    assert "manor.web serve" in script
    stop_idx = script.index("taskkill")
    start_idx = script.index("manor.web serve")
    assert stop_idx < start_idx, "既存サーバを止めるのはサーバ起動より前でなければならない"


def test_launcher_script_windows_starts_server_without_new_console_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`start` に `/B` が無いと、サーバ専用の新しいコンソール窓（既定の端末アプリ、
    Windows Terminal 等）が開いてしまい、それがサーバを止めるまで残り続ける
    ——`.lnk` を wscript 経由にしただけでは直らない、実機で見つかった追加の不具合。
    """
    monkeypatch.setattr(shortcut_mod.sys, "platform", "win32")
    script = shortcut_mod.launcher_script(port=9999, repo_root=Path(r"C:\fake\repo"))
    assert 'start /B "" "' in script
    assert 'start "manor"' not in script


def test_launcher_script_darwin_contains_stop_before_start_and_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shortcut_mod.sys, "platform", "darwin")
    script = shortcut_mod.launcher_script(port=7000, repo_root=Path("/fake/repo"))
    assert "PORT=7000" in script
    assert script.startswith("#!/bin/bash")
    stop_idx = script.index("kill -9")
    start_idx = script.index("manor.web serve")
    assert stop_idx < start_idx


def test_launcher_script_other_platform_contains_stop_before_start_and_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shortcut_mod.sys, "platform", "linux")
    script = shortcut_mod.launcher_script(port=8000, repo_root=Path("/fake/repo"))
    assert "PORT=8000" in script
    assert script.startswith("#!/bin/sh")
    stop_idx = script.index("kill -9")
    start_idx = script.index("manor.web serve")
    assert stop_idx < start_idx


# --- vbs シム: cmd 窓を出さずにランチャーを走らせる（Windows のみ意味を持つ） ---------------------


def test_vbs_script_contains_hidden_run_call_and_launcher_path() -> None:
    launcher = Path(r"C:\Users\<user name>\AppData\Local\manor\launch-manor.cmd")
    script = shortcut_mod._vbs_script(launcher=launcher)
    assert 'CreateObject("WScript.Shell").Run' in script
    assert "0, False" in script
    assert str(launcher) in script


def test_windows_lnk_script_targets_wscript_with_vbs_argument_and_hidden_style() -> None:
    lnk = Path(r"C:\Users\<user name>\Desktop\manor を開く.lnk")
    vbs = Path(r"C:\Users\<user name>\AppData\Local\manor\launch-manor-hidden.vbs")
    workdir = Path(r"C:\repo path\manor")
    script = shortcut_mod._windows_lnk_script(lnk_path=lnk, vbs_path=vbs, workdir=workdir)
    assert "wscript.exe" in script.lower()
    assert "//nologo" in script
    assert str(vbs) in script
    assert "$lnk.WindowStyle = 7" in script
    # パスに空白があっても引数として1つにまとまるよう `"` で囲んでいる（バッククォート escape）
    assert f'`"{vbs}`"' in script


def test_wscript_exe_path_is_absolute_under_system32() -> None:
    path = shortcut_mod._wscript_exe_path()
    assert path.lower().endswith(r"system32\wscript.exe")
    assert Path(path).is_absolute()


# --- 置き場: 環境変数の差し替えが最優先 -------------------------------------------------------


def test_launcher_dir_honors_env_override(isolated_dirs: tuple[Path, Path]) -> None:
    launcher_dir, _ = isolated_dirs
    assert shortcut_mod.launcher_dir() == launcher_dir


def test_desktop_dir_honors_env_override(isolated_dirs: tuple[Path, Path]) -> None:
    _, desktop_dir = isolated_dirs
    assert shortcut_mod.desktop_dir() == desktop_dir


# --- --dry-run: 何も書かない --------------------------------------------------------------


def test_dry_run_writes_nothing(isolated_dirs: tuple[Path, Path]) -> None:
    launcher_dir, desktop_dir = isolated_dirs
    result = shortcut_mod.create(port=8789, dry_run=True)

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert "8789" in result["launcher_script"]
    assert not launcher_dir.exists()
    assert not desktop_dir.exists()
    assert not shortcut_mod.launcher_path().exists()
    assert not shortcut_mod.shortcut_path().exists()


def test_cli_dry_run_writes_nothing(
    home_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """`home_path`（`tests/conftest.py`）は `MANOR_HOME` に加えて `MANOR_SHORTCUT_DIR` /
    `MANOR_DESKTOP_DIR` も一時ディレクトリへ向けている——`cli.main()` を通す試験は
    `MANOR_HOME` も隔離しないと、実機の `home/manor.db`（このリポジトリ自身の②データ）に
    `manor.db` migrate が走ってしまう。
    """
    rc = cli.main(["shortcut", "create", "--dry-run", "--json"])
    out = capsys.readouterr().out
    data = json.loads(out)

    assert rc == 0
    assert data["ok"] is True
    assert data["dry_run"] is True
    assert not shortcut_mod.launcher_path().exists()
    assert not shortcut_mod.shortcut_path().exists()


# --- create → status → remove の往復（一時ディレクトリの中で完結） -------------------------------


def test_create_then_status_then_remove_roundtrip(isolated_dirs: tuple[Path, Path]) -> None:
    created = shortcut_mod.create(port=8789)
    assert created["ok"] is True, created.get("reason")
    launcher_path = Path(created["launcher_path"])
    shortcut_path = Path(created["shortcut_path"])
    assert launcher_path.is_file()
    assert shortcut_path.is_file()
    assert "8789" in launcher_path.read_text(encoding="utf-8")

    is_windows = shortcut_mod._kind() == "windows"
    vbs_path = Path(created["vbs_path"]) if is_windows else None
    if is_windows:
        assert vbs_path is not None and vbs_path.is_file()
        # utf-16 で書く（WSH が日本語コメントを正しく読める形式。`_vbs_script` の docstring 参照）
        assert str(launcher_path) in vbs_path.read_text(encoding="utf-16")

    st = shortcut_mod.status()
    assert st["ok"] is True
    assert st["launcher"]["exists"] is True
    assert st["launcher"]["path"] == str(launcher_path)
    assert st["shortcut"]["exists"] is True
    assert st["shortcut"]["path"] == str(shortcut_path)
    if is_windows:
        assert st["vbs"]["exists"] is True
        assert st["vbs"]["path"] == str(vbs_path)

    removed = shortcut_mod.remove()
    assert removed["ok"] is True
    # Windows は絵（`.ico`）もランチャーと同じ場所へ複製するので、片付けの対象に入る
    expected_removed = (
        {"launcher", "shortcut", "vbs", "icon"} if is_windows else {"launcher", "shortcut"}
    )
    assert set(removed["removed"]) == expected_removed
    assert not launcher_path.exists()
    assert not shortcut_path.exists()
    if is_windows:
        assert vbs_path is not None and not vbs_path.exists()

    st2 = shortcut_mod.status()
    assert st2["launcher"]["exists"] is False
    assert st2["shortcut"]["exists"] is False
    if is_windows:
        assert st2["vbs"]["exists"] is False


def test_vbs_file_is_written_as_utf16_for_wsh_compatibility(
    isolated_dirs: tuple[Path, Path],
) -> None:
    """実機で踏んだ罠の再発防止: UTF-8（BOM無し）のまま書くと、日本語コメントを含む行を
    Windows Script Host が ANSI コードページで誤読し、`wscript`/`cscript` がエラーも
    出さずに `Run` まで辿り着かず終わる。BOM 付き UTF-16LE（Python の `utf-16` の既定）
    で書けているかをバイト列で確かめる。
    """
    if shortcut_mod._kind() != "windows":
        pytest.skip("Windows 専用の罠（vbs シムは Windows でしか作らない）")
    created = shortcut_mod.create(port=8789)
    assert created["ok"] is True, created.get("reason")
    raw = Path(created["vbs_path"]).read_bytes()
    assert raw[:2] == b"\xff\xfe", "UTF-16LE の BOM で始まっていない"


def test_remove_missing_files_is_not_an_error(isolated_dirs: tuple[Path, Path]) -> None:
    result = shortcut_mod.remove()
    assert result["ok"] is True
    assert result["removed"] == []
    # launcher・shortcut は常に「元々ありません」。Windows はさらに vbs と絵の分が増える
    expected_notes = 4 if shortcut_mod._kind() == "windows" else 2
    assert len(result["notes"]) == expected_notes


def test_cli_create_status_remove_roundtrip_non_windows_kind(
    home_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Windows の実機が無くても（`.lnk` の PowerShell 呼び出しが要らない）確かめられる経路。"""
    monkeypatch.setattr(shortcut_mod.sys, "platform", "linux")

    assert cli.main(["shortcut", "create", "--port", "9100"]) == 0
    capsys.readouterr()

    assert cli.main(["shortcut", "status", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["launcher"]["exists"] is True
    assert out["shortcut"]["exists"] is True
    assert out["shortcut"]["path"].endswith(".desktop")

    assert cli.main(["shortcut", "remove", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out["removed"]) == {"launcher", "shortcut"}


# --- 奇妙な/未対応のプラットフォームは理由つきで諦める。例外を投げない ------------------------------


def test_odd_platform_string_degrades_to_desktop_entry_without_raising(
    isolated_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shortcut_mod.sys, "platform", "freebsd13")
    result = shortcut_mod.create()
    assert result["ok"] is True
    assert result["platform"] == "other"
    assert Path(result["shortcut_path"]).name.endswith(".desktop")


def test_windows_lnk_creation_failure_returns_reason_without_raising(
    isolated_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shortcut_mod.sys, "platform", "win32")

    def fake_run(cmd, *a, **kw):  # type: ignore[no-untyped-def]
        raise OSError("powershell が見つかりません（テスト用の偽の失敗）")

    monkeypatch.setattr(shortcut_mod.subprocess, "run", fake_run)

    result = shortcut_mod.create()  # must not raise
    assert result["ok"] is False
    assert "PowerShell" in result["reason"]


def test_unexpected_write_failure_returns_reason_without_raising(
    isolated_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_write_text(self, *a, **kw):  # type: ignore[no-untyped-def]
        raise OSError("書けません（テスト用の偽の失敗）")

    monkeypatch.setattr(Path, "write_text", fake_write_text)

    result = shortcut_mod.create()  # must not raise
    assert result["ok"] is False
    assert "reason" in result


def test_cli_create_failure_prints_reason_and_exits_1(
    home_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(shortcut_mod.sys, "platform", "win32")

    def fake_run(cmd, *a, **kw):  # type: ignore[no-untyped-def]
        raise OSError("偽の失敗")

    monkeypatch.setattr(shortcut_mod.subprocess, "run", fake_run)

    rc = cli.main(["shortcut", "create"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "PowerShell" in out


# --- profile.apply_setup との連携（ADR-007 × ADR-011 D8） --------------------------------------


def test_apply_setup_default_creates_shortcut(conn) -> None:
    """`shortcut` を省略すれば既定 `true`。`tests/conftest.py` の `home_path` が
    `MANOR_SHORTCUT_DIR`/`MANOR_DESKTOP_DIR` を一時ディレクトリへ向けているので、
    実機の Desktop には触れない。
    """
    result = profile_mod.apply_setup(conn, {"callname": "旦那様"})
    conn.commit()

    assert result["warnings"] == []
    assert shortcut_mod.launcher_path().is_file()
    assert shortcut_mod.shortcut_path().is_file()


def test_apply_setup_shortcut_false_creates_nothing(conn) -> None:
    result = profile_mod.apply_setup(conn, {"callname": "旦那様", "shortcut": False})
    conn.commit()

    assert result["warnings"] == []
    assert not shortcut_mod.launcher_path().exists()
    assert not shortcut_mod.shortcut_path().exists()


def test_apply_setup_shortcut_failure_leaves_setup_successful_with_warning(
    conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_create(**kwargs):  # type: ignore[no-untyped-def]
        return {"ok": False, "reason": "デスクトップに書けません（テスト用の偽の失敗）"}

    monkeypatch.setattr(shortcut_mod, "create", fake_create)

    result = profile_mod.apply_setup(conn, {"callname": "旦那様"})
    conn.commit()

    assert profile_mod.is_setup_done(conn) is True  # セットアップ自体は失敗していない
    assert len(result["warnings"]) == 1
    assert "デスクトップのショートカットは作れませんでした" in result["warnings"][0]
    assert "偽の失敗" in result["warnings"][0]


def test_apply_setup_shortcut_raises_is_still_caught_as_warning(
    conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`shortcut.create()` 自体は例外を投げない設計だが、`apply_setup` 側にも二重の
    安全策がある——万一の例外でもセットアップは失敗させない。
    """

    def fake_create(**kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("想定外の例外（テスト用）")

    monkeypatch.setattr(shortcut_mod, "create", fake_create)

    result = profile_mod.apply_setup(conn, {"callname": "旦那様"})
    conn.commit()

    assert profile_mod.is_setup_done(conn) is True
    assert len(result["warnings"]) == 1
    assert "想定外の例外" in result["warnings"][0]


def test_cli_setup_prints_shortcut_warning(
    home_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()

    def fake_create(**kwargs):  # type: ignore[no-untyped-def]
        return {"ok": False, "reason": "偽の失敗（CLI試験）"}

    monkeypatch.setattr(shortcut_mod, "create", fake_create)

    answers_path = tmp_path / "answers.json"
    answers_path.write_text('{"callname": "旦那様"}', encoding="utf-8")
    assert cli.main(["setup", "--answers", str(answers_path)]) == 0
    out = capsys.readouterr().out
    assert "警告:" in out
    assert "偽の失敗（CLI試験）" in out


def test_create_puts_the_icon_next_to_the_launcher_and_points_the_lnk_at_it(
    isolated_dirs: tuple[Path, Path],
) -> None:
    """絵はリポジトリの中を直に指さず、ランチャーと同じ場所へ複製してから指す。

    `.lnk` は絵の位置を絶対パスで覚えるので、リポジトリのフォルダを動かした瞬間に
    机の上の絵が壊れる（2026-09-05 主人の icon を入れたときの判断）。
    """
    if shortcut_mod._kind() != "windows":
        pytest.skip("`.lnk` の絵は Windows だけ")

    created = shortcut_mod.create(port=8789)
    assert created["ok"] is True, created.get("reason")

    icon = shortcut_mod.icon_path()
    assert icon.is_file(), "絵がランチャーの場所へ複製されていない"
    assert icon.read_bytes() == shortcut_mod.source_icon_path().read_bytes()

    script = shortcut_mod._windows_lnk_script(
        lnk_path=Path("x.lnk"), vbs_path=Path("y.vbs"), workdir=Path("z"), icon=icon
    )
    assert f'$lnk.IconLocation = "{icon},0"' in script


def test_lnk_script_without_an_icon_omits_the_line() -> None:
    """絵が無い環境（`web/public/favicon.ico` を消した場合）でも作成は続く。"""
    script = shortcut_mod._windows_lnk_script(
        lnk_path=Path("x.lnk"), vbs_path=Path("y.vbs"), workdir=Path("z"), icon=None
    )
    assert "IconLocation" not in script
    assert "$lnk.Save()" in script


def test_create_removes_a_shortcut_left_under_the_old_name(
    isolated_dirs: tuple[Path, Path],
) -> None:
    """表示名を変えたとき、古い名前のショートカットが机に残らない。

    `manor を開く` → `AI Manor を開く` の改名（2026-09-05）。片付けないと主人の机に
    同じものが2つ並び、古い方は消えたランチャーを指したまま残る。
    """
    _, desktop = isolated_dirs
    legacy = desktop / shortcut_mod._shortcut_filename(shortcut_mod.LEGACY_SHORTCUT_LABELS[0])
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("古いショートカット", encoding="utf-8")

    created = shortcut_mod.create(port=8789)
    assert created["ok"] is True, created.get("reason")

    assert not legacy.exists(), "古い名前のショートカットが残っている"
    assert str(legacy) in created["legacy_removed"]
    assert Path(created["shortcut_path"]).is_file()


def test_create_does_not_touch_an_unrelated_shortcut(isolated_dirs: tuple[Path, Path]) -> None:
    """片付けるのは挙げた名前だけ。主人が自分で置いた別のものには触らない。"""
    _, desktop = isolated_dirs
    desktop.mkdir(parents=True, exist_ok=True)
    mine = desktop / "私のメモ.lnk"
    mine.write_text("触らないで", encoding="utf-8")

    created = shortcut_mod.create(port=8789)
    assert created["ok"] is True, created.get("reason")

    assert mine.is_file()
    assert mine.read_text(encoding="utf-8") == "触らないで"


def test_launcher_waits_for_the_server_instead_of_a_fixed_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ブラウザを開く前に、サーバが実際に待ち受けるまで待つ。

    以前は 2 秒固定で待って開いていたが、起動はそれより長くかかり、ブラウザが
    真っ白なまま開いていた（主人の実測 2026-09-05「最初だけ表示されない」）。
    """
    monkeypatch.setattr(shortcut_mod.sys, "platform", "win32")
    script = shortcut_mod.launcher_script(port=8789)

    assert "timeout /t 2 /nobreak" not in script, "固定2秒待ちが残っている"
    assert ":manor_wait" in script and ":manor_ready" in script
    # 待ちは「開く」より前で、諦めの上限を持つ
    assert script.index(":manor_ready") < script.index("start \"\" \"http://127.0.0.1:8789/\"")
    assert "GEQ 60" in script


def test_launcher_waits_for_health_on_other_platforms(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows 以外も同じ——固定 sleep ではなく `/api/v1/health` が応えるまで待つ。"""
    for platform in ("darwin", "linux"):
        monkeypatch.setattr(shortcut_mod.sys, "platform", platform)
        script = shortcut_mod.launcher_script(port=8789)
        assert "sleep 2" not in script, f"{platform}: 固定待ちが残っている"
        assert "/api/v1/health" in script
        assert "-lt 60" in script
