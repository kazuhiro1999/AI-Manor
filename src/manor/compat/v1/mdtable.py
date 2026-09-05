"""v1 butler-board からのコピー。読む側だけ。

出所: `apps/butler-board/src/butler_board/mdtable.py`（AI執事 v1）。
このファイルには元々書き戻し用の副作用は無い（純粋なパーサ）。
ADR-003 D1 のとおり書き直さずにそのままコピーしている。

---

Markdown の「行」を保ったまま扱うための最小パーサ。

このアプリは QUEUE.md を書き戻す。書き戻しを「パース→全体再生成」でやると
見出し・引用ブロック・運用メモ・強調記法など、モデル化していない要素を必ず失う。
そこで本モジュールは **各セルの値と同時に「元ファイルの何行目か」を保持** し、
書き戻し側はその行だけを差し替え／削除／挿入できるようにしている。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# エスケープされていないパイプで分割する
_CELL_SPLIT = re.compile(r"(?<!\\)\|")
_SEPARATOR_CELL = re.compile(r"^:?-{1,}:?$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")

# 「空」とみなすプレースホルダ（| — | — | — |）
PLACEHOLDER_CELLS = {"", "—", "-", "ー", "–", "n/a", "なし"}


@dataclass
class Row:
    """テーブルの1行。line は 0 始まりの行番号。"""

    cells: list[str]
    line: int
    raw: str

    def cell(self, index: int, default: str = "") -> str:
        if 0 <= index < len(self.cells):
            return self.cells[index]
        return default

    def is_placeholder(self) -> bool:
        return all(c.strip().lower() in PLACEHOLDER_CELLS for c in self.cells)


@dataclass
class Table:
    header: list[str]
    header_line: int
    separator_line: int
    rows: list[Row] = field(default_factory=list)

    @property
    def last_line(self) -> int:
        return self.rows[-1].line if self.rows else self.separator_line

    def index_of(self, column_name: str) -> int:
        """ヘッダ名から列番号を引く（前方一致・見つからなければ -1）。"""
        target = column_name.strip()
        for i, name in enumerate(self.header):
            if name.strip() == target:
                return i
        for i, name in enumerate(self.header):
            if target and target in name:
                return i
        return -1


@dataclass
class Section:
    """見出し1つ分のブロック。"""

    level: int
    title: str
    heading_line: int
    start: int  # 本文の開始行（見出しの次の行）
    end: int  # 本文の終了行（この行は含まない）


def split_lines(text: str) -> tuple[list[str], str]:
    """改行コードを保存したまま行に分ける。戻り値は (行リスト, 改行コード)。"""
    eol = "\r\n" if "\r\n" in text else "\n"
    normalized = text.replace("\r\n", "\n")
    return normalized.split("\n"), eol


def join_lines(lines: list[str], eol: str) -> str:
    return eol.join(lines)


def is_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.count("|") >= 2


def split_cells(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|") and not stripped.endswith("\\|"):
        stripped = stripped[:-1]
    return [c.strip() for c in _CELL_SPLIT.split(stripped)]


def is_separator_line(line: str) -> bool:
    if not is_table_line(line):
        return False
    cells = split_cells(line)
    return bool(cells) and all(_SEPARATOR_CELL.match(c.replace(" ", "")) for c in cells)


def format_row(cells: list[str]) -> str:
    """セル値からテーブル行を組み立てる。値に含まれる `|` はエスケープする。"""
    escaped = [str(c).replace("\\|", "|").replace("|", "\\|").strip() for c in cells]
    return "| " + " | ".join(escaped) + " |"


def find_sections(lines: list[str]) -> list[Section]:
    """見出しを列挙する。end は「同じかそれより浅い見出し」の直前まで。"""
    heads: list[tuple[int, int, str]] = []
    in_fence = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING.match(line)
        if m:
            heads.append((i, len(m.group(1)), m.group(2).strip()))

    sections: list[Section] = []
    for idx, (line_no, level, title) in enumerate(heads):
        end = len(lines)
        for next_line, next_level, _ in heads[idx + 1 :]:
            if next_level <= level:
                end = next_line
                break
        sections.append(
            Section(level=level, title=title, heading_line=line_no, start=line_no + 1, end=end)
        )
    return sections


def find_section(lines: list[str], pattern: str | re.Pattern[str]) -> Section | None:
    regex = re.compile(pattern) if isinstance(pattern, str) else pattern
    for section in find_sections(lines):
        if regex.search(section.title):
            return section
    return None


def parse_table(lines: list[str], start: int, end: int) -> Table | None:
    """[start, end) の範囲にある最初のテーブルを読む。

    テーブルは「ヘッダ行 + 区切り行 + 連続する本体行」。本体行が途切れた時点で終了する
    （表の後ろに引用ブロックや箇条書きが続いても巻き込まない）。
    """
    i = start
    while i < end - 1:
        if is_table_line(lines[i]) and is_separator_line(lines[i + 1]):
            header = split_cells(lines[i])
            table = Table(header=header, header_line=i, separator_line=i + 1)
            j = i + 2
            while j < end and is_table_line(lines[j]):
                if not is_separator_line(lines[j]):
                    table.rows.append(Row(cells=split_cells(lines[j]), line=j, raw=lines[j]))
                j += 1
            return table
        i += 1
    return None
