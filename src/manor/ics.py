"""ICS（iCalendar）の解析（ADR-012 §2「5d カレンダー拡張」D4・D5）。

**この層は純粋関数にする。** 文字列（ICS のテキスト）を受け取り、予定の辞書の一覧を返す
だけ——ネットワーク（URL 取得）もDB（`secretary_event` への書き込み）もここでは触らない。
取り込みの実体（HTTP・DB・冪等な突き合わせ）は `src/manor/calendar.py` の仕事。
分けてあるのは「壊れたときに切り分けられるように」（ADR-012 D4 の指示どおり）——
解析がおかしいのか、突き合わせがおかしいのか、試験だけで判別できる。

**新しい pip 依存を入れない**という当初の判断（ADR-012 D4）は、**ICS を解くための
外部ライブラリを入れない**という意図だった。標準ライブラリ（`re`・`datetime`・
`zoneinfo`）だけで完結させる方針は変わらないが、`zoneinfo` 自身が Windows では
`tzdata`（コードを持たないデータのみの配布物。CPython の中の人が保守）が無いと
IANA のタイムゾーンを**1つも**解決できない（`UTC` すら不可。2026-09-05 実機で
`ZoneInfoNotFoundError` を確認）ことが分かったため、D4 を改訂して `tzdata` を
`pyproject.toml` の依存に加えた（2026-09-05・執事の判断）。

## 対応する項目

`UID` `SUMMARY` `DTSTART` `DTEND` `LOCATION` `DESCRIPTION` `RRULE` `EXDATE` `STATUS`。
`STATUS:CANCELLED` は取り込まない。

## 時刻（ADR-012 D4）

3つの形を扱い、**呼び手が渡した `local_tz`（既定は実行時のシステムのローカルタイム
ゾーン）に揃えて**、その他のテーブル（`secretary_event.start`/`end`）と同じ「naive な
ローカル ISO 文字列」で返す（`util.now()` が `datetime.now().isoformat()` で naive を
返すのと同じ形——tz 情報を表に持たせる設計に manor はなっていないため）:

- `DTSTART;TZID=Asia/Tokyo:20260905T100000` — 指定された tz として読み、`local_tz` へ変換
- `DTSTART:20260905T010000Z` — UTC として読み、`local_tz` へ変換
- `DTSTART;VALUE=DATE:20260905` — 終日。tz 変換はしない（日付だけを返す）

`local_tz` を引数にして呼び手に渡させているのは、この層を「同じ入力なら同じ出力」の
純粋関数のままにするため（システム時計の設定に直接依存させると、試験機ごとに結果が
変わってしまう）。

**それでも `TZID` が解決できなかったときの逃げ道は残す**（`tzdata` が入っていても、
主人のカレンダーが `Antarctica/Casey` のような滅多に無い地域を使う可能性はゼロでは
ない。環境は壊れうる）。その場合は `local_tz` そのものとして扱う（諦めてローカルと
みなすフォールバック）が、**黙って落とさない**——非対応の RRULE と同じ作法で、
「どの TZID を解決できずローカル時刻として扱ったか」を `note` に残し、画面から
見える形にする（`calendar_url` の疎通と違い、これは1件ごとの予定に付く注記）。

## 繰り返し（ADR-012 D5）

対応: `FREQ=DAILY|WEEKLY|MONTHLY|YEARLY`・`INTERVAL`・`COUNT`・`UNTIL`・`BYDAY`
（**WEEKLY のみ**）。それ以外のキーワード（`BYMONTHDAY`・`BYSETPOS`・`COUNT` と
`UNTIL` の同時指定 等）が混じっていたら「対応できない繰り返し」として扱う。

**対応できない繰り返しを黙って落とさない。** 最初の1回（`DTSTART`）だけを取り込み、
`note` に元の `RRULE` 文字列をそのまま残す——「出ていない予定がある」ことが
画面から見える形にする。

**`today`/`window_days`（既定90日）の窓は、繰り返しの展開にだけかける**（D5 改訂・
2026-09-05・執事の判断）。単発の予定（繰り返しが無い、または対応できない繰り返しで
最初の1回だけを取り込む場合も含む）は、**今日以降であれば上限なくすべて取り込む**
——4か月先に予約した旅行のような、実在するが窓の外にある予定を manor が持たない、
という取り込み漏れを避けるため。際限なく増える原因は「終わりの無い繰り返し」だけ
なので、そこにだけ上限を置けば十分である。過去（`today` より前）は単発・繰り返し
問わず取り込まない。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

#: 繰り返し展開の安全弁。壊れた ICS（異常に細かい INTERVAL 等）で無限ループしないための
#: 上限（実際の窓は最大90日程度なので、この回数に達することは通常の入力では起きない）。
_MAX_EXPANSION_ITERATIONS = 3000

_SUPPORTED_FREQ = {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}
_BYDAY_MAP: dict[str, int] = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


# --- 行の折り返しを戻す（RFC5545: 75オクテットで折られ、次行が空白/タブで始まる） -------------


def unfold(text: str) -> str:
    """折り返された行を1本に戻す。改行コードは `\\n` に統一する。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    for line in normalized.split("\n"):
        if line and line[0] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return "\n".join(out)


