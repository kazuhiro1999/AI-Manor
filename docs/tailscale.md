# スマホから自宅の manor へ（Tailscale）

公開 URL は作らない。**同じ tailnet に入っている自分の端末だけ**が届く（ADR-004 D7・ADR-005 D4）。
Tailscale はこの PC とスマホに入れてある前提（アカウント作成と端末の認証は主人の手）。

## 2 つのやり方（どちらか）

| | A. tailnet の IP に直接待ち受ける | B. `tailscale serve` で HTTPS にする（**推奨**） |
|---|---|---|
| 起動 | `uv run manor web serve --host <この PC の tailnet IP> --port 8789` | `uv run manor web serve`（127.0.0.1 のまま）＋ `tailscale serve --bg 8789` |
| スマホの URL | `http://<tailnet IP>:8789/` | `https://<マシン名>.<tailnet 名>.ts.net/`（`tailscale serve status` に出る） |
| HTTPS | 無し（HTTP） | **あり**（Tailscale が証明書を持つ） |
| PWA（ホーム画面に追加・アプリ殻のキャッシュ） | **不可**（service worker は HTTPS か localhost だけ） | 可 |
| passcode | 必須（`--host` がループバック以外なので、無ければ起動を拒む） | **既定では聞かれない**（Tailscale が 127.0.0.1 へ転送するので、サーバからはループバックに見える）。聞かせたいなら `[web] require_passcode = true` |
| 守っているもの | tailnet ＋ passcode | tailnet（＋ `require_passcode` で passcode） |

**執事の推奨は B ＋ `require_passcode = true`**（HTTPS で PWA が使え、家族の端末が tailnet に入る日が来ても
二つ目の錠がある）。

## 手順（B）

1. この PC の tailnet 名を確かめる: `tailscale status`（先頭行がこの PC。`tailscale ip -4` で IP）
2. `home/config.toml` に書く（**このファイルは git に載らない**）:

   ```toml
   [web]
   passcode = "好きな合言葉"        # 設定画面（settings）からも書ける。画面には表示されない
   require_passcode = true          # ループバックでも passcode を聞く（tailscale serve 用）
   ```

3. Web アプリを立てる: `uv run manor web serve`（既定 127.0.0.1:8789）
4. Tailscale に転送させる（**一度だけ**。以後は Tailscale が覚える）:

   ```
   tailscale serve --bg 8789
   tailscale serve status
   ```

   `https://<マシン名>.<tailnet 名>.ts.net/` が出る。スマホの Tailscale を ON にして、そこへ
5. 初回はログイン画面が出る（`require_passcode` のとき）。合言葉を入れる。cookie は 24 時間
6. ホーム画面に追加すると PWA として開ける（HTTPS なので可）

やめるとき: `tailscale serve --bg 8789 off`（`tailscale serve reset` で全部消す）。
**`tailscale funnel` は使わない**（インターネットへ公開する機能。設計で禁じている）。

## 手順（A）

```
uv run manor web serve --host <tailnet IP> --port 8789
```

`[web] passcode` が無いと起動を拒む。スマホからは `http://<tailnet IP>:8789/`。
**`--host 0.0.0.0` にはしない**（同じ LAN の全端末から届く）。

## 常時起動（PC のログオン時に立ち上げる）

```
uv run manor web install --at boot --host 127.0.0.1 --port 8789         # 表示だけ
uv run manor web install --at boot --host 127.0.0.1 --port 8789 --yes   # 実際に登録（schtasks ONLOGON）
uv run manor web status / uninstall --yes
```

`tailscale serve --bg` は Tailscale 側が保持するので、PC を再起動しても転送は残る。

## passcode の確認

- 設定されているかは **Web アプリの「設定」画面**か `GET /api/v1/settings` の `web.has_passcode`
  で分かる。**値そのものは表示しない**（書くだけ）。忘れたら `home/config.toml` を開いて確かめるか、書き換える
- 間違いは 1 分に 5 回まで。超えると 429（1 分待つ）

## 確かめ方

- スマホ側で「同期中」の緑の印が出れば `meta` のポーリングが通っている
- PC 側で `uv run manor web serve` のログに `GET /api/v1/meta` が 100.x.x.x（A）か 127.0.0.1（B）から来る
- 家族の端末を tailnet に入れる日が来たら、`require_passcode = true` が二つ目の錠になる
