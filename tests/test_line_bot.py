"""本人用 LINE Bot・ポイント・ガチャの確認。LINE には送らない（試し運転で返事を受け取る）。"""
from __future__ import annotations

import json

from app import db, line_api, points
from tests.helpers import (add_user, assign, login, make_code, post_events, query, rec, save_settings, say,
                           sync, text_event, today_at)


def setup_user(client, baseline=1000, steps_today=0):
    """利用者を1人作り、端末 omamori-01 を割り当てる（目標 = baseline + 1500）。"""
    login(client)
    user_id = add_user(client, "さくら", baseline)
    sync(client, records=[rec(today_at(0), steps_today)] if steps_today else [])
    assign(client, "omamori-01", user_id)
    return user_id


# ---------------------------------------------------------------- 入口（署名）

def test_webhook_checks_signature(bot_client):
    body = json.dumps({"events": []}).encode()
    assert bot_client.post("/line/webhook", content=body).status_code == 401
    assert bot_client.post("/line/webhook", content=body, headers={"X-Line-Signature": "wrong"}).status_code == 401
    assert post_events(bot_client, [], secret="other-secret").status_code == 401
    # LINE Developers の「検証」ボタンは、イベントなしで送ってくる
    assert post_events(bot_client, []).status_code == 200


def test_bot_app_has_only_webhook(bot_client):
    # LINE の入口だけをインターネットに出すので、管理者用サイトの画面や API はここにはない
    for path in ["/", "/manage", "/api/summary", "/api/export.csv", "/static/style.css"]:
        assert bot_client.get(path).status_code == 404, path
    assert bot_client.post("/api/sync", json={}).status_code == 404


def test_signature_helper_matches_line_spec():
    # LINE の説明どおり: base64(HMAC-SHA256(チャネルシークレット, 本文))
    assert line_api.verify_signature("secret", b"body", line_api.sign("secret", b"body"))
    assert not line_api.verify_signature("secret", b"body2", line_api.sign("secret", b"body"))
    assert not line_api.verify_signature("", b"body", "x")


# ---------------------------------------------------------------- 登録

def test_not_linked_user_is_told_how_to_register(client, bot_client):
    assert "まだ登録されていません" in say(bot_client, "今日")
    res = post_events(bot_client, [{"type": "follow", "source": {"type": "user", "userId": "U-new"}, "replyToken": "t"}])
    assert "友だち追加ありがとうございます" in res.json()["replies"][0]["messages"][0]["text"]


def test_register_with_code_including_fullwidth(client, bot_client):
    user_id = setup_user(client)
    code = make_code(client, user_id)
    zenkaku = code.translate(str.maketrans("0123456789", "０１２３４５６７８９"))
    reply = say(bot_client, f"登録　{zenkaku}")  # 全角の数字と空白でもよい
    assert "さくらさん" in reply and "登録しました" in reply
    assert query(client, "SELECT user_id FROM line_links")[0]["user_id"] == user_id
    # 家族も同じ番号で登録できる
    say(bot_client, f"登録 {code}", user="U-family")
    assert query(client, "SELECT COUNT(*) AS n FROM line_links WHERE user_id = ?", (user_id,))[0]["n"] == 2


def test_wrong_codes_are_limited(client, bot_client):
    user_id = setup_user(client)
    code = make_code(client, user_id)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        assert "見つかりませんでした" in say(bot_client, f"登録 {wrong}")
    assert "1時間ほど" in say(bot_client, f"登録 {code}")  # 5回まちがえたら、正しい番号でもしばらく受けつけない
    assert query(client, "SELECT COUNT(*) AS n FROM line_links")[0]["n"] == 0


def test_reissued_code_replaces_old_one(client, bot_client):
    user_id = setup_user(client)
    old = make_code(client, user_id)
    new = make_code(client, user_id)
    if old != new:
        assert "見つかりませんでした" in say(bot_client, f"登録 {old}")
    assert "登録しました" in say(bot_client, f"登録 {new}")


def test_unlink_by_message_unfollow_and_admin(client, bot_client):
    user_id = setup_user(client)
    code = make_code(client, user_id)
    say(bot_client, f"登録 {code}")
    assert "解除しました" in say(bot_client, "解除")
    assert "まだ登録されていません" in say(bot_client, "今日")

    say(bot_client, f"登録 {code}")
    post_events(bot_client, [{"type": "unfollow", "source": {"type": "user", "userId": "U-test-0001"}}])  # ブロック
    assert query(client, "SELECT COUNT(*) AS n FROM line_links")[0]["n"] == 0

    say(bot_client, f"登録 {code}")
    link_id = query(client, "SELECT id FROM line_links")[0]["id"]
    client.post(f"/users/{user_id}/line-links/{link_id}/delete", data={})  # confirm がなければ消さない
    assert query(client, "SELECT COUNT(*) AS n FROM line_links")[0]["n"] == 1
    client.post(f"/users/{user_id}/line-links/{link_id}/delete", data={"confirm": "yes"})
    assert query(client, "SELECT COUNT(*) AS n FROM line_links")[0]["n"] == 0


