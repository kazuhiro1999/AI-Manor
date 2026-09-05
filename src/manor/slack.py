"""`manor slack` — Slack 連携（ADR-009 §3「Slack 拡張（5b）」D10・D11・D12）。

v1 `AI執事/apps/slack-relay`（`slack-lib.ps1` / `run-brief.ps1` / `read-inbox.ps1`）を
読み取り専用で参照し、そこに書かれていた**規則**を移す（PowerShell そのものは移さない）。
v1 は送信を Incoming Webhook・受信を Bot Token（`conversations.history`）と分けていたが、
ADR-009 は両方を Slack Web API（`chat.postMessage` / `conversations.replies` / `auth.test`）＋
Bot Token 1本に寄せている。**HTTP は stdlib の `urllib` のみ**（新しい依存を増やさない）。

## 送信と受信を分ける理由（D10。v1 README §「なぜ送信を分離するのか」を踏襲）

1. 秘密（`bot_token`）を執事の文脈に載せない——読むのはこのモジュールの送受信部だけ
2. `claude` が壊れていても「今日の要対応は N 件」を送れる（`--generate` 無しの既定は
   DB から機械的に本文を組む。`render.active_data` が既に計算した値を読むだけで、
   新しいクエリ層は作らない）
3. 送信の直前に**禁止語スキャン**という機構層のゲートを必ず1つ通す

## 受信の対応づけ（D11。2026-09-04 の指摘で書き直した版）

最初の実装は「1通のブリーフィングに全 open decision をまとめて書き、スレッド返信を
`thread_ts` だけで引く」形だった。だがブリーフィングは普通いくつもの判断待ちに触れる
——1通に複数 decision が乗るとスレッド返信では**どれへの返信か決められず**、受信が
ほぼ常に不発になる（推測しない、を貫くと機能そのものが動かない）。**曖昧さを推測で
埋めるのではなく、送り方を変えて曖昧さそのものを無くした**:

1. **`brief` は「まとめ」を1通、続けて open な decision ごとに1通ずつ送る。**
   まとめの通も `slack_message` に行を残す（**`decision_id` は NULL**——id 明示の
   返信をそのスレッドでも拾えるようにするための記録で、対応する decision は無い）。
   decision ごとの通は**その decision 専用のメッセージの `ts`** を1行だけ持つ。
   これでスレッド返信は decision に **1:1** で引ける
2. **返信の本文先頭に id（`D3` のような）があれば、それを優先する。**
   id が本文に明示されているのは推測ではない。id がスレッドから引ける decision と
   食い違うとき、または id が送信記録に無いときは**取り込まず** inbox へ（矛盾を
   主人に見せる。id が無ければ従来どおりスレッドの対応で引く）

結果として `home/inbox/slack-<date>.md` へ落ちるのは「id も無く、スレッドからも
decision を特定できない返信」「文面から承認／却下／修正を読み取れない返信」
「id とスレッドが食い違う返信」だけになる。

冪等性は `slack_reply` 表（`UNIQUE(channel, ts)`）で守る。裁定できた・できなかったに
関わらず「処理済みの返信」としてここへ記録するので、同じ返信を次回また `inbox` を
回しても二重に裁定したり `home/inbox/` へ二重に書いたりしない。

## 秘密（D4・ADR-009 §2）

`bot_token` は `secrets.get("slack", "bot_token")` から読む。着手時点では
`src/manor/secrets.py` が無かったため、`import` を関数の中で遅延させてある
（`secrets.py` が無くてもこのモジュール自体は読み込め、試験は `_secrets_get`——
このモジュールの内部の継ぎ目——を差し替えるだけで実装を待たずに走る）。並行して
拡張機構を作っていた担当がその後 `secrets.py` を実装しており、実際に繋いで確かめた
ところ、このモジュールを1行も変えずにそのまま動いた（`MANOR_SECRETS_DIR` で隔離した
統合試験で確認済み）。

チャンネル ID（秘密ではない）は `home/config.toml` の `[slack] channel`（`[voice]` と
同じ流儀。ADR-009 D4「非秘密の設定は home/config.toml の [<id>]」）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import db, decision as decision_mod, i18n, render as render_mod, runlog, util
from .errors import ManorError

CONFIG_FILE_NAME = "config.toml"
SLACK_API_BASE = "https://slack.com/api"

#: 疎通・送信の HTTP タイムアウト（秒）。voice.py の STATUS_TIMEOUT 等と同じ流儀。
API_TIMEOUT = 15.0

#: `--generate` で `claude -p` を呼ぶときの既定。
GENERATE_MODEL = "sonnet"
GENERATE_MAX_TURNS = 4
GENERATE_TIMEOUT_SECONDS = 180.0

#: 生成ステップに道具を持たせない（本文を書くだけの仕事に副作用を持ち込まない）。
#: v1 `watch-inbox.ps1` の `--disallowed-tools WebFetch WebSearch` を踏襲しつつ、
#: ここでは判断（本文執筆）と送受信（このファイルの他の関数）をさらに厳密に分けるため
#: ファイル操作・実行系もまとめて塞ぐ。
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

#: 禁止語彙リストの既定の置き場（`.githooks/pre-commit` と同じ。リポジトリの外）。
LEAK_TERMS_ENV = "MANOR_LEAK_TERMS"

#: まとめの通の末尾に添える案内。decision ごとの通が続くこと・id で明示的に裁定できることを
#: 伝える（id 優先の対応づけが「推測ではなく明示」であるための前提——主人に使い方を教える）。
DECISION_REPLY_HINT = (
    "この後、判断待ちを1件ずつ個別に送ります。"
    "そのスレッドへの返信で「承認」「却下」「修正: 一言」と裁定できます。"
    "id を付けて「D3 承認」のように返信しても構いません。"
)


def _default_leak_terms_path() -> Path:
    return Path.home() / ".manor" / "git-leak-terms.txt"


# --- 設定（home/config.toml の [slack]。D4） -----------------------------------------------


def _load_slack_config(home: Path) -> dict[str, object]:
    """`home/config.toml` の `[slack]` 節。無い・壊れているときは空辞書
    （`voice._load_voice_config` と同じ流儀）。"""
    path = Path(home) / CONFIG_FILE_NAME
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except Exception:  # noqa: BLE001 - 壊れた TOML は「未設定」として扱う
        return {}
    section = data.get("slack") if isinstance(data, dict) else None
    return section if isinstance(section, dict) else {}


def channel_id(home: Path) -> str:
    """`[slack] channel`。無ければ空文字。"""
    cfg = _load_slack_config(home)
    ch = cfg.get("channel")
    return ch.strip() if isinstance(ch, str) else ""


# --- 秘密（D4）。`secrets.py` が無くても import で落ちないよう遅延 import にしてある ----------


def _secrets_get(key: str) -> str | None:
    """`secrets.get("slack", key)` の薄いラッパー。`manor.secrets` を関数内で遅延
    import する——モジュールがまだ無い環境でも `manor.slack` 自体の import は落ちない。
    試験はこの関数を差し替える（`secrets.py` の実装を待たない）。
    """
    try:
        from . import secrets  # noqa: PLC0415
    except ImportError:
        return None
    try:
        value = secrets.get("slack", key)
    except Exception:  # noqa: BLE001 - 秘密の読み出しの失敗で送受信全体を落とさない
        return None
    return value if isinstance(value, str) and value.strip() else None


def bot_token() -> str | None:
    return _secrets_get("bot_token")


# --- Slack Web API（urllib のみ。D10） ------------------------------------------------------


def _slack_api(
    method: str, token: str, *, params: dict[str, object] | None = None, timeout: float = API_TIMEOUT
) -> dict[str, object]:
    """Slack Web API を1回呼ぶ。**例外は投げない**——失敗は Slack 自身のエラー形
    （`{"ok": False, "error": "..."}`）に揃えて返す。`chat.postMessage` は JSON の
    POST、それ以外（`auth.test` / `conversations.replies`）はクエリ文字列の GET/POST
    で送る（Slack Web API はどちらの形でも受け付けるが、POST 系は JSON、参照系は
    クエリのほうが素直なのでそう分けてある）。
    """
    url = f"{SLACK_API_BASE}/{method}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        if method == "chat.postMessage":
            body = json.dumps(params or {}).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        else:
            qs = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
            full_url = f"{url}?{qs}" if qs else url
            req = urllib.request.Request(full_url, headers=headers, method="POST" if method == "auth.test" else "GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - Slack の公式 API ドメイン固定
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
            data = json.loads(raw.decode("utf-8"))
            if isinstance(data, dict):
                data.setdefault("ok", False)
                return data
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "error": f"http_error_{exc.code}"}
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return {"ok": False, "error": f"network_error: {exc}"}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return {"ok": False, "error": f"bad_response: {exc}"}
    if not isinstance(data, dict):
        return {"ok": False, "error": "unexpected_response"}
    return data


def test_connection(home: Path | str | None = None) -> dict[str, object]:
    """`auth.test` を叩いて疎通を確かめる（ADR-009 D3 の `check()` の実体）。
    例外は投げない。`home` は今のところ使わない（token はホーム非依存。将来
    channel の生死も見るならここへ足す）。
    """
    checked_at = util.now()
    token = bot_token()
    if not token:
        return {
            "ok": False,
            "reason": "bot_token が未設定です（manor ext set slack --secret bot_token）",
            "checked_at": checked_at,
        }
    resp = _slack_api("auth.test", token, params={})
    if not resp.get("ok"):
        return {"ok": False, "reason": f"Slack API エラー: {resp.get('error', '不明')}", "checked_at": checked_at}
    return {
        "ok": True,
        "reason": "",
        "checked_at": checked_at,
        "team": resp.get("team"),
        "user": resp.get("user"),
    }


# --- 禁止語スキャン（D10。`.githooks/pre-commit` / `tests/test_privacy_boundary.py` と同じ場所・
# 同じ形式を読む）。**この読み込みをここに複製している**——`test_privacy_boundary.py` にも
# 同種の読み込みがインラインで存在する（本タスクの指示どおり。共有先の importable な既存
# ローダは無かった）。両者は同じ規約（1行1語・`#` コメント・大小文字無視・BOM/CR除去）に
# 従うが、関数としては別々。将来どちらかを importable な共通モジュールへ寄せる余地がある。
# ------------------------------------------------------------------------------------------


def _leak_terms_path() -> Path:
    override = os.environ.get(LEAK_TERMS_ENV, "").strip()
    return Path(override) if override else _default_leak_terms_path()


def load_leak_terms() -> list[str] | None:
    """禁止語彙を読む。ファイルが無ければ `None`（fail-closed の印。v1 `Test-Denylist`
    ／ `.githooks/pre-commit` と同じ——**リストが無いことと、一致が無いことを区別する**）。
    """
    path = _leak_terms_path()
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    return [t.strip() for t in raw.splitlines() if t.strip() and not t.strip().startswith("#")]


def scan_for_leak_terms(text: str) -> dict[str, object]:
    """送信直前の禁止語スキャン（D10）。**一致した語そのものは返さない**
    ——`position`（本文中の最初の一致位置。0始まり）だけを返す。

    戻り値: `{"ok": bool, "reason": str, "position": int | None}`。
    `ok=False` かつ `position is None` は「語彙リストが読めず検査できなかった」
    （fail-closed。v1 と同じく「送らない」側へ倒す）。
    """
    terms = load_leak_terms()
    if terms is None:
        path = _leak_terms_path()
        return {"ok": False, "reason": f"禁止語リストが見つかりません（{path}）", "position": None}
    lowered = text.lower()
    best_pos: int | None = None
    for t in terms:
        if not t:
            continue
        idx = lowered.find(t.lower())
        if idx >= 0 and (best_pos is None or idx < best_pos):
            best_pos = idx
    if best_pos is not None:
        return {"ok": False, "reason": "禁止語に一致しました", "position": best_pos}
    return {"ok": True, "reason": "", "position": None}


# --- 本文の組み立て（機械／生成。D10） -------------------------------------------------------


def _clip(text: object, limit: int = 88) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def format_mechanical_brief(data: dict[str, object]) -> str:
    """「まとめ」の1通。`render.active_data` が既に計算した値だけから組む
    （`--generate` 無しの既定。D10: `claude` が壊れていても「今日の要対応は N 件」は
    送れる）。**新しいクエリ層は作らない**——ここは整形だけ。open decision の詳細
    （推奨・risk 等）はここには出さない——decision ごとの個別の通（`_format_decision_message`）
    が担当する。まとめは件数と ID・件名の一覧に留める。
    """
    open_decisions = list(data["open_decisions"])  # type: ignore[arg-type]
    section_a = list(data["section_a"])  # type: ignore[arg-type]
    milestones = list(data["milestones"])  # type: ignore[arg-type]
    today = util.today()
    today_milestones = [m for m in milestones if str(m["date"]) == today]

    lines: list[str] = [f"主人待ち: 判断 {len(open_decisions)}件 / タスク {len(section_a)}件"]
    for d in open_decisions:
        lines.append(f"- {d['id']}（{d['days']}日）{_clip(d['title'], 70)}")
    for t in section_a:
        rec = _clip(t.get("recommendation") or "", 40)
        tail = f"（推奨: {rec}）" if rec else ""
        lines.append(f"- {t['id']} {_clip(t['title'], 60)}{tail}")
    if today_milestones:
        lines.append(f"今日のマイルストーン: {len(today_milestones)}件")
        for m in today_milestones:
            lines.append(f"- {_clip(m['title'], 70)}")
    if not open_decisions and not section_a and not today_milestones:
        lines.append("本日、主人待ち・今日のマイルストーンはともにありません。")
    return "\n".join(lines)


def _format_decision_message(decision_id: str, detail: dict[str, object], days: object) -> str:
    """decision ごとの個別の通（常に機械組み。`--generate` の対象外）。**1通=1decision**
    にすることで、返信のスレッドが decision に 1:1 で引ける（D11 の書き直しの核心）。
    """
    title = str(detail.get("title") or "")
    lines = [f"{decision_id}　{_clip(title, 70)}"]
    rec = _clip(detail.get("recommendation") or "", 80)
    if rec:
        lines.append(f"推奨: {rec}")
    risk = str(detail.get("risk") or "")
    if risk:
        lines.append(f"risk: {risk}")
    if days is not None:
        lines.append(f"（{days}日 経過）")
    lines.append("→ このスレッドへの返信で裁定できます: 「承認」／「却下」／「修正: 直したい点」")
    return "\n".join(lines)


def _build_generate_prompt(mechanical: str) -> str:
    return (
        "以下は今日の状況を DB から機械的に組んだ下書きです。この内容だけをもとに、"
        "Slack に送る短い日本語のブリーフィングを書いてください。"
        "事実を足さない・数字を変えない・道具は使わない。6行程度、絵文字は使わない。"
        "本文だけを出力してください（前置き・後書きは不要）。\n\n---\n" + mechanical
    )


def _claude_generate_argv(*, model: str, claude_bin: str | None) -> list[str]:
    # `talk.py` の `build_command` / `night/runner.py` の `default_exec_argv` と同じ解決順。
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
    prompt: str, *, model: str = GENERATE_MODEL, claude_bin: str | None = None,
    timeout: float = GENERATE_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """`claude -p` に本文を書かせる（「まとめ」の通だけが対象。decision ごとの通は常に
    機械組み）。`gate.py` / `night/runner.py` と同じパターン（`subprocess.run` で JSON
    出力を受け、`is_error` を見る）。**例外は投げない**。
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