# --- VEVENT ブロックの切り出し ------------------------------------------------------------


def extract_vevents(unfolded_text: str) -> list[str]:
    """`BEGIN:VEVENT` 〜 `END:VEVENT` の中身（行のリストを結合した文字列）を切り出す。
    ネストは想定しない（VEVENT 同士は入れ子にならないのが ICS の規則）。
    """
    blocks: list[str] = []
    current: list[str] | None = None
    for line in unfolded_text.split("\n"):
        stripped = line.strip()
        if stripped == "BEGIN:VEVENT":
            current = []
        elif stripped == "END:VEVENT":
            if current is not None:
                blocks.append("\n".join(current))
            current = None
        elif current is not None:
            current.append(line)
    return blocks


# --- 1行の解釈: `KEY;PARAM=値;PARAM=値:値` -------------------------------------------------


def _split_prop_line(line: str) -> tuple[str, dict[str, str], str] | None:
    """`NAME;PARAM=VALUE;...:VALUE` を `(NAME, {PARAM: VALUE}, VALUE)` に割る。

    値の中に `:`（URL 等）が混じることがあるため、**引用符の外にある最初の `:`** を
    区切りとして探す（引用符付きパラメータ値の中の `:` は区切りにしない）。
    """
    in_quotes = False
    split_at: int | None = None
    for i, ch in enumerate(line):
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == ":" and not in_quotes:
            split_at = i
            break
    if split_at is None:
        return None
    head = line[:split_at]
    value = line[split_at + 1 :]
    parts = head.split(";")
    if not parts or not parts[0]:
        return None
    name = parts[0].strip().upper()
    params: dict[str, str] = {}
    for chunk in parts[1:]:
        if "=" not in chunk:
            continue
        key, _, raw_value = chunk.partition("=")
        params[key.strip().upper()] = raw_value.strip().strip('"')
    return name, params, value


def _unescape_text(value: str) -> str:
    """TEXT 型の値のエスケープを戻す: `\\,` `\\;` `\\n`/`\\N` `\\\\`。"""
    out: list[str] = []
    i = 0
    n = len(value)
    while i < n:
        ch = value[i]
        if ch == "\\" and i + 1 < n:
            nxt = value[i + 1]
            if nxt in ("n", "N"):
                out.append("\n")
                i += 2
                continue
            if nxt in (",", ";", "\\"):
                out.append(nxt)
                i += 2
                continue
        out.append(ch)
        i += 1
    return "".join(out)


# --- 日時の解決（3つの形。D4） -------------------------------------------------------------


def _resolve_local_tz(local_tz: tzinfo | None) -> tzinfo:
    if local_tz is not None:
        return local_tz
    # 呼び手が省略したときだけ、実行時のシステムのローカルタイムゾーンを見る
    # （純粋関数としての決定性は呼び手が `local_tz` を渡すことで保つ想定。ここは
    # 「省略時にどう動くか」という利便性のための既定値でしかない）。
    resolved = datetime.now().astimezone().tzinfo
    return resolved if resolved is not None else timezone.utc


