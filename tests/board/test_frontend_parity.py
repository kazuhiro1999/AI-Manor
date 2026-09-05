"""静的ファイル（HTML/CSS/JS）の文面をそのまま検める試験。

JS の実行そのものは pytest から検証できない（ブラウザが要る）ので、ここでは
「壊れたら消えるはずの文字列」がソースにあることを見張る——v1 相当の挙動を実装した
証跡（`docs/board_parity.md` の △/× を○に直した箇所）を、リグレッションとして
機械的に押さえておくための試験。DB や home fixture は使わない（ファイルを読むだけ）。
"""

from __future__ import annotations

from pathlib import Path

BOARD_DIR = Path(__file__).resolve().parents[2] / "src" / "manor" / "board"
STATIC_DIR = BOARD_DIR / "static"


def _read(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def _read_py(name: str) -> str:
    return (BOARD_DIR / name).read_text(encoding="utf-8")


# --- ステータス別／プロジェクト別の切り替え -----------------------------------------------


def test_index_running_panel_has_id_for_mode_toggle():
    """`app.js` の `start()` は `#panel-running .seg-btn[data-mode]` を頼りに
    切り替えボタンへ click ハンドラを付ける。id が無いと **何も選ばれず、ボタンを
    押しても何も起きない**（主人の指摘「プロジェクト別／ステータス別の切り替えが
    できない」の原因そのもの）。
    """
    html = _read("index.html")
    assert 'id="panel-running"' in html
    assert 'data-mode="list"' in html
    assert 'data-mode="tree"' in html


def test_app_js_wires_mode_toggle_via_panel_running_id():
    js = _read("app.js")
    assert "#panel-running .seg-btn[data-mode]" in js


def test_app_js_persists_task_mode_to_local_storage():
    js = _read("app.js")
    assert "TASK_MODE_KEY" in js
    assert "manor-board.taskMode" in js
    assert "localStorage" in js


# --- プロジェクト別ツリー（バッジ） --------------------------------------------------------


def test_app_js_tree_shows_pending_resident_withdrawn_badges():
    js = _read("app.js")
    for needle in ("要対応 ", "常駐 ", "取り下げ ", "実行中 "):
        assert needle in js, f"tree badge text missing: {needle!r}"


# --- タイムライン: 列数は JS が入れる -------------------------------------------------------


def test_app_js_sets_timeline_grid_template_columns_from_js():
    """v1 README §2-4: 「列の本数は JS が `grid-template-columns` に入れる」。CSS 側で
    `repeat(var(--tl-cols), …)` と書くと、ブラウザによっては丸ごと無視され、
    タイムラインが縦に積まれて壊れる。
    """
    js = _read("app.js")
    assert "headTrack.style.gridTemplateColumns" in js
    assert "track.style.gridTemplateColumns" in js


def test_style_css_does_not_use_tl_cols_in_grid_template_columns():
    css = _read("style.css")
    assert "grid-template-columns: repeat(var(--tl-cols)" not in css


# --- プロジェクト俯瞰の表: 横スクロール無しに残日数が見える ---------------------------------


def test_style_css_table_grid_does_not_force_nowrap_on_all_columns():
    """以前は `table.grid th, table.grid td` に `white-space: nowrap` を一律で付けていた。
    「次の一手」のような自由文が長いと表全体が横に伸び、期限・残日数を見るのに
    横スクロールが要った（主人の指摘「計画→プロジェクトの表示が変」の原因）。
    """
    css = _read("style.css")
    assert "table.grid th, table.grid td { padding: 6px 9px; border-bottom: 1px solid var(--border); text-align: left; white-space: nowrap; }" not in css
    assert "col-nowrap" in css


def test_app_js_projects_table_marks_only_short_columns_nowrap():
    js = _read("app.js")
    assert "col-nowrap" in js
    assert "col-wide" in js


# --- キーボードショートカット --------------------------------------------------------------


def test_app_js_has_keyboard_shortcuts_for_views_and_sidebar():
    js = _read("app.js")
    assert '"12345".indexOf(ev.key)' in js
    assert 'ev.key === "\\\\"' in js
    assert "nav-hidden" in js


# --- 5秒ポーリング・外部更新の反映バッジ ----------------------------------------------------


def test_app_js_shows_external_update_badge_on_fingerprint_change():
    js = _read("app.js")
    assert "外部の更新を反映しました" in js
    assert "state.fingerprint" in js


# --- 既定ポート 8788 -----------------------------------------------------------------------


# --- 主人の要望①: ステータス別の表示順（主人の作業〈進行中〉が最上段） -----------------------


def _function_body(js: str, name: str) -> str:
    """`function <name>(...) { ... }` の本体だけを雑に切り出す（次の `function ` の
    手前まで）。文字列の出現順を確かめる試験は、他の関数の同名文字列に引きずられない
    よう範囲を絞る。
    """
    start = js.index(f"function {name}(")
    rest = js[start:]
    next_fn = rest.find("\nfunction ", 1)
    return rest[:next_fn] if next_fn >= 0 else rest


def test_running_list_puts_master_doing_block_first():
    """主人の要望「『主人の作業（進行中）』が一番関心があるので一番上に」。
    ステータス別の並び: 主人の作業（進行中）→ 執事の実行中 → 委譲中 → 常駐 →
    未着手・保留・待ち → ① 直近の完了 → 取り下げ。
    """
    js = _read("app.js")
    body = _function_body(js, "renderRunningList")
    labels = ["主人の作業（進行中）", "実行中（執事）", "委譲中", "常駐", "未着手・保留・待ち",
              "① 直近の完了", "取り下げ（直近7日）"]
    positions = [body.index(label) for label in labels]
    assert positions == sorted(positions), f"status block order is wrong: {labels} at {positions}"


def test_running_list_merges_master_backlog_and_resident_with_owner_tag():
    """「主人の待ち・未着手は『未着手・保留・待ち』内に（主人）印で」——`resident` /
    `backlog`（todo/waiting/hold）フィルタから owner=master を除外していないことを確かめる
    （以前の実装は `notMaster(t)` で主人ぶんを弾いていた）。
    """
    js = _read("app.js")
    body = _function_body(js, "renderRunningList")
    assert "notMaster" not in body
    assert 't.status === "resident"' in body and 'owner === "butler" && t.status === "resident"' not in body
    assert '"todo", "waiting", "hold"].indexOf(t.status) >= 0)' in body


# --- 主人の要望①: 完了・取り下げの折りたたみ（既定閉じる・localStorage） ------------------


def test_done_recent_defaults_closed_and_persists_open_state_to_local_storage():
    js = _read("app.js")
    assert "DONE_OPEN_KEY" in js
    assert "manor-board.doneOpen" in js
    assert "loadDoneOpenSet" in js
    assert "saveDoneOpenSet" in js
    # 以前あった「最新の日を自動で開く」種（doneSeeded）が消えている
    # ＝既定は閉じる、が復活していないことの保険。
    assert "doneSeeded" not in js


def test_withdrawn_block_is_a_collapsible_fold_block():
    js = _read("app.js")
    assert "renderFoldBlock" in js
    assert "state.withdrawnOpen" in js


# --- 主人の要望③: プロジェクト別の関心順（interest） ---------------------------------------


def test_running_tree_sorts_projects_by_interest_rank():
    js = _read("app.js")
    body = _function_body(js, "renderRunningTree")
    assert "interest" in body
    assert ".rank" in body


def test_running_tree_shows_interest_reason_and_butler_kind_badge():
    js = _read("app.js")
    assert "interestReasonText" in js
    assert "tree-group-interest" in js
    assert "tree-group-kind" in js
    assert '"執事"' in js  # kind==='執事' の判定


# --- 主人の要望②: Markdown 描画 -------------------------------------------------------------


def test_md_js_exists_and_escapes_before_converting():
    js = _read("md.js")
    assert "function mdEscape(" in js
    assert "function mdToHtml(" in js
    # 安全のための約束: mdToHtml はまず mdEscape で丸ごとエスケープしてから記法を組み立てる。
    to_html_body = _function_body(js, "mdToHtml")
    assert "mdEscape(" in to_html_body


def test_index_html_loads_md_js_before_app_js():
    html = _read("index.html")
    md_pos = html.index('src="/static/md.js"')
    app_pos = html.index('src="/static/app.js"')
    assert md_pos < app_pos


def test_ctx_modal_renders_markdown_via_md_js():
    js = _read("app.js")
    assert "mdToHtml(res.markdown)" in js


def test_state_tab_renders_markdown():
    js = _read("app.js")
    body = _function_body(js, "renderState")
    assert "mdToHtml(text)" in body


def test_handoff_brief_and_report_render_markdown():
    js = _read("app.js")
    assert "mdToHtml(full.brief)" in js
    assert "mdToHtml(full.report)" in js


# --- 主人の要望④: 夜勤の作業報告 -------------------------------------------------------------


def test_log_tabs_include_night():
    js = _read("app.js")
    assert '"night"' in js.split("const LOG_TABS", 1)[1].split("\n", 1)[0]


def test_index_html_has_night_panel_and_tab():
    html = _read("index.html")
    assert 'id="panel-night"' in html
    assert 'data-log-tab="night"' in html
    assert 'id="night-date-select"' in html


def test_app_js_wires_night_reports_api():
    js = _read("app.js")
    assert '"/api/night/reports"' in js
    assert '"/api/night/reports/"' in js


def test_app_js_night_report_falls_back_to_raw_markdown_when_not_ok():
    js = _read("app.js")
    body = _function_body(js, "renderNightReport")
    assert "parsed.ok" in body
    assert "mdToHtml(data.text" in body


# --- 主人の指摘（2巡目・1）: 一言なしで承認／却下できるように --------------------------------


def test_rule_decision_only_blocks_empty_ruling_for_modified():
    """承認・却下は一言なしで押せる（core が既定の一言を入れる）。ブロックするのは
    `status === "modified"` のときだけ——以前は全裁定で `ruling` が空だと弾いていた。
    """
    js = _read("app.js")
    body = _function_body(js, "ruleDecision")
    assert 'status === "modified"' in body
    assert "!trimmed" in body
    # 全裁定を一律で弾いていた頃の書き方（if (!ruling ...)）が残っていないこと。
    assert "if (!ruling" not in body


def test_rule_decision_marks_input_error_without_submitting():
    js = _read("app.js")
    body = _function_body(js, "ruleDecision")
    assert "input-error" in body
    # 拒否したときは api(...) を呼ばずに return している（送信しない）。
    assert body.index("return;") < body.index("await api(")


def test_judge_buttons_pass_input_element_to_rule_decision():
    js = _read("app.js")
    assert "ruleDecision(d.id, status, input.value, input)" in js


def test_decision_api_model_does_not_require_ruling():
    py = _read_py("api_core.py")
    # 空を弾いていた頃の書き方（`Field(..., min_length=1)`）が消え、既定 "" に緩んでいること。
    assert "ruling: str = Field(..., min_length=1)" not in py
    assert 'ruling: str = Field("", max_length=2000)' in py


# --- 主人の指摘（2巡目・2）: 入力中はポーリングでフォーカスを奪わない ---------------------------


def test_composition_guard_tracks_ime_state():
    js = _read("app.js")
    assert "compositionstart" in js
    assert "compositionend" in js
    assert "composingElement" in js


def test_is_editing_within_checks_active_element_and_composition():
    js = _read("app.js")
    body = _function_body(js, "isEditingWithin")
    assert "document.activeElement" in body
    assert "composingElement" in body
    assert 'tagName === "INPUT"' in body
    assert 'tagName === "TEXTAREA"' in body


def test_apply_skips_judge_rerender_while_editing():
    js = _read("app.js")
    body = _function_body(js, "apply")
    assert 'isEditingWithin("panel-judge")' in body


def test_load_log_skips_handoff_rerender_while_editing():
    js = _read("app.js")
    body = _function_body(js, "loadLog")
    assert 'isEditingWithin("panel-handoff")' in body


def test_init_composition_guard_is_wired_into_start():
    js = _read("app.js")
    body = _function_body(js, "start")
    assert "initCompositionGuard()" in body


# --- 主人の指摘（2巡目・3）: ツリーの `[pj]` 接頭辞が親と重なるときは落とす -----------------------


def test_strip_leading_project_bracket_only_matches_code_or_name():
    js = _read("app.js")
    body = _function_body(js, "stripLeadingProjectBracket")
    assert "project.code" in body
    assert "project.title" in body
    # DB のタイトルは書き換えない（関数は新しい文字列を返すだけ）——呼び出し側が
    # `t.title` へ代入し直していないことを軽く確認。
    assert "t.title =" not in js


def test_task_row_hides_prefix_only_when_parent_project_matches():
    js = _read("app.js")
    body = _function_body(js, "taskRow")
    assert "opts.parentProject" in body
    assert "isUnderMatchingParent" in body
    assert "stripLeadingProjectBracket(t.title, opts.parentProject)" in body


def test_running_tree_passes_parent_project_to_task_rows_and_done_days():
    js = _read("app.js")
    body = _function_body(js, "renderRunningTree")
    assert "parentProject: proj" in body


def test_running_list_does_not_pass_parent_project():
    """ステータス別ではプロジェクトが分からないので `[pj]` を常に残す——
    `renderRunningList` は `parentProject` を taskRow へ渡さない。"""
    js = _read("app.js")
    body = _function_body(js, "renderRunningList")
    assert "parentProject" not in body


def test_default_port_is_8788_everywhere():
    src_dir = STATIC_DIR.parent
    for name in ("app.py", "__init__.py", "__main__.py"):
        text = (src_dir / name).read_text(encoding="utf-8")
        assert "8788" in text, f"{name} does not mention the new default port 8788"
        assert "8787" not in text, f"{name} still hard-codes the old (v1) default port 8787"


# --- JS が参照する id が HTML に実在する（2026-09-02・同じ型の不一致が2度起きた） -------------
#   1度目: `#panel-running` が無く切り替えのハンドラが付かなかった
#   2度目: `panel-judge` が無く、入力中の再描画ガードが効かずフォーカスが外れた


def test_ids_referenced_by_js_exist_in_html_or_are_created_by_js():
    import re
    from pathlib import Path

    static = Path(__file__).resolve().parents[2] / "src" / "manor" / "board" / "static"
    js = (static / "app.js").read_text(encoding="utf-8")
    html = (static / "index.html").read_text(encoding="utf-8")

    referenced: set[str] = set()
    referenced.update(re.findall(r'isEditingWithin\("([\w-]+)"\)', js))
    referenced.update(re.findall(r'getElementById\(["\']([\w-]+)["\']\)', js))
    referenced.update(re.findall(r'querySelector(?:All)?\(["\']#([\w-]+)', js))

    def exists(i: str) -> bool:
        if f'id="{i}"' in html:
            return True
        # JS が自分で作る要素（テンプレート文字列や .id = "..."）は許す
        return (f'id="{i}"' in js) or (f'.id = "{i}"' in js) or (f".id = '{i}'" in js)

    missing = sorted(i for i in referenced if not exists(i))
    assert not missing, f"JS が参照するが HTML にも JS にも無い id: {missing}"
