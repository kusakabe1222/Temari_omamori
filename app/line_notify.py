"""LINE Messaging API でメッセージを送る（プッシュメッセージ）。

外部（api.line.me）と通信するのはこのファイルだけ。
.env に LINE_CHANNEL_ACCESS_TOKEN と LINE_TO の両方があるときだけ送る。
送る内容はニックネームと歩数だけにする（本名などの個人情報は送らない）。
"""
from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from datetime import date, datetime

from . import db, services
from .config import Config

PUSH_URL = "https://api.line.me/v2/bot/message/push"
MAX_TEXT = 5000            # LINE のテキストメッセージの上限
MAX_ALERT_TRIES = 3        # 同じアラートの送信に失敗したとき、1日に試す回数の上限
WEEKDAYS = "月火水木金土日"


def send_text(config: Config, text: str) -> tuple[bool, str | None]:
    """テキストを1通送る。戻り値は (送れたか, 送れなかった理由)。"""
    if not config.line_configured:
        return False, "LINE の設定（.env の LINE_CHANNEL_ACCESS_TOKEN と LINE_TO）がありません"
    body = json.dumps({"to": config.line_to, "messages": [{"type": "text", "text": text[:MAX_TEXT]}]}).encode("utf-8")
    request = urllib.request.Request(
        PUSH_URL,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {config.line_channel_access_token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10):
            return True, None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        return False, f"LINE API エラー {e.code}: {detail}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return False, f"LINE に接続できませんでした: {e}"


def log_send(conn: sqlite3.Connection, kind: str, ok: bool, error: str | None,
             user_id: int | None = None, alert_date: str | None = None) -> None:
    conn.execute(
        "INSERT INTO line_logs (kind, user_id, alert_date, ok, error, sent_at) VALUES (?, ?, ?, ?, ?, ?)",
        (kind, user_id, alert_date, 1 if ok else 0, error, db.to_jst_text(db.now_jst())),
    )
    conn.commit()


def _jdate(day: date) -> str:
    return f"{day.month}月{day.day}日（{WEEKDAYS[day.weekday()]}）"


def format_summary(data: dict) -> str:
    generated = datetime.fromisoformat(data["generated_at"])
    lines = [f"【お守り歩数計】{_jdate(generated.date())} {generated:%H:%M} 時点のまとめ"]
    if not data["users"]:
        lines.append("利用者が登録されていません。")
    for user in data["users"]:
        marks = "".join(f"【{a['label']}】" for a in user["alerts"] if a["level"] != "info")
        lines.append(f"・{user['display_name']}さん {user['today_steps']:,}歩（目標の{user['rate']}%）{marks}")
    lines.append(f"目標達成 {data['achieved_count']}/{data['user_count']}人")
    return "\n".join(lines)


def format_alert(user: dict, overload_percent: int) -> str:
    return (
        "【お守り歩数計】過負荷アラート\n"
        f"{user['display_name']}さんの今日の歩数が {user['today_steps']:,}歩 になりました"
        f"（目標 {user['goal_steps']:,}歩 の{user['rate']}%）。\n"
        f"しきい値: 目標の{overload_percent}%以上"
    )


def notify_overload_if_needed(config: Config, user_id: int) -> None:
    """同期のあとに呼ぶ。過負荷になっていて、今日まだ送っていなければ LINE に送る。"""
    if not config.line_configured:
        return
    conn = db.connect(config.db_path)
    try:
        settings = db.get_settings(conn)
        if not settings["line_alert_enabled"]:
            return
        now = db.now_jst()
        user = next((u for u in services.user_overview(conn, now) if u["id"] == user_id), None)
        if user is None or not any(a["kind"] == "overload" for a in user["alerts"]):
            return
        today = now.date().isoformat()
        sent, failed = conn.execute(
            """SELECT COALESCE(SUM(ok = 1), 0), COALESCE(SUM(ok = 0), 0) FROM line_logs
               WHERE kind = 'alert' AND user_id = ? AND alert_date = ?""",
            (user_id, today),
        ).fetchone()
        if sent or failed >= MAX_ALERT_TRIES:
            return
        ok, error = send_text(config, format_alert(user, settings["overload_percent"]))
        log_send(conn, "alert", ok, error, user_id=user_id, alert_date=today)
    finally:
        conn.close()
