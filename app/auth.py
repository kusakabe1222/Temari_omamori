"""ログイン（共通パスワード1つ）と、中継スクリプト用の API キーの確認。"""
from __future__ import annotations

import hashlib
import hmac
import time

from .config import Config

COOKIE_NAME = "omamori_session"
SESSION_SECONDS = 12 * 60 * 60  # 12時間でログインし直し


def _same(a: str, b: str) -> bool:
    # かかった時間から中身を推測されないよう、比べ方に hmac.compare_digest を使う
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def check_password(config: Config, password: str) -> bool:
    return _same(password, config.admin_password)


def is_valid_api_key(config: Config, key: str | None) -> bool:
    return bool(key) and _same(key, config.sync_api_key)


def _signature(config: Config, issued: str) -> str:
    # パスワードも鍵に混ぜるので、パスワードを変えると今のログインはすべて無効になる
    key = (config.session_secret + config.admin_password).encode("utf-8")
    return hmac.new(key, issued.encode("utf-8"), hashlib.sha256).hexdigest()


def make_session_token(config: Config, now: float | None = None) -> str:
    issued = str(int(now if now is not None else time.time()))
    return f"{issued}.{_signature(config, issued)}"


def is_valid_session(config: Config, token: str | None, now: float | None = None) -> bool:
    if not token or "." not in token:
        return False
    issued, signature = token.split(".", 1)
    if not _same(signature, _signature(config, issued)):
        return False
    try:
        age = (now if now is not None else time.time()) - int(issued)
    except ValueError:
        return False
    return 0 <= age <= SESSION_SECONDS
