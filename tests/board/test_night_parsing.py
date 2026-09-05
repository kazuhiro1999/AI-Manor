"""`manor.board.night`（夜勤の作業報告の解析）の単体試験。

DB や HTTP は使わない——`night.py` は純粋関数（ファイル I/O と文字列処理だけ）なので、
API 越しではなく直接呼んで確かめる。`tests/board/test_api.py` の night セクションは
HTTP 経由の結合試験（一覧・本文・404・フォールバック）を持つので、ここでは解析そのものの
枝分かれ（v1 night_report.py の考え方の移植）を細かく見る。

ファイル名を `test_night_parsing.py` にしているのは、リポジトリ直下に既に
`tests/test_night.py`（別担当が作っている夜勤の仕組み `manor.night.runner` の試験）が
あるため——`tests/` に `__init__.py` が無い（pytest の既定 import mode）ので、
`test_night.py` という同名ファイルが2箇所にあると衝突する。
"""

from __future__ import annotations

from pathlib import Path

from manor.board import night


# --- list_report_dates / read_report_text -------------------------------------------


def test_list_report_dates_sorts_newest_first_and_ignores_non_date_names(tmp_path: Path):
    reports_dir = tmp_path / "night" / "reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "2026-09-01.md").write_text("x", encoding="utf-8")
    (reports_dir / "2026-08-15.md").write_text("x", encoding="utf-8")
    (reports_dir / "2026-08-30.md").write_text("x", encoding="utf-8")
    (reports_dir / "README.md").write_text("x", encoding="utf-8")
    (reports_dir / "not-a-date.txt").write_text("x", encoding="utf-8")

    assert night.list_report_dates(tmp_path) == ["2026-09-01", "2026-08-30", "2026-08-15"]


def test_list_report_dates_empty_when_no_reports_folder(tmp_path: Path):
    assert night.list_report_dates(tmp_path) == []


def test_read_report_text_none_for_bad_date_shape(tmp_path: Path):
    reports_dir = tmp_path / "night" / "reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "2026-09-01.md").write_text("hello", encoding="utf-8")
    assert night.read_report_text(tmp_path, "2026/09/01") is None
    assert night.read_report_text(tmp_path, "not-a-date") is None


def test_read_report_text_none_when_file_missing(tmp_path: Path):
    assert night.read_report_text(tmp_path, "2026-01-01") is None


def test_read_report_text_reads_utf8(tmp_path: Path):
    reports_dir = tmp_path / "night" / "reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "2026-09-01.md").write_text("# 夜勤の作業報告 2026-09-01\n", encoding="utf-8")
    assert night.read_report_text(tmp_path, "2026-09-01") == "# 夜勤の作業報告 2026-09-01\n"


# --- parse_report ----------------------------------------------------------------------


_SAMPLE = """# 夜勤の作業報告 2026-09-01

> 開始 23:10 / 終了 05:40
> 上限 6時間

## N1 発話の設計（QUEUE B48）

- **背景**: 主人から「もっと自然にしてほしい」と言われていた
- **目的**: 発話のテンプレートを増やす
- **やったこと**: テンプレートを3種類追加した
- **どこまで**: 完了。動作確認まで済んだ
- **次**: 主人の反応を見て調整する

## N2 通知バグの調査

- **背景**: 通知が二重に届く報告があった
- **どこまで**: 保留。再現条件が絞りきれていない
"""


def test_parse_report_splits_into_task_cards():
    report = night.parse_report(_SAMPLE, date_hint="2026-09-01")
    assert report["ok"] is True
    assert report["date"] == "2026-09-01"
    assert "夜勤の作業報告" in report["title"]
    assert len(report["tasks"]) == 2


def test_parse_report_summary_is_the_quote_block_after_title():
    report = night.parse_report(_SAMPLE)
    # `>` の引用ブロックは空行を挟まなければ1段落にまとめる。
    assert report["summary"] == ["開始 23:10 / 終了 05:40 上限 6時間"]


def test_parse_report_field_order_is_fixed_regardless_of_source_order():
    text = "## N1 順番入れ替え\n\n- **次**: 最後にやる\n- **背景**: 先に書く\n"
    report = night.parse_report(text)
    task = report["tasks"][0]
    labels = [f["label"] for f in task["fields"]]
    assert labels == ["背景", "次"]  # FIELD_LABELS の順（背景が先、次が後）


def test_parse_report_unlabeled_list_items_kept_after_known_fields():
    text = "## N1 タスク\n\n- **背景**: 既知の項目\n- 無ラベルの補足\n"
    report = night.parse_report(text)
    fields = report["tasks"][0]["fields"]
    assert fields[0]["key"] == "background"
    assert fields[1]["key"] == ""
    assert fields[1]["text"] == "無ラベルの補足"


def test_parse_report_number_extracted_from_heading():
    text = "## N1 発話の設計\n\n- **背景**: b\n"
    report = night.parse_report(text)
    assert report["tasks"][0]["number"] == "N1"
    assert report["tasks"][0]["title"] == "発話の設計"


def test_parse_report_state_classification_hold_beats_done():
    """「どこまで」に「保留」と「完了」の両方があれば保留に倒す（v1 と同じ判断。
    「まだ手が要るもの」を見逃さないため）。"""
    text = "## N1 タスク\n\n- **どこまで**: 完了。ただし1件は保留のまま\n"
    report = night.parse_report(text)
    assert report["tasks"][0]["state"] == "hold"


def test_parse_report_state_done_when_only_done_mentioned():
    text = "## N1 タスク\n\n- **どこまで**: 完了\n"
    report = night.parse_report(text)
    assert report["tasks"][0]["state"] == "done"


def test_parse_report_state_other_when_neither_mentioned():
    text = "## N1 タスク\n\n- **どこまで**: 半分くらい進めた\n"
    report = night.parse_report(text)
    assert report["tasks"][0]["state"] == "other"


def test_parse_report_headings_inside_code_fence_are_not_task_boundaries():
    text = (
        "## N1 タスク\n\n"
        "- **背景**: 説明\n"
        "```\n## これはコード内の見出しもどき\n```\n"
        "- **次**: 続き\n"
    )
    report = night.parse_report(text)
    assert len(report["tasks"]) == 1
    labels = [f["label"] for f in report["tasks"][0]["fields"]]
    assert "背景" in labels and "次" in labels


def test_parse_report_ok_false_when_no_task_like_heading():
    report = night.parse_report("ただの雑記。見出しも項目も無い。\n")
    assert report["ok"] is False
    assert report["tasks"] == []


def test_parse_report_section_without_number_or_fields_is_not_a_task():
    """`##` 見出しでも、番号も既定項目も無ければタスクにしない（v1 の判断基準と同じ:
    「番号が付いている」か「既定の項目が1つでもある」か）。"""
    text = "## 所感\n\n今日は特に問題なし。\n"
    report = night.parse_report(text)
    assert report["ok"] is False
    assert report["tasks"] == []


def test_parse_report_never_raises_on_garbage_input():
    """**どんな入力でも例外を投げない**（v1 と同じ約束）。"""
    for garbage in ("", None, "###", "```\nunterminated", "\x00\x01binary-ish"):
        report = night.parse_report(garbage)  # type: ignore[arg-type]
        assert isinstance(report, dict)
        assert "ok" in report
