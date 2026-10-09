"""テストの準備。テストごとに一時フォルダに新しい DB を作るので、本番用の data/omamori.db には触らない。"""
from __future__ import annotations

import os
from dataclasses import replace

import pytest

# app.main は読み込んだときに設定を読むので、その前にテスト用の値を入れておく
os.environ.setdefault("ADMIN_PASSWORD", "test-password")
os.environ.setdefault("SYNC_API_KEY", "test-key")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import Config  # noqa: E402
from app.main import create_app  # noqa: E402

PASSWORD = "test-password"
API_KEY = "test-key"


@pytest.fixture
def config(tmp_path) -> Config:
    return Config(admin_password=PASSWORD, sync_api_key=API_KEY, session_secret="test-secret",
                  db_path=tmp_path / "test.db")


@pytest.fixture
def client(config):
    with TestClient(create_app(config)) as c:
        yield c


@pytest.fixture
def line_client(config):
    """LINE の設定が入っている状態（実際には送らないよう、テストの中で send_text を置きかえる）。"""
    with TestClient(create_app(replace(config, line_channel_access_token="dummy-token", line_to="U-dummy"))) as c:
        yield c