def test_group_chat_is_not_supported(client, bot_client):
    event = text_event("今日")
    event["source"] = {"type": "group", "groupId": "C1", "userId": "U-test-0001"}
    res = post_events(bot_client, [event])
    assert "1対1" in res.json()["replies"][0]["messages"][0]["text"]


# ---------------------------------------------------------------- ことば

def test_commands_reply_with_quick_buttons(client, bot_client):
    user_id = setup_user(client, baseline=1000, steps_today=3000)  # 目標 2500歩
    say(bot_client, f"登録 {make_code(client, user_id)}")
    today = say(bot_client, "今日")
    assert "3,000歩" in today and "目標を達成しました" in today and "120%" in today
    assert "記録なし" in say(bot_client, "1週間") and "3,000歩 ✅" in say(bot_client, "1週間")
    assert "合計 3,000歩" in say(bot_client, "1か月")
    assert "1日の目標 2,500歩" in say(bot_client, "目標")
    assert "お守り歩数計です" in say(bot_client, "こんにちは")  # 知らないことばには使い方を返す

    res = post_events(bot_client, [text_event("ポイント")])
    message = res.json()["replies"][0]["messages"][0]
    labels = [item["action"]["label"] for item in message["quickReply"]["items"]]
    assert labels == ["今日", "1週間", "1か月", "ポイント", "ガチャ", "目標"]


def test_overload_message_in_today(client, bot_client):
    user_id = setup_user(client, baseline=0, steps_today=3000)  # 目標 1500歩の 200%
    say(bot_client, f"登録 {make_code(client, user_id)}")
    assert "無理をせず" in say(bot_client, "今日")


# ---------------------------------------------------------------- ポイント・ガチャ

def test_day_points_rules():
    settings = dict(db.DEFAULT_SETTINGS)  # 1,000歩で1pt、目標達成で+2pt、目標の150%で頭打ち、ガチャ10pt
    assert points.day_points(999, 4000, settings) == 0
    assert points.day_points(3500, 4000, settings) == 3
    assert points.day_points(4000, 4000, settings) == 4 + 2           # 目標達成のボーナス
    assert points.day_points(6000, 4000, settings) == 6 + 2           # 150% = 6000歩まで
    assert points.day_points(12000, 4000, settings) == 6 + 2          # それより多く歩いても増えない


def test_gacha_uses_points_and_is_recorded(client, bot_client):
    user_id = setup_user(client, baseline=0, steps_today=1000)  # 目標 1500歩。1000歩 = 1pt
    say(bot_client, f"登録 {make_code(client, user_id)}")
    assert "ポイントが足りません" in say(bot_client, "ガチャ")
    assert query(client, "SELECT COUNT(*) AS n FROM gacha_draws")[0]["n"] == 0

    save_settings(client, steps_per_point=100)  # 1000歩で 10pt になる
    reply = say(bot_client, "ガチャ")
    assert "ガチャの結果" in reply and "のこり 0pt" in reply
    draw = query(client, "SELECT * FROM gacha_draws")[0]
    assert draw["points_used"] == 10 and draw["rarity"] in ("common", "rare", "super_rare")
    assert "いま使えるポイント 0pt" in say(bot_client, "ポイント")

    # 管理者用サイトの個人ページにも出る
    page = client.get(f"/users/{user_id}").text
    assert draw["message"] in page and "ガチャを回した回数" in page


def test_redelivered_event_does_not_spin_twice(client, bot_client):
    user_id = setup_user(client, baseline=0, steps_today=2000)
    save_settings(client, steps_per_point=100)  # 2000歩 → 20pt ＋ 目標達成のボーナス 2pt
    say(bot_client, f"登録 {make_code(client, user_id)}")
    event = text_event("ガチャ", event_id="01HXYZ-same-event")
    post_events(bot_client, [event])
    res = post_events(bot_client, [event])  # LINE が送り直してきた
    assert res.json()["replies"] == []
    assert query(client, "SELECT COUNT(*) AS n FROM gacha_draws")[0]["n"] == 1


def test_points_never_negative_after_rule_change(client, bot_client):
    user_id = setup_user(client, baseline=0, steps_today=1000)
    save_settings(client, steps_per_point=100)
    say(bot_client, f"登録 {make_code(client, user_id)}")
    say(bot_client, "ガチャ")
    save_settings(client, steps_per_point=1000)  # ルールを戻すと、もらった分 1pt < 使った分 10pt
    assert "いま使えるポイント 0pt" in say(bot_client, "ポイント")


def test_delete_user_removes_line_and_gacha(client, bot_client):
    user_id = setup_user(client, baseline=0, steps_today=1000)
    save_settings(client, steps_per_point=100)
    say(bot_client, f"登録 {make_code(client, user_id)}")
    say(bot_client, "ガチャ")
    client.post(f"/users/{user_id}/delete", data={"confirm": "yes"})
    for table in ["line_links", "line_link_codes", "gacha_draws"]:
        assert query(client, f"SELECT COUNT(*) AS n FROM {table}")[0]["n"] == 0, table


def test_user_page_shows_link_code(client):
    user_id = setup_user(client)
    assert "登録の番号を作る" in client.get(f"/users/{user_id}").text
    code = make_code(client, user_id)
    assert f"登録 {code}" in client.get(f"/users/{user_id}").text
