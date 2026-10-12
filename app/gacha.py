"""ガチャ（メッセージガチャ）。

メッセージは、前の LINE Bot 案（Desktop\\PD\\omamori-bot\\src\\gacha.js）のものをそのまま使う。画像は使わない。
まずレア度を 60% / 30% / 10% で決め、そのレア度の中からメッセージを1つ選ぶ。
"""
from __future__ import annotations

import random
import sqlite3
from datetime import datetime

from . import db, points

RARITY_WEIGHTS = [("common", 60), ("rare", 30), ("super_rare", 10)]
LABELS = {"common": "★☆☆ ノーマル", "rare": "★★☆ レア", "super_rare": "★★★ スーパーレア"}
PRIZES = {
    "common": [
        ("c1", "一歩一歩が健康の積み重ね。よく歩きました！🌿"),
        ("c2", "今日も体を動かしてくれてありがとう😊"),
        ("c3", "コツコツ歩くことが長生きの秘訣です🚶"),
    ],
    "rare": [
        ("r1", "素晴らしい！その調子で歩き続けましょう🌟"),
        ("r2", "あなたの歩みは家族みんなの安心です👨‍👩‍👧"),
        ("r3", "この調子！健康的な毎日を続けましょう💪"),
    ],
    "super_rare": [
        ("s1", "最高！今日のあなたはとても輝いています✨🎊"),
        ("s2", "歩数チャンピオン！この調子で健康長寿へ🏆"),
    ],
}

_rng = random.SystemRandom()


def draw(rng: random.Random | None = None) -> dict:
    rng = rng or _rng
    rarity = rng.choices([r for r, _ in RARITY_WEIGHTS], weights=[w for _, w in RARITY_WEIGHTS])[0]
    key, message = rng.choice(PRIZES[rarity])
    return {"key": key, "rarity": rarity, "label": LABELS[rarity], "message": message}


def spin(conn: sqlite3.Connection, user_id: int, now: datetime, rng: random.Random | None = None) -> dict:
    """ポイントが足りれば1回回す。戻り値の ok が False ならポイント不足（何も変えない）。"""
    # 家族が同時に回しても、ポイントを使いすぎないように、書き込みを1つずつにする
    conn.execute("BEGIN IMMEDIATE")
    try:
        before = points.summary(conn, user_id, now.date())
        if before is None:
            conn.rollback()
            raise ValueError("利用者が見つかりません")
        cost = before["gacha_cost"]
        if before["available"] < cost:
            conn.rollback()
            return {"ok": False, "available": before["available"], "cost": cost}
        prize = draw(rng)
        conn.execute(
            """INSERT INTO gacha_draws (user_id, prize_key, rarity, message, points_used, drawn_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, prize["key"], prize["rarity"], prize["message"], cost, db.to_jst_text(now)),
        )
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    return {"ok": True, "prize": prize, "cost": cost, "available": before["available"] - cost}


def recent_draws(conn: sqlite3.Connection, user_id: int, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM gacha_draws WHERE user_id = ? ORDER BY drawn_at DESC, id DESC LIMIT ?", (user_id, limit)
    ).fetchall()
    return [{**dict(row), "label": LABELS.get(row["rarity"], row["rarity"])} for row in rows]
