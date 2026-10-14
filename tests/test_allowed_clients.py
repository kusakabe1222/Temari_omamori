"""タブレットから見るための「開いてよい機器」の確認（ALLOWED_CLIENTS）。"""
from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import load_config
from app.main import client_checker, create_app
from tests.conftest import API_KEY, PASSWORD


def test_checker_allows_this_pc_and_listed_ranges():
    allowed = client_checker(("192.168.137.0/24", "10.0.0.5"))
    assert allowed("127.0.0.1") and allowed("::1")                 # この PC
    assert allowed("192.168.137.5") and allowed("10.0.0.5")       # .env に書いたもの
    assert allowed("::ffff:192.168.137.20")                       # IPv6 の形で届いた IPv4
    assert not allowed("172.18.40.20")                            # 大学の Wi-Fi など、ほかの機器
    assert not allowed("10.0.0.6") and not allowed("") and not allowed(None) and not allowed("testclient")


def test_default_is_this_pc_only():
    allowed = client_checker(())
    assert allowed("127.0.0.1")
    assert not allowed("192.168.137.5")


@pytest.mark.parametrize("host, expected", [("192.168.137.5", 200), ("127.0.0.1", 200), ("172.18.40.20", 403)])
def test_pages_by_client(config, host, expected):
    app = create_app(replace(config, allowed_clients=("192.168.137.0/24",)))
    with TestClient(app, client=(host, 50000)) as c:
        assert c.get("/login").status_code == expected
        res = c.post("/login", data={"password": PASSWORD}, follow_redirects=False)
        assert res.status_code == (303 if expected == 200 else 403)
        # 歩数を書き込む API も、ほかの機器からは API キーがあっても受けつけない
        sync = c.post("/api/sync", json={"device_id": "omamori-01"}, headers={"X-API-Key": API_KEY})
        assert sync.status_code == (200 if expected == 200 else 403)
        assert c.get("/static/style.css").status_code == expected


def test_load_config_reads_allowed_clients(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("ADMIN_PASSWORD=a\nSYNC_API_KEY=b\nALLOWED_CLIENTS=192.168.137.0/24, 10.0.0.5\n", encoding="utf-8")
    monkeypatch.delenv("ALLOWED_CLIENTS", raising=False)
    assert load_config(env).allowed_clients == ("192.168.137.0/24", "10.0.0.5")

    env.write_text("ADMIN_PASSWORD=a\nSYNC_API_KEY=b\nALLOWED_CLIENTS=tablet\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="ALLOWED_CLIENTS"):
        load_config(env)
