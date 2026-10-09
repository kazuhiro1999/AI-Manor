"""中継（GAS）と話す段（ADR-025 §7.2・§7.4・§8）。

- `pull`: 中継から新しいイベントを取り込み、紐づけ表を書き直し、タスクの現在地へ反映する。
- `gas_deploy`: `src/manor/remote/gas/` と、ここで組む `Config.js` を clasp で置いて公開する。
- `machine_add` / `machine_revoke`: PCごとの鍵（中継にはハッシュだけ）。

鍵と宛先は `secrets`（`~/.manor/secrets/remote.json`）。カーソルと間引きの時刻は
`home/remote/state.json`（④ 環境固有。git に入らない）。
"""

from __future__ import annotations

import hashlib
import json
import secrets as pysecrets
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .. import secrets as secrets_mod
from .. import util
from . import store

SECRETS_ID = "remote"
GAS_SRC = Path(__file__).resolve().parent / "gas"
CLIENT_SRC = Path(__file__).resolve().parent / "client" / "manor_report.py"
#: Web の画面から呼ばれる pull を、この秒数より詰めて中継へ出さない。
PULL_MIN_INTERVAL = 10.0
#: 1回の pull で追いかける回数の上限（溜まりが多いときに画面を待たせすぎない）。
PULL_MAX_ROUNDS = 5


class RelayError(RuntimeError):
    pass


def configured() -> bool:
    return bool(secrets_mod.get(SECRETS_ID, "endpoint") and secrets_mod.get(SECRETS_ID, "admin_token"))


def endpoint() -> str | None:
    return secrets_mod.get(SECRETS_ID, "endpoint")


def _state_path(home: Path) -> Path:
    return home / "remote" / "state.json"


