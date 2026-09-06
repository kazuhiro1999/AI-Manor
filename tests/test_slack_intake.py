"""`manor slack intake`（v1 `apps/slack-relay/watch-inbox.ps1` の `#task` / `#log` 部分の
移植。T4・2026-09-06）の試験。

**実 Slack へは一切繋がない。** `slack_mod._slack_api` を必ず差し替える。

v1 がここで踏んだ事故を、そのまま試験にしてある:

- 2026-08-26: `#task` だけ（本文なし）の投稿を**無言で捨て**、主人が気づけなかった
  → `test_prefix_without_body_still_replies`
- 読んだ位置を「取り込んだ位置」で代用すると、`#task` が来ない限り窓が動かず
  **16分前の投稿を永遠に見落とす**
  → `test_cursor_advances_even_when_nothing_matched`
"""

from __future__ import annotations

from pathlib import Path

import pytest

from manor import slack as slack_mod


# --- 接頭辞の読み取り（純粋関数。Slack も DB も要らない） ---------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("#task 評価をまとめる", {"kind": "task", "body": "評価をまとめる"}),
        # **日本語は空白を空けずに続けて書く。** 弾くと主人の言葉が黙って消える
        ("#task評価をまとめる", {"kind": "task", "body": "評価をまとめる"}),
        ("#TASK 大文字でも読む", {"kind": "task", "body": "大文字でも読む"}),
        ("- #task 箇条書きの頭が付いていても読む", {"kind": "task", "body": "箇条書きの頭が付いていても読む"}),
        ("  #task  前後の空白は落とす  ", {"kind": "task", "body": "前後の空白は落とす"}),
        ("#log P4 実装を進めた", {"kind": "log", "body": "P4 実装を進めた"}),
        # 本文が無くても None にしない（＝呼び出し元が案内を返せる）
        ("#task", {"kind": "task", "body": ""}),
        ("#log", {"kind": "log", "body": ""}),
        # 別の語を吸い込まない
        ("#tasks 別の語", None),
        ("#taskZ", None),
        ("#task_x", None),
        # 接頭辞の無い雑談には反応しない（会話に割り込まない）
        ("今日は暑いですね", None),
        ("", None),
    ],
)
def test_parse_intake(text: str, expected: dict[str, str] | None) -> None:
    assert slack_mod.parse_intake(text) == expected


# --- 取り込み本体 -------------------------------------------------------------------------


