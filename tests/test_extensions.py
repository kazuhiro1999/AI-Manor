"""`extensions`（ADR-009 D2〜D6・§5）の試験。**すべて合成データ・tmp_path 隔離**。

`home_path`/`home` フィクスチャ（`tests/conftest.py`）が `MANOR_HOME` と
`MANOR_SECRETS_DIR`（ADR-009 D4）の両方を一時ディレクトリへ向けるので、本物の
`home/` にも `~/.manor/secrets/` にも触れない。
"""

from __future__ import annotations

import json
import os
import types
from pathlib import Path

import pytest

from manor import extensions as ext_mod
from manor import secrets as secrets_mod
from manor.errors import ManorError

# --- D2: 登録簿の形 -------------------------------------------------------------------


def test_all_manifests_have_required_keys() -> None:
    manifests = ext_mod.all_manifests()
    ids = [m["id"] for m in manifests]
    assert "voicevox" in ids
    assert "tailscale" in ids
    for m in manifests:
        for key in ext_mod.REQUIRED_MANIFEST_KEYS:
            assert key in m
        assert m["kind"] in ext_mod.VALID_KINDS
        assert isinstance(m["install_steps"], list)
        for field in m["fields"]:
            assert field["kind"] in ext_mod.VALID_FIELD_KINDS
            assert "key" in field and "label" in field


def test_get_unknown_id_raises_manor_error() -> None:
    with pytest.raises(ManorError):
        ext_mod.get("no-such-extension")


def test_get_returns_a_copy_not_the_live_manifest() -> None:
    """`get()` の戻り値をいじっても登録簿本体は変わらない（呼び出し側が汚染できない）。"""
    m = ext_mod.get("voicevox")
    m["label"] = "改ざん"
    assert ext_mod.get("voicevox")["label"] != "改ざん"


@pytest.mark.parametrize(
    "manifest",
    [
        {},  # 必須キーが丸ごと無い
        {"id": "x", "label": "x", "kind": "local_app", "summary": "", "install_steps": [], "fields": []},  # secret_fields 無し
        {
            "id": "x", "label": "x", "kind": "not-a-real-kind", "summary": "", "install_steps": [],
            "fields": [], "secret_fields": [],
        },  # kind が不正
        {
            "id": "x", "label": "x", "kind": "local_app", "summary": "", "install_steps": [],
            "fields": [{"key": "a", "label": "a", "kind": "not-a-real-field-kind"}], "secret_fields": [],
        },  # field.kind が不正
    ],
)
def test_manifest_validation_rejects_malformed_manifest(manifest: dict[str, object]) -> None:
    """import 時の検算（`_validate_manifest`）が壊れた形を拒むこと（D2「必須キーを持つ」）。"""
    fake = types.ModuleType("manor.extensions._fake_bad")
    fake.MANIFEST = manifest  # type: ignore[attr-defined]
    with pytest.raises(ext_mod.ExtensionManifestError):
        ext_mod._validate_manifest(fake)  # type: ignore[arg-type]


def test_manifest_validation_accepts_real_modules() -> None:
    from manor.extensions import tailscale as tailscale_mod
    from manor.extensions import voicevox as voicevox_mod

    ext_mod._validate_manifest(voicevox_mod)
    ext_mod._validate_manifest(tailscale_mod)


# --- fixture: 秘密フィールドを持つ拡張（本体の voicevox/tailscale はどちらも秘密を持たない
# ので、「秘密が API から読み出せないこと」を試すには1つ差し込む必要がある） ------------------


