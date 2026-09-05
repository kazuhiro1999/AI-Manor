"""`manor face` — 姿の小窓を開く（ADR-008 D4）。

v1 `apps/butler-face/show-face.ps1` 相当。**窓を開くところまで**を移植し、隅への配置は
best-effort とする。v1 は Win32 の `MoveWindow` で窓を掴んで動かしていたが、manor は
OS 固有スクリプトを持たない設計方針と衝突するため、位置合わせは Chrome の
`--window-position`/`--window-size` に任せる。効かない（Chrome が既に起動していて
コマンドラインが無視される等）ことがあり、その場合はその旨を1行出すだけで止まらない。

DB には触らない（`needs_db=False`）。開く先は `manor web serve` が待つ `/face`
（ADR-008 D2）。

**ADR-011 D5（画面から開く。ヘッダを消す）**: `window.open` のポップアップには題名の帯が
JS からは消せずに付く。`web/api_v1/face_window.py` の `POST /api/v1/face/open` はここの
`try_open_app_window` を呼び、`manor face` と**同じ Chrome アプリモード起動**をサーバ側から
行う。`open_face`（CLI）との違いは1つだけ——**既定ブラウザへのフォールバックを持たない**。
Chrome が無い／起動できないときは `opened=False` を返すだけで、代わりに別の窓を開いたり
しない（既定ブラウザで開けば結局ヘッダ付きの窓に戻ってしまい、D5 の意味が無くなる。
フォールバックするなら画面側の JS が `window.open` でやる——その旨を伝えるのも画面の役目）。
`manor face`（CLI）の挙動はここでは変えていない。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import webbrowser
from pathlib import Path

from . import i18n
from .agent_meta import valid_agents
from .errors import ManorError
from .talk import BUTLER

#: `manor web serve` の既定ポート（`src/manor/web/__init__.py` と同じ値）。
DEFAULT_PORT = 8789

_CORNERS = ("BottomRight", "BottomLeft", "TopRight", "TopLeft")


def build_url(agent: str, *, port: int = DEFAULT_PORT) -> str:
    return f"http://127.0.0.1:{port}/face?agent={agent}"


def _validate_agent(agent: str) -> str:
    agent = agent.strip()
    agents = valid_agents()
    if agent not in agents:
        known = " / ".join(agents)
        raise ManorError(
            f"担当が見つかりません: {agent!r}（使えるのは {known}）",
            code=2,
            key="error.face.agent_unknown",
            params={"agent": repr(agent), "known": known},
        )
    return agent


# --- Chrome を探す（v1 show-face.ps1 の探索順。クロスプラットフォームに緩める） ------------


def find_chrome() -> str | None:
    """Chrome の実行ファイルを探す。無ければ `None`（呼び出し側は既定ブラウザへ落とす）。"""
    candidates: list[str] = []
    program_files = os.environ.get("ProgramFiles")
    program_files_x86 = os.environ.get("ProgramFiles(x86)")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if program_files:
        candidates.append(str(Path(program_files) / "Google" / "Chrome" / "Application" / "chrome.exe"))
    if program_files_x86:
        candidates.append(str(Path(program_files_x86) / "Google" / "Chrome" / "Application" / "chrome.exe"))
    if local_app_data:
        candidates.append(str(Path(local_app_data) / "Google" / "Chrome" / "Application" / "chrome.exe"))
    # macOS
    candidates.append("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    for path in candidates:
        if path and Path(path).is_file():
            return path
    # Linux やその他: PATH 上のよくある名前
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None


# --- 隅への配置（best-effort。tkinter が無ければ諦める） ---------------------------------


def _screen_size() -> tuple[int, int] | None:
    try:
        import tkinter

        root = tkinter.Tk()
        try:
            w = root.winfo_screenwidth()
            h = root.winfo_screenheight()
        finally:
            root.destroy()
        return int(w), int(h)
    except Exception:  # noqa: BLE001 - 位置合わせは best-effort。tkinter が無くても落とさない
        return None


def compute_position(
    corner: str, *, width: int, height: int, margin: int = 16
) -> tuple[int, int] | None:
    """画面サイズが取れたときだけ `(x, y)` を返す。取れなければ `None`（位置指定は諦める）。"""
    size = _screen_size()
    if size is None:
        return None
    screen_w, screen_h = size
    if corner == "BottomRight":
        return screen_w - width - margin, screen_h - height - margin
    if corner == "BottomLeft":
        return margin, screen_h - height - margin
    if corner == "TopRight":
        return screen_w - width - margin, margin
    return margin, margin  # TopLeft


# --- 開く ------------------------------------------------------------------------------


def open_face(
    *,
    agent: str = BUTLER,
    port: int = DEFAULT_PORT,
    corner: str = "BottomRight",
    width: int = 280,
    height: int = 340,
    dry_run: bool = False,
) -> dict[str, object]:
    """`manor face` の本体。`dry_run=True` なら何も起動せず URL とコマンドだけ返す。

    Chrome が見つからない・起動できない・位置が取れない、いずれも例外にしない
    （呼び出し側の CLI が「効かないことがある」を1行出すだけで済むように、ここでは
    `notes`（起きたことの一覧）を積んで返す）。
    """
    agent = _validate_agent(agent)
    url = build_url(agent, port=port)
    notes: list[str] = []

    chrome = find_chrome()
    position = compute_position(corner, width=width, height=height)
    if position is None:
        notes.append(i18n.t("face.notes.no_screen_size"))

    cmd: list[str] | None = None
    if chrome is not None:
        cmd = [chrome, f"--app={url}", f"--window-size={width},{height}"]
        if position is not None:
            cmd.append(f"--window-position={position[0]},{position[1]}")
    else:
        notes.append(i18n.t("face.notes.no_chrome"))

    result: dict[str, object] = {
        "agent": agent,
        "url": url,
        "chrome": chrome,
        "position": list(position) if position is not None else None,
        "command": cmd,
        "notes": notes,
    }

    if dry_run:
        return result

    if cmd is not None:
        failure = _popen_chrome(cmd)
        if failure is not None:
            # `notes` は `manor face`（CLI）専用の戻り値——`try_open_app_window`（web の
            # `/api/v1/face/open`）はここを通らないので、ここは訳して構わない
            # （`_popen_chrome` 自体の返り値は web 側とも共有するので日本語の生文字列のまま。
            # 下の関数のコメント参照）。
            notes.append(i18n.t("face.notes.chrome_spawn_failed", exc=failure))
            _open_default_browser(url, notes)
    else:
        _open_default_browser(url, notes)

    return result


def _popen_chrome(cmd: list[str]) -> str | None:
    """Chrome を起動する（`open_face`・`try_open_app_window` の共通部分）。
    成功すれば `None`、失敗すれば例外の文字列表現だけを返す（**日本語の文にしない**——
    `try_open_app_window` は web の `/api/v1/face/open` の応答にそのまま使うので、ここで
    訳文を混ぜると web 側の挙動が変わってしまう。文の組み立ては呼び出し側がそれぞれ行う）。
    """
    try:
        subprocess.Popen(cmd)
    except OSError as exc:
        return str(exc)
    return None


def _open_default_browser(url: str, notes: list[str]) -> None:
    try:
        webbrowser.open(url)
    except Exception as exc:  # noqa: BLE001 - 「開けなかった」を1行出すだけで落とさない
        notes.append(i18n.t("face.notes.default_browser_failed", exc=str(exc)))


def try_open_app_window(
    *,
    agent: str = BUTLER,
    port: int = DEFAULT_PORT,
    corner: str = "BottomRight",
    width: int = 280,
    height: int = 340,
) -> dict[str, object]:
    """`POST /api/v1/face/open` の本体（ADR-011 D5）。Chrome のアプリモードだけを試す——
    `open_face`（`manor face` CLI）と違い、**既定ブラウザへは絶対に落ちない**。既定ブラウザで
    開いても結局ヘッダ付きの窓に戻ってしまい D5 の意味が無くなるため、フォールバックは
    画面側の JS に委ねる（Chrome が無かったときだけ `window.open` に落とし、その旨を伝える）。

    例外は投げない（不明な担当だけ `ManorError(code=2)`。呼び出し側の web ルートは
    `web/face.py._require_agent` で先に検査しているので、通常この経路には来ない）。
    """
    agent = _validate_agent(agent)
    url = build_url(agent, port=port)

    chrome = find_chrome()
    if chrome is None:
        return {"opened": False, "method": "none", "reason": "Chrome が見つかりません"}

    position = compute_position(corner, width=width, height=height)
    cmd = [chrome, f"--app={url}", f"--window-size={width},{height}"]
    if position is not None:
        cmd.append(f"--window-position={position[0]},{position[1]}")

    failure = _popen_chrome(cmd)
    if failure is not None:
        # web 向けの応答なのでここは訳さない（`open_face` 側の CLI 表示とは別の文面を
        # 組み立てる。`_popen_chrome` のコメント参照）。挙動・文言とも従来のまま。
        return {"opened": False, "method": "none", "reason": f"Chrome を起動できませんでした（{failure}）"}
    return {"opened": True, "method": "app", "reason": ""}


# --- CLI 配線 ----------------------------------------------------------------------


def register(subparsers: "argparse._SubParsersAction") -> None:
    p = subparsers.add_parser(
        "face", help=i18n.t("cli.face.help")
    )
    p.add_argument("--agent", default=BUTLER, help=i18n.t("cli.face.agent.help"))
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=i18n.t("cli.face.port.help", port=DEFAULT_PORT))
    p.add_argument("--corner", choices=_CORNERS, default="BottomRight")
    p.add_argument("--width", type=int, default=280)
    p.add_argument("--height", type=int, default=340)
    p.add_argument("--dry-run", action="store_true", dest="dry_run", help=i18n.t("cli.face.dry_run.help"))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_face, is_write=False, needs_db=False)


def _cmd_face(args: "argparse.Namespace") -> int:
    result = open_face(
        agent=args.agent, port=args.port, corner=args.corner,
        width=args.width, height=args.height, dry_run=bool(args.dry_run),
    )
    if args.json:
        import json

        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(result["url"])
    if args.dry_run:
        cmd = result["command"]
        print(" ".join(cmd) if cmd else i18n.t("cli.face.dry_run.no_chrome_fallback"))
    for note in result["notes"]:  # type: ignore[union-attr]
        print(note)
    if not args.dry_run:
        print(i18n.t("cli.face.opened") if result["command"] else i18n.t("cli.face.opened_default_browser"))
    return 0
