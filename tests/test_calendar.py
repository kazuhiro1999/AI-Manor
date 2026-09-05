"""`manor calendar`（ADR-012 §2「5d カレンダー拡張」）の試験。

**実際のカレンダー URL へは一切繋がない。** HTTP は必ず `calendar_mod.urllib.request.urlopen`
を差し替える（`test_notion.py`/`test_slack.py` と同じ道具立て）。URL・予定・人名は
すべて合成データ（`example.com`）。**主人の実際のカレンダー情報はここにもコードにも書かない**
（ROADMAP・依頼の「合成データのみ」の指示）。

`home`/`conn` フィクスチャ（`tests/conftest.py`）は `MANOR_HOME` と `MANOR_SECRETS_DIR`
（ADR-009 D4）の両方を一時ディレクトリへ向けるので、本物の home にも `~/.manor/secrets/`
にも触れない。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from manor import calendar as calendar_mod
from manor import secrets as secrets_mod
from manor import util
from manor.errors import ManorError

FAKE_URL = "https://example.com/private/calendar.ics"


# --- 共通の下ごしらえ ---------------------------------------------------------------------


class _FakeResp:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *_a: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._data


def _fix_clock(monkeypatch: pytest.MonkeyPatch, date_: str = "2026-09-05") -> None:
    monkeypatch.setenv("MANOR_TODAY", date_)
    monkeypatch.setenv("MANOR_NOW", f"{date_}T09:00:00")


def _ics_bytes(events: list[tuple[str, str, str]]) -> bytes:
    """`(uid, dtstart, summary)` の一覧から最小の ICS 本文を組む（Asia/Tokyo 固定）。"""
    body = "\n".join(
        f"BEGIN:VEVENT\nUID:{uid}\nDTSTART;TZID=Asia/Tokyo:{dtstart}\nSUMMARY:{summary}\nEND:VEVENT"
        for uid, dtstart, summary in events
    )
    return f"BEGIN:VCALENDAR\nVERSION:2.0\n{body}\nEND:VCALENDAR\n".encode("utf-8")


@pytest.fixture
def http_guard(monkeypatch: pytest.MonkeyPatch):
    """`urllib.request.urlopen` を爆発させる（差し替え忘れの検出用。test_notion.py と同じ形）。"""

    def boom(*_a: object, **_kw: object) -> object:
        raise AssertionError("urllib.request.urlopen が呼ばれました（HTTP は禁止のはずです）")

    monkeypatch.setattr(urllib.request, "urlopen", boom)


def _set_url(url: str = FAKE_URL) -> None:
    secrets_mod.set("calendar", "url", url)


# --- fetch_ics: 例外を出さず理由を返す ---------------------------------------------------


def test_fetch_ics_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        calendar_mod.urllib.request, "urlopen", lambda req, timeout=0: _FakeResp(_ics_bytes([]))
    )
    result = calendar_mod.fetch_ics(FAKE_URL)
    assert result["ok"] is True
    assert "BEGIN:VCALENDAR" in str(result["text"])


def test_fetch_ics_http_error_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(req, timeout=0):  # noqa: ANN001
        raise urllib.error.HTTPError(FAKE_URL, 404, "Not Found", None, None)  # type: ignore[arg-type]

    monkeypatch.setattr(calendar_mod.urllib.request, "urlopen", boom)
    result = calendar_mod.fetch_ics(FAKE_URL)
    assert result["ok"] is False
    assert "404" in str(result["reason"])


def test_fetch_ics_connection_error_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(req, timeout=0):  # noqa: ANN001
        raise urllib.error.URLError("接続できません")

    monkeypatch.setattr(calendar_mod.urllib.request, "urlopen", boom)
    result = calendar_mod.fetch_ics(FAKE_URL)
    assert result["ok"] is False


def test_fetch_ics_rejects_non_ics_content(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        calendar_mod.urllib.request, "urlopen", lambda req, timeout=0: _FakeResp(b"<html>not ics</html>")
    )
    result = calendar_mod.fetch_ics(FAKE_URL)
    assert result["ok"] is False
    assert "ICS" in str(result["reason"])


# --- URL が API から読み出せないこと（ADR-009 D4・ADR-012 D3） -------------------------------


def test_url_is_not_readable_from_extension_api(home: Path) -> None:
    from manor import extensions as ext_mod

    ext_mod.save_settings(home, "calendar", {"url": "https://very-secret.example.com/private.ics"})
    detail = ext_mod.detail(home, "calendar")
    dumped = json.dumps(detail, ensure_ascii=False)
    assert "very-secret" not in dumped
    assert detail["values"]["has_url"] is True
    assert "url" not in detail["values"]

    st = ext_mod.status(home, "calendar")
    assert "very-secret" not in json.dumps(st, ensure_ascii=False)


def test_url_is_not_written_to_config_toml(home: Path) -> None:
    """ADR-012 D3 の本題: URL 自体が鍵なので `config.toml` には一切現れない。"""
    from manor import extensions as ext_mod

    ext_mod.save_settings(home, "calendar", {"url": "https://very-secret.example.com/private.ics"})
    config_text = (home / "config.toml").read_text(encoding="utf-8") if (home / "config.toml").is_file() else ""
    assert "very-secret" not in config_text


# --- sync: URL 未設定 ------------------------------------------------------------------------


def test_sync_without_url_configured_returns_reason_without_http(
    home: Path, monkeypatch: pytest.MonkeyPatch, http_guard
) -> None:
    _fix_clock(monkeypatch)
    result = calendar_mod.sync(home)
    assert result["ok"] is False
    assert "URL" in str(result["reason"])


# --- sync: 単発の予定は窓の外（4か月先）でも取り込む（ADR-012 D5 改訂・2026-09-05） -----------


def test_sync_keeps_single_event_beyond_90_day_window(home: Path, conn, monkeypatch: pytest.MonkeyPatch) -> None:
    """`calendar.WINDOW_DAYS`（90日）は繰り返しの展開にだけ掛かる。単発の予定（旅行の予約等）
    が窓の外にあっても消えない・そもそも取り込み漏れしないことを、`sync()` を通した
    end-to-end で確認する（`ics.py` 単体の試験は `tests/test_ics.py` にある）。
    """
    _fix_clock(monkeypatch)
    _set_url()
    # today=2026-09-05 から120日ほど先（90日の窓を明確に超える）。
    ics = _ics_bytes([("trip@example.com", "20270105T090000", "家族旅行")])
    monkeypatch.setattr(calendar_mod.urllib.request, "urlopen", lambda req, timeout=0: _FakeResp(ics))

    result = calendar_mod.sync(home)
    assert result["ok"] is True
    assert result["added"] == 1

    row = conn.execute("SELECT title, start FROM secretary_event WHERE source = 'ics'").fetchone()
    assert row["title"] == "家族旅行"
    assert row["start"] == "2027-01-05T09:00:00"


# --- sync: 冪等性（同じ内容で2回流しても増えない） ------------------------------------------


def test_sync_is_idempotent_on_unchanged_feed(home: Path, conn, monkeypatch: pytest.MonkeyPatch) -> None:
    _fix_clock(monkeypatch)
    _set_url()
    ics = _ics_bytes([("evt1@example.com", "20260906T090000", "定例会議")])
    monkeypatch.setattr(calendar_mod.urllib.request, "urlopen", lambda req, timeout=0: _FakeResp(ics))

    first = calendar_mod.sync(home)
    assert first["ok"] is True
    assert first["added"] == 1
    assert first["updated"] == 0
    assert first["removed"] == 0
    rows = conn.execute("SELECT * FROM secretary_event WHERE source = 'ics'").fetchall()
    assert len(rows) == 1

    second = calendar_mod.sync(home)
    assert second == {"ok": True, "added": 0, "updated": 0, "removed": 0, "total": 1}
    rows_again = conn.execute("SELECT * FROM secretary_event WHERE source = 'ics'").fetchall()
    assert len(rows_again) == 1  # 増えていない


def test_sync_updates_changed_event(home: Path, conn, monkeypatch: pytest.MonkeyPatch) -> None:
    _fix_clock(monkeypatch)
    _set_url()
    ics1 = _ics_bytes([("evt1@example.com", "20260906T090000", "旧タイトル")])
    monkeypatch.setattr(calendar_mod.urllib.request, "urlopen", lambda req, timeout=0: _FakeResp(ics1))
    calendar_mod.sync(home)

    ics2 = _ics_bytes([("evt1@example.com", "20260906T090000", "新タイトル")])
    monkeypatch.setattr(calendar_mod.urllib.request, "urlopen", lambda req, timeout=0: _FakeResp(ics2))
    result = calendar_mod.sync(home)

    assert result["added"] == 0
    assert result["updated"] == 1
    row = conn.execute("SELECT title FROM secretary_event WHERE source = 'ics'").fetchone()
    assert row["title"] == "新タイトル"


def test_sync_removes_event_missing_from_feed(home: Path, conn, monkeypatch: pytest.MonkeyPatch) -> None:
    _fix_clock(monkeypatch)
    _set_url()
    ics1 = _ics_bytes(
        [("evt1@example.com", "20260906T090000", "会議A"), ("evt2@example.com", "20260907T090000", "会議B")]
    )
    monkeypatch.setattr(calendar_mod.urllib.request, "urlopen", lambda req, timeout=0: _FakeResp(ics1))
    calendar_mod.sync(home)
    assert conn.execute("SELECT COUNT(*) AS n FROM secretary_event WHERE source = 'ics'").fetchone()["n"] == 2

    ics2 = _ics_bytes([("evt1@example.com", "20260906T090000", "会議A")])
    monkeypatch.setattr(calendar_mod.urllib.request, "urlopen", lambda req, timeout=0: _FakeResp(ics2))
    result = calendar_mod.sync(home)

    assert result["removed"] == 1
    rows = conn.execute("SELECT title FROM secretary_event WHERE source = 'ics'").fetchall()
    assert [r["title"] for r in rows] == ["会議A"]


# --- sync: manual の予定には絶対に触らない（D6の核心） --------------------------------------


def test_sync_never_touches_manual_events(home: Path, conn, monkeypatch: pytest.MonkeyPatch) -> None:
    """主人が手で入れた予定（`source='manual'`）は、同期がフィードに存在しないものを
    削除する処理を回しても消えない・書き換わらない（ADR-012 D6「絶対に触らない」）。
    """
    _fix_clock(monkeypatch)
    conn.execute(
        'INSERT INTO secretary_event (start, "end", title, place, note, source, external_id, created_at)'
        " VALUES (?, ?, ?, ?, ?, 'manual', NULL, ?)",
        ("2026-09-06T09:00:00", None, "手で入れた予定", "自宅", "", util.now()),
    )
    conn.commit()

    _set_url()
    ics = _ics_bytes([("evt1@example.com", "20260907T090000", "ICSの予定")])
    monkeypatch.setattr(calendar_mod.urllib.request, "urlopen", lambda req, timeout=0: _FakeResp(ics))
    result = calendar_mod.sync(home)
    assert result["ok"] is True

    manual_row = conn.execute("SELECT * FROM secretary_event WHERE source = 'manual'").fetchone()
    assert manual_row is not None
    assert manual_row["title"] == "手で入れた予定"
    assert manual_row["place"] == "自宅"

    # フィードが空になっても manual は影響を受けない（ics 由来だけが消える）。
    empty_ics = b"BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n"
    monkeypatch.setattr(calendar_mod.urllib.request, "urlopen", lambda req, timeout=0: _FakeResp(empty_ics))
    result2 = calendar_mod.sync(home)
    assert result2["removed"] == 1  # ics 由来の1件だけ消える

    manual_row2 = conn.execute("SELECT * FROM secretary_event WHERE source = 'manual'").fetchone()
    assert manual_row2 is not None
    assert manual_row2["title"] == "手で入れた予定"
    ics_rows = conn.execute("SELECT * FROM secretary_event WHERE source = 'ics'").fetchall()
    assert ics_rows == []


def test_apply_events_ignores_manual_rows_when_matching_by_external_id(home: Path, conn) -> None:
    """`apply_events` 単体でも、`external_id` が偶然一致するような細工では manual 行は
    拾われない（SELECT 自体が `source = 'ics'` に絞られているため。構造的な保証の直接確認）。
    """
    conn.execute(
        'INSERT INTO secretary_event (start, "end", title, place, note, source, external_id, created_at)'
        " VALUES (?, ?, ?, ?, ?, 'manual', ?, ?)",
        ("2026-09-06T09:00:00", None, "手で入れた予定", "", "", "evt1@example.com::2026-09-06T09:00:00", util.now()),
    )
    conn.commit()

    events = [
        {
            "uid": "evt1@example.com",
            "external_id": "evt1@example.com::2026-09-06T09:00:00",
            "title": "ICS側のタイトル",
            "start": "2026-09-06T09:00:00",
            "end": None,
            "place": "",
            "note": "",
        }
    ]
    result = calendar_mod.apply_events(conn, events)
    conn.commit()
    assert result["added"] == 1  # manual 行とは別に ics 行が新規追加される（上書きしない）
    assert result["updated"] == 0

    rows = conn.execute("SELECT source, title FROM secretary_event ORDER BY source").fetchall()
    titles_by_source = {r["source"]: r["title"] for r in rows}
    assert titles_by_source["manual"] == "手で入れた予定"
    assert titles_by_source["ics"] == "ICS側のタイトル"


def test_sync_without_secretary_table_does_not_raise(home_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`manor init` 前（`secretary_event` が無い home）でも同期は例外で落ちず、
    理由付きで `ok: False` を返す（`db.connect` は空の DB ファイルを開くだけで、
    スキーマが無いことを `apply_events` の `_require_secretary` が検知する）。
    """
    home_path.mkdir(parents=True, exist_ok=True)  # db.connect は既存のディレクトリを前提にする
    _fix_clock(monkeypatch)
    _set_url()
    ics = _ics_bytes([("evt1@example.com", "20260906T090000", "会議")])
    monkeypatch.setattr(calendar_mod.urllib.request, "urlopen", lambda req, timeout=0: _FakeResp(ics))

    result = calendar_mod.sync(home_path)
    assert result["ok"] is False
    assert "secretary" in str(result["reason"]) or "秘書" in str(result["reason"])