@pytest.fixture
def fake_secret_ext(monkeypatch: pytest.MonkeyPatch) -> str:
    fake_module = types.ModuleType("manor.extensions._fake_secret")
    manifest = {
        "id": "fake_secret",
        "label": "偽の拡張（秘密あり）",
        "kind": "service",
        "summary": "試験用",
        "install_steps": ["手順1"],
        "fields": [{"key": "bot_token", "label": "トークン", "kind": "password", "required": True}],
        "secret_fields": ["bot_token"],
    }
    fake_module.MANIFEST = manifest  # type: ignore[attr-defined]
    fake_module.detect = lambda home: {"installed": True, "reason": ""}  # type: ignore[attr-defined]
    fake_module.check = lambda home: {"ok": True, "reason": "つながった"}  # type: ignore[attr-defined]
    entry = ext_mod._Entry(module=fake_module, manifest=manifest)
    monkeypatch.setitem(ext_mod._ENTRIES, "fake_secret", entry)
    return "fake_secret"


def test_secret_never_appears_in_detail_or_status(home: Path, fake_secret_ext: str) -> None:
    id_ = fake_secret_ext
    ext_mod.save_settings(home, id_, {"bot_token": "sekai-no-himitsu"})

    detail = ext_mod.detail(home, id_)
    assert "sekai-no-himitsu" not in json.dumps(detail, ensure_ascii=False)
    assert detail["values"]["has_bot_token"] is True
    assert "bot_token" not in detail["values"]  # 値そのもののキーは無い

    st = ext_mod.status(home, id_)
    assert "sekai-no-himitsu" not in json.dumps(st, ensure_ascii=False)
    assert st["status"] == "ready"  # 必須フィールドが埋まった


def test_secret_missing_keeps_needs_config(home: Path, fake_secret_ext: str) -> None:
    st = ext_mod.status(home, fake_secret_ext)
    assert st["status"] == "needs_config"
    assert "未設定" in st["reason"]


def test_forget_clears_secret_and_config(home: Path, fake_secret_ext: str) -> None:
    id_ = fake_secret_ext
    ext_mod.save_settings(home, id_, {"bot_token": "sekai-no-himitsu"})
    assert secrets_mod.has(id_, "bot_token") is True

    ext_mod.forget(home, id_)
    assert secrets_mod.has(id_, "bot_token") is False
    st = ext_mod.status(home, id_)
    assert st["status"] == "needs_config"


def test_empty_string_secret_value_deletes_it(home: Path, fake_secret_ext: str) -> None:
    id_ = fake_secret_ext
    ext_mod.save_settings(home, id_, {"bot_token": "x"})
    assert secrets_mod.has(id_, "bot_token") is True
    ext_mod.save_settings(home, id_, {"bot_token": ""})
    assert secrets_mod.has(id_, "bot_token") is False


# --- D3: 状態遷移（voicevox で確かめる） -----------------------------------------------


