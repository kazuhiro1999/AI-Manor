"""steward（家令・家計）の純粋関数。ADR-002 §5。CLI（cli.py）と分けて試験しやすくする。

**道具は判断しない。** ここにあるのは並べ替え・突き合わせ・集計・期日計算・入力検算だけ。
「何を勧めるか」「解約を勧めるか」は担当（LLM。`.claude/agents/steward.md`）が決める。
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from manor import i18n
from manor.errors import ManorError

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_YM_RE = re.compile(r"^\d{4}-\d{2}$")

VALID_CYCLES: frozenset[str] = frozenset({"weekly", "monthly", "yearly"})


def parse_date(value: str) -> date:
    """CLI が受ける日付は `YYYY-MM-DD` だけ（ADR-002 §5）。それ以外は `ManorError`。

    `datetime.strptime` 単体だと `2026-9-2` のような桁数違いも通ってしまうため、
    先に正規表現で桁数を固定してから実在する日付かを検算する（2/30 のような日を弾く）。
    """
    text = str(value).strip()
    if not _DATE_RE.match(text):
        raise ManorError(
            f"日付は YYYY-MM-DD で指定してください: {value!r}",
            code=2,
            key="error.money.date_format",
            params={"value": repr(value)},
        )
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ManorError(
            f"日付が実在しません: {value!r}",
            code=2,
            key="error.money.date_not_exist",
            params={"value": repr(value)},
        ) from exc


def parse_ym(value: str) -> str:
    """`manor money month --ym` の年月。`YYYY-MM` だけを受ける。"""
    text = str(value).strip()
    if not _YM_RE.match(text):
        raise ManorError(
            f"年月は YYYY-MM で指定してください: {value!r}",
            code=2,
            key="error.money.ym_format",
            params={"value": repr(value)},
        )
    # 実在する月かどうかは date.fromisoformat(f"{text}-01") で検算する。
    try:
        date.fromisoformat(f"{text}-01")
    except ValueError as exc:
        raise ManorError(
            f"年月が実在しません: {value!r}",
            code=2,
            key="error.money.ym_not_exist",
            params={"value": repr(value)},
        ) from exc
    return text


def parse_amount(value: str) -> int:
    """金額は**整数の円のみ**。小数・負数・数値でない文字列は `ManorError`（ADR-002 §5 の絶対の線）。

    `int(str)` は `"12.5"` のような小数表記をそのまま `ValueError` にしてくれるので、
    小数の拒否はそこに任せる。負数は int 変換自体は通るので、変換後に別途弾く。
    """
    text = str(value).strip()
    try:
        amount = int(text)
    except ValueError as exc:
        raise ManorError(
            f"金額は整数の円で指定してください: {value!r}",
            code=2,
            key="error.money.amount_format",
            params={"value": repr(value)},
        ) from exc
    if amount < 0:
        raise ManorError(
            f"金額に負数は指定できません: {value!r}",
            code=2,
            key="error.money.amount_negative",
            params={"value": repr(value)},
        )
    return amount


def advance(next_due: str, cycle: str) -> str:
    """定期支払いの `next_due` を1周期進める（`manor money recurring paid` が使う）。

    **月末の規則**（ここが唯一の出どころ。書き写さない）:
    月を1つ進めた結果の月に元の日が無ければ、その月の末日に丸める
    （例: 1/31 + monthly → 2/28、うるう年なら 2/29）。
    丸めた日をそのまま次回の起点にする＝**元の日には戻さない**（簡単な規則にする）。
    年送りも同じ考え方: 2/29 + yearly は、結果の年がうるう年でなければ 2/28 に丸める。
    週送りは丸めが要らない（7日後は必ず存在する）。
    """
    if cycle not in VALID_CYCLES:
        raise ManorError(
            f"語彙外の cycle です: {cycle!r}",
            code=2,
            key="error.money.cycle_unknown",
            params={"cycle": repr(cycle)},
        )
    d = parse_date(next_due)

    if cycle == "weekly":
        return (d + timedelta(days=7)).isoformat()

    if cycle == "yearly":
        year = d.year + 1
        month = d.month
    else:  # monthly
        month = d.month + 1
        year = d.year
        if month > 12:
            month = 1
            year += 1

    last_day = calendar.monthrange(year, month)[1]
    day = min(d.day, last_day)
    return date(year, month, day).isoformat()


def month_summary(rows: list[dict], budgets: dict[str, int], ym: str) -> dict:
    """月ぶんの `steward_expense` 行から、分類別合計・予算との差・収入をまとめる（数えるだけ）。

    `rows` は事前に月で絞り込んでいなくてよい（ここで `ym` により絞る）。
    返り値の `expenses` の並びは: ①予算超過 ②予算内（残り少ない順） ③予算未設定（分類名の昇順）。
    収入は `expenses` に混ぜず、別に `income` として返す（ADR-002 §5「収入は別行」）。
    """
    ym = parse_ym(ym)
    by_category: dict[str, int] = {}
    income_total = 0
    for r in rows:
        if str(r["date"])[:7] != ym:
            continue
        amount = int(r["amount"])
        if r["kind"] == "income":
            income_total += amount
        else:
            by_category[r["category"]] = by_category.get(r["category"], 0) + amount

    expenses: list[dict] = []
    for category, spent in by_category.items():
        budget = budgets.get(category)
        if budget is None:
            diff = None
            over = False
        else:
            diff = int(budget) - spent
            over = diff < 0
        expenses.append(
            {"category": category, "spent": spent, "budget": budget, "diff": diff, "over": over}
        )

    def _sort_key(e: dict) -> tuple:
        if e["over"]:
            group = 0
        elif e["budget"] is not None:
            group = 1
        else:
            group = 2
        return (group, e["diff"] if e["diff"] is not None else 0, e["category"])

    expenses.sort(key=_sort_key)
    total_expense = sum(e["spent"] for e in expenses)
    return {"ym": ym, "expenses": expenses, "income": income_total, "total_expense": total_expense}


def upcoming(rows: list[dict], today: str, days: int) -> list[dict]:
    """`steward_recurring` を期日順に並べる（`manor money due` が使う）。

    `active=0` の行は除く。`today + days` より先の行（まだ先の話）は落とす。
    **既に期日を過ぎている行は `days` に関わらず必ず含める**（見落とし防止。滞納を window で隠さない）。
    各行に `overdue_days` を足す。正なら滞納日数（CLI 側で `+N日` に整形する）、
    0 なら本日、負なら「あと -overdue_days 日」。
    """
    today_d = parse_date(today)
    cutoff = today_d + timedelta(days=days)
    out: list[dict] = []
    for r in rows:
        if int(r.get("active", 1)) == 0:
            continue
        due_d = parse_date(str(r["next_due"]))
        if due_d > cutoff:
            continue
        item = dict(r)
        item["overdue_days"] = (today_d - due_d).days
        out.append(item)
    out.sort(key=lambda r: r["next_due"])
    return out


def trend(rows: list[dict], months: int) -> list[dict]:
    """`steward_expense` を月別に集計する（`manor money trend` が使う。数えるだけ）。

    対象は rows に実際にデータがある月に限る（データの無い月を0円で捏造しない）。
    古い月→新しい月の順に、直近 `months` か月ぶんだけ返す。
    """
    buckets: dict[str, dict] = {}
    for r in rows:
        ym = str(r["date"])[:7]
        bucket = buckets.setdefault(ym, {"total_expense": 0, "total_income": 0, "by_category": {}})
        amount = int(r["amount"])
        if r["kind"] == "income":
            bucket["total_income"] += amount
        else:
            bucket["total_expense"] += amount
            bucket["by_category"][r["category"]] = bucket["by_category"].get(r["category"], 0) + amount

    yms = sorted(buckets.keys())
    if months > 0:
        yms = yms[-months:]
    else:
        yms = []
    return [{"ym": ym, **buckets[ym]} for ym in yms]
