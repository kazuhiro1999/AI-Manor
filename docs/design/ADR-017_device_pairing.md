# ADR-017: 端末のペアリング（端末鍵・探索・LAN の規則）

- 状態: 採択（2026-09-13 主人「鍵方式はいいですね。それでお願いします」）
- 関係: ADR-014（利用者）・ADR-015（レシピ帳）・ADR-016（動画リスト）・KitchenXR `Docs/manor-connection.md`

## 1. 背景

KitchenXR（Quest 3）は manor の API をレシピ帳と動画リストの読み書きに使う。v1.0.9 までの繋ぎ方は
`persistentDataPath/manor.json` に **接続先の URL と合言葉を平文で**書いて adb で押し込むものだった。
主人の指摘は3つ:

1. 合言葉を平文でファイルに置きたくない。
2. Quest から届くように manor を `--host 0.0.0.0` で立てると、家の Wi-Fi・tailnet・WSL の口が全部開く。
3. PC の LAN の IP は変わり得るので、URL を固定で書くのは脆い。

## 2. 決めたこと

### D1 端末ごとの鍵（アクセストークン）

- 表 `web_device`: `id`（uuid）・`name`（「Quest 3」など）・`kind`（`kitchenxr`）・`user_id`（ADR-014 の利用者。
  端末はこの利用者として振る舞う）・`token_hash`（鍵の `pbkdf2_sha256`。`web/passcode.py` と同じ形）・
  `created_at`・`last_seen_at`・`revoked_at`。
- 鍵は 32 バイトの乱数を base64url にした文字列。**manor はハッシュしか持たない**（合言葉と同じ扱い）。
- API は `Authorization: Bearer <鍵>` を cookie と並んで受け付ける。鍵が通ると `viewing_user_id` は端末の `user_id`。
- 端末鍵で触れるのは **`/api/v1/kitchen/*` と `/api/v1/devices/me` だけ**（読み書きとも）。Web の頁や他の
  API は端末鍵では 403。失効した鍵は 401（端末は「ペアリングし直し」を出す）。

### D2 ペアリング（番号を Web で許可する）

文字入力は板に置かない（KitchenXR 設計 §6）ので、**番号は端末に出し、主人が Web に入れる**。

1. 端末: `POST /api/v1/devices/pair/start {"name": "Quest 3", "kind": "kitchenxr"}`
   → `{"pair_id", "code": "6桁", "expires_in": 300, "poll_after": 2}`。番号は表示用、`pair_id` は照合用。
2. 端末は番号を大きく出し、`POST /api/v1/devices/pair/poll {"pair_id"}` を `poll_after` 秒おきに叩く
   （`{"status": "pending"}` / `{"status": "approved", "token", "device_id", "user_id"}` / `{"status": "expired"}`）。
   鍵は **一度しか返さない**。
3. 主人: Web の 設定 → 端末 で番号を入れ、利用者を選んで「許可」。ここは通常の（合言葉の）ログインが要る。
4. 番号は 5 分で失効、照合を 5 回外したら `pair_id` ごと捨てる。`pair/start` は送信元アドレスごとに
   1 分 10 回まで（LAN の誰かが番号を積み上げても、主人が許可しない限り何も起きない）。
5. 端末は鍵を受け取ったら **`persistentDataPath/manor-device.json`**（`base_url`・`token`・`device_id`）に保存し、
   以後は `manor.json` を見ない。

### D3 探索（IP が変わっても繋がる）

- manor は UDP **8791** で待ち、`manor-discover v1` の 1 行に
  `{"kind": "manor", "name": <マシン名>, "base_url": "http://<受けた口の IP>:<port>", "version": ...}` を返す。
  返す IP は **その問い合わせが届いた口のアドレス**（NIC が複数あっても正しい方を返す）。
- 探索は Web を loopback 以外で立てたときだけ有効（`[web] discovery = true` が既定。loopback では無意味）。
- 端末は起動時と、API が繋がらなかったときに探索し、見つかった `base_url` で `manor-device.json` を更新する。
  `manor.json` に `base_url` があればそれを優先（tailnet 経由など、探索が届かない置き方のため）。

### D4 LAN の規則（`--host 0.0.0.0` を実用上安全にする）

送信元アドレスを3種に分ける: loopback / tailnet（100.64.0.0/10）/ それ以外（LAN）。

