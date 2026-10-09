"""データの登録・集計・アラート判定。画面と API の両方から使う。"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta

from . import db
from .schemas import SyncIn

GOAL_INCREMENT = 1500  # 目標 = 開始前の1日平均歩数 + 1500歩

# 端末の時計がおかしいデータは保存しない（この範囲の外は「時刻エラー」として数える）
OLDEST_VALID = datetime(2024, 1, 1, tzinfo=db.JST)
FUTURE_MARGIN = timedelta(minutes=10)


def goal_for(baseline_steps: int) -> int:
    return baseline_steps + GOAL_INCREMENT


# ---------------------------------------------------------------- 利用者・端末

def create_user(conn: sqlite3.Connection, display_name: str, baseline_steps: int, now: datetime) -> int:
    cur = conn.execute(
        "INSERT INTO users (display_name, baseline_steps, goal_steps, created_at) VALUES (?, ?, ?, ?)",
        (display_name, baseline_steps, goal_for(baseline_steps), db.to_jst_text(now)),
    )
    conn.commit()
    return cur.lastrowid


def update_user(conn: sqlite3.Connection, user_id: int, display_name: str, baseline_steps: int) -> bool:
    cur = conn.execute(
        "UPDATE users SET display_name = ?, baseline_steps = ?, goal_steps = ? WHERE id = ?",
        (display_name, baseline_steps, goal_for(baseline_steps), user_id),
    )
    conn.commit()
    return cur.rowcount == 1


def delete_user(conn: sqlite3.Connection, user_id: int) -> bool:
    """利用者を消す。この人の歩数の記録も消え、端末は「持ち主なし」に戻る。"""
    cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    return cur.rowcount == 1


def create_device(conn: sqlite3.Connection, device_id: str, user_id: int | None) -> bool:
    """端末を登録する。同じ ID がすでにあれば False。"""
    cur = conn.execute("INSERT OR IGNORE INTO devices (device_id) VALUES (?)", (device_id,))
    conn.commit()
    if cur.rowcount == 0:
        return False
    if user_id is not None:
        assign_device(conn, cur.lastrowid, user_id)
    return True


def assign_device(conn: sqlite3.Connection, device_pk: int, user_id: int | None) -> bool:
    """端末の持ち主を変える。これまでの記録は前の持ち主のまま残す。"""
    row = conn.execute("SELECT device_id FROM devices WHERE id = ?", (device_pk,)).fetchone()
    if row is None:
        return False
    conn.execute("UPDATE devices SET user_id = ? WHERE id = ?", (user_id, device_pk))
    if user_id is not None:
        # 持ち主が決まる前に届いていた記録は、この持ち主のものにする（登録が後になった場合のため）
        conn.execute(
            "UPDATE step_records SET user_id = ? WHERE device_id = ? AND user_id IS NULL",
            (user_id, row["device_id"]),
        )
    conn.commit()
    return True


def delete_device(conn: sqlite3.Connection, device_pk: int) -> str:
    """端末を消す。歩数の記録が残っている端末は消さない。戻り値: ok / has_records / not_found"""
    row = conn.execute("SELECT device_id FROM devices WHERE id = ?", (device_pk,)).fetchone()
    if row is None:
        return "not_found"
    if conn.execute("SELECT 1 FROM step_records WHERE device_id = ? LIMIT 1", (row["device_id"],)).fetchone():
        return "has_records"
    conn.execute("DELETE FROM devices WHERE id = ?", (device_pk,))
    conn.commit()
    return "ok"


# ---------------------------------------------------------------- 同期の受け取り

def ingest_sync(conn: sqlite3.Connection, data: SyncIn, now: datetime) -> dict:
    """中継（PC・タブレット）から届いた歩数を保存し、同期の記録を残す。"""
    now_text = db.to_jst_text(now)
    inserted = duplicates = invalid = 0
    user_id = None

    if data.status == "ok":
        # 登録されていない端末も「持ち主なし」で登録して、データは捨てない
        conn.execute("INSERT OR IGNORE INTO devices (device_id) VALUES (?)", (data.device_id,))
        user_id = conn.execute("SELECT user_id FROM devices WHERE device_id = ?", (data.device_id,)).fetchone()["user_id"]
        latest_valid = now + FUTURE_MARGIN
        for record in data.records:
            measured = db.as_jst(record.measured_at)
            if not OLDEST_VALID <= measured <= latest_valid:
                invalid += 1
                continue
            cur = conn.execute(
                """INSERT OR IGNORE INTO step_records (device_id, user_id, measured_at, steps, received_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (data.device_id, user_id, measured.isoformat(), record.steps, now_text),
            )
            if cur.rowcount == 1:
                inserted += 1
            else:
                duplicates += 1
        conn.execute("UPDATE devices SET last_seen_at = ? WHERE device_id = ?", (now_text, data.device_id))

    errors = [data.error] if data.error else []
    if invalid:
        errors.append(f"端末の時刻がおかしいため {invalid} 件を保存しませんでした")

    cur = conn.execute(
        """INSERT INTO sync_logs (device_id, source, status, started_at, finished_at, record_count,
                                  inserted_count, skipped_count, rssi, note, error, received_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            data.device_id, data.source, data.status,
            db.to_jst_text_ms(data.started_at) if data.started_at else None,
            db.to_jst_text_ms(data.finished_at) if data.finished_at else None,
            len(data.records), inserted, duplicates + invalid, data.rssi, data.note,
            " / ".join(errors) or None, now_text,
        ),
    )
    conn.commit()
    return {
        "device_id": data.device_id,
        "received": len(data.records),
        "inserted": inserted,
        "skipped": duplicates,   # 重複でスキップした件数
        "invalid": invalid,      # 時刻がおかしくて保存しなかった件数
        "user_id": user_id,
        "sync_log_id": cur.lastrowid,
    }


# ---------------------------------------------------------------- 集計

def _day_bounds(start: date, end: date) -> tuple[str, str]:
    """start〜end（両端を含む）を、measured_at と比べられる文字列の範囲にする。"""
    return start.isoformat(), (end + timedelta(days=1)).isoformat()


def _alerts(rate: int, device_count: int, last_seen: str | None, settings: dict[str, int], now: datetime) -> list[dict]:
    alerts = []
    threshold = settings["overload_percent"]
    if rate >= threshold:
        alerts.append({"kind": "overload", "level": "danger", "label": "過負荷",
                       "detail": f"目標の{rate}%（しきい値 {threshold}%）"})
    hours = settings["no_sync_hours"]
    if device_count == 0:
        alerts.append({"kind": "no_device", "level": "info", "label": "端末なし",
                       "detail": "端末が割り当てられていません"})
    elif hours > 0:
        if last_seen is None:
            alerts.append({"kind": "no_sync", "level": "warn", "label": "未同期",
                           "detail": "まだ一度も同期していません"})
        else:
            gap = now - datetime.fromisoformat(last_seen)
            if gap >= timedelta(hours=hours):
                alerts.append({"kind": "no_sync", "level": "warn", "label": "未同期",
                               "detail": f"{int(gap.total_seconds() // 3600)}時間データが届いていません"})
    return alerts


def user_overview(conn: sqlite3.Connection, now: datetime) -> list[dict]:
    """利用者ごとの今日の歩数・目標達成率・アラート。"""
    settings = db.get_settings(conn)
    lo, hi = _day_bounds(now.date(), now.date())
    today_steps = {
        row["user_id"]: row["steps"]
        for row in conn.execute(
            """SELECT user_id, SUM(steps) AS steps FROM step_records
               WHERE user_id IS NOT NULL AND measured_at >= ? AND measured_at < ? GROUP BY user_id""",
            (lo, hi),
        )
    }
    devices: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for row in conn.execute("SELECT device_id, user_id, last_seen_at FROM devices WHERE user_id IS NOT NULL ORDER BY device_id"):
        devices[row["user_id"]].append(row)

    result = []
    for user in conn.execute("SELECT * FROM users ORDER BY id"):
        steps = today_steps.get(user["id"], 0)
        goal = user["goal_steps"]
        rate = round(steps * 100 / goal) if goal > 0 else 0
        user_devices = devices.get(user["id"], [])
        seen = [d["last_seen_at"] for d in user_devices if d["last_seen_at"]]
        last_seen = max(seen) if seen else None
        result.append({
            "id": user["id"],
            "display_name": user["display_name"],
            "baseline_steps": user["baseline_steps"],
            "goal_steps": goal,
            "today_steps": steps,
            "rate": rate,
            "achieved": steps >= goal,
            "devices": [d["device_id"] for d in user_devices],
            "last_seen_at": last_seen,
            "alerts": _alerts(rate, len(user_devices), last_seen, settings, now),
        })
    return result


def summary(conn: sqlite3.Connection, now: datetime) -> dict:
    """今日の歩数・目標達成率・アラートの一覧。"""
    users = user_overview(conn, now)
    alerts = [
        {"user_id": u["id"], "display_name": u["display_name"], **alert}
        for u in users for alert in u["alerts"] if alert["level"] != "info"
    ]
    return {
        "date": now.date().isoformat(),
        "generated_at": db.to_jst_text(now),
        "settings": db.get_settings(conn),
        "user_count": len(users),
        "achieved_count": sum(1 for u in users if u["achieved"]),
        "users": users,
        "alerts": alerts,
    }


def get_user(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def daily_steps(conn: sqlite3.Connection, user_id: int, start: date, end: date) -> list[dict]:
    """日別の歩数。記録が1件もない日は steps を None にする（0歩と区別するため）。"""
    lo, hi = _day_bounds(start, end)
    rows = conn.execute(
        """SELECT substr(measured_at, 1, 10) AS day, SUM(steps) AS steps, COUNT(*) AS n
           FROM step_records WHERE user_id = ? AND measured_at >= ? AND measured_at < ?
           GROUP BY day""",
        (user_id, lo, hi),
    ).fetchall()
    by_day = {row["day"]: row for row in rows}
    days = []
    day = start
    while day <= end:
        row = by_day.get(day.isoformat())
        days.append({"date": day.isoformat(), "steps": row["steps"] if row else None, "record_count": row["n"] if row else 0})
        day += timedelta(days=1)
    return days


def duration_seconds(started_at: str | None, finished_at: str | None) -> float | None:
    if not started_at or not finished_at:
        return None
    return (datetime.fromisoformat(finished_at) - datetime.fromisoformat(started_at)).total_seconds()


def recent_sync_logs(conn: sqlite3.Connection, device_ids: list[str] | None = None, limit: int = 50) -> list[dict]:
    sql = """SELECT s.*, u.display_name FROM sync_logs s
             LEFT JOIN devices d ON d.device_id = s.device_id
             LEFT JOIN users u ON u.id = d.user_id"""
    params: list = []
    if device_ids is not None:
        if not device_ids:
            return []
        sql += f" WHERE s.device_id IN ({','.join('?' * len(device_ids))})"
        params.extend(device_ids)
    sql += " ORDER BY s.received_at DESC, s.id DESC LIMIT ?"
    params.append(limit)
    return [
        {**dict(row), "duration_sec": duration_seconds(row["started_at"], row["finished_at"])}
        for row in conn.execute(sql, params)
    ]


def sync_stats(conn: sqlite3.Connection) -> list[dict]:
    """端末ごとの同期の成功率・電波の強さ・同期時間（BLE 検証用）。"""
    rows = conn.execute(
        """SELECT device_id,
                  COUNT(*) AS total,
                  SUM(status = 'ok') AS ok_count,
                  AVG(rssi) AS avg_rssi, MIN(rssi) AS min_rssi, MAX(rssi) AS max_rssi,
                  AVG(CASE WHEN status = 'ok' THEN (julianday(finished_at) - julianday(started_at)) * 86400 END) AS avg_duration,
                  MAX(received_at) AS last_at
           FROM sync_logs GROUP BY device_id ORDER BY device_id"""
    ).fetchall()
    return [{**dict(row), "success_rate": round(row["ok_count"] * 100 / row["total"]) if row["total"] else 0} for row in rows]
