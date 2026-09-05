"""`manor init --demo` — 誰でも試せる合成データ（ROADMAP §8 6b・6f・ADR-004 D10）。

`seed(home)` は **DB が空のとき（task が0件）だけ** 架空の家庭のデータを入れる。
公開リポジトリのクローン直後でも `manor board` や部下の CLI をすぐ試せるようにするための
作り物のデータで、**人名は入れない**（プロジェクト名は「引っ越しの準備」「家族旅行の計画」
「執事の改良」のように用途を表すものだけ）。本物の `home/` を汚さないよう、
呼び出し側（`manor init --demo`）は空の home にだけ使う想定——このモジュール自身も
空でなければ何もしないことでそれを守る（冪等）。

書き込みは core（task/project/milestone）は `task.py`/`project.py`/`graph.py` の
API を通す（状態機械・decision の自動生成をそのまま使うため）。部下（chef/housekeeper/
steward/secretary）の表は、各担当の `cli.py` が普段やっているのと同じ形の生 SQL で
直接 INSERT する（担当の CLI 関数は argparse の Namespace を要求するため、ここでは
スキーマに直接書くほうが素直）。
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

from . import db, graph, project as project_mod, task as task_mod, util


def _d(offset: int) -> str:
    """今日から `offset` 日後（負なら前）の日付を `YYYY-MM-DD` で返す。"""
    base = date.fromisoformat(util.today())
    return (base + timedelta(days=offset)).isoformat()


def _has_data(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT COUNT(*) AS n FROM task").fetchone()
    return int(row["n"]) > 0


def seed(home: Path) -> dict[str, int]:
    """DB が空（task 0件）のときだけ合成データを入れる。冪等（2回目以降は何もしない）。

    戻り値は入れた件数の内訳（`{"project": 3, "task": 12, ...}`）。
    既にデータがあって何もしなかった場合は空の dict を返す
    （呼び出し側はこれを見て「空の home でだけ使えます」と案内する）。
    """
    home = Path(home)
    conn = db.connect(home)
    try:
        if _has_data(conn):
            return {}
        counts = _seed(conn)
        conn.commit()
        return counts
    finally:
        conn.close()


def _seed(conn: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}

    # --- project（3） -----------------------------------------------------------
    project_mod.add(
        conn, "MOVE", "引っ越しの準備", kind="生活", priority=2, preset="careful",
        body="新居への引っ越し一式。旧居の退去手続きも含む。",
    )
    project_mod.add(
        conn, "TRIP", "家族旅行の計画", kind="生活", priority=3, preset="standard",
        body="秋の家族旅行。行き先はまだ最終決定していない。",
    )
    project_mod.add(
        conn, "BUTLER", "執事の改良", kind="執事", priority=4, preset="fast",
        body="manor 自身の改良・整備タスクを置く場所（v1 の「X系」に相当）。",
    )
    counts["project"] = 3

    # --- milestone（2。どちらも未来日付。C8 を踏まないため） --------------------------
    move_project_id = str(project_mod.resolve(conn, "MOVE")["id"])
    trip_project_id = str(project_mod.resolve(conn, "TRIP")["id"])
    graph.milestone_add(conn, "引っ越し当日", date=_d(30), project_id=move_project_id)
    graph.milestone_add(conn, "旅行出発日", date=_d(45), project_id=trip_project_id, approximate=True)
    counts["milestone"] = 2

    # --- task（12。todo/doing/waiting/hold/resident/done を含む） -------------------
    t1 = task_mod.add(
        conn, "梱包用の段ボールを手配する", project="MOVE",
        goal="荷造りを迷わず始められる状態にする", now="サイズと必要枚数を数えている",
        next_="ホームセンターへ注文する", due=_d(20),
        body="台所と本棚から手を付ける想定。",
    )
    t2 = task_mod.add(
        conn, "引っ越し業者の見積もりを取る", project="MOVE",
        goal="3社から見積もりを揃えて比較できるようにする", now="2社に依頼済み、1社回答待ち",
        next_="残り1社に連絡する", due=_d(15),
    )
    task_mod.status(conn, t2, "doing")

    t3 = task_mod.add(
        conn, "新居のインターネット回線を申し込む", project="MOVE",
        goal="入居日までに開通させる", due=_d(25),
    )
    task_mod.link_dependency(conn, t3, t2, note="見積もりが確定してから")  # -> t3 は自動で waiting

    t4 = task_mod.add(
        conn, "旧居の退去立会いの日程調整", project="MOVE",
        goal="管理会社と退去日を確定する", due=_d(18),
    )
    task_mod.status(conn, t4, "hold", note="管理会社からの折返し待ち")

    t5 = task_mod.add(
        conn, "住所変更の手続き一覧を作る", project="MOVE",
        goal="役所・銀行・各種サービスの変更漏れを無くす",
        now="思い出したものから随時足している",
    )
    task_mod.status(conn, t5, "resident", note="随時更新するので終わらせない")

    t6 = task_mod.add(conn, "郵便物の転送届を出す", project="MOVE")
    task_mod.status(conn, t6, "doing")
    task_mod.status(conn, t6, "done", note="郵便局アプリから申請済み")

    t7 = task_mod.add(
        conn, "行き先の候補を3つ挙げる", project="TRIP",
        goal="家族で選べる候補を揃える",
    )
    task_mod.status(conn, t7, "doing")
    task_mod.status(conn, t7, "done", note="山・海・温泉の3候補を出した")

    t8 = task_mod.add(conn, "宿の予約をする", project="TRIP", due=_d(40))

    t9 = task_mod.add(
        conn, "旅先の最終候補をどちらにするか決める", project="TRIP", level="HG",
        goal="旅先を1つに決める", now="家族の希望を聞いている段階",
        next_="決定して宿の予約に進む", due=_d(10),
        body="候補は山間の温泉地（提案A）と海沿いの温泉地（提案B）。",
        recommendation="海が近い方（提案B）を推します。子どもが海を見たがっているため。",
        risk="low",
    )

    t10 = task_mod.add(
        conn, "旅行のしおりを作る", project="TRIP", due=_d(35),
        goal="日程・持ち物・予算を1枚にまとめる",
    )
    task_mod.status(conn, t10, "doing", owner="master")  # 主人の作業のデモ

    t11 = task_mod.add(
        conn, "夜勤の本登録を検討する", project="BUTLER",
        goal="`manor night install --yes` を主人の合図で叩けるようにする",
    )

    t12 = task_mod.add(conn, "夜勤を有効化するタスク（重複起票）", project="BUTLER")
    task_mod.dup(conn, t12, t11)  # -> t12 は withdrawn、duplicates の辺

    counts["task"] = 12
    _ = (t1, t3, t4, t5, t6, t7, t8, t9)  # 変数として残すのは可読性のため（未使用警告を避ける）

    # --- chef（在庫6・買い物3・食事4） -----------------------------------------------
    now = util.now()
    for item, qty, unit, expires, place in (
        ("卵", "6", "個", _d(10), "冷蔵"),
        ("牛乳", "1", "本", _d(4), "冷蔵"),
        ("玉ねぎ", "3", "個", None, "常温"),
        ("冷凍餃子", "1", "袋", _d(60), "冷凍"),
        ("醤油", "1", "本", None, "常温"),
        ("米", "2", "kg", None, "常温"),
    ):
        conn.execute(
            "INSERT INTO chef_pantry (item, qty, unit, expires, place, note, added_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, '', ?, ?)",
            (item, qty, unit, expires, place, now, now),
        )
    counts["chef_pantry"] = 6

    for item, reason, aisle in (
        ("卵", "そろそろ切れる", "乳卵"),
        ("トイレットペーパー", "家政婦の残量報告から", "その他"),
        ("きゃべつ", "今週の献立用", "野菜"),
    ):
        conn.execute(
            "INSERT INTO chef_shopping (item, reason, aisle, added_at, bought_at)"
            " VALUES (?, ?, ?, ?, NULL)",
            (item, reason, aisle, now),
        )
    counts["chef_shopping"] = 3

    for d_off, slot, dish, ingredients, planned in (
        (-2, "dinner", "カレーライス", "玉ねぎ、にんじん、豚肉", 0),
        (-1, "dinner", "肉じゃが", "じゃがいも、玉ねぎ、牛肉", 0),
        (0, "dinner", "餃子", "冷凍餃子、キャベツ", 1),
        (1, "lunch", "親子丼", "卵、鶏肉、玉ねぎ", 1),
    ):
        conn.execute(
            "INSERT INTO chef_meal (date, slot, dish, ingredients, note, planned, created_at)"
            " VALUES (?, ?, ?, ?, '', ?, ?)",
            (_d(d_off), slot, dish, ingredients, planned, now),
        )
    counts["chef_meal"] = 4

    # --- housekeeper（当番3・消耗品3・ゴミ2） -----------------------------------------
    for name, area, cadence, last_done in (
        ("トイレ掃除", "浴室", 3, _d(-2)),
        ("床の掃除機がけ", "全体", 7, _d(-9)),  # 期限切れのデモ
        ("窓拭き", "全体", 30, None),  # 一度も記録なしのデモ
    ):
        conn.execute(
            "INSERT INTO housekeeper_chore (name, area, cadence_days, last_done, note, created_at)"
            " VALUES (?, ?, ?, ?, '', ?)",
            (name, area, cadence, last_done, now),
        )
    counts["housekeeper_chore"] = 3

    for item, qty, unit, threshold, place in (
        ("洗剤", 1, "本", 2, "洗面所"),  # qty<=threshold で「少ない」のデモ
        ("トイレットペーパー", 8, "ロール", 4, "トイレ"),
        ("ゴミ袋(45L)", 10, "枚", 5, "台所"),
    ):
        conn.execute(
            "INSERT INTO housekeeper_supply (item, qty, unit, threshold, place, note, updated_at)"
            " VALUES (?, ?, ?, ?, ?, '', ?)",
            (item, qty, unit, threshold, place, now),
        )
    counts["housekeeper_supply"] = 3

    for kind, rule in (
        ("可燃", "weekly:mon,thu"),
        ("資源", "weekly:wed"),
    ):
        conn.execute(
            "INSERT INTO housekeeper_waste (kind, rule, note) VALUES (?, ?, '')",
            (kind, rule),
        )
    counts["housekeeper_waste"] = 2

    # --- steward（支出6・定期2・予算2） ----------------------------------------------
    for d_off, amount, kind, category, memo in (
        (-5, 3200, "expense", "食費", "スーパー"),
        (-4, 1500, "expense", "日用品", "ドラッグストア"),
        (-3, 8000, "expense", "外食", "家族で外食"),
        (-2, 45000, "expense", "住居", "家賃"),
        (-1, 2400, "expense", "食費", "八百屋"),
        (0, 300000, "income", "給与", "給与振込"),
    ):
        conn.execute(
            "INSERT INTO steward_expense (date, amount, kind, category, memo, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (_d(d_off), amount, kind, category, memo, now),
        )
    counts["steward_expense"] = 6

    for rname, amount, cycle, due_off, category, kind in (
        ("動画配信サービス", 1490, "monthly", 12, "娯楽", "subscription"),
        ("電気代", 9000, "monthly", 6, "光熱費", "bill"),
    ):
        conn.execute(
            "INSERT INTO steward_recurring (name, amount, cycle, next_due, category, kind, active, note)"
            " VALUES (?, ?, ?, ?, ?, ?, 1, '')",
            (rname, amount, cycle, _d(due_off), category, kind),
        )
    counts["steward_recurring"] = 2

    for category, limit_ in (("食費", 60000), ("娯楽", 5000)):
        conn.execute(
            "INSERT INTO steward_budget (category, monthly_limit) VALUES (?, ?)",
            (category, limit_),
        )
    counts["steward_budget"] = 2

    # --- secretary（控え2・予定2） --------------------------------------------------
    for on_off, at_time, text in (
        (3, "08:00", "燃えるゴミを出す"),
        (1, None, "旅行の宿の締切を確認する"),
    ):
        conn.execute(
            "INSERT INTO secretary_reminder (on_date, at_time, text, source, done_at, created_at)"
            " VALUES (?, ?, ?, 'butler', NULL, ?)",
            (_d(on_off), at_time, text, now),
        )
    counts["secretary_reminder"] = 2

    conn.execute(
        "INSERT INTO secretary_event (start, \"end\", title, place, note, source, external_id, created_at)"
        " VALUES (?, ?, ?, ?, '', 'manual', NULL, ?)",
        (f"{_d(7)}T10:00", f"{_d(7)}T11:00", "不動産屋との打ち合わせ", "新居予定地近く", now),
    )
    conn.execute(
        "INSERT INTO secretary_event (start, \"end\", title, place, note, source, external_id, created_at)"
        " VALUES (?, NULL, ?, '', '家族旅行', 'manual', NULL, ?)",
        (_d(45), "旅行出発", now),
    )
    counts["secretary_event"] = 2

    return counts
