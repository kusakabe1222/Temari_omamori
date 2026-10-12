"""ダミーデータを作るスクリプト（本物の利用者の情報は使わない）。

使い方（プロジェクトのフォルダで）:
    .\\.venv\\Scripts\\python.exe scripts\\seed.py             # DB が空のときだけ作る
    .\\.venv\\Scripts\\python.exe scripts\\seed.py --days 30   # 30日分作る（初期値は14日分）
    .\\.venv\\Scripts\\python.exe scripts\\seed.py --reset     # 今のデータを全部消してから作る（yes と入力したときだけ消す）

ダミーの端末 ID は「dummy-」で始める（本物の端末のデータと混ざらないように）。
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db, services  # noqa: E402
from app.config import load_config  # noqa: E402

# (ニックネーム, 開始前の1日平均歩数, 期間の終わりまでに +1500歩のどれだけ伸びるか, 特徴)
DUMMY_USERS = [
    ("さくら", 3200, 1.1, None),
    ("まつ", 4500, 0.9, None),
    ("うめ", 2600, 0.8, "stale"),      # 30時間以上同期していない → 未同期アラートの例
    ("たけ", 5200, 1.0, "overload"),   # 今日歩きすぎ → 過負荷アラートの例
    ("もみじ", 3800, 0.6, None),
]
SLOT_MINUTES = 30            # 30分ごとの歩数を1件にする
WALK_HOURS = (7, 19)         # 7時〜19時に歩く
SYNC_HOURS = (12, 19)        # 1日2回同期する
FAIL_RATE = 0.06             # 同期が失敗する割合
STALE_HOURS = 30
# settings は残す。順番は、ほかの表から参照されている表があとになるように並べる
TABLES_TO_CLEAR = ["line_events", "gacha_draws", "line_links", "line_link_codes",
                   "sync_logs", "step_records", "devices", "users"]


def slot_weights() -> list[tuple[int, float]]:
    """(0時からの分, 歩きやすさ)。10時ごろと15時ごろに山がある。"""
    result = []
    minutes = WALK_HOURS[0] * 60 + SLOT_MINUTES
    while minutes <= WALK_HOURS[1] * 60:
        hour = minutes / 60
        weight = 0.4 + math.exp(-((hour - 10) ** 2) / 2) + 0.8 * math.exp(-((hour - 15) ** 2) / 2)
        result.append((minutes, weight))
        minutes += SLOT_MINUTES
    return result


def at(day, minutes: int) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=db.JST) + timedelta(minutes=minutes)


def make_records(rng: random.Random, baseline: int, lift: float, kind: str | None,
                 days: int, now: datetime) -> list[tuple[datetime, int]]:
    weights = slot_weights()
    total_weight = sum(w for _, w in weights)
    records = []
    for back in range(days - 1, -1, -1):
        day = now.date() - timedelta(days=back)
        progress = (days - 1 - back) / max(days - 1, 1)
        daily_total = (baseline + services.GOAL_INCREMENT * lift * progress) * rng.uniform(0.8, 1.2)
        denominator = total_weight
        if back == 0 and kind == "overload":
            # 今日はここまでで目標の 180% 歩いたことにする（最後の同期より後の分は届かないので、少し多めにしておく）
            daily_total = services.goal_for(baseline) * 1.8
            denominator = sum(w for m, w in weights if at(day, m) <= now) or total_weight
        for minutes, weight in weights:
            measured = at(day, minutes)
            if measured > now:
                continue
            if kind == "stale" and measured > now - timedelta(hours=STALE_HOURS):
                continue
            records.append((measured, int(daily_total * weight / denominator * rng.uniform(0.75, 1.25))))
    return records


def make_syncs(rng: random.Random, records: list[tuple[datetime, int]], kind: str | None,
               days: int, now: datetime) -> tuple[list[tuple[datetime, int, datetime]], list[dict]]:
    """同期の時刻ごとに、それまでに測った記録をまとめて受け取ったことにする。"""
    times = []
    for back in range(days - 1, -1, -1):
        day = now.date() - timedelta(days=back)
        times += [at(day, hour * 60 + rng.randint(0, 20)) for hour in SYNC_HOURS]
    times.append(now - timedelta(minutes=rng.randint(2, 15)))
    limit = now - timedelta(hours=STALE_HOURS) if kind == "stale" else now
    times = sorted(t for t in times if t <= limit)

    received, logs, pending = [], [], sorted(records)
    for sync_time in times:
        batch = [r for r in pending if r[0] <= sync_time]
        duration = rng.uniform(1.5, 6.0) + len(batch) * 0.02
        log = {"started_at": sync_time - timedelta(seconds=duration), "finished_at": sync_time,
               "rssi": rng.randint(-88, -52), "received_at": sync_time}
        if rng.random() < FAIL_RATE:
            logs.append({**log, "status": "failed", "count": 0, "error": "接続が途中で切れました（ダミー）"})
            continue  # 受け取れなかった分は次の同期に回る
        pending = [r for r in pending if r[0] > sync_time]
        received += [(measured, steps, sync_time) for measured, steps in batch]
        logs.append({**log, "status": "ok", "count": len(batch), "error": None})
    return received, logs  # 最後の同期より後に測った分は、まだ届いていないので入れない


def clear_data(conn) -> bool:
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES_TO_CLEAR}
    print("次のデータをすべて消します（設定は残します）:")
    print(f"  利用者 {counts['users']}人 / 端末 {counts['devices']}台 / 歩数の記録 {counts['step_records']}件 / "
          f"同期の記録 {counts['sync_logs']}件 / ガチャの記録 {counts['gacha_draws']}件 / "
          f"LINE の登録 {counts['line_links']}件")
    try:
        answer = input("本当に消してよければ yes と入力してください: ")
    except EOFError:
        answer = ""
    if answer.strip() != "yes":
        print("やめました。何も消していません。")
        return False
    for table in TABLES_TO_CLEAR:
        conn.execute(f"DELETE FROM {table}")
    conn.execute(f"DELETE FROM sqlite_sequence WHERE name IN ({','.join('?' * len(TABLES_TO_CLEAR))})", TABLES_TO_CLEAR)
    conn.commit()
    print("消しました。")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="ダミーデータを作る")
    parser.add_argument("--days", type=int, default=14, help="何日分作るか（初期値 14）")
    parser.add_argument("--reset", action="store_true", help="今のデータを消してから作る（確認あり）")
    args = parser.parse_args()
    if not 1 <= args.days <= 120:
        parser.error("--days は 1〜120 にしてください")

    config = load_config()
    db.init_db(config.db_path)
    conn = db.connect(config.db_path)
    try:
        has_data = conn.execute("SELECT EXISTS (SELECT 1 FROM users) OR EXISTS (SELECT 1 FROM devices)").fetchone()[0]
        if has_data:
            if not args.reset:
                print("DB にはすでにデータがあるので、何もしませんでした。作り直すときは --reset を付けてください。")
                return 1
            if not clear_data(conn):
                return 1

        rng = random.Random(20261009)  # 毎回同じ形のデータになるように固定
        now = db.now_jst()
        for number, (name, baseline, lift, kind) in enumerate(DUMMY_USERS, start=1):
            user_id = services.create_user(conn, name, baseline, now - timedelta(days=args.days))
            device_id = f"dummy-{number:02d}"
            records = make_records(rng, baseline, lift, kind, args.days, now)
            received, logs = make_syncs(rng, records, kind, args.days, now)
            ok_times = [log["finished_at"] for log in logs if log["status"] == "ok"]
            conn.execute(
                "INSERT INTO devices (device_id, user_id, last_seen_at) VALUES (?, ?, ?)",
                (device_id, user_id, db.to_jst_text(max(ok_times)) if ok_times else None),
            )
            conn.executemany(
                "INSERT INTO step_records (device_id, user_id, measured_at, steps, received_at) VALUES (?, ?, ?, ?, ?)",
                [(device_id, user_id, db.to_jst_text(m), s, db.to_jst_text(r)) for m, s, r in received],
            )
            conn.executemany(
                """INSERT INTO sync_logs (device_id, source, status, started_at, finished_at, record_count,
                                          inserted_count, skipped_count, rssi, note, error, received_at)
                   VALUES (?, 'seed', ?, ?, ?, ?, ?, 0, ?, 'ダミー', ?, ?)""",
                [(device_id, log["status"], db.to_jst_text_ms(log["started_at"]), db.to_jst_text_ms(log["finished_at"]),
                  log["count"], log["count"], log["rssi"], log["error"], db.to_jst_text(log["received_at"]))
                 for log in logs],
            )
            conn.commit()
            print(f"  {name}さん（{device_id}）: 歩数の記録 {len(received)}件、同期の記録 {len(logs)}件")
        print(f"ダミーデータを作りました（{args.days}日分）。DB: {config.db_path}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
