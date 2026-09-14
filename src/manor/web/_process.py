"""`manor web stop`（T23）が使う、ポートで待ち受けているプロセスを止める処理。

Windows 専用（`netstat` / `taskkill`）——`launch-manor.cmd`（`%LOCALAPPDATA%\\manor\\`。
④環境固有・git 管理外）が持っていた「既にこのポートで待ち受けているサーバがあれば止める」
手順を core へ持ち上げた（同じ手順が2箇所に散っていた。ランチャー側は環境固有ファイルなので
夜勤からは書き換えない——次に `manor shortcut create` を回すときに、そちらもこの関数を
呼ぶ形へ寄せるのが筋）。他 OS では `find_listening_pids` が常に `[]` を返し、`stop` は
「止めるものが無い」を返す（誤検出しない）。
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any


def find_listening_pids(port: int) -> list[int]:
    """`netstat -ano` で、指定ポートを LISTENING しているプロセスの PID 一覧を返す。

    複数（同じポートを複数プロトコルスタックで待ち受けている等）を想定し、重複無く
    `sorted` で返す。`netstat` が無い・失敗したときは判定できないので `[]`
    （C10 が「判定できないものを誤検出にしない」のと同じ考え方）。
    """
    if not sys.platform.startswith("win"):
        return []
    try:
        proc = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=10
        )
    except OSError:
        return []
    if proc.returncode != 0:
        return []
    suffix = f":{port}"
    pids: set[int] = set()
    for line in proc.stdout.splitlines():
        parts = line.split()
        # 列は `TCP  ローカルアドレス  外部アドレス  状態  PID` の5つ——**状態の位置に
        # 依存しない**（IPv6 行などで列がずれることがある）。PID は常に最後の列。
        if len(parts) < 5 or parts[0] != "TCP" or "LISTENING" not in parts:
            continue
        if not parts[1].endswith(suffix):
            continue
        try:
            pids.add(int(parts[-1]))
        except ValueError:
            continue
    return sorted(pids)


def stop(*, port: int = 8789) -> dict[str, Any]:
    """指定ポートで LISTENING しているプロセスを `taskkill /F` で止める。

    見つからなければ「止めるものが無かった」として `ok=True` を返す（止まっている状態が
    目的なので、既に止まっていれば成功と同じ）。`taskkill` 1つでも失敗すれば `ok=False`
    （`killed` に成功した PID だけが入る）。
    """
    pids = find_listening_pids(port)
    if not pids:
        return {"port": port, "found": [], "killed": [], "ok": True}
    killed: list[int] = []
    for pid in pids:
        try:
            proc = subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)], capture_output=True, text=True, timeout=10
            )
        except OSError:
            continue
        if proc.returncode == 0:
            killed.append(pid)
    return {"port": port, "found": pids, "killed": killed, "ok": len(killed) == len(pids)}
