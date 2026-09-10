"""夜勤が落ちたとき、**なぜ落ちたかが残ること**を検算する（2026-09-10）。

9/10 の夜勤が `exit=1` で落ちたとき、記録に残っていたのは
「claude が異常終了しました (exit=1): （理由不明）」の1行だけだった。原因を突き止めるのに
`~/.claude/projects/` の会話記録まで掘る必要があり、**それでも断定できなかった**。

捨てていたもの:

- `claude` の結果 JSON の `terminal_reason`（`max_turns` などが**そのまま入っている**）。
  runner は `"result"` フィールドだけを正規表現で探していたが、**失敗の JSON に
  `"result"` は無い**（実測: `--max-turns 1` で落とすと `terminal_reason: "max_turns"` が
  返り、`"result"` は無い）→ 必ず「（理由不明）」になる
- 標準出力・標準エラーの現物。しかも `raw = stdout + stderr` に畳んでいたので、
  **どちらが言ったのか**まで消えていた（`claude` には stderr にしか出ない失敗がある）
- 失敗時の `num_turns` / `cost` / `usage`。`_runlog_finish` が非0のときは
  `from_claude_result` を通さないので、**何ターンまで行って落ちたのか**が残らなかった

ここはその3つが残ることだけを見る。**主人のご指摘（2026-09-10）「ログを仕込んでおけませんか」。**
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from manor import db as db_mod
from manor.night import runner


def _write_tasks(home_path: Path) -> None:
    p = runner.tasks_path(home_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("| N1 | 例のタスク | 30分 |\n", encoding="utf-8")


def _failing_command(tmp_path: Path, *, stdout: str, stderr: str = "", exit_code: int = 1) -> str:
    """本物の `claude` の代わりに、決めた出力を出して決めた終了コードで死ぬコマンド。"""
    script = tmp_path / "fake_claude.py"
    script.write_text(
        "import sys\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.stderr.write({stderr!r})\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    return f'"{sys.executable}" "{script}"'


#: `--max-turns` に当たったときに `claude` が実際に返す形（2026-09-10 に実測）。
#: **`"result"` フィールドが無い**のが肝で、ここが「（理由不明）」の出どころだった。
MAX_TURNS_JSON = json.dumps(
    {
        "is_error": True,
        "num_turns": 160,
        "stop_reason": "tool_use",
        "total_cost_usd": 2.5,
        "usage": {
            "input_tokens": 2,
            "output_tokens": 100,
            "cache_creation_input_tokens": 700,
            "cache_read_input_tokens": 190000,
        },
        "permission_denials": [],
        "terminal_reason": "max_turns",
    }
)


# --- diagnose（純粋関数） ------------------------------------------------------------


def test_diagnose_reads_terminal_reason() -> None:
    diag = runner.diagnose(json.loads(MAX_TURNS_JSON), code=1, killed=False)
    assert diag["terminal_reason"] == "max_turns"
    assert diag["num_turns"] == 160
    assert diag["usage"]["cache_read_input_tokens"] == 190000
    assert diag["exit_code"] == 1


def test_diagnose_says_so_when_the_output_was_not_json() -> None:
    """**推測で埋めない。** 読めなかったことが分かる形で残す。"""
    diag = runner.diagnose(None, code=1, killed=False)
    assert diag["parsed"] is False
    assert "terminal_reason" not in diag


# --- 現物を残す ----------------------------------------------------------------------


def test_dump_keeps_stdout_and_stderr_apart(home_path: Path) -> None:
    """**混ぜない。** `claude` には stderr にしか出ない失敗がある（実例:
    `this workspace has not been trusted` で許可設定が 49 件無視される）。"""
    path = runner.write_failure_dump(
        home_path, attempt=1, argv=["claude", "-p"],
        stdout="でた（標準出力）", stderr="でた（標準エラー）",
        diag={"exit_code": 1},
    )
    assert path is not None
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["stdout_tail"] == "でた（標準出力）"
    assert saved["stderr_tail"] == "でた（標準エラー）"
    assert saved["argv"] == ["claude", "-p"]


def test_dump_does_not_pile_up_forever(home_path: Path) -> None:
    """溜め続けない（`manor-slack-inbox.log` で踏んだのと同じ形。検分 S6）。"""
    for i in range(runner.FAILURE_KEEP + 5):
        d = home_path / "night" / runner.FAILURE_DIR_NAME
        d.mkdir(parents=True, exist_ok=True)
        (d / f"2026-01-{i:02d}_000000.json").write_text("{}", encoding="utf-8")
    runner.write_failure_dump(
        home_path, attempt=1, argv=[], stdout="", stderr="", diag={},
    )
    kept = list((home_path / "night" / runner.FAILURE_DIR_NAME).glob("*.json"))
    assert len(kept) == runner.FAILURE_KEEP


# --- 通しで（`run()` が落ちたとき） ---------------------------------------------------


def test_a_failed_night_leaves_the_reason_behind(home_path: Path, tmp_path: Path) -> None:
    """9/10 の晩を再現する。**あの晩に欲しかったものが、全部残ること。**"""
    _write_tasks(home_path)
    db_mod.init(home_path)
    cmd = _failing_command(
        tmp_path,
        stdout=MAX_TURNS_JSON,
        stderr="Ignoring 49 permissions.allow entries: this workspace has not been trusted.",
    )

    result = runner.run(home_path, now="02:00", exec_cmd=cmd, echo=False)

    # ⚠ **`max_turns` はもう「晩の失敗」ではない**（2026-09-11 の作り直し。席の区切り）。
    # ここで見たいのは status ではなく「理由が残ったか」なので、そちらを見る。
    assert result["diagnosis"]["terminal_reason"] == "max_turns"
    assert result["diagnosis"]["num_turns"] == 160

    # 2. 現物が残る（stderr にしか出ていない手がかりを含めて）
    dump = Path(str(result["failure_dump"]))
    assert dump.is_file()
    saved = json.loads(dump.read_text(encoding="utf-8"))
    assert "has not been trusted" in saved["stderr_tail"]

    # 3. ログの1行を読めば理由が分かる（掘らなくてよい）
    log_text = (home_path / "night" / "logs").glob("*.log")
    body = "\n".join(p.read_text(encoding="utf-8") for p in log_text)
    assert "max_turns" in body
    assert dump.name in body

    # 4. **何ターンまで行ったか**が run 表に残る（`--max-turns` を疑えるように）
    conn = db_mod.connect(home_path)
    try:
        row = conn.execute(
            "SELECT exit_reason, turns, cost_usd, note FROM run WHERE kind = 'night'"
            " ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row["exit_reason"] == "failed"
    assert row["turns"] == 160
    assert row["cost_usd"] == 2.5


def test_unreadable_output_still_leaves_the_bytes(home_path: Path, tmp_path: Path) -> None:
    """JSON ですらない出力で落ちても、**その出力そのもの**は残る。

    ここが 9/10 に効かなかった経路——「解釈できなかった」で終わらせず、現物を置く。
    """
    _write_tasks(home_path)
    db_mod.init(home_path)
    cmd = _failing_command(tmp_path, stdout="", stderr="Error: something went very wrong")

    result = runner.run(home_path, now="02:00", exec_cmd=cmd, echo=False)

    assert result["status"] == "failed"
    assert result["diagnosis"]["parsed"] is False
    saved = json.loads(Path(str(result["failure_dump"])).read_text(encoding="utf-8"))
    assert "something went very wrong" in saved["stderr_tail"]
