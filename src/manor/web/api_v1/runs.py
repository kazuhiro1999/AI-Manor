"""`runs`（ADR-006 §3 D11・§6 担当C「稼働と費用」）。`run` 表の読み取りだけをする。

**書き込みはしない**（ADR-005 §1 D3 と同じ規則）。読みは担当A（core）の `src/manor/runlog.py`
（`list_runs` / `stats`）をそのまま呼ぶ——CLI の `manor run list` / `manor run stats`
（D11）と同じ集計ロジックを使う（web だけ別の数字にならないように）。

`run` 表が無い DB（`--read-only` は `create_app` が `db.init()` を呼ばないので、
移行前の古い home ではまだ表が無いことがある。`app.py` 参照）では 500 にしない。
部下の表と同じ約束（`kitchen`/`house`/... の `available: false`）にならい、
`{"available": false, ...}` を返す（`_common.table_exists` で見る）。
"""

from __future__ import annotations

from fastapi import FastAPI

from ... import runlog as runlog_mod
from ...errors import ManorError
from .._common import WebContext, manor_error_to_http, open_conn, table_exists


def register(app: FastAPI, ctx: WebContext) -> None:
    @app.get("/api/v1/runs")
    def list_runs(kind: str | None = None, days: int = 30) -> dict[str, object]:
        with open_conn(ctx) as conn:
            if not table_exists(conn, "run"):
                return {"available": False, "runs": []}
            try:
                runs = runlog_mod.list_runs(conn, kind=kind, days=days)
            except ManorError as exc:
                # 語彙外の kind（ADR-005 §2 の裁定7: `ManorError(code=2)` は 404）。
                raise manor_error_to_http(exc)
            return {"available": True, "runs": runs}

    @app.get("/api/v1/runs/stats")
    def stats(days: int = 30) -> dict[str, object]:
        with open_conn(ctx) as conn:
            if not table_exists(conn, "run"):
                return {"available": False, "by_kind": [], "total_cost_usd": 0.0}
            by_kind_raw = runlog_mod.stats(conn, days=days)
            # `runlog.stats` は `fail_rate`（比率）を返す。画面の要件（担当Cの契約）は
            # 「件数」（失敗件数）なので、count × fail_rate から復元する（両方とも
            # `runlog.stats` が同じ行集合から計算した値なので、四捨五入の誤差は出ない
            # ——count が小さい家庭用途の規模では安全）。
            by_kind: list[dict[str, object]] = []
            total_cost_usd = 0.0
            for row in by_kind_raw:
                count = int(row["count"])
                fail_rate = float(row.get("fail_rate") or 0.0)
                cost_usd = float(row.get("cost_usd") or 0.0)
                total_cost_usd += cost_usd
                by_kind.append(
                    {
                        "kind": row["kind"],
                        "count": count,
                        "cost_usd": cost_usd,
                        "avg_seconds": row.get("avg_seconds"),
                        "failed": round(fail_rate * count) if count else 0,
                        "input_tokens": int(row.get("input_tokens") or 0),
                        "output_tokens": int(row.get("output_tokens") or 0),
                    }
                )
            return {"available": True, "by_kind": by_kind, "total_cost_usd": round(total_cost_usd, 4)}
