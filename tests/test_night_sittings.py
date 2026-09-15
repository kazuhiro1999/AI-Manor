"""一晩を「席（sitting）の列」として運転する仕組みの検算（2026-09-11 の作り直し）。

それまでは **一晩＝`claude -p` 一回**だった。セッションが尽きれば（`max_turns`）その晩が
終わる造りで、締切 269 分のうち使えたのは 14分45秒。一覧の最後に置いた指示へ2晩とも
届かなかった（実測: `terminal_reason=max_turns` / `num_turns=81`）。

ここで見るのは3つ:

1. **席が尽きても晩は続く** — `max_turns` は事故ではなく区切り
2. **済んだかどうかは台帳で持つ** — 散文（作業報告の見出し）を読まない
3. **空回りしない** — 宣言の無いまま2席使った指示は詰まりとして次へ回す
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

from manor import db as db_mod
from manor.night import plan, progress, runner

# `--max-turns` に当たったときに `claude` が返す形（2026-09-11 に実測）。
MAX_TURNS_JSON = json.dumps(
    {"is_error": True, "num_turns": 81, "terminal_reason": "max_turns", "subtype": "error_max_turns"}
)
DONE_JSON = json.dumps({"is_error": False, "num_turns": 12, "result": "ok"})

TASKS = """| # | 内容 | 上限 |
|---|------|------|
| N1 | 小さい仕事 | 10分 |
| N2 | 中くらいの仕事 | 20分 |
| ~~N3~~ | もう消してある仕事 | 30分 |
"""


def _write_tasks(home: Path, body: str = TASKS) -> None:
    p = runner.tasks_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def _fake_claude(tmp_path: Path, name: str, script_body: str) -> str:
    """本物の `claude` の代わり。`script_body` は python の断片。"""
    script = tmp_path / f"{name}.py"
    script.write_text("import sys, os, pathlib, json\n" + script_body, encoding="utf-8")
    return f'"{sys.executable}" "{script}"'


def _freeze_clock(monkeypatch: pytest.MonkeyPatch, fixed: datetime) -> None:
    """`runner.datetime.now()` を固定する（T69・G1と同型: 時刻に依る試験は時計を固定する）。
    ここで `fixed` を実際の実行時刻の代わりに使うことで、`--now` の偽装（開始時刻）と
    実行時刻が大きく離れた「夕方〜夜に回した」状況を、実行時刻に関わらず再現する。"""

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            return fixed if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr(runner, "datetime", _FixedDateTime)


def _far_apart_now_and_frozen_clock() -> tuple[datetime, datetime]:
    """開始（`--now`）と実行時刻（固定する `datetime.now()`）を20時間4分離して返す。
    日付は実際の「今日」に合わせる——`progress.today()` 等が使う `util.today()` は
    ここではモックしないので、食い違うと台帳のパスがずれる。"""
    today = datetime.now().date()
    first_at = datetime(today.year, today.month, today.day, 2, 0)
    frozen_now = datetime(today.year, today.month, today.day, 22, 4)
    return first_at, frozen_now


# --- 指示書を数える（純粋関数） --------------------------------------------------------


def test_plan_reads_ids_and_skips_the_struck_ones() -> None:
    items = plan.parse(TASKS)
    assert [i.id for i in items] == ["N1", "N2", "N3"]
    assert [i.id for i in plan.live(items)] == ["N1", "N2"]
    assert items[0].title.startswith("小さい仕事")


def test_plan_ignores_the_header_row() -> None:
    """`| # | 内容 |` は記号の形が違うので自然に外れる。"""
    assert all(i.id != "#" for i in plan.parse(TASKS))


# --- 台帳 ----------------------------------------------------------------------------


def test_ledger_only_counts_what_was_declared(home_path: Path) -> None:
    """**黙って済んだことにはならない。** 宣言が無ければ「まだ」。"""
    assert progress.settled(home_path, "2026-09-11") == set()
    progress.mark(home_path, "2026-09-11", "N1", progress.DONE, note="やった")
    progress.mark(home_path, "2026-09-11", "N2", progress.DOING)
    assert progress.settled(home_path, "2026-09-11") == {"N1"}  # doing はまだ


def test_ledger_keeps_the_history(home_path: Path) -> None:
    progress.mark(home_path, "2026-09-11", "N1", progress.DOING)
    progress.mark(home_path, "2026-09-11", "N1", progress.DONE, note="2席目で済んだ")
    entry = progress.load(home_path, "2026-09-11")["items"]["N1"]
    assert [h["state"] for h in entry["history"]] == ["doing", "done"]


def test_a_broken_ledger_does_not_stop_the_night(home_path: Path) -> None:
    p = progress.path(home_path, "2026-09-11")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("これは JSON ではありません", encoding="utf-8")
    assert progress.load(home_path, "2026-09-11")["recovered"] is True


# --- 一晩の運転 ----------------------------------------------------------------------


def test_the_night_continues_after_a_sitting_runs_out_of_turns(
    home_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**これがこの作り直しの本題。**

    1席目はターン上限で切れる。2席目が残りを片付ける。それまでの造りでは
    1席目で晩が終わっていた。

    実行時刻は「開始（`--now=02:00`）から20時間4分後」に固定する（T69: 2席目の
    「締切まで残り」を実時刻そのものに任せていた造りでは、pytest を夕方〜夜に
    回すと `left` が大きく負になり、2席目に進めなかった）。
    """
    _write_tasks(home_path)
    first_at, frozen_now = _far_apart_now_and_frozen_clock()
    _freeze_clock(monkeypatch, frozen_now)
    db_mod.init(home_path)
    counter = tmp_path / "count.txt"
    cmd = _fake_claude(
        tmp_path,
        "two_sittings",
        f"""
c = pathlib.Path(r"{counter}")
n = int(c.read_text()) + 1 if c.is_file() else 1
c.write_text(str(n))
home = pathlib.Path(os.environ["MANOR_HOME"])
d = home / "night" / "progress"
d.mkdir(parents=True, exist_ok=True)
if n == 1:
    # 1本だけ宣言して、ターン上限で切れる
    sys.stdout.write({MAX_TURNS_JSON!r})
    (d / "mark1.txt").write_text("x")
    sys.exit(1)
sys.stdout.write({DONE_JSON!r})
sys.exit(0)
""",
    )

    # 1席目が N1 を、2席目が N2 を宣言する形を、台帳を直に書いて再現する
    def _declare(item: str) -> None:
        progress.mark(home_path, progress.today(), item, progress.DONE)

    original = runner._run_sitting

    def spy(*args, **kwargs):  # noqa: ANN002, ANN003
        out = original(*args, **kwargs)
        _declare("N1" if out["kind"] == "max_turns" else "N2")
        return out

    runner._run_sitting = spy  # type: ignore[assignment]
    try:
        result = runner.run(home_path, now=first_at.isoformat(), exec_cmd=cmd, echo=False)
    finally:
        runner._run_sitting = original  # type: ignore[assignment]

    assert int(counter.read_text()) == 2, "2席目が設けられていない"
    assert result["status"] == "done"
    assert result["sittings"] == 2
    assert set(result["progress"]["done"]) == {"N1", "N2"}


