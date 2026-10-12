"""テストで使う準備用の関数。"""
from __future__ import annotations

import json
import sqlite3
from datetime import timedelta

from app import db, line_api
from tests.conftest import API_KEY, LINE_SECRET, PASSWORD


def today_at(minutes: int, days_ago: int = 0) -> str:
    """今日（または days_ago 日前）の 0:00 から minutes 分後（日本時間）。テストがいつ動いても同じ日になるように使う。"""
    midnight = db.now_jst().replace(hour=0, minute=0, second=0) - timedelta(days=days_ago)
    return (midnight + timedelta(minutes=minutes)).isoformat()


def rec(measured_at: str, steps: int) -> dict:
    return {"measured_at": measured_at, "steps": steps}


def sync(client, device="omamori-01", records=(), **extra):
    body = {"device_id": device, "records": list(records), **extra}
    return client.post("/api/sync", json=body, headers={"X-API-Key": API_KEY})


def login(client) -> None:
    res = client.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    assert res.status_code == 303


def query(client, sql: str, params=()) -> list[sqlite3.Row]:
    conn = db.connect(client.app.state.config.db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def add_user(client, name: str, baseline: int) -> int:
    client.post("/users", data={"display_name": name, "baseline_steps": str(baseline)})
    return query(client, "SELECT id FROM users WHERE display_name = ? ORDER BY id DESC", (name,))[0]["id"]


def assign(client, device_id: str, user_id: int | None) -> None:
    pk = query(client, "SELECT id FROM devices WHERE device_id = ?", (device_id,))[0]["id"]
    client.post(f"/devices/{pk}", data={"user_id": "" if user_id is None else str(user_id)})


def user_summary(client, user_id: int) -> dict:
    return next(u for u in client.get("/api/summary").json()["users"] if u["id"] == user_id)


def save_settings(client, **values) -> None:
    """設定画面から保存する（指定しなかった欄は初期値）。"""
    data = {key: str(value) for key, value in {**db.DEFAULT_SETTINGS, **values}.items()}
    client.post("/settings", data=data)


# ---------------------------------------------------------------- LINE

def post_events(bot_client, events: list[dict], secret: str = LINE_SECRET):
    body = json.dumps({"destination": "test", "events": events}, ensure_ascii=False).encode("utf-8")
    return bot_client.post("/line/webhook", content=body,
                           headers={"Content-Type": "application/json", "X-Line-Signature": line_api.sign(secret, body)})


def text_event(text: str, user: str = "U-test-0001", event_id: str | None = None) -> dict:
    event = {"type": "message", "source": {"type": "user", "userId": user}, "replyToken": "token",
             "message": {"type": "text", "id": "1", "text": text}}
    if event_id:
        event["webhookEventId"] = event_id
    return event


def say(bot_client, text: str, user: str = "U-test-0001") -> str:
    """LINE で text を送ったときの返事（1通目の文字）。"""
    res = post_events(bot_client, [text_event(text, user)])
    assert res.status_code == 200
    replies = res.json()["replies"]
    return replies[0]["messages"][0]["text"] if replies else ""


def make_code(client, user_id: int) -> str:
    client.post(f"/users/{user_id}/link-code")
    return query(client, "SELECT code FROM line_link_codes WHERE user_id = ?", (user_id,))[0]["code"]
