"""`manor slack`（ADR-009 §3「Slack 拡張（5b）」）の試験。

**実 Slack へは一切繋がない。** HTTP は必ず `slack_mod._slack_api` を差し替える
（差し替え忘れがあれば `urllib.request.urlopen` を爆発させる `http_guard` fixture が
検出する）。`claude -p` も同様に `subprocess.run` を差し替える／そもそも呼ばせない。

時刻は `MANOR_NOW` / `MANOR_TODAY`（`conftest.home_path` が既定で削除している）を
明示的に固定する（`butler/GROWTH.md` G1: 時刻に依る試験は時計を固定して書く）。

2026-09-04 に受信の対応づけを書き直した（1メッセージ=1decision＋id優先）。このファイルの
下半分（inbox 関連）はその新しい設計に対する試験。
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

import pytest

from manor import decision as decision_mod
from manor import slack as slack_mod
from manor import task as task_mod
from manor import util


# --- 共通の下ごしらえ ---------------------------------------------------------------------


@pytest.fixture
def leak_terms(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`MANOR_LEAK_TERMS` を一時ファイルへ向ける。禁止語スキャンを試験内で完全に制御する。"""

    def _set(terms: list[str]) -> Path:
        path = tmp_path / "leak-terms.txt"
        path.write_text("\n".join(terms) + "\n" if terms else "", encoding="utf-8")
        monkeypatch.setenv("MANOR_LEAK_TERMS", str(path))
        return path

    return _set


