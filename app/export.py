"""CSV エクスポート（検証の分析とレポート用）。"""
from __future__ import annotations

import csv
import io
import sqlite3
from datetime import date, timedelta

from .services import duration_seconds

KINDS = ("daily", "steps", "sync")


def _safe(value):
    # Excel で開いたときに式として動かないよう、= + - @ で始まる文字列の前に ' を付ける
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def to_csv_bytes(header: list[str], rows: list[list]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows([[_safe(v) for v in row] for row in rows])
    return buffer.getvalue().encode("utf-8-sig")  # BOM 付きにして Excel で文字化けしないようにする


def _where(column: str, start: date | None, end: date | None) -> tuple[str, list[str]]:
    clauses, params = [], []
    if start:
        clauses.append(f"{column} >= ?")
        params.append(start.isoformat())
    if end:
        clauses.append(f"{column} < ?")
        params.append((end + timedelta(days=1)).isoformat())
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def build(conn: sqlite3.Connection, kind: str, start: date | None, end: date | None) -> tuple[list[str], list[list]]:
    if kind == "steps":
        where, params = _where("r.measured_at", start, end)
        header = ["measured_at", "date", "device_id", "user_id", "display_name", "steps", "received_at"]
        rows = conn.execute(
            f"""SELECT r.measured_at, substr(r.measured_at, 1, 10), r.device_id, r.user_id, u.display_name, r.steps, r.received_at
                FROM step_records r LEFT JOIN users u ON u.id = r.user_id{where}
                ORDER BY r.measured_at, r.device_id""",
            params,
        ).fetchall()
        return header, [list(row) for row in rows]

    if kind == "daily":
        where, params = _where("r.measured_at", start, end)
        header = ["date", "user_id", "display_name", "steps", "record_count", "goal_steps", "achieved", "rate_percent"]
        rows = conn.execute(
            f"""SELECT substr(r.measured_at, 1, 10) AS day, r.user_id, u.display_name, SUM(r.steps), COUNT(*), u.goal_steps
                FROM step_records r JOIN users u ON u.id = r.user_id{where}
                GROUP BY day, r.user_id ORDER BY day, r.user_id""",
            params,
        ).fetchall()
        result = []
        for day, user_id, name, steps, count, goal in rows:
            rate = round(steps * 100 / goal) if goal else 0
            result.append([day, user_id, name, steps, count, goal, 1 if steps >= goal else 0, rate])
        return header, result

    if kind == "sync":
        where, params = _where("s.received_at", start, end)
        header = ["id", "received_at", "device_id", "display_name", "source", "status", "started_at", "finished_at",
                  "duration_sec", "record_count", "inserted_count", "skipped_count", "rssi", "note", "error"]
        rows = conn.execute(
            f"""SELECT s.id, s.received_at, s.device_id, u.display_name, s.source, s.status, s.started_at, s.finished_at,
                       s.record_count, s.inserted_count, s.skipped_count, s.rssi, s.note, s.error
                FROM sync_logs s LEFT JOIN devices d ON d.device_id = s.device_id
                LEFT JOIN users u ON u.id = d.user_id{where}
                ORDER BY s.received_at, s.id""",
            params,
        ).fetchall()
        result = []
        for row in rows:
            row = list(row)
            duration = duration_seconds(row[6], row[7])
            result.append(row[:8] + [round(duration, 2) if duration is not None else None] + row[8:])
        return header, result

    raise ValueError(f"知らない種類です: {kind}")