# --- brief（送信。D10・D11） -------------------------------------------------------------------


def brief(
    home: Path, *, generate: bool = False, dry_run: bool = False, claude_bin: str | None = None
) -> dict[str, object]:
    """`manor slack brief`。

    1. 「まとめ」を1通組む（既定は `render.active_data` から機械的に。`--generate` なら
       `claude -p` に書き直させる。失敗・`claude` 不在なら機械組みへフォールバック）
    2. open な decision ごとに個別の通を組む（**常に機械組み**。1通=1decision）
    3. **すべての本文**（まとめ＋個別）へ送信直前の禁止語スキャンを通す。**1つでも
       引っかかれば、何も送らない**（まとめだけ・個別だけ送れてしまう中途半端な状態を
       作らない）
    4. `--dry-run` ならここで終える（**HTTP は一切呼ばない**）
    5. まとめを送り、続けて decision ごとに送る。decision の通の `ts` を `slack_message`
       へ1行ずつ記録する（1decision=1行=1ts。D11の対応づけの元）
    """
    home = Path(home)
    conn = db.connect(home)
    try:
        data = render_mod.active_data(conn)
        open_decisions = list(data["open_decisions"])  # type: ignore[assignment]
        mechanical = format_mechanical_brief(data)
        summary_text = mechanical
        generated = False
        generate_note = ""

        if generate:
            claude_path = claude_bin or shutil.which("claude")
            if claude_path is None:
                generate_note = "claude が見つからないため機械組みへフォールバックしました"
            else:
                run_id = runlog.start(conn, "talk", ref="slack-brief", model=GENERATE_MODEL)
                conn.commit()
                result = _run_claude_generate(
                    _build_generate_prompt(mechanical), model=GENERATE_MODEL, claude_bin=claude_bin
                )
                if result["ok"]:
                    summary_text = str(result["text"])
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

        if open_decisions:
            summary_text = summary_text + "\n\n" + DECISION_REPLY_HINT

        # decision ごとの通（常に機械組み。1decision=1通=1スレッドにするための核心）。
        decision_texts: dict[str, str] = {}
        for d in open_decisions:
            decision_id = str(d["id"])
            detail = decision_mod.show(conn, decision_id)
            decision_texts[decision_id] = _format_decision_message(decision_id, detail, d.get("days"))

        # 送信直前の禁止語スキャン。**まとめ・個別のどれか1つでも引っかかれば何も送らない**
        # （中途半端に一部だけ届く状態を作らない）。
        scan_failures: list[dict[str, object]] = []
        summary_scan = scan_for_leak_terms(summary_text)
        if not summary_scan["ok"]:
            scan_failures.append(
                {"which": "summary", "reason": summary_scan["reason"], "position": summary_scan["position"]}
            )
        for decision_id, text in decision_texts.items():
            s = scan_for_leak_terms(text)
            if not s["ok"]:
                scan_failures.append({"which": decision_id, "reason": s["reason"], "position": s["position"]})

        if scan_failures:
            # **本文そのものは返さない**——decision のタイトル等、禁止語を含む本文を
            # そのまま応答に載せると「語は隠したが文脈で漏れる」ことになりかねない。
            # 返すのは `scan_failures`（`which` と `position` だけ）に留める。
            return {
                "sent": False,
                "reason": "禁止語に一致しました",
                "scan_failures": scan_failures,
                "generated": generated,
                "generate_note": generate_note,
            }

        if dry_run:
            return {
                "sent": False,
                "dry_run": True,
                "text": summary_text,
                "decision_texts": decision_texts,
                "generated": generated,
                "generate_note": generate_note,
                "decisions": list(decision_texts.keys()),
            }

        token = bot_token()
        if not token:
            return {"sent": False, "reason": "bot_token が未設定です", "text": summary_text, "generated": generated}
        channel = channel_id(home)
        if not channel:
            return {
                "sent": False,
                "reason": "channel が未設定です（home/config.toml の [slack]）",
                "text": summary_text,
                "generated": generated,
            }

        summary_resp = _slack_api("chat.postMessage", token, params={"channel": channel, "text": summary_text})
        if not summary_resp.get("ok"):
            return {
                "sent": False,
                "reason": f"Slack API エラー: {summary_resp.get('error', '不明')}",
                "text": summary_text,
                "generated": generated,
            }
        summary_ts = str(summary_resp.get("ts") or "")
        sent_channel = str(summary_resp.get("channel") or channel)
        now_ts = util.now()

        # まとめの通も `slack_message` に記録する（`decision_id IS NULL`）。**decision には
        # 紐づけない**が、そのスレッドへ「D3 承認」のように id を明示した返信が来たときに
        # `inbox()` がそのスレッドを引けるようにするための記録（id 優先の対応づけの前提）。
        conn.execute(
            "INSERT INTO slack_message (decision_id, channel, ts, sent_at) VALUES (NULL, ?, ?, ?)",
            (sent_channel, summary_ts, now_ts),
        )

        decision_results: list[dict[str, object]] = []
        for decision_id, text in decision_texts.items():
            resp = _slack_api("chat.postMessage", token, params={"channel": sent_channel, "text": text})
            if not resp.get("ok"):
                decision_results.append(
                    {"decision_id": decision_id, "sent": False, "reason": f"Slack API エラー: {resp.get('error', '不明')}"}
                )
                continue
            d_ts = str(resp.get("ts") or "")
            conn.execute(
                "INSERT INTO slack_message (decision_id, channel, ts, sent_at) VALUES (?, ?, ?, ?)",
                (decision_id, sent_channel, d_ts, now_ts),
            )
            decision_results.append({"decision_id": decision_id, "sent": True, "ts": d_ts})
        conn.commit()

        return {
            "sent": True,
            "ts": summary_ts,
            "channel": sent_channel,
            "text": summary_text,
            "decisions": decision_results,
            "generated": generated,
            "generate_note": generate_note,
        }
    finally:
        conn.close()


