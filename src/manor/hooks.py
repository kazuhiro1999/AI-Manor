"""Claude Code の hooks（ADR-001 §7）。`python -m manor.hooks <event>` で呼ぶ。

**絶対に例外で落ちない。** stdin が空でも、JSON が壊れていても、DB が無くても、
必ず (空文字列でも) 標準出力と終了コード0を返す。無人セッション
（`permission_mode` が `dontAsk`/`bypassPermissions`）では文脈を注入する
hook（session-start / stop / pre-compact）は黙る。`MANOR_HOOKS=off` は全部を黙らせる。

hooks は DB を書き換えない。読むだけ。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path, PurePosixPath

from . import db, profile as profile_mod, render, util

DISABLE_ENV = "MANOR_HOOKS"
UNATTENDED_MODES: frozenset[str] = frozenset({"dontAsk", "bypassPermissions"})

ALREADY_INJECTED = (
    "【起動時の射影】これは `manor active` の出力です。もう一度回す必要はありません。"
)

PROTECTED_DENY_MESSAGE = "射影です。`manor task …` / `manor project …` で書いてください。"


def _hooks_disabled() -> bool:
    return os.environ.get(DISABLE_ENV, "").strip().lower() == "off"


def _unattended(payload: dict[str, object]) -> bool:
    return str(payload.get("permission_mode", "")) in UNATTENDED_MODES


def read_payload(stream: object | None = None) -> dict[str, object]:
    """stdin の JSON を読む。読めなくても・壊れていても落ちない（空の辞書を返す）。"""
    src = stream if stream is not None else sys.stdin
    try:
        raw = src.read()  # type: ignore[union-attr]
    except Exception:
        return {}
    if not raw or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _emit(event: str, **fields: object) -> str:
    body = {"hookEventName": event, **{k: v for k, v in fields.items() if v}}
    return json.dumps({"hookSpecificOutput": body}, ensure_ascii=False)


# --- PreToolUse: 射影・DB を守る ------------------------------------------------


def _normalize_parts(raw_path: str) -> list[str]:
    """`\\` / `/`・絶対/相対のどの表記でも同じ部品列にする。"""
    norm = raw_path.replace("\\", "/")
    return [p for p in norm.split("/") if p not in ("", ".")]


def _resolved_under_home(raw_path: str, home: Path, cwd: str | None) -> bool:
    """`MANOR_HOME` を実際に解決して、射影の実体を指しているかを見る。

    名前だけの判定（下の `is_protected_path` の前半）は `MANOR_HOME` を `home` 以外の
    名前のフォルダへ向けたときに素通りする（QA 指摘 2026-09-02）。ADR-001 §2
    「パスは MANOR_HOME から導く」に従い、実体で比べる。解決できなければ False。
    """
    try:
        p = Path(raw_path.replace("\\", "/"))
        if not p.is_absolute():
            p = (Path(cwd) / p) if cwd else p
        p = p.resolve()
        home_r = home.resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    target = p.as_posix().lower()
    proj = (home_r / "projections").as_posix().lower().rstrip("/") + "/"
    state = (home_r / "STATE.md").as_posix().lower()
    return target.startswith(proj) or target == state


def is_protected_path(raw_path: str, *, home: Path | None = None, cwd: str | None = None) -> bool:
    """`<MANOR_HOME>/projections/**` / `<MANOR_HOME>/STATE.md` / `*.db` のどれかに当たるか。

    2段で見る: (1) 名前（`home/projections/...` の形。相対パス・cwd 不明でも効く）、
    (2) 実体（`MANOR_HOME` を解決して比べる。フォルダ名が `home` でなくても効く）。
    """
    if not raw_path:
        return False
    parts = [p.lower() for p in _normalize_parts(raw_path)]
    if parts and parts[-1].endswith(".db"):
        return True
    for i in range(len(parts) - 1):
        if parts[i] == "home" and parts[i + 1] == "projections":
            return True
    if len(parts) >= 2 and parts[-2] == "home" and parts[-1] == "state.md":
        return True
    return _resolved_under_home(raw_path, home or util.manor_home(), cwd)


def pre_tool_use(payload: dict[str, object]) -> str:
    tool_name = str(payload.get("tool_name", ""))
    if tool_name not in ("Edit", "Write", "MultiEdit"):
        return ""
    tool_input = payload.get("tool_input")
    file_path = ""
    if isinstance(tool_input, dict):
        file_path = str(tool_input.get("file_path", "") or "")
    cwd = payload.get("cwd")
    if not is_protected_path(file_path, cwd=str(cwd) if cwd else None):
        return ""
    return _emit(
        "PreToolUse",
        permissionDecision="deny",
        permissionDecisionReason=PROTECTED_DENY_MESSAGE,
    )


# --- SessionStart / Stop / PreCompact: DB を読むだけ ----------------------------


def _open_readable(home: Path) -> sqlite3.Connection | None:
    """DB が無い・開けないときは None（黙って諦める）。"""
    if not (home / "manor.db").is_file():
        return None
    try:
        return db.connect(home)
    except Exception:
        return None


def session_start(payload: dict[str, object], home: Path) -> str:
    if _unattended(payload):
        return ""
    conn = _open_readable(home)
    if conn is None:
        return ""
    try:
        text = render.active_text(conn)
        # ADR-007 D3: プロフィールの要約を1行、本文の前に置く（未設定なら何も足さない）。
        try:
            summary = profile_mod.summary_line(conn)
        except Exception:
            summary = ""
    except Exception:
        return ""
    finally:
        conn.close()
    prefix = f"{summary}\n\n" if summary else ""
    return f"{ALREADY_INJECTED}\n\n{prefix}{text}"


def _stop_report(home: Path) -> str:
    conn = _open_readable(home)
    if conn is None:
        return ""
    try:
        notes: list[str] = []
        rows = conn.execute(
            "SELECT id FROM task WHERE status = 'doing' AND owner = 'butler'"
            " ORDER BY CAST(substr(id, 2) AS INTEGER)"
        ).fetchall()
        if rows:
            ids = " / ".join(str(r["id"]) for r in rows)
            notes.append(f"`doing` のままです（owner=butler）: {ids}")
        blocked = conn.execute(
            "SELECT id FROM v_blocked_ready ORDER BY CAST(substr(id, 2) AS INTEGER)"
        ).fetchall()
        if blocked:
            ids = " / ".join(str(r["id"]) for r in blocked)
            notes.append(f"止まる理由が消えています: {ids}")
        if not notes:
            return ""
        return _emit("Stop", systemMessage="⚠ " + " ／ ".join(notes))
    except Exception:
        return ""
    finally:
        conn.close()


def stop(payload: dict[str, object], home: Path) -> str:
    """既存の警告（`doing` 放置・ブロック解除見落とし）に加え、声かけ（notify）を鳴らす。

    無人（`permission_mode` が unattended）なら両方とも黙る。`MANOR_HOOKS=off` は声かけだけを
    黙らせる（既存の警告は `main()` の入口で既に止めているので、ここでは声かけの分だけ見ればよい）。
    **声かけの結果は `systemMessage` に足さない**（v1 と同じで黙って鳴らすだけ。画面のノイズに
    しない）。`notify.run` が何を投げても、この hook は落ちない。
    """
    if _unattended(payload):
        return ""
    out = _stop_report(home)
    if not _hooks_disabled():
        try:
            from . import notify as notify_mod

            notify_mod.run(home)
        except Exception:
            pass
    return out


def pre_compact(payload: dict[str, object], home: Path) -> str:
    if _unattended(payload):
        return ""
    conn = _open_readable(home)
    text = "圧縮後は `manor active` と `manor ctx <進行中の id>` を読み直してください。"
    if conn is not None:
        try:
            rows = conn.execute(
                "SELECT id FROM task WHERE status = 'doing'"
                " ORDER BY CAST(substr(id, 2) AS INTEGER)"
            ).fetchall()
            if rows:
                ids = " / ".join(str(r["id"]) for r in rows)
                text += f" いま `doing`: {ids}。"
        except Exception:
            pass
        finally:
            conn.close()
    return _emit("PreCompact", additionalContext=text)


# --- 入口 ------------------------------------------------------------------------


def run(event: str, payload: dict[str, object], home: Path) -> str:
    """1つの hook を走らせて標準出力に書く文字列を返す。**例外を投げない。**"""
    try:
        if event == "session-start":
            return session_start(payload, home)
        if event == "pre-tool-use":
            return pre_tool_use(payload)
        if event == "stop":
            return stop(payload, home)
        if event == "pre-compact":
            return pre_compact(payload, home)
    except Exception:
        return ""
    return ""


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return 0
    if _hooks_disabled():
        return 0
    payload = read_payload()
    try:
        home = util.manor_home()
    except Exception:
        return 0
    out = run(args[0], payload, home)
    if out:
        print(out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