@pytest.fixture
def http_guard(monkeypatch: pytest.MonkeyPatch):
    """`urllib.request.urlopen` を爆発させる。**呼ばれたら試験が失敗する**——
    「HTTP を一切呼ばない」ことを主張する試験は、代わりにこれが黙って通っていないかを
    保証する道具として使う（差し替え忘れの検出）。
    """

    def boom(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("urllib.request.urlopen が呼ばれました（HTTP は禁止のはずです）")

    monkeypatch.setattr(urllib.request, "urlopen", boom)


def _write_slack_config(home: Path, *, channel: str = "C123456") -> None:
    Path(home).mkdir(parents=True, exist_ok=True)
    (Path(home) / "config.toml").write_text(f"[slack]\nchannel = '{channel}'\n", encoding="utf-8")


def _make_open_decision(
    conn, *, title: str = "テスト決定", recommend: str = "承認してください"
) -> tuple[str, str]:
    task_id = task_mod.add(conn, title)
    decision_id = decision_mod.ask(conn, title, task_id=task_id, recommend=recommend, background="背景")
    conn.commit()
    return task_id, decision_id


def _insert_slack_message(conn, *, decision_id: str | None, channel: str, ts: str) -> None:
    conn.execute(
        "INSERT INTO slack_message (decision_id, channel, ts, sent_at) VALUES (?, ?, ?, ?)",
        (decision_id, channel, ts, util.now()),
    )
    conn.commit()


def _replies_response(thread_ts: str, replies: list[dict[str, object]]) -> dict[str, object]:
    messages = [{"ts": thread_ts, "text": "（元メッセージ）"}] + replies
    return {"ok": True, "messages": messages}


def _fake_replies_api(replies_by_key: dict[tuple[str, str], dict[str, object]]):
    def fake(method: str, token: str, *, params: dict[str, object] | None = None, timeout: float = 0):
        assert method == "conversations.replies", f"想定外の method です: {method}"
        params = params or {}
        key = (str(params.get("channel")), str(params.get("ts")))
        return replies_by_key.get(key, {"ok": True, "messages": []})

    return fake


def _fix_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANOR_TODAY", "2026-09-04")
    monkeypatch.setenv("MANOR_NOW", "2026-09-04T09:00:00")


# --- 禁止語スキャン（D10） ----------------------------------------------------------------


def test_scan_for_leak_terms_reports_position_not_term(leak_terms):
    leak_terms(["ひみつのあいことば"])
    text = "本日の連絡です。ひみつのあいことば が漏れています。"
    result = slack_mod.scan_for_leak_terms(text)
    assert result["ok"] is False
    assert result["position"] == text.index("ひみつのあいことば")
    # **語そのものを返さない**——reason に元の語が含まれてはいけない。
    assert "ひみつのあいことば" not in str(result["reason"])


def test_scan_for_leak_terms_passes_when_no_hit(leak_terms):
    leak_terms(["まったく関係ない語"])
    result = slack_mod.scan_for_leak_terms("こんにちは、今日の状況です。")
    assert result == {"ok": True, "reason": "", "position": None}


def test_scan_for_leak_terms_fail_closed_when_list_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("MANOR_LEAK_TERMS", str(tmp_path / "does-not-exist.txt"))
    result = slack_mod.scan_for_leak_terms("何でもない本文")
    assert result["ok"] is False
    assert result["position"] is None


def test_brief_blocked_by_banned_word_does_not_echo_term(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch, leak_terms, http_guard
):
    """decision のタイトルに禁止語を混ぜ、`brief()` がそれを検出して**何も送らない**
    （まとめ含め）ことを確かめる。HTTP は一切呼ばれない（`http_guard`）。
    `scan_failures` に語そのものは出てこない。
    """
    leak_terms(["ひみつのあいことば"])
    _write_slack_config(home)
    monkeypatch.setattr(slack_mod, "bot_token", lambda: "xoxb-test-token")
    _make_open_decision(conn, title="ひみつのあいことば を含む決定")

    result = slack_mod.brief(home, generate=False, dry_run=False)

    assert result["sent"] is False
    failures = result["scan_failures"]
    assert failures
    assert all(isinstance(f["position"], int) for f in failures)
    dumped = str(result)
    assert "ひみつのあいことば" not in dumped


# --- dry-run は HTTP を一切呼ばない（D10） --------------------------------------------------


def test_brief_dry_run_makes_no_http_call(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch, leak_terms, http_guard
):
    leak_terms([])  # 空リスト（存在はする）＝ヒットなし
    _write_slack_config(home)
    monkeypatch.setattr(slack_mod, "bot_token", lambda: "xoxb-test-token")
    _, decision_id = _make_open_decision(conn)

    result = slack_mod.brief(home, generate=False, dry_run=True)

    assert result["sent"] is False
    assert result["dry_run"] is True
    assert decision_id in result["decisions"]
    assert decision_id in result["decision_texts"]
    # slack_message は書かれていない（送っていないので）。
    rows = conn.execute("SELECT * FROM slack_message").fetchall()
    assert rows == []


# --- (a) decision が2件ある brief で、それぞれ別 ts が記録される -----------------------------


def test_brief_sends_separate_message_per_decision_with_distinct_ts(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch, leak_terms
):
    leak_terms([])
    _write_slack_config(home, channel="C999")
    monkeypatch.setattr(slack_mod, "bot_token", lambda: "xoxb-test-token")
    _, d1 = _make_open_decision(conn, title="決定1")
    _, d2 = _make_open_decision(conn, title="決定2")

    calls: list[dict[str, object]] = []
    counter = {"n": 0}

    def fake_api(method, token, *, params=None, timeout=0):  # noqa: ANN001
        assert method == "chat.postMessage"
        counter["n"] += 1
        ts = f"1700000000.{counter['n']:06d}"
        calls.append({"params": params, "ts": ts})
        return {"ok": True, "ts": ts, "channel": params["channel"]}

    monkeypatch.setattr(slack_mod, "_slack_api", fake_api)

    result = slack_mod.brief(home, generate=False, dry_run=False)

    assert result["sent"] is True
    # まとめ1通 + decision 2件 = 3回の chat.postMessage
    assert len(calls) == 3
    sent = {r["decision_id"]: r for r in result["decisions"]}
    assert sent[d1]["sent"] is True
    assert sent[d2]["sent"] is True
    assert sent[d1]["ts"] != sent[d2]["ts"]

    rows = conn.execute(
        "SELECT decision_id, ts FROM slack_message WHERE decision_id IS NOT NULL ORDER BY id"
    ).fetchall()
    assert {r["decision_id"] for r in rows} == {d1, d2}
    assert len({r["ts"] for r in rows}) == 2  # decision ごとに別 ts

    # まとめの通も記録されるが decision_id は NULL（対応する decision が無い）。
    summary_rows = conn.execute("SELECT ts FROM slack_message WHERE decision_id IS NULL").fetchall()
    assert len(summary_rows) == 1
    assert summary_rows[0]["ts"] not in {r["ts"] for r in rows}  # まとめの ts は decision の ts と重ならない


def test_brief_with_no_open_decisions_sends_summary_only(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch, leak_terms
):
    leak_terms([])
    _write_slack_config(home, channel="C999")
    monkeypatch.setattr(slack_mod, "bot_token", lambda: "xoxb-test-token")

    calls: list[dict[str, object]] = []

    def fake_api(method, token, *, params=None, timeout=0):  # noqa: ANN001
        calls.append({"method": method, "params": params})
        return {"ok": True, "ts": "1700000000.000001", "channel": "C999"}

    monkeypatch.setattr(slack_mod, "_slack_api", fake_api)

    result = slack_mod.brief(home, generate=False, dry_run=False)

    assert result["sent"] is True
    assert result["decisions"] == []
    assert len(calls) == 1  # まとめ1通だけ


# --- 機械組みの本文は claude が無くても動く（D10） -------------------------------------------


def test_mechanical_brief_works_with_claude_absent(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch, leak_terms
):
    leak_terms([])

    def boom_subprocess(*_a: object, **_k: object) -> object:
        raise AssertionError("claude を起動してはいけません（--generate 無指定のはずです）")

    monkeypatch.setattr(slack_mod.subprocess, "run", boom_subprocess)
    monkeypatch.setattr(slack_mod.shutil, "which", lambda name: None)  # claude が PATH に無い

    _, decision_id = _make_open_decision(conn, title="機械組みの確認")

    result = slack_mod.brief(home, generate=False, dry_run=True)

    assert result["generated"] is False
    assert decision_id in result["decisions"]
    assert "機械組みの確認" in result["decision_texts"][decision_id]
    assert "主人待ち" in result["text"]


def test_generate_falls_back_to_mechanical_when_claude_missing(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch, leak_terms
):
    """`--generate` を付けても `claude` が無ければ機械組みへフォールバックする
    （D10「claude が壊れていても送れる」の実装）。「まとめ」だけが対象——decision
    ごとの通はもとから常に機械組み。"""
    leak_terms([])
    monkeypatch.setattr(slack_mod.shutil, "which", lambda name: None)
    _make_open_decision(conn, title="生成フォールバックの確認")

    result = slack_mod.brief(home, generate=True, dry_run=True)

    assert result["generated"] is False
    assert "主人待ち" in result["text"]
    assert "claude が見つからない" in result["generate_note"]


# --- 受信: 1メッセージ=1decision の対応づけ・id優先・modified（D11 書き直し版） ------------------


def test_inbox_rules_decision_when_thread_ts_matches(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch
):
    """decision 専用のスレッドへの「承認」は、id が無くてもそのスレッドから引ける。"""
    _fix_clock(monkeypatch)
    monkeypatch.setattr(slack_mod, "bot_token", lambda: "xoxb-test-token")
    _, decision_id = _make_open_decision(conn)
    _insert_slack_message(conn, decision_id=decision_id, channel="C1", ts="1000.0001")

    fake = _fake_replies_api(
        {("C1", "1000.0001"): _replies_response("1000.0001", [{"ts": "1000.0002", "text": "承認"}])}
    )
    monkeypatch.setattr(slack_mod, "_slack_api", fake)

    result = slack_mod.inbox(home, dry_run=False)

    assert result["ok"] is True
    assert result["ruled"] == [
        {"channel": "C1", "ts": "1000.0002", "decision_id": decision_id, "verdict": "approved"}
    ]
    assert result["unmapped"] == []
    shown = decision_mod.show(conn, decision_id)
    assert shown["status"] == "approved"
    consumed = conn.execute("SELECT channel, ts, decision_id, verdict FROM slack_reply").fetchall()
    assert len(consumed) == 1
    assert consumed[0]["decision_id"] == decision_id
    assert consumed[0]["verdict"] == "approved"


# --- (b) 片方のスレッドへの「承認」がその decision だけを裁定する -----------------------------


def test_inbox_reply_in_one_decision_thread_rules_only_that_decision(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch
):
    _fix_clock(monkeypatch)
    monkeypatch.setattr(slack_mod, "bot_token", lambda: "xoxb-test-token")
    _, d1 = _make_open_decision(conn, title="決定1")
    _, d2 = _make_open_decision(conn, title="決定2")
    _insert_slack_message(conn, decision_id=d1, channel="C1", ts="7000.0001")
    _insert_slack_message(conn, decision_id=d2, channel="C1", ts="7000.0002")

    fake = _fake_replies_api(
        {
            ("C1", "7000.0001"): _replies_response("7000.0001", [{"ts": "7000.0011", "text": "承認"}]),
            ("C1", "7000.0002"): _replies_response("7000.0002", []),
        }
    )
    monkeypatch.setattr(slack_mod, "_slack_api", fake)

    result = slack_mod.inbox(home, dry_run=False)

    assert result["ruled"] == [
        {"channel": "C1", "ts": "7000.0011", "decision_id": d1, "verdict": "approved"}
    ]
    assert decision_mod.show(conn, d1)["status"] == "approved"
    assert decision_mod.show(conn, d2)["status"] == "open"


# --- (c) まとめの通への「承認」は裁定にならず inbox へ ----------------------------------------


def test_inbox_reply_to_summary_thread_without_id_is_not_a_ruling(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch
):
    _fix_clock(monkeypatch)
    monkeypatch.setattr(slack_mod, "bot_token", lambda: "xoxb-test-token")
    _, decision_id = _make_open_decision(conn, title="決定1")
    # まとめの通（decision_id=NULL）と、その decision 専用の通の両方を用意する
    # （実運用でも brief() は必ずこの2行を作る）。
    _insert_slack_message(conn, decision_id=None, channel="C1", ts="8000.0001")
    _insert_slack_message(conn, decision_id=decision_id, channel="C1", ts="8000.0002")

    fake = _fake_replies_api(
        {
            ("C1", "8000.0001"): _replies_response("8000.0001", [{"ts": "8000.0011", "text": "承認"}]),
            ("C1", "8000.0002"): _replies_response("8000.0002", []),
        }
    )
    monkeypatch.setattr(slack_mod, "_slack_api", fake)

    result = slack_mod.inbox(home, dry_run=False)

    assert result["ruled"] == []
    assert len(result["unmapped"]) == 1
    assert result["unmapped"][0]["ts"] == "8000.0011"
    assert decision_mod.show(conn, decision_id)["status"] == "open"


# --- (d) 「D3 承認」が id で引ける（スレッドに関わらず） --------------------------------------


def test_inbox_reply_with_leading_id_rules_by_id(home: Path, conn, monkeypatch: pytest.MonkeyPatch):
    _fix_clock(monkeypatch)
    monkeypatch.setattr(slack_mod, "bot_token", lambda: "xoxb-test-token")
    _, decision_id = _make_open_decision(conn, title="決定1")
    _insert_slack_message(conn, decision_id=None, channel="C1", ts="9000.0001")  # まとめの通
    _insert_slack_message(conn, decision_id=decision_id, channel="C1", ts="9000.0002")

    reply_text = f"{decision_id} 承認"
    fake = _fake_replies_api(
        {
            # まとめのスレッドへ id 付きで返信された、というシナリオ。
            ("C1", "9000.0001"): _replies_response("9000.0001", [{"ts": "9000.0011", "text": reply_text}]),
            ("C1", "9000.0002"): _replies_response("9000.0002", []),
        }
    )
    monkeypatch.setattr(slack_mod, "_slack_api", fake)

    result = slack_mod.inbox(home, dry_run=False)

    assert result["ruled"] == [
        {"channel": "C1", "ts": "9000.0011", "decision_id": decision_id, "verdict": "approved"}
    ]
    assert decision_mod.show(conn, decision_id)["status"] == "approved"


# --- (e) 本文の id とスレッドが食い違えば裁定せず inbox へ -----------------------------------


def test_inbox_id_conflicting_with_thread_is_not_ruled(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch
):
    _fix_clock(monkeypatch)
    monkeypatch.setattr(slack_mod, "bot_token", lambda: "xoxb-test-token")
    _, d1 = _make_open_decision(conn, title="決定1")
    _, d2 = _make_open_decision(conn, title="決定2")
    _insert_slack_message(conn, decision_id=d1, channel="C1", ts="10000.0001")
    _insert_slack_message(conn, decision_id=d2, channel="C1", ts="10000.0002")

    # d1 のスレッドの中で、本文には d2 の id を書いてしまった（食い違い）。
    reply_text = f"{d2} 承認"
    fake = _fake_replies_api(
        {
            ("C1", "10000.0001"): _replies_response("10000.0001", [{"ts": "10000.0011", "text": reply_text}]),
            ("C1", "10000.0002"): _replies_response("10000.0002", []),
        }
    )
    monkeypatch.setattr(slack_mod, "_slack_api", fake)

    result = slack_mod.inbox(home, dry_run=False)

    assert result["ruled"] == []
    assert len(result["unmapped"]) == 1
    assert d1 in result["unmapped"][0]["reason"] or d2 in result["unmapped"][0]["reason"]
    assert decision_mod.show(conn, d1)["status"] == "open"
    assert decision_mod.show(conn, d2)["status"] == "open"


def test_inbox_id_unknown_to_slack_is_not_ruled(home: Path, conn, monkeypatch: pytest.MonkeyPatch):
    """送信記録の無い id（別の decision、または存在しない id）は推測で使わない。"""
    _fix_clock(monkeypatch)
    monkeypatch.setattr(slack_mod, "bot_token", lambda: "xoxb-test-token")
    _, decision_id = _make_open_decision(conn, title="決定1")
    _insert_slack_message(conn, decision_id=decision_id, channel="C1", ts="11000.0001")

    fake = _fake_replies_api(
        {("C1", "11000.0001"): _replies_response("11000.0001", [{"ts": "11000.0011", "text": "D9999 承認"}])}
    )
    monkeypatch.setattr(slack_mod, "_slack_api", fake)

    result = slack_mod.inbox(home, dry_run=False)

    assert result["ruled"] == []
    assert len(result["unmapped"]) == 1
    assert "D9999" in result["unmapped"][0]["reason"]
    assert decision_mod.show(conn, decision_id)["status"] == "open"


# --- (f)(g) modified（修正・差し戻し） --------------------------------------------------------


def test_inbox_modify_with_note_rules_as_modified(home: Path, conn, monkeypatch: pytest.MonkeyPatch):
    _fix_clock(monkeypatch)
    monkeypatch.setattr(slack_mod, "bot_token", lambda: "xoxb-test-token")
    _, decision_id = _make_open_decision(conn)
    _insert_slack_message(conn, decision_id=decision_id, channel="C1", ts="12000.0001")

    fake = _fake_replies_api(
        {
            ("C1", "12000.0001"): _replies_response(
                "12000.0001", [{"ts": "12000.0011", "text": "修正: 宛先を変えて"}]
            )
        }
    )
    monkeypatch.setattr(slack_mod, "_slack_api", fake)

    result = slack_mod.inbox(home, dry_run=False)

    assert result["ruled"] == [
        {"channel": "C1", "ts": "12000.0011", "decision_id": decision_id, "verdict": "modified"}
    ]
    shown = decision_mod.show(conn, decision_id)
    assert shown["status"] == "modified"
    assert "宛先を変えて" in shown["ruling"]


def test_inbox_modify_without_note_is_not_a_ruling(home: Path, conn, monkeypatch: pytest.MonkeyPatch):
    _fix_clock(monkeypatch)
    monkeypatch.setattr(slack_mod, "bot_token", lambda: "xoxb-test-token")
    _, decision_id = _make_open_decision(conn)
    _insert_slack_message(conn, decision_id=decision_id, channel="C1", ts="13000.0001")

    fake = _fake_replies_api(
        {("C1", "13000.0001"): _replies_response("13000.0001", [{"ts": "13000.0011", "text": "修正:"}])}
    )
    monkeypatch.setattr(slack_mod, "_slack_api", fake)

    result = slack_mod.inbox(home, dry_run=False)

    assert result["ruled"] == []
    assert len(result["unmapped"]) == 1
    assert decision_mod.show(conn, decision_id)["status"] == "open"


def test_inbox_reject_variant_words_rules_as_rejected(home: Path, conn, monkeypatch: pytest.MonkeyPatch):
    _fix_clock(monkeypatch)
    monkeypatch.setattr(slack_mod, "bot_token", lambda: "xoxb-test-token")
    _, decision_id = _make_open_decision(conn)
    _insert_slack_message(conn, decision_id=decision_id, channel="C1", ts="14000.0001")

    fake = _fake_replies_api(
        {("C1", "14000.0001"): _replies_response("14000.0001", [{"ts": "14000.0011", "text": "だめ"}])}
    )
    monkeypatch.setattr(slack_mod, "_slack_api", fake)

    result = slack_mod.inbox(home, dry_run=False)

    assert result["ruled"] == [
        {"channel": "C1", "ts": "14000.0011", "decision_id": decision_id, "verdict": "rejected"}
    ]
    assert decision_mod.show(conn, decision_id)["status"] == "rejected"


def test_inbox_ambiguous_reply_is_not_a_ruling(home: Path, conn, monkeypatch: pytest.MonkeyPatch):
    """「OK です」のように動詞の後ろへ文が続く形は承認と読まない（保守的な解釈。D11）。"""
    _fix_clock(monkeypatch)
    monkeypatch.setattr(slack_mod, "bot_token", lambda: "xoxb-test-token")
    _, decision_id = _make_open_decision(conn)
    _insert_slack_message(conn, decision_id=decision_id, channel="C1", ts="3000.0001")

    fake = _fake_replies_api(
        {("C1", "3000.0001"): _replies_response("3000.0001", [{"ts": "3000.0002", "text": "OK です、たぶん"}])}
    )
    monkeypatch.setattr(slack_mod, "_slack_api", fake)

    result = slack_mod.inbox(home, dry_run=False)

    assert result["ruled"] == []
    assert len(result["unmapped"]) == 1
    assert decision_mod.show(conn, decision_id)["status"] == "open"


def test_inbox_same_reply_twice_rules_once(home: Path, conn, monkeypatch: pytest.MonkeyPatch):
    _fix_clock(monkeypatch)
    monkeypatch.setattr(slack_mod, "bot_token", lambda: "xoxb-test-token")
    _, decision_id = _make_open_decision(conn)
    _insert_slack_message(conn, decision_id=decision_id, channel="C1", ts="4000.0001")

    fake = _fake_replies_api(
        {("C1", "4000.0001"): _replies_response("4000.0001", [{"ts": "4000.0002", "text": "承認"}])}
    )
    monkeypatch.setattr(slack_mod, "_slack_api", fake)

    first = slack_mod.inbox(home, dry_run=False)
    assert len(first["ruled"]) == 1
    decided_at_after_first = decision_mod.show(conn, decision_id)["decided_at"]

    # Slack はスレッドの全履歴を返す。既に処理した返信を含む同じレスポンスをもう一度返しても、
    # 冪等（`slack_reply` の UNIQUE(channel, ts)）で二重に裁定しない
    # （`decision.rule` が二度目は一切呼ばれない——`decided_at` が動かないことで確かめる）。
    second = slack_mod.inbox(home, dry_run=False)
    assert second["ruled"] == []
    assert second["unmapped"] == []
    assert decision_mod.show(conn, decision_id)["decided_at"] == decided_at_after_first

    consumed = conn.execute("SELECT COUNT(*) AS n FROM slack_reply WHERE ts = '4000.0002'").fetchone()
    assert consumed["n"] == 1  # 冪等: 印は1行だけ


def test_inbox_dry_run_does_not_write_or_rule(home: Path, conn, monkeypatch: pytest.MonkeyPatch):
    _fix_clock(monkeypatch)
    monkeypatch.setattr(slack_mod, "bot_token", lambda: "xoxb-test-token")
    _, decision_id = _make_open_decision(conn)
    _insert_slack_message(conn, decision_id=decision_id, channel="C1", ts="5000.0001")

    fake = _fake_replies_api(
        {("C1", "5000.0001"): _replies_response("5000.0001", [{"ts": "5000.0002", "text": "承認"}])}
    )
    monkeypatch.setattr(slack_mod, "_slack_api", fake)

    result = slack_mod.inbox(home, dry_run=True)

    assert result["ruled"][0]["would_rule"] is True
    assert decision_mod.show(conn, decision_id)["status"] == "open"
    assert conn.execute("SELECT COUNT(*) AS n FROM slack_reply").fetchone()["n"] == 0


def test_inbox_ignores_bot_and_own_messages(home: Path, conn, monkeypatch: pytest.MonkeyPatch):
    _fix_clock(monkeypatch)
    monkeypatch.setattr(slack_mod, "bot_token", lambda: "xoxb-test-token")
    _, decision_id = _make_open_decision(conn)
    _insert_slack_message(conn, decision_id=decision_id, channel="C1", ts="6000.0001")

    fake = _fake_replies_api(
        {
            ("C1", "6000.0001"): _replies_response(
                "6000.0001",
                [
                    {"ts": "6000.0002", "text": "承認", "bot_id": "B1"},
                    {"ts": "6000.0003", "text": "承認", "subtype": "channel_join"},
                ],
            )
        }
    )
    monkeypatch.setattr(slack_mod, "_slack_api", fake)

    result = slack_mod.inbox(home, dry_run=False)

    assert result["ruled"] == []
    assert result["unmapped"] == []
    assert decision_mod.show(conn, decision_id)["status"] == "open"


# --- parse_reply の単体試験（D11 の語彙。id・承認/却下/修正） ---------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("承認", {"id": None, "verdict": "approved", "ruling": ""}),
        ("OK", {"id": None, "verdict": "approved", "ruling": ""}),
        ("ok", {"id": None, "verdict": "approved", "ruling": ""}),
        ("はい", {"id": None, "verdict": "approved", "ruling": ""}),
        ("- OK", {"id": None, "verdict": "approved", "ruling": ""}),
        ("承認。", {"id": None, "verdict": "approved", "ruling": ""}),
        ("却下", {"id": None, "verdict": "rejected", "ruling": ""}),
        ("だめ", {"id": None, "verdict": "rejected", "ruling": ""}),
        ("いいえ", {"id": None, "verdict": "rejected", "ruling": ""}),
        ("OK です", {"id": None, "verdict": None, "ruling": ""}),
        ("たぶん承認", {"id": None, "verdict": None, "ruling": ""}),
        ("よろしく", {"id": None, "verdict": None, "ruling": ""}),
        ("", {"id": None, "verdict": None, "ruling": ""}),
        ("D3 承認", {"id": "D3", "verdict": "approved", "ruling": ""}),
        ("d12 却下", {"id": "D12", "verdict": "rejected", "ruling": ""}),
        ("D5 修正: 宛先を変えて", {"id": "D5", "verdict": "modified", "ruling": "宛先を変えて"}),
        ("修正: 宛先を変えて", {"id": None, "verdict": "modified", "ruling": "宛先を変えて"}),
        ("差し戻し: 一言", {"id": None, "verdict": "modified", "ruling": "一言"}),
        ("修正:", {"id": None, "verdict": None, "ruling": ""}),
        ("修正", {"id": None, "verdict": None, "ruling": ""}),
    ],
)
def test_parse_reply(text: str, expected: dict[str, object]) -> None:
    assert slack_mod.parse_reply(text) == expected