def _zoneinfo_or_none(tzid: str) -> tzinfo | None:
    """`ZoneInfo(tzid)` を試す。解決できなければ `None`（例外は外へ出さない）。
    `tzdata` を依存に足した後も、この関数自体は残す——**逃げ道**（滅多に無い地域・
    `tzdata` が入っていない実行環境）を塞がないため。呼び手（`parse_datetime_value`・
    `parse_ics`）はこれで「解決できたか」を判定し、できなかった分だけ `note` に残す。
    """
    try:
        return ZoneInfo(tzid)
    except Exception:  # noqa: BLE001 - 未知/未導入の TZID は None（例外は出さない）
        return None


def parse_datetime_value(value: str, params: dict[str, str], *, local_tz: tzinfo) -> tuple[str, bool, bool]:
    """`DTSTART`/`DTEND`/`EXDATE` の1値を `(ISO文字列, 終日かどうか, TZID未解決か)` に
    変換する。

    - `VALUE=DATE`（または8桁の日付のみ）: 終日。tz変換はしない
    - 末尾 `Z`: UTC として読み、`local_tz` へ変換
    - `TZID=...`: そのタイムゾーンとして読み、`local_tz` へ変換。解決できなければ
      `local_tz` そのものとして扱う（諦めてローカルとみなすフォールバック。逃げ道）——
      戻り値の3つ目（`tz_unresolved`）を `True` にして、**呼び手が note に残せるように
      知らせる**（黙って落とさない。ADR-012 D4 改訂）
    - どちらも無い（フローティング時刻）: そのまま naive local として扱う
    """
    v = value.strip()
    if params.get("VALUE", "").upper() == "DATE" or (len(v) == 8 and v.isdigit()):
        d = datetime.strptime(v, "%Y%m%d").date()
        return d.isoformat(), True, False

    if v.endswith("Z"):
        dt = datetime.strptime(v[:-1], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        local = dt.astimezone(local_tz).replace(tzinfo=None)
        return local.isoformat(timespec="seconds"), False, False

    tzid = params.get("TZID")
    if tzid:
        zone = _zoneinfo_or_none(tzid)
        tz_unresolved = zone is None
        dt = datetime.strptime(v, "%Y%m%dT%H%M%S").replace(tzinfo=zone or local_tz)
        local = dt.astimezone(local_tz).replace(tzinfo=None)
        return local.isoformat(timespec="seconds"), False, tz_unresolved

    # フローティング時刻（tz指定なし）: 既にローカルとみなしてそのまま使う。
    dt = datetime.strptime(v, "%Y%m%dT%H%M%S")
    return dt.isoformat(timespec="seconds"), False, False


def _parse_until(value: str, *, local_tz: tzinfo) -> datetime:
    """`RRULE` の `UNTIL` を naive local な `datetime` にする（比較用）。
    日付のみのときは、その日の終わり（23:59:59）を含むよう扱う（RFC5545 では UNTIL の
    値型は DTSTART と揃うのが通例だが、日付だけの場合はその日を含める判断をここでする）。
    """
    v = value.strip()
    if len(v) == 8 and v.isdigit():
        d = datetime.strptime(v, "%Y%m%d")
        return d.replace(hour=23, minute=59, second=59)
    if v.endswith("Z"):
        dt = datetime.strptime(v[:-1], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        return dt.astimezone(local_tz).replace(tzinfo=None)
    return datetime.strptime(v, "%Y%m%dT%H%M%S")


# --- RRULE の解釈 ---------------------------------------------------------------------------


def parse_rrule(rrule: str) -> dict[str, str] | None:
    """`FREQ=WEEKLY;BYDAY=MO,WE` を `{"FREQ": "WEEKLY", "BYDAY": "MO,WE"}` に割る。
    `KEY=VALUE` の形を保てない・`FREQ` を持たない文字列は `None`（壊れた RRULE）。
    """
    parts: dict[str, str] = {}
    for chunk in rrule.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            return None
        key, _, value = chunk.partition("=")
        parts[key.strip().upper()] = value.strip()
    if "FREQ" not in parts:
        return None
    return parts


def rrule_supported(parts: dict[str, str]) -> bool:
    """「よくある形」かどうか（ADR-012 D5）。対応: `FREQ`(DAILY/WEEKLY/MONTHLY/YEARLY)・
    `INTERVAL`・`COUNT`・`UNTIL`・`BYDAY`（WEEKLY のみ）。それ以外のキーが混じっている、
    `BYDAY` の値が曜日略号でない、`COUNT` と `UNTIL` が同時にある（RFC5545 上も不正）等は
    非対応として扱う。
    """
    freq = parts.get("FREQ")
    if freq not in _SUPPORTED_FREQ:
        return False
    allowed = {"FREQ", "INTERVAL", "COUNT", "UNTIL"}
    if freq == "WEEKLY":
        allowed = allowed | {"BYDAY"}
    if not set(parts.keys()) <= allowed:
        return False
    if "COUNT" in parts and "UNTIL" in parts:
        return False
    if "BYDAY" in parts:
        days = [d.strip().upper() for d in parts["BYDAY"].split(",") if d.strip()]
        if not days or not all(d in _BYDAY_MAP for d in days):
            return False
    if "COUNT" in parts and not parts["COUNT"].strip().isdigit():
        return False
    if "INTERVAL" in parts:
        interval_s = parts["INTERVAL"].strip()
        if not interval_s.isdigit() or int(interval_s) < 1:
            return False
    return True


def _add_months(dt: datetime, months: int) -> datetime | None:
    """月を進める。対象月にその日が存在しない（例: 1/31 → 2月）ときは `None`
    （RFC5545 の通例どおり、その回は無い＝スキップする）。
    """
    total = dt.month - 1 + months
    year = dt.year + total // 12
    month = total % 12 + 1
    try:
        return dt.replace(year=year, month=month)
    except ValueError:
        return None


def _add_years(dt: datetime, years: int) -> datetime | None:
    """年を進める。閏日（2/29）が対象年に無ければ `None`（スキップ）。"""
    try:
        return dt.replace(year=dt.year + years)
    except ValueError:
        return None


def _fmt_occurrence(dt: datetime, all_day: bool) -> str:
    return dt.date().isoformat() if all_day else dt.isoformat(timespec="seconds")


def expand_rrule(
    start_iso: str,
    parts: dict[str, str],
    *,
    all_day: bool,
    today: date,
    window_end: date,
    local_tz: tzinfo,
) -> list[str]:
    """`rrule_supported(parts)` が真であることを前提に、各回の開始（ISO文字列）の
    一覧を `[today, window_end]` の範囲内だけ返す（範囲外の回も `COUNT`/`UNTIL` の
    数え上げには使うが、出力には含めない）。
    """
    freq = parts["FREQ"]
    interval = int(parts.get("INTERVAL", "1") or "1")
    count = int(parts["COUNT"]) if "COUNT" in parts else None
    until = _parse_until(parts["UNTIL"], local_tz=local_tz) if "UNTIL" in parts else None
    byday: list[int] | None = None
    if "BYDAY" in parts:
        byday = sorted({_BYDAY_MAP[d.strip().upper()] for d in parts["BYDAY"].split(",") if d.strip()})

    if all_day:
        start_dt = datetime.combine(date.fromisoformat(start_iso), time.min)
    else:
        start_dt = datetime.fromisoformat(start_iso)

    results: list[str] = []

    if freq == "WEEKLY" and byday:
        week_start = start_dt - timedelta(days=start_dt.weekday())
        week_idx = 0
        produced = 0
        iterations = 0
        while True:
            iterations += 1
            if iterations > _MAX_EXPANSION_ITERATIONS:
                break
            base_week = week_start + timedelta(weeks=week_idx * interval)
            if base_week.date() > window_end:
                break
            stop = False
            for wd in byday:
                candidate = (base_week + timedelta(days=wd)).replace(
                    hour=start_dt.hour, minute=start_dt.minute, second=start_dt.second
                )
                if candidate < start_dt:
                    continue  # 系列の開始（DTSTART）より前は数えない
                if until is not None and candidate > until:
                    stop = True
                    break
                if candidate.date() > window_end:
                    stop = True
                    break
                produced += 1
                if candidate.date() >= today:
                    results.append(_fmt_occurrence(candidate, all_day))
                if count is not None and produced >= count:
                    stop = True
                    break
            if stop:
                break
            week_idx += 1
        return results

    cur = start_dt
    produced = 0
    iterations = 0
    while True:
        iterations += 1
        if iterations > _MAX_EXPANSION_ITERATIONS:
            break
        if until is not None and cur > until:
            break
        if cur.date() > window_end:
            break
        produced += 1
        if cur.date() >= today:
            results.append(_fmt_occurrence(cur, all_day))
        if count is not None and produced >= count:
            break
        if freq == "DAILY":
            cur = cur + timedelta(days=interval)
        elif freq == "WEEKLY":
            cur = cur + timedelta(weeks=interval)
        elif freq == "MONTHLY":
            nxt = _add_months(cur, interval)
            if nxt is None:
                break
            cur = nxt
        elif freq == "YEARLY":
            nxt = _add_years(cur, interval)
            if nxt is None:
                break
            cur = nxt
        else:  # pragma: no cover - rrule_supported() が既に弾いているはずの経路
            break
    return results


# --- VEVENT 1件の生の解釈 -------------------------------------------------------------------


def _parse_vevent_block(block: str) -> dict[str, object] | None:
    uid: str | None = None
    summary = ""
    location = ""
    description = ""
    status = ""
    dtstart_raw: tuple[str, dict[str, str]] | None = None
    dtend_raw: tuple[str, dict[str, str]] | None = None
    rrule_text: str | None = None
    exdates_raw: list[tuple[str, dict[str, str]]] = []

    for line in block.split("\n"):
        line = line.strip()
        if not line:
            continue
        parsed = _split_prop_line(line)
        if parsed is None:
            continue
        name, params, value = parsed
        if name == "UID":
            uid = value.strip()
        elif name == "SUMMARY":
            summary = _unescape_text(value)
        elif name == "LOCATION":
            location = _unescape_text(value)
        elif name == "DESCRIPTION":
            description = _unescape_text(value)
        elif name == "STATUS":
            status = value.strip().upper()
        elif name == "DTSTART":
            dtstart_raw = (value, params)
        elif name == "DTEND":
            dtend_raw = (value, params)
        elif name == "RRULE":
            rrule_text = value.strip()
        elif name == "EXDATE":
            for one in value.split(","):
                one = one.strip()
                if one:
                    exdates_raw.append((one, params))

    if uid is None or dtstart_raw is None:
        return None  # UID/DTSTART が無い VEVENT は取り込みようがない（壊れた入力）

    return {
        "uid": uid,
        "summary": summary,
        "location": location,
        "description": description,
        "status": status,
        "dtstart_raw": dtstart_raw,
        "dtend_raw": dtend_raw,
        "rrule": rrule_text,
        "exdates_raw": exdates_raw,
    }


def _shift(start_iso: str, duration: timedelta | None, all_day: bool) -> str | None:
    if duration is None:
        return None
    if all_day:
        d = date.fromisoformat(start_iso) + duration
        return d.isoformat()
    dt = datetime.fromisoformat(start_iso) + duration
    return dt.isoformat(timespec="seconds")


def _from_today_onward(start_iso: str, today: date) -> bool:
    """単発の予定（繰り返し無し・または対応できない繰り返しの最初の1回）に適用する
    唯一の絞り込み: **今日以降であれば上限なく取り込む**（ADR-012 D5 改訂・2026-09-05）。
    `window_days` の上限は繰り返しの展開（`expand_rrule`）にだけかける——4か月先の
    旅行のような実在する単発の予定を manor が持たない、という取り込み漏れを避ける。
    """
    d = date.fromisoformat(start_iso[:10])
    return d >= today


# --- 公開 API ---------------------------------------------------------------------------


def parse_ics(
    text: str,
    *,
    today: date,
    window_days: int = 90,
    local_tz: tzinfo | None = None,
) -> list[dict[str, object]]:
    """ICS 全体（`VCALENDAR` のテキスト）から予定の一覧を作る。**純粋関数**——
    ネットワーク・DB を触らない。`STATUS:CANCELLED` は取り込まない。

    戻り値の各要素は `secretary_event` の列にそのまま対応する形
    （`uid`・`external_id`・`title`・`start`・`end`・`place`・`note`）。
    `external_id` は `UID` ＋ 開始時刻（ADR-012 D6。繰り返しの各回を別の予定として
    突き合わせるための鍵）。
    """
    resolved_tz = _resolve_local_tz(local_tz)
    window_end = today + timedelta(days=window_days)
    unfolded = unfold(text)

    out: list[dict[str, object]] = []
    for block in extract_vevents(unfolded):
        parsed = _parse_vevent_block(block)
        if parsed is None:
            continue
        if parsed["status"] == "CANCELLED":
            continue

        dtstart_value, dtstart_params = parsed["dtstart_raw"]  # type: ignore[misc]
        start_iso, all_day, dtstart_tz_unresolved = parse_datetime_value(
            dtstart_value, dtstart_params, local_tz=resolved_tz
        )
        unresolved_tzids: list[str] = []
        if dtstart_tz_unresolved:
            unresolved_tzids.append(str(dtstart_params.get("TZID")))

        duration: timedelta | None = None
        if parsed["dtend_raw"] is not None:
            dtend_value, dtend_params = parsed["dtend_raw"]  # type: ignore[misc]
            end_iso, _, dtend_tz_unresolved = parse_datetime_value(dtend_value, dtend_params, local_tz=resolved_tz)
            if dtend_tz_unresolved:
                unresolved_tzids.append(str(dtend_params.get("TZID")))
            if all_day:
                duration = date.fromisoformat(end_iso) - date.fromisoformat(start_iso)
            else:
                duration = datetime.fromisoformat(end_iso) - datetime.fromisoformat(start_iso)

        exdate_set: set[str] = set()
        for raw_value, raw_params in parsed["exdates_raw"]:  # type: ignore[union-attr]
            ex_iso, _, _ = parse_datetime_value(raw_value, raw_params, local_tz=resolved_tz)
            exdate_set.add(ex_iso)

        note = str(parsed["description"])
        if unresolved_tzids:
            # D4 改訂: `tzdata` を入れても解決できない TZID（滅多に無い地域・壊れた環境）
            # が残る余地がある。黙って落とさず、非対応 RRULE と同じ作法で note に残す。
            tzids_text = "、".join(dict.fromkeys(unresolved_tzids))  # 重複を保ったまま順序維持で除く
            note = _append_note(note, f"[TZID未解決] {tzids_text} を解決できず、ローカル時刻として扱いました")

        rrule_text = parsed["rrule"]

        if rrule_text:
            rrule_parts = parse_rrule(str(rrule_text))
            supported = rrule_parts is not None and rrule_supported(rrule_parts)
        else:
            rrule_parts = None
            supported = True  # 繰り返しではない

        if rrule_text and not supported:
            # D5: 対応できない繰り返し。黙って落とさず、最初の1回だけ・note に原文を残す。
            # 単発扱いなので窓の上限は掛けない（今日以降であれば取り込む。D5 改訂）。
            fallback_note = _append_note(note, f"[未対応の繰り返し] RRULE:{rrule_text}")
            starts = [start_iso] if _from_today_onward(start_iso, today) else []
            for s in starts:
                out.append(_build_event(parsed, s, _shift(s, duration, all_day), note=fallback_note))
            continue

        if rrule_text and supported:
            assert rrule_parts is not None
            # 繰り返しの展開だけに90日の窓をかける（D5 改訂。無限に増えうるのはここだけ）。
            starts = expand_rrule(
                start_iso, rrule_parts, all_day=all_day, today=today, window_end=window_end, local_tz=resolved_tz
            )
        else:
            # 単発の予定: 今日以降はすべて取り込む（上限なし。feed 自体が有限なので増え続けない）。
            starts = [start_iso] if _from_today_onward(start_iso, today) else []

        for s in starts:
            if s in exdate_set:
                continue
            out.append(_build_event(parsed, s, _shift(s, duration, all_day), note=note))

    return out


def _append_note(base: str, addition: str) -> str:
    return f"{base}\n\n{addition}" if base else addition


def _build_event(parsed: dict[str, object], start_iso: str, end_iso: str | None, *, note: str) -> dict[str, object]:
    uid = str(parsed["uid"])
    return {
        "uid": uid,
        "external_id": f"{uid}::{start_iso}",
        "title": str(parsed["summary"]),
        "start": start_iso,
        "end": end_iso,
        "place": str(parsed["location"]),
        "note": note,
    }
