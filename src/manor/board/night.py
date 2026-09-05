"""夜勤の作業報告（`home/night/reports/*.md`）を一覧・解析する。

夜勤の仕組みそのもの（誰が・いつ・どう書くか）は別担当（`src/manor/night/**`）の領分。
board は**その成果物（Markdown ファイル）を読んで見せるだけ**——書き込み口はここに置かない
（GET しか無い。`api_night.py` も同様）。

書式は v1 `apps/butler-board/src/butler_board/night_report.py` と同じ想定
（`# 夜勤の作業報告 <日付>` / `> 開始 .. / 終了 .. / 上限 ..` / `## N1 <件名>` の下に
`- **背景**` `- **目的**` `- **やったこと**` `- **どこまで**` `- **次**`）。ここに実装したのは
その「`## N` ごとにカードへ割る」という考え方——queue_id の抽出など v1 固有の細部までは
移植していない（board の役目は「読める形で見せる」ことで、queue との突き合わせではない）。

v1 と同じ設計判断を1つ引き継ぐ: **書式が違っても落ちない。** 夜勤の報告は毎晩ひとりでに
書かれ、書式は今後も変わりうる。取れたぶんだけ返し、取れなければ `ok: false`（画面は
それを見て原文の Markdown 表示に落ちる）。**例外は投げない。**
"""

from __future__ import annotations

import re
from pathlib import Path

#: 拾う項目。**完全一致ではなく前方一致**で拾う（v1 と同じ理由: 見出し名が将来変わりうる）。
FIELD_LABELS = ("背景", "目的", "やったこと", "どこまで", "次")

