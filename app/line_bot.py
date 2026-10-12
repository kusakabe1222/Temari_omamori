"""本人用 LINE Bot の中身。LINE から届いたイベントに、返事のメッセージを作る。

使えることば（全角でもよい）: 登録 123456 / 今日 / 1週間 / 1か月 / ポイント / ガチャ / 目標 / 解除 / ヘルプ
返事にはクイックリプライ（トークの下に出るボタン）を付けて、文字を打たなくても使えるようにする。
LINE に送るのはニックネーム・歩数・ポイント・ガチャのメッセージだけ。
"""
from __future__ import annotations

import random
import re
import sqlite3
import time
import traceback
import unicodedata
from collections import defaultdict, deque
from datetime import date, datetime, timedelta

from . import db, gacha, line_api, line_links, points, services
from .config import Config

QUICK_REPLIES = ["今日", "1週間", "1か月", "ポイント", "ガチャ", "目標"]
WEEKDAYS = "月火水木金土日"
LINK_PATTERN = re.compile(r"^登録\s*(\d{6})$")

# 番号を何度もまちがえたら、しばらく受けつけない（番号を当てずっぽうで試されないように）
MAX_LINK_FAILURES = 5
LINK_FAILURE_SECONDS = 3600
_link_failures: dict[str, deque] = defaultdict(deque)  # サーバーを起動し直すと消える

EVENT_KEEP_DAYS = 7  # 送り直しを見分けるためのイベント ID を残す日数

COMMANDS = {
    "今日": "today", "きょう": "today", "今日の歩数": "today",
    "1週間": "week", "一週間": "week", "週間": "week", "週間レポート": "week",
    "1か月": "month", "1ヶ月": "month", "一か月": "month", "月間": "month", "月間レポート": "month",
    "ポイント": "points", "ポイント確認": "points",
    "ガチャ": "gacha", "ガチャを回す": "gacha",
    "目標": "goal", "目標確認": "goal",
    "解除": "unlink", "登録解除": "unlink",
    "ヘルプ": "help", "メニュー": "help", "使い方": "help",
}

NOT_LINKED = (
    "まだ登録されていません。\n"
    "施設の方から受け取った6けたの番号を、\n"
    "「登録 123456」\n"
    "のように送ってください。"
)
HELP = (
    "お守り歩数計です。\n"
    "下のボタンを押すか、ことばを送ってください。\n"
    "・今日 … きょうの歩数\n"
    "・1週間／1か月 … これまでの歩数\n"
    "・ポイント／ガチャ／目標\n"
    "・解除 … このLINEの登録をやめる\n\n"
    "歩数は、お守りが施設のパソコンと通信したときに新しくなります。"
)
ONE_TO_ONE = "このアカウントは、1対1のトークで使ってください。"
ERROR = "うまくいきませんでした。しばらくしてから、もう一度ためしてください。"


# ---------------------------------------------------------------- メッセージの部品

def text_message(text: str) -> dict:
    return {"type": "text", "text": text}


def with_quick_reply(message: dict) -> dict:
    message["quickReply"] = {
        "items": [{"type": "action", "action": {"type": "message", "label": label, "text": label}} for label in QUICK_REPLIES]
    }
    return message


def jdate(day: date) -> str:
    return f"{day.month}/{day.day}（{WEEKDAYS[day.weekday()]}）"


