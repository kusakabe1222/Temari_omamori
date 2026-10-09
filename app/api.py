"""API（/api/...）。

- POST /api/sync は中継スクリプト（PC・タブレット）が使う。X-API-Key ヘッダーが必要。
- それ以外は、ログインした画面か X-API-Key があれば使える。
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import Response

from . import db, export, line_notify, services
from .deps import Conn
from .schemas import SyncIn

router = APIRouter(prefix="/api")

MAX_DAYS = 366


def _parse_date(text: str | None, name: str) -> date | None:
    # 画面のフォームから空の値が来ることがあるので、空は「指定なし」として扱う
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise HTTPException(400, f"{name} は 2026-10-16 の形で指定してください")


@router.post("/sync")
def sync(data: SyncIn, request: Request, background: BackgroundTasks, conn: Conn) -> dict:
    """中継から歩数をまとめて受け取る。返り値: 受け取った件数・新しく保存した件数・重複でスキップした件数。"""
    result = services.ingest_sync(conn, data, db.now_jst())
    if result["inserted"] and result["user_id"] is not None:
        # LINE への送信は時間がかかることがあるので、返事をしてから行う
        background.add_task(line_notify.notify_overload_if_needed, request.app.state.config, result["user_id"])
    return result


@router.get("/users")
def users(conn: Conn) -> list[dict]:
    return services.user_overview(conn, db.now_jst())


@router.get("/users/{user_id}/daily")
def user_daily(user_id: int, conn: Conn,
               from_: str | None = Query(None, alias="from"), to: str | None = None) -> dict:
    """日別歩数。期間を省くと、今日までの14日間。"""
    user = services.get_user(conn, user_id)
    if user is None:
        raise HTTPException(404, "利用者が見つかりません")
    end = _parse_date(to, "to") or db.now_jst().date()
    start = _parse_date(from_, "from") or end - timedelta(days=13)
    if start > end:
        raise HTTPException(400, "from は to より前の日にしてください")
    if (end - start).days >= MAX_DAYS:
        raise HTTPException(400, f"期間は {MAX_DAYS} 日以内にしてください")
    return {
        "user": {"id": user["id"], "display_name": user["display_name"],
                 "baseline_steps": user["baseline_steps"], "goal_steps": user["goal_steps"]},
        "from": start.isoformat(),
        "to": end.isoformat(),
        "days": services.daily_steps(conn, user_id, start, end),
    }


@router.get("/summary")
def summary(conn: Conn) -> dict:
    return services.summary(conn, db.now_jst())


@router.get("/export.csv")
def export_csv(conn: Conn, kind: str = Query("daily", alias="type"),
               from_: str | None = Query(None, alias="from"), to: str | None = None) -> Response:
    """CSV を返す。type: daily（日別歩数）/ steps（細かい記録）/ sync（同期の記録）。日付を省くと全期間。"""
    if kind not in export.KINDS:
        raise HTTPException(400, f"type は {' / '.join(export.KINDS)} のどれかにしてください")
    start, end = _parse_date(from_, "from"), _parse_date(to, "to")
    header, rows = export.build(conn, kind, start, end)
    filename = f"omamori_{kind}_{db.now_jst():%Y%m%d_%H%M}.csv"
    return Response(
        content=export.to_csv_bytes(header, rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