| 口 | loopback・tailnet | LAN |
|---|---|---|
| Web の頁・合言葉のログイン（`/api/v1/auth/login`） | 可 | **不可（403）** ※`[web] lan_passcode_login = true` で許す |
| `/api/v1/devices/pair/*` | 可 | 可（速度制限つき） |
| 端末鍵つきの `/api/v1/kitchen/*` | 可 | 可 |
| それ以外の API | 可（cookie） | 不可 |

つまり LAN に見えている面は「鍵つきの API」と「主人が Web で許可しない限り何も起きないペアリングの口」だけ。
`--host 0.0.0.0` のままでよく、IP に縛らない（D3 と両立）。Windows の受信規則は private プロファイル限定を推奨。

### D5 Web・CLI

- Web: 設定 → **端末**。上に「ペアリング番号」の入力と利用者の選択と「許可」、下に端末の一覧
  （名前・利用者・最後に使った日時・「失効」）。i18n ja/en。
- CLI: `manor web device list` / `manor web device revoke <id>` / `manor web device pair <番号> [--user]`
  （Web が使えないときの逃げ道）。

### D6 KitchenXR 側（別リポジトリ。ここは契約の覚え）

- `manor-device.json` に鍵があればそれで叩く。無ければ探索 → `pair/start` → **ペアリングの板**（6桁を大きく・
  「manor の 設定 → 端末 に入れてください」）→ `pair/poll`。401 が返ったら鍵を捨ててペアリングし直し。
- `manor.json` は `base_url` の上書きだけに残す。`passcode` は読まない（v1.0.10 で廃止）。

### D7 残す課題

- LAN 上の HTTP は平文のまま。必要になったら「ペアリング時に manor の自己署名証明書の指紋を端末へ渡し、
  端末が固定する（TOFU）」を追補で入れる。Wi-Fi は無線区間では暗号化されているので、今回は受け入れる。
- 端末鍵の保存先は Android のアプリ専用外部領域（他アプリからは読めない。adb と MTP では読める）。

## 3. 検算

- 鍵のハッシュ往復、Bearer で `viewing_user_id` が端末の利用者になる、失効で 401、範囲外の口で 403。
- ペアリング: 発行 → 許可 → 一度だけ鍵 → 二度目は `expired`／`used`。失効時間・回数・速度制限。
- D4 の表を送信元ごとに。探索の応答（受けた口の IP を返す）。
- Web: vitest（番号の入力・一覧・失効）。i18n parity。

## 4. 実装で決めた細部（2026-09-12 実装時の追補）

ADR の契約（口の名前・JSON の形・UDP の文言）は変えていない。以下は「決めていなかった
ところ」を実装で埋めた記録である。

### 4.1 表の列（`src/manor/schema/core.sql`）

`web_device`: `id`（uuid4 の hex）・`name`・`kind`・`user_id`・`token_hash`
（`pbkdf2_sha256$200000$<塩>$<値>`。`web/passcode.py` と同じ形）・`created_at`・
`last_seen_at`・`revoked_at`。索引は `web_device_user(user_id)`。
**`user` への外部キーは張らない**（`project.user_id`・`task.user_id` と同じ流儀。
利用者を畳んでも端末の記録は残す）。**失効は行を消さず `revoked_at` を入れる**
（誰がいつ何を持っていたかを残す。照合は `revoked_at IS NULL` だけを見るので即座に効く）。

`web_device_pairing`: `pair_id`・`code`・`name`・`kind`・`created_at`・`expires_at`・
`attempts`・`approved_user_id`・**`device_id`**・`token_plain_once`・`consumed_at`。
索引は `web_device_pairing_code(code)`。

- **`device_id` は ADR 本文に無かった列**。`pair/poll` が `device_id` を返す約束（D2-2）を
  果たすには、許可で作った端末の id をこの行に覚えておく必要がある。
- `token_plain_once` は「許可」から「端末が受け取る」までの**受け渡しの置き場**で、
  `pair/poll` が返した瞬間に `NULL` にし `consumed_at` を入れる。以後 manor に平文は無い。
- 移行は core.sql の `CREATE TABLE IF NOT EXISTS` に任せる（`db.init` と `db.migrate_core`
  の両方が毎回当てるので、`manor init` を忘れた既存 home でも黙って足される。
  `run` 表・`notion_page` 表と同じ経路）。`db.CORE_TABLES` に2つとも加えた（C9 の検査が
  「知らない表」と言わないように）。

### 4.2 `poll` が返す語は3つに閉じる

D2-2 の `pending` / `approved` / `expired` だけを返す。**知らない `pair_id`・期限切れ・
既に鍵を渡した後**はすべて `expired`（§3 の「二度目は `expired`／`used`」のうち
`expired` を採った）。端末に4つめの語を覚えさせない方が、繋ぎ方の取り決めが小さく済む。

