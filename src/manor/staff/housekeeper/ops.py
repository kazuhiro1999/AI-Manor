"""家政婦の純粋関数（ADR-002 §4）。DB に触らない。CLI（`cli.py`）と分けて試験しやすくする。

道具（ここ）は判断しない。並べ替え・突き合わせ・集計・期日計算まで。
「何を勧めるか」は担当（LLM。`.claude/agents/housekeeper.md`）が決める。

`rule` の文法（`parse_rule` / `next_dates` が解く。LLM に計算させない）:

- ``weekly:mon,thu``                 毎週、指定した曜日（複数可）
- ``monthly:2nd-wed,4th-wed``        毎月、第何週の何曜日（複数可。1st〜5th）
- ``biweekly:tue@2026-09-01``        起点日から2週間おき。起点日の曜日と指定曜日は一致していること
- ``date:2026-09-15``                単発の日付

読めない文法は `ManorError` で拒否する（登録時に呼ぶのは CLI 側の役目）。
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from manor import i18n
from manor.errors import ManorError

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

WEEKDAY_TOKENS: dict[str, int] = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}
_WEEKDAY_NAMES: dict[int, str] = {v: k for k, v in WEEKDAY_TOKENS.items()}

_MONTHLY_RE = re.compile(r"^([1-5])(st|nd|rd|th)-(mon|tue|wed|thu|fri|sat|sun)$")


def parse_date(value: str) -> date:
    """`YYYY-MM-DD` だけを受ける。形式違い・実在しない日付は `ManorError`。"""
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise ManorError(
            f"日付は YYYY-MM-DD 形式で指定してください: {value!r}",
            key="error.house.date_format",
            params={"value": repr(value)},
        )
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ManorError(
            f"日付が不正です（実在しない日付）: {value!r}",
            key="error.house.date_invalid",
            params={"value": repr(value)},
        ) from exc


def add_days(day: str, n: int) -> str:
    """`day`（YYYY-MM-DD）から `n` 日後の日付を返す。期日計算は道具がやる。"""
    return (parse_date(day) + timedelta(days=n)).isoformat()


def parse_rule(rule: str) -> dict[str, object]:
    """`rule` 文字列を構造化して返す。読めなければ `ManorError`。

    戻り値は `next_dates` が使う内部表現（kind ごとに形が違う）。
    """
    if not isinstance(rule, str) or ":" not in rule:
        raise ManorError(
            f"rule の文法が不正です（kind: が要ります）: {rule!r}",
            key="error.house.rule_missing_kind",
            params={"rule": repr(rule)},
        )
    kind, _, rest = rule.partition(":")
    kind = kind.strip().lower()
    rest = rest.strip()
    if not rest:
        raise ManorError(
            f"rule の文法が不正です（内容が空です）: {rule!r}",
            key="error.house.rule_empty",
            params={"rule": repr(rule)},
        )

    if kind == "weekly":
        tokens = [t.strip().lower() for t in rest.split(",") if t.strip()]
        if not tokens:
            raise ManorError(
                f"weekly には曜日が要ります: {rule!r}",
                key="error.house.rule_weekly_missing_weekday",
                params={"rule": repr(rule)},
            )
        weekdays: set[int] = set()
        for t in tokens:
            if t not in WEEKDAY_TOKENS:
                raise ManorError(
                    f"曜日が不正です（mon〜sun）: {t!r}（{rule!r}）",
                    key="error.house.rule_weekday_invalid",
                    params={"token": repr(t), "rule": repr(rule)},
                )
            weekdays.add(WEEKDAY_TOKENS[t])
        return {"kind": "weekly", "weekdays": weekdays}

    if kind == "monthly":
        items_raw = [t.strip().lower() for t in rest.split(",") if t.strip()]
        if not items_raw:
            raise ManorError(
                f"monthly には「第N-曜日」が要ります: {rule!r}",
                key="error.house.rule_monthly_missing_item",
                params={"rule": repr(rule)},
            )
        items: list[tuple[int, int]] = []
        for t in items_raw:
            m = _MONTHLY_RE.match(t)
            if not m:
                raise ManorError(
                    f"monthly の書式が不正です（例: 2nd-wed）: {t!r}（{rule!r}）",
                    key="error.house.rule_monthly_format_invalid",
                    params={"token": repr(t), "rule": repr(rule)},
                )
            nth = int(m.group(1))
            weekday = WEEKDAY_TOKENS[m.group(3)]
            items.append((nth, weekday))
        return {"kind": "monthly", "items": items}

    if kind == "biweekly":
        if "@" not in rest:
            raise ManorError(
                f"biweekly は「曜日@起点日」の形です: {rule!r}",
                key="error.house.rule_biweekly_missing_at",
                params={"rule": repr(rule)},
            )
        wd_str, _, anchor_str = rest.partition("@")
        wd_str = wd_str.strip().lower()
        anchor_str = anchor_str.strip()
        if wd_str not in WEEKDAY_TOKENS:
            raise ManorError(
                f"曜日が不正です（mon〜sun）: {wd_str!r}（{rule!r}）",
                key="error.house.rule_weekday_invalid",
                params={"token": repr(wd_str), "rule": repr(rule)},
            )
        anchor = parse_date(anchor_str)
        weekday = WEEKDAY_TOKENS[wd_str]
        if anchor.weekday() != weekday:
            raise ManorError(
                f"biweekly の起点日 {anchor_str} は {_WEEKDAY_NAMES[anchor.weekday()]} で、"
                f"指定した曜日 {wd_str} と一致しません: {rule!r}",
                key="error.house.rule_biweekly_anchor_mismatch",
                params={
                    "anchor_str": anchor_str,
                    "weekday_name": _WEEKDAY_NAMES[anchor.weekday()],
                    "wd_str": wd_str,
                    "rule": repr(rule),
                },
            )
        return {"kind": "biweekly", "weekday": weekday, "anchor": anchor_str}

    if kind == "date":
        parse_date(rest)  # 実在確認（結果は使わず文字列のまま保持する）
        return {"kind": "date", "date": rest}

    raise ManorError(
        f"rule の kind が不明です（weekly/monthly/biweekly/date のいずれか）: {rule!r}",
        key="error.house.rule_kind_unknown",
        params={"rule": repr(rule)},
    )


def _matches(parsed: dict[str, object], d: date) -> bool:
    kind = parsed["kind"]
    if kind == "weekly":
        return d.weekday() in parsed["weekdays"]  # type: ignore[operator]
    if kind == "monthly":
        for nth, weekday in parsed["items"]:  # type: ignore[union-attr]
            if d.weekday() == weekday and (d.day - 1) // 7 + 1 == nth:
                return True
        return False
    if kind == "biweekly":
        if d.weekday() != parsed["weekday"]:
            return False
        anchor = date.fromisoformat(str(parsed["anchor"]))
        return (d - anchor).days % 14 == 0
    if kind == "date":
        return d.isoformat() == parsed["date"]
    return False  # pragma: no cover — parse_rule が既に kind を検算済み


def next_dates(rule: str, today: str, days: int) -> list[str]:
    """`rule` に従い、``[today, today+days]``（両端含む）内の該当日を古い順に返す。

    `today` は `YYYY-MM-DD`。`days` が負なら空リスト（範囲が無い）。
    """
    parsed = parse_rule(rule)
    start = parse_date(today)
    end = start + timedelta(days=days)
    out: list[str] = []
    d = start
    while d <= end:
        if _matches(parsed, d):
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _due_key(r: dict[str, object]) -> str:
    return str(r.get("name") or r.get("what") or r.get("item") or "")


def due_chores(
    rows: list[dict[str, object]], today: str, days: int | None = None
) -> list[dict[str, object]]:
    """当番／手入れ（`cadence_days` と `last_done` を持つ行）を超過日数の大きい順に並べる。

    各行に `next_due`（`last_done + cadence_days` 日。未記録なら None）と
    `overdue_days`（`today - next_due` の日数。マイナスならまだ先。未記録なら None）を足す。

    `last_done` が None の行は「一度も記録なし」として常に先頭に来る
    （`days` を指定しても除外されない——一度もやっていない当番は常に知らせる）。

    `days` を指定すると、`overdue_days is None`（未記録）または
    `overdue_days >= -days`（今日から `days` 日以内に来る、または既に超過）の行だけを残す。
    """
    today_d = parse_date(today)
    out: list[dict[str, object]] = []
    for row in rows:
        r = dict(row)
        last_done = r.get("last_done")
        cadence = int(r["cadence_days"])  # type: ignore[arg-type]
        if not last_done:
            r["next_due"] = None
            r["overdue_days"] = None
        else:
            next_due = parse_date(str(last_done)) + timedelta(days=cadence)
            r["next_due"] = next_due.isoformat()
            r["overdue_days"] = (today_d - next_due).days
        out.append(r)

    if days is not None:
        out = [r for r in out if r["overdue_days"] is None or r["overdue_days"] >= -days]

    out.sort(
        key=lambda r: (
            0 if r["overdue_days"] is None else 1,
            -(r["overdue_days"] if r["overdue_days"] is not None else 0),
            _due_key(r),
        )
    )
    return out


def low_supplies(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """`qty` が `threshold` 以下になっている消耗品を、不足が大きい順に返す。

    `threshold` が NULL（未設定）の行は対象外（判定できないので拾わない）。
    `qty` が NULL（不明）の行も対象外（推測で埋めない）。
    """
    out: list[dict[str, object]] = []
    for row in rows:
        r = dict(row)
        qty = r.get("qty")
        threshold = r.get("threshold")
        if qty is None or threshold is None:
            continue
        if float(qty) <= float(threshold):
            out.append(r)
    out.sort(key=lambda r: (float(r["qty"]) - float(r["threshold"]), str(r.get("item") or "")))
    return out