def progress_bar(rate: int) -> str:
    filled = max(0, min(10, rate // 10))
    return "🟩" * filled + "⬜" * (10 - filled)


def rules_text(settings: dict[str, int]) -> str:
    lines = ["ポイントのもらいかた", f"・{settings['steps_per_point']:,}歩ごとに 1pt"]
    if settings["goal_bonus_points"]:
        lines.append(f"・目標を達成した日は +{settings['goal_bonus_points']}pt")
    lines.append(f"・目標の{settings['overload_percent']}%をこえた分の歩数は、ポイントになりません（歩きすぎに気をつけましょう）")
    return "\n".join(lines)


# ---------------------------------------------------------------- それぞれの返事

def _overview(conn: sqlite3.Connection, user_id: int, now: datetime) -> dict:
    return next(u for u in services.user_overview(conn, now) if u["id"] == user_id)


def reply_today(conn: sqlite3.Connection, user: sqlite3.Row, now: datetime) -> str:
    info = _overview(conn, user["id"], now)
    pts = points.summary(conn, user["id"], now.date())
    steps, goal, rate = info["today_steps"], info["goal_steps"], info["rate"]
    lines = [
        f"【きょうの歩数】{jdate(now.date())}",
        f"👟 {steps:,}歩",
        f"{progress_bar(rate)} {rate}%",
        f"🎯 目標 {goal:,}歩",
        "🎉 目標を達成しました！" if steps >= goal else f"あと {goal - steps:,}歩で目標です",
    ]
    if any(a["kind"] == "overload" for a in info["alerts"]):
        lines.append("⚠ きょうはたくさん歩きました。無理をせず、休むことも大切です。")
    lines += ["", f"💰 きょうのポイント {pts['today']}pt（いま使えるのは {pts['available']}pt）"]
    if info["last_seen_at"]:
        seen = datetime.fromisoformat(info["last_seen_at"])
        lines.append(f"🔄 記録が届いた時刻 {seen.month}/{seen.day} {seen:%H:%M}")
    else:
        lines.append("🔄 まだ記録が届いていません")
    return "\n".join(lines)


def reply_week(conn: sqlite3.Connection, user: sqlite3.Row, now: datetime) -> str:
    today = now.date()
    days = services.daily_steps(conn, user["id"], today - timedelta(days=6), today)
    goal = user["goal_steps"]
    lines = ["【この1週間】"]
    for d in days:
        day = date.fromisoformat(d["date"])
        if d["steps"] is None:
            lines.append(f"{jdate(day)}  記録なし")
        else:
            lines.append(f"{jdate(day)}  {d['steps']:,}歩{' ✅' if d['steps'] >= goal else ''}")
    total = sum(d["steps"] or 0 for d in days)
    achieved = sum(1 for d in days if d["steps"] is not None and d["steps"] >= goal)
    lines += ["", f"合計 {total:,}歩", f"目標を達成した日 {achieved}日／7日（✅）"]
    return "\n".join(lines)


def reply_month(conn: sqlite3.Connection, user: sqlite3.Row, now: datetime) -> str:
    today = now.date()
    start = today.replace(day=1)
    days = [d for d in services.daily_steps(conn, user["id"], start, today) if d["steps"] is not None]
    goal = user["goal_steps"]
    total = sum(d["steps"] for d in days)
    achieved = sum(1 for d in days if d["steps"] >= goal)
    pts = points.summary(conn, user["id"], today)
    lines = [f"【{today.month}月のまとめ】（{start.day}日〜{today.day}日）", f"👟 合計 {total:,}歩", f"✅ 目標を達成した日 {achieved}日"]
    if days:
        best = max(days, key=lambda d: d["steps"])
        lines.append(f"🏅 いちばん歩いた日 {jdate(date.fromisoformat(best['date']))} {best['steps']:,}歩")
    lines.append(f"💰 今月のポイント {pts['month']}pt")
    return "\n".join(lines)


def reply_points(conn: sqlite3.Connection, user: sqlite3.Row, now: datetime) -> str:
    pts = points.summary(conn, user["id"], now.date())
    return "\n".join([
        "【ポイント】",
        f"💰 いま使えるポイント {pts['available']}pt",
        f"🎰 ガチャ あと{pts['gacha_left']}回まわせます（1回 {pts['gacha_cost']}pt）",
        "",
        f"もらったポイントの合計 {pts['earned']}pt",
        f"使ったポイント {pts['spent']}pt",
        "",
        rules_text(db.get_settings(conn)),
    ])


def reply_gacha(conn: sqlite3.Connection, user: sqlite3.Row, now: datetime, rng: random.Random | None) -> str:
    result = gacha.spin(conn, user["id"], now, rng)
    if not result["ok"]:
        need = result["cost"] - result["available"]
        steps_per_point = db.get_settings(conn)["steps_per_point"]
        return (
            "ポイントが足りません。\n"
            f"いま {result['available']}pt、ガチャには {result['cost']}pt 必要です。\n"
            f"あと {need}pt（だいたい {need * steps_per_point:,}歩）でまわせます🚶"
        )
    prize = result["prize"]
    return f"🎰 ガチャの結果\n\n{prize['label']}\n{prize['message']}\n\n使ったポイント {result['cost']}pt／のこり {result['available']}pt"


def reply_goal(conn: sqlite3.Connection, user: sqlite3.Row) -> str:
    settings = db.get_settings(conn)
    return "\n".join([
        "【目標】",
        f"🎯 1日の目標 {user['goal_steps']:,}歩",
        f"（はじめる前の平均 {user['baseline_steps']:,}歩 ＋ {services.GOAL_INCREMENT:,}歩）",
        "",
        rules_text(settings),
        f"🎰 ガチャは {settings['gacha_cost']}pt で1回",
    ])


def register(conn: sqlite3.Connection, line_user_id: str, code: str, now: datetime) -> str:
    failures = _link_failures[line_user_id]
    cutoff = time.monotonic() - LINK_FAILURE_SECONDS
    while failures and failures[0] < cutoff:
        failures.popleft()
    if len(failures) >= MAX_LINK_FAILURES:
        return "何度も番号がちがったので、1時間ほどたってから、もう一度送ってください。"
    user = line_links.link(conn, line_user_id, code, now)
    if user is None:
        failures.append(time.monotonic())
        return "番号が見つかりませんでした。施設の方から受け取った6けたの番号を、もう一度たしかめてください。"
    failures.clear()
    return f"登録しました。{user['display_name']}さんの歩数とポイントを見られるようになりました。\n下のボタンを押してみてください。"


# ---------------------------------------------------------------- 届いたものの振り分け

def handle_text(conn: sqlite3.Connection, line_user_id: str, text: str, now: datetime,
                rng: random.Random | None = None) -> list[dict]:
    text = unicodedata.normalize("NFKC", text).strip()  # 全角の数字や空白も使えるように
    match = LINK_PATTERN.match(text)
    if match:
        message = register(conn, line_user_id, match.group(1), now)
        linked = line_links.linked_user(conn, line_user_id) is not None
        return [with_quick_reply(text_message(message)) if linked else text_message(message)]

    user = line_links.linked_user(conn, line_user_id)
    if user is None:
        return [text_message(NOT_LINKED)]

    command = COMMANDS.get(text, "help")
    if command == "unlink":
        line_links.unlink_line_user(conn, line_user_id)
        return [text_message("登録を解除しました。もう一度使うときは、番号を送ってください。")]
    if command == "today":
        reply = reply_today(conn, user, now)
    elif command == "week":
        reply = reply_week(conn, user, now)
    elif command == "month":
        reply = reply_month(conn, user, now)
    elif command == "points":
        reply = reply_points(conn, user, now)
    elif command == "gacha":
        reply = reply_gacha(conn, user, now, rng)
    elif command == "goal":
        reply = reply_goal(conn, user)
    else:
        reply = HELP
    return [with_quick_reply(text_message(reply))]


def _is_redelivery(conn: sqlite3.Connection, event_id: str | None, now: datetime) -> bool:
    """LINE が同じイベントを送り直してきたか。初めてなら ID を残す。"""
    if not event_id:
        return False
    cur = conn.execute("INSERT OR IGNORE INTO line_events (webhook_event_id, received_at) VALUES (?, ?)",
                       (event_id, db.to_jst_text(now)))
    conn.execute("DELETE FROM line_events WHERE received_at < ?", (db.to_jst_text(now - timedelta(days=EVENT_KEEP_DAYS)),))
    conn.commit()
    return cur.rowcount == 0


def handle_event(conn: sqlite3.Connection, event: dict, now: datetime,
                 rng: random.Random | None = None) -> list[dict] | None:
    """イベント1つを処理して、返事のメッセージを返す（返事がいらなければ None）。"""
    if _is_redelivery(conn, event.get("webhookEventId"), now):
        return None
    kind = event.get("type")
    source = event.get("source") or {}
    if source.get("type") != "user":
        return [text_message(ONE_TO_ONE)] if kind == "message" else None
    line_user_id = source.get("userId")
    if not line_user_id:
        return None

    if kind == "follow":
        linked = line_links.linked_user(conn, line_user_id) is not None
        intro = "友だち追加ありがとうございます。お守り歩数計です。\n\n"
        return [with_quick_reply(text_message(intro + HELP)) if linked else text_message(intro + NOT_LINKED)]
    if kind == "unfollow":
        # ブロックされたら、つながりを消す（もう返事を送れないため）
        line_links.unlink_line_user(conn, line_user_id)
        return None
    if kind == "message":
        message = event.get("message") or {}
        if message.get("type") == "text":
            return handle_text(conn, line_user_id, message.get("text", ""), now, rng)
        return [text_message("文字で送ってください。\n\n" + HELP)]
    return None


def handle_events(config: Config, events: list[dict], rng: random.Random | None = None) -> list[dict]:
    """webhook に届いたイベントをすべて処理し、返事を送る。戻り値は送った返事（試し運転で表示する用）。"""
    results = []
    conn = db.connect(config.db_path)
    try:
        for event in events:
            try:
                messages = handle_event(conn, event, db.now_jst(), rng)
            except Exception:  # noqa: BLE001 - 1つのイベントの失敗で、ほかのイベントを止めない
                traceback.print_exc()
                if conn.in_transaction:
                    conn.rollback()
                messages = [text_message(ERROR)]
            token = event.get("replyToken")
            if messages and token:
                ok, error = line_api.reply(config, token, messages)
                if not ok:
                    print(f"[LINE] 返事を送れませんでした: {error}", flush=True)
                results.append({"messages": messages, "sent": ok})
    finally:
        conn.close()
    return results
