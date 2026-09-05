"""`manor notion`（ADR-009 §7「Notion 拡張（5c）」）の試験。

**実 Notion へは一切繋がない。** HTTP は必ず `notion_mod._notion_request` を差し替える
（差し替え忘れがあれば `urllib.request.urlopen` を爆発させる `http_guard` fixture が
検出する。`test_slack.py` と同じ道具立て）。`claude -p` も同様に `subprocess.run` を
差し替える／そもそも呼ばせない。

時刻は `MANOR_NOW` / `MANOR_TODAY`（`conftest.home_path` が既定で削除している）を
明示的に固定する（`butler/GROWTH.md` G1: 時刻に依る試験は時計を固定して書く）。
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

import pytest

from manor import decision as decision_mod
from manor import graph as graph_mod
from manor import notion as notion_mod
from manor import task as task_mod
from manor import util


# --- 共通の下ごしらえ ---------------------------------------------------------------------


@pytest.fixture
def leak_terms(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`MANOR_LEAK_TERMS` を一時ファイルへ向ける。禁止語スキャンを試験内で完全に制御する
    （`test_slack.py` の同名 fixture と同じ形。共有 conftest には無いので複製してある）。
    """

    def _set(terms: list[str]) -> Path:
        path = tmp_path / "leak-terms.txt"
        path.write_text("\n".join(terms) + "\n" if terms else "", encoding="utf-8")
        monkeypatch.setenv("MANOR_LEAK_TERMS", str(path))
        return path

    return _set


