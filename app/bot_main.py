"""LINE（本人用）の入口。管理者用サイトとは別のサーバーとして動かす。

起動（プロジェクトのフォルダで）:
    .\\.venv\\Scripts\\python.exe -m uvicorn app.bot_main:app --port 8001

インターネット（トンネル）に出すのは、このサーバー（8001）だけにする。
このサーバーにあるのは /line/webhook と /healthz だけで、管理者用サイトの画面や /api/sync はない。
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from . import db, line_api, line_bot
from .config import BOT_REQUIRED, Config, load_config

MAX_BODY = 1_000_000  # LINE から届くものはずっと小さい。大きすぎるものは読まない


def create_bot_app(config: Config | None = None) -> FastAPI:
    config = config or load_config(required=BOT_REQUIRED)
    if not config.line_bot_ready:
        raise RuntimeError(
            "LINE_CHANNEL_ACCESS_TOKEN が設定されていません。"
            "LINE に送らずに試すだけなら、.env に LINE_DRY_RUN=1 を入れてください（README の「LINE（本人用）」）。"
        )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        db.init_db(config.db_path)  # テーブルがなければ作る（データは消さない）
        if config.line_dry_run:
            print("[LINE] 試し運転中: LINE には返事を送りません", flush=True)
        yield

    app = FastAPI(title="お守り歩数計 LINE の入口", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.config = config

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.post("/line/webhook")
    async def webhook(request: Request, background: BackgroundTasks):
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > MAX_BODY:
            return JSONResponse({"detail": "大きすぎます"}, status_code=413)
        body = await request.body()
        if len(body) > MAX_BODY:
            return JSONResponse({"detail": "大きすぎます"}, status_code=413)
        # LINE から届いたものか、署名で確かめる（違えば何もしない）
        if not line_api.verify_signature(config.line_channel_secret, body, request.headers.get("X-Line-Signature")):
            return JSONResponse({"detail": "署名が正しくありません"}, status_code=401)
        try:
            payload = json.loads(body)
        except ValueError:
            return JSONResponse({"detail": "JSON ではありません"}, status_code=400)
        events = payload.get("events", []) if isinstance(payload, dict) else []
        if not isinstance(events, list):
            events = []
        if config.line_dry_run:
            # 試し運転: その場で処理して、返事を結果として返す（scripts/fake_line.py で表示する）
            replies = await run_in_threadpool(line_bot.handle_events, config, events)
            return {"status": "ok", "replies": replies}
        # LINE にはすぐ「受け取った」と返し、返事は後で送る
        background.add_task(line_bot.handle_events, config, events)
        return {"status": "ok"}

    return app


app = create_bot_app()
