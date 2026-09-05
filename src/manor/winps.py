"""Windows で PowerShell を呼ぶときの共通の作法（2026-09-05）。

**`-Command "<文字列>"` に日本語を渡してはいけない。** argv は実行時のアクティブな
コードページで符号化されるので、英語ロケールの Windows では非 ASCII が `?` に落ちる。

実測（GitHub Actions の windows-latest。2026-09-05）:

    「AI Manor を開く.lnk」→「AI Manor ???.lnk」
    → `?` は Windows のファイル名に使えないので `$lnk.Save()` が
      FileNotFoundException で失敗し、ショートカットが作れなかった

**日本語環境では起きない**（CP932 が日本語を通す）ので、手元では見えなかった。
主人の PC は日本語なので実害は無かったが、リポジトリを公開した以上、英語環境の人が
`manor shortcut create` を叩くと壊れる。

`-EncodedCommand` は base64（UTF-16LE）で渡すのでコードページを通らない。出力も同じ
理由で UTF-8 に固定して受ける——デスクトップの場所に日本語の利用者名が入っていると、
戻り値の側で同じことが起きるため。

**ここを通さない例外**: `manor notify` の `speak_command`（`home/config.toml` で主人が
差し替えられる**文字列テンプレート**）。argv の形が契約なので、ここへ寄せると設定の
意味が変わる。英語環境で日本語の文面を読み上げると `?` になる限界が残っている。
"""

from __future__ import annotations

import base64
import subprocess

#: 出力を UTF-8 に固定する前置き。これが無いと戻り値がコンソールのコードページで返る。
_PRELUDE = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"


def encode_command(script: str) -> str:
    """`-EncodedCommand` に渡す base64（UTF-16LE）。"""
    return base64.b64encode((_PRELUDE + script).encode("utf-16-le")).decode("ascii")


def run(script: str, *, timeout: int) -> tuple[int, str, str]:
    """PowerShell を1回動かし、`(終了コード, 標準出力, 標準エラー)` を返す。

    **例外は投げない**（呼び出し元はいずれも「失敗したら別の道へ落ちる」作りなので、
    ここで止めない）。呼べなかったときは終了コード `-1` と、**生の理由**を `stderr` に
    入れて返す——ここで主人向けの文章を組み立てない。何と言うかは、文脈を知っている
    呼び出し元（`shortcut.create` など）の役目である。
    """
    argv = ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encode_command(script)]
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - 呼べない・時間切れも「失敗」に畳む
        return -1, "", str(exc)
    out = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace")
    return proc.returncode, out, err
