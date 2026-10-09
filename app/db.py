"""SQLite への接続、テーブルの作成、設定値の読み書き。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Windows には標準でタイムゾーンのデータがないので、日本時間は +9時間で固定して扱う
JST = timezone(timedelta(hours=9), "JST")
SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# 設定の初期値（仮の値。チームと施設で決めたら設定画面で変える）
DEFAULT_SETTINGS: dict[str, int] = {
    "overload_percent": 150,  # 今日の歩数が目標の何%以上で過負荷アラートを出すか
    "no_sync_hours": 24,      # 何時間データが届かなければ未同期アラートを出すか（0 = 出さない）
    "line_alert_enabled": 0,  # 1 = 過負荷アラートを LINE に自動で送る
}


def now_jst() -> datetime:
    return datetime.now(JST).replace(microsecond=0)


def as_jst(value: datetime) -> datetime:
    """日本時間の日時にそろえる。タイムゾーンが書かれていない日時は日本時間とみなす。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=JST)
    return value.astimezone(JST).replace(microsecond=0)


def to_jst_text(value: datetime) -> str:
    """保存用の文字列にする（例: 2026-10-16T09:00:00+09:00）。"""
    return as_jst(value).isoformat()


def to_jst_text_ms(value: datetime) -> str:
    """同期の開始・終了の時刻用。同期時間を測るので、ミリ秒まで残す（例: 2026-10-16T12:00:03.250+09:00）。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=JST)
    return value.astimezone(JST).isoformat(timespec="milliseconds")


def connect(db_path: Path) -> sqlite3.Connection:
    # FastAPI は1つのリクエストの中でもスレッドを変えることがあるので check_same_thread=False にする。
    # 接続はリクエストごとに作って閉じるので、同じ接続を同時に使うことはない。
    conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path) -> None:
    """テーブルがなければ作り、設定の初期値を入れる。今あるデータは消さない。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")  # 書き込み中でも画面から読めるようにする
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        now = to_jst_text(now_jst())
        conn.executemany(
            "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            [(key, str(value), now) for key, value in DEFAULT_SETTINGS.items()],
        )
        conn.commit()
    finally:
        conn.close()


def get_settings(conn: sqlite3.Connection) -> dict[str, int]:
    values = dict(DEFAULT_SETTINGS)
    for row in conn.execute("SELECT key, value FROM settings"):
        if row["key"] in values:
            values[row["key"]] = int(row["value"])
    return values


def save_settings(conn: sqlite3.Connection, values: dict[str, int]) -> None:
    now = to_jst_text(now_jst())
    conn.executemany(
        """INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
           ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
        [(key, str(value), now) for key, value in values.items() if key in DEFAULT_SETTINGS],
    )
    conn.commit()