# --- inbox（受信。D11） ----------------------------------------------------------------------


_APPROVE_WORDS: frozenset[str] = frozenset({"承認", "ok", "はい"})
_REJECT_WORDS: frozenset[str] = frozenset({"却下", "だめ", "いいえ"})

#: 返信の先頭の id（`D3` のような decision node id）。大小文字は問わない
#: （`d3` も受ける）が、内部表現は常に `D<n>` へ正規化する。
_LEADING_ID_RE = re.compile(r"^[Dd](\d+)\b[\s:：,、]*")

#: 「修正」「差し戻し」＋一言（コロン・読点・空白のいずれかで区切ってよい）。
_MODIFY_PREFIX_RE = re.compile(r"^(修正|差し戻し)\s*[:：]?\s*(.*)$", re.DOTALL)


def _normalize_reply_text(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("- "):
        t = t[2:].strip()
    if t.endswith("。"):
        t = t[:-1].strip()
    return t


def _extract_leading_id(text: str) -> tuple[str | None, str]:
    """本文の先頭が decision id（`D3` 等）で始まっていれば `(id, 残りの本文)` を返す。
    無ければ `(None, 正規化した本文)`。**id は明示があれば推測ではない**——D11 の
    id 優先の対応づけの入口。
    """
    t = _normalize_reply_text(text)
    m = _LEADING_ID_RE.match(t)
    if not m:
        return None, t
    decision_id = f"D{m.group(1)}"
    rest = t[m.end() :].strip()
    return decision_id, rest


def _parse_verdict_and_ruling(text: str) -> tuple[str, str] | None:
    """id を除いた本文から `(verdict, ruling)` を読む。読み取れなければ `None`
    （裁定にしない）。承認／却下は本文**全体**がその語と一致するときだけ受ける
    （`OK です` のように動詞の後ろへ文が続く形は受けない。v1 README の姿勢を踏襲）。
    `修正`／`差し戻し` は一言（コロンの後ろ）が**必須**——空なら裁定にしない。
    """
    t = text.strip()
    key = t.lower()
    if key in _APPROVE_WORDS:
        return "approved", ""
    if key in _REJECT_WORDS:
        return "rejected", ""
    m = _MODIFY_PREFIX_RE.match(t)
    if m:
        note = m.group(2).strip()
        if not note:
            return None  # 一言の無い「修正」は裁定にしない
        return "modified", note
    return None


def parse_reply(text: str) -> dict[str, object]:
    """返信本文を解釈する。戻り値: `{"id": str|None, "verdict": str|None, "ruling": str}`。
    `id` は本文先頭に明示されていた decision id（無ければ `None`）。`verdict` が
    `None` なら裁定にしない（id の有無に関わらず）。
    """
    decision_id, rest = _extract_leading_id(text)
    parsed = _parse_verdict_and_ruling(rest)
    if parsed is None:
        return {"id": decision_id, "verdict": None, "ruling": ""}
    verdict, ruling = parsed
    return {"id": decision_id, "verdict": verdict, "ruling": ruling}


def _record_consumed(
    conn: sqlite3.Connection, *, channel: str, ts: str, thread_ts: str,
    decision_id: str | None, verdict: str,
) -> None:
    conn.execute(
        "INSERT INTO slack_reply (channel, ts, thread_ts, decision_id, verdict, consumed_at)"
        " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(channel, ts) DO NOTHING",
        (channel, ts, thread_ts, decision_id, verdict, util.now()),
    )


def _write_inbox_file(home: Path, unmapped: list[dict[str, object]]) -> Path:
    """対応づけできなかった返信を `home/inbox/slack-<日付>.md` へ落とす（D11）。
    既存ファイルへは追記する（同日に複数回 `inbox` を回しても記録が失われない）。
    """
    date = util.today()
    path = Path(home) / "inbox" / f"slack-{date}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    body_lines: list[str] = []
    for item in unmapped:
        body_lines.append(
            f"- channel={item['channel']} ts={item['ts']} thread_ts={item.get('thread_ts', '')}"
        )
        if item.get("decision_id"):
            body_lines.append(f"  decision: {item['decision_id']}")
        body_lines.append(f"  理由: {item['reason']}")
        body_lines.append(f"  本文: {item['text']}")
    body = "\n".join(body_lines) + "\n"
    if path.is_file():
        with path.open("a", encoding="utf-8") as f:
            f.write(body)
    else:
        header = f"# Slack 受信（{date}）\n\n対応づけできなかった返信です。主人の判断が要ります。\n\n"
        path.write_text(header + body, encoding="utf-8")
    return path


def inbox(home: Path, *, dry_run: bool = False) -> dict[str, object]:
    """`manor slack inbox`。

    記録済みの `slack_message`（送信時に残した channel・ts。decision ごとの通に加え、
    「まとめ」の通も `decision_id=NULL` で記録されている——id 明示の返信をそのスレッドでも
    拾うため）ごとに `conversations.replies` を引き、新着の返信を裁定として取り込む。

    対応づけ（D11。優先順）:

    1. 本文の先頭に id（`D3` 等）があり、それが送信記録にある decision なら**その id を使う**
       （スレッドがどれであっても——「まとめ」のスレッドへの返信でも id があれば拾える）
    2. id が無ければ、そのスレッド自身が対応している decision を使う（1通=1decision なので
       常に0件か1件）
    3. id があり、かつスレッドの decision と食い違うときは**取り込まない**（矛盾を
       主人に見せる。多いほうへ倒すような推測はしない）
    4. id も無く、スレッドからも decision を特定できない（＝「まとめ」のスレッドへの
       素の「承認」等）ときも**取り込まない**

    取り込めなかった返信・文面から裁定を読み取れなかった返信は `home/inbox/slack-<date>.md`
    へ理由つきで落とす。

    `--dry-run`: 実際には裁定を適用せず・`slack_reply` にも記録しない・`home/inbox/` にも
    書かない。判定結果だけを返す（HTTP は呼ぶ——Slack から読むだけなので D10 の
    「dry-run は HTTP を呼ばない」は送信側 `brief()` の話。読み取りは実害が無い）。
    """
    home = Path(home)
    conn = db.connect(home)
    try:
        token = bot_token()
        if not token:
            return {"ok": False, "reason": "bot_token が未設定です", "ruled": [], "unmapped": [], "errors": []}

        threads = conn.execute(
            "SELECT DISTINCT channel, ts FROM slack_message ORDER BY sent_at"
        ).fetchall()

        # id 優先の対応づけのため、「送信記録のある decision」の集合をあらかじめ引く。
        # 本文の id がこの集合に無ければ「送信記録の無い id」として扱う（推測しない）。
        # まとめの通の行（decision_id IS NULL）は対象外。
        known_ids = {
            str(r["decision_id"])
            for r in conn.execute(
                "SELECT DISTINCT decision_id FROM slack_message WHERE decision_id IS NOT NULL"
            ).fetchall()
        }

        ruled: list[dict[str, object]] = []
        unmapped: list[dict[str, object]] = []
        errors: list[dict[str, object]] = []

        for row in threads:
            channel = str(row["channel"])
            thread_ts = str(row["ts"])
            resp = _slack_api(
                "conversations.replies", token, params={"channel": channel, "ts": thread_ts, "limit": 200}
            )
            if not resp.get("ok"):
                errors.append({"channel": channel, "ts": thread_ts, "reason": str(resp.get("error", "不明"))})
                continue

            messages = resp.get("messages")
            if not isinstance(messages, list):
                continue

            # このスレッド自身が対応している decision。1通=1decision の設計なので
            # 常に0件（「まとめ」の通。decision_id は NULL）か1件（decision 専用の通）。
            thread_rows = conn.execute(
                "SELECT DISTINCT decision_id FROM slack_message"
                " WHERE channel = ? AND ts = ? AND decision_id IS NOT NULL",
                (channel, thread_ts),
            ).fetchall()
            thread_ids = [str(r["decision_id"]) for r in thread_rows]
            thread_decision = thread_ids[0] if len(thread_ids) == 1 else None

            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                msg_ts = str(msg.get("ts") or "")
                if not msg_ts or msg_ts == thread_ts:
                    continue  # 親メッセージ自身は返信ではない
                if msg.get("bot_id") or msg.get("subtype"):
                    continue  # Bot 自身の投稿は読まない（無限ループの防止）

                already = conn.execute(
                    "SELECT id FROM slack_reply WHERE channel = ? AND ts = ?", (channel, msg_ts)
                ).fetchone()
                if already is not None:
                    continue  # 冪等: 既に処理済み

                text = str(msg.get("text") or "")
                parsed = parse_reply(text)
                id_in_text = parsed["id"]

                decision_id: str | None
                reason: str = ""
                if id_in_text is not None:
                    if id_in_text not in known_ids:
                        decision_id = None
                        reason = f"本文の {id_in_text} は送信記録がありません"
                    elif thread_decision is not None and thread_decision != id_in_text:
                        decision_id = None
                        reason = f"本文の {id_in_text} とスレッドの {thread_decision} が食い違います"
                    else:
                        decision_id = id_in_text
                else:
                    decision_id = thread_decision
                    if decision_id is None:
                        reason = "id も無く、スレッドからも decision を特定できません"

                if decision_id is None:
                    item = {
                        "channel": channel, "ts": msg_ts, "thread_ts": thread_ts,
                        "text": text, "reason": reason,
                    }
                    unmapped.append(item)
                    if not dry_run:
                        _record_consumed(conn, channel=channel, ts=msg_ts, thread_ts=thread_ts, decision_id=None, verdict="")
                    continue

                verdict = parsed["verdict"]
                if verdict is None:
                    item = {
                        "channel": channel, "ts": msg_ts, "thread_ts": thread_ts, "text": text,
                        "decision_id": decision_id, "reason": "文面から承認／却下／修正を読み取れません",
                    }
                    unmapped.append(item)
                    if not dry_run:
                        _record_consumed(
                            conn, channel=channel, ts=msg_ts, thread_ts=thread_ts,
                            decision_id=decision_id, verdict="",
                        )
                    continue

                if dry_run:
                    ruled.append(
                        {"channel": channel, "ts": msg_ts, "decision_id": decision_id, "verdict": verdict, "would_rule": True}
                    )
                    continue

                default_word = {"approved": "承認", "rejected": "却下"}.get(verdict, "")
                ruling_body = str(parsed["ruling"]) or default_word
                ruling = f"{ruling_body}（Slack）"
                try:
                    decision_mod.rule(conn, decision_id, verdict, ruling=ruling)
                except ManorError as exc:
                    item = {
                        "channel": channel, "ts": msg_ts, "thread_ts": thread_ts, "text": text,
                        "decision_id": decision_id, "reason": exc.message_ja,
                    }
                    unmapped.append(item)
                    _record_consumed(
                        conn, channel=channel, ts=msg_ts, thread_ts=thread_ts,
                        decision_id=decision_id, verdict="",
                    )
                    continue
                _record_consumed(
                    conn, channel=channel, ts=msg_ts, thread_ts=thread_ts,
                    decision_id=decision_id, verdict=verdict,
                )
                ruled.append({"channel": channel, "ts": msg_ts, "decision_id": decision_id, "verdict": verdict})

        if unmapped and not dry_run:
            _write_inbox_file(home, unmapped)

        if not dry_run:
            conn.commit()
        return {"ok": True, "ruled": ruled, "unmapped": unmapped, "errors": errors, "dry_run": dry_run}
    finally:
        conn.close()


# --- CLI（`manor slack ...`。DB は各関数が自分で開閉するので needs_db=False。
# `voice.py`／`night/__init__.py` と同じ流儀） -----------------------------------------------


def _print_json(obj: object) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _cmd_brief(args: argparse.Namespace) -> int:
    home = util.manor_home()
    result = brief(home, generate=bool(args.generate), dry_run=bool(args.dry_run))
    if args.json:
        _print_json(result)
    else:
        if result.get("sent"):
            print(i18n.t("slack.brief.sent", channel=result.get("channel"), ts=result.get("ts")))
            for d in result.get("decisions", []):  # type: ignore[union-attr]
                if isinstance(d, dict) and d.get("sent"):
                    print(i18n.t("slack.brief.decision_sent", decision_id=d["decision_id"], ts=d["ts"]))
                elif isinstance(d, dict):
                    print(i18n.t("slack.brief.decision_failed", decision_id=d["decision_id"], reason=d.get("reason", "")))
        elif result.get("dry_run"):
            print(i18n.t("slack.brief.dry_run_header"))
            print(i18n.t("slack.brief.dry_run_summary_label"))
            print(result.get("text", ""))
            for did, text in (result.get("decision_texts") or {}).items():  # type: ignore[union-attr]
                print(f"[{did}]")
                print(text)
        else:
            # `reason` は check()（web の拡張ステータス表示）とも共有する診断文字列なので
            # 訳さない（calendar.py と同じ判断）——包む文だけ訳す。
            print(i18n.t("slack.brief.not_sent", reason=result.get("reason", "")))
            for f in result.get("scan_failures", []) or []:  # type: ignore[union-attr]
                print(i18n.t("slack.brief.scan_failure", which=f["which"], position=f["position"]))
        note = result.get("generate_note")
        if note:
            print(i18n.t("import_v1.note_line", note=note))
    return 0 if (result.get("sent") or result.get("dry_run")) else 1


def _cmd_inbox(args: argparse.Namespace) -> int:
    home = util.manor_home()
    result = inbox(home, dry_run=bool(args.dry_run))
    if args.json:
        _print_json(result)
    else:
        if not result.get("ok"):
            print(i18n.t("slack.inbox.failed", reason=result.get("reason", "")))
        else:
            for r in result.get("ruled", []):  # type: ignore[union-attr]
                tag = i18n.t("slack.inbox.tag_would_rule") if r.get("would_rule") else i18n.t("slack.inbox.tag_applied")
                print(i18n.t("slack.inbox.ruled_line", tag=tag, decision_id=r["decision_id"], verdict=r["verdict"]))
            for u in result.get("unmapped", []):  # type: ignore[union-attr]
                print(i18n.t("slack.inbox.unmapped_line", channel=u["channel"], ts=u["ts"], reason=u["reason"]))
            for e in result.get("errors", []):  # type: ignore[union-attr]
                print(i18n.t("slack.inbox.error_line", channel=e["channel"], ts=e["ts"], reason=e["reason"]))
            if not result.get("ruled") and not result.get("unmapped"):
                print(i18n.t("slack.inbox.no_new_replies"))
    return 0 if result.get("ok", True) else 1


def _cmd_test(args: argparse.Namespace) -> int:
    home = util.manor_home()
    result = test_connection(home)
    if args.json:
        _print_json(result)
    else:
        if result.get("ok"):
            print(i18n.t("slack.test.ok", team=result.get("team"), user=result.get("user")))
        else:
            print(i18n.t("slack.test.failed", reason=result.get("reason", "")))
    return 0 if result.get("ok") else 1


def _add_slack_subcommands(sub: "argparse._SubParsersAction", *, needs_db: bool | None) -> None:
    """`brief`／`inbox`／`test` を1つの `add_subparsers()` の戻り値へ足す。
    `register()`（`manor slack ...` として配線される版）と `main()`
    （`python -m manor.slack ...` として単独で動く版）の両方から呼ぶ
    （`needs_db` は前者だけが要る印。後者は `None` で `set_defaults` に足さない）。
    """
    extra: dict[str, object] = {} if needs_db is None else {"needs_db": needs_db}

    b = sub.add_parser("brief", help=i18n.t("cli.slack.brief.help"))
    b.add_argument(
        "--generate", action="store_true",
        help=i18n.t("cli.slack.brief.generate.help"),
    )
    b.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help=i18n.t("cli.slack.brief.dry_run.help"),
    )
    b.add_argument("--json", action="store_true")
    b.set_defaults(func=_cmd_brief, **extra)

    i = sub.add_parser("inbox", help=i18n.t("cli.slack.inbox.help"))
    i.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help=i18n.t("cli.slack.inbox.dry_run.help"),
    )
    i.add_argument("--json", action="store_true")
    i.set_defaults(func=_cmd_inbox, **extra)

    t = sub.add_parser("test", help=i18n.t("cli.slack.test.help"))
    t.add_argument("--json", action="store_true")
    t.set_defaults(func=_cmd_test, **extra)


def register(subparsers: "argparse._SubParsersAction") -> None:
    """`manor slack brief|inbox|test` を足す（将来 `cli.py` から配線される公開口。
    `gate.py` / `night/__init__.py` と同じ形）。それまでの起動口は
    `python -m manor.slack ...`（本ファイル自身が `__main__` にもなる）。
    """
    p = subparsers.add_parser("slack", help=i18n.t("cli.slack.help"))
    sub = p.add_subparsers(dest="verb")
    _add_slack_subcommands(sub, needs_db=False)


def main(argv: list[str] | None = None) -> int:
    """`python -m manor.slack brief|inbox|test ...`（`cli.py` へ配線される前の起動口。
    `gate.py` の `main()` と同じ形）。
    """
    try:
        import sys

        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass
    parser = argparse.ArgumentParser(prog="python -m manor.slack", description=i18n.t("cli.slack.help"))
    sub = parser.add_subparsers(dest="verb")
    _add_slack_subcommands(sub, needs_db=None)

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
