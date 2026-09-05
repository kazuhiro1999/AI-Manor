"""家庭のルール（ADR-005 §2「rules」）の試験。**合成データのみ**（架空の家庭。人名は入らない）。

`rule.py` の関数（core パターン）と `manor rule ...`（CLI）の両方を確かめる。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manor import cli
from manor import rule as rule_mod
from manor.errors import ManorError

# --- rule.py（関数レベル） ---------------------------------------------------------------


def test_add_and_get_roundtrip(conn) -> None:
    rule_id = rule_mod.add(conn, "門限", body="22時までに帰る", scope="kids", tags="生活、門限")
    data = rule_mod.get(conn, rule_id)
    assert data["title"] == "門限"
    assert data["scope"] == "kids"
    assert data["tag_list"] == ["生活", "門限"]
    assert data["archived_at"] is None


def test_add_rejects_empty_title(conn) -> None:
    with pytest.raises(ManorError):
        rule_mod.add(conn, "   ")


def test_add_rejects_unknown_scope(conn) -> None:
    with pytest.raises(ManorError):
        rule_mod.add(conn, "タイトル", scope="よそ者")


def test_split_tags_accepts_comma_and_touten() -> None:
    """タグの区切りは読点（、）とカンマ（, ／ ，）の両方（ADR-005 §7 の裁定）。"""
    assert rule_mod.split_tags("食事、掃除,来客，夜") == ["食事", "掃除", "来客", "夜"]
    assert rule_mod.split_tags("") == []
    assert rule_mod.split_tags(None) == []


def test_split_tags_trims_whitespace() -> None:
    assert rule_mod.split_tags(" 食事 , 掃除 、 来客 ") == ["食事", "掃除", "来客"]


def test_set_updates_only_given_fields(conn) -> None:
    rule_id = rule_mod.add(conn, "元のタイトル", body="元の本文", scope="family", tags="生活")
    rule_mod.set(conn, rule_id, title="新しいタイトル")
    data = rule_mod.get(conn, rule_id)
    assert data["title"] == "新しいタイトル"
    assert data["body"] == "元の本文"  # 触っていない項目は変わらない
    assert data["tags"] == "生活"


def test_set_unknown_rule_is_code_2(conn) -> None:
    with pytest.raises(ManorError) as excinfo:
        rule_mod.set(conn, 999, title="いない")
    assert excinfo.value.code == 2


def test_archive_sets_archived_at_and_excludes_from_default_list(conn) -> None:
    rule_id = rule_mod.add(conn, "アーカイブ対象")
    result = rule_mod.archive(conn, rule_id)
    assert result["archived_at"]

    active_only = rule_mod.list_rules(conn)
    assert rule_id not in [r["id"] for r in active_only]

    with_archived = rule_mod.list_rules(conn, include_archived=True)
    assert rule_id in [r["id"] for r in with_archived]


def test_list_rules_filters_by_exact_tag_not_substring(conn) -> None:
    """タグの一致は「タグそのもの」——部分文字列一致ではない（「家」で「家事」を拾わない）。"""
    rule_mod.add(conn, "家事の分担", tags="家事、生活")
    rule_mod.add(conn, "帰省の作法", tags="家、行事")
    hits = rule_mod.list_rules(conn, tag="家")
    assert [r["title"] for r in hits] == ["帰省の作法"]


def test_get_unknown_rule_is_code_2(conn) -> None:
    with pytest.raises(ManorError) as excinfo:
        rule_mod.get(conn, 999)
    assert excinfo.value.code == 2


# --- CLI（`manor rule ...`） ------------------------------------------------------------


def test_cli_rule_add_list_show_set_archive_flow(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()

    assert cli.main(["rule", "add", "来客対応", "--scope", "adults", "--tags", "来客,礼儀", "--json"]) == 0
    add_out = json.loads(capsys.readouterr().out)
    rule_id = add_out["id"]

    assert cli.main(["rule", "list", "--json"]) == 0
    list_out = json.loads(capsys.readouterr().out)
    assert any(r["id"] == rule_id for r in list_out)

    assert cli.main(["rule", "list", "--tag", "来客", "--json"]) == 0
    tag_out = json.loads(capsys.readouterr().out)
    assert [r["id"] for r in tag_out] == [rule_id]

    assert cli.main(["rule", "show", str(rule_id), "--json"]) == 0
    show_out = json.loads(capsys.readouterr().out)
    assert show_out["scope"] == "adults"

    assert cli.main(["rule", "set", str(rule_id), "--body", "手土産は玄関で受け取る", "--json"]) == 0
    capsys.readouterr()

    assert cli.main(["rule", "show", str(rule_id), "--json"]) == 0
    updated = json.loads(capsys.readouterr().out)
    assert updated["body"] == "手土産は玄関で受け取る"

    assert cli.main(["rule", "archive", str(rule_id), "--json"]) == 0
    archive_out = json.loads(capsys.readouterr().out)
    assert archive_out["id"] == rule_id

    assert cli.main(["rule", "list", "--json"]) == 0
    after_archive = json.loads(capsys.readouterr().out)
    assert rule_id not in [r["id"] for r in after_archive]

    assert cli.main(["rule", "list", "--all", "--json"]) == 0
    with_archived = json.loads(capsys.readouterr().out)
    assert rule_id in [r["id"] for r in with_archived]


def test_cli_rule_add_unknown_scope_is_exit_2(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    code = cli.main(["rule", "add", "だめなルール", "--scope", "よそ者"])
    assert code == 2


def test_cli_rule_show_unknown_id_is_exit_2(home_path: Path, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["init"]) == 0
    capsys.readouterr()
    code = cli.main(["rule", "show", "999"])
    assert code == 2
