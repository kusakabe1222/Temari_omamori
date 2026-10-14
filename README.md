# お守り型歩数計 管理者用サイト（PD実践 CCCA-G1）

高齢者向け「お守り型歩数計」の試作システムです。施設の職員が使う**管理者用サイト**と、本人や家族が LINE で歩数・ポイント・ガチャを見る**本人用 LINE Bot** があります。
大学の授業の試作なので、本番運用はしません。**本物の利用者の名前や個人情報は入れず、ニックネームとダミーデータで使ってください。**

> 全体の起動のしかたと、中で何が起きているかは **[docs/起動と動作のしくみ.md](docs/起動と動作のしくみ.md)** にまとめています。

## データの流れ

```
お守り端末（M5StickS3） ──BLE──> PC（scripts/ble_gateway.py） ──HTTP──> 管理者用サイト（app.main・ポート 8000）
                                                                              │
                                                                         DB（SQLite）
                                                                              │
本人・家族の LINE ──> LINE のサーバー ──> トンネル ──> LINE の入口（app.bot_main・ポート 8001）
```

- **PC直結**: タブレットの代わりに、サーバーと同じ PC が BLE で端末から歩数を受け取ります。
  サーバーの入口（`POST /api/sync`）は同じなので、あとからタブレット経由に戻すこともできます。
- **LINE の入口は別のサーバー**です。インターネット（トンネル）に出すのはこちら（8001）だけで、管理者用サイト（8000）は PC の中だけで使います。
- BLE の決まりは [docs/ble_protocol.md](docs/ble_protocol.md)。端末（M5StickS3）側のプログラムは [firmware/](firmware/README.md)。
- 端末がまだないときは `scripts/fake_tablet.py`（にせタブレット）、LINE がまだないときは `scripts/fake_line.py`（にせ LINE）で試せます。

## フォルダの中身

| 場所 | 中身 |
|---|---|
| `app/main.py` | 管理者用サイトの本体（起動・ログインの確認） |
| `app/bot_main.py` | LINE の入口（別のサーバー。`/line/webhook` だけ） |
| `app/schema.sql` | テーブル定義 |
| `app/db.py` | DB への接続・設定値 |
| `app/services.py` | 歩数の保存・集計・アラート判定 |
| `app/points.py`・`app/gacha.py` | ポイントの計算、ガチャ |
| `app/line_bot.py` | LINE で届いたことばへの返事 |
| `app/line_links.py` | LINE の登録（番号・つながり） |
| `app/line_api.py` | LINE との通信（署名の確認と返事の送信。外部と通信するのはここだけ） |
| `app/api.py` | API（`/api/...`） |
| `app/pages.py`・`app/templates/` | 画面 |
| `app/export.py` | CSV |
| `app/static/` | CSS・JavaScript・Chart.js（`vendor/`、MIT ライセンス） |
| `scripts/seed.py` | ダミーデータを作る |
| `scripts/fake_tablet.py` | にせタブレット（API にテストデータを送る） |
| `scripts/fake_line.py` | にせ LINE（LINE の入口にテストのことばを送る） |
| `scripts/ble_gateway.py` | PC直結の BLE 中継 |
| `firmware/omamori_m5/` | お守り端末（M5StickS3）のプログラム（Arduino）。コンパイルは確認ずみ、**実機確認はまだ** |
| `docs/ble_protocol.md` | 端末と PC の BLE 通信の決まり |
| `tests/` | 自動テスト |
| `docs/review/` | レビュー用メモ（授業のルール） |

---

## 準備（最初に1回）

PowerShell で、このフォルダ（`PD実践`）を開いて実行します。Python 3.11 以上が必要です。

```powershell
python -m venv .venv
```

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

`.env` を作ります（パスワードやキーを入れるファイル。Git やチャットに載せないこと）。
**`.env` がもうあるときは、コピーしません**（上書きすると、入れた値が消えるため）。

```powershell
if (Test-Path .env) { "もう .env があるので、コピーしませんでした" } else { Copy-Item .env.example .env; ".env を作りました。中に値を入れてください" }
```

