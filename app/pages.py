"""管理者用サイトの画面（HTML）。

1. 利用者一覧  2. 個人ページ  3. 利用者・端末の登録と編集  4. 設定  5. CSV エクスポート
＋ 同期の記録（BLE 検証用）とログイン。
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from . import auth, db, gacha, line_links, points, services
from .deps import Conn
from .schemas import DEVICE_ID_PATTERN

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).with_name("templates"))

WEEKDAYS = "月火水木金土日"
MAX_NAME_LENGTH = 30
MAX_BASELINE = 50_000

# 処理のあと画面の上に出すメッセージ。URL には文章ではなく記号（msg=...）だけを載せる。
MESSAGES = {
    "user_added": ("ok", "利用者を登録しました。"),
    "user_saved": ("ok", "利用者の内容を保存しました。"),
    "user_deleted": ("ok", "利用者を削除しました。"),
    "device_added": ("ok", "端末を登録しました。"),
    "device_saved": ("ok", "端末の持ち主を保存しました。"),
    "device_deleted": ("ok", "端末を削除しました。"),
    "device_exists": ("error", "その端末IDはすでに登録されています。"),
    "device_has_records": ("error", "歩数の記録がある端末は削除できません。持ち主を「なし」にしてください。"),
    "bad_device_id": ("error", "端末IDには英数字と _ - : . だけを使ってください（64文字まで）。"),
    "bad_input": ("error", "入力を確かめてください。"),
    "not_found": ("error", "見つかりませんでした。"),
    "settings_saved": ("ok", "設定を保存しました。"),
    "link_code_issued": ("ok", "LINE の登録の番号を作りました。前の番号は使えなくなりました。"),
    "line_unlinked": ("ok", "LINE の登録を解除しました。"),
}


# ---------------------------------------------------------------- テンプレートで使う表示の形

def _dt(value: str | None, fmt: str = "%m/%d %H:%M") -> str:
    return datetime.fromisoformat(value).strftime(fmt) if value else ""


def _num(value) -> str:
    return "—" if value is None else f"{int(value):,}"


def _jdate(value: str) -> str:
    day = date.fromisoformat(value[:10])
    return f"{day.month}月{day.day}日（{WEEKDAYS[day.weekday()]}）"


def _sec(value) -> str:
    return "—" if value is None else f"{value:.1f}秒"


templates.env.filters.update(dt=_dt, num=_num, jdate=_jdate, sec=_sec)


def render(request: Request, name: str, active: str | None, status_code: int = 200, **context) -> Response:
    level_text = MESSAGES.get(request.query_params.get("msg", ""))
    return templates.TemplateResponse(
        request,
        name,
        {
            "active": active,
            "flash": {"level": level_text[0], "text": level_text[1]} if level_text else None,
            "line_bot_ready": request.app.state.config.line_bot_ready,
            **context,
        },
        status_code=status_code,
    )


def back(path: str, msg: str) -> RedirectResponse:
    return RedirectResponse(f"{path}?msg={msg}", status_code=303)


def _clean_name(text: str) -> str | None:
    name = text.strip()
    return name if 1 <= len(name) <= MAX_NAME_LENGTH else None


def _int_in_range(text: str, low: int, high: int) -> int | None:
    try:
        value = int(text.strip())
    except ValueError:
        return None
    return value if low <= value <= high else None


def _user_id_or_none(conn, text: str) -> int | None | bool:
    """フォームの持ち主欄。空なら None、正しい利用者なら id、おかしければ False。"""
    if not text:
        return None
    if not text.isdigit() or services.get_user(conn, int(text)) is None:
        return False
    return int(text)


# ---------------------------------------------------------------- ログイン

@router.get("/login")
def login_page(request: Request):
    return render(request, "login.html", None, hide_nav=True, error=False)


@router.post("/login")
def login(request: Request, password: Annotated[str, Form()] = ""):
    config = request.app.state.config
    if not auth.check_password(config, password):
        return render(request, "login.html", None, status_code=401, hide_nav=True, error=True)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(auth.COOKIE_NAME, auth.make_session_token(config),
                        max_age=auth.SESSION_SECONDS, httponly=True, samesite="lax")
    return response


@router.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.COOKIE_NAME)
    return response


# ---------------------------------------------------------------- 1. 利用者一覧

@router.get("/")
def index(request: Request, conn: Conn):
    return render(request, "index.html", "index", summary=services.summary(conn, db.now_jst()))


# ---------------------------------------------------------------- 2. 個人ページ

@router.get("/users/{user_id}")
def user_detail(request: Request, user_id: int, conn: Conn):
    now = db.now_jst()
    user = next((u for u in services.user_overview(conn, now) if u["id"] == user_id), None)
    if user is None:
        return back("/", "not_found")
    return render(
        request, "user_detail.html", "index",
        user=user,
        logs=services.recent_sync_logs(conn, device_ids=user["devices"], limit=10),
        today=now.date().isoformat(),
        overload_percent=db.get_settings(conn)["overload_percent"],
        goal_increment=services.GOAL_INCREMENT,
        points=points.summary(conn, user_id, now.date()),
        draws=gacha.recent_draws(conn, user_id, limit=10),
        link_code=line_links.get_code(conn, user_id),
        line_links=line_links.links_for_user(conn, user_id),
    )


@router.post("/users/{user_id}/link-code")
def issue_link_code(user_id: int, conn: Conn):
    if services.get_user(conn, user_id) is None:
        return back("/", "not_found")
    line_links.issue_code(conn, user_id, db.now_jst())
    return back(f"/users/{user_id}", "link_code_issued")


@router.post("/users/{user_id}/line-links/{link_id}/delete")
def unlink_line(user_id: int, link_id: int, conn: Conn, confirm: Annotated[str, Form()] = ""):
    if confirm != "yes":
        return back(f"/users/{user_id}", "bad_input")
    if not line_links.unlink_by_id(conn, user_id, link_id):
        return back(f"/users/{user_id}", "not_found")
    return back(f"/users/{user_id}", "line_unlinked")


# ---------------------------------------------------------------- 3. 利用者・端末の登録と編集

@router.get("/manage")
def manage(request: Request, conn: Conn):
    users = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    devices = conn.execute(
        """SELECT d.*, u.display_name FROM devices d LEFT JOIN users u ON u.id = d.user_id
           ORDER BY d.user_id IS NOT NULL, d.device_id"""  # 持ち主なしの端末を先に出す
    ).fetchall()
    record_counts = dict(conn.execute("SELECT device_id, COUNT(*) FROM step_records GROUP BY device_id").fetchall())
    return render(request, "manage.html", "manage", users=users, devices=devices,
                  record_counts=record_counts, goal_increment=services.GOAL_INCREMENT)


@router.post("/users")
def create_user(conn: Conn, display_name: Annotated[str, Form()] = "", baseline_steps: Annotated[str, Form()] = ""):
    name = _clean_name(display_name)
    baseline = _int_in_range(baseline_steps, 0, MAX_BASELINE)
    if name is None or baseline is None:
        return back("/manage", "bad_input")
    services.create_user(conn, name, baseline, db.now_jst())
    return back("/manage", "user_added")


@router.post("/users/{user_id}")
def update_user(user_id: int, conn: Conn,
                display_name: Annotated[str, Form()] = "", baseline_steps: Annotated[str, Form()] = ""):
    name = _clean_name(display_name)
    baseline = _int_in_range(baseline_steps, 0, MAX_BASELINE)
    if name is None or baseline is None:
        return back("/manage", "bad_input")
    if not services.update_user(conn, user_id, name, baseline):
        return back("/manage", "not_found")
    return back("/manage", "user_saved")


@router.post("/users/{user_id}/delete")
def delete_user(user_id: int, conn: Conn, confirm: Annotated[str, Form()] = ""):
    # 画面の確認ダイアログで「OK」を押したときだけ confirm=yes が届く
    if confirm != "yes":
        return back("/manage", "bad_input")
    if not services.delete_user(conn, user_id):
        return back("/manage", "not_found")
    return back("/manage", "user_deleted")


@router.post("/devices")
def create_device(conn: Conn, device_id: Annotated[str, Form()] = "", user_id: Annotated[str, Form()] = ""):
    device_id = device_id.strip()
    if not re.fullmatch(DEVICE_ID_PATTERN, device_id):
        return back("/manage", "bad_device_id")
    owner = _user_id_or_none(conn, user_id)
    if owner is False:
        return back("/manage", "bad_input")
    if not services.create_device(conn, device_id, owner):
        return back("/manage", "device_exists")
    return back("/manage", "device_added")


@router.post("/devices/{device_pk}")
def update_device(device_pk: int, conn: Conn, user_id: Annotated[str, Form()] = ""):
    owner = _user_id_or_none(conn, user_id)
    if owner is False:
        return back("/manage", "bad_input")
    if not services.assign_device(conn, device_pk, owner):
        return back("/manage", "not_found")
    return back("/manage", "device_saved")


@router.post("/devices/{device_pk}/delete")
def delete_device(device_pk: int, conn: Conn, confirm: Annotated[str, Form()] = ""):
    if confirm != "yes":
        return back("/manage", "bad_input")
    result = services.delete_device(conn, device_pk)
    return back("/manage", {"ok": "device_deleted", "has_records": "device_has_records"}.get(result, "not_found"))


# ---------------------------------------------------------------- 同期の記録（BLE 検証用）

@router.get("/sync-logs")
def sync_logs(request: Request, conn: Conn, device: str = ""):
    device_ids = [row[0] for row in conn.execute("SELECT DISTINCT device_id FROM sync_logs ORDER BY device_id")]
    if device not in device_ids:
        device = ""
    return render(
        request, "sync_logs.html", "sync",
        logs=services.recent_sync_logs(conn, device_ids=[device] if device else None, limit=200),
        stats=services.sync_stats(conn),
        device_ids=device_ids,
        selected=device,
    )


# ---------------------------------------------------------------- 4. 設定

@router.get("/settings")
def settings_page(request: Request, conn: Conn):
    linked = conn.execute("SELECT COUNT(DISTINCT user_id) AS users, COUNT(*) AS accounts FROM line_links").fetchone()
    return render(request, "settings.html", "settings", settings=db.get_settings(conn),
                  linked_users=linked["users"], linked_accounts=linked["accounts"])


# 設定の欄: (名前, 下限, 上限)
SETTING_RANGES = {
    "overload_percent": (100, 500),
    "no_sync_hours": (0, 720),
    "steps_per_point": (100, 10_000),
    "goal_bonus_points": (0, 100),
    "gacha_cost": (1, 1_000),
}


@router.post("/settings")
def save_settings(conn: Conn,
                  overload_percent: Annotated[str, Form()] = "",
                  no_sync_hours: Annotated[str, Form()] = "",
                  steps_per_point: Annotated[str, Form()] = "",
                  goal_bonus_points: Annotated[str, Form()] = "",
                  gacha_cost: Annotated[str, Form()] = ""):
    texts = {"overload_percent": overload_percent, "no_sync_hours": no_sync_hours, "steps_per_point": steps_per_point,
             "goal_bonus_points": goal_bonus_points, "gacha_cost": gacha_cost}
    values = {key: _int_in_range(text, *SETTING_RANGES[key]) for key, text in texts.items()}
    if any(value is None for value in values.values()):
        return back("/settings", "bad_input")
    db.save_settings(conn, values)
    return back("/settings", "settings_saved")


# ---------------------------------------------------------------- 5. CSV エクスポート

@router.get("/export")
def export_page(request: Request):
    today = db.now_jst().date()
    return render(request, "export.html", "export",
                  today=today.isoformat(), default_from=(today - timedelta(days=29)).isoformat())
