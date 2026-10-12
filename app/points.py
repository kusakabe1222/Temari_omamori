"""ポイントの計算。

ポイントは歩数の記録から毎回計算する（毎日決まった時刻に確定させる処理はいらない。
あとから届いた歩数も、届いた時点で反映される）。ガチャで使った分は gacha_draws に残す。

ルール（設定画面で変えられる。変えると、これまでの分もふくめて計算し直す）:
- steps_per_point 歩ごとに 1 ポイント
- その日の目標を達成したら goal_bonus_points をたす
- 過負荷アラートのしきい値（目標の overload_percent %）を超えた分の歩数はポイントにしない（歩きすぎをすすめないため）
"""
from __future__ import annotations

import sqlite3
from datetime import date

from . import db


def day_points(steps: int, goal: int, settings: dict[str, int]) -> int:
    cap = goal * settings["overload_percent"] // 100
    counted = min(steps, cap) if cap > 0 else steps
    points = counted // max(settings["steps_per_point"], 1)
    if goal > 0 and steps >= goal:
        points += settings["goal_bonus_points"]
    return points


def daily_totals(conn: sqlite3.Connection, user_id: int) -> list[tuple[str, int]]:
    """[(日付, その日の歩数), ...]（記録のある日だけ）"""
    rows = conn.execute(
        """SELECT substr(measured_at, 1, 10) AS day, SUM(steps) AS steps
           FROM step_records WHERE user_id = ? GROUP BY day ORDER BY day""",
        (user_id,),
    ).fetchall()
    return [(row["day"], row["steps"]) for row in rows]


def summary(conn: sqlite3.Connection, user_id: int, today: date) -> dict | None:
    user = conn.execute("SELECT goal_steps FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        return None
    settings = db.get_settings(conn)
    goal = user["goal_steps"]
    today_text, month_text = today.isoformat(), today.isoformat()[:7]
    earned = today_points = month_points = 0
    for day, steps in daily_totals(conn, user_id):
        points = day_points(steps, goal, settings)
        earned += points
        if day == today_text:
            today_points = points
        if day.startswith(month_text):
            month_points += points
    spent, draws = conn.execute(
        "SELECT COALESCE(SUM(points_used), 0), COUNT(*) FROM gacha_draws WHERE user_id = ?", (user_id,)
    ).fetchone()
    available = max(earned - spent, 0)  # ルールを変えて「使った分」の方が多くなっても、マイナスにはしない
    return {
        "earned": earned,          # もらったポイントの合計
        "spent": spent,            # ガチャで使ったポイントの合計
        "available": available,    # いま使えるポイント
        "today": today_points,     # 今日の分（歩くと増える）
        "month": month_points,     # 今月の分
        "draw_count": draws,       # ガチャを回した回数
        "gacha_cost": settings["gacha_cost"],
        "gacha_left": available // max(settings["gacha_cost"], 1),  # あと何回回せるか
    }