次のコマンドで出てくるランダムな文字列を、`.env` の `SYNC_API_KEY` と `SESSION_SECRET` に1つずつ入れます（2回実行）。
`ADMIN_PASSWORD` には、チームで決めたパスワードを入れます。

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
```

> コマンドは `.\.venv\Scripts\python.exe` で書いています。`Activate.ps1` で仮想環境を有効にしなくても動きます。

---

## 段階1: DB と API

### ダミーデータを作る

```powershell
.\.venv\Scripts\python.exe scripts\seed.py
```

5人分（さくら・まつ・うめ・たけ・もみじ）、14日分の歩数と同期記録ができます。端末 ID は `dummy-01`〜`dummy-05`。
「うめ」さんは30時間以上同期していない（未同期アラート）、「たけ」さんは今日歩きすぎ（過負荷アラート）の例です。

- DB にデータがあるときは何もしません。
- 作り直すときは `--reset` を付けます。**今のデータが全部消えます。** `yes` と入力したときだけ消します（設定は残ります）。

### サーバーを起動する

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

ブラウザで http://127.0.0.1:8000 を開き、`.env` の `ADMIN_PASSWORD` でログインします。止めるときは Ctrl+C。

- この起動方法では、**この PC からしか開けません**（安全のため）。タブレットなど別の機器から画面を見たいときは、
  `--host 0.0.0.0` を付けて起動し、Windows のファイアウォールで許可します。そのときは学内のネットワークだけで使ってください。

### 動作確認

```powershell
Invoke-RestMethod http://127.0.0.1:8000/healthz
```

`status : ok` が出れば動いています。API キーを付けると、ログインなしで API を読めます。

```powershell
$key = (Select-String -Path .env -Pattern '^SYNC_API_KEY=(.*)$').Matches[0].Groups[1].Value
Invoke-RestMethod http://127.0.0.1:8000/api/summary -Headers @{ "X-API-Key" = $key } | ConvertTo-Json -Depth 5
```

| API | 内容 |
|---|---|
| `POST /api/sync` | 中継から歩数をまとめて受け取る（`X-API-Key` が必要）。返り値は受け取った件数・新規・重複でスキップした件数 |
| `GET /api/users` | 利用者ごとの今日の歩数・達成率・アラート |
| `GET /api/users/{id}/daily?from=2026-10-01&to=2026-10-14` | 日別歩数（省くと今日までの14日間）。記録のない日は `steps: null` |
| `GET /api/summary` | 今日の歩数・目標達成率・アラートの一覧 |
| `GET /api/export.csv?type=daily` | CSV（`type` は `daily`・`steps`・`sync`、`from`・`to` で期間） |

`POST /api/sync` の形:

```json
{
  "device_id": "omamori-01",
  "rssi": -62,
  "started_at": "2026-10-16T12:00:00+09:00",
  "finished_at": "2026-10-16T12:00:04+09:00",
  "records": [{"measured_at": "2026-10-16T09:00:00+09:00", "steps": 120}],
  "source": "pc",
  "note": "距離3m"
}
```

- `steps` は**その時間帯に歩いた歩数**（累計ではない）。日別歩数は日本時間の日付ごとの合計です。
- 同じ `device_id` と `measured_at` の記録は二重に数えません（タイムゾーンの書き方が違っても同じ時刻なら重複）。
- 登録していない端末からのデータも保存し、端末は「持ち主なし」で登録されます。画面で持ち主を決めると、それまでのデータもその人の記録になります。
- 端末の時計がおかしい記録（2024年より前・今より10分以上先）は保存せず、`invalid` として数えます。
- 中継側で BLE 同期に失敗したときは `"status": "failed"` と `"error"` を送ると、検証用に記録が残ります。

## 段階2: にせタブレット

サーバーを起動したまま、別の PowerShell で実行します。

```powershell
.\.venv\Scripts\python.exe scripts\fake_tablet.py
```

`fake-01` の直近1時間分（10分ごと6件）を送ります。確かめること:

1. `受け取り 6件 → 新規 6件 / 重複でスキップ 0件` と出る
2. 画面の「登録・編集」に `fake-01` が「持ち主なし」で出る → 持ち主を選んで保存 → 一覧でその人の歩数が増える
3. 同じデータを2回送ると、2回目は重複でスキップされる

```powershell
.\.venv\Scripts\python.exe scripts\fake_tablet.py --repeat
```

4. 歩数を多くして送ると、過負荷アラートが出る

```powershell
.\.venv\Scripts\python.exe scripts\fake_tablet.py --steps 3000
```

5. 失敗した同期を送ると、「同期記録」に失敗が出る

```powershell
.\.venv\Scripts\python.exe scripts\fake_tablet.py --fail
```

## 段階3: 画面

| 画面 | 場所 | 中身 |
|---|---|---|
| 利用者一覧 | `/` | 名前・今日の歩数・目標達成率・アラート・最終同期。1分ごとに自動で更新 |
| 個人ページ | `/users/{id}` | 日別歩数のグラフ（目標の線・過負荷の目安の線つき）、7・14・30日の切り替え、直近の同期、ポイント・ガチャ、LINE の登録の番号 |
| 登録・編集 | `/manage` | 利用者（ニックネーム・開始前の平均歩数）と端末の登録・編集・削除 |
| 同期記録 | `/sync-logs` | BLE 検証用。端末ごとの成功率・RSSI・同期時間 |
| 設定 | `/settings` | 過負荷アラート・未同期アラートのしきい値、ポイント・ガチャのルール |
| CSV | `/export` | 日別歩数（ポイントつき）・細かい記録・同期記録・ガチャの CSV（Excel で文字化けしない形） |

- 目標 ＝ 開始前の1日平均歩数 ＋ 1500歩（自動で計算）。
- **アラートのしきい値は仮の値**です（過負荷: 目標の150%以上、未同期: 24時間）。チームと施設で決めたら設定画面で変えてください。
- 利用者を削除すると、その人の歩数の記録・ポイントとガチャの記録・LINE の登録も消えます（確認のダイアログが出ます）。記録のある端末は削除できません。

## 段階4: PC直結の BLE 中継

端末側のプログラム（[firmware/README.md](firmware/README.md)）を M5StickS3 に書き込んでから使います。
通信の決まりは [docs/ble_protocol.md](docs/ble_protocol.md)。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-ble.txt
```