# --- 疎通確認（`manor slack test` の実体） --------------------------------------------------


def test_test_connection_without_token(home: Path, monkeypatch: pytest.MonkeyPatch, http_guard):
    monkeypatch.setattr(slack_mod, "bot_token", lambda: None)
    result = slack_mod.test_connection(home)
    assert result["ok"] is False
    assert "bot_token" in result["reason"]


def test_test_connection_calls_auth_test(home: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(slack_mod, "bot_token", lambda: "xoxb-test-token")

    def fake_api(method, token, *, params=None, timeout=0):  # noqa: ANN001
        assert method == "auth.test"
        return {"ok": True, "team": "テストの家", "user": "butler"}

    monkeypatch.setattr(slack_mod, "_slack_api", fake_api)
    result = slack_mod.test_connection(home)
    assert result["ok"] is True
    assert result["team"] == "テストの家"


# --- 拡張マニフェスト（extensions/slack.py。ADR-009 D2） ------------------------------------


def test_extension_manifest_shape():
    from manor.extensions import slack as slack_ext

    m = slack_ext.MANIFEST
    assert m["id"] == "slack"
    assert m["kind"] == "service"
    assert {"channel", "bot_token"} <= {f["key"] for f in m["fields"]}  # type: ignore[index]
    assert m["secret_fields"] == ["bot_token"]
    assert m["install_steps"]  # 空でない


def test_extension_detect_and_check_never_raise(home: Path, monkeypatch: pytest.MonkeyPatch, http_guard):
    from manor.extensions import slack as slack_ext

    monkeypatch.setattr(slack_mod, "bot_token", lambda: None)
    detect_result = slack_ext.detect(home)
    assert detect_result == {"installed": True, "reason": ""}
    result = slack_ext.check(home)
    assert result["ok"] is False  # 秘密が無いので当然疎通できないが、例外にはならない


def test_extension_registered_via_real_registry_contract(home: Path, monkeypatch: pytest.MonkeyPatch, http_guard):
    """実際に出来上がった `extensions/__init__.py` の `_safe_detect`/`_safe_check`
    （`entry.module.detect(home)` / `entry.module.check(home)`）と同じ形で呼んでも
    落ちないことを確かめる（登録簿側への配線はこのタスクの担当外だが、契約は合わせてある）。
    """
    from manor.extensions import slack as slack_ext

    monkeypatch.setattr(slack_mod, "bot_token", lambda: None)
    detect_result = slack_ext.detect(home)
    assert isinstance(detect_result, dict)
    assert detect_result.get("installed") is True

    check_result = slack_ext.check(home)
    assert isinstance(check_result, dict)
    assert "ok" in check_result and "reason" in check_result
