# お守り型歩数計 管理者用サイト（PD実践 CCCA-G1）

## このプロジェクトについて
- 高齢者向け「お守り型歩数計」の試作システムのうち、施設職員が使う管理者用サイトを作る。
- データの流れ: お守り型端末（M5StickS3）→ BLE → **施設のPC（`scripts/ble_gateway.py`）** → HTTPでこのサーバーへ送信 → DBに保存 → 管理者用サイトで表示。
  - 2026-10-09 にタブレット経由から PC 直結に変更。サーバーの入口（`POST /api/sync`）は同じなので、タブレット経由にも戻せる。
  - BLE の決まりは `docs/ble_protocol.md`（仮の案。端末担当とそろえる）。
- 大学の授業（プロジェクトデザイン実践）の試作。本番運用はしない。学内でのデモと検証に使う。
- 分担: DB＝林、バックエンド＝日下部、フロントエンド＝森脇（担当はチームで確認中）。

## 技術構成（変更するときは先に相談すること）
- Python 3.11以上、FastAPI、SQLite（標準の sqlite3。テーブル定義は `app/schema.sql`）。
- 画面はサーバー側のHTMLテンプレート（Jinja2）＋素のJavaScript。グラフは Chart.js を `static/vendor/` に置いて使う（CDNに頼らない）。
- PC 直結の BLE は bleak（`requirements-ble.txt`）。LINE への送信は LINE Messaging API（標準の urllib で送る）。
- 開発PCは Windows（PowerShell）。コマンドは PowerShell で動く形で書く。
- 依存ライブラリは最小限にし、`requirements.txt` に版を固定して書く。

## データベース（`app/schema.sql` が正。下は要点）
- `users`（利用者）: id, display_name（ニックネーム）, baseline_steps（開始前の1日平均歩数）, goal_steps（= baseline_steps + 1500）, created_at
- `devices`（端末）: id, device_id（BLEで送られてくるID）, user_id, last_seen_at
- `step_records`（歩数）: id, device_id, user_id（受信したときの持ち主）, measured_at（端末側の時刻）, steps, received_at
  - `UNIQUE(device_id, measured_at)` で、同じデータが2回届いても二重に数えない
  - steps は「その時間帯に歩いた歩数」（累計ではない。仮）。日時は日本時間の ISO 8601 文字列で保存
- `sync_logs`（同期の記録）: device_id, source, status, started_at, finished_at, record_count, inserted_count, skipped_count, rssi, note, error, received_at
  - BLE同期の検証（距離・成功率・同期時間）に使うので必ず残す。失敗した同期も残す
- `settings`: 過負荷アラートのしきい値など。しきい値の値はチームと施設で決めるので、仮の値を入れて画面から変えられるようにする
- `line_logs`: LINE に送った記録

## API
- `POST /api/sync`: 中継（PC・タブレット）から歩数をまとめて受け取る。`X-API-Key` ヘッダーが必要
  - 例: `{"device_id": "omamori-01", "rssi": -62, "started_at": "...", "finished_at": "...", "records": [{"measured_at": "2026-10-16T09:00:00+09:00", "steps": 120}]}`
  - 返り値: 受け取った件数と、重複でスキップした件数
- `GET /api/users`, `GET /api/users/{id}/daily?from=&to=`: 日別歩数
- `GET /api/summary`: 今日の歩数・目標達成率・アラートの一覧
- `GET /api/export.csv`: 歩数と同期記録のCSV（検証の分析とレポート用）

## 画面（日本語、タブレットとPCの両方で見やすく）
1. 利用者一覧: 名前、今日の歩数、目標達成率、アラートの有無
2. 個人ページ: 日別歩数のグラフ（目標の線つき）、直近の同期日時
3. 利用者・端末の登録と編集
4. 設定: 過負荷アラートのしきい値、LINE 連携
5. CSVエクスポート
- 同期記録（BLE 検証用）
- ログインは共通パスワード1つでよい（`.env` に置く。リポジトリには入れない）。

## 作る順番
1. DBとAPI。ダミーデータを作るスクリプト `scripts/seed.py` も作る
2. タブレットの代わりにAPIへ送る「にせタブレット」スクリプト `scripts/fake_tablet.py`（BLEが完成する前にテストするため）
3. 画面（一覧 → 個人ページ → 登録 → 設定 → CSV）
4. 本物の端末とつなぐ（PC 直結: `scripts/ble_gateway.py`）

各段階で、起動方法と動作確認の手順を README に追記すること。

## 守ること
- 説明は日本語で行う。大きな変更の前に、何をどう変えるかを先に説明する。
- **授業のルール: 生成AIが作ったコードは、実行する前にチーム全員でレビューし、結果を担当教員（佐野先生）に報告する。** そのため、変更のたびに `docs/review/` にレビュー用メモを書く（変更したファイル、何をするコードか、動かし方、ファイル削除や外部への通信など注意が必要な処理の有無）。
- **生成AIで作った画像は使わない**（授業のルール）。アイコンが必要なときは文字か、ライセンスが明確な素材にする。
- 本物の利用者の名前や個人情報は使わない。テストはニックネームとダミーデータで行う。LINE に送るのもニックネームと歩数だけ。
- パスワードやキーはコードに直接書かない。
- データを消す操作（DBの初期化など）は、実行前に必ず確認を取る。