def test_a_sitting_that_declares_nothing_twice_is_marked_stuck(
    home_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**空回りの歯止め。** 何も宣言しない席が2回続いたら、その指示は詰まりにして次へ。

    実行時刻を開始（`--now`）から20時間4分後に固定する（T69。上のテストと同じ理由）。
    """
    _write_tasks(home_path, "| N1 | 進まない仕事 | 10分 |\n")
    first_at, frozen_now = _far_apart_now_and_frozen_clock()
    _freeze_clock(monkeypatch, frozen_now)
    db_mod.init(home_path)
    cmd = _fake_claude(tmp_path, "barren", f'sys.stdout.write({DONE_JSON!r})\nsys.exit(0)\n')

    result = runner.run(home_path, now=first_at.isoformat(), exec_cmd=cmd, echo=False)

    assert result["progress"]["stuck"] == ["N1"]
    assert result["sittings"] == 2, "3席目を設けてはいけない"


def test_the_night_stops_when_everything_is_declared(home_path: Path, tmp_path: Path) -> None:
    """全部済んだら、締切が余っていても席を設けない。"""
    _write_tasks(home_path)
    db_mod.init(home_path)
    for item in ("N1", "N2"):
        progress.mark(home_path, progress.today(), item, progress.DONE)
    cmd = _fake_claude(tmp_path, "never", 'raise SystemExit("呼ばれてはいけない")\n')

    result = runner.run(home_path, now="02:00", exec_cmd=cmd, echo=False)

    assert result["status"] == "done"
    assert result["sittings"] == 0


def test_a_broken_sitting_stops_the_night(home_path: Path, tmp_path: Path) -> None:
    """**同じ壊れ方をもう一度させない。** `max_turns` 以外の失敗では席を重ねない。"""
    _write_tasks(home_path)
    db_mod.init(home_path)
    counter = tmp_path / "n.txt"
    cmd = _fake_claude(
        tmp_path,
        "broken",
        f"""
c = pathlib.Path(r"{counter}")
c.write_text(str(int(c.read_text()) + 1 if c.is_file() else 1))
sys.stderr.write("Error: something went very wrong")
sys.exit(1)
""",
    )

    result = runner.run(home_path, now="02:00", exec_cmd=cmd, echo=False)

    assert result["status"] == "failed"
    assert int(counter.read_text()) == 1, "壊れた席を重ねてはいけない"


# --- プロンプトに差す一覧 --------------------------------------------------------------


def test_the_prompt_tells_the_sitting_what_is_left_and_what_is_done() -> None:
    items = plan.live(plan.parse(TASKS))
    block = runner.build_plan_block(items[1:], {"N1"}, 2)
    assert "2 席目" in block
    assert "**N2**" in block
    assert "N1" in block.split("手を付けないもの")[1][:40]
    assert "manor night done" in block
