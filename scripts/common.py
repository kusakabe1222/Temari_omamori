"""scripts で共通に使う部品（API キーの読み込みと、/api/sync への送信）。"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from app.config import load_env_file

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SERVER = "http://127.0.0.1:8000"


def load_api_key() -> str:
    key = os.environ.get("SYNC_API_KEY") or load_env_file(ROOT / ".env").get("SYNC_API_KEY", "")
    if not key:
        raise SystemExit("SYNC_API_KEY が見つかりません。.env を作ってください（README の「準備」）。")
    return key


def post_sync(server: str, api_key: str, payload: dict, timeout: float = 15) -> dict:
    """サーバーの /api/sync に送る。送るのは .env で決めたサーバー（初期値はこの PC）だけ。"""
    request = urllib.request.Request(
        server.rstrip("/") + "/api/sync",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "X-API-Key": api_key},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def describe_result(result: dict) -> str:
    return (f"受け取り {result['received']}件 → 新規 {result['inserted']}件 / "
            f"重複でスキップ {result['skipped']}件 / 時刻エラー {result['invalid']}件")


def explain_error(error: Exception, server: str) -> str:
    if isinstance(error, urllib.error.HTTPError):
        if error.code == 401:
            return "API キーが違います。.env の SYNC_API_KEY を、サーバーと同じ値にしてください。"
        body = error.read().decode("utf-8", "replace")[:300]
        return f"サーバーがエラーを返しました（{error.code}）: {body}"
    if isinstance(error, urllib.error.URLError):
        return f"サーバーに接続できません（{error.reason}）。サーバーが起動しているか確かめてください: {server}"
    return f"{type(error).__name__}: {error}"
