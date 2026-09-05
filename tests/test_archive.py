"""`manor archive`（ADR-006 §4）。合成データだけを使う。本物のリポジトリには一切触れない。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from manor import archive as archive_mod

TODAY = "2026-09-03"  # 3か月分の合成データと揃えた固定日付（時計は必ず固定する）


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


CHANGELOG_3M = """# CHANGELOG

## 他の記録との住み分け

前置きは動かさない。

## 2026-07-01

### 追加: 何か1

説明1

## 2026-08-01

### 追加: 何か2

説明2

## 2026-09-01

### 追加: 何か3

説明3
"""


def test_changelog_heading_format_keep1_moves_2_months(tmp_path: Path):
    path = tmp_path / "CHANGELOG.md"
    _write(path, CHANGELOG_3M)

    result = archive_mod.apply(path, keep_months=1, today=TODAY)

    assert result["supported"]
    assert result["format"] == "heading"
    assert result["written"]
    archived_months = {row["month"] for row in result["archived_months"]}
    assert archived_months == {"2026-07", "2026-08"}
    assert result["kept_months"] == ["2026-09"]

    text = path.read_text(encoding="utf-8")
    assert "前置きは動かさない。" in text  # 見出しでない節は動かさない
    assert "## 2026-09-01" in text
    assert "## 2026-07-01" not in text
    assert "## 2026-08-01" not in text
    assert "<!-- archived: 2026-07" in text
    assert "<!-- archived: 2026-08" in text

    for month in ("2026-07", "2026-08"):
        dest = tmp_path / "docs" / "archive" / f"CHANGELOG-{month}.md"
        assert dest.is_file(), f"{dest} が作られていません"
        assert f"### 追加: 何か" in dest.read_text(encoding="utf-8")


def test_dry_run_does_not_write(tmp_path: Path):
    path = tmp_path / "CHANGELOG.md"
    original = CHANGELOG_3M
    _write(path, original)

    result = archive_mod.apply(path, keep_months=1, today=TODAY, dry_run=True)

    assert result["dry_run"] is True
    assert result["written"] is False
    assert {row["month"] for row in result["archived_months"]} == {"2026-07", "2026-08"}
    # 元ファイルは一切変わっていない
    assert path.read_text(encoding="utf-8") == original
    # 宛先ファイルも作られていない
    assert not (tmp_path / "docs" / "archive").exists()


def test_idempotent_second_run_does_nothing(tmp_path: Path):
    path = tmp_path / "CHANGELOG.md"
    _write(path, CHANGELOG_3M)

    first = archive_mod.apply(path, keep_months=1, today=TODAY)
    assert first["written"]
    text_after_first = path.read_text(encoding="utf-8")

    second = archive_mod.apply(path, keep_months=1, today=TODAY)
    assert second["written"] is False
    assert second["archived_months"] == []
    # 何も変わっていない（2回目は移す月が無い）
    assert path.read_text(encoding="utf-8") == text_after_first


GROWTH_TABLE = """# GROWTH.md

## 観測の視点

- 何か

## 台帳

| # | 日付 | 観測 | 仮説 | 打った手 | 検証 |
|---|------|------|------|----------|------|
| G1 | 2026-07-02 | obs1 | hyp1 | fix1 | ver1 |
| G2 | 2026-08-02 | obs2 | hyp2 | fix2 | ver2 |
| G3 | 2026-09-02 | obs3 | hyp3 | fix3 | ver3 |
"""


def test_growth_table_format(tmp_path: Path):
    # 実物のレイアウトに合わせ、docs/ を butler/ の兄弟に置く
    (tmp_path / "docs").mkdir()
    path = tmp_path / "butler" / "GROWTH.md"
    _write(path, GROWTH_TABLE)

    result = archive_mod.apply(path, keep_months=1, today=TODAY)

    assert result["format"] == "table"
    assert {row["month"] for row in result["archived_months"]} == {"2026-07", "2026-08"}

    text = path.read_text(encoding="utf-8")
    assert "| G3 | 2026-09-02 |" in text
    assert "| G1 |" not in text
    assert "| G2 |" not in text
    assert "## 観測の視点" in text  # 表以外の節は動かさない
    assert "| # | 日付 |" in text  # ヘッダ行は残る

    dest7 = tmp_path / "docs" / "archive" / "GROWTH-2026-07.md"
    assert dest7.is_file()
    assert "G1" in dest7.read_text(encoding="utf-8")


LOG_BULLETS = """# LOG

- 2026-07-03 何かした
  詳細1行目
