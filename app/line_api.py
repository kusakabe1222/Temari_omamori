"""LINE Messaging API とのやりとり（署名の確認と、返事の送信）。

外部（api.line.me）と通信するのはこのファイルの reply() だけ。
試し運転（.env の LINE_DRY_RUN=1）のときは送らずに、返事を画面に出すだけにする。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import urllib.error
import urllib.request

from .config import Config

REPLY_URL = "https://api.line.me/v2/bot/message/reply"
MAX_TEXT = 5000  # LINE のテキストメッセージの上限


def verify_signature(channel_secret: str, body: bytes, signature: str | None) -> bool:
    """LINE から届いたものか確かめる（X-Line-Signature ＝ チャネルシークレットで作った HMAC-SHA256）。"""
    if not channel_secret or not signature:
        return False
    digest = hmac.new(channel_secret.encode("utf-8"), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode("ascii"), signature)


def sign(channel_secret: str, body: bytes) -> str:
    """テストや scripts/fake_line.py で、LINE と同じ署名を作るために使う。"""
    return base64.b64encode(hmac.new(channel_secret.encode("utf-8"), body, hashlib.sha256).digest()).decode("ascii")


def reply(config: Config, reply_token: str, messages: list[dict]) -> tuple[bool, str | None]:
    """返事を送る（1回に5通まで）。戻り値は (送れたか, 送れなかった理由)。"""
    for message in messages:
        if message.get("type") == "text":
            message["text"] = message["text"][:MAX_TEXT]
    if config.line_dry_run:
        for message in messages:
            print("[LINE 試し運転] 返事:\n" + message.get("text", json.dumps(message, ensure_ascii=False)), flush=True)
        return True, None
    body = json.dumps({"replyToken": reply_token, "messages": messages[:5]}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        REPLY_URL,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {config.line_channel_access_token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10):
            return True, None
    except urllib.error.HTTPError as e:
        return False, f"LINE API エラー {e.code}: {e.read().decode('utf-8', 'replace')[:300]}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return False, f"LINE に接続できませんでした: {e}"
