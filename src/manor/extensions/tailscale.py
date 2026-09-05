"""Tailscale 拡張（ADR-009 D9）。

**設定項目を持たない**——`detect` は `tailscale` コマンドの有無、`check` は
`tailscale status` を回すだけ。**状態を変えるコマンドは一切実行しない**
（`tailscale up`/`tailscale serve` は呼ばない。導入手順として画面に文字で示すだけ）。
`[web] require_passcode` の案内はここに寄せる（ADR-009 D9）。

ADR-013 D2: 以前は「home/config.toml に require_passcode = true を手で追記」と
案内していたが、方針（「フォルダの中をユーザーが操作しなくてよいようにする」）に
正面から反していた。今は設定画面（パスコード節のトグル）から入れられるので、
案内もそちらへ差し替える。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .. import branding

MANIFEST: dict[str, object] = {
    "id": "tailscale",
    "label": "Tailscale（外出先からのアクセス）",
    "kind": "local_app",
    "summary": f"自宅の外から {branding.APP_NAME} の Web アプリへ安全につなげます。無くてもループバック（自宅内）では動きます。",
    "install_steps": [
        "1. https://tailscale.com/download から Tailscale をインストールし、サインインします。",
        f"2. ターミナルで `tailscale serve --bg 8789` を実行し、{branding.APP_NAME} の Web アプリ（既定ポート8789）を tailnet に公開します。",
        "3. 「設定」画面でパスコードを設定したうえで、同じ画面の「外部からのアクセスに"
        "パスコードを要求する」を on にしてください（ループバック以外へ晒すため。ADR-005 D4）。",
    ],
    "fields": [],
    "secret_fields": [],
}

#: `tailscale status` の待ち時間（秒）。短く——UI の「試す」ボタンを長く待たせない。
_STATUS_TIMEOUT = 5


#: PATH に無くても標準の場所に入っていることがある（実測 2026-09-04: Windows の Tailscale は
#: `C:\Program Files\Tailscale\tailscale.exe` に入るが PATH には載らず、`shutil.which` だけでは
#: **入っているのに「未導入」と出た**）。voicevox の `engine_path` 探索と同じ考え方で補う。
_WELL_KNOWN_PATHS: tuple[str, ...] = (
    r"C:\Program Files\Tailscale\tailscale.exe",
    r"C:\Program Files (x86)\Tailscale\tailscale.exe",
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    "/usr/bin/tailscale",
    "/usr/local/bin/tailscale",
    "/opt/homebrew/bin/tailscale",
)


def _find_binary() -> str | None:
    """`tailscale` の実体。PATH → 標準の場所の順に探す。見つからなければ None。"""
    try:
        path = shutil.which("tailscale")
    except Exception:  # noqa: BLE001 - 探索は例外を出さない
        path = None
    if path:
        return path
    for candidate in _WELL_KNOWN_PATHS:
        try:
            if Path(candidate).is_file():
                return candidate
        except OSError:
            continue
    return None


def detect(home: Path) -> dict[str, object]:
    """`tailscale` の実体があるか。それだけ（状態は変えない）。"""
    path = _find_binary()
    if path is None:
        return {"installed": False, "reason": "tailscale コマンドが見つかりません"}
    return {"installed": True, "reason": f"見つかりました: {path}"}


def check(home: Path) -> dict[str, object]:
    """`tailscale status` を実行するだけ。**状態を変えない**。短いタイムアウト。
    コマンドが無い・失敗する・タイムアウトする、いずれも例外を出さず理由を返す。
    """
    try:
        path = _find_binary()
        if path is None:
            return {"ok": False, "reason": "tailscale コマンドが見つかりません"}
        proc = subprocess.run(  # noqa: S603 - 引数は固定リテラルのみ。ユーザー入力を渡さない
            [path, "status"], capture_output=True, text=True, timeout=_STATUS_TIMEOUT
        )
        if proc.returncode != 0:
            lines = (proc.stderr or proc.stdout or "").strip().splitlines()
            reason = lines[0] if lines else f"終了コード {proc.returncode}"
            return {"ok": False, "reason": reason}
        lines = (proc.stdout or "").strip().splitlines()
        reason = lines[0] if lines else "起動しています"
        return {"ok": True, "reason": reason}
    except Exception as exc:  # noqa: BLE001 - check() は例外を外へ出さない
        return {"ok": False, "reason": f"確認できませんでした: {exc}"}