- 2026-08-03 何かした2
- 2026-09-03 何かした3
"""


def test_log_bullet_format(tmp_path: Path):
    path = tmp_path / "home" / "LOG.md"
    _write(path, LOG_BULLETS)

    result = archive_mod.apply(path, keep_months=1, today=TODAY)

    assert result["format"] == "bullet"
    assert {row["month"] for row in result["archived_months"]} == {"2026-07", "2026-08"}

    text = path.read_text(encoding="utf-8")
    assert "- 2026-09-03 何かした3" in text
    assert "- 2026-07-03" not in text
    assert "詳細1行目" not in text  # 複数行に渡る記述もまとめて移る
    assert "home/archive/LOG-2026-07.md" in text  # 索引の相対パス表記

    dest = path.parent / "archive" / "LOG-2026-07.md"
    assert dest.is_file()
    body = dest.read_text(encoding="utf-8")
    assert "何かした" in body
    assert "詳細1行目" in body


def test_unsupported_format_does_nothing(tmp_path: Path):
    path = tmp_path / "NOTES.md"
    original = "# ただのメモ\n\n日付らしきものはありません。\n"
    _write(path, original)

    result = archive_mod.apply(path, keep_months=1, today=TODAY)

    assert result["supported"] is False
    assert result["reason"]
    assert path.read_text(encoding="utf-8") == original
    assert not (tmp_path / "docs").exists()


def test_missing_file_is_unsupported_not_error(tmp_path: Path):
    path = tmp_path / "GHOST.md"
    result = archive_mod.apply(path, keep_months=1, today=TODAY)
    assert result["supported"] is False
    assert result["reason"]


def test_keep_months_larger_keeps_more(tmp_path: Path):
    path = tmp_path / "CHANGELOG.md"
    _write(path, CHANGELOG_3M)

    result = archive_mod.apply(path, keep_months=3, today=TODAY)

    # 07・08・09 の3か月とも keep-months=3 の範囲内なので、何も動かさない
    assert result["archived_months"] == []
    assert result["written"] is False
    assert "## 2026-07-01" in path.read_text(encoding="utf-8")


# --- D19/D20: 再レビューへの裁定 ------------------------------------------------------


def _index_line_count(text: str, month: str) -> int:
    return len(re.findall(rf"<!-- archived: {re.escape(month)} →", text))


def test_rearchive_appends_backdated_entries_heading(tmp_path: Path):
    path = tmp_path / "CHANGELOG.md"
    _write(path, CHANGELOG_3M)

    archive_mod.apply(path, keep_months=1, today=TODAY)

    # 遡って7月付けの新しいエントリを追記する運用(夜勤の前日報告の穴埋めなど)
    backdated = "\n## 2026-07-20\n\n### 追加: 遅れて追加\n\n説明4\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(backdated)

    result = archive_mod.apply(path, keep_months=1, today=TODAY)
    assert result["written"]

    text = path.read_text(encoding="utf-8")
    assert _index_line_count(text, "2026-07") == 1  # 索引行は2本にならない
    assert "<!-- archived: 2026-07 → docs/archive/CHANGELOG-2026-07.md（2件） -->" in text
    assert "遅れて追加" not in text  # 新しいエントリも移した後は元ファイルに残らない

    dest = tmp_path / "docs" / "archive" / "CHANGELOG-2026-07.md"
    dest_text = dest.read_text(encoding="utf-8")
    assert "### 追加: 何か1" in dest_text  # 旧エントリ
    assert "### 追加: 遅れて追加" in dest_text  # 新エントリ
    assert dest_text.count("### 追加: 遅れて追加") == 1  # 重複していない


def test_rearchive_appends_backdated_entries_bullet(tmp_path: Path):
    path = tmp_path / "home" / "LOG.md"
    _write(path, LOG_BULLETS)

    archive_mod.apply(path, keep_months=1, today=TODAY)

    backdated = "- 2026-07-20 遅れて追加\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(backdated)

    result = archive_mod.apply(path, keep_months=1, today=TODAY)
    assert result["written"]

    text = path.read_text(encoding="utf-8")
    assert _index_line_count(text, "2026-07") == 1
    assert "<!-- archived: 2026-07 → home/archive/LOG-2026-07.md（2件） -->" in text
    assert "遅れて追加" not in text

    dest = path.parent / "archive" / "LOG-2026-07.md"
    dest_text = dest.read_text(encoding="utf-8")
    assert "何かした" in dest_text  # 旧エントリ
    assert "遅れて追加" in dest_text  # 新エントリ
    assert dest_text.count("遅れて追加") == 1


def test_rearchive_appends_backdated_entries_table(tmp_path: Path):
    (tmp_path / "docs").mkdir()
    path = tmp_path / "butler" / "GROWTH.md"
    _write(path, GROWTH_TABLE)

    archive_mod.apply(path, keep_months=1, today=TODAY)

    backdated = "| G4 | 2026-07-20 | obs4 | hyp4 | fix4 | ver4 |\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(backdated)

    result = archive_mod.apply(path, keep_months=1, today=TODAY)
    assert result["written"]

    text = path.read_text(encoding="utf-8")
    assert _index_line_count(text, "2026-07") == 1
    assert "（2件）" in text
    assert "G4" not in text

    dest = tmp_path / "docs" / "archive" / "GROWTH-2026-07.md"
    dest_text = dest.read_text(encoding="utf-8")
    assert "G1" in dest_text
    assert "G4" in dest_text
    assert dest_text.count("G4") == 1


def test_rearchive_twice_without_new_entries_is_noop(tmp_path: Path):
    path = tmp_path / "CHANGELOG.md"
    _write(path, CHANGELOG_3M)

    archive_mod.apply(path, keep_months=1, today=TODAY)
    with path.open("a", encoding="utf-8") as f:
        f.write("\n## 2026-07-20\n\n### 追加: 遅れて追加\n\n説明4\n")
    archive_mod.apply(path, keep_months=1, today=TODAY)

    text_after_second = path.read_text(encoding="utf-8")

    third = archive_mod.apply(path, keep_months=1, today=TODAY)
    assert third["written"] is False
    assert third["archived_months"] == []
    assert path.read_text(encoding="utf-8") == text_after_second  # バイト単位で同一


def test_table_destination_has_header_and_separator(tmp_path: Path):
    (tmp_path / "docs").mkdir()
    path = tmp_path / "butler" / "GROWTH.md"
    _write(path, GROWTH_TABLE)

    archive_mod.apply(path, keep_months=1, today=TODAY)

    dest = tmp_path / "docs" / "archive" / "GROWTH-2026-07.md"
    lines = dest.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("|")
    assert lines[1].startswith("|")
    assert re.match(r"^\|[\s:-]+\|", lines[1])

    # 2回目(追記)ではヘッダを複製しない --> ヘッダは1本のまま
    with path.open("a", encoding="utf-8") as f:
        f.write("| G4 | 2026-07-20 | obs4 | hyp4 | fix4 | ver4 |\n")
    archive_mod.apply(path, keep_months=1, today=TODAY)

    dest_text2 = dest.read_text(encoding="utf-8")
    assert dest_text2.count("| # | 日付 |") == 1
    assert dest_text2.count("|---|") == 1


def test_source_untouched_when_destination_write_fails(tmp_path: Path):
    path = tmp_path / "CHANGELOG.md"
    _write(path, CHANGELOG_3M)
    original = path.read_text(encoding="utf-8")

    # 保存先ディレクトリになるはずの場所に、あらかじめ「ただのファイル」を置いて
    # 保存先への書き込み(mkdir)を失敗させる。
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "archive").write_text("じゃま", encoding="utf-8")

    with pytest.raises(OSError):
        archive_mod.apply(path, keep_months=1, today=TODAY)

    assert path.read_text(encoding="utf-8") == original  # 元ファイルは一切変わっていない
    assert not (docs_dir / "archive" / "CHANGELOG-2026-07.md").exists()


# --- 検分で見つかった不具合(索引行がエントリに飲み込まれる／CRLF) ----------------------------
#
# 「残るエントリが先、アーカイブされるエントリが後」という並びだと、索引行を挿入する位置の
# 直前で cur_month がまだ有効なまま(直前のエントリの続き)になり、旧実装ではその索引行が
# 直前のエントリの buf に飲み込まれて "entry" ブロックの一部になっていた。この並びを固定した
# 合成データで再現する(CLI での再現手順と揃えた)。

LOG_KEPT_FIRST = """# LOG