### 4.3 照合を5回外したときの扱い（D2-4）

外した番号は**どの行のものとも分からない**（合わないから外れた）。そこで
`device.pair_approve` は、外したときに**まだ待っている行すべて**の `attempts` を1つ進め、
5に達した行を捨てる（`_count_attempt_and_drop`）。番号は端末に出ているので、
やり直しの手間は「もう一度読む」だけである。

### 4.4 速度制限の実装

`web/auth.py` の `KeyedRateLimiter`（既存の `RateLimiter` に鍵を足したもの。プロセス内
メモリのみ）。`ctx.pair_limiter` に1つ持ち、`POST /devices/pair/start` が
**送信元アドレスごとに1分10回**で通す（11回目は 429）。覚える送信元は 256 までで、
窓を過ぎた鍵から掃除する（偽った送信元で辞書が際限なく太らないように）。
`pair/poll` は制限しない（2秒おきに叩く約束なので、10回/分では足りない）。
`pair/approve` は cookie が要る口なので、番号の回数（4.3）だけで守る。

### 4.5 `X-Forwarded-For` の扱い（D4 への追記）

**素通しで信用しない。** 見るのは1つの形だけ——`request.client.host` が**ループバック**
かつ `X-Forwarded-For` の先頭が 100.64/10 のときに限り tailnet 扱いにする。
`tailscale serve` は HTTPS を終端してループバックから転送し、そのとき元の tailnet の IP を
この見出しに入れるためである。LAN の相手が同じ見出しを付けても `request.client.host` が
LAN なので何も変わらない。判定は `web/net.py` の `classify_host` 1か所。

### 4.6 `lan_passcode_login = true` は LAN を tailnet と同じ扱いにする（D4 への追記）

ADR の表はこの印を「Web の頁・合言葉のログイン」の行にだけ書いているが、**ログインだけ
通して他の API を止めると、入れた画面が全部 403 になる**（主人から見れば壊れている）。
「合言葉で入れてよい相手」と決めた以上、その先の cookie の世界も同じ相手に開く
（`web/net.py` の `lan_allows`）。既定は `false`。

### 4.7 LAN の規則は「ループバック以外で待ち受けているとき」だけ効く

`WebContext.lan_rules`（`not is_loopback(host)`）。ループバックの口には LAN から届かないので
区分を分ける意味が無く、**`tailscale serve` 経由の既存の使い方（`127.0.0.1` 待ち受け＋
`require_passcode`）が丸ごと従来どおり動く**（`tests/web/test_lan_rules.py` で検算）。

### 4.8 探索は経路表に選ばせる（D3）

受信ソケットの `getsockname()` は `0.0.0.0` なので使えない。**送信元へ向けて UDP ソケットを
`connect` し、OS の経路表が選んだ送り元アドレスを読む**（`discovery._local_address_for`。
パケットは出ない）。NIC を列挙して同じサブネットを探すより経路表のほうが正しく、
標準ライブラリだけで書ける（`psutil` を足さない）。受け口は `manor web serve` の
lifespan で立て、失敗しても Web は止めない。`[web] discovery`（既定 true）と
`--no-discovery` で止められる。

### 4.9 端末鍵の照合を速くする（pbkdf2 の反復を省く）

pbkdf2 は 20 万回なので1回の照合に数十ミリ秒かかる。`device._TokenCache` が
`sha256(鍵)` → `(device_id, token_hash)` を5分だけ覚えて反復を省く。**行は毎回 DB から
読み直す**ので、失効（`revoked_at`）は覚えていても即座に効く。`last_seen_at` の書き込みは
1分に1回まで間引く（数秒おきの問い合わせをそのまま書き込みにしない）。

### 4.10 端末鍵のときの `viewing_user_id`

`web/_common.py` の `viewing_user_id` は、`request.state.device` があれば**端末の
`user_id`**を返す（cookie `manor_user` は見ない）。畳まれた利用者の端末でもその利用者の
ままにする——主人へ落とすと、畳んだはずの端末が主人の机を覗くことになる。

### 4.11 画面の置き場（D5）

設定の頁の1節（`/settings` の「端末」節。`#settings-devices`）として置いた——既存の
設定は「利用者」「タスクの種類」「姿」などを節で並べる作りで、下位の経路を持たない。
**鍵は画面に出さない**（受け取るのは端末だけ。出せばスクリーンショットや記録に残る）。
失効は2度押し（`window.confirm` は使わない。「畳む」と同じ作法）。
