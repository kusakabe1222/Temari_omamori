"""テストの準備。テストごとに一時フォルダに新しい DB を作るので、本番用の data/omamori.db には触らない。
LINE にも実際には送らない（LINE の入口は試し運転で動かす）。"""
from __future__ import annotations

import os
from dataclasses import replace

import pytest

PASSWORD = "test-password"
API_KEY = "test-key"
LINE_SECRET = "test-line-secret"

# app.main と app.bot_main は読み込んだときに設定を読むので、その前にテスト用の値を入れておく
os.environ.setdefault("ADMIN_PASSWORD", PASSWORD)
os.environ.setdefault("SYNC_API_KEY", API_KEY)
os.environ.setdefault("LINE_CHANNEL_SECRET", LINE_SECRET)
os.environ.setdefault("LINE_DRY_RUN", "1")

from fastapi.testclient import TestClient  # noqa: E402

from app import line_bot  # noqa: E402
from app.bot_main import create_bot_app  # noqa: E402
from app.config import Config  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture
def config(tmp_path) -> Config:
    return Config(admin_password=PASSWORD, sync_api_key=API_KEY, session_secret="test-secret",
                  db_path=tmp_path / "test.db", line_channel_secret=LINE_SECRET, line_dry_run=True)


@pytest.fixture
def client(config):
    """管理者用サイト。"""
    with TestClient(create_app(config)) as c:
        yield c


@pytest.fixture
def bot_client(config, client):
    """LINE の入口（管理者用サイトと同じ DB を使う）。"""
    with TestClient(create_bot_app(replace(config))) as c:
        yield c


@pytest.fixture(autouse=True)
def reset_link_failures():
    line_bot._link_failures.clear()
    yield
    line_bot._link_failures.clear()