@pytest.fixture
def http_guard(monkeypatch: pytest.MonkeyPatch):
    """`urllib.request.urlopen` を爆発させる。**呼ばれたら試験が失敗する**——
    「HTTP を一切呼ばない」ことを主張する試験の、差し替え忘れの検出用。
    """

    def boom(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("urllib.request.urlopen が呼ばれました（HTTP は禁止のはずです）")

    monkeypatch.setattr(urllib.request, "urlopen", boom)


def _write_notion_config(home: Path, *, database_id: str = "db-abc123") -> None:
    Path(home).mkdir(parents=True, exist_ok=True)
    (Path(home) / "config.toml").write_text(f"[notion]\ndatabase_id = '{database_id}'\n", encoding="utf-8")


def _fix_clock(monkeypatch: pytest.MonkeyPatch, date: str = "2026-09-04") -> None:
    monkeypatch.setenv("MANOR_TODAY", date)
    monkeypatch.setenv("MANOR_NOW", f"{date}T09:00:00")


def _complete_task(conn, *, title: str = "テストタスク") -> str:
    """`todo` → `doing` → `done` まで進めた task を1件作る（`task_event.to_status='done'`
    が付く。`_completed_tasks` が拾う対象）。"""
    task_id = task_mod.add(conn, title)
    task_mod.status(conn, task_id, "doing")
    task_mod.status(conn, task_id, "done")
    conn.commit()
    return task_id


def _make_ruled_decision(
    conn, *, title: str = "テスト決定", verdict: str = "approved", ruling: str = ""
) -> str:
    task_id = task_mod.add(conn, title)
    decision_id = decision_mod.ask(conn, title, task_id=task_id, recommend="承認してください", background="背景")
    decision_mod.rule(conn, decision_id, verdict, ruling=ruling)
    conn.commit()
    return decision_id


def _fake_notion_dispatch(*, on_query_exists: bool = False, existing: dict | None = None):
    """`database_id` → `data_source_id` の解決・重複確認・ページ作成の3種を模す
    `_notion_request` の差し替え。呼ばれた `(method, path)` の一覧も一緒に返す。
    """
    calls: list[tuple[str, str]] = []

    def fake(method: str, path: str, token: str, *, body=None, timeout: float = 0):  # noqa: ANN001
        calls.append((method, path))
        if path.startswith("/databases/"):
            return {"ok": True, "data": {"data_sources": [{"id": "ds-1"}]}}
        if path.startswith("/data_sources/") and path.endswith("/query"):
            if on_query_exists:
                return {"ok": True, "data": {"results": [existing or {"id": "page-existing", "url": "https://notion.so/page-existing"}]}}
            return {"ok": True, "data": {"results": []}}
        if path == "/pages":
            return {"ok": True, "data": {"id": "page-xyz", "url": "https://notion.so/page-xyz"}}
        raise AssertionError(f"想定外の呼び出しです: {method} {path}")

    return fake, calls


# --- 禁止語スキャン（D18: slack.py の関数を再利用） -----------------------------------------


def test_diary_blocked_by_banned_word_does_not_echo_term(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch, leak_terms, http_guard
):
    """タスクの題名に禁止語を混ぜ、`diary()` がそれを検出して**何も投函しない**（HTTP は
    一切呼ばれない。`http_guard`）ことを確かめる。`scan_failure` に語そのものは出てこない。
    """
    leak_terms(["ひみつのあいことば"])
    _fix_clock(monkeypatch)
    _complete_task(conn, title="ひみつのあいことば を含むタスク")

    result = notion_mod.diary(home, generate=False, dry_run=False)

    assert result["posted"] is False
    assert "scan_failure" in result
    assert isinstance(result["scan_failure"]["position"], int)
    dumped = str(result)
    assert "ひみつのあいことば" not in dumped


def test_scan_reuses_slack_function_object(leak_terms):
    """`slack.py` の `scan_for_leak_terms` を re-export ではなく同じ関数として使い回して
    いることの直接の確認（二重実装していないことの印）。"""
    assert notion_mod.slack_mod.scan_for_leak_terms is not None
    leak_terms(["まったく関係ない語"])
    assert notion_mod.slack_mod.scan_for_leak_terms("こんにちは") == {"ok": True, "reason": "", "position": None}


# --- dry-run は HTTP を一切呼ばない ----------------------------------------------------------


def test_diary_dry_run_makes_no_http_call(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch, leak_terms, http_guard
):
    leak_terms([])  # 空リスト（存在はする）＝ヒットなし
    _fix_clock(monkeypatch)
    _complete_task(conn, title="下ごしらえの確認")

    result = notion_mod.diary(home, generate=False, dry_run=True)

    assert result["posted"] is False
    assert result["dry_run"] is True
    assert "下ごしらえの確認" in result["text"]
    rows = conn.execute("SELECT * FROM notion_page").fetchall()
    assert rows == []  # 投函していないので記録も無い


# --- 機械組みの本文は claude が無くても動く ---------------------------------------------------


def test_mechanical_diary_works_with_claude_absent(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch, leak_terms
):
    leak_terms([])
    _fix_clock(monkeypatch)

    def boom_subprocess(*_a: object, **_k: object) -> object:
        raise AssertionError("claude を起動してはいけません（--generate 無指定のはずです）")

    monkeypatch.setattr(notion_mod.subprocess, "run", boom_subprocess)
    monkeypatch.setattr(notion_mod.shutil, "which", lambda name: None)  # claude が PATH に無い

    _complete_task(conn, title="機械組みの確認")

    result = notion_mod.diary(home, generate=False, dry_run=True)

    assert result["generated"] is False
    assert "機械組みの確認" in result["text"]
    assert "2026-09-04" in result["text"]


def test_generate_falls_back_to_mechanical_when_claude_missing(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch, leak_terms
):
    leak_terms([])
    _fix_clock(monkeypatch)
    monkeypatch.setattr(notion_mod.shutil, "which", lambda name: None)
    _complete_task(conn, title="生成フォールバックの確認")

    result = notion_mod.diary(home, generate=True, dry_run=True)

    assert result["generated"] is False
    assert "生成フォールバックの確認" in result["text"]
    assert "claude が見つからない" in result["generate_note"]


# --- 本文の組み立て（機械組み。task_event / decision / milestone / 夜勤） ----------------------


def test_format_mechanical_diary_includes_all_categories(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch
):
    _fix_clock(monkeypatch)
    _complete_task(conn, title="完了したタスクの件")
    _make_ruled_decision(conn, title="裁定した件", verdict="approved")
    graph_mod.milestone_add(conn, "節目の件", date="2026-09-04")
    conn.commit()

    reports_dir = Path(home) / "night" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "2026-09-04.md").write_text("# 夜勤の報告\n作業しました。", encoding="utf-8")

    data = notion_mod.diary_data(conn, home, "2026-09-04")
    text = notion_mod.format_mechanical_diary(data)

    assert "完了したタスクの件" in text
    assert "裁定した件" in text
    assert "承認" in text
    assert "節目の件" in text
    assert "夜勤の報告" in text or "作業しました" in text


def test_format_mechanical_diary_empty_day_says_nothing_happened(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch
):
    _fix_clock(monkeypatch)
    data = notion_mod.diary_data(conn, home, "2026-09-04")
    text = notion_mod.format_mechanical_diary(data)
    assert "記録された動きはありませんでした" in text