_FIELD_KEYS = {
    "背景": "background",
    "目的": "purpose",
    "やったこと": "did",
    "どこまで": "progress",
    "次": "next",
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")
#: `- **背景**: ...` / `- **背景** ...` の両方を拾う（v1 queue_doc.FIELD_LINE 相当）。
_FIELD_LINE = re.compile(r"^\s*-\s+\*\*([^*]+)\*\*[:：]?\s*(.*)$")
_LIST_ITEM = re.compile(r"^\s*-\s+(.*)$")
_HRULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
#: 見出し先頭の通し番号（`N1` / `N10`）。年号（4桁）を誤って拾わないよう桁を絞る。
_TASK_NUMBER = re.compile(r"^([A-Za-z]{0,2}\d{1,3})(?![\d-])(?=\s|$)")
#: 「どこまで」から状態を1語で決める（v1 classify_state と同じ判断。迷ったら未完に倒す）。
_STATE_HOLD = "hold"
_STATE_DONE = "done"
_STATE_OTHER = "other"


def list_report_dates(home: Path) -> list[str]:
    """`home/night/reports/*.md` の日付一覧（新しい順）。フォルダが無ければ空リスト。"""
    reports_dir = Path(home) / "night" / "reports"
    if not reports_dir.is_dir():
        return []
    dates = [p.stem for p in reports_dir.glob("*.md") if _DATE_RE.match(p.stem)]
    return sorted(dates, reverse=True)


def read_report_text(home: Path, date: str) -> str | None:
    """1晩ぶんの報告の生 Markdown。日付の形が違う・ファイルが無ければ `None`。"""
    if not _DATE_RE.match(date):
        return None
    path = Path(home) / "night" / "reports" / f"{date}.md"
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _fenced(lines: list[str]) -> list[bool]:
    """行ごとに「コードフェンスの中か」を返す（中の `#` を見出しと誤認しないため）。"""
    out: list[bool] = []
    inside = False
    for line in lines:
        if line.lstrip().startswith("```"):
            inside = not inside
            out.append(True)
            continue
        out.append(inside)
    return out


def _quote_block(lines: list[str], start: int) -> list[str]:
    """見出し直後の引用ブロックを段落ごとに返す（`>` は落とす）。"""
    i = start
    while i < len(lines) and not lines[i].strip():
        i += 1
    paragraphs: list[str] = []
    current = ""
    while i < len(lines):
        m = _QUOTE.match(lines[i])
        if m is None:
            break
        text = m.group(1).strip()
        if not text:
            if current:
                paragraphs.append(current)
                current = ""
        else:
            current = (current + " " + text).strip() if current else text
        i += 1
    if current:
        paragraphs.append(current)
    return paragraphs


def _normalize_label(raw_label: str) -> str:
    for known in FIELD_LABELS:
        if raw_label.startswith(known):
            return known
    return ""


def _order_fields(raw: list[list[str]]) -> list[dict[str, str]]:
    """既定ラベルを決まった順で先に、未知のラベル・無ラベルは原文順で後ろに置く。"""
    ordered: list[dict[str, str]] = []
    used: set[int] = set()
    for label in FIELD_LABELS:
        for i, (raw_label, text) in enumerate(raw):
            if i in used or _normalize_label(raw_label) != label:
                continue
            used.add(i)
            if text.strip():
                ordered.append({"key": _FIELD_KEYS[label], "label": label, "text": text.strip()})
            break
    for i, (raw_label, text) in enumerate(raw):
        if i in used or not text.strip():
            continue
        ordered.append({"key": "", "label": raw_label or "（無題）", "text": text.strip()})
    return ordered


def _parse_fields(body: list[str]) -> list[dict[str, str]]:
    raw: list[list[str]] = []
    open_item = False
    for line in body:
        if _HRULE.match(line):
            open_item = False
            continue
        if not line.strip():
            open_item = False
            continue
        m = _FIELD_LINE.match(line)
        if m:
            raw.append([m.group(1).strip(), m.group(2).strip()])
            open_item = True
            continue
        item = _LIST_ITEM.match(line)
        if item:
            raw.append(["", item.group(1).strip()])
            open_item = True
            continue
        text = line.strip()
        if open_item and raw:
            raw[-1][1] = (raw[-1][1] + " " + text).strip()
        else:
            raw.append(["", text])
            open_item = True
    return _order_fields(raw)


def _split_heading(title: str) -> tuple[str, str]:
    """`N1 発話の設計` を (番号, 題名) に割る。取れなければ番号は空。"""
    rest = (title or "").strip()
    number = ""
    m = _TASK_NUMBER.match(rest)
    if m:
        number = m.group(1)
        rest = rest[m.end() :].strip()
    return number, rest.strip(" 　-—–:：")


def _classify_state(progress: str) -> str:
    """v1 と同じ判断: 「保留」を含めば保留、次に「完了」、それ以外は other。

    迷ったら未完に倒す（保留が完了より先）——「まだ手が要るもの」を見つけるための画面で、
    完了を保留に見誤る代償は一瞥だが、保留を完了に見誤る代償は残作業が隠れることだから
    （v1 `night_report.classify_state` のコメントをそのまま引き継ぐ）。
    """
    text = (progress or "").strip()
    if "保留" in text:
        return _STATE_HOLD
    if "完了" in text:
        return _STATE_DONE
    return _STATE_OTHER


def parse_report(text: str, date_hint: str = "") -> dict[str, object]:
    """1晩ぶんの報告を `## N<件名>` ごとのカードへ割る（v1 night_report.py の考え方）。

    **どんな入力でも例外を投げない。** 拾えたぶんだけ返し、`## ` 見出しが1つも
    タスクとして拾えなければ `ok: False`——画面はそれを見て原文の Markdown 表示に落ちる。
    """
    try:
        lines = (text or "").replace("\r\n", "\n").split("\n")
        fenced = _fenced(lines)
        title = ""
        summary: list[str] = []
        heads: list[tuple[int, int, str]] = []
        for i, line in enumerate(lines):
            if fenced[i]:
                continue
            m = _HEADING.match(line)
            if m:
                heads.append((i, len(m.group(1)), m.group(2).strip()))

        if heads and heads[0][1] == 1:
            i, _level, htitle = heads[0]
            title = htitle
            summary = _quote_block(lines, i + 1)
            heads = heads[1:]

        starts = [h for h in heads if h[1] <= 2]
        tasks: list[dict[str, object]] = []
        for index, (line_no, _level, htitle) in enumerate(starts):
            end = starts[index + 1][0] if index + 1 < len(starts) else len(lines)
            body = lines[line_no + 1 : end]
            number, name = _split_heading(htitle)
            fields = _parse_fields(body)
            is_task = bool(number) or any(f["key"] for f in fields)
            if not is_task:
                continue
            progress = next((f["text"] for f in fields if f["key"] == "progress"), "")
            tasks.append(
                {
                    "number": number,
                    "title": name or htitle,
                    "heading": htitle,
                    "fields": fields,
                    "state": _classify_state(progress),
                }
            )

        return {
            "date": date_hint,
            "title": title,
            "summary": summary,
            "tasks": tasks,
            "ok": bool(tasks),
        }
    except Exception:
        # v1 と同じ約束: 構文解析で画面を落とさない。取れなければ空で返し、
        # 画面は生の Markdown 表示に落ちる。
        return {"date": date_hint, "title": "", "summary": [], "tasks": [], "ok": False}
