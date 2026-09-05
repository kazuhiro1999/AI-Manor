"""担当の語彙と日本語表示名（ADR-008 D3・D4）。

語彙の正は `talk.available_agents`（`.claude/agents/*.md` のファイル名 + `butler`）を
そのまま使う——ここで再定義しない。表示名は部下モジュール（`staff/<name>/__init__.py`
の `LABEL`）が既にあるものを再利用し、無い担当（butler・qa・auditor）だけここで補う。

`/face`（web）と `manor face`（CLI）の両方から使うので、fastapi には依存しない
（CLI 側は fastapi が無い環境でも動く約束——`talk.py` と同じ理由）。
"""

from __future__ import annotations

from pathlib import Path

from .talk import BUTLER, available_agents

#: 部下モジュールに `LABEL` を持たない担当の表示名。
#: `steward.LABEL` は「家令（家計）」だが、小窓の題は短く保つのでここで上書きする
#: （ADR-008 D3「執事 / 料理長 / 家政婦 / 家令 / 秘書 / 検分 / 監査」の表記に合わせる）。
_LABEL_OVERRIDES: dict[str, str] = {
    BUTLER: "執事",
    "steward": "家令",
    "qa": "検分",
    "auditor": "監査",
}


def agent_label(name: str) -> str:
    """担当の日本語表示名。語彙外の名前を渡したときは名前をそのまま返す
    （呼び出し側が先に `available_agents` で検査している前提——ここではエラーにしない）。
    """
    if name in _LABEL_OVERRIDES:
        return _LABEL_OVERRIDES[name]
    try:
        mod = __import__(f"manor.staff.{name}", fromlist=["LABEL"])
    except ModuleNotFoundError:
        return name
    label = getattr(mod, "LABEL", None)
    return str(label) if label else name


def valid_agents(repo: Path | None = None) -> list[str]:
    """語彙（`butler` + `.claude/agents/*.md` の stem）。`talk.available_agents` の別名。"""
    return available_agents(repo)


#: 担当の一覧カード（ADR-011 D3）に出す一行要旨。**唯一の出どころ**——`docs/staff/*.md`
#: の「何をする人か」を要約したもの（chef/housekeeper/secretary/steward）。butler・qa・auditor は
#: `docs/staff/` に文書が無いので、`butler/SOUL.md`・`.claude/agents/{qa,auditor}.md` の
#: description から一文だけ抜いた（曖昧だった点として報告する）。
AGENT_SUMMARY: dict[str, str] = {
    BUTLER: "主人の判断待ちとタスク全体の采配、部下への委譲を担います。",
    "chef": "在庫・食事の記録・買い物リスト・好みを預かり、献立の提案と記録を行います。",
    "housekeeper": "家の中の当番・消耗品の残量・設備の手入れ周期・ゴミの日を預かります。",
    "steward": "支出の記録・定期支払いの期日管理・予算との差を扱います（支払いの実行はしません）。",
    "secretary": "予定・控え・受け渡し置き場（inbox）の仕分けと、相対日付の解決を担います。",
    "qa": "作ったものを主人に渡す前に検めます。直すのではなく、見つけて伝えます。",
    "auditor": "①層（規則・道具の定義）の肥大・矛盾を月に一度、外から点検します。",
}


def agent_summary(name: str) -> str:
    """担当の一覧カード用の一行要旨。語彙外の名前を渡したときは空文字を返す
    （呼び出し側が先に `valid_agents` で検査している前提——ここではエラーにしない）。
    """
    return AGENT_SUMMARY.get(name, "")