- 2026-09-01 九月
- 2026-07-10 七月A
  続き行
- 2026-07-05 七月B
"""

CHANGELOG_KEPT_FIRST = """# CHANGELOG

## 2026-09-01

### 追加: 九月

説明9

## 2026-07-10

### 追加: 七月A

説明7A

## 2026-07-05

### 追加: 七月B

説明7B
"""


def test_index_line_not_swallowed_by_kept_entry_bullet(tmp_path: Path):
    path = tmp_path / "home" / "LOG.md"
    _write(path, LOG_KEPT_FIRST)

    archive_mod.apply(path, keep_months=1, today=TODAY)
    with path.open("a", encoding="utf-8") as f:
        f.write("- 2026-07-20 七月C（遡って追記）\n")
    result = archive_mod.apply(path, keep_months=1, today=TODAY)
    assert result["written"]

    text = path.read_text(encoding="utf-8")
    assert _index_line_count(text, "2026-07") == 1  # 索引行が2本に増えていない
    assert "（3件）" in text  # 七月A・七月B・七月C の累計

    dest = tmp_path / "home" / "archive" / "LOG-2026-07.md"
    dest_text = dest.read_text(encoding="utf-8")
    assert "七月A" in dest_text
    assert "七月B" in dest_text
    assert "七月C" in dest_text
    assert dest_text.count("七月C") == 1


def test_index_line_not_swallowed_by_kept_entry_heading(tmp_path: Path):
    path = tmp_path / "CHANGELOG.md"
    _write(path, CHANGELOG_KEPT_FIRST)

    archive_mod.apply(path, keep_months=1, today=TODAY)
    with path.open("a", encoding="utf-8") as f:
        f.write("\n## 2026-07-20\n\n### 追加: 七月C（遡って追記）\n\n説明7C\n")
    result = archive_mod.apply(path, keep_months=1, today=TODAY)
    assert result["written"]

    text = path.read_text(encoding="utf-8")
    assert _index_line_count(text, "2026-07") == 1
    assert "（3件）" in text

    dest = tmp_path / "docs" / "archive" / "CHANGELOG-2026-07.md"
    dest_text = dest.read_text(encoding="utf-8")
    assert "### 追加: 七月A" in dest_text
    assert "### 追加: 七月B" in dest_text
    assert "### 追加: 七月C（遡って追記）" in dest_text
    assert dest_text.count("七月C") == 1


def test_no_crlf_in_written_files(tmp_path: Path):
    path = tmp_path / "CHANGELOG.md"
    _write(path, CHANGELOG_3M)

    archive_mod.apply(path, keep_months=1, today=TODAY)
    with path.open("a", encoding="utf-8") as f:
        f.write("\n## 2026-07-20\n\n### 追加: 遅れて追加\n\n説明4\n")
    archive_mod.apply(path, keep_months=1, today=TODAY)

    assert b"\r" not in path.read_bytes()
    dest = tmp_path / "docs" / "archive" / "CHANGELOG-2026-07.md"
    assert b"\r" not in dest.read_bytes()


# --- 3周目のレビューで見つかった穴(保存先の末尾に改行が無いまま追記すると2件が1行に融合する) ---

LOG_FOR_FUSION_TEST = """# LOG

