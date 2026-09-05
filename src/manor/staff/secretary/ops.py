"""secretary（秘書）の純粋関数（ADR-002 §6・§7）。

道具（この module）は判断しない。日付式の解決・並べ替え・検証・突き合わせだけをする。
「予定同士がぶつかったらどちらを優先するか」「inbox をどこへ回すか」は
担当（LLM。`.claude/agents/secretary.md`）が決める。

DB（`sqlite3.Connection`）を触る関数はここには置かない（`cli.py` の仕事）。
ここにあるのは合成データ・素の辞書・リスト・`Path` だけで試験できる関数に限る。

`resolve_date` は secretary だけでなく**全員が使う**契約（ADR-002 §2「日付の解決は
道具がやる。secretary の resolve_date を全員が使う」）。シグネチャを変えるときは
ADR-002 を先に直すこと。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

from manor import i18n
from manor.errors import ManorError

# --- resolve_date（ADR-002 §6。読めなければ ManorError。LLM に計算させない） ----------------

_WEEKDAY_JA: dict[str, int] = {"月": 0, "火": 1, "水": 2, "木": 3, "金": 4, "土": 5, "日": 6}
_WEEKDAY_EN: dict[str, int] = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PLUS_N_RE = re.compile(r"^\+(\d+)$")
_NEXT_WEEK_RE = re.compile(r"^来週の(月|火|水|木|金|土|日)$")
_MD_RE = re.compile(r"^(\d{1,2})/(\d{1,2})$")


def resolve_date(expr: str, today: date) -> date:
    """日付式を絶対日付へ解く（ADR-002 §6）。読めなければ `ManorError`。

    受ける式: `YYYY-MM-DD` ／ 今日|明日|明後日|today|tomorrow ／ `+N`（N日後） ／
    月|火|水|木|金|土|日|mon..sun（次のその曜日。**今日ならば今日**） ／
    来週の火（来週。今日の曜日と一致していても次の週へ進める） ／
    `M/D`（今年。過ぎていれば来年。今年その日が存在しない＝閏日なら来年で試す）。
    """
    s = expr.strip()
    if not s:
        raise ManorError("日付式が空です", code=2, key="error.sec.date_expr_empty")

    if _ISO_DATE_RE.match(s):
        try:
            return date.fromisoformat(s)
        except ValueError as exc:
            raise ManorError(
                f"日付式を解釈できません: {expr!r}",
                code=2,
                key="error.sec.date_expr_unreadable",
                params={"expr": repr(expr)},
            ) from exc

    lowered = s.lower()
    if s == "今日" or lowered == "today":
        return today
    if s == "明日" or lowered == "tomorrow":
        return today + timedelta(days=1)
    if s == "明後日":
        return today + timedelta(days=2)

    m = _PLUS_N_RE.match(s)
    if m:
        return today + timedelta(days=int(m.group(1)))

    m = _NEXT_WEEK_RE.match(s)
    if m:
        target = _WEEKDAY_JA[m.group(1)]
        this_monday = today - timedelta(days=today.weekday())
        next_monday = this_monday + timedelta(days=7)
        return next_monday + timedelta(days=target)

    if s in _WEEKDAY_JA:
        return _next_weekday(today, _WEEKDAY_JA[s])
    if lowered in _WEEKDAY_EN:
        return _next_weekday(today, _WEEKDAY_EN[lowered])

    m = _MD_RE.match(s)
    if m:
        return _resolve_month_day(int(m.group(1)), int(m.group(2)), today)

    raise ManorError(
        f"日付式を解釈できません: {expr!r}",
        code=2,
        key="error.sec.date_expr_unreadable",
        params={"expr": repr(expr)},
    )


def _next_weekday(today: date, target_weekday: int) -> date:
    """次のその曜日を返す。**今日がその曜日なら今日**（ADR-002 §6）。"""
    delta = (target_weekday - today.weekday()) % 7
    return today + timedelta(days=delta)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _resolve_month_day(month: int, day: int, today: date) -> date:
    """`M/D` を解く。今年、過ぎていれば来年（ADR-002 §6）。

    閏日（2/29）が対象年に存在しない場合は、その年では解釈できないので `ManorError`
    にする（来年へ丸めたり次の閏年まで探したりしない。読めない式を LLM に憶測させない
    という方針をここでも守る。曖昧だった点として報告に書く）。
    """
    candidate = _safe_date(today.year, month, day)
    if candidate is None:
        raise ManorError(
            f"日付式を解釈できません: {month}/{day}（{today.year}年に存在しません）",
            code=2,
            key="error.sec.date_md_not_exist",
            params={"month": month, "day": day, "year": today.year},
        )
    if candidate >= today:
        return candidate
    candidate_next = _safe_date(today.year + 1, month, day)
    if candidate_next is None:
        raise ManorError(
            f"日付式を解釈できません: {month}/{day}（{today.year + 1}年に存在しません）",
            code=2,
            key="error.sec.date_md_not_exist",
            params={"month": month, "day": day, "year": today.year + 1},
        )
    return candidate_next


# --- 検証（CLI が受ける形式はここで縛る） ---------------------------------------------------

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?)?$")


def validate_time(value: str, *, field: str = "時刻") -> str:
    """`HH:MM` だけを受ける（`secretary_reminder.at_time`）。"""
    v = value.strip()
    if not _TIME_RE.match(v):
        raise ManorError(
            f"{field}は HH:MM 形式で指定してください: {value!r}",
            code=2,
            key="error.sec.time_format",
            params={"field": field, "value": repr(value)},
        )
    return v


def validate_datetime(value: str, *, field: str = "日時") -> str:
    """`YYYY-MM-DD` か `YYYY-MM-DDTHH:MM[:SS]` だけを受ける（`secretary_event.start/end`）。

    相対日付（「明日」等）はここでは扱わない。CLI が要求するのは絶対日時であり、
    日付だけの相対表現を解きたいときは `resolve_date` を通してから渡す。
    """
    v = value.strip()
    if not _DATETIME_RE.match(v):
        raise ManorError(
            f"{field}の形式が不正です（YYYY-MM-DD か YYYY-MM-DDTHH:MM）: {value!r}",
            code=2,
            key="error.sec.datetime_format",
            params={"field": field, "value": repr(value)},
        )
    return v


# --- inbox（ファイルの発見だけ。中身は読まない。DB 登録は cli.py） --------------------------


def scan_inbox_files(inbox_dir: Path, existing_refs: set[str]) -> list[dict[str, object]]:
    """`inbox_dir` 直下の未登録ファイルを見つける。**中身は読まない**（ADR-002 §6）。

    ファイル名と更新時刻だけを返す。`README.md` と `existing_refs` に含まれるものは除く。
    ディレクトリが無ければ空リスト（`manor init` 前でも例外にしない）。
    """
    if not inbox_dir.is_dir():
        return []
    found: list[dict[str, object]] = []
    for path in sorted(inbox_dir.iterdir()):
        if not path.is_file():
            continue
        if path.name == "README.md":
            continue
        if path.name in existing_refs:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        received_at = datetime.fromtimestamp(mtime).isoformat(timespec="seconds")
        found.append({"ref": path.name, "received_at": received_at})
    return found


# --- agenda（予定＋控え＋core の milestone＋core の task.due を1本に。数えるだけ） -----------

_KIND_ORDER: dict[str, int] = {"reminder": 0, "event": 1, "milestone": 2, "task": 3}


def merge_agenda(
    *,
    events: list[dict[str, object]],
    reminders: list[dict[str, object]],
    milestones: list[dict[str, object]],
    tasks: list[dict[str, object]],
    today: date,
) -> list[dict[str, object]]:
    """4種の行（呼び手が SQL で期間内に絞り込んだもの）を日付順の1本にまとめる。

    並べ替えと「超過」判定だけがここの仕事（ADR-002 §6）。
    control_reminders は呼び手が「未完了で on_date <= 期間の終わり」まで（**下限なし**）で
    渡す約束。それより前（今日より前）の on_date は「超過」として先頭に出す
    （そうしないと「期限切れの未完了を先頭に出す」が実現できない——期間の下限を
    今日にすると、そもそも過去の on_date が集合に入らない）。event/milestone/task は
    呼び手が既に [today, today+days] に絞ってから渡す想定（overdue は常に False）。
    """
    today_s = today.isoformat()
    items: list[dict[str, object]] = []

    for e in events:
        detail = str(e.get("place") or "")
        note = str(e.get("note") or "")
        # ICS 取り込み（`ics.py`/`calendar.py`）が黙って落とさず note に残した印を、
        # agenda の一覧からも見える短い警告に変える（ADR-012 D5・D4改訂）。note 本文
        # （元の RRULE・解決できなかった TZID）まではここに出さない——agenda は短い
        # 一覧なので、詳しくは `manor sec event list`/`manor calendar list` へ。
        warnings: list[str] = []
        if "[未対応の繰り返し]" in note:
            warnings.append(i18n.t("sec.agenda.warning.recurring_unsupported"))
        if "[TZID未解決]" in note:
            warnings.append(i18n.t("sec.agenda.warning.tzid_unresolved"))
        if warnings:
            detail = f"{detail} {' '.join(warnings)}" if detail else " ".join(warnings)
        items.append(
            {
                "date": str(e["start"])[:10],
                "kind": "event",
                "id": e["id"],
                "title": e["title"],
                "detail": detail,
                "overdue": False,
            }
        )

    for r in reminders:
        d = str(r["on_date"])
        items.append(
            {
                "date": d,
                "kind": "reminder",
                "id": r["id"],
                "title": r["text"],
                "detail": r.get("at_time") or "",
                "overdue": d < today_s,
            }
        )

    for m in milestones:
        items.append(
            {
                "date": str(m["date"]),
                "kind": "milestone",
                "id": m["id"],
                "title": m["title"],
                "detail": "",
                "overdue": False,
            }
        )

    for t in tasks:
        items.append(
            {
                "date": str(t["due"])[:10],
                "kind": "task",
                "id": t["id"],
                "title": t["title"],
                "detail": "",
                "overdue": False,
            }
        )

    items.sort(
        key=lambda it: (
            0 if it["overdue"] else 1,
            str(it["date"]),
            _KIND_ORDER.get(str(it["kind"]), 9),
            str(it["id"]),
        )
    )
    return items
