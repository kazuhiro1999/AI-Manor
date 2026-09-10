"""`manor night ...` と `python -m manor.night ...` が**同じ引数を受ける**ことの検算。

2026-09-06、`--sleep-back` を `register()` 側にだけ足したところ、
`python -m manor.night run --sleep-back` が `unrecognized arguments` で落ちるように
なった。**スケジューラに登録されているのはこの起動口のほう**なので、そのままなら
夜勤が丸ごと動かない朝になっていた（気づいたのは、登録したコマンドを最後に手で
叩いてみたから——叩かなければ翌朝まで分からなかった）。

原因は `__main__.py` が引数の定義を**書き写していた**こと。いまは `_add_*()` を
呼ぶだけにしてあるが、**また書き写されたら**ここで落ちる。

`manor slack` / `manor notion` も同じ形（`register()` と `main()` の2つの口）を
持っているので、ついでに同じ検算をかける。

`manor board`（サブコマンドを持たない単一パーサ）も同じ2つの口の形なので、
検算を当てる（T10・G15 の残り）。
"""

from __future__ import annotations

import argparse

import pytest

from manor import board as board_mod
from manor import night as night_mod
from manor import notion as notion_mod
from manor import slack as slack_mod
from manor.board import __main__ as board_main
from manor.night import __main__ as night_main


def _flat_options(parser: argparse.ArgumentParser) -> set[str]:
    """サブコマンドを持たない単一パーサが定義するオプション文字列の集合。"""
    opts: set[str] = set()
    for action in parser._actions:  # noqa: SLF001 — argparse の構造を読むのが目的
        opts.update(action.option_strings)
    return opts


def _options(parser: argparse.ArgumentParser) -> dict[str, set[str]]:
    """サブコマンド名 → そのサブコマンドが受け付けるオプション文字列の集合。"""
    out: dict[str, set[str]] = {}
    for action in parser._actions:  # noqa: SLF001 — argparse の構造を読むのが目的
        if not isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            continue
        for name, sub in action.choices.items():
            opts: set[str] = set()
            for a in sub._actions:  # noqa: SLF001
                opts.update(a.option_strings)
            out[name] = opts
    return out


def _registered_parser(register) -> argparse.ArgumentParser:
    """`register(subparsers)` が足したサブコマンド群を持つ親パーサ。"""
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(dest="group"))
    # `register` は `manor <group>` の下にさらにサブコマンドを足すので、1段掘る
    for action in parser._actions:  # noqa: SLF001
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return next(iter(action.choices.values()))
    raise AssertionError("register がサブコマンドを足していません")


def test_night_entrypoints_accept_the_same_options() -> None:
    """**登録されているのは `python -m manor.night`。** こちらが痩せていたら夜勤が動かない。"""
    from_cli = _options(_registered_parser(night_mod.register))
    from_module = _options(night_main._build_arg_parser())

    assert from_cli.keys() == from_module.keys()
    for verb in from_cli:
        assert from_cli[verb] == from_module[verb], f"night {verb} の引数が食い違っています"


def test_sleep_back_reaches_the_module_entrypoint() -> None:
    """名指しの回帰試験——この旗が落ちると、夜勤が起動直後に死ぬ。"""
    args = night_main._build_arg_parser().parse_args(["run", "--sleep-back"])
    assert args.sleep_back is True


@pytest.mark.parametrize(
    ("mod", "adder", "name"),
    [
        (slack_mod, "_add_slack_subcommands", "slack"),
        (notion_mod, "_add_notion_subcommands", "notion"),
    ],
)
def test_other_two_entrypoints_share_one_builder(mod, adder: str, name: str) -> None:
    """`slack` / `notion` も `register()` と `main()` の2つの口を持っている。

    定例のジョブが叩くのは `python -m manor.<name> ...` のほうなので、ここが痩せると
    夜勤と同じ形の事故になる。**どちらも同じ `_add_*_subcommands()` を呼ぶこと**を
    確かめる——ここで組み立て直している（＝書き写している）なら、その日から腐り始める。

    ⚠ 試験の側で引数を書き写してはいけない。それをすると「試験と実装が一致している」
    ことしか言えず、**2つの起動口が一致していること**は何も言えない。
    """
    build = getattr(mod, adder)

    shared = argparse.ArgumentParser()
    build(shared.add_subparsers(dest="verb"), needs_db=None)

    from_cli = _options(_registered_parser(mod.register))
    assert from_cli == _options(shared), f"{name} の register() が共通の組み立てを使っていません"


def test_board_entrypoints_accept_the_same_options() -> None:
    """`manor board`（register()）と `python -m manor.board`（main()）が同じ引数を受けること。

    board は night/slack/notion と違いサブコマンドを持たない単一パーサ。
    """
    from_cli = _flat_options(_registered_parser(board_mod.register))
    from_module = _flat_options(board_main._build_arg_parser())

    assert from_cli == from_module, "board の register() と __main__.main() の引数が食い違っています"