- 2026-09-01 九月
- 2026-07-10 七月2
"""


def test_dest_missing_trailing_newline_does_not_fuse_entries(tmp_path: Path):
    path = tmp_path / "home" / "LOG.md"
    _write(path, LOG_FOR_FUSION_TEST)

    dest = path.parent / "archive" / "LOG-2026-07.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    # 過去のツール・手編集などで、末尾に改行の無い保存先が既にある状態を模す
    dest.write_bytes("- 2026-07-15 七月1".encode("utf-8"))

    result = archive_mod.apply(path, keep_months=1, today=TODAY)
    assert result["written"]

    dest_text = dest.read_text(encoding="utf-8")
    assert dest_text.splitlines() == ["- 2026-07-15 七月1", "- 2026-07-10 七月2"]  # 融合せず2行

    dest_blocks = archive_mod._parse_blocks(dest_text, "bullet")
    assert sum(1 for b in dest_blocks if b["kind"] == "entry") == 2
    assert "（2件）" in path.read_text(encoding="utf-8")


LOG_SOURCE_NO_TRAILING_NEWLINE = "# LOG\n\n- 2026-09-01 九月\n- 2026-07-15 七月1"  # 最終行に改行が無い


def test_source_last_line_without_newline_first_run(tmp_path: Path):
    path = tmp_path / "home" / "LOG.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    # _write()(write_text)だと Windows で改行が \r\n に化けて前提が崩れるため write_bytes で直接書く
    path.write_bytes(LOG_SOURCE_NO_TRAILING_NEWLINE.encode("utf-8"))

    result = archive_mod.apply(path, keep_months=1, today=TODAY)
    assert result["written"]

    dest = path.parent / "archive" / "LOG-2026-07.md"
    dest_bytes = dest.read_bytes()
    assert dest_bytes.endswith(b"\n")  # 元の最終行に改行が無くても、保存先はちゃんと改行で終わる
    assert dest_bytes.decode("utf-8") == "- 2026-07-15 七月1\n"

    # このあと遡って追記しても、1回目の穴埋めのおかげで融合しない
    with path.open("a", encoding="utf-8") as f:
        f.write("- 2026-07-20 後から足した\n")
    archive_mod.apply(path, keep_months=1, today=TODAY)

    dest_text2 = dest.read_text(encoding="utf-8")
    dest_blocks2 = archive_mod._parse_blocks(dest_text2, "bullet")
    assert sum(1 for b in dest_blocks2 if b["kind"] == "entry") == 2
    assert "（2件）" in path.read_text(encoding="utf-8")
