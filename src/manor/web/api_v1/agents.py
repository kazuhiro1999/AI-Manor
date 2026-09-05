"""`agents`（ADR-011 D3・D4）。担当の一覧をカードで見せるための読み取り専用の1本。

**新しい判定は作らない**——語彙は `agent_meta.valid_agents`、表示名は `agent_meta.agent_label`、
要旨は `agent_meta.agent_summary`、導入されているかは `module_list` と同じ「表の有無」、
姿の有無は `face_models._model_info` と同じ判定をそのまま束ねる。読み取り専用
（`require_writable` は課さない——書き込みが無いので元から要らない）。
"""

from __future__ import annotations

from fastapi import FastAPI

from ...agent_meta import agent_label, agent_summary, valid_agents
from .._common import MODULE_DEFS, WebContext, open_conn, table_exists
from .face_models import _model_info

#: 担当 → 行き先ページ（module id）。ADR-011 D3: 「執事→タスク、料理長→台所、
#: 家政婦→家事、家令→家計、秘書→秘書。qa/auditor は行き先が無いので出さない」。
AGENT_PAGE: dict[str, str | None] = {
    "butler": "tasks",
    "chef": "kitchen",
    "housekeeper": "house",
    "steward": "money",
    "secretary": "secretary",
    "qa": None,
    "auditor": None,
}

#: `page`（module id）→ その module が依存する表の名前。`_common.MODULE_DEFS` と同じ
#: ソース（ここで新しい対応表を作らない）。table が None の module（core）は常に有効。
_MODULE_TABLE: dict[str, object] = {str(m["id"]): m["table"] for m in MODULE_DEFS}


def register(app: FastAPI, ctx: WebContext) -> None:
    @app.get("/api/v1/agents")
    def agents() -> list[dict[str, object]]:
        with open_conn(ctx) as conn:
            out: list[dict[str, object]] = []
            for agent in valid_agents():
                page = AGENT_PAGE.get(agent)
                table = _MODULE_TABLE.get(page) if page else None
                # butler/qa/auditor は表を持たない担当（module_list の table=None と同じ扱い
                # ＝常に enabled: true）。chef/housekeeper/steward/secretary は対応する
                # staff の表があるかで判定する（meta.modules の enabled と同じ判定。ADR-011 D3）。
                enabled = True if table is None else table_exists(conn, str(table))
                out.append(
                    {
                        "id": agent,
                        # ADR-011 D3「名前と役職」: この家には担当ごとの個人名が無く、
                        # 名前がそのまま役職なので label/role は同じ値を返す
                        # （曖昧だった点として報告する）。
                        "label": agent_label(agent),
                        "role": agent_label(agent),
                        "summary": agent_summary(agent),
                        "page": page,
                        "has_model": bool(_model_info(ctx, agent)["has_model"]),
                        "enabled": enabled,
                    }
                )
            return out