# --- 投函: notion_page への記録 ----------------------------------------------------------------


def test_diary_posts_and_records_notion_page(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch, leak_terms
):
    leak_terms([])
    _fix_clock(monkeypatch)
    _write_notion_config(home)
    monkeypatch.setattr(notion_mod, "api_token", lambda: "ntn_test_token")
    _complete_task(conn, title="投函の確認")

    fake, calls = _fake_notion_dispatch()
    monkeypatch.setattr(notion_mod, "_notion_request", fake)

    result = notion_mod.diary(home, generate=False, dry_run=False)

    assert result["posted"] is True
    assert result["page_id"] == "page-xyz"
    assert result["url"] == "https://notion.so/page-xyz"

    row = conn.execute("SELECT date, page_id, url FROM notion_page").fetchone()
    assert row["date"] == "2026-09-04"
    assert row["page_id"] == "page-xyz"
    assert row["url"] == "https://notion.so/page-xyz"

    assert ("GET", "/databases/db-abc123") in calls
    assert any(m == "POST" and p.endswith("/query") for m, p in calls)
    assert ("POST", "/pages") in calls


# --- 二重投函の防止（D19「両方やる」。ローカルと Notion 側を別々に確かめる） --------------------


def test_diary_second_run_blocked_by_local_record_without_any_http(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch, leak_terms, http_guard
):
    """`notion_page` に既にその日の記録があれば、Notion へは一切問い合わせない
    （`http_guard` で HTTP がゼロ回であることまで確かめる）。"""
    leak_terms([])
    _fix_clock(monkeypatch)
    _write_notion_config(home)
    monkeypatch.setattr(notion_mod, "api_token", lambda: "ntn_test_token")
    conn.execute(
        "INSERT INTO notion_page (date, page_id, url, posted_at) VALUES (?, ?, ?, ?)",
        ("2026-09-04", "page-old", "https://notion.so/page-old", util.now()),
    )
    conn.commit()

    result = notion_mod.diary(home, generate=False, dry_run=False)

    assert result["posted"] is False
    assert result["which"] == "local"
    assert "既に投函済み" in result["reason"]


def test_diary_second_run_blocked_by_remote_query_and_backfills_local_record(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch, leak_terms
):
    """ローカル記録は空だが、Notion 側に既にその日のページがある場合。ページ作成は
    呼ばれず（`/pages` を呼んだら試験を落とす）、見つかったページをローカルへも記録する
    （次回以降はローカルの速い経路で気づけるようにするための手当て）。"""
    leak_terms([])
    _fix_clock(monkeypatch)
    _write_notion_config(home)
    monkeypatch.setattr(notion_mod, "api_token", lambda: "ntn_test_token")
    _complete_task(conn, title="重複確認（Notion 側）")

    def fake(method: str, path: str, token: str, *, body=None, timeout: float = 0):  # noqa: ANN001
        if path.startswith("/databases/"):
            return {"ok": True, "data": {"data_sources": [{"id": "ds-1"}]}}
        if path.endswith("/query"):
            return {
                "ok": True,
                "data": {"results": [{"id": "page-existing", "url": "https://notion.so/page-existing"}]},
            }
        raise AssertionError(f"既にページがあるはずなのに呼ばれました: {method} {path}")

    monkeypatch.setattr(notion_mod, "_notion_request", fake)

    result = notion_mod.diary(home, generate=False, dry_run=False)

    assert result["posted"] is False
    assert result["which"] == "remote"

    row = conn.execute("SELECT page_id, url FROM notion_page WHERE date = ?", ("2026-09-04",)).fetchone()
    assert row is not None
    assert row["page_id"] == "page-existing"
    assert row["url"] == "https://notion.so/page-existing"


def test_diary_does_not_post_when_remote_duplicate_check_itself_fails(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch, leak_terms
):
    """Notion 側への重複確認そのものが失敗（ネットワーク断など）したら、確認できないまま
    投函しない（fail-closed。D19 の「両方やる」を守れないなら投函もしない、という判断）。
    """
    leak_terms([])
    _fix_clock(monkeypatch)
    _write_notion_config(home)
    monkeypatch.setattr(notion_mod, "api_token", lambda: "ntn_test_token")
    _complete_task(conn, title="重複確認が失敗するケース")

    def fake(method: str, path: str, token: str, *, body=None, timeout: float = 0):  # noqa: ANN001
        if path.startswith("/databases/"):
            return {"ok": True, "data": {"data_sources": [{"id": "ds-1"}]}}
        if path.endswith("/query"):
            return {"ok": False, "reason": "network_error: timed out"}
        raise AssertionError(f"重複確認が失敗した後に呼ばれてはいけません: {method} {path}")

    monkeypatch.setattr(notion_mod, "_notion_request", fake)

    result = notion_mod.diary(home, generate=False, dry_run=False)

    assert result["posted"] is False
    assert result["which"] == "remote_check_failed"
    assert conn.execute("SELECT * FROM notion_page").fetchall() == []


