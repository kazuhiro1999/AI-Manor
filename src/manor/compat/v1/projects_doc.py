"""v1 butler-board からのコピー。読む側だけ。

出所: `apps/butler-board/src/butler_board/projects_doc.py`（AI執事 v1）。
元のファイルは PROJECTS.md と STATE.md の両方を読んでいたが、ADR-003 の対応表は
PROJECTS.md（`parse_projects`）だけを扱うので、STATE.md 側（`parse_state` /
`StateDoc` / 現フェーズ・次のアクション・ブロッカーの見出し）は持ち込んでいない。
元々このファイルに書き戻し用の副作用は無い（純粋なパーサ）。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import mdtable
from .mdtable import Table, split_lines
from .queue_doc import strip_md

SECTION_MILESTONE = re.compile(r"マイルストーン")
SECTION_OVERVIEW = re.compile(r"^\s*俯瞰")
SECTION_RELAY = re.compile(r"伝達キュー")

_FULL_DATE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_YEAR = re.compile(r"(\d{4})")
_MONTH_DAY = re.compile(r"(?<!\d)(\d{1,2})-(\d{1,2})(?!\d)")
_FUZZY = ("下旬", "中旬", "上旬", "頃", "仮", "約", "〜", "~", "ごろ", "未定")
_STARS = re.compile(r"★+")


def resolve_milestone_date(text: str) -> tuple[date | None, bool]:
    """「2026-09下旬（仮 09-25）」のような表記から日付を推定する。

    戻り値は (日付, 概算フラグ)。決められない場合は (None, True)。
    """
    approx = any(token in text for token in _FUZZY)
    m = _FULL_DATE.search(text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))), approx
        except ValueError:
            return None, True

    year_m = _YEAR.search(text)
    md_m = _MONTH_DAY.search(text)
    if year_m and md_m:
        try:
            return date(int(year_m.group(1)), int(md_m.group(1)), int(md_m.group(2))), True
        except ValueError:
            return None, True
    return None, True


@dataclass
class Milestone:
    date_text: str
    date_iso: str | None
    approximate: bool
    title: str
    title_plain: str
    remaining_text: str
    days_left: int | None
    line: int


@dataclass
class Project:
    id: str
    name: str
    category: str
    priority: str
    priority_rank: int
    status: str
    status_plain: str
    next_action: str
    deadline: str
    line: int


@dataclass
class RelayItem:
    id: str
    to: str
    content: str
    origin: str
    status: str
    line: int


@dataclass
class ProjectsDoc:
    path: Path
    exists: bool
    sha256: str = ""
    mtime: float = 0.0
    last_updated: str = ""
    milestones: list[Milestone] = field(default_factory=list)
    projects: list[Project] = field(default_factory=list)
    relays: list[RelayItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "exists": self.exists,
            "sha256": self.sha256,
            "mtime": self.mtime,
            "last_updated": self.last_updated,
            "milestones": [vars(m) for m in self.milestones],
            "projects": [vars(p) for p in self.projects],
            "relays": [vars(r) for r in self.relays],
            "errors": self.errors,
        }


def _column(table: Table, name: str, fallback: int) -> int:
    idx = table.index_of(name)
    return idx if idx >= 0 else fallback


def _read(path: Path) -> tuple[str, str, float]:
    raw = path.read_bytes()
    return raw.decode("utf-8"), hashlib.sha256(raw).hexdigest(), path.stat().st_mtime


def parse_projects(path: Path, today: date) -> ProjectsDoc:
    if not path.is_file():
        return ProjectsDoc(path=path, exists=False, errors=[f"ファイルが見つかりません: {path}"])

    text, digest, mtime = _read(path)
    lines, _ = split_lines(text)
    doc = ProjectsDoc(path=path, exists=True, sha256=digest, mtime=mtime)

    for line in lines[:20]:
        m = re.match(r"^\s*>?\s*最終更新:\s*(.*)$", line)
        if m:
            doc.last_updated = m.group(1).strip()
            break

    sec = mdtable.find_section(lines, SECTION_MILESTONE)
    if sec is None:
        doc.errors.append("マイルストーンの見出しが見つかりません")
    else:
        table = mdtable.parse_table(lines, sec.start, sec.end)
        if table is None:
            doc.errors.append("マイルストーンのテーブルが見つかりません")
        else:
            c_date = _column(table, "日付", 0)
            c_title = _column(table, "予定", 1)
            c_rest = _column(table, "残", 2)
            for row in table.rows:
                if row.is_placeholder():
                    continue
                date_text = strip_md(row.cell(c_date))
                resolved, approx = resolve_milestone_date(date_text)
                doc.milestones.append(
                    Milestone(
                        date_text=date_text,
                        date_iso=resolved.isoformat() if resolved else None,
                        approximate=approx,
                        title=row.cell(c_title),
                        title_plain=strip_md(row.cell(c_title)),
                        remaining_text=strip_md(row.cell(c_rest)),
                        days_left=(resolved - today).days if resolved else None,
                        line=row.line,
                    )
                )
            doc.milestones.sort(key=lambda m: (m.days_left is None, m.days_left or 0))

    sec = mdtable.find_section(lines, SECTION_OVERVIEW)
    if sec is None:
        doc.errors.append("俯瞰の見出しが見つかりません")
    else:
        table = mdtable.parse_table(lines, sec.start, sec.end)
        if table is None:
            doc.errors.append("俯瞰のテーブルが見つかりません")
        else:
            c_id = _column(table, "ID", 0)
            c_name = _column(table, "プロジェクト", 1)
            c_cat = _column(table, "区分", 2)
            c_pri = _column(table, "優先度", 3)
            c_status = _column(table, "状態", 4)
            c_next = _column(table, "次の一手", 5)
            c_due = _column(table, "期限", 6)
            for row in table.rows:
                if row.is_placeholder():
                    continue
                priority = strip_md(row.cell(c_pri))
                stars = _STARS.search(priority)
                doc.projects.append(
                    Project(
                        id=strip_md(row.cell(c_id)),
                        name=row.cell(c_name),
                        category=strip_md(row.cell(c_cat)),
                        priority=priority,
                        priority_rank=len(stars.group(0)) if stars else 0,
                        status=row.cell(c_status),
                        status_plain=strip_md(row.cell(c_status)),
                        next_action=row.cell(c_next),
                        deadline=strip_md(row.cell(c_due)),
                        line=row.line,
                    )
                )

    sec = mdtable.find_section(lines, SECTION_RELAY)
    if sec is not None:
        table = mdtable.parse_table(lines, sec.start, sec.end)
        if table is not None:
            for row in table.rows:
                if row.is_placeholder():
                    continue
                doc.relays.append(
                    RelayItem(
                        id=strip_md(row.cell(0)),
                        to=strip_md(row.cell(1)),
                        content=row.cell(2),
                        origin=strip_md(row.cell(3)),
                        status=row.cell(4),
                        line=row.line,
                    )
                )
    return doc
