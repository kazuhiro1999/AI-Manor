"""`manor calendar` — ICS カレンダーの取り込み（ADR-012 §2「5d カレンダー拡張」）。

**読むだけ（HTTP GET のみ）。書き戻さない**（D2）——外部送信ではないので human_gate の
対象にならない。解析（折り返し・エスケープ・繰り返しの展開）は `src/manor/ics.py`
（純粋関数）に任せ、このモジュールは HTTP 取得・DB への冪等な突き合わせ・CLI だけを持つ
（「壊れたときに切り分けられる」よう、意図的に分けてある。ADR-012 D4）。

## 予定は `secretary_event` にそのまま入れる（D1）

新しい表は作らない。`source='ics'`・`external_id`（`UID` ＋ 開始時刻）で ICS 由来と
分かるようにする。

## 同期は冪等（D6）

- 突き合わせの鍵は `external_id`
- 既にあるものは（内容が変わっていれば）更新、feed から消えたものは削除
- **`source='manual'` の行には絶対に触らない**——`WHERE source = 'ics'` を通さない
  SELECT/UPDATE/DELETE をこのモジュールに書かないことで、構造的に守る（試験で確かめる）

## URL は秘密（ADR-012 D3）

`secrets.get("calendar", "url")` から読む。**`config.toml` には置かない**——URL 自体が
鍵なので、拡張の `secret_fields` へ回す（`extensions/calendar.py` がその配線）。
"""

from __future__ import annotations

import argparse
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from . import branding, db, i18n, ics as ics_mod, util
from .errors import ManorError

#: 取り込みの HTTP タイムアウト（秒）。slack.py の API_TIMEOUT / notion.py と同じ流儀。
FETCH_TIMEOUT = 15.0

#: **繰り返しの展開にだけ**かける窓（ADR-012 D5 改訂・2026-09-05）。単発の予定は
#: 今日以降なら上限なく取り込む（`ics.parse_ics` 参照）——4か月先の旅行のような
#: 実在する予定を manor が持たない、という取り込み漏れを避けるため。際限なく増える
#: 原因は終わりの無い繰り返しだけなので、そこにだけ上限を置けば足りる。
WINDOW_DAYS = 90

#: ICS 由来の予定であることの印（`secretary_event.source`）。
SOURCE = "ics"


# --- 秘密（URL）。secrets.py が読めなくても落ちないよう遅延 import（slack.py と同じ流儀） -------


def calendar_url() -> str | None:
    """`secrets.get("calendar", "url")` の薄いラッパー。読み出しの失敗で全体を落とさない。"""
    try:
        from . import secrets as secrets_mod  # noqa: PLC0415

        value = secrets_mod.get("calendar", "url")
    except Exception:  # noqa: BLE001 - 秘密の読み出しの失敗で同期全体を落とさない
        return None
    return value if isinstance(value, str) and value.strip() else None


# --- HTTP 取得（urllib のみ。例外は投げない） -----------------------------------------------


