"""主人のプロフィール（ADR-007 D1・D2・D5）。

真実は DB の `profile` 表（`key TEXT PRIMARY KEY / value / updated_at`）。
`home/USER.md` は主人（または執事）が書く自由記述のままで、ここは触らない
（ADR-007 D1）。初回セットアップウィザードの登録は、ここの `apply_setup` が
既存の機構（`project.add` / `task.add`）を1つのトランザクションで通す（D2）。

CLI（`manor profile ...` `manor setup ...`）と Web（`GET/POST /api/v1/setup`
`PUT /api/v1/setup/profile`）の両方がここの関数を呼ぶ（ADR-005 §2 の流儀と同じ）。
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

from . import db as db_mod
from . import i18n
from . import project as project_mod
from . import task as task_mod
from . import util
from .errors import ManorError
from .staff.steward.importer import PRESET_MAPS

#: profile の鍵の語彙（ADR-007 D1・D9 の表が正）。
KEYS: frozenset[str] = frozenset(
    {
        "master.callname",
        "butler.callname",
        "purposes",
        "purposes.note",
        "setup.completed_at",
        "money.app",
        "money.currency",
    }
)

#: 用途の語彙（id → 表示名。「使いたい機能」として聞く。ADR-007 §6 D7。旧語彙
#: （research/work/household/health/hobby/other）は捨てた——本番に該当データ無し）。
PURPOSES: dict[str, str] = {
    "tasks": "タスク・プロジェクトの管理",
    "kitchen": "料理・買い物",
    "money": "家計",
    "house": "家事・消耗品",
    "secretary": "予定・調べもの・書きもの",
}

#: 家計簿アプリの語彙外エラーで使う（`none` は「使っていない」）。
_MONEY_APPS: frozenset[str] = frozenset({"none", *PRESET_MAPS})

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")

#: プロジェクトのプリセット（id → 表示名。`project.VALID_PRESET` と同じ語彙。
#: `src/manor/board/static/app.js` の `PROJECT_PRESET_LABEL` と同じ絵文字を使う）。
PRESETS: dict[str, str] = {
    "careful": "慎重",
    "standard": "標準",
    "fast": "高速",
}


def get_all(conn: sqlite3.Connection) -> dict[str, str]:
    """`profile` 表の現在値。**未設定の鍵は含まない**（既定値の穴埋めは呼び出し側の役目——
    `summary_line` / `render.profile_markdown` がそれぞれの文脈で行う）。
    """
    rows = conn.execute("SELECT key, value FROM profile").fetchall()
    return {str(r["key"]): str(r["value"]) for r in rows}


def set_many(conn: sqlite3.Connection, values: dict[str, object]) -> None:
    """profile の鍵をまとめて書く（upsert）。語彙外の鍵は拒否する（`ManorError(code=2)`）。

    `purposes` は Python の list（既知の id の一覧）を渡す——ここで JSON へ直列化して
    保存する。語彙外の id が混ざっていれば拒否する。
    """
    now = util.now()
    for key, raw in values.items():
        if key not in KEYS:
            known = " / ".join(sorted(KEYS))
            raise ManorError(
                f"{key}: 語彙外の profile key です（使えるのは {known}）",
                code=2,
                key="error.profile.key_unknown",
                params={"key": key, "known": known},
            )

        if key == "purposes":
            if not isinstance(raw, list):
                raise ManorError(
                    "purposes: 配列（用途 id の一覧）で指定してください",
                    key="error.profile.purposes_not_list",
                )
            unknown = [str(i) for i in raw if str(i) not in PURPOSES]
            if unknown:
                known = " / ".join(PURPOSES)
                unknown_text = ", ".join(unknown)
                raise ManorError(
                    f"purposes: 語彙外です: {unknown_text}（使えるのは {known}）",
                    code=2,
                    key="error.profile.purposes_unknown",
                    params={"unknown": unknown_text, "known": known},
                )
            value = json.dumps([str(i) for i in raw], ensure_ascii=False)
        else:
            value = "" if raw is None else str(raw)

        conn.execute(
            "INSERT INTO profile (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value, now),
        )


def is_setup_done(conn: sqlite3.Connection) -> bool:
    return bool(get_all(conn).get("setup.completed_at", "").strip())


def purposes_of(conn_or_values: sqlite3.Connection | dict[str, str]) -> list[str]:
    """`purposes` の値（JSON 文字列）を id の list に戻す。壊れていれば空 list（表示を壊さない）。"""
    values = get_all(conn_or_values) if isinstance(conn_or_values, sqlite3.Connection) else conn_or_values
    raw = values.get("purposes", "") or "[]"
    try:
        ids = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(ids, list):
        return []
    return [str(i) for i in ids if str(i) in PURPOSES]


def summary_line(conn: sqlite3.Connection) -> str:
    """起動時に注入する1行（ADR-007 D3）。`master.callname` が空なら空文字
    （未設定＝まだ何も足さない）。
    """
    values = get_all(conn)
    callname = values.get("master.callname", "").strip()
    if not callname:
        return ""
    butler_name = values.get("butler.callname", "").strip() or "執事"
    parts = [f"主人の呼び名: {callname}", f"執事: {butler_name}"]
    labels = [PURPOSES[i] for i in purposes_of(values)]
    if labels:
        parts.append("用途: " + ", ".join(labels))
    return " ／ ".join(parts)


def status(conn: sqlite3.Connection) -> dict[str, object]:
    values = get_all(conn)
    completed_at = values.get("setup.completed_at", "").strip()
    return {"done": bool(completed_at), "completed_at": completed_at or None, "profile": values}


def _apply_money(conn: sqlite3.Connection, money: dict[str, object] | None) -> None:
    """ADR-007 §6 D9: money 答え（`app`／`currency`）を `profile` の
    `money.app`／`money.currency` へ。`money` が無ければ（段を「あとで」で飛ばした）
    何もしない。`app` は `none`（使っていない）か `PRESET_MAPS`（`steward/importer.py`）の
    id でなければ語彙外（`ManorError(code=2)`）。`currency` は英大文字3文字。既定 `JPY`。
    """
    if money is None:
        return
    values: dict[str, object] = {}

    app = money.get("app")
    if app is not None and str(app).strip():
        app = str(app).strip()
        if app not in _MONEY_APPS:
            known = " / ".join(sorted(_MONEY_APPS))
            raise ManorError(
                f"money.app: 語彙外です: {app}（使えるのは {known}）",
                code=2,
                key="error.profile.money_app_unknown",
                params={"app": app, "known": known},
            )
        values["money.app"] = app

    currency_raw = money.get("currency")
    currency = str(currency_raw).strip().upper() if currency_raw else "JPY"
    if not _CURRENCY_RE.match(currency):
        raise ManorError(
            "money.currency: 英字3文字で指定してください（例: JPY）",
            code=2,
            key="error.profile.money_currency_invalid",
        )
    values["money.currency"] = currency

    set_many(conn, values)


def _apply_kitchen(conn: sqlite3.Connection, kitchen: dict[str, object] | None) -> None:
    """ADR-007 §6 D9: kitchen 答え（`household_size`／`allergies`／`dislikes`）を
    `chef_taste` へ。`kitchen` が無ければ（段を「あとで」で飛ばした）何もしない。空文字は
    無視する。`chef_taste` は部下（chef）の表——スキーマが当たっていなければ
    `db.ensure_staff_schema` で当ててから書く（DDL を手書きしない。D9「部下の表だが、
    書くのは執事の初期化」）。既に値がある鍵は上書きしない（D8）。
    """
    if not kitchen:
        return

    candidates: dict[str, str] = {}
    household_size = kitchen.get("household_size")
    if household_size is not None and str(household_size).strip():
        candidates["household_size"] = str(household_size).strip()
    for key in ("allergies", "dislikes"):
        raw = str(kitchen.get(key) or "").strip()
        if raw:
            candidates[key] = raw

    if not candidates:
        return

    if not db_mod.ensure_staff_schema(conn, "chef"):
        return  # chef 部下パッケージ自体が無い環境では、この答えの書き込みだけ諦める

    now = util.now()
    for key, value in candidates.items():
        existing = conn.execute("SELECT value FROM chef_taste WHERE key = ?", (key,)).fetchone()
        if existing is not None and str(existing["value"]).strip():
            continue  # 既に値がある（D8「上書きしない」）
        conn.execute(
            "INSERT INTO chef_taste (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value, now),
        )


def apply_setup(conn: sqlite3.Connection, answers: dict[str, object]) -> dict[str, object]:
    """初回セットアップウィザードの登録（ADR-007 D2・§6 D9）。1トランザクション:

    1. profile の鍵を書く（`set_many`）。`money` 答えがあれば `money.app`／`money.currency`
       も同じ段で書く
    2. `kitchen` 答えがあれば `chef_taste` へ（部下の表だが、書くのは執事の初期化）
    3. `projects` を `project.add` で作る
    4. `tasks` を `task.add` で作る（`cls` が level を決める。HG 固定クラスは
       `recommendation` が無いと `task.add` 自身が拒否する——ウィザード側で特別扱いしない）
    5. `setup.completed_at` を書く
    6. `shortcut` 答えが真（既定）なら、DB のトランザクションが確定した最後に
       `manor shortcut create` 相当（`shortcut.create()`）を試す（ADR-011 D8: 「初回
       セットアップの最後に『デスクトップにショートカットを作りますか』を置く。既定は
       作る」）。**これは DB とは別のファイルシステムの副作用なので、失敗してもセットアップ
       自体は失敗させない**——`result["warnings"]` に1行積むだけ

    どれか1つでも失敗すれば `ManorError` を投げる（項目名つきの理由）。**ここではコミットしない**
    ——呼び出し側（CLI の `main()` / web の handler）が成功時に commit、失敗時に rollback する。
    ショートカット作成はこの DB 検証がすべて通った後（`setup.completed_at` を書いた後）に
    行う——途中の項目（`projects`/`tasks` 等）が語彙外で `ManorError` になったときに、
    ロールバックできないデスクトップへの書き込みだけが残ることを避けるため。

    `callname` が空／未指定なら既定 `ご主人様`（§6 D8。もう必須ではない）。
    """
    callname = str(answers.get("callname") or "").strip() or "ご主人様"

    butler_name = str(answers.get("butler_name") or "").strip() or "執事"

    purposes = answers.get("purposes")
    if purposes is None:
        purposes = []
    if not isinstance(purposes, list):
        raise ManorError(
            "purposes: 配列（用途 id の一覧）で指定してください",
            key="error.profile.purposes_not_list",
        )

    note = str(answers.get("note") or "")

    set_many(
        conn,
        {
            "master.callname": callname,
            "butler.callname": butler_name,
            "purposes": purposes,
            "purposes.note": note,
        },
    )

    money = answers.get("money")
    _apply_money(conn, money if isinstance(money, dict) else None)

    kitchen = answers.get("kitchen")
    _apply_kitchen(conn, kitchen if isinstance(kitchen, dict) else None)

    created_projects: list[str] = []
    for i, p in enumerate(answers.get("projects") or []):
        code = str((p or {}).get("code") or "").strip()
        name = str((p or {}).get("name") or "").strip()
        if not code:
            raise ManorError(
                f"projects[{i}].code: 必須です", key="error.profile.project_code_required", params={"i": i}
            )
        if not name:
            raise ManorError(
                f"projects[{i}].name: 必須です", key="error.profile.project_name_required", params={"i": i}
            )
        preset = str((p or {}).get("preset") or "standard")
        due = (p or {}).get("due") or None
        project_id = project_mod.add(conn, code, name, preset=preset, due=due)
        created_projects.append(project_id)

    created_tasks: list[str] = []
    for i, t in enumerate(answers.get("tasks") or []):
        title = str((t or {}).get("title") or "").strip()
        if not title:
            raise ManorError(
                f"tasks[{i}].title: 必須です", key="error.profile.task_title_required", params={"i": i}
            )
        # ADR-010 D1: セットアップでは「行動クラス」を聞かない。`cls` は後方互換
        # （執事や旧クライアントがまだ送ってくる）のために残すが、**省略時は既定 `general`
        # （L2）で起票する**——主人向けの画面からは既に消えている前提（フロント側の変更）。
        cls = str((t or {}).get("cls") or "").strip() or "general"
        # ADR-010 D2: `kind`（タスクの種類）は任意。語彙外なら `task_mod.add` 自身が
        # `ManorError(code=2)` で拒む（ここでは特別扱いしない）。
        kind = str((t or {}).get("kind") or "").strip()
        project_code = (t or {}).get("project_code") or None
        due = (t or {}).get("due") or None
        task_id = task_mod.add(conn, title, project=project_code, cls=cls, due=due, kind=kind)
        created_tasks.append(task_id)

    set_many(conn, {"setup.completed_at": util.now()})

    warnings: list[str] = []
    want_shortcut = answers.get("shortcut")
    if want_shortcut is None:
        want_shortcut = True  # ADR-011 D8: 既定は作る
    if want_shortcut:
        from . import shortcut as shortcut_mod

        try:
            shortcut_result = shortcut_mod.create()
        except Exception as exc:  # noqa: BLE001 - セットアップ自体は失敗させない（D2 の対象外）
            shortcut_result = {"ok": False, "reason": str(exc)}
        if not shortcut_result.get("ok", False):
            reason = str(shortcut_result.get("reason") or "理由不明")
            warnings.append(f"デスクトップのショートカットは作れませんでした: {reason}")

    return {
        "profile": get_all(conn),
        "created": {"projects": created_projects, "tasks": created_tasks},
        "warnings": warnings,
    }


# --- CLI（`manor profile ...` `manor setup ...`。ADR-007 D5） ------------------------


def cmd_profile_show(conn: sqlite3.Connection, home, args: argparse.Namespace) -> object:
    data = get_all(conn)
    if args.json:
        return data
    if not data:
        return i18n.t("profile.show.unset")
    return "\n".join(f"{k}: {v}" for k, v in data.items())


def cmd_profile_set(conn: sqlite3.Connection, home, args: argparse.Namespace) -> object:
    value: object = args.value
    if args.key == "purposes":
        try:
            value = json.loads(args.value)
        except json.JSONDecodeError as exc:
            raise ManorError(
                'purposes: JSON配列で指定してください（例: ["tasks","kitchen"]）',
                code=2,
                key="error.profile.purposes_json_required",
            ) from exc
    set_many(conn, {args.key: value})
    if args.json:
        return {args.key: value}
    return i18n.t("common.updated", id=args.key)


def cmd_setup(conn: sqlite3.Connection, home, args: argparse.Namespace) -> object:
    if args.status:
        result = status(conn)
        if args.json:
            return result
        if result["done"]:
            return i18n.t("setup.status.done", completed_at=result["completed_at"])
        return i18n.t("setup.status.not_done")

    # `utf-8-sig`: Windows の PowerShell が既定で書く JSON には BOM が付く。`utf-8` で
    # 読むと「Unexpected UTF-8 BOM」で落ちる——主人が用意した答えの中身は正しいのに
    # 使えない（2026-09-05 実測）。BOM が無いファイルもそのまま読める。
    answers = json.loads(Path(args.answers).read_text(encoding="utf-8-sig"))
    result = apply_setup(conn, answers)
    if args.json:
        return result
    created = result["created"]  # type: ignore[index]
    lines = [i18n.t("setup.apply.done")]
    if created["projects"]:  # type: ignore[index]
        lines.append(i18n.t("setup.apply.projects_line", codes=", ".join(created["projects"])))  # type: ignore[index]
    if created["tasks"]:  # type: ignore[index]
        lines.append(i18n.t("setup.apply.tasks_line", ids=", ".join(created["tasks"])))  # type: ignore[index]
    lines.extend(i18n.t("common.warning_line", reason=w) for w in result.get("warnings", []))  # type: ignore[union-attr]
    return "\n".join(lines)


def register(subparsers: "argparse._SubParsersAction") -> None:
    """`manor profile show|set` と `manor setup --answers|--status` を足す。"""
    profile_p = subparsers.add_parser("profile", help=i18n.t("cli.profile.help"))
    profile_sub = profile_p.add_subparsers(dest="verb")

    p = profile_sub.add_parser("show")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_profile_show, is_write=False)

    p = profile_sub.add_parser("set")
    p.add_argument("key", help="KEYS: " + " / ".join(sorted(KEYS)))
    p.add_argument("value")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(func=cmd_profile_set, is_write=True)

    setup_p = subparsers.add_parser("setup", help=i18n.t("cli.setup.help"))
    group = setup_p.add_mutually_exclusive_group(required=True)
    group.add_argument("--answers", help=i18n.t("cli.setup.answers.help"))
    group.add_argument("--status", action="store_true", help=i18n.t("cli.setup.status.help"))
    setup_p.add_argument("--json", action="store_true")
    setup_p.add_argument("--no-render", action="store_true")
    setup_p.set_defaults(func=cmd_setup, is_write=True)