def load_state(home: Path) -> dict[str, Any]:
    try:
        return json.loads(_state_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(home: Path, state: dict[str, Any]) -> None:
    path = _state_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def call(payload: dict[str, Any], *, timeout: float = 20.0) -> dict[str, Any]:
    url = endpoint()
    token = secrets_mod.get(SECRETS_ID, "admin_token")
    if not url or not token:
        raise RelayError("中継がまだ設定されていません（`manor remote gas deploy` を先に）")
    body = json.dumps({**payload, "token": token}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "text/plain;charset=utf-8"})
    raw = ""
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 宛先は設定の固定値
                raw = resp.read().decode("utf-8", errors="replace")
            break
        except urllib.error.HTTPError as exc:
            # GAS は結果の置き場（302 の先）が時々 404 になる（2026-10-09 実測。許可の直後に多発）。
            # どの op もやり直して安全（events は event_id で重複を捨て、他は上書き）なので1回だけ。
            if attempt == 0 and (exc.code == 404 or exc.code >= 500):
                time.sleep(2)
                continue
            raise RelayError(f"中継に届きません: {exc}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise RelayError(f"中継に届きません: {exc}") from exc
    try:
        out = json.loads(raw)
    except ValueError as exc:
        hint = "（初回の権限の許可がまだかもしれません）" if "<html" in raw[:200].lower() else ""
        raise RelayError(f"中継の応答が JSON ではありません{hint}: {raw[:160]!r}") from exc
    if not out.get("ok"):
        raise RelayError(f"中継が断りました: {out.get('error')}")
    return out


# --- 取り込み ------------------------------------------------------------------------------


def pull(conn: sqlite3.Connection, home: Path, *, min_interval: float = 0.0,
         push_directory: bool = True, timeout: float = 20.0) -> dict[str, Any]:
    """中継から取り込む。`min_interval` 秒以内に前回があれば何もしない（画面から呼ぶとき）。"""
    state = load_state(home)
    if min_interval and time.time() - float(state.get("last_pull_ts") or 0) < min_interval:
        return {"skipped": True}
    state["last_pull_ts"] = time.time()
    save_state(home, state)
    since = int(state.get("cursor") or 0)
    total = {"accepted": 0, "duplicates": 0, "sessions": [], "phase_changes": []}
    for _ in range(PULL_MAX_ROUNDS):
        out = call({"op": "pull", "since": since, "limit": 500}, timeout=timeout)
        events = out.get("events") or []
        result = store.ingest(conn, events)
        for key in ("accepted", "duplicates"):
            total[key] += result[key]
        total["sessions"] += [s for s in result["sessions"] if s not in total["sessions"]]
        total["phase_changes"] += result["phase_changes"]
        since = int(out.get("last_seq") or since)
        conn.commit()
        state["cursor"] = since
        save_state(home, state)
        if not out.get("more"):
            break
    total["reflected"] = reflect_tasks(conn, total["phase_changes"])
    conn.commit()
    if push_directory:
        total["directory"] = push_directory_if_changed(conn, home)
    return total


def try_pull(home: Path, *, timeout: float = 5.0) -> str | None:
    """起動時の射影・`manor active` の前に1回だけ取り込む（失敗しても黙って手元の最新で続ける）。

    戻り値は失敗の理由（成功・未設定なら None）。自分で接続を開く（呼ぶ側の接続は読み取り専用のことがある）。
    """
    if not configured():
        return None
    from .. import db as db_mod

    try:
        conn = db_mod.connect(home)
    except Exception as exc:  # noqa: BLE001
        return str(exc)
    try:
        pull(conn, home, min_interval=PULL_MIN_INTERVAL, push_directory=False, timeout=timeout)
        conn.commit()
        return None
    except Exception as exc:  # noqa: BLE001 - 起動を止めない
        conn.rollback()
        return str(exc)
    finally:
        conn.close()


def reflect_tasks(conn: sqlite3.Connection, changes: list[tuple[str, str, str, str]]) -> list[str]:
    """段階が変わったセッションのタスクに、現在地を1文で書く（§7.3。完了は閉じない）。"""
    from .. import task as task_mod

    done = []
    for session_id, task_id, _prev, phase in changes:
        row = conn.execute("SELECT * FROM remote_session WHERE session_id=?", (session_id,)).fetchone()
        t = conn.execute("SELECT status FROM task WHERE id=?", (task_id,)).fetchone()
        if not row or not t or t["status"] in ("done", "withdrawn"):
            continue
        label = store.PHASE_LABELS_JA.get(phase, phase)
        pct = "" if row["progress"] is None else f"（{row['progress']}%）"
        nxt = f"。主人の次: {row['human_next']}" if row["human_next"] else ""
        if phase == "done":
            text = f"[{row['machine']}] {row['title']}：完了と報告（執事の検分待ち）"
        else:
            text = f"[{row['machine']}] {row['title']}：{label}{pct}{nxt}"
        try:
            task_mod.set(conn, task_id, now=text)
            done.append(task_id)
        except Exception:  # noqa: BLE001 - 1件の失敗で取り込み全体を止めない
            continue
    return done


def push_directory_if_changed(conn: sqlite3.Connection, home: Path) -> str:
    """紐づけ表が変わっていれば中継へ書き直す。禁止語に当たる行は送らない（fail-closed）。"""
    from .. import slack as slack_mod

    entries = store.build_directory(conn)
    safe: dict[str, Any] = {}
    skipped = 0
    for key, entry in entries.items():
        text = json.dumps({key: entry}, ensure_ascii=False)
        scan = slack_mod.scan_for_leak_terms(text)
        if scan.get("ok"):
            safe[key] = entry
        else:
            skipped += 1
    digest = hashlib.sha256(json.dumps(safe, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    state = load_state(home)
    if state.get("directory_digest") == digest:
        return "unchanged"
    call({"op": "set_directory", "entries": safe})
    state["directory_digest"] = digest
    save_state(home, state)
    return f"pushed {len(safe)}" + (f"（禁止語で {skipped} 件を外しました）" if skipped else "")


# --- 鍵 ------------------------------------------------------------------------------------


def _new_token() -> str:
    return pysecrets.token_urlsafe(32)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def machine_add(conn: sqlite3.Connection, name: str) -> str:
    """PCの鍵を作り、中継にハッシュを登録する。平文はこの戻り値だけ（manor は持たない）。"""
    token = _new_token()
    call({"op": "add_machine", "name": name, "token_sha256": _sha256(token)})
    conn.execute(
        "INSERT INTO remote_machine(name, created_at, revoked_at) VALUES (?,?,NULL)"
        " ON CONFLICT(name) DO UPDATE SET created_at=excluded.created_at, revoked_at=NULL",
        (name, util.now()),
    )
    return token


def machine_revoke(conn: sqlite3.Connection, name: str) -> int:
    out = call({"op": "revoke_machine", "name": name})
    conn.execute("UPDATE remote_machine SET revoked_at=? WHERE name=? AND revoked_at IS NULL", (util.now(), name))
    return int(out.get("revoked") or 0)


def install_command(name: str, token: str) -> dict[str, str]:
    """他のPCで流す導入の手順（PowerShell と sh）。道具は中継から受け取る。"""
    url = endpoint() or ""
    body = json.dumps({"op": "client", "token": token})
    ps = (
        "$d=\"$HOME/.manor-report\"; New-Item -ItemType Directory -Force $d | Out-Null; "
        f"Invoke-WebRequest -UseBasicParsing -Method Post -ContentType 'text/plain' -Body '{body}' "
        f"\"{url}\" -OutFile \"$d/manor_report.py\"; "
        "$py = if (Get-Command py -ErrorAction SilentlyContinue) { 'py' } "
        "elseif (Get-Command python -ErrorAction SilentlyContinue) { 'python' } else { $null }; "
        f"if ($py) {{ & $py \"$d/manor_report.py\" install --endpoint \"{url}\" --token \"{token}\" --machine \"{name}\" }} "
        f"else {{ uv run --python 3.12 --no-project \"$d/manor_report.py\" install --endpoint \"{url}\" --token \"{token}\" --machine \"{name}\" }}"
    )
    sh = (
        "mkdir -p ~/.manor-report && "
        f"curl -fsSL -X POST -H 'Content-Type: text/plain' -d '{body}' \"{url}\" -o ~/.manor-report/manor_report.py && "
        f"python3 ~/.manor-report/manor_report.py install --endpoint \"{url}\" --token \"{token}\" --machine \"{name}\""
    )
    return {"powershell": ps, "sh": sh}


# --- GAS の配置 ----------------------------------------------------------------------------


def gas_dir(home: Path) -> Path:
    return home / "remote" / "gas"


def _clasp() -> str:
    for name in ("clasp.cmd", "clasp"):
        found = shutil.which(name)
        if found:
            return found
    raise RelayError("clasp が見つかりません（`npm i -g @google/clasp`）")


def _run_clasp(home: Path, *args: str) -> str:
    out = subprocess.run([_clasp(), *args], cwd=gas_dir(home), capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=180)
    if out.returncode != 0:
        raise RelayError(f"clasp {' '.join(args)} に失敗: {out.stderr.strip() or out.stdout.strip()}")
    return out.stdout


def write_gas_files(home: Path, admin_token: str) -> None:
    folder = gas_dir(home)
    if not (folder / ".clasp.json").is_file():
        raise RelayError(f"{folder} に .clasp.json がありません（`clasp create-script --type sheets` を先に）")
    for name in ("Code.js", "appsscript.json"):
        shutil.copyfile(GAS_SRC / name, folder / name)
    client = CLIENT_SRC.read_text(encoding="utf-8")
    config = (
        "// manor が生成（`manor remote gas deploy`）。直接直さない。git に入らない（home/ の下）。\n"
        f"var ADMIN_SHA256 = {json.dumps(_sha256(admin_token))};\n"
        f"var CLIENT_SOURCE = {json.dumps(client, ensure_ascii=False)};\n"
    )
    (folder / "Config.js").write_text(config, encoding="utf-8")


def gas_deploy(home: Path) -> dict[str, Any]:
    """Code.js・Config.js を置いて push し、公開（初回は作成、以後は同じ URL を更新）。"""
    admin = secrets_mod.get(SECRETS_ID, "admin_token")
    if not admin:
        admin = _new_token()
        secrets_mod.set(SECRETS_ID, "admin_token", admin)
    write_gas_files(home, admin)
    _run_clasp(home, "push", "--force")
    deployment_id = secrets_mod.get(SECRETS_ID, "deployment_id")
    if deployment_id:
        _run_clasp(home, "update-deployment", deployment_id, "--description", f"manor {util.now()}")
        created = False
    else:
        out = _run_clasp(home, "create-deployment", "--description", f"manor {util.now()}")
        deployment_id = _parse_deployment_id(out)
        secrets_mod.set(SECRETS_ID, "deployment_id", deployment_id)
        created = True
    url = f"https://script.google.com/macros/s/{deployment_id}/exec"
    secrets_mod.set(SECRETS_ID, "endpoint", url)
    return {"endpoint": url, "deployment_id": deployment_id, "created": created}


def _parse_deployment_id(out: str) -> str:
    import re

    m = re.search(r"(AKfy[\w-]{20,})", out)
    if not m:
        raise RelayError(f"公開の番号が読めません: {out.strip()}")
    return m.group(1)