def fetch_ics(url: str, *, timeout: float = FETCH_TIMEOUT) -> dict[str, object]:
    """ICS を1回取得する。**例外は投げない**（`notion._notion_request` と同じ約束）。
    成功時は `{"ok": True, "text": str}`。失敗時は `{"ok": False, "reason": str}`。
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": f"{branding.CLI_NAME}-calendar/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - URL は主人が設定したもの
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        return {"ok": False, "reason": f"HTTP エラー: {exc.code}"}
    except urllib.error.URLError as exc:
        return {"ok": False, "reason": f"接続できませんでした: {exc.reason}"}
    except TimeoutError:
        return {"ok": False, "reason": "タイムアウトしました"}
    except Exception as exc:  # noqa: BLE001 - fetch は例外を外へ出さない
        return {"ok": False, "reason": f"取得できませんでした: {exc}"}

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    if "BEGIN:VCALENDAR" not in text:
        return {"ok": False, "reason": "ICS 形式ではないようです（BEGIN:VCALENDAR が見つかりません）"}
    return {"ok": True, "text": text}


def check_connection(home: Path | str | None = None) -> dict[str, object]:
    """拡張の `check()` の実体（ADR-009 D3）。URL の疎通と ICS として読めるかだけ確かめる。
    **例外は投げない**。`home` は今のところ使わない（URL はホーム非依存。voicevox/notion の
    `check()` と引数の形を揃えるため受け取るだけ）。
    """
    try:
        url = calendar_url()
        if not url:
            return {"ok": False, "reason": "URL が未設定です（manor ext set calendar --secret url）"}
        fetched = fetch_ics(url)
        if not fetched.get("ok"):
            return {"ok": False, "reason": str(fetched.get("reason") or "")}
        text = str(fetched.get("text") or "")
        if "BEGIN:VEVENT" not in text:
            return {"ok": True, "reason": "接続できました（予定は0件です）"}
        return {"ok": True, "reason": ""}
    except Exception as exc:  # noqa: BLE001 - check() は例外を外へ出さない（二重の砦）
        return {"ok": False, "reason": f"確認できませんでした: {exc}"}


# --- 同期（D6。冪等） ------------------------------------------------------------------------


def _require_secretary(conn) -> None:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'secretary_event'"
    ).fetchone()
    if row is None:
        raise ManorError(
            "秘書（secretary）が導入されていません",
            code=2,
            key="error.calendar.secretary_not_installed",
        )


def apply_events(conn, events: list[dict[str, object]]) -> dict[str, object]:
    """解析済みの予定（`ics.parse_ics` の戻り値）を `secretary_event` へ冪等に反映する。

    **`source = 'ics'` の行だけを対象にする**——SELECT/UPDATE/DELETE のすべてに
    `WHERE source = 'ics'` を通し、`source = 'manual'`（主人が手で入れた予定）には
    構造的に触れないようにする（ADR-012 D6「絶対に触らない」）。
    """
    _require_secretary(conn)
    now = util.now()
    existing = {
        str(r["external_id"]): dict(r)
        for r in conn.execute(
            "SELECT * FROM secretary_event WHERE source = 'ics' AND external_id IS NOT NULL"
        ).fetchall()
    }

    seen: set[str] = set()
    added = 0
    updated = 0
    for ev in events:
        ext_id = str(ev["external_id"])
        seen.add(ext_id)
        row = existing.get(ext_id)
        if row is None:
            conn.execute(
                'INSERT INTO secretary_event (start, "end", title, place, note, source, external_id, created_at)'
                " VALUES (?, ?, ?, ?, ?, 'ics', ?, ?)",
                (ev["start"], ev.get("end"), ev["title"], ev.get("place") or "", ev.get("note") or "", ext_id, now),
            )
            added += 1
            continue
        changed = (
            str(row.get("start")) != str(ev["start"])
            or (row.get("end") or None) != (ev.get("end") or None)
            or str(row.get("title")) != str(ev["title"])
            or str(row.get("place") or "") != str(ev.get("place") or "")
            or str(row.get("note") or "") != str(ev.get("note") or "")
        )
        if changed:
            conn.execute(
                'UPDATE secretary_event SET start = ?, "end" = ?, title = ?, place = ?, note = ?'
                " WHERE id = ? AND source = 'ics'",
                (ev["start"], ev.get("end"), ev["title"], ev.get("place") or "", ev.get("note") or "", row["id"]),
            )
            updated += 1

    removed = 0
    for ext_id, row in existing.items():
        if ext_id not in seen:
            conn.execute("DELETE FROM secretary_event WHERE id = ? AND source = 'ics'", (row["id"],))
            removed += 1

    return {"added": added, "updated": updated, "removed": removed, "total": len(events)}


def sync(home: Path | str) -> dict[str, object]:
    """`manor calendar sync` の実体。URL 取得 → 解析 → 冪等な反映まで一気に行う。
    取得や解析が失敗しても例外は投げない（`{"ok": False, "reason": ...}` を返す）。
    """
    home = Path(home)
    url = calendar_url()
    if not url:
        return {"ok": False, "reason": "URL が未設定です（manor ext set calendar --secret url）"}

    fetched = fetch_ics(url)
    if not fetched.get("ok"):
        return {"ok": False, "reason": str(fetched.get("reason") or "")}

    today = date.fromisoformat(util.today())
    events = ics_mod.parse_ics(str(fetched["text"]), today=today, window_days=WINDOW_DAYS)

    conn = db.connect(home)
    try:
        try:
            result = apply_events(conn, events)
        except ManorError as exc:
            conn.rollback()
            return {"ok": False, "reason": exc.message_ja}
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, **result}


def list_events(home: Path | str, *, days: int = 7) -> list[dict[str, object]]:
    """`manor calendar list` の実体。`secretary_event` を素直に読むだけ（manual/ics 両方）。"""
    home = Path(home)
    conn = db.connect(home)
    try:
        _require_secretary(conn)
        today = util.today()
        end = (date.fromisoformat(today) + timedelta(days=max(days, 0))).isoformat()
        rows = [
            dict(r)
            for r in conn.execute(
                'SELECT id, start, "end", title, place, note, source FROM secretary_event'
                " WHERE substr(start, 1, 10) BETWEEN ? AND ? ORDER BY start",
                (today, end),
            ).fetchall()
        ]
    finally:
        conn.close()
    return rows


# --- CLI（`manor calendar sync|list`） ------------------------------------------------------


def _print_json(obj: object) -> None:
    import json

    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _cmd_sync(args: argparse.Namespace) -> int:
    home = util.manor_home()
    result = sync(home)
    if args.json:
        _print_json(result)
        return 0 if result.get("ok") else 1
    if result.get("ok"):
        print(i18n.t("calendar.sync.done", added=result["added"], updated=result["updated"], removed=result["removed"]))
        return 0
    # `reason` は `sync()` が返す共有の診断文字列（web の拡張ステータス表示とも共有するため
    # 訳さない。calendar.py モジュール docstring 参照）——包む文だけ訳す。
    print(i18n.t("calendar.sync.failed", reason=result.get("reason", "")))
    return 1


def _cmd_list(args: argparse.Namespace) -> int:
    home = util.manor_home()
    try:
        rows = list_events(home, days=args.days)
    except ManorError as exc:
        print(exc.localized_message())
        return exc.code
    if args.json:
        _print_json(rows)
        return 0
    if not rows:
        print(i18n.t("common.none"))
        return 0
    for r in rows:
        place = f" @{r['place']}" if r.get("place") else ""
        print(i18n.t("calendar.list.line", start=r["start"], title=r["title"], place=place, source=r["source"], id=r["id"]))
    return 0


def register(subparsers: "argparse._SubParsersAction") -> None:
    """`manor calendar sync|list` を足す（ADR-012 5d。`manor slack`/`manor notion` と
    同じ形——`needs_db=False` で自分の関数の中で接続を開閉する）。
    """
    p = subparsers.add_parser("calendar", help=i18n.t("cli.calendar.help"))
    sub = p.add_subparsers(dest="verb")

    s = sub.add_parser("sync", help=i18n.t("cli.calendar.sync.help"))
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=_cmd_sync, needs_db=False)

    s = sub.add_parser("list", help=i18n.t("cli.calendar.list.help"))
    s.add_argument("--days", type=int, default=7)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=_cmd_list, needs_db=False)
