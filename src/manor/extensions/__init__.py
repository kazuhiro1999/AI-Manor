"""拡張機能の明示的な登録簿（ADR-009 D2）。

各拡張は `src/manor/extensions/<id>.py` が `MANIFEST` と `detect()`/`check()`（任意で
`options()`）を持つ1ファイル。**暗黙の走査はしない**——ここが import して並べる
（`web/src/app/registry.ts` と同じ考え方。`src/manor/staff/__init__.py` の `pkgutil` 走査とは
違い、こちらは「知らないコードを実行しない」ため意図的に明示的）。

MANIFEST の形は import 時に検算する（壊れていれば起動時に分かるようにする——検算せず
実行時に初めて壊れて分かる、を避けるため）。

状態は5つ（D3）。`detect()` が `not_installed` かどうかを、保存済みの設定が
`needs_config` かどうかを、`check()`（`test()` 経由。押されたときだけ）が `ok`/`error` を
決める。結果は `home/extensions/state.json` に `{id: {status, checked_at, reason}}` として残る。

秘密は `src/manor/secrets.py`（`~/.manor/secrets/<id>.json`）。**ここでも読み出しの口は
作らない**——`save_settings`/`forget` は書くだけ、`status`/`statuses`/`detail` が返すのは
`has_<key>: bool` だけ（D4）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from .. import branding
from .. import i18n
from .. import secrets as secrets_mod
from .. import util
from ..errors import ManorError
from ..web import config as web_config
from . import calendar as _calendar_mod
from . import notion as _notion_mod
from . import slack as _slack_mod
from . import tailscale as _tailscale_mod
from . import voicevox as _voicevox_mod

# --- MANIFEST の検算（import 時。D2） -----------------------------------------------

REQUIRED_MANIFEST_KEYS = ("id", "label", "kind", "summary", "install_steps", "fields", "secret_fields")
VALID_KINDS = {"local_app", "service", "network"}
VALID_FIELD_KINDS = {"text", "password", "number", "select", "path"}


class ExtensionManifestError(RuntimeError):
    """MANIFEST が壊れている（起動時に分かるようにするための専用の例外）。"""


def _validate_manifest(module: ModuleType) -> dict[str, object]:
    name = module.__name__
    manifest = getattr(module, "MANIFEST", None)
    if not isinstance(manifest, dict):
        raise ExtensionManifestError(f"{name}: MANIFEST がありません（辞書である必要があります）")
    missing = [k for k in REQUIRED_MANIFEST_KEYS if k not in manifest]
    if missing:
        raise ExtensionManifestError(f"{name}: MANIFEST に必須キーがありません: {missing}")
    if not isinstance(manifest["id"], str) or not manifest["id"].strip():
        raise ExtensionManifestError(f"{name}: id は空でない文字列が必要です")
    if not isinstance(manifest["label"], str) or not manifest["label"].strip():
        raise ExtensionManifestError(f"{name}: label は空でない文字列が必要です")
    if manifest["kind"] not in VALID_KINDS:
        raise ExtensionManifestError(f"{name}: kind が不正です（{manifest['kind']!r}）: {VALID_KINDS}")
    if not isinstance(manifest["summary"], str):
        raise ExtensionManifestError(f"{name}: summary は文字列が必要です")
    steps = manifest["install_steps"]
    if not isinstance(steps, list) or not all(isinstance(s, str) for s in steps):
        raise ExtensionManifestError(f"{name}: install_steps は文字列のリストが必要です")
    fields = manifest["fields"]
    if not isinstance(fields, list):
        raise ExtensionManifestError(f"{name}: fields はリストが必要です")
    for field in fields:
        if not isinstance(field, dict):
            raise ExtensionManifestError(f"{name}: fields の各要素は辞書が必要です")
        for key in ("key", "label", "kind"):
            if not isinstance(field.get(key), str) or not str(field.get(key)).strip():
                raise ExtensionManifestError(f"{name}: fields[].{key} が不正です: {field!r}")
        if field["kind"] not in VALID_FIELD_KINDS:
            raise ExtensionManifestError(f"{name}: fields[].kind が不正です: {field!r}")
    secret_fields = manifest["secret_fields"]
    if not isinstance(secret_fields, list) or not all(isinstance(s, str) for s in secret_fields):
        raise ExtensionManifestError(f"{name}: secret_fields は文字列のリストが必要です")
    return manifest


@dataclass(frozen=True)
class _Entry:
    module: ModuleType
    manifest: dict[str, object]


#: 登録簿本体。**明示的に import して並べる**（D2）。
_MODULES: tuple[ModuleType, ...] = (
    _voicevox_mod,
    _tailscale_mod,
    _slack_mod,
    _notion_mod,
    _calendar_mod,
)

_ENTRIES: dict[str, _Entry] = {}
for _mod in _MODULES:
    _manifest = _validate_manifest(_mod)
    _id = str(_manifest["id"])
    if _id in _ENTRIES:
        raise ExtensionManifestError(f"重複した拡張 id です: {_id}")
    _ENTRIES[_id] = _Entry(module=_mod, manifest=_manifest)
del _mod, _manifest, _id


def _entry(id_: str) -> _Entry:
    entry = _ENTRIES.get(id_)
    if entry is None:
        raise ManorError(
            f"拡張が見つかりません: {id_}",
            code=2,
            key="error.ext.not_found",
            params={"id": id_},
        )
    return entry


def _config_section(entry: _Entry) -> str:
    return str(getattr(entry.module, "CONFIG_SECTION", entry.manifest["id"]))


def _implied_config(entry: _Entry) -> dict[str, object]:
    return dict(getattr(entry.module, "IMPLIED_CONFIG", {}))


# --- state.json（D3） ----------------------------------------------------------------


def _extensions_dir(home: Path) -> Path:
    return Path(home) / "extensions"


def _state_path(home: Path) -> Path:
    return _extensions_dir(home) / "state.json"


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp_name, path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


def _load_state(home: Path) -> dict[str, dict[str, object]]:
    path = _state_path(home)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(home: Path, data: dict[str, object]) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    _atomic_write_bytes(_state_path(home), payload)


def _clear_state(home: Path, id_: str) -> None:
    state = _load_state(home)
    if id_ in state:
        del state[id_]
        _write_state(home, state)


# --- detect/check の安全な呼び出し（「detect/check/options から例外を出さない」の二重の砦） ----


def _safe_detect(entry: _Entry, home: Path) -> dict[str, object]:
    try:
        result = entry.module.detect(home)
    except Exception as exc:  # noqa: BLE001
        return {"installed": False, "reason": f"検出できませんでした: {exc}"}
    if not isinstance(result, dict):
        return {"installed": False, "reason": "detect() の戻り値が不正です"}
    return result


def _safe_check(entry: _Entry, home: Path) -> dict[str, object]:
    try:
        result = entry.module.check(home)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"確認できませんでした: {exc}"}
    if not isinstance(result, dict):
        return {"ok": False, "reason": "check() の戻り値が不正です"}
    return result


# --- 公開 API（web / CLI 共通） ---------------------------------------------------------


def all_manifests() -> list[dict[str, object]]:
    """登録順（import した順）のマニフェスト一覧。"""
    return [dict(e.manifest) for e in _ENTRIES.values()]


def get(id_: str) -> dict[str, object]:
    """1件のマニフェスト。無ければ `ManorError(code=2)`。"""
    return dict(_entry(id_).manifest)


def _section_values(home: Path, entry: _Entry) -> dict[str, object]:
    """`CONFIG_SECTION` の生の値。モジュールが `from_config(cfg) -> dict` を持てば、
    その戻り値（フィールド鍵→値）を重ねて返す（ADR-011 D10・ADR-009 D2 の追記）——
    `[voice.speakers]` のような入れ子のテーブルを `speaker_<agent>` という平らな
    フィールド鍵へ均すための口。フックが無い拡張は素の節の値のまま（今までどおり）。
    """
    section = _config_section(entry)
    cfg_values = web_config.read_config(home).get(section, {})
    if not isinstance(cfg_values, dict):
        cfg_values = {}
    from_config_fn = getattr(entry.module, "from_config", None)
    if from_config_fn is not None:
        try:
            derived = from_config_fn(cfg_values)
        except Exception:  # noqa: BLE001 - フォームの表示を壊さない
            derived = None
        if isinstance(derived, dict):
            cfg_values = {**cfg_values, **derived}
    return cfg_values


def _missing_required_fields(home: Path, entry: _Entry) -> list[str]:
    cfg_values = _section_values(home, entry)
    id_ = str(entry.manifest["id"])
    secret_keys = set(entry.manifest.get("secret_fields", []))  # type: ignore[arg-type]
    missing: list[str] = []
    for field in entry.manifest.get("fields", []):  # type: ignore[union-attr]
        if not field.get("required"):
            continue
        key = field["key"]
        if key in secret_keys:
            if not secrets_mod.has(id_, key):
                missing.append(str(field.get("label", key)))
        else:
            value = cfg_values.get(key)
            if value is None or (isinstance(value, str) and not value.strip()):
                missing.append(str(field.get("label", key)))
    return missing


def status(home: Path, id_: str) -> dict[str, object]:
    """5状態の判定（D3）。優先順位: `not_installed` > `needs_config` >（保存済みの
    `ok`/`error`）> `ready`。**`check()` はここでは回さない**（画面の描画では回さない。
    `test()` が押されたときだけ）。
    """
    entry = _entry(id_)
    home = Path(home)
    detect_result = _safe_detect(entry, home)
    if not detect_result.get("installed"):
        return {
            "id": id_,
            "status": "not_installed",
            "reason": str(detect_result.get("reason") or ""),
            "checked_at": None,
        }
    missing = _missing_required_fields(home, entry)
    if missing:
        return {
            "id": id_,
            "status": "needs_config",
            "reason": "未設定: " + "、".join(missing),
            "checked_at": None,
        }
    saved = _load_state(home).get(id_)
    if isinstance(saved, dict) and saved.get("status") in ("ok", "error"):
        return {
            "id": id_,
            "status": str(saved.get("status")),
            "reason": str(saved.get("reason") or ""),
            "checked_at": saved.get("checked_at"),
        }
    return {"id": id_, "status": "ready", "reason": "", "checked_at": None}


def statuses(home: Path) -> list[dict[str, object]]:
    """`GET /api/v1/extensions` の中身: `[{id,label,kind,summary,status,checked_at,reason}]`。"""
    out: list[dict[str, object]] = []
    for id_, entry in _ENTRIES.items():
        st = status(home, id_)
        out.append(
            {
                "id": id_,
                "label": entry.manifest["label"],
                "kind": entry.manifest["kind"],
                "summary": entry.manifest["summary"],
                "status": st["status"],
                "checked_at": st["checked_at"],
                "reason": st["reason"],
            }
        )
    return out


def _values(home: Path, entry: _Entry) -> dict[str, object]:
    """現在の値。**秘密は `has_<key>` の真偽だけ**（D4）。"""
    cfg_values = _section_values(home, entry)
    id_ = str(entry.manifest["id"])
    secret_keys = set(entry.manifest.get("secret_fields", []))  # type: ignore[arg-type]
    out: dict[str, object] = {}
    for field in entry.manifest.get("fields", []):  # type: ignore[union-attr]
        key = field["key"]
        if key in secret_keys:
            out[f"has_{key}"] = secrets_mod.has(id_, key)
        else:
            out[key] = cfg_values.get(key)
    return out


def detail(home: Path, id_: str) -> dict[str, object]:
    """`GET /api/v1/extensions/{id}` の中身: manifest（fields込み）＋現在の値
    （秘密は has_* のみ）＋install_steps＋現在の状態（D6）。
    """
    entry = _entry(id_)
    home = Path(home)
    st = status(home, id_)
    return {
        "id": id_,
        "manifest": dict(entry.manifest),
        "values": _values(home, entry),
        "install_steps": list(entry.manifest.get("install_steps", [])),  # type: ignore[arg-type]
        "status": st["status"],
        "checked_at": st["checked_at"],
        "reason": st["reason"],
    }


def save_settings(home: Path, id_: str, values: dict[str, object]) -> dict[str, object]:
    """設定の保存（D6 PUT）。秘密は秘密の置き場へ、それ以外は `config.toml` へ。
    **部分更新**——渡さなかったキーはそのまま。`values` に無い・未知のキーは無視する
    （フォームは manifest の `fields` どおりに送る契約）。値が空文字の秘密キーは削除扱い。

    モジュールが `to_config(values) -> dict` を持てば、非秘密の値をそこへ通してから
    `config.toml` へ書く（ADR-011 D10・ADR-009 D2 の追記）——`speaker_<agent>` のような
    平らなフィールド鍵を `[voice.speakers]` の入れ子のテーブルへ変換するための口。
    フックが無い拡張はこれまでどおりフィールド鍵をそのまま節へ書く。
    """
    entry = _entry(id_)
    home = Path(home)
    field_keys = {f["key"] for f in entry.manifest.get("fields", [])}  # type: ignore[union-attr]
    secret_keys = set(entry.manifest.get("secret_fields", []))  # type: ignore[arg-type]
    non_secret_updates: dict[str, object] = {}
    for key, value in values.items():
        if key not in field_keys:
            continue
        if key in secret_keys:
            if value is None:
                continue
            text = str(value)
            if text == "":
                secrets_mod.delete(id_, key)
            else:
                secrets_mod.set(id_, key, text)
        elif value is not None:
            non_secret_updates[key] = value
    section = _config_section(entry)
    implied = _implied_config(entry)
    to_config_fn = getattr(entry.module, "to_config", None)
    if to_config_fn is not None:
        config_updates = to_config_fn(non_secret_updates)
        if not isinstance(config_updates, dict):
            config_updates = {}
    else:
        config_updates = non_secret_updates
    if implied or config_updates:
        web_config.update_section(home, section, {**implied, **config_updates})
    return status(home, id_)


def test(home: Path, id_: str) -> dict[str, object]:
    """`check()` を回して `home/extensions/state.json` を更新する（D6 POST test）。
    **押されたときだけ回す**——`status()`/`statuses()`/`detail()` はここを呼ばない。
    """
    entry = _entry(id_)
    home = Path(home)
    result = _safe_check(entry, home)
    ok = bool(result.get("ok"))
    state = _load_state(home)
    state[id_] = {
        "status": "ok" if ok else "error",
        "reason": str(result.get("reason") or ""),
        "checked_at": util.now(),
    }
    _write_state(home, state)
    return status(home, id_)


def options(home: Path, id_: str, name: str) -> list[dict[str, object]]:
    """`GET /api/v1/extensions/{id}/options/{name}`（D5）。外部が落ちていれば空リスト
    （例外は出さない。拡張側が守るが、ここでも二重に守る）。
    """
    entry = _entry(id_)
    home = Path(home)
    fn = getattr(entry.module, "options", None)
    if fn is None:
        return []
    try:
        result = fn(home, name)
    except Exception:  # noqa: BLE001 - D5「外部が落ちていれば空を返す」
        return []
    if not isinstance(result, list):
        return []
    out: list[dict[str, object]] = []
    for item in result:
        if isinstance(item, dict) and "value" in item and "label" in item:
            # `value`/`label` は必須。`group`/`member_label` は**任意**で、あれば画面が
            # 「親 → 子」の2段で選ばせる（ADR-009 D17）。知らない鍵は落とす——ここは
            # 契約の関所なので、拡張が何を返しても外へ出るのは決めた形だけにする。
            option: dict[str, object] = {"value": item["value"], "label": str(item["label"])}
            group = item.get("group")
            if isinstance(group, str) and group.strip():
                option["group"] = group
            member_label = item.get("member_label")
            if isinstance(member_label, str) and member_label.strip():
                option["member_label"] = member_label
            out.append(option)
    return out


def forget(home: Path, id_: str) -> dict[str, object]:
    """`DELETE /api/v1/extensions/{id}`（D6）。設定（fields＋隠しキー分）と秘密を消す。
    その拡張が管理していないキー（同じ config セクションに他の拡張・機能が書いた値）には触れない。

    モジュールが `config_keys() -> set[str]` を持てば、消す生キーの集合はそちらを使う
    （ADR-011 D10・ADR-009 D2 の追記）——`speaker_<agent>` のようなフィールド鍵は節の
    生キーと一致しない（`[voice.speakers]` という入れ子のテーブルへ丸めて書くため）ので、
    素朴に `field_keys` を消すだけでは足りない拡張のための口。フックが無ければ今までどおり
    `field_keys | implied_keys`。
    """
    entry = _entry(id_)
    home = Path(home)
    section = _config_section(entry)
    field_keys = {f["key"] for f in entry.manifest.get("fields", [])}  # type: ignore[union-attr]
    implied_keys = set(_implied_config(entry).keys())
    config_keys_fn = getattr(entry.module, "config_keys", None)
    keys_to_clear = set(config_keys_fn()) if config_keys_fn is not None else (field_keys | implied_keys)

    data = dict(web_config.read_config(home))
    current = dict(data.get(section, {})) if isinstance(data.get(section), dict) else {}
    for key in keys_to_clear:
        current.pop(key, None)
    data[section] = current
    web_config.write_config(home, data)

    secrets_mod.delete(id_)
    _clear_state(home, id_)
    return status(home, id_)


# --- CLI（`manor ext ...`。D8。DB には触れない = needs_db=False） ---------------------------


def _print_json(obj: object) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _cmd_list(args: argparse.Namespace) -> int:
    home = util.manor_home()
    rows = statuses(home)
    if args.json:
        _print_json(rows)
        return 0
    if not rows:
        print(i18n.t("ext.list.empty"))
        return 0
    for r in rows:
        checked = i18n.t("ext.list.checked_tail", checked_at=r["checked_at"]) if r.get("checked_at") else ""
        reason = i18n.t("ext.list.reason_tail", reason=r["reason"]) if r.get("reason") else ""
        print(i18n.t("ext.list.line", id=r["id"], label=r["label"], status=r["status"], checked=checked, reason=reason))
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    home = util.manor_home()
    try:
        data = detail(home, args.id)
    except ManorError as exc:
        print(exc.localized_message())
        return exc.code
    if args.json:
        _print_json(data)
        return 0
    manifest = data["manifest"]
    print(i18n.t("ext.show.header", id=data["id"], label=manifest["label"], status=data["status"]))  # type: ignore[index]
    print(manifest["summary"])  # type: ignore[index]
    if data.get("reason"):
        print(i18n.t("ext.show.reason", reason=data["reason"]))
    print(i18n.t("ext.show.install_steps_header"))
    for step in data["install_steps"]:  # type: ignore[union-attr]
        print(f"  {step}")
    fields = manifest["fields"]  # type: ignore[index]
    if fields:
        print(i18n.t("ext.show.fields_header"))
        values = data["values"]  # type: ignore[index]
        for field in fields:  # type: ignore[union-attr]
            key = field["key"]
            if f"has_{key}" in values:  # type: ignore[operator]
                state = (
                    i18n.t("ext.show.field_configured")
                    if values[f"has_{key}"]  # type: ignore[index]
                    else i18n.t("ext.show.field_not_configured")
                )
                print(i18n.t("ext.show.field_secret_line", key=key, label=field["label"], state=state))
            else:
                print(i18n.t("ext.show.field_line", key=key, label=field["label"], value=values.get(key)))  # type: ignore[union-attr]
    return 0


def _cmd_set(args: argparse.Namespace) -> int:
    home = util.manor_home()
    if args.secret:
        key = args.secret
        value = sys.stdin.readline().rstrip("\n").rstrip("\r")
    else:
        if not args.key or args.value is None:
            print(i18n.t("ext.set.usage_error"))
            return 2
        key = args.key
        value = args.value
    try:
        save_settings(home, args.id, {key: value})
        data = detail(home, args.id)
    except ManorError as exc:
        print(exc.localized_message())
        return exc.code
    if args.json:
        _print_json(data)
        return 0
    print(i18n.t("ext.set.done", id=args.id, key=key, status=data["status"]))
    return 0


def _cmd_test(args: argparse.Namespace) -> int:
    home = util.manor_home()
    try:
        test(home, args.id)
        data = detail(home, args.id)
    except ManorError as exc:
        print(exc.localized_message())
        return exc.code
    if args.json:
        _print_json(data)
        return 0 if data["status"] == "ok" else 1
    print(i18n.t("ext.test.line", id=args.id, status=data["status"], reason=data.get("reason") or ""))
    return 0 if data["status"] == "ok" else 1


def register(subparsers: "argparse._SubParsersAction") -> None:
    """`manor ext list|show|set|test` を足す（ADR-009 D8。画面が正でも道具は道具）。"""
    p = subparsers.add_parser("ext", help=i18n.t("cli.ext.help", app_name=branding.APP_NAME))
    sub = p.add_subparsers(dest="verb")

    s = sub.add_parser("list", help=i18n.t("cli.ext.list.help"))
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=_cmd_list, needs_db=False)

    s = sub.add_parser("show", help=i18n.t("cli.ext.show.help"))
    s.add_argument("id")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=_cmd_show, needs_db=False)

    s = sub.add_parser(
        "set", help=i18n.t("cli.ext.set.help")
    )
    s.add_argument("id")
    s.add_argument("key", nargs="?", help=i18n.t("cli.ext.set.key.help"))
    s.add_argument("value", nargs="?", help=i18n.t("cli.ext.set.value.help"))
    s.add_argument("--secret", metavar="KEY", help=i18n.t("cli.ext.set.secret.help"))
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=_cmd_set, needs_db=False)

    s = sub.add_parser("test", help=i18n.t("cli.ext.test.help"))
    s.add_argument("id")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=_cmd_test, needs_db=False)
