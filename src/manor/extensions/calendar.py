"""カレンダー拡張のマニフェスト（ADR-009 D2・ADR-012 §2「5d カレンダー拡張」D3）。

取り込みの実体（`fetch_ics`／`sync`／`check_connection`）はすべて `src/manor/calendar.py`
に置いてある。このファイルは拡張機構（`src/manor/extensions/`）向けの**薄いマニフェスト
層**——`MANIFEST` と `detect()`／`check()` だけを持つ（`extensions/slack.py`／
`extensions/notion.py` と同じ形。呼び出し規約もそちらに合わせてある）:

- `detect(home: Path) -> {"installed": bool, "reason": str}` — サービス拡張
  （ローカルにインストールする実体を持たない。D1「サービス＋鍵」に相当。
  カレンダーの実体は「URL」であって、ローカルの何かではない）なので `installed` は常に
  `True`
- `check(home: Path) -> {"ok": bool, "reason": str}` — URL を実際に取りに行き、
  ICS として読めるか（`BEGIN:VCALENDAR` が返るか）を確かめる（ADR-012 D3「URL が鍵」）。
  **外部へ実際に触るので、押されたときだけ呼ばれる**（ADR-009 D3）

`url` は **`secret_fields`**（ADR-012 D3）——URL 自体が非公開の鍵であり、知っていれば
誰でもカレンダーの中身を読めるため、`config.toml`（②主人の情報ではなく④環境固有の
置き場だが、git には入らないとはいえ手で覗きやすい）ではなく `~/.manor/secrets/` へ回す。
**カレンダーは1本から始める**（ADR-012 D3。家族分は必要になってから）。
"""

from __future__ import annotations

from pathlib import Path

from .. import branding
from .. import calendar as calendar_mod

MANIFEST: dict[str, object] = {
    "id": "calendar",
    "kind": "service",
    "label": "カレンダー（ICS 取り込み）",
    "summary": (
        "Google カレンダー・Outlook 等の非公開 ICS URL から予定を読み取り、秘書の予定へ"
        f"取り込みます。読み取るだけで、外部へは一切書き戻しません。無くても {branding.APP_NAME}"
        "は完全に動きます。"
    ),
    "install_steps": [
        "Google カレンダー: 左メニューでカレンダーにカーソルを合わせ「⋮」→「設定と共有」→"
        "下の方の「カレンダーの統合」までスクロールし、「非公開の iCal 形式の URL」をコピーする",
        "Outlook（outlook.com）: 「設定」→「カレンダー」→「共有カレンダー」→"
        "「ICS の発行」で発行される URL をコピーする",
        "この画面で URL（url）を貼り付けて保存する。**この URL 自体が鍵**なので、"
        "人に教えたり公開の場所に貼ったりしない",
        "保存後、「試す」または `manor calendar sync` で最初の取り込みを行う"
        "（以降は明示的に sync を回したときだけ更新される。自動では同期しない）",
    ],
    "fields": [
        {
            "key": "url",
            "label": "ICS の URL",
            "kind": "password",
            "required": True,
            "help": "非公開の iCal 形式 URL（多くは .ics で終わる）",
        },
    ],
    "secret_fields": ["url"],
}


def detect(home: Path) -> dict[str, object]:
    """サービス拡張（ローカルにインストールする実体を持たない）ので常に「見つかった」扱い。
    **例外は投げない**。`home` はこの拡張では使わない（`slack.detect(home)` 等と引数の形を
    揃えるため受け取るだけ）。
    """
    return {"installed": True, "reason": ""}


def check(home: Path) -> dict[str, object]:
    """URL を実際に取りに行って ICS として読めるかを確かめる（ADR-009 D3）。
    **外部へ実際に触るので、押されたときだけ呼ばれる**。例外は投げない
    ——`src/manor/calendar.py` の `check_connection()` がその約束を守っている。
    """
    return calendar_mod.check_connection(home)