# --- check_connection（拡張の check()。ADR-009 D3） -----------------------------------------


def test_check_connection_without_url(home: Path, http_guard) -> None:
    result = calendar_mod.check_connection(home)
    assert result["ok"] is False
    assert "URL" in str(result["reason"])


def test_check_connection_ok(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_url()
    ics = _ics_bytes([("evt1@example.com", "20260906T090000", "会議")])
    monkeypatch.setattr(calendar_mod.urllib.request, "urlopen", lambda req, timeout=0: _FakeResp(ics))
    result = calendar_mod.check_connection(home)
    assert result["ok"] is True


def test_check_connection_never_raises(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> str:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(calendar_mod, "calendar_url", boom)
    result = calendar_mod.check_connection(home)
    assert result["ok"] is False
    assert "kaboom" in str(result["reason"])


# --- 拡張マニフェスト（extensions/calendar.py。ADR-009 D2・ADR-012 D3） ------------------------


def test_extension_manifest_shape() -> None:
    from manor.extensions import calendar as calendar_ext

    m = calendar_ext.MANIFEST
    assert m["id"] == "calendar"
    assert m["kind"] == "service"
    assert {"url"} <= {f["key"] for f in m["fields"]}  # type: ignore[index]
    assert m["secret_fields"] == ["url"]
    assert m["install_steps"]


def test_extension_detect_and_check_never_raise(home: Path, http_guard) -> None:
    from manor.extensions import calendar as calendar_ext

    detect_result = calendar_ext.detect(home)
    assert detect_result == {"installed": True, "reason": ""}
    check_result = calendar_ext.check(home)  # URL 未設定でも例外にならない
    assert check_result["ok"] is False


def test_extension_is_registered_in_real_registry(home: Path) -> None:
    from manor import extensions as ext_mod

    ids = {m["id"] for m in ext_mod.all_manifests()}
    assert "calendar" in ids
    detail = ext_mod.detail(home, "calendar")
    assert detail["status"] in ("not_installed", "needs_config", "ready", "ok", "error")


def test_extension_registered_via_real_registry_contract(home: Path, http_guard) -> None:
    from manor.extensions import calendar as calendar_ext

    detect_result = calendar_ext.detect(home)
    assert isinstance(detect_result, dict)
    assert detect_result.get("installed") is True

    check_result = calendar_ext.check(home)
    assert isinstance(check_result, dict)
    assert "ok" in check_result and "reason" in check_result


# --- CLI（`manor calendar sync|list`） -------------------------------------------------------


def test_cli_registers_calendar_group() -> None:
    from manor import cli as cli_mod

    parser = cli_mod.build_parser()
    args = parser.parse_args(["calendar", "list", "--json"])
    assert args.func is not None
    assert getattr(args, "needs_db", True) is False


def test_cli_calendar_sync_reports_missing_url(
    home: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from manor import cli as cli_mod

    _fix_clock(monkeypatch)
    rc = cli_mod.main(["calendar", "sync", "--json"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False


def test_cli_calendar_sync_and_list_round_trip(
    home: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from manor import cli as cli_mod

    _fix_clock(monkeypatch)
    _set_url()
    ics = _ics_bytes([("evt1@example.com", "20260906T090000", "定例会議")])
    monkeypatch.setattr(calendar_mod.urllib.request, "urlopen", lambda req, timeout=0: _FakeResp(ics))

    rc = cli_mod.main(["calendar", "sync", "--json"])
    assert rc == 0
    sync_out = json.loads(capsys.readouterr().out)
    assert sync_out["added"] == 1

    rc2 = cli_mod.main(["calendar", "list", "--days", "7", "--json"])
    assert rc2 == 0
    list_out = json.loads(capsys.readouterr().out)
    assert len(list_out) == 1
    assert list_out[0]["title"] == "定例会議"
    assert list_out[0]["source"] == "ics"


def test_cli_calendar_list_empty_home_reports_error(
    home_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`manor init` 前（`secretary_event` が無い home）でも CLI は例外で落ちない。"""
    from manor import cli as cli_mod

    home_path.mkdir(parents=True, exist_ok=True)  # db.connect は既存のディレクトリを前提にする
    _fix_clock(monkeypatch)
    rc = cli_mod.main(["calendar", "list"])
    assert rc == 2  # ManorError(code=2)