近くの BLE 機器と電波の強さ（RSSI）を見る:

```powershell
.\.venv\Scripts\python.exe scripts\ble_gateway.py --scan
```

1回だけ同期する（サーバーを起動しておくこと）:

```powershell
.\.venv\Scripts\python.exe scripts\ble_gateway.py --once
```

5分ごとに同期し続ける（Ctrl+C で止める）:

```powershell
.\.venv\Scripts\python.exe scripts\ble_gateway.py
```

距離の検証のときは、メモと「いるはずの端末」を付けます。見つからなかったときも失敗として「同期記録」に残ります。

```powershell
.\.venv\Scripts\python.exe scripts\ble_gateway.py --once --expect omamori-01 --note "距離3m"
```

## 段階5: LINE（本人用）

本人や家族が、LINE で自分の歩数・ポイント・ガチャを見られます。
LINE に送るのは**ニックネーム・歩数・ポイント・ガチャのメッセージだけ**です。

### 送ることばと返事

| 送ることば | 返事 |
|---|---|
| `登録 123456` | 番号で登録する（番号は職員が個人ページで作る。家族も同じ番号で登録できる） |
| `今日` | きょうの歩数・目標まであと何歩・きょうのポイント・記録が届いた時刻 |
| `1週間`・`1か月` | 日ごとの歩数、目標を達成した日数、いちばん歩いた日 |
| `ポイント` | いま使えるポイント、あと何回ガチャを回せるか、ポイントのもらいかた |
| `ガチャ` | ポイントを使ってメッセージガチャ（★☆☆ 60%・★★☆ 30%・★★★ 10%） |
| `目標` | 1日の目標と、ポイントのルール |
| `解除` | この LINE の登録をやめる |

返事の下にボタン（今日・1週間・1か月・ポイント・ガチャ・目標）が出るので、文字を打たなくても使えます。全角の数字でも大丈夫です。

### ポイントのルール（設定画面で変えられる）

- **1,000歩ごとに 1pt**、その日の**目標を達成したら +2pt**、**ガチャ1回 10pt**
- **過負荷アラートのしきい値（目標の150%）を超えた分の歩数は、ポイントになりません**（歩きすぎをすすめないため）
- ポイントは歩数の記録から毎回計算します。届いた歩数はすぐ反映され、ルールを変えると、これまでの分もふくめて計算し直します