# --- API バージョンが送られること（D20） ------------------------------------------------------


def test_notion_request_sends_pinned_api_version_header(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *_a: object) -> bool:
            return False

        def read(self) -> bytes:
            return b'{"object":"user","id":"u1","name":"tester"}'

    def fake_urlopen(req, timeout=0):  # noqa: ANN001
        captured["notion_version"] = req.get_header("Notion-version")
        captured["auth"] = req.get_header("Authorization")
        captured["url"] = req.full_url
        return _FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    result = notion_mod._notion_request("GET", "/users/me", "ntn_test_token")

    assert result["ok"] is True
    assert captured["notion_version"] == notion_mod.NOTION_API_VERSION
    assert captured["auth"] == "Bearer ntn_test_token"
    assert captured["url"] == f"{notion_mod.NOTION_API_BASE}/users/me"


# --- 疎通確認（`manor notion test` の実体） ---------------------------------------------------


def test_test_connection_without_token(home: Path, monkeypatch: pytest.MonkeyPatch, http_guard):
    monkeypatch.setattr(notion_mod, "api_token", lambda: None)
    result = notion_mod.test_connection(home)
    assert result["ok"] is False
    assert "token" in result["reason"]


def test_test_connection_calls_users_me(home: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(notion_mod, "api_token", lambda: "ntn_test_token")

    def fake(method, path, token, *, body=None, timeout=0):  # noqa: ANN001
        assert method == "GET"
        assert path == "/users/me"
        return {"ok": True, "data": {"name": "テスト執事"}}

    monkeypatch.setattr(notion_mod, "_notion_request", fake)
    result = notion_mod.test_connection(home)
    assert result["ok"] is True
    assert result["name"] == "テスト執事"


# --- 拡張マニフェスト（extensions/notion.py。ADR-009 D2・D20） --------------------------------


def test_extension_manifest_shape():
    from manor.extensions import notion as notion_ext

    m = notion_ext.MANIFEST
    assert m["id"] == "notion"
    assert m["kind"] == "service"
    assert {"database_id", "token"} <= {f["key"] for f in m["fields"]}  # type: ignore[index]
    assert m["secret_fields"] == ["token"]
    assert m["install_steps"]  # 空でない


def test_extension_detect_and_check_never_raise(home: Path, monkeypatch: pytest.MonkeyPatch, http_guard):
    from manor.extensions import notion as notion_ext

    monkeypatch.setattr(notion_mod, "api_token", lambda: None)
    detect_result = notion_ext.detect(home)
    assert detect_result == {"installed": True, "reason": ""}
    result = notion_ext.check(home)
    assert result["ok"] is False  # 秘密が無いので当然疎通できないが、例外にはならない


def test_extension_registered_via_real_registry_contract(
    home: Path, monkeypatch: pytest.MonkeyPatch, http_guard
):
    """実際に出来上がった `extensions/__init__.py` の `_safe_detect`/`_safe_check`
    と同じ形で呼んでも落ちないことを確かめる（`test_slack.py` の同名試験と対になる）。
    """
    from manor.extensions import notion as notion_ext

    monkeypatch.setattr(notion_mod, "api_token", lambda: None)
    detect_result = notion_ext.detect(home)
    assert isinstance(detect_result, dict)
    assert detect_result.get("installed") is True

    check_result = notion_ext.check(home)
    assert isinstance(check_result, dict)
    assert "ok" in check_result and "reason" in check_result


def test_extension_is_registered_in_real_registry(home: Path):
    """`src/manor/extensions/__init__.py` の `_MODULES` に実際に並んでいることの確認
    （担当外のファイルだが、配線が済んでいることを検分できるようにしておく）。
    """
    from manor import extensions as ext_mod

    ids = {m["id"] for m in ext_mod.all_manifests()}
    assert "notion" in ids
    detail = ext_mod.detail(home, "notion")
    assert detail["status"] in ("not_installed", "needs_config", "ready", "ok", "error")