def test_status_needs_config_then_ready_then_ok_then_error(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from manor.extensions import voicevox as voicevox_mod

    monkeypatch.setattr(voicevox_mod, "detect", lambda home: {"installed": True, "reason": "見つかった体"})

    st = ext_mod.status(home, "voicevox")
    assert st["status"] == "needs_config"  # speaker が未設定

    ext_mod.save_settings(home, "voicevox", {"speaker": 13})
    st = ext_mod.status(home, "voicevox")
    assert st["status"] == "ready"
    assert st["checked_at"] is None

    monkeypatch.setattr(voicevox_mod, "check", lambda home: {"ok": True, "reason": "つながった"})
    ext_mod.test(home, "voicevox")
    st = ext_mod.status(home, "voicevox")
    assert st["status"] == "ok"
    assert st["checked_at"]

    monkeypatch.setattr(voicevox_mod, "check", lambda home: {"ok": False, "reason": "落ちた"})
    ext_mod.test(home, "voicevox")
    st = ext_mod.status(home, "voicevox")
    assert st["status"] == "error"
    assert st["reason"] == "落ちた"


def test_status_not_installed_overrides_needs_config(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """detect() が False を返せば、設定が足りていても not_installed が勝つ（優先順位）。"""
    from manor.extensions import voicevox as voicevox_mod

    monkeypatch.setattr(voicevox_mod, "detect", lambda home: {"installed": False, "reason": "見つからない"})
    ext_mod.save_settings(home, "voicevox", {"speaker": 1})
    st = ext_mod.status(home, "voicevox")
    assert st["status"] == "not_installed"


def test_statuses_list_shape(home: Path) -> None:
    rows = ext_mod.statuses(home)
    ids = {r["id"] for r in rows}
    assert {"voicevox", "tailscale"} <= ids
    for r in rows:
        assert set(r.keys()) == {"id", "label", "kind", "summary", "status", "checked_at", "reason"}


def test_status_survives_detect_raising(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """detect() が例外を投げても `status()` は落ちない（「例外を出さない」の二重の砦）。"""
    from manor.extensions import tailscale as tailscale_mod

    def boom(home: Path) -> dict[str, object]:
        raise RuntimeError("boom")

    monkeypatch.setattr(tailscale_mod, "detect", boom)
    st = ext_mod.status(home, "tailscale")
    assert st["status"] == "not_installed"
    assert "boom" in st["reason"]


def test_test_survives_check_raising(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from manor.extensions import tailscale as tailscale_mod

    monkeypatch.setattr(tailscale_mod, "detect", lambda home: {"installed": True, "reason": ""})

    def boom(home: Path) -> dict[str, object]:
        raise RuntimeError("boom")

    monkeypatch.setattr(tailscale_mod, "check", boom)
    result = ext_mod.test(home, "tailscale")
    assert result["status"] == "error"
    assert "boom" in result["reason"]


def test_tailscale_ready_and_ok_without_config(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """tailscale は設定項目を持たない（D9）ので、導入済みなら即 ready。"""
    from manor.extensions import tailscale as tailscale_mod

    monkeypatch.setattr(tailscale_mod, "detect", lambda home: {"installed": True, "reason": "見つかった"})
    st = ext_mod.status(home, "tailscale")
    assert st["status"] == "ready"

    monkeypatch.setattr(tailscale_mod, "check", lambda home: {"ok": True, "reason": "100.x.x.x"})
    ext_mod.test(home, "tailscale")
    st = ext_mod.status(home, "tailscale")
    assert st["status"] == "ok"


# --- D9: voicevox が既存 [voice] 節へ書くこと（互換） ------------------------------------


def test_save_settings_writes_into_existing_voice_section(home: Path) -> None:
    """ADR-009 の本題: 保存すると既存の voice.py がそのまま拾える形で書かれる
    （新しい [voicevox] ではなく [voice]、しかも engine が隠しキーとして自動で入る）。
    """
    from manor import voice as voice_mod
    from manor.web import config as web_config

    ext_mod.save_settings(home, "voicevox", {"speaker": 8})

    data = web_config.read_config(home)
    assert "voicevox" not in data
    assert data["voice"]["engine"] == "voicevox"
    assert data["voice"]["speaker"] == 8

    cfg = voice_mod.load_config(home)
    assert cfg is not None
    assert cfg.speaker == 8


def test_forget_voicevox_clears_only_its_own_keys(home: Path) -> None:
    """[voice] 節の中でも、拡張が管理していないキー（例: speak_command は notify のもの）は
    forget で消えない。"""
    from manor.web import config as web_config

    web_config.update_section(home, "voice", {"unrelated_key": "keep-me"})
    ext_mod.save_settings(home, "voicevox", {"speaker": 3, "engine_path": r"C:\vv\run.exe"})

    ext_mod.forget(home, "voicevox")
    data = web_config.read_config(home)
    assert data["voice"]["unrelated_key"] == "keep-me"
    assert "engine" not in data["voice"]
    assert "speaker" not in data["voice"]
    assert "engine_path" not in data["voice"]


# --- ADR-011 D10: web/config.py の入れ子のテーブル対応（土台） ----------------------------


def test_update_section_writes_nested_table_and_round_trips(home: Path) -> None:
    from manor.web import config as web_config

    web_config.update_section(home, "voice", {"engine": "voicevox", "speakers": {"housekeeper": 46}})

    text = (home / "config.toml").read_text(encoding="utf-8")
    assert "[voice.speakers]" in text
    assert "housekeeper = 46" in text

    data = web_config.read_config(home)
    assert data["voice"]["engine"] == "voicevox"
    assert data["voice"]["speakers"]["housekeeper"] == 46


def test_update_section_merges_nested_table_instead_of_replacing(home: Path) -> None:
    from manor.web import config as web_config

    web_config.update_section(home, "voice", {"speakers": {"housekeeper": 46}})
    web_config.update_section(home, "voice", {"speakers": {"chef": 3}})

    data = web_config.read_config(home)
    assert data["voice"]["speakers"] == {"housekeeper": 46, "chef": 3}


def test_update_section_none_deletes_a_nested_key(home: Path) -> None:
    from manor.web import config as web_config

    web_config.update_section(home, "voice", {"speakers": {"housekeeper": 46, "chef": 3}})
    web_config.update_section(home, "voice", {"speakers": {"housekeeper": None}})

    data = web_config.read_config(home)
    assert data["voice"]["speakers"] == {"chef": 3}


def test_update_section_none_is_not_written_when_the_table_is_new(home: Path) -> None:
    """削除の印（`None`）は、その入れ子テーブルが**まだ無いとき**でも書き込まれない。

    実測（2026-09-05）: 初回保存だけ `chef = ""` という空の行が config.toml に残っていた
    ——「両側が辞書のときだけ再帰する」条件が、相手側が無い初回に素通りしていた。
    """
    from manor.web import config as web_config

    web_config.update_section(home, "voice", {"speakers": {"housekeeper": 46, "chef": None}})

    data = web_config.read_config(home)
    assert data["voice"]["speakers"] == {"housekeeper": 46}
    assert "chef" not in (home / "config.toml").read_text(encoding="utf-8")


def test_update_section_does_not_leave_an_empty_nested_table(home: Path) -> None:
    """上書きを全部外したら、空の `[voice.speakers]` を残さない（見て意味の無い節を作らない）。"""
    from manor.web import config as web_config

    web_config.update_section(home, "voice", {"speakers": {"housekeeper": 46}})
    web_config.update_section(home, "voice", {"speakers": {"housekeeper": None}})

    assert "[voice.speakers]" not in (home / "config.toml").read_text(encoding="utf-8")


# --- ADR-011 D10: 担当ごとの話者フィールド ------------------------------------------------


def test_voicevox_manifest_has_one_optional_speaker_field_per_agent() -> None:
    from manor import agent_meta

    manifest = ext_mod.get("voicevox")
    fields_by_key = {f["key"]: f for f in manifest["fields"]}

    # 既定の話者は先頭で必須のまま（D10「先頭は既定」）。
    assert manifest["fields"][0]["key"] == "speaker"
    assert manifest["fields"][0]["required"] is True

    for agent in agent_meta.valid_agents():
        key = f"speaker_{agent}"
        assert key in fields_by_key, f"{key} が fields に無い"
        field = fields_by_key[key]
        assert field["required"] is False  # D10「担当ごとの欄は任意」
        assert field["kind"] == "select"
        assert field["options_from"] == "speakers"
        assert agent_meta.agent_label(agent) in field["label"]


def test_save_speaker_agent_field_lands_in_voice_speakers_table(home: Path) -> None:
    """D10 の本題: `speaker_housekeeper` は `[voice.speakers] housekeeper` へ、
    フォームの読み出し（`detail().values`）もそこから均して返す。"""
    from manor.web import config as web_config

    ext_mod.save_settings(home, "voicevox", {"speaker": 13, "speaker_housekeeper": 46})

    data = web_config.read_config(home)
    assert data["voice"]["speaker"] == 13
    assert data["voice"]["speakers"]["housekeeper"] == 46
    assert "speaker_housekeeper" not in data["voice"]  # 平らな鍵のままでは残らない

    detail = ext_mod.detail(home, "voicevox")
    assert detail["values"]["speaker_housekeeper"] == 46
    assert detail["values"]["speaker_chef"] is None  # 上書きの無い担当は空欄（None）


def test_save_speaker_agent_field_does_not_disturb_other_agents(home: Path) -> None:
    """1担当ぶんの保存が他の担当の上書きを消さない（`_merge_update` の深いマージ）。"""
    from manor.web import config as web_config

    ext_mod.save_settings(home, "voicevox", {"speaker_housekeeper": 46})
    ext_mod.save_settings(home, "voicevox", {"speaker_chef": 3})

    data = web_config.read_config(home)
    assert data["voice"]["speakers"] == {"housekeeper": 46, "chef": 3}


def test_clearing_speaker_agent_field_removes_only_that_override(home: Path) -> None:
    """フォームで欄を空にすれば、その担当の上書きだけを外す（D10「空なら既定を使う」）。"""
    from manor.web import config as web_config

    ext_mod.save_settings(home, "voicevox", {"speaker_housekeeper": 46, "speaker_chef": 3})
    ext_mod.save_settings(home, "voicevox", {"speaker_housekeeper": ""})

    data = web_config.read_config(home)
    assert "housekeeper" not in data["voice"]["speakers"]
    assert data["voice"]["speakers"]["chef"] == 3

    detail = ext_mod.detail(home, "voicevox")
    assert detail["values"]["speaker_housekeeper"] is None
    assert detail["values"]["speaker_chef"] == 3


def test_forget_voicevox_clears_the_whole_speakers_table_too(home: Path) -> None:
    """`forget()` は `speakers` テーブルまるごと消す（`config_keys()` フック。D10）。"""
    from manor.web import config as web_config

    ext_mod.save_settings(home, "voicevox", {"speaker": 13, "speaker_housekeeper": 46})
    ext_mod.forget(home, "voicevox")

    data = web_config.read_config(home)
    assert "speakers" not in data.get("voice", {})
    assert "speaker" not in data.get("voice", {})


# --- D5: options は外部の不在で空を返す ------------------------------------------------


def test_options_returns_empty_when_engine_unreachable(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from manor.extensions import voicevox as voicevox_mod

    # 実ネットワークへは触れない（試験環境に依存させない）。`_http_get` は失敗すると
    # None を返す約束（voice.py）なので、それをそのまま模す。
    monkeypatch.setattr(voicevox_mod.voice_mod, "_http_get", lambda url, timeout: None)
    assert ext_mod.options(home, "voicevox", "speakers") == []


def test_options_unknown_name_returns_empty(home: Path) -> None:
    assert ext_mod.options(home, "voicevox", "not-a-real-option-name") == []


def test_options_tailscale_has_no_options_fn_returns_empty(home: Path) -> None:
    assert ext_mod.options(home, "tailscale", "anything") == []


def test_options_unknown_extension_raises(home: Path) -> None:
    with pytest.raises(ManorError):
        ext_mod.options(home, "no-such", "speakers")


def test_voicevox_options_flattens_speakers(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`options("speakers")` が `GET /speakers` を `{value,label}` へ平らにすること
    （ADR-009 §1 の本題: `speaker = 13` を名前で選べるようにする）。
    """
    from manor.extensions import voicevox as voicevox_mod

    payload = json.dumps(
        [
            {
                "name": "四国めたん",
                "styles": [{"name": "ノーマル", "id": 2}, {"name": "あまあま", "id": 0}],
            },
            {"name": "ずんだもん", "styles": [{"name": "ノーマル", "id": 3}]},
        ]
    ).encode("utf-8")
    monkeypatch.setattr(voicevox_mod.voice_mod, "_http_get", lambda url, timeout: payload)

    result = voicevox_mod.options(home, "speakers")
    # `label` は単独で意味が通る形のまま（`group` を解さない読み手でも読める）。
    # あわせて `group`/`member_label` を持ち、画面が「話者 → スタイル」の2段にできる（D17）。
    assert {"value": 2, "label": "四国めたん（ノーマル）", "group": "四国めたん", "member_label": "ノーマル"} in result
    assert {"value": 0, "label": "四国めたん（あまあま）", "group": "四国めたん", "member_label": "あまあま"} in result
    assert {"value": 3, "label": "ずんだもん（ノーマル）", "group": "ずんだもん", "member_label": "ノーマル"} in result
    assert len({str(o["group"]) for o in result}) == 2  # 話者は2人


def test_voicevox_options_returns_empty_on_bad_json(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from manor.extensions import voicevox as voicevox_mod

    monkeypatch.setattr(voicevox_mod.voice_mod, "_http_get", lambda url, timeout: b"not json")
    assert voicevox_mod.options(home, "speakers") == []


# --- tailscale: 状態を変えない・例外を出さない ------------------------------------------


def test_tailscale_detect_not_installed_when_command_missing(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from manor.extensions import tailscale as tailscale_mod

    monkeypatch.setattr(tailscale_mod.shutil, "which", lambda name: None)
    # 標準の場所も空にする——**この試験機に本物が入っていると素通りしてしまう**（実測 2026-09-04）。
    monkeypatch.setattr(tailscale_mod, "_WELL_KNOWN_PATHS", ())
    result = tailscale_mod.detect(home)
    assert result["installed"] is False


def test_tailscale_check_never_raises(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from manor.extensions import tailscale as tailscale_mod

    def boom(*a: object, **kw: object) -> object:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(tailscale_mod.shutil, "which", lambda name: "/usr/bin/tailscale")
    monkeypatch.setattr(tailscale_mod.subprocess, "run", boom)
    result = tailscale_mod.check(home)
    assert result["ok"] is False
    assert "kaboom" in result["reason"]


# --- secrets.py 直接（D4） --------------------------------------------------------------


def test_secrets_roundtrip(home_path: Path) -> None:
    assert secrets_mod.has("x", "k") is False
    assert secrets_mod.get("x", "k") is None
    secrets_mod.set("x", "k", "v")
    assert secrets_mod.has("x", "k") is True
    assert secrets_mod.get("x", "k") == "v"

    secrets_mod.delete("x", "k")
    assert secrets_mod.has("x", "k") is False


def test_secrets_delete_whole_file(home_path: Path) -> None:
    secrets_mod.set("y", "a", "1")
    secrets_mod.set("y", "b", "2")
    secrets_mod.delete("y")  # key 省略 = 丸ごと消す
    assert secrets_mod.has("y", "a") is False
    assert secrets_mod.has("y", "b") is False


def test_secrets_env_override_isolates_from_real_home(home_path: Path) -> None:
    secrets_mod.set("z", "k", "v")
    assert str(secrets_mod.secrets_dir()) != str(Path.home() / ".manor" / "secrets")
    assert (secrets_mod.secrets_dir() / "z.json").is_file()


@pytest.mark.skipif(os.name == "nt", reason="POSIX のパーミッションは Windows に無い")
def test_secrets_file_is_0600_on_posix(home_path: Path) -> None:
    import stat as stat_mod

    secrets_mod.set("z", "k", "v")
    mode = (secrets_mod.secrets_dir() / "z.json").stat().st_mode
    assert stat_mod.S_IMODE(mode) == 0o600


# --- CLI（D8） ---------------------------------------------------------------------------


def test_cli_registers_ext_group() -> None:
    from manor import cli as cli_mod

    parser = cli_mod.build_parser()
    args = parser.parse_args(["ext", "list", "--json"])
    assert args.func is not None
    assert getattr(args, "needs_db", True) is False


def test_cli_ext_set_and_show(home: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from manor import cli as cli_mod
    from manor.extensions import voicevox as voicevox_mod

    monkeypatch.setattr(voicevox_mod, "detect", lambda home: {"installed": True, "reason": ""})

    rc = cli_mod.main(["ext", "set", "voicevox", "speaker", "5", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    # CLI は型変換をしない（生の文字列のまま書く）。`voice._as_int` が読み出し側で
    # int("5") に直すので機能的には困らない（`test_save_settings_writes_into_existing_voice_section`
    # で int を渡した場合との差はここだけ。曖昧だった点として報告する）。
    assert out["values"]["speaker"] == "5"
    assert out["status"] == "ready"


def test_cli_ext_set_secret_reads_stdin_not_argv(
    home: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--secret <key>` は標準入力から読む——引数（argv）には値そのものが現れない。"""
    import io

    from manor import cli as cli_mod

    monkeypatch.setattr("sys.stdin", io.StringIO("sekai-no-himitsu\n"))
    argv = ["ext", "set", "fake_secret_cli", "--secret", "bot_token", "--json"]
    assert "sekai-no-himitsu" not in " ".join(argv)  # 引数（=シェル履歴に残る文字列）に値は無い

    fake_module = types.ModuleType("manor.extensions._fake_secret_cli")
    manifest = {
        "id": "fake_secret_cli", "label": "偽", "kind": "service", "summary": "",
        "install_steps": [], "fields": [{"key": "bot_token", "label": "t", "kind": "password", "required": True}],
        "secret_fields": ["bot_token"],
    }
    fake_module.MANIFEST = manifest  # type: ignore[attr-defined]
    fake_module.detect = lambda home: {"installed": True, "reason": ""}  # type: ignore[attr-defined]
    fake_module.check = lambda home: {"ok": True, "reason": ""}  # type: ignore[attr-defined]
    entry = ext_mod._Entry(module=fake_module, manifest=manifest)
    monkeypatch.setitem(ext_mod._ENTRIES, "fake_secret_cli", entry)

    rc = cli_mod.main(argv)
    assert rc == 0
    assert secrets_mod.get("fake_secret_cli", "bot_token") == "sekai-no-himitsu"
    assert "sekai-no-himitsu" not in capsys.readouterr().out


# --- tailscale の探索（2026-09-04・実機で「入っているのに未導入」と出た） ------------------


def test_tailscale_detect_finds_binary_outside_path(monkeypatch, tmp_path, home_path):
    """PATH に無くても標準の場所にあれば「導入済み」。Windows の Tailscale は PATH に載らない。"""
    from manor.extensions import tailscale as ts

    fake = tmp_path / "tailscale.exe"
    fake.write_bytes(b"")
    monkeypatch.setattr(ts.shutil, "which", lambda name: None)
    monkeypatch.setattr(ts, "_WELL_KNOWN_PATHS", (str(fake),))
    result = ts.detect(home_path)
    assert result["installed"] is True
    assert str(fake) in str(result["reason"])


def test_tailscale_detect_reports_missing_when_nowhere(monkeypatch, home_path):
    from manor.extensions import tailscale as ts

    monkeypatch.setattr(ts.shutil, "which", lambda name: None)
    monkeypatch.setattr(ts, "_WELL_KNOWN_PATHS", ())
    assert ts.detect(home_path)["installed"] is False


# --- 階層のある選択肢（ADR-009 D17。主人の指摘 2026-09-04） ------------------------------


def test_options_passes_group_and_member_label_through(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`group`/`member_label` は関所を通る（画面が「親→子」の2段にするために要る）。"""
    from manor import extensions as ext

    entry = ext._ENTRIES["voicevox"]
    monkeypatch.setattr(
        entry.module,
        "options",
        lambda home, name: [
            {"value": 2, "label": "四国めたん（ノーマル）", "group": "四国めたん", "member_label": "ノーマル"},
        ],
    )
    out = ext.options(home, "voicevox", "speakers")
    assert out == [
        {"value": 2, "label": "四国めたん（ノーマル）", "group": "四国めたん", "member_label": "ノーマル"}
    ]


def test_options_drops_unknown_keys_but_keeps_flat_shape(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """知らない鍵は落とす。`group` が無ければ平らなままで、既存の画面が壊れない。"""
    from manor import extensions as ext

    entry = ext._ENTRIES["voicevox"]
    monkeypatch.setattr(
        entry.module,
        "options",
        lambda home, name: [{"value": 1, "label": "ひとつ", "secret": "漏らさない", "group": "  "}],
    )
    assert ext.options(home, "voicevox", "speakers") == [{"value": 1, "label": "ひとつ"}]