### まず LINE なしで試す（にせ LINE）

1. `.env` に、試し用の `LINE_CHANNEL_SECRET`（下のコマンドで作った文字列）と `LINE_DRY_RUN=1` を入れる

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(16))"
```

2. 管理者用サイト（8000）を起動し、利用者の個人ページで「登録の番号を作る」を押す
3. LINE の入口（8001）を起動する（試し運転なので、LINE には何も送りません）

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.bot_main:app --port 8001
```

4. 別の PowerShell で、にせ LINE から送る（返事がそのまま表示されます）

```powershell
.\.venv\Scripts\python.exe scripts\fake_line.py "登録 123456"
```

```powershell
.\.venv\Scripts\python.exe scripts\fake_line.py 今日
```

```powershell
.\.venv\Scripts\python.exe scripts\fake_line.py ガチャ
```

### 本物の LINE につなぐ

1. [LINE Developers](https://developers.line.biz/) で Messaging API のチャネルを用意する（前の omamori-bot のチャネルを使ってもよい）
   - 「チャネル基本設定」の**チャネルシークレット** → `.env` の `LINE_CHANNEL_SECRET`
   - 「Messaging API設定」の**チャネルアクセストークン（長期）** → `.env` の `LINE_CHANNEL_ACCESS_TOKEN`
   - `LINE_DRY_RUN` は空にする
2. LINE の入口（8001）を起動する（上の「3.」と同じコマンド）
3. **トンネルで 8001 だけ**をインターネットに出す（どちらか1つ。ツールは各自で入れる）
   - Cloudflare Tunnel（アカウントなしで試せる）: `cloudflared tunnel --url http://127.0.0.1:8001` → `https://〇〇.trycloudflare.com` が出る
   - ngrok（アカウントが必要）: `ngrok http 8001`
4. LINE Developers の「Messaging API設定」で、Webhook URL に `https://〇〇/line/webhook` を入れて「検証」→ 成功したら「Webhookの利用」をオン
5. LINE Official Account Manager の「応答設定」で「応答メッセージ」をオフにする（自動の返事と重ならないように）
6. 個人ページで作った番号を本人・家族に渡し、「友だち追加して『登録 番号』と送ってください」と伝える
   （友だち追加の QR コードは、LINE Developers の「Messaging API設定」にあります）

### 気をつけること

- **トンネルに出すのは 8001 だけ**。8000（管理者用サイト）は出さない。8001 にあるのは `/line/webhook` と `/healthz` だけで、LINE の署名がないものは受けつけません
- Cloudflare の試し用トンネルは、起動するたびに URL が変わります → Webhook URL も入れ直す
- DB に LINE のユーザーID（U で始まる）が入ります。プロフィール（名前・写真）は取りません。ブロックされたら、その LINE の登録は消します
- 返事（応答メッセージ）は、無料プランの送信数に数えられません。こちらから送るプッシュメッセージは使っていません
- 番号を5回まちがえた LINE からは、1時間受けつけません

## テスト

```powershell
.\.venv\Scripts\python.exe -m pytest
```

テストは一時フォルダに別の DB を作って動くので、`data/omamori.db` のデータには触りません。LINE にも実際には送りません（LINE の入口は試し運転で動かします）。

## 困ったとき

- **`ADMIN_PASSWORD, SYNC_API_KEY が設定されていません`** → 「準備」の `.env` を作ってください。
- **`LINE_CHANNEL_SECRET が設定されていません`**（LINE の入口） → 「段階5」の `.env` の値を入れてください。
- **ポート 8000 が使われている** → `--port 8002` を付けて起動し、ブラウザも http://127.0.0.1:8002 にする。にせタブレットと BLE 中継には `--server http://127.0.0.1:8002` を付ける（8001 は LINE の入口が使う）。
- **`API キーが違います`**（にせタブレット・BLE 中継） → `.env` の `SYNC_API_KEY` を確かめて、サーバーを起動し直す。
- **DB を最初からにしたい** → `scripts\seed.py --reset`（全データが消えます。`yes` と入力したときだけ）。
