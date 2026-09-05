"""`manor archive` — 追記ファイルに寿命を持たせる（ADR-006 §4）。

`CHANGELOG.md` / `butler/GROWTH.md` / `home/LOG.md` はどれも**追記のみ**で育つ①・②の
ファイルで、放っておくと際限なく太る（`manor check` の C10 が 40KB 超で警告する）。
このモジュールは、月境界を機械で見つけて古い月を別ファイルへ**移す**（消さない）。

## 3つの形

同じ「月で切る」操作でも、ファイルごとに月の切れ目の書式が違う（実物を読んで確認済み）。

| ファイル | 書式 | 例 |
|---|---|---|
| `CHANGELOG.md` | 見出し | `## 2026-09-02` |
| `butler/GROWTH.md` | 表の行 | `| G1 | 2026-09-02 | ... |` |
| `home/LOG.md` | 箇条書き | `- 2026-09-02 ...`（自由記述） |

`<path>` の**中身の形**で自動判定する（ファイル名では決め打たない。合成データでの
試験を素直に通すため）。どの形にも当たらなければ「対象外」（何もしない。エラーにしない）。

## 移し先

- `CHANGELOG.md`・`butler/GROWTH.md` 相当（`LOG.md` 以外）: `<path> から辿れる docs/ を
  持つ祖先>/docs/archive/<stem>-<YYYY-MM>.md`
- `LOG.md`: `<path の親>/archive/LOG-<YYYY-MM>.md`（`home/LOG.md` なら `home/archive/`）

元ファイルには、移した月のぶんだけ `<!-- archived: YYYY-MM → docs/archive/....md（N件） -->`
の1行索引を残す。**消さない・冪等**（2回目は移す月が無いので何もしない）。

`register(subparsers)` は将来 `src/manor/cli.py` への配線用の公開口（`src/manor/night/__init__.py`
と同じ形。配線は執事が行う）。それまでの起動口は `python -m manor.archive <path> ...`。
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from . import i18n, util

NAME = "archive"
LABEL = "アーカイブ"

_HEADING_DATE_RE = re.compile(r"^## (\d{4})-(\d{2})-(\d{2})\s*$")
_ANY_HEADING_RE = re.compile(r"^## ")
_BULLET_DATE_RE = re.compile(r"^- (\d{4})-(\d{2})-(\d{2})\b")
_TABLE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_INDEX_LINE_RE = re.compile(r"^<!-- archived: (\d{4}-\d{2}) →")


# --- 書式の判定・分割 ---------------------------------------------------------------


def _table_row_date(line: str) -> str | None:
    line = line.strip()
    if not (line.startswith("|") and line.endswith("|") and len(line) >= 2):
        return None
    cells = [c.strip() for c in line.strip("|").split("|")]
    for cell in cells:
        if _TABLE_DATE_RE.match(cell):
            return cell
    return None


def detect_format(text: str) -> str | None:
    """`"heading" | "table" | "bullet" | None`（対象外）。"""
    lines = text.splitlines()
    if any(_HEADING_DATE_RE.match(ln) for ln in lines):
        return "heading"
    if any(_table_row_date(ln) for ln in lines):
        return "table"
    if any(_BULLET_DATE_RE.match(ln) for ln in lines):
        return "bullet"
    return None


def _parse_heading_blocks(text: str) -> list[dict]:
    blocks: list[dict] = []
    buf: list[str] = []
    cur_month: str | None = None

    def flush() -> None:
        nonlocal buf, cur_month
        if buf:
            blocks.append({"kind": "entry" if cur_month else "keep", "month": cur_month, "text": "".join(buf)})
        buf = []
        cur_month = None

    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        if _INDEX_LINE_RE.match(stripped):
            # 索引行は必ずそれだけの keep ブロックにする（直前のエントリに飲み込まれると、
            # 次回の実行で `_flatten_for_rewrite` が見つけられなくなる）
            flush()
            blocks.append({"kind": "keep", "month": None, "text": line})
            continue
        m = _HEADING_DATE_RE.match(stripped)
        if _ANY_HEADING_RE.match(stripped):
            flush()
            cur_month = f"{m.group(1)}-{m.group(2)}" if m else None
        buf.append(line)
    flush()
    return blocks


def _parse_bullet_blocks(text: str) -> list[dict]:
    blocks: list[dict] = []
    buf: list[str] = []
    cur_month: str | None = None

    def flush() -> None:
        nonlocal buf, cur_month
        if buf:
            blocks.append({"kind": "entry" if cur_month else "keep", "month": cur_month, "text": "".join(buf)})
        buf = []
        cur_month = None

    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        if _INDEX_LINE_RE.match(stripped):
            # 索引行は必ずそれだけの keep ブロックにする(理由は heading 版と同じ)
            flush()
            blocks.append({"kind": "keep", "month": None, "text": line})
            continue
        m = _BULLET_DATE_RE.match(stripped)
        if m:
            flush()
            cur_month = f"{m.group(1)}-{m.group(2)}"
        buf.append(line)
    flush()
    return blocks


def _parse_table_blocks(text: str) -> list[dict]:
    blocks: list[dict] = []
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        if _INDEX_LINE_RE.match(stripped):
            # 索引行は直前の keep ブロックへ静かに合流させない(他の2形式と揃える)
            blocks.append({"kind": "keep", "month": None, "text": line})
            continue
        d = _table_row_date(stripped)
        if d:
            blocks.append({"kind": "entry", "month": d[:7], "text": line})
        elif blocks and blocks[-1]["kind"] == "keep":
            blocks[-1]["text"] += line
        else:
            blocks.append({"kind": "keep", "month": None, "text": line})
    return blocks


def _parse_blocks(text: str, fmt: str) -> list[dict]:
    if fmt == "heading":
        return _parse_heading_blocks(text)
    if fmt == "table":
        return _parse_table_blocks(text)
    if fmt == "bullet":
        return _parse_bullet_blocks(text)
    raise ValueError(i18n.t("error.archive.format_unknown", fmt=fmt))


# --- 月の算術 --------------------------------------------------------------------


def _month_add(ym: str, delta: int) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    idx = y * 12 + (m - 1) + delta
    y2, m2 = divmod(idx, 12)
    return f"{y2:04d}-{m2 + 1:02d}"


def _cutoff_month(keep_months: int, today_ym: str) -> str:
    keep = max(int(keep_months), 0)
    return _month_add(today_ym, 1 - keep)


# --- 移し先のパス ------------------------------------------------------------------


def _find_docs_root(start: Path, max_up: int = 3) -> Path:
    cur = start
    for _ in range(max_up + 1):
        if (cur / "docs").is_dir():
            return cur
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return start


def _destination(path: Path, month: str) -> Path:
    if path.name == "LOG.md":
        return path.parent / "archive" / f"LOG-{month}.md"
    root = _find_docs_root(path.parent)
    return root / "docs" / "archive" / f"{path.stem}-{month}.md"


def _describe_dest(path: Path, dest: Path) -> str:
    base = path.parent.parent if path.name == "LOG.md" else _find_docs_root(path.parent)
    try:
        return dest.relative_to(base).as_posix()
    except ValueError:
        return dest.as_posix()


# --- D19/D20: 追記・索引行の付け替え・表ヘッダの複製 -----------------------------------


def _table_header_lines(text: str) -> list[str]:
    """表形式（`fmt == "table"`）の保存先を**新規に作る**ときだけ複製するヘッダ行・区切り行。

    元ファイルで最初のエントリ行（どこかのセルが日付になっている行）の直前にある、
    `|` で始まる行の連続（典型的にはヘッダ行と区切り行の2行）を、出現順のまま返す。
    見つからなければ空リスト（複製しない）。
    """
    lines = text.splitlines(keepends=True)
    first_idx = None
    for i, line in enumerate(lines):
        if _table_row_date(line.rstrip("\r\n")):
            first_idx = i
            break
    if first_idx is None:
        return []
    header: list[str] = []
    j = first_idx - 1
    while j >= 0 and lines[j].strip().startswith("|"):
        header.insert(0, lines[j])
        j -= 1
    return header


def _dest_missing_trailing_newline(dest: Path) -> bool:
    """`dest` が既にあり、空でなく、末尾が改行(`\\n`)でなければ `True`。

    追記の直前にこれを見て改行を差し込まないと、保存先の最終行に改行が無いまま
    次のエントリをそのまま `open("ab")` で連結してしまい、2件が1行に融合する
    (かつ `_parse_blocks` で数えたときに1件と数えられて索引行の件数も嘘になる)。
    """
    try:
        size = dest.stat().st_size
    except OSError:
        return False
    if size == 0:
        return False
    with dest.open("rb") as f:
        f.seek(-1, 2)
        return f.read(1) != b"\n"


def _flatten_for_rewrite(blocks: list[dict]) -> list[tuple[str, str | None, str]]:
    """元ファイル書き換え用に、ブロック列を「行」単位へ平らにする。

    `entry` ブロックはそのまま1項目（移すときは丸ごと消す単位）。`keep` ブロックは
    行ごとに割り、`<!-- archived: YYYY-MM → ... -->` の索引行だけを `"index"` として
    見分ける（索引行は keep ブロックの中に埋もれているため、行単位まで割らないと
    既存の索引行をピンポイントで置き換えられない）。
    戻り値の要素は `(kind, month, raw_text)`。`kind` は `"entry"` / `"index"` / `"keep"`。
    """
    items: list[tuple[str, str | None, str]] = []
    for b in blocks:
        if b["kind"] == "entry":
            items.append(("entry", b["month"], b["text"]))
            continue
        for line in b["text"].splitlines(keepends=True):
            m = _INDEX_LINE_RE.match(line.rstrip("\r\n"))
            if m:
                items.append(("index", m.group(1), line))
            else:
                items.append(("keep", None, line))
    return items


# --- 本体 ------------------------------------------------------------------------


def apply(path: Path, *, keep_months: int = 1, today: str | None = None, dry_run: bool = False) -> dict[str, object]:
    """`<path>` を読み、`keep_months` より古い月を移す（`dry_run=True` なら計画だけ返す）。

    戻り値: `{"path","supported","format","reason","archived_months"(=[{month,count,dest}]),
    "kept_months","dry_run","written"}`
    """
    path = Path(path)
    out: dict[str, object] = {
        "path": str(path),
        "supported": False,
        "format": None,
        "reason": None,
        "archived_months": [],
        "kept_months": [],
        "dry_run": dry_run,
        "written": False,
    }
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        out["reason"] = i18n.t("error.archive.cannot_read", exc=str(exc))
        return out

    fmt = detect_format(text)
    if fmt is None:
        out["reason"] = i18n.t("error.archive.format_not_supported")
        return out
    out["supported"] = True
    out["format"] = fmt

    blocks = _parse_blocks(text, fmt)
    entry_months = sorted({b["month"] for b in blocks if b["kind"] == "entry"})
    if not entry_months:
        out["reason"] = i18n.t("error.archive.no_dated_entries")
        return out

    today_ym = (today or util.today())[:7]
    cutoff = _cutoff_month(keep_months, today_ym)
    archived_set = {m for m in entry_months if m < cutoff}
    kept_set = {m for m in entry_months if m >= cutoff}
    out["kept_months"] = sorted(kept_set)

    if not archived_set:
        out["reason"] = i18n.t("error.archive.no_target_months")
        return out

    counts: dict[str, int] = {}
    dest_map: dict[str, Path] = {}
    archived_text: dict[str, list[str]] = {}
    for b in blocks:
        if b["kind"] == "entry" and b["month"] in archived_set:
            m = b["month"]
            counts[m] = counts.get(m, 0) + 1
            archived_text.setdefault(m, []).append(b["text"])
            dest_map.setdefault(m, _destination(path, m))

    out["archived_months"] = [
        {"month": m, "count": counts[m], "dest": str(dest_map[m])} for m in sorted(archived_set)
    ]

    if dry_run:
        return out

    # --- 1. 保存先を全部書く(既にあれば追記、無ければ新規。表形式はヘッダ行を複製) -----------
    # ここで例外が飛んだら、元ファイルにはまだ一切触れていないので安全に伝播させる。
    header_lines = _table_header_lines(text) if fmt == "table" else []
    for m in sorted(archived_set):
        dest = dest_map[m]
        dest.parent.mkdir(parents=True, exist_ok=True)
        is_new = not dest.exists()
        needs_leading_newline = (not is_new) and _dest_missing_trailing_newline(dest)
        content = "".join(archived_text[m])
        if is_new and fmt == "table" and header_lines:
            content = "".join(header_lines) + content
        if not content.endswith("\n"):
            content += "\n"  # 元ファイルの最終行に改行が無かった場合の穴埋め(次回以降の融合を防ぐ)
        # バイナリ追記(Windows で "\n" が "\r\n" に化けるのを防ぐ。render.py / handoff.py と同じ理由・同じ流儀)
        with dest.open("ab") as f:
            if needs_leading_newline:
                # 保存先の末尾に改行が無いまま連結すると2件が1行に融合するため先に補う
                f.write(b"\n")
            f.write(content.encode("utf-8"))
            f.flush()

    # --- 2. 保存先を全部書き終えてから読み直し、累計件数を確定する(D19) --------------------
    for m in sorted(archived_set):
        dest_text = dest_map[m].read_text(encoding="utf-8")
        dest_blocks = _parse_blocks(dest_text, fmt)
        counts[m] = sum(1 for bb in dest_blocks if bb["kind"] == "entry")
    out["archived_months"] = [
        {"month": m, "count": counts[m], "dest": str(dest_map[m])} for m in sorted(archived_set)
    ]

    # --- 3. 元ファイルを書き換える -------------------------------------------------------
    # 既にその月の索引行があればその位置を置き換え、無ければ最初のエントリの位置に置く
    # (索引行は keep ブロックの中に埋もれているので、行単位まで割ってから探す)。
    items = _flatten_for_rewrite(blocks)

    replace_pos: dict[str, int] = {}
    first_entry_pos: dict[str, int] = {}
    for i, (kind, month, _raw) in enumerate(items):
        if kind == "index" and month in archived_set and month not in replace_pos:
            replace_pos[month] = i
        if kind == "entry" and month in archived_set and month not in first_entry_pos:
            first_entry_pos[month] = i

    placeholder_pos = {m: replace_pos.get(m, first_entry_pos[m]) for m in archived_set}
    pos_to_month = {pos: m for m, pos in placeholder_pos.items()}

    new_parts: list[str] = []
    for i, (kind, month, raw) in enumerate(items):
        if i in pos_to_month:
            m = pos_to_month[i]
            rel = _describe_dest(path, dest_map[m])
            new_parts.append(f"<!-- archived: {m} → {rel}（{counts[m]}件） -->\n")
        if kind == "entry" and month in archived_set:
            continue  # 移した(保存先へ書いた)ので元ファイルからは消える
        if kind == "index" and month in archived_set:
            continue  # 上で新しい索引行に置き換え済み(同じ月を2本にしない)
        new_parts.append(raw)

    # 一時ファイルへ書いてから os.replace で入れ替える(保存先の書き込みが全部済んだ後だけ
    # ここに来るので、失敗時に元ファイルが半端な状態になることはない)。
    new_text = "".join(new_parts)
    tmp_path = path.parent / f"{path.name}.tmp"
    tmp_path.write_bytes(new_text.encode("utf-8"))  # write_text は Windows で \n を \r\n に変換するため使わない
    os.replace(tmp_path, path)
    out["written"] = True
    return out


# --- CLI 配線 --------------------------------------------------------------------


def _add_common_args(p: "argparse.ArgumentParser") -> None:
    p.add_argument("path")
    p.add_argument("--keep-months", type=int, default=1, dest="keep_months")
    p.add_argument("--dry-run", action="store_true", dest="dry_run")
    p.add_argument("--json", action="store_true")


def register(subparsers: "argparse._SubParsersAction") -> None:
    """`manor archive <path> [--keep-months N] [--dry-run] [--json]` を足す。"""
    p = subparsers.add_parser("archive", help=i18n.t("cli.archive.help"))
    _add_common_args(p)
    p.set_defaults(func=_cmd_archive, is_write=False, needs_db=False)


def _cmd_archive(args: "argparse.Namespace") -> int:
    result = apply(Path(args.path), keep_months=args.keep_months, dry_run=args.dry_run)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if not result["supported"]:
        print(i18n.t("archive.cli.no_reason", reason=result["reason"]))
        return 0
    if not result["archived_months"]:
        print(str(result.get("reason") or i18n.t("archive.cli.no_target_default")))
        return 0

    tag = i18n.t("archive.cli.dry_run_tag") if args.dry_run else ""
    print(i18n.t("archive.cli.format_line", format=result["format"], tag=tag))
    for row in result["archived_months"]:
        print(i18n.t("archive.cli.row_line", month=row["month"], dest=row["dest"], count=row["count"]))
    if result["written"]:
        print(i18n.t("archive.cli.written"))
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        import sys

        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass
    parser = argparse.ArgumentParser(prog="python -m manor.archive", description=i18n.t("cli.archive.help"))
    _add_common_args(parser)
    args = parser.parse_args(argv)
    return _cmd_archive(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
