"""API と画面の動作確認。実行: .\\.venv\\Scripts\\python.exe -m pytest"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import db
from tests.conftest import API_KEY
from tests.helpers import add_user, assign, login, query, rec, save_settings, sync, today_at, user_summary


# ---------------------------------------------------------------- POST /api/sync

def test_sync_needs_api_key(client):
    assert client.post("/api/sync", json={"device_id": "omamori-01"}).status_code == 401
    assert client.post("/api/sync", json={"device_id": "omamori-01"}, headers={"X-API-Key": "wrong"}).status_code == 401


def test_sync_saves_and_skips_duplicates(client):
    records = [rec(today_at(i), 100) for i in range(3)]
    first = sync(client, records=records).json()
    assert (first["received"], first["inserted"], first["skipped"]) == (3, 3, 0)

    second = sync(client, records=records).json()
    assert (second["inserted"], second["skipped"]) == (0, 3)

    # 同じ時刻を別の書き方（UTC）で送っても重複として扱う
    utc = datetime.fromisoformat(records[0]["measured_at"]).astimezone(timezone.utc).isoformat()
    assert sync(client, records=[rec(utc, 100)]).json()["skipped"] == 1

    assert query(client, "SELECT COUNT(*) AS n FROM step_records")[0]["n"] == 3
    assert query(client, "SELECT COUNT(*) AS n FROM sync_logs")[0]["n"] == 3
    # 登録していない端末は「持ち主なし」で登録される
    device = query(client, "SELECT * FROM devices WHERE device_id = 'omamori-01'")[0]
    assert device["user_id"] is None and device["last_seen_at"] is not None


def test_sync_rejects_records_with_wrong_clock(client):
    future = (db.now_jst() + timedelta(hours=2)).isoformat()
    result = sync(client, records=[rec(future, 10), rec("2000-01-01T00:00:00+09:00", 10)]).json()
    assert (result["inserted"], result["invalid"]) == (0, 2)
    log = query(client, "SELECT * FROM sync_logs")[0]
    assert "時刻" in log["error"] and log["skipped_count"] == 2


def test_sync_rejects_bad_input(client):
    assert sync(client, device="bad id!").status_code == 422
    assert sync(client, records=[rec(today_at(0), -1)]).status_code == 422


def test_sync_duration_keeps_milliseconds(client):
    # BLE の同期時間の検証に使うので、秒より細かい値を残す
    sync(client, records=[], started_at="2026-10-09T12:00:00.250+09:00", finished_at="2026-10-09T12:00:03.750+09:00")
    assert query(client, "SELECT started_at FROM sync_logs")[0]["started_at"] == "2026-10-09T12:00:00.250+09:00"
    login(client)
    lines = client.get("/api/export.csv?type=sync").content.decode("utf-8-sig").splitlines()
    assert ",3.5," in lines[1]
    assert client.get("/sync-logs").text.count("3.5秒") >= 1


def test_failed_sync_is_logged_without_records(client):
    result = sync(client, status="failed", error="接続が切れた", rssi=-90, note="距離10m").json()
    assert result["inserted"] == 0
    log = query(client, "SELECT * FROM sync_logs")[0]
    assert (log["status"], log["rssi"], log["note"]) == ("failed", -90, "距離10m")
    assert query(client, "SELECT COUNT(*) AS n FROM devices")[0]["n"] == 0


# ---------------------------------------------------------------- ログイン

def test_pages_and_api_need_login(client):
    res = client.get("/", follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"] == "/login"
    assert client.get("/api/summary").status_code == 401
    assert client.post("/login", data={"password": "wrong"}).status_code == 401

    login(client)
    assert client.get("/").status_code == 200
    assert client.get("/api/summary").status_code == 200
    # ログインしていても、歩数の書き込みは API キーがないとできない
    assert client.post("/api/sync", json={"device_id": "omamori-01"}).status_code == 401


def test_read_api_works_with_api_key(client):
    assert client.get("/api/summary", headers={"X-API-Key": API_KEY}).status_code == 200


def test_all_pages_render(client):
    sync(client, records=[rec(today_at(0), 100)])
    login(client)
    user_id = add_user(client, "さくら", 3000)
    assign(client, "omamori-01", user_id)
    for path in ["/", f"/users/{user_id}", "/manage", "/sync-logs", "/sync-logs?device=omamori-01", "/settings", "/export"]:
        res = client.get(path)
        assert res.status_code == 200, path
        assert "お守り歩数計" in res.text


# ---------------------------------------------------------------- 集計・アラート

def test_summary_daily_and_overload_alert(client):
    login(client)
    user_id = add_user(client, "さくら", 1000)  # 目標 2500歩
    sync(client, records=[rec(today_at(0), 1000)])
    assign(client, "omamori-01", user_id)  # 持ち主が決まる前に届いた記録も、この人のものになる

    user = user_summary(client, user_id)
    assert (user["today_steps"], user["goal_steps"], user["rate"]) == (1000, 2500, 40)
    assert not any(a["kind"] == "overload" for a in user["alerts"])

    sync(client, records=[rec(today_at(1), 3000)])  # 合計 4000歩 = 160%
    user = user_summary(client, user_id)
    assert user["rate"] == 160 and user["achieved"]
    assert any(a["kind"] == "overload" for a in user["alerts"])

    daily = client.get(f"/api/users/{user_id}/daily").json()
    assert len(daily["days"]) == 14
    assert daily["days"][-1]["steps"] == 4000
    assert daily["days"][0]["steps"] is None  # 記録のない日は 0 ではなく None


def test_daily_api_checks_dates(client):
    login(client)
    user_id = add_user(client, "さくら", 1000)
    assert client.get(f"/api/users/{user_id}/daily?from=2026-10-10&to=2026-10-01").status_code == 400
    assert client.get(f"/api/users/{user_id}/daily?from=2026-13-01").status_code == 400
    assert client.get("/api/users/9999/daily").status_code == 404
    res = client.get(f"/api/users/{user_id}/daily?from=2026-10-01&to=2026-10-07").json()
    assert [d["date"] for d in res["days"]][0] == "2026-10-01" and len(res["days"]) == 7


def test_no_sync_alert(client):
    login(client)
    user_id = add_user(client, "まつ", 1000)
    client.post("/devices", data={"device_id": "omamori-02", "user_id": str(user_id)})
    kinds = [a["kind"] for a in user_summary(client, user_id)["alerts"]]
    assert "no_sync" in kinds  # 一度も同期していない
    save_settings(client, no_sync_hours=0)
    assert user_summary(client, user_id)["alerts"] == []  # 0 にすると出さない


def test_reassigning_device_keeps_history(client):
    login(client)
    a = add_user(client, "Aさん", 1000)
    b = add_user(client, "Bさん", 1000)
    sync(client, records=[])
    assign(client, "omamori-01", a)
    sync(client, records=[rec(today_at(0), 500)])
    assign(client, "omamori-01", b)
    sync(client, records=[rec(today_at(1), 700)])
    assert user_summary(client, a)["today_steps"] == 500
    assert user_summary(client, b)["today_steps"] == 700


# ---------------------------------------------------------------- 登録・編集

def test_user_edit_and_validation(client):
    login(client)
    user_id = add_user(client, "さくら", 3000)
    assert query(client, "SELECT goal_steps FROM users")[0]["goal_steps"] == 4500

    client.post(f"/users/{user_id}", data={"display_name": "さくらさん", "baseline_steps": "2000"})
    row = query(client, "SELECT * FROM users")[0]
    assert (row["display_name"], row["goal_steps"]) == ("さくらさん", 3500)

    res = client.post("/users", data={"display_name": " ", "baseline_steps": "abc"}, follow_redirects=False)
    assert "msg=bad_input" in res.headers["location"]
    assert query(client, "SELECT COUNT(*) AS n FROM users")[0]["n"] == 1


def test_delete_user_needs_confirm_and_removes_records(client):
    login(client)
    user_id = add_user(client, "さくら", 1000)
    sync(client, records=[rec(today_at(0), 100)])
    assign(client, "omamori-01", user_id)

    client.post(f"/users/{user_id}/delete", data={})  # confirm がなければ消さない
    assert query(client, "SELECT COUNT(*) AS n FROM users")[0]["n"] == 1

    client.post(f"/users/{user_id}/delete", data={"confirm": "yes"})
    assert query(client, "SELECT COUNT(*) AS n FROM users")[0]["n"] == 0
    assert query(client, "SELECT COUNT(*) AS n FROM step_records")[0]["n"] == 0
    assert query(client, "SELECT user_id FROM devices")[0]["user_id"] is None


def test_device_register_and_delete_rules(client):
    login(client)
    assert "msg=device_added" in client.post("/devices", data={"device_id": "omamori-09"}, follow_redirects=False).headers["location"]
    assert "msg=device_exists" in client.post("/devices", data={"device_id": "omamori-09"}, follow_redirects=False).headers["location"]
    assert "msg=bad_device_id" in client.post("/devices", data={"device_id": "お守り"}, follow_redirects=False).headers["location"]

    sync(client, records=[rec(today_at(0), 100)])  # omamori-01 には記録がある
    with_records = query(client, "SELECT id FROM devices WHERE device_id = 'omamori-01'")[0]["id"]
    res = client.post(f"/devices/{with_records}/delete", data={"confirm": "yes"}, follow_redirects=False)
    assert "msg=device_has_records" in res.headers["location"]

    empty = query(client, "SELECT id FROM devices WHERE device_id = 'omamori-09'")[0]["id"]
    client.post(f"/devices/{empty}/delete", data={"confirm": "yes"})
    assert [r["device_id"] for r in query(client, "SELECT device_id FROM devices")] == ["omamori-01"]


def test_settings_validation(client):
    login(client)
    save_settings(client, overload_percent=120, no_sync_hours=48, steps_per_point=500, goal_bonus_points=3, gacha_cost=5)
    assert client.get("/api/summary").json()["settings"] == {
        "overload_percent": 120, "no_sync_hours": 48, "steps_per_point": 500, "goal_bonus_points": 3, "gacha_cost": 5}
    for bad in ({"overload_percent": 50}, {"steps_per_point": 0}, {"gacha_cost": 0}, {"goal_bonus_points": -1}):
        data = {key: str(value) for key, value in {**db.DEFAULT_SETTINGS, **bad}.items()}
        res = client.post("/settings", data=data, follow_redirects=False)
        assert "msg=bad_input" in res.headers["location"], bad
    assert client.get("/api/summary").json()["settings"]["overload_percent"] == 120  # 変わっていない


def test_admin_line_features_are_removed(client):
    login(client)
    assert client.post("/line/test").status_code in (404, 405)
    assert client.post("/line/summary").status_code in (404, 405)
    assert "LINEに今日のまとめを送る" not in client.get("/").text


# ---------------------------------------------------------------- CSV

def test_export_csv(client):
    login(client)
    user_id = add_user(client, "=さくら", 1000)  # Excel の式にならないことも確かめる
    sync(client, records=[rec(today_at(0), 100), rec(today_at(1), 200)], rssi=-60, note="距離1m")
    assign(client, "omamori-01", user_id)

    res = client.get("/api/export.csv?type=daily&from=&to=")
    assert res.status_code == 200 and res.content.startswith(b"\xef\xbb\xbf")
    lines = res.content.decode("utf-8-sig").splitlines()
    assert lines[0] == "date,user_id,display_name,steps,record_count,goal_steps,achieved,rate_percent,points"
    assert ",'=さくら,300," in lines[1] and lines[1].endswith(",0")  # 300歩では 0pt

    steps = client.get("/api/export.csv?type=steps").content.decode("utf-8-sig").splitlines()
    assert len(steps) == 3
    sync_csv = client.get("/api/export.csv?type=sync").content.decode("utf-8-sig")
    assert "距離1m" in sync_csv and "-60" in sync_csv
    gacha_csv = client.get("/api/export.csv?type=gacha").content.decode("utf-8-sig").splitlines()
    assert gacha_csv[0].startswith("id,drawn_at,user_id") and len(gacha_csv) == 1
    assert client.get("/api/export.csv?type=other").status_code == 400

