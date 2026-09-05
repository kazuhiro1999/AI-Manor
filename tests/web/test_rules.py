"""`rules`（家庭のルール。ADR-005 §2「新設」）の試験。**合成データのみ**。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from manor import rule as rule_mod
from manor.web import app as web_app_mod


def make_client(home: Path, *, read_only: bool = False) -> TestClient:
    return TestClient(web_app_mod.create_app(home, read_only=read_only))


def test_rules_list_empty(home: Path) -> None:
    client = make_client(home)
    res = client.get("/api/v1/rules")
    assert res.status_code == 200
    assert res.json() == []


def test_rules_create_and_list(home: Path) -> None:
    client = make_client(home)
    res = client.post(
        "/api/v1/rules", json={"title": "門限", "body": "22時までに帰る", "scope": "kids", "tags": "生活、門限"}
    )
    assert res.status_code == 200
    created = res.json()
    assert created["title"] == "門限"
    assert created["tag_list"] == ["生活", "門限"]

    listed = client.get("/api/v1/rules").json()
    assert len(listed) == 1
    assert listed[0]["id"] == created["id"]


def test_rules_list_filters_by_tag(conn, home: Path) -> None:
    rule_mod.add(conn, "来客対応", tags="来客,礼儀")
    rule_mod.add(conn, "洗濯の順番", tags="家事")
    conn.commit()
    client = make_client(home)
    res = client.get("/api/v1/rules", params={"tag": "来客"})
    body = res.json()
    assert len(body) == 1
    assert body[0]["title"] == "来客対応"


def test_rules_update_partial(conn, home: Path) -> None:
    rule_id = rule_mod.add(conn, "元のルール", body="元の本文")
    conn.commit()
    client = make_client(home)
    res = client.put(f"/api/v1/rules/{rule_id}", json={"body": "書き直した本文"})
    assert res.status_code == 200
    body = res.json()
    assert body["title"] == "元のルール"
    assert body["body"] == "書き直した本文"


def test_rules_update_unknown_is_404(home: Path) -> None:
    client = make_client(home)
    res = client.put("/api/v1/rules/999", json={"body": "だめ"})
    assert res.status_code == 404


def test_rules_delete_archives_not_deletes(conn, home: Path) -> None:
    rule_id = rule_mod.add(conn, "アーカイブ対象")
    conn.commit()
    client = make_client(home)
    res = client.delete(f"/api/v1/rules/{rule_id}")
    assert res.status_code == 200
    assert res.json()["archived_at"]

    row = conn.execute("SELECT archived_at FROM rule WHERE id = ?", (rule_id,)).fetchone()
    assert row is not None  # 物理削除していない
    assert row["archived_at"]

    active_list = client.get("/api/v1/rules").json()
    assert active_list == []
    all_list = client.get("/api/v1/rules", params={"all": "true"}).json()
    assert len(all_list) == 1


def test_rules_create_unknown_scope_is_404(home: Path) -> None:
    """`scope` の語彙外は `rule.py` が `ManorError(code=2)` で拒否する——`task.py` の
    「語彙外」判定と同じ流儀（`_common.manor_error_to_http` の約束どおり 404）。
    """
    client = make_client(home)
    res = client.post("/api/v1/rules", json={"title": "だめなルール", "scope": "よそ者"})
    assert res.status_code == 404


def test_rules_read_only_blocks_create(home: Path) -> None:
    client = make_client(home, read_only=True)
    res = client.post("/api/v1/rules", json={"title": "書けないはず"})
    assert res.status_code == 403
