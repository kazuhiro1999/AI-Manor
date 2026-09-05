"""v1 butler-board からのコピー。読む側だけ。

出所: `apps/butler-board/src/butler_board/queue_doc.py`（AI執事 v1）。
ADR-003 D2 のとおり、`parse_queue` とそれが依存する読み取り関数だけを残した。
**書き戻し（`apply_decision` / `build_decision_text` 等）・バックアップ
（`_backup` / `list_backups` / `restore_backup`）・`os` / `shutil` を使う
副作用のある関数はすべて削ってある。** manor はここへ書き戻さない
（`QueueDoc.to_dict` など書き戻しに関係しない補助はそのまま残してある）。

---

QUEUE.md の読み取り（v1 原文のコメント）:
書き戻しの方針（README にも記載）:
  パース→再生成ではなく **該当行の置換・削除・挿入** のみを行う。
  触るのは以下の3行だけ:
    1. A セクションの対象行（削除）
    2. C セクションへ挿入する1行（追加）
    3. 冒頭の「最終更新:」行（設定で無効化可）
  見出し・引用ブロック・運用メモ・B セクションには一切触れない。
  （↑書き戻し自体は manor に持ち込んでいない。読み取りの経緯として残す）
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import mdtable
from .mdtable import PLACEHOLDER_CELLS, Table, split_lines

SECTION_A = re.compile(r"^\s*A[.．、]")
SECTION_B = re.compile(r"^\s*B[.．、]")
SECTION_C = re.compile(r"^\s*C[.．、]")
# D は「判断の背景」。任意セクション。無くても従来どおり動く（後方互換）。
SECTION_D = re.compile(r"^\s*D[.．、]")

# D の中で拾う既定ラベル。この順で画面に出す（原文の並び順は保証しない仕様）。
DETAIL_LABELS = ("背景", "目的", "意図", "影響")
_DETAIL_KEYS = {"背景": "background", "目的": "purpose", "意図": "intent", "影響": "impact"}

# 「### Q6」「### Q6 — 件名」など。ID は先頭の1トークン。
_DETAIL_HEADING_ID = re.compile(r"^([^\s:：|—–]+)")
# 「- **背景**: …」「* 背景：…」「+ **影響** : …」を拾う。強調記号は任意。
FIELD_LINE = re.compile(r"^\s*[-*+]\s+\*{0,2}\s*([^*:：]{1,24}?)\s*\*{0,2}\s*[:：]\s*(.*)$")
LIST_ITEM_LINE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
_DETAIL_FIELD = FIELD_LINE
_DETAIL_LIST_ITEM = LIST_ITEM_LINE
_DETAIL_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")

_DATE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_LAST_UPDATED = re.compile(r"^(\s*>?\s*)最終更新:\s*(.*)$")

RISK_ORDER = {"high": 0, "medium": 1, "low": 2, "": 3}


def strip_md(text: str) -> str:
    """表示用の素の文字列。強調・コード・Wikiリンクの記号だけ落とす。"""
    out = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    out = re.sub(r"`(.+?)`", r"\1", out)
    out = re.sub(r"\[\[(.+?)\]\]", r"\1", out)
    out = out.replace("\\|", "|")
    return out.strip()


def join_wrapped(head: str, tail: str) -> str:
    """折り返された行をつなぐ。日本語どうしの境目には空白を入れない。"""
    if not head:
        return tail
    if not tail:
        return head
    if head[-1].isascii() and tail[0].isascii():
        return head + " " + tail
    return head + tail


def parse_date(text: str) -> date | None:
    m = _DATE.search(text or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


#: 「これは主人が手を動かす作業です」の印。
#: `内容` の先頭に置く決まりだが、`状態` 側に書かれていることもあるので
#: どちらに書かれていても拾う。表示するときは印を外す。
OWNER_MASTER_MARK = "（主人）"


# --- 状態（状態列）の正規化 -------------------------------------------------
#
# 執事が QUEUE.md を移行したあとの語彙は次の7種だけ（`tasks/QUEUE.md` B の語彙表が正）:
#   未着手 / 進行中 / 常駐 / 待ち / 保留 / 完了 YYYY-MM-DD / 取り下げ
# いずれも後ろに任意で「 — 補足文」が続く。
#
# 判定は「先頭がその語か」で行う。「その語を含むか」ではない。
# 語彙で引けないセルは code="other" に寄せ、画面には原文（status）をそのまま出す。

STATUS_LABELS = {
    "doing": "進行中",
    "resident": "常駐",
    "todo": "未着手",
    "hold": "保留",
    "waiting": "待ち",
    "done": "完了",
    "withdrawn": "取り下げ",
    "other": "その他",
}

#: 状態欄の語彙。先頭がこの語で始まるかだけを見る（含んでいるだけでは引かない）。
#: 7語（QUEUE.md B の語彙表）＋ 実データに残る裁定の2語（`承認` `却下`）。
STATUS_WORDS: tuple[str, ...] = (
    "未着手", "進行中", "常駐", "待ち", "保留", "完了", "取り下げ", "承認", "却下",
)

#: 語彙 → コード。`取り下げ` / `却下` を `done` に混ぜない（成果ではない）。
STATUS_WORD_TO_CODE: dict[str, str] = {
    "未着手": "todo",
    "進行中": "doing",
    "常駐": "resident",
    "待ち": "waiting",
    "保留": "hold",
    "完了": "done",
    "取り下げ": "withdrawn",
    "却下": "withdrawn",
    "承認": "other",
}

# 「<状態> — <補足>」の区切り。原文の作法は全角ダッシュだが、
# 見た目が同じ他のダッシュで書かれていても補足は補足として読む。
_NOTE_SPLIT = re.compile(r"\s*[—–―]\s*|\s+-\s+")


def split_status_note(plain: str) -> tuple[str, str]:
    """素の状態文を (先頭部, 補足) に切る。

    先頭の `（主人）` は剥がしてから見る（正当な書き方）。
    """
    body = plain
    while body.startswith(OWNER_MASTER_MARK):
        body = body[len(OWNER_MASTER_MARK):].lstrip()
    parts = _NOTE_SPLIT.split(body, maxsplit=1)
    return parts[0].strip(), (parts[1].strip() if len(parts) > 1 else "")


def status_word(head: str) -> str:
    """先頭部から語彙を引く。先頭がその語で始まるかだけを見る。引けなければ空文字。"""
    for candidate in STATUS_WORDS:
        if head.startswith(candidate):
            return candidate
    return ""


def parse_status_cell(cell: str) -> tuple[str, str, str]:
    """状態セルを (語彙, 補足, 素の全文) に分ける。語彙を引けなければ語彙は空文字。"""
    plain = strip_md(cell or "").strip()
    head, note = split_status_note(plain)
    return status_word(head), note, plain


def _status_code(head: str) -> str:
    """先頭部からコードを引く。語彙（先頭一致）だけを見る。引けなければ `other`。"""
    word = status_word(head)
    return STATUS_WORD_TO_CODE[word] if word else "other"


def normalize_status(status: str) -> tuple[str, str, str | None]:
    """状態セルを (code, 補足文, 完了日ISO|None) に分解する。"""
    plain = strip_md(status or "")
    head, note = split_status_note(plain)

    code = _status_code(head)
    if code == "other" and not head and note:
        # 「— 完了 2026-08-25」のように先頭部が空で、語彙が補足側にしか無い場合だけ拾う。
        code = _status_code(note)

    done: date | None = None
    if code == "done":
        done = parse_date(head) or parse_date(note)
    return code, note, (done.isoformat() if done else None)


def _status_kind(status: str) -> str:
    """既存の粗い分類（CSS クラス・API 後方互換）。normalize_status から導く。"""
    code, _, _ = normalize_status(status)
    if code in ("hold", "waiting"):
        return "blocked"
    if code == "resident":
        return "watching"
    if code == "other":
        return "watching" if "監視" in strip_md(status) else "other"
    return code


def _pj_value(raw: str) -> str:
    """PJ セルの値。プレースホルダ（`—` 等）と空欄は「未設定」= 空文字に寄せる。"""
    value = strip_md(raw or "").strip()
    return "" if value.lower() in PLACEHOLDER_CELLS else value


@dataclass
class DetailItem:
    """D セクション（判断の背景）の1件。A・C の同じ ID に対応する。"""

    id: str
    heading: str = ""
    background: str = ""
    purpose: str = ""
    intent: str = ""
    impact: str = ""
    fields: list[dict[str, str]] = field(default_factory=list)
    line: int = 0


@dataclass
class PendingItem:
    """A セクション（主人待ち）の1件。

    `pj` は所属プロジェクトID（`P1`〜`P9` / `X1` / `X2` など）。
    PJ 列が無い旧フォーマットでは空文字（＝未設定）になる。
    """

    id: str
    raised: str
    raised_date: str | None
    title: str
    title_plain: str
    recommendation: str
    risk: str
    risk_level: str
    status: str
    stale_days: int | None
    is_stale: bool
    line: int
    has_detail: bool = False
    pj: str = ""
    status_code: str = "other"
    status_label: str = "その他"
    status_note: str = ""
    done_date: str | None = None


def _detect_owner(content: str, status: str) -> str:
    """誰が手を動かす作業か。`butler`（既定）か `master` か。"""
    for text in (content, status):
        if OWNER_MASTER_MARK in strip_md(text)[:12]:
            return "master"
    return "butler"


def _strip_owner_mark(text: str) -> str:
    """表示用に印を外す。外すのは先頭に付いているものだけ。"""
    s = text.lstrip()
    if s.startswith(OWNER_MASTER_MARK):
        return s[len(OWNER_MASTER_MARK):].lstrip()
    return text


@dataclass
class RunningItem:
    """B セクション（執事の自走キュー）の1件。"""

    id: str
    content: str
    content_plain: str
    level: str
    status: str
    status_kind: str
    line: int
    pj: str = ""
    status_code: str = "other"
    status_label: str = "その他"
    status_note: str = ""
    done_date: str | None = None
    #: `butler` … 執事が手を動かす／`master` … 主人が手を動かす
    owner: str = "butler"
    #: ずっと居るが手は動いていない見張りか（`status_code == "resident"` の別名）。
    is_resident: bool = False


@dataclass
class DecidedItem:
    """C セクション（裁定済み）の1件。"""

    id: str
    decided: str
    title: str
    decision: str
    line: int
    has_detail: bool = False


@dataclass
class QueueDoc:
    path: Path
    exists: bool
    sha256: str = ""
    mtime: float = 0.0
    last_updated: str = ""
    pending: list[PendingItem] = field(default_factory=list)
    running: list[RunningItem] = field(default_factory=list)
    decided: list[DecidedItem] = field(default_factory=list)
    details: dict[str, DetailItem] = field(default_factory=dict)
    has_details_section: bool = False
    # A・B のどちらかに PJ 列があったか。無い＝移行前のフォーマット（画面の注記に使う）
    has_pj_column: bool = False
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "exists": self.exists,
            "sha256": self.sha256,
            "mtime": self.mtime,
            "last_updated": self.last_updated,
            "pending": [vars(i) for i in self.pending],
            "running": [vars(i) for i in self.running],
            "decided": [vars(i) for i in self.decided],
            "details": {k: vars(v) for k, v in self.details.items()},
            "has_details_section": self.has_details_section,
            "has_pj_column": self.has_pj_column,
            "errors": self.errors,
        }


def _read(path: Path) -> tuple[str, str, float]:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    return raw.decode("utf-8"), digest, path.stat().st_mtime


def _column(table: Table, name: str, fallback: int) -> int:
    idx = table.index_of(name)
    return idx if idx >= 0 else fallback


def _pj_column(table: Table) -> tuple[int, int]:
    """PJ 列の (列番号, 以降の列に足すオフセット) を返す。"""
    idx = table.index_of("PJ")
    return (idx, 1) if idx >= 0 else (-1, 0)


def _build_detail(item_id: str, heading: str, line: int, raw_fields: list[list[str]]) -> DetailItem | None:
    """収集した (ラベル, 本文) から 1件分の詳細を組み立てる。中身が空なら None。"""
    ordered: list[dict[str, str]] = []
    used: set[int] = set()
    for label in DETAIL_LABELS:  # 既定ラベルを先に、決まった順で
        for i, (raw_label, text) in enumerate(raw_fields):
            if i in used or raw_label != label:
                continue
            used.add(i)
            if text.strip():
                ordered.append({"label": label, "text": text.strip()})
            break
    for i, (raw_label, text) in enumerate(raw_fields):  # 未知のラベル・無ラベルは原文順
        if i in used or not text.strip():
            continue
        ordered.append({"label": raw_label, "text": text.strip()})

    if not ordered:
        return None

    item = DetailItem(id=item_id, heading=heading, fields=ordered, line=line)
    for entry in ordered:
        key = _DETAIL_KEYS.get(entry["label"])
        if key and not getattr(item, key):
            setattr(item, key, entry["text"])
    return item


def parse_details(lines: list[str], start: int, end: int) -> dict[str, DetailItem]:
    """D セクション本文 [start, end) から `### <ID>` ごとの詳細を読む。"""
    details: dict[str, DetailItem] = {}
    current_id: str | None = None
    current_heading = ""
    current_line = 0
    raw_fields: list[list[str]] = []
    in_fence = False

    def flush() -> None:
        nonlocal current_id, raw_fields
        if current_id and current_id not in details:
            item = _build_detail(current_id, current_heading, current_line, raw_fields)
            if item is not None:
                details[current_id] = item
        current_id = None
        raw_fields = []

    for i in range(start, min(end, len(lines))):
        line = lines[i]
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue

        if not in_fence:
            heading = _DETAIL_HEADING.match(line)
            if heading:
                flush()
                title = strip_md(heading.group(2))
                m = _DETAIL_HEADING_ID.match(title)
                current_id = m.group(1) if m else ""
                current_heading = title
                current_line = i
                continue

            if current_id is None:
                continue  # D 見出し直後のリード文などは無視する

            field_m = _DETAIL_FIELD.match(line)
            if field_m:
                raw_fields.append([strip_md(field_m.group(1)), field_m.group(2).strip()])
                continue

            if not line.strip():
                if raw_fields and raw_fields[-1][0] != "":
                    raw_fields.append(["", ""])  # 空行で継続を切る
                continue

            list_m = _DETAIL_LIST_ITEM.match(line)
            if list_m:  # ラベルの無い箇条書きは、独立した無ラベル項目として扱う
                raw_fields.append(["", list_m.group(1).strip()])
                continue
            text = line.strip()
        else:
            if current_id is None:
                continue
            text = line.strip()

        # 箇条書きでない行は直前の項目の続きとして足す。
        if raw_fields:
            raw_fields[-1][1] = join_wrapped(raw_fields[-1][1], text)
        else:
            raw_fields.append(["", text])

    flush()
    return details


def parse_queue(path: Path, today: date, stale_days: int = 3) -> QueueDoc:
    if not path.is_file():
        return QueueDoc(path=path, exists=False, errors=[f"ファイルが見つかりません: {path}"])

    text, digest, mtime = _read(path)
    lines, _ = split_lines(text)
    doc = QueueDoc(path=path, exists=True, sha256=digest, mtime=mtime)

    for line in lines[:20]:
        m = _LAST_UPDATED.match(line)
        if m:
            doc.last_updated = m.group(2).strip()
            break

    sec_a = mdtable.find_section(lines, SECTION_A)
    sec_b = mdtable.find_section(lines, SECTION_B)
    sec_c = mdtable.find_section(lines, SECTION_C)
    sec_d = mdtable.find_section(lines, SECTION_D)

    if sec_a is None:
        doc.errors.append("A セクション（主人待ち）の見出しが見つかりません")
    else:
        table = mdtable.parse_table(lines, sec_a.start, sec_a.end)
        if table is None:
            doc.errors.append("A セクションにテーブルが見つかりません")
        else:
            c_pj, off = _pj_column(table)
            doc.has_pj_column = doc.has_pj_column or c_pj >= 0
            c_id = _column(table, "#", 0)
            c_raised = _column(table, "起票", 1 + off)
            c_title = _column(table, "件名", 2 + off)
            c_reco = _column(table, "執事の推奨", 3 + off)
            c_risk = _column(table, "risk", 4 + off)
            c_status = _column(table, "状態", 5 + off)
            for row in table.rows:
                if row.is_placeholder():
                    continue
                raised_raw = row.cell(c_raised)
                raised_dt = parse_date(raised_raw)
                stale = (today - raised_dt).days if raised_dt else None
                risk_plain = strip_md(row.cell(c_risk)).lower()
                status = row.cell(c_status)
                code, note, done_on = normalize_status(status)
                doc.pending.append(
                    PendingItem(
                        id=strip_md(row.cell(c_id)),
                        raised=strip_md(raised_raw),
                        raised_date=raised_dt.isoformat() if raised_dt else None,
                        title=row.cell(c_title),
                        title_plain=strip_md(row.cell(c_title)),
                        recommendation=row.cell(c_reco),
                        risk=strip_md(row.cell(c_risk)),
                        risk_level=risk_plain if risk_plain in RISK_ORDER else "",
                        status=status,
                        stale_days=stale,
                        is_stale=bool(stale is not None and stale >= stale_days),
                        line=row.line,
                        pj=_pj_value(row.cell(c_pj)) if c_pj >= 0 else "",
                        status_code=code,
                        status_label=STATUS_LABELS[code],
                        status_note=note,
                        done_date=done_on,
                    )
                )

    if sec_b is None:
        doc.errors.append("B セクション（自走キュー）の見出しが見つかりません")
    else:
        table = mdtable.parse_table(lines, sec_b.start, sec_b.end)
        if table is None:
            doc.errors.append("B セクションにテーブルが見つかりません")
        else:
            c_pj, off = _pj_column(table)
            doc.has_pj_column = doc.has_pj_column or c_pj >= 0
            c_id = _column(table, "#", 0)
            c_content = _column(table, "内容", 1 + off)
            c_level = _column(table, "L", 2 + off)
            c_status = _column(table, "状態", 3 + off)
            for row in table.rows:
                if row.is_placeholder():
                    continue
                status = row.cell(c_status)
                code, note, done_on = normalize_status(status)
                owner = _detect_owner(row.cell(c_content), status)
                doc.running.append(
                    RunningItem(
                        id=strip_md(row.cell(c_id)),
                        content=_strip_owner_mark(row.cell(c_content)),
                        content_plain=_strip_owner_mark(strip_md(row.cell(c_content))),
                        level=strip_md(row.cell(c_level)),
                        status=status,
                        status_kind=_status_kind(status),
                        line=row.line,
                        pj=_pj_value(row.cell(c_pj)) if c_pj >= 0 else "",
                        status_code=code,
                        status_label=STATUS_LABELS[code],
                        status_note=note,
                        done_date=done_on,
                        owner=owner,
                        is_resident=code == "resident",
                    )
                )

    if sec_c is not None:
        table = mdtable.parse_table(lines, sec_c.start, sec_c.end)
        if table is not None:
            c_id = _column(table, "#", 0)
            c_date = _column(table, "裁定日", 1)
            c_title = _column(table, "件名", 2)
            c_decision = _column(table, "裁定", 3)
            for row in table.rows:
                if row.is_placeholder():
                    continue
                doc.decided.append(
                    DecidedItem(
                        id=strip_md(row.cell(c_id)),
                        decided=strip_md(row.cell(c_date)),
                        title=row.cell(c_title),
                        decision=row.cell(c_decision),
                        line=row.line,
                    )
                )

    # D は任意。存在しなくてもエラーにしない（後方互換）。
    if sec_d is not None:
        doc.has_details_section = True
        doc.details = parse_details(lines, sec_d.start, sec_d.end)
        for item in doc.pending:
            item.has_detail = item.id in doc.details
        for decided in doc.decided:
            decided.has_detail = decided.id in doc.details

    doc.pending.sort(
        key=lambda i: (
            -(i.stale_days or 0),
            RISK_ORDER.get(i.risk_level, 3),
            i.id,
        )
    )
    return doc