@pytest.fixture
def leak_terms(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`MANOR_LEAK_TERMS` を一時ファイルへ向ける（`tests/test_slack.py` と同じ道具）。
    隔離しないと、開発機に語彙リストがあるかどうかで結果が変わる。
    """

    def _set(terms: list[str]) -> Path:
        path = tmp_path / "leak-terms.txt"
        path.write_text("\n".join(terms) + "\n" if terms else "", encoding="utf-8")
        monkeypatch.setenv("MANOR_LEAK_TERMS", str(path))
        return path

    return _set


def _write_slack_config(home: Path, *, channel: str = "C123456") -> None:
    Path(home).mkdir(parents=True, exist_ok=True)
    (Path(home) / "config.toml").write_text(f"[slack]\nchannel = '{channel}'\n", encoding="utf-8")


def _history(messages: list[dict[str, object]], *, page_size: int | None = None):
    """`conversations.history` だけに答える偽 API。送信は記録して ok を返す。

    `page_size` を渡すと、**新しい側から**その件数ずつ `has_more` ＋ `next_cursor` で
    返す（Slack の実際の返し方に合わせる。S4 の試験用）。
    """
    posted: list[dict[str, object]] = []
    newest_first = list(reversed(messages))  # Slack は新しい順に返す

    def fake(method: str, token: str, *, params: dict[str, object] | None = None, timeout: float = 0):
        if method == "conversations.history":
            if page_size is None:
                return {"ok": True, "messages": newest_first}
            start = int((params or {}).get("cursor") or 0)
            chunk = newest_first[start:start + page_size]
            nxt = start + page_size
            has_more = nxt < len(newest_first)
            out: dict[str, object] = {"ok": True, "messages": chunk, "has_more": has_more}
            if has_more:
                out["response_metadata"] = {"next_cursor": str(nxt)}
            return out
        if method == "chat.postMessage":
            posted.append(params or {})
            return {"ok": True, "ts": "9999.0001"}
        raise AssertionError(f"想定外の method です: {method}")

    return fake, posted


def _setup(home: Path, monkeypatch: pytest.MonkeyPatch, leak_terms, messages, *, page_size=None):
    leak_terms([])
    _write_slack_config(home)
    monkeypatch.setattr(slack_mod, "bot_token", lambda: "xoxb-test-token")
    fake, posted = _history(messages, page_size=page_size)
    monkeypatch.setattr(slack_mod, "_slack_api", fake)
    return posted


def test_task_message_files_a_task_and_replies(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch, leak_terms
) -> None:
    posted = _setup(
        home, monkeypatch, leak_terms,
        [{"ts": "1000.0001", "text": "#task 来週の会までに評価をまとめる"}],
    )

    result = slack_mod.intake(home)

    assert result["ok"] is True
    assert result["replied"] is True
    taken = result["taken"]
    assert len(taken) == 1 and taken[0]["kind"] == "task"
    task_id = taken[0]["node_id"]

    row = conn.execute("SELECT title, body FROM node WHERE id = ?", (task_id,)).fetchone()
    assert row is not None
    assert "評価をまとめる" in row["title"]
    # **本文は丸ごと残す**（要約して捨てない）
    assert row["body"] == "来週の会までに評価をまとめる"
    assert task_id in str(posted[0]["text"])


def test_log_message_files_a_note(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch, leak_terms
) -> None:
    _setup(home, monkeypatch, leak_terms, [{"ts": "1000.0002", "text": "#log P4 実装を進めた"}])

    result = slack_mod.intake(home)

    note_id = result["taken"][0]["node_id"]
    row = conn.execute("SELECT kind FROM node WHERE id = ?", (note_id,)).fetchone()
    assert row["kind"] == "note"


def test_prefix_without_body_still_replies(
    home: Path, monkeypatch: pytest.MonkeyPatch, leak_terms
) -> None:
    """**沈黙で終わらせない。** 本文の無い `#task` にも書き方の案内を返す（v1 2026-08-26）。"""
    posted = _setup(home, monkeypatch, leak_terms, [{"ts": "1000.0003", "text": "#task"}])

    result = slack_mod.intake(home)

    assert result["replied"] is True
    assert "本文がありません" in str(posted[0]["text"])
    assert result["taken"][0]["node_id"] is None  # 起票はしていない


def test_plain_chatter_is_ignored_without_reply(
    home: Path, monkeypatch: pytest.MonkeyPatch, leak_terms
) -> None:
    """接頭辞の無い投稿には**返信しない**（反応すると会話に割り込む）。"""
    posted = _setup(home, monkeypatch, leak_terms, [{"ts": "1000.0004", "text": "今日は暑いですね"}])

    result = slack_mod.intake(home)

    assert result["ok"] is True
    assert result["replied"] is False
    assert posted == []


def test_bot_and_thread_replies_are_skipped(
    home: Path, monkeypatch: pytest.MonkeyPatch, leak_terms
) -> None:
    """Bot 自身の投稿（無限ループ）と、スレッドの返信（`inbox()` の担当）は読まない。"""
    _setup(
        home, monkeypatch, leak_terms,
        [
            {"ts": "1000.0005", "text": "#task ボットの投稿", "bot_id": "B1"},
            {"ts": "1000.0006", "text": "#task スレッドの返信", "thread_ts": "999.0001"},
        ],
    )

    result = slack_mod.intake(home)

    assert result["taken"] == []
    assert result["replied"] is False


# --- subtype は名指しで弾く（検分 S3） ------------------------------------------------------


def test_file_share_with_a_task_prefix_is_not_dropped(
    home: Path, monkeypatch: pytest.MonkeyPatch, leak_terms
) -> None:
    """**これが S3 の本体。** 画像に添えて `#task これを直す` と書いたら、消えてはいけない。

    以前は `subtype` が付いていれば全部落としていたので、`file_share` ごと消えていた——
    `parse_intake` が「黙って捨てない」ために作った分岐と矛盾していた。
    """
    _setup(
        home, monkeypatch, leak_terms,
        [{"ts": "1000.0010", "text": "#task これを直す", "subtype": "file_share"}],
    )

    result = slack_mod.intake(home)

    assert len(result["taken"]) == 1
    assert result["taken"][0]["kind"] == "task"


@pytest.mark.parametrize("subtype", ["channel_join", "channel_name", "bot_message", "message_changed"])
def test_noise_subtypes_are_ignored(
    home: Path, monkeypatch: pytest.MonkeyPatch, leak_terms, subtype: str
) -> None:
    """雑音は名指しで弾く（実物のチャンネルで数えた4種を含む）。"""
    _setup(
        home, monkeypatch, leak_terms,
        [{"ts": "1000.0011", "text": "#task 雑音に紛れた文字列", "subtype": subtype}],
    )

    assert slack_mod.intake(home)["taken"] == []


# --- has_more を最後まで追う（検分 S4） -----------------------------------------------------


def test_reads_past_the_first_page(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch, leak_terms
) -> None:
    """**1ページで止めない。** Slack は新しい側から返すので、止めると古い側が
    永久に読まれない（位置だけが先に進んでしまう）。
    """
    messages = [{"ts": f"1000.{i:04d}", "text": f"#task {i} 番目"} for i in range(1, 8)]
    _setup(home, monkeypatch, leak_terms, messages, page_size=3)

    result = slack_mod.intake(home)

    assert len(result["taken"]) == 7
    # いちばん古いもの（1ページ目には入らない）も起票されている
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM node WHERE title LIKE '1 番目%'"
    ).fetchone()["n"] == 1


def test_truncated_history_fails_instead_of_advancing_the_cursor(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch, leak_terms
) -> None:
    """読み切れないときは**失敗として返す**——位置を進めると、読んでいない側が消える。"""
    messages = [{"ts": f"1000.{i:04d}", "text": f"#task {i}"} for i in range(1, 30)]
    _setup(home, monkeypatch, leak_terms, messages, page_size=2)
    monkeypatch.setattr(slack_mod, "INTAKE_MAX_PAGES", 3)

    result = slack_mod.intake(home)

    assert result["ok"] is False
    assert "新着が多すぎます" in result["reason"]
    # 位置は進んでいない（次の起動で読み直せる）
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM meta WHERE key LIKE 'slack_intake_cursor:%'"
    ).fetchone()["n"] == 0


def test_same_message_is_not_taken_twice(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch, leak_terms
) -> None:
    """冪等: 何度回しても同じ投稿からタスクは1本しか生まれない。"""
    _setup(home, monkeypatch, leak_terms, [{"ts": "1000.0007", "text": "#task 一度だけ起票する"}])

    first = slack_mod.intake(home)
    second = slack_mod.intake(home)

    assert len(first["taken"]) == 1
    assert second["taken"] == []
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM node WHERE title LIKE '一度だけ起票する%'"
    ).fetchone()["n"]
    assert count == 1


def test_cursor_advances_even_when_nothing_matched(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch, leak_terms
) -> None:
    """**読んだ位置は、接頭辞の無い投稿でも進む。**

    進まないと窓が「15分前」から動かず、16分前に届いた `#task` を永遠に見落とす
    （v1 が `watch-state.json` を別に持っていた理由）。
    """
    _setup(home, monkeypatch, leak_terms, [{"ts": "1700.0001", "text": "ただの雑談"}])

    slack_mod.intake(home)

    row = conn.execute(
        "SELECT value FROM meta WHERE key = ?", (slack_mod._intake_cursor_key("C123456"),)
    ).fetchone()
    assert row is not None and row["value"] == "1700.0001"


def test_dry_run_files_nothing_and_posts_nothing(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch, leak_terms
) -> None:
    posted = _setup(home, monkeypatch, leak_terms, [{"ts": "1000.0008", "text": "#task 下見だけ"}])

    result = slack_mod.intake(home, dry_run=True)

    assert result["dry_run"] is True
    assert posted == []
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM node WHERE title LIKE '下見だけ%'"
    ).fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM slack_intake").fetchone()["n"] == 0


def test_leak_term_blocks_the_reply_but_keeps_the_intake(
    home: Path, conn, monkeypatch: pytest.MonkeyPatch, leak_terms
) -> None:
    """禁止語は**返信を止める**が、取り込み自体は残す——巻き戻すと次の起動で二重起票になる。"""
    posted = _setup(
        home, monkeypatch, leak_terms, [{"ts": "1000.0009", "text": "#task ひみつのあいことば の件"}]
    )
    leak_terms(["ひみつのあいことば"])

    result = slack_mod.intake(home)

    assert result["ok"] is False
    assert result["replied"] is False
    assert posted == []
    assert conn.execute("SELECT COUNT(*) AS n FROM slack_intake").fetchone()["n"] == 1
    assert "ひみつのあいことば" not in str(result.get("reason", ""))
