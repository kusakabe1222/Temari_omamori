"""にせ LINE: 本物の LINE やトンネルがなくても、LINE の入口（app.bot_main）を試すためのスクリプト。

LINE と同じ形のイベントに、同じやり方で署名を付けて送る。
LINE の入口は「試し運転」（LINE_DRY_RUN=1）で起動しておくこと。返事は LINE には送られず、ここに表示される。

使い方（LINE の入口を起動したまま、別の PowerShell で）:
    .\\.venv\\Scripts\\python.exe scripts\\fake_line.py --follow           # 友だち追加
    .\\.venv\\Scripts\\python.exe scripts\\fake_line.py "登録 123456"       # 番号で登録
    .\\.venv\\Scripts\\python.exe scripts\\fake_line.py 今日
    .\\.venv\\Scripts\\python.exe scripts\\fake_line.py ガチャ
    .\\.venv\\Scripts\\python.exe scripts\\fake_line.py --user U-family 今日   # 別の人（家族など）として送る
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import load_env_file  # noqa: E402
from app.line_api import sign  # noqa: E402

DEFAULT_SERVER = "http://127.0.0.1:8001"


def main() -> int:
    parser = argparse.ArgumentParser(description="にせ LINE（LINE の入口にテストのイベントを送る）")
    parser.add_argument("text", nargs="?", help="送ることば（例: 今日）")
    parser.add_argument("--follow", action="store_true", help="友だち追加のイベントを送る")
    parser.add_argument("--user", default="U-fake-user-0001", help="LINE のユーザーID（初期値 U-fake-user-0001）")
    parser.add_argument("--server", default=DEFAULT_SERVER, help=f"LINE の入口の URL（初期値 {DEFAULT_SERVER}）")
    args = parser.parse_args()
    if not args.follow and not args.text:
        parser.error("送ることばか --follow を指定してください")

    secret = os.environ.get("LINE_CHANNEL_SECRET") or load_env_file(ROOT / ".env").get("LINE_CHANNEL_SECRET", "")
    if not secret:
        print("LINE_CHANNEL_SECRET が見つかりません（LINE の入口と同じ値が必要です）。")
        return 1

    event = {
        "type": "follow" if args.follow else "message",
        "webhookEventId": uuid.uuid4().hex,
        "timestamp": int(time.time() * 1000),
        "source": {"type": "user", "userId": args.user},
        "replyToken": "fake-reply-token",
        "mode": "active",
    }
    if not args.follow:
        event["message"] = {"type": "text", "id": uuid.uuid4().hex, "text": args.text}
    body = json.dumps({"destination": "fake", "events": [event]}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        args.server.rstrip("/") + "/line/webhook",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "X-Line-Signature": sign(secret, body)},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"LINE の入口がエラーを返しました（{e.code}）: {e.read().decode('utf-8', 'replace')[:300]}")
        return 1
    except urllib.error.URLError as e:
        print(f"LINE の入口に接続できません（{e.reason}）。起動しているか確かめてください: {args.server}")
        return 1

    replies = result.get("replies")
    if replies is None:
        print("送りました（LINE の入口が試し運転ではないので、返事は本物の LINE に送られます）。")
        return 0
    if not replies:
        print("（返事なし）")
    for reply in replies:
        for message in reply["messages"]:
            print("――― 返事 ―――")
            print(message.get("text", json.dumps(message, ensure_ascii=False)))
            buttons = [item["action"]["label"] for item in message.get("quickReply", {}).get("items", [])]
            if buttons:
                print("［ボタン］ " + " / ".join(buttons))
    return 0


if __name__ == "__main__":
    sys.exit(main())
