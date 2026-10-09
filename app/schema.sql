-- お守り型歩数計 管理者用サイトのテーブル定義（SQLite）
--
-- 日時はすべて日本時間の ISO 8601 文字列で保存する（例: 2026-10-16T09:00:00+09:00）。
-- 形をそろえているので、文字列の大小で前後を比べられ、先頭10文字がそのまま日付になる。

CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name   TEXT    NOT NULL,                             -- ニックネーム（本名は入れない）
    baseline_steps INTEGER NOT NULL CHECK (baseline_steps >= 0), -- 開始前の1日平均歩数
    goal_steps     INTEGER NOT NULL CHECK (goal_steps >= 0),     -- = baseline_steps + 1500（アプリ側で計算して保存）
    created_at     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id    TEXT    NOT NULL UNIQUE,                         -- BLE で名乗る ID（例: omamori-01）
    user_id      INTEGER REFERENCES users(id) ON DELETE SET NULL, -- NULL = まだ誰にも割り当てていない
    last_seen_at TEXT                                             -- 最後に同期が成功した日時
);

CREATE TABLE IF NOT EXISTS step_records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   TEXT    NOT NULL REFERENCES devices(device_id) ON UPDATE CASCADE,
    user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,  -- 受信したときの持ち主。端末を付け替えても過去の記録は動かない
    measured_at TEXT    NOT NULL,                                -- 端末側の時刻（その時間帯の終わり）
    steps       INTEGER NOT NULL CHECK (steps >= 0),             -- その時間帯に歩いた歩数（累計ではない。仮の決め方）
    received_at TEXT    NOT NULL,                                -- サーバーが受け取った日時
    UNIQUE (device_id, measured_at)                              -- 同じデータが2回届いても二重に数えない
);
CREATE INDEX IF NOT EXISTS idx_step_records_user_time ON step_records (user_id, measured_at);

-- BLE 同期の検証（距離・成功率・同期時間）に使う。失敗した同期も残す。
CREATE TABLE IF NOT EXISTS sync_logs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id      TEXT    NOT NULL,
    source         TEXT    NOT NULL DEFAULT 'unknown',  -- 送ってきたもの: pc / tablet / fake / seed
    status         TEXT    NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'failed')),
    started_at     TEXT,                                -- BLE 接続を始めた日時
    finished_at    TEXT,                                -- BLE で受け取り終わった日時
    record_count   INTEGER NOT NULL DEFAULT 0,          -- 送られてきた件数
    inserted_count INTEGER NOT NULL DEFAULT 0,          -- 新しく保存した件数
    skipped_count  INTEGER NOT NULL DEFAULT 0,          -- 保存しなかった件数（重複・時刻がおかしいもの）
    rssi           INTEGER,                             -- 電波の強さ（dBm）
    note           TEXT,                                -- 検証のメモ（例: 距離3m・壁1枚）
    error          TEXT,                                -- 失敗や注意の内容
    received_at    TEXT    NOT NULL                     -- サーバーが受け取った日時
);
CREATE INDEX IF NOT EXISTS idx_sync_logs_device ON sync_logs (device_id, received_at);

-- 過負荷アラートのしきい値など。値はチームと施設で決めるので、画面から変えられる。
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- LINE に送った記録。同じアラートを1日に何度も送らないためにも使う。
CREATE TABLE IF NOT EXISTS line_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT    NOT NULL,   -- alert / summary / test
    user_id    INTEGER,            -- アラートの対象（まとめ・テストは NULL）
    alert_date TEXT,               -- アラートの日付
    ok         INTEGER NOT NULL,   -- 1 = 送れた
    error      TEXT,
    sent_at    TEXT    NOT NULL
);
