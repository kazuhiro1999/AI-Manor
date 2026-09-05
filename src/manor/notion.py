"""`manor notion` — Notion 連携（ADR-009 §7「Notion 拡張（5c）」D18・D19・D20）。

v1 `AI執事/apps/notion-diary`（`post-diary.ps1` / `SETUP.md` / `diary-prompt.txt`）を
読み取り専用で参照し、そこに書かれていた**規則**を移す（PowerShell そのものは移さない）。
移すのは**日記**（その日に何があったかを1ページ書く）だけ——v1 にあった夜間の自動起動・
LOG.md/GROWTH.md/CHANGELOG.md を丸ごと埋め込む生成方式は移さない（manor は DB が真実
なので、資料は DB から機械的に組む。D18）。**HTTP は stdlib の `urllib` のみ**
（`slack.py` と同じく新しい依存を増やさない）。

## 中身は DB から機械的に組む（D18。`slack.py` の `brief` と同じ思想）

`--generate` 無しの既定は、その日の DB から機械的に本文を組む: 完了した task
（`task_event` を直接見る。`WHERE to_status='done' AND date(at)=<date>`）・下した裁定
（`decision.list_decisions()` を再利用し、`decided_at` の日付でふるいに掛ける——
新しいクエリ層は作らない）・過ぎたマイルストーン（`milestone` を直接見る。`render.py` の
`active_data` と同じ書き方だが、`active_data` 自身は「いま」から先7日を見る前方参照の
クエリで、日記が要る「任意の過去の1日」には使えないため、ここは並行する素の SQL を
`render._rows` と同じ書き方で持つ）・夜勤の結果（`night.runner.report(home, date)`。
`home/night/reports/<date>.md` があれば読む）。`claude` が壊れていても日記は残る。

## 禁止語スキャンは Slack と同じ関数を使い回す（D18）

`slack.scan_for_leak_terms()` は着手時点で既に public だったので（`_` 始まりではない）、
`slack.py` を変更する必要は無かった。二重に実装していない。

## 二重投函の防止は「両方やる」（D19）

1. **ローカル**: `notion_page` 表（`date` UNIQUE）。投函の直前に確かめる
2. **Notion 側**: 投函の直前に `日付` プロパティで問い合わせる（`data_sources/<id>/query`）

どちらか一方だけでは足りない——ローカルだけでは「手で書いた」「別の PC から投函した」を
見落とし、Notion への問い合わせだけでは同じ秒に2回叩いたときに競合しうる（ADR 本文の
理由をそのまま踏襲）。**Notion 側への問い合わせ自体が失敗した**（ネットワーク断など）
ときは、確認できないまま投函するとまさに D19 が防ごうとしている二重投函を起こしかねない
ので、**投函しない**（fail-closed。禁止語スキャンで語彙リストが読めないときと同じ判断）。
v1 の `post-diary.ps1` は問い合わせ失敗時に「ローカル記録で代替」して投函を続けたが、
D19 の書き直しは「両方やる」ことそのものが安全の根拠なので、ここは v1 より一段厳しくした
（担当5cの判断。曖昧だった点として報告する）。

Notion 側で既にページが見つかったときは、その `page_id`/`url` を `notion_page` へも
書いておく（次回以降はローカルの速い経路で気づける。D19 の主旨——ローカルの記録が
Notion 側の実際の状態からずれたまま放置されるのを防ぐ）。

## API バージョンはコード内に固定（D20）

`NOTION_API_VERSION` は v1 が `SETUP.md` §Step 5 で実際に疎通確認まで済ませた版
（`2026-03-11`）をそのまま使う。**外部の既定（最新版）に流されない**——上げるなら
このバージョンで新しい疎通確認をしてから。

## `database_id` → `data_source_id`（v1 の SETUP.md Step 5 が確認した実際の経路）

Notion のページ作成・検索は `data_source_id` を親に指定する必要がある（`database_id`
そのものではない）。主人が設定するのは（画面から見つけやすい）`database_id` だけで、
`GET /v1/databases/<database_id>` を1回呼んで `data_sources[0].id` を引く
（投函のたびに引く。キャッシュはしない——`database_id` の設定を変えたときに古い
`data_source_id` を握ったままにならないようにするため。実測でのコストは無視できる
——日記は1日1回）。

## 秘密（D4・ADR-009 §2）

`token` は `secrets.get("notion", "token")` から読む。`slack.py` は着手時点で
`secrets.py` が無かったため遅延 import にしていたが、**このモジュールの着手時点では
既に `secrets.py` が実装済み**なので、素直にモジュール先頭で import している
（`slack.py` の遅延 import は歴史的な事情——両者が同時並行で作られたための一時的な
措置——であって、今後の踏襲すべき型ではない）。

`database_id`（秘密ではない）は `home/config.toml` の `[notion] database_id`
（`[slack] channel` と同じ流儀）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

from . import db, decision as decision_mod, i18n, runlog, secrets as secrets_mod, slack as slack_mod, util
from .errors import ManorError
from .night import runner as night_runner

CONFIG_FILE_NAME = "config.toml"
NOTION_API_BASE = "https://api.notion.com/v1"

#: Notion の API バージョン（D20: コード内に固定。`Notion-Version` ヘッダに常にこれを送る）。
#: v1 `apps/notion-diary/post-diary.ps1` / `SETUP.md` §Step 5 が実際に疎通確認まで
#: 済ませた版をそのまま使う（無料プランでも動くことを実測済み）。**上げるならこの版で
#: 新しい疎通確認をしてから**——外部の既定（最新版）に流されると、プロパティの検証規則
#: が変わって無言で投函できなくなる恐れがある。
NOTION_API_VERSION = "2026-03-11"

#: 送受信の HTTP タイムアウト（秒）。slack.py の API_TIMEOUT と同じ流儀。
API_TIMEOUT = 15.0

#: `--generate` で `claude -p` を呼ぶときの既定（slack.py と同じ値を独立して持つ。
#: このファイルの担当外である slack.py を変更しないため、定数として複製してある）。
GENERATE_MODEL = "sonnet"
GENERATE_MAX_TURNS = 4
GENERATE_TIMEOUT_SECONDS = 180.0

#: 生成ステップに道具を持たせない（slack.py の GENERATE_DISALLOWED_TOOLS と同じ理由）。
GENERATE_DISALLOWED_TOOLS: tuple[str, ...] = (
    "Bash",
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
)

#: 投函するページの既定タグ（D18「プロパティ名は v1 と同じ」）。v1 の `post-diary.ps1`
#: は実装上タグを送っていなかった（コネクションの Update content 権限を切っていたため、
#: 未知の選択肢を増やす経路を怖れて見送ったと読める）。ここでは固定の1語だけを送る
#: ——新しい選択肢の追加は「ページの作成」に伴う通常の書き込みで、SETUP の手順で
#: 有効にする Insert content 権限の範囲内のはず。曖昧だった点として報告する。
DEFAULT_TAGS: tuple[str, ...] = ("日記",)


# --- 設定（home/config.toml の [notion]。D4・D20） -------------------------------------------


def _load_notion_config(home: Path) -> dict[str, object]:
    """`home/config.toml` の `[notion]` 節。無い・壊れているときは空辞書
    （`slack._load_slack_config` と同じ流儀）。"""
    path = Path(home) / CONFIG_FILE_NAME
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except Exception:  # noqa: BLE001 - 壊れた TOML は「未設定」として扱う
        return {}
    section = data.get("notion") if isinstance(data, dict) else None
    return section if isinstance(section, dict) else {}


def database_id(home: Path) -> str:
    """`[notion] database_id`。無ければ空文字。"""
    cfg = _load_notion_config(home)
    v = cfg.get("database_id")
    return v.strip() if isinstance(v, str) else ""


def api_token() -> str | None:
    """`secrets.get("notion", "token")` の薄いラッパー。読み出しの失敗で
    送受信全体を落とさない（`slack.bot_token` と同じ約束）。"""
    try:
        value = secrets_mod.get("notion", "token")
    except Exception:  # noqa: BLE001
        return None
    return value if isinstance(value, str) and value.strip() else None


# --- Notion API（urllib のみ。D18） ---------------------------------------------------------


def _notion_request(
    method: str,
    path: str,
    token: str,
    *,
    body: dict[str, object] | None = None,
    timeout: float = API_TIMEOUT,
) -> dict[str, object]:
    """Notion API を1回呼ぶ。**例外は投げない**（`slack._slack_api` と同じ約束）。

    成功時は `{"ok": True, "data": <レスポンスの JSON>}`。失敗時は
    `{"ok": False, "reason": str}`。Notion のエラー応答（`{"object": "error", ...}`）は
    ここで判定して `ok: False` に揃える（Slack と違い、成功応答には `ok` フィールドが
    無いため、成否の判定そのものをこの層でやる必要がある）。
    """
    url = f"{NOTION_API_BASE}{path}"
    headers = {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_API_VERSION}
    data: bytes | None = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - Notion の公式 API ドメイン固定
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
            parsed = json.loads(raw.decode("utf-8"))
            if isinstance(parsed, dict) and parsed.get("message"):
                return {"ok": False, "reason": str(parsed["message"])}
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "reason": f"http_error_{exc.code}"}
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return {"ok": False, "reason": f"network_error: {exc}"}
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return {"ok": False, "reason": f"bad_response: {exc}"}
    if not isinstance(parsed, dict):
        return {"ok": False, "reason": "unexpected_response"}
    if parsed.get("object") == "error":
        return {"ok": False, "reason": str(parsed.get("message") or parsed.get("code") or "不明なエラー")}
    return {"ok": True, "data": parsed}


def test_connection(home: Path | str | None = None) -> dict[str, object]:
    """`GET /v1/users/me` を叩いて疎通を確かめる（ADR-009 D20 の `check()` の実体）。
    例外は投げない。`home` は今のところ使わない（token はホーム非依存。将来
    database_id の生死も見るならここへ足す）。
    """
    checked_at = util.now()
    token = api_token()
    if not token:
        return {
            "ok": False,
            "reason": "token が未設定です（manor ext set notion --secret token）",
            "checked_at": checked_at,
        }
    resp = _notion_request("GET", "/users/me", token)
    if not resp.get("ok"):
        return {"ok": False, "reason": f"Notion API エラー: {resp.get('reason', '不明')}", "checked_at": checked_at}
    data = resp.get("data")
    name = data.get("name") if isinstance(data, dict) else None
    return {"ok": True, "reason": "", "checked_at": checked_at, "name": name}


def _resolve_data_source_id(database_id_: str, token: str) -> dict[str, object]:
    """`database_id` → `data_source_id`（v1 SETUP.md Step 5 が確認した経路）。"""
    resp = _notion_request("GET", f"/databases/{database_id_}", token)
    if not resp.get("ok"):
        return {"ok": False, "reason": f"データベースを取得できません: {resp.get('reason', '不明')}"}
    data = resp.get("data")
    sources = data.get("data_sources") if isinstance(data, dict) else None
    if not isinstance(sources, list) or not sources:
        return {"ok": False, "reason": "データベースに data_source がありません"}
    first = sources[0]
    ds_id = first.get("id") if isinstance(first, dict) else None
    if not ds_id:
        return {"ok": False, "reason": "data_source_id を取得できません"}
    return {"ok": True, "data_source_id": str(ds_id)}


def _query_existing_page(data_source_id: str, token: str, date: str) -> dict[str, object]:
    """`日付` プロパティで既存ページを探す（D19 の2つめの砦）。"""
    body: dict[str, object] = {"filter": {"property": "日付", "date": {"equals": date}}, "page_size": 1}
    resp = _notion_request("POST", f"/data_sources/{data_source_id}/query", token, body=body)
    if not resp.get("ok"):
        return {"ok": False, "reason": resp.get("reason", "不明")}
    data = resp.get("data") or {}
    results = data.get("results") if isinstance(data, dict) else None
    results = results if isinstance(results, list) else []
    return {"ok": True, "exists": len(results) > 0, "results": results}


def _create_page(
    data_source_id: str, token: str, *, title: str, date: str, tags: list[str], body_markdown: str
) -> dict[str, object]:
    """ページ properties は D18「名前（title）／日付（date）／タグ（multi_select）— v1 と同じ」。
    本文は `markdown` フィールドにそのまま渡す（v1 `post-diary.ps1` が実際に疎通確認まで
    済ませた投函の形。ブロック単位への分解はしない）。
    """
    payload: dict[str, object] = {
        "parent": {"data_source_id": data_source_id},
        "properties": {
            "名前": {"title": [{"text": {"content": title}}]},
            "日付": {"date": {"start": date}},
            "タグ": {"multi_select": [{"name": t} for t in tags]},
        },
        "markdown": body_markdown,
    }
    resp = _notion_request("POST", "/pages", token, body=payload)
    if not resp.get("ok"):
        return {"ok": False, "reason": resp.get("reason", "不明")}
    data = resp.get("data") or {}
    return {"ok": True, "page_id": data.get("id"), "url": data.get("url")}


# --- 本文の組み立て（機械／生成。D18） -------------------------------------------------------


def _clip(text: object, limit: int = 120) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _rows(conn: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> list[dict[str, object]]:
    """`render._rows` と同じ形の小さな道具（新しいクエリ層ではなく、行→辞書の整形だけ）。"""
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _completed_tasks(conn: sqlite3.Connection, date: str) -> list[dict[str, object]]:
    """その日に `done` になった task。`task_event`（`to_status='done'` かつ
    `date(at) = <date>`）を直接見る——D18 の指示どおり、これ以上のクエリ層は作らない。
    """
    return _rows(
        conn,
        "SELECT DISTINCT t.id, n.title AS title FROM task_event ev"
        " JOIN task t ON t.id = ev.task_id JOIN node n ON n.id = t.id"
        " WHERE ev.to_status = 'done' AND date(ev.at) = ?"
        " ORDER BY CAST(substr(t.id, 2) AS INTEGER)",
        (date,),
    )


def _ruled_decisions(conn: sqlite3.Connection, date: str) -> list[dict[str, object]]:
    """その日に裁定された decision。`decision.list_decisions()` を再利用し、
    `decided_at` の日付でふるいに掛ける（D18: 新しいクエリ層は作らない）。
    """
    all_decisions = decision_mod.list_decisions(conn)
    return [d for d in all_decisions if d.get("decided_at") and str(d["decided_at"])[:10] == date]


def _passed_milestones(conn: sqlite3.Connection, date: str) -> list[dict[str, object]]:
    """その日が期日の milestone（＝その日に過ぎた）。`render.active_data` は「いまから
    先7日」を見る前方参照のクエリで任意の過去日には使えないため、同じ書き方
    （`milestone` を直接見る）を並行して持つ。
    """
    return _rows(
        conn,
        "SELECT m.id, n.title AS title FROM milestone m JOIN node n ON n.id = m.id"
        " WHERE date(m.date) = ? ORDER BY CAST(substr(m.id, 2) AS INTEGER)",
        (date,),
    )


def diary_data(conn: sqlite3.Connection, home: Path, date: str) -> dict[str, object]:
    """その日の資料一式（完了タスク・裁定・過ぎたマイルストーン・夜勤の結果）。"""
    return {
        "date": date,
        "tasks": _completed_tasks(conn, date),
        "decisions": _ruled_decisions(conn, date),
        "milestones": _passed_milestones(conn, date),
        "night": night_runner.report(Path(home), date),
    }


_VERDICT_LABEL: dict[str, str] = {"approved": "承認", "rejected": "却下", "modified": "修正"}


def format_mechanical_diary(data: dict[str, object]) -> str:
    """機械組みの日記本文（`--generate` 無しの既定。D18: `claude` が壊れていても
    日記は残る）。新しいクエリ層は作らない——ここは整形だけ。
    """
    date = str(data["date"])
    tasks = list(data["tasks"])  # type: ignore[arg-type]
    decisions = list(data["decisions"])  # type: ignore[arg-type]
    milestones = list(data["milestones"])  # type: ignore[arg-type]
    night = dict(data["night"])  # type: ignore[arg-type]

    lines: list[str] = [f"{date} の記録"]
    if tasks:
        lines.append("完了したタスク:")
        for t in tasks:
            lines.append(f"- {t['id']} {_clip(t['title'], 70)}")
    if decisions:
        lines.append("裁定:")
        for d in decisions:
            label = _VERDICT_LABEL.get(str(d.get("status")), str(d.get("status")))
            lines.append(f"- {d['id']} {_clip(d['title'], 70)}（{label}）")
    if milestones:
        lines.append("マイルストーン:")
        for m in milestones:
            lines.append(f"- {_clip(m['title'], 70)}")
    if night.get("found"):
        lines.append("夜勤の結果:")
        lines.append(_clip(night.get("text"), 400))
    if not tasks and not decisions and not milestones and not night.get("found"):
        lines.append("この日、記録された動きはありませんでした。")
    return "\n".join(lines)


def _build_generate_prompt(mechanical: str, date: str) -> str:
    return (
        f"以下は {date} の出来事を DB から機械的に組んだ下書きです。この内容だけをもとに、"
        "Notion に残す執事日誌の本文を書いてください。"
        "事実を足さない・数字を変えない・道具は使わない。8〜12行程度の日本語、絵文字は使わない。"
        "本文だけを出力してください（前置き・後書きは不要）。\n\n---\n" + mechanical
    )


def _claude_generate_argv(*, model: str, claude_bin: str | None) -> list[str]:
    # slack.py の `_claude_generate_argv` と同じ解決順。
    exe = claude_bin or shutil.which("claude") or "claude"
    return [
        exe,
        "-p",
        "--output-format",
        "json",
        "--permission-mode",
        "dontAsk",
        "--strict-mcp-config",
        "--max-turns",
        str(GENERATE_MAX_TURNS),
        "--model",
        model,
        "--disallowed-tools",
        *GENERATE_DISALLOWED_TOOLS,
    ]


def _run_claude_generate(
    prompt: str,
    *,
    model: str = GENERATE_MODEL,
    claude_bin: str | None = None,
    timeout: float = GENERATE_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """`claude -p` に本文を書かせる。slack.py の `_run_claude_generate` と同じパターン
    （`subprocess.run` で JSON 出力を受け、`is_error` を見る）。**例外は投げない**。
    """
    argv = _claude_generate_argv(model=model, claude_bin=claude_bin)
    try:
        proc = subprocess.run(
            argv, input=prompt, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "reason": f"claude を起動できませんでした: {exc}", "text": None, "parsed": None}
    if proc.returncode != 0:
        return {
            "ok": False,
            "reason": f"claude が異常終了しました (exit={proc.returncode})",
            "text": None,
            "parsed": None,
        }
    try:
        parsed = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return {"ok": False, "reason": "結果 JSON を解釈できませんでした", "text": None, "parsed": None}
    if not isinstance(parsed, dict):
        return {"ok": False, "reason": "結果 JSON の形が不正です", "text": None, "parsed": None}
    if parsed.get("is_error"):
        return {"ok": False, "reason": str(parsed.get("result", "claude がエラーを返しました")), "text": None, "parsed": parsed}
    text = parsed.get("result")
    if not isinstance(text, str) or not text.strip():
        return {"ok": False, "reason": "本文が空でした", "text": None, "parsed": parsed}
    return {"ok": True, "reason": "", "text": text.strip(), "parsed": parsed}


# --- diary（投函。D18・D19） -------------------------------------------------------------------


def diary(
    home: Path,
    *,
    date: str | None = None,
    generate: bool = False,
    dry_run: bool = False,
    claude_bin: str | None = None,
) -> dict[str, object]:
    """`manor notion diary`。

    1. 本文を組む（既定は DB から機械的に。`--generate` なら `claude -p` に書き直させる。
       失敗・`claude` 不在なら機械組みへフォールバック）
    2. 送信直前の禁止語スキャンを通す（`slack.scan_for_leak_terms` を再利用）
    3. **ローカルの二重投函チェック**（`notion_page` の date UNIQUE）。ここで見つかれば
       投函しない（HTTP を1本も呼ばない）
    4. `--dry-run` ならここで終える（**HTTP は一切呼ばない**）
    5. `database_id` → `data_source_id` を引き、**Notion 側の二重投函チェック**
       （`日付` で問い合わせる）。見つかれば投函しない（ローカルにも記録し、次回以降は
       ローカルの経路で気づけるようにする）
    6. 見つからなければページを作成し、`notion_page` へ記録する
    """
    home = Path(home)
    target_date = date or util.today()
    conn = db.connect(home)
    try:
        data = diary_data(conn, home, target_date)
        mechanical = format_mechanical_diary(data)
        title = f"執事日誌 {target_date}"
        body_text = mechanical
        generated = False
        generate_note = ""

        if generate:
            claude_path = claude_bin or shutil.which("claude")
            if claude_path is None:
                generate_note = "claude が見つからないため機械組みへフォールバックしました"
            else:
                run_id = runlog.start(conn, "talk", ref=f"notion-diary-{target_date}", model=GENERATE_MODEL)
                conn.commit()
                result = _run_claude_generate(
                    _build_generate_prompt(mechanical, target_date), model=GENERATE_MODEL, claude_bin=claude_bin
                )
                if result["ok"]:
                    body_text = str(result["text"])
                    generated = True
                    parsed = result.get("parsed")
                    if isinstance(parsed, dict):
                        info = runlog.from_claude_result(parsed)
                        runlog.finish(
                            conn, run_id, usage=info["usage"], cost=info["cost"],
                            turns=info["turns"], exit_reason=info["exit_reason"],
                        )
                    else:
                        runlog.finish(conn, run_id, exit_reason="done", note="結果JSONなし")
                else:
                    runlog.finish(conn, run_id, exit_reason="failed", note=str(result.get("reason", "")))
                    generate_note = f"生成に失敗したため機械組みへフォールバックしました: {result.get('reason', '')}"
                conn.commit()

        # 送信直前の禁止語スキャン（slack.py の関数を再利用。二重に実装しない）。
        scan = slack_mod.scan_for_leak_terms(title + "\n" + body_text)
        if not scan["ok"]:
            # **本文そのものは返さない**（slack.brief と同じ理由）。
            return {
                "posted": False,
                "reason": "禁止語に一致しました",
                "scan_failure": {"reason": scan["reason"], "position": scan["position"]},
                "generated": generated,
                "generate_note": generate_note,
            }

        # ローカルの二重投函チェック（D19「両方やる」の1つめ）。読むだけなので dry-run でも行う。
        existing_local = conn.execute(
            "SELECT page_id, url FROM notion_page WHERE date = ?", (target_date,)
        ).fetchone()

        if dry_run:
            return {
                "posted": False,
                "dry_run": True,
                "date": target_date,
                "title": title,
                "text": body_text,
                "generated": generated,
                "generate_note": generate_note,
                "already_posted_locally": existing_local is not None,
            }

        if existing_local is not None:
            return {
                "posted": False,
                "reason": f"{target_date} は既に投函済みです（ローカル記録）",
                "which": "local",
                "date": target_date,
                "title": title,
                "generated": generated,
                "generate_note": generate_note,
            }

        token = api_token()
        if not token:
            return {
                "posted": False, "reason": "token が未設定です", "date": target_date, "title": title,
                "generated": generated, "generate_note": generate_note,
            }
        db_id = database_id(home)
        if not db_id:
            return {
                "posted": False,
                "reason": "database_id が未設定です（home/config.toml の [notion]）",
                "date": target_date, "title": title, "generated": generated, "generate_note": generate_note,
            }

        ds_result = _resolve_data_source_id(db_id, token)
        if not ds_result.get("ok"):
            return {
                "posted": False, "reason": str(ds_result.get("reason")), "date": target_date, "title": title,
                "generated": generated, "generate_note": generate_note,
            }
        data_source_id = str(ds_result["data_source_id"])

        # Notion 側の二重投函チェック（D19「両方やる」の2つめ）。問い合わせ自体が失敗したら
        # 確認できないまま投函しない（fail-closed。モジュール docstring 参照）。
        query_result = _query_existing_page(data_source_id, token, target_date)
        if not query_result.get("ok"):
            return {
                "posted": False,
                "reason": f"Notion 側の重複確認に失敗しました: {query_result.get('reason')}",
                "which": "remote_check_failed",
                "date": target_date, "title": title, "generated": generated, "generate_note": generate_note,
            }
        if query_result.get("exists"):
            found_list = query_result.get("results") or []
            found = found_list[0] if isinstance(found_list, list) and found_list else {}
            found_id = str(found.get("id")) if isinstance(found, dict) and found.get("id") else ""
            found_url = str(found.get("url")) if isinstance(found, dict) and found.get("url") else ""
            # 見つかったページをローカルにも記録しておく（次回以降はローカルの速い経路で
            # 気づける。D19「ローカルの記録だけでは他の経路を見落とす」の裏返しの手当て）。
            conn.execute(
                "INSERT INTO notion_page (date, page_id, url, posted_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(date) DO NOTHING",
                (target_date, found_id, found_url, util.now()),
            )
            conn.commit()
            return {
                "posted": False,
                "reason": f"{target_date} は Notion 側に既にページがあります",
                "which": "remote",
                "date": target_date, "title": title, "generated": generated, "generate_note": generate_note,
            }

        create_result = _create_page(
            data_source_id, token, title=title, date=target_date, tags=list(DEFAULT_TAGS), body_markdown=body_text
        )
        if not create_result.get("ok"):
            return {
                "posted": False, "reason": str(create_result.get("reason")), "date": target_date, "title": title,
                "generated": generated, "generate_note": generate_note,
            }

        page_id = str(create_result.get("page_id") or "")
        url = str(create_result.get("url") or "")
        conn.execute(
            "INSERT INTO notion_page (date, page_id, url, posted_at) VALUES (?, ?, ?, ?)",
            (target_date, page_id, url, util.now()),
        )
        conn.commit()
        return {
            "posted": True, "date": target_date, "title": title, "page_id": page_id, "url": url,
            "generated": generated, "generate_note": generate_note,
        }
    finally:
        conn.close()


# --- CLI（`manor notion ...`。DB は diary()/test_connection() が自分で開閉するので
# needs_db=False。slack.py と同じ流儀） ------------------------------------------------------


def _print_json(obj: object) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _cmd_diary(args: argparse.Namespace) -> int:
    home = util.manor_home()
    result = diary(home, date=args.date, generate=bool(args.generate), dry_run=bool(args.dry_run))
    if args.json:
        _print_json(result)
    else:
        if result.get("posted"):
            print(i18n.t("notion.diary.posted", url=result.get("url")))
        elif result.get("dry_run"):
            print(i18n.t("notion.diary.dry_run_header"))
            print(f"[{result.get('title')}]")
            print(result.get("text", ""))
            if result.get("already_posted_locally"):
                print(i18n.t("notion.diary.already_posted_locally"))
        else:
            # `reason` は check()（web の拡張ステータス表示）とも共有する診断文字列なので
            # 訳さない（calendar.py と同じ判断）——包む文だけ訳す。
            print(i18n.t("notion.diary.not_posted", reason=result.get("reason", "")))
            sf = result.get("scan_failure")
            if isinstance(sf, dict):
                print(i18n.t("notion.diary.scan_failure", position=sf.get("position")))
        note = result.get("generate_note")
        if note:
            print(i18n.t("import_v1.note_line", note=note))
    return 0 if (result.get("posted") or result.get("dry_run")) else 1


def _cmd_test(args: argparse.Namespace) -> int:
    home = util.manor_home()
    result = test_connection(home)
    if args.json:
        _print_json(result)
    else:
        if result.get("ok"):
            print(i18n.t("notion.test.ok", name=result.get("name") or i18n.t("notion.test.no_name")))
        else:
            print(i18n.t("notion.test.failed", reason=result.get("reason", "")))
    return 0 if result.get("ok") else 1


def register(subparsers: "argparse._SubParsersAction") -> None:
    """`manor notion diary|test` を足す（`cli.py` から配線される公開口。
    `slack.py` の `register()` と同じ形）。
    """
    p = subparsers.add_parser("notion", help=i18n.t("cli.notion.help"))
    sub = p.add_subparsers(dest="verb")

    d = sub.add_parser("diary", help=i18n.t("cli.notion.diary.help"))
    d.add_argument("--date", help=i18n.t("cli.notion.diary.date.help"))
    d.add_argument("--generate", action="store_true", help=i18n.t("cli.notion.diary.generate.help"))
    d.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help=i18n.t("cli.notion.diary.dry_run.help"),
    )
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=_cmd_diary, needs_db=False)

    t = sub.add_parser("test", help=i18n.t("cli.notion.test.help"))
    t.add_argument("--json", action="store_true")
    t.set_defaults(func=_cmd_test, needs_db=False)


def main(argv: list[str] | None = None) -> int:
    """`python -m manor.notion diary|test ...`（`cli.py` へ配線される前の起動口。
    `slack.py` の `main()` と同じ形）。
    """
    try:
        import sys

        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass
    parser = argparse.ArgumentParser(prog="python -m manor.notion", description=i18n.t("cli.notion.help"))
    sub = parser.add_subparsers(dest="verb")

    d = sub.add_parser("diary", help=i18n.t("cli.notion.diary.help"))
    d.add_argument("--date")
    d.add_argument("--generate", action="store_true")
    d.add_argument("--dry-run", action="store_true", dest="dry_run")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=_cmd_diary)

    t = sub.add_parser("test")
    t.add_argument("--json", action="store_true")
    t.set_defaults(func=_cmd_test)

    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 2
    try:
        return int(func(args) or 0)
    except ManorError as exc:
        print(exc.localized_message())
        return exc.code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
