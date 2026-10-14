"""FastAPI アプリの本体。

起動（プロジェクトのフォルダで）:
    .\\.venv\\Scripts\\python.exe -m uvicorn app.main:app --reload
ブラウザで http://127.0.0.1:8000 を開く。
タブレットから見るときは README の「タブレットで管理者用サイトを見る」。
"""
from __future__ import annotations

import ipaddress
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import api, auth, db, pages
from .config import Config, load_config

STATIC_DIR = Path(__file__).with_name("static")
PUBLIC_PATHS = {"/login", "/healthz", "/favicon.ico"}  # ログインなしで開けるもの
LOCAL_NETWORKS = (ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("::1/128"))  # この PC
FORBIDDEN_PAGE = (
    "<!doctype html><meta charset='utf-8'><title>開けません</title>"
    "<p>この機器からは、お守り歩数計の管理者用サイトを開けません。</p>"
    "<p>使う機器の IP アドレスを、サーバーの PC の .env の ALLOWED_CLIENTS に入れてください（README の「タブレットで管理者用サイトを見る」）。</p>"
)


def client_checker(allowed_clients: tuple[str, ...]):
    """この PC と、ALLOWED_CLIENTS に書いた機器だけを通す関数を作る。"""
    networks = list(LOCAL_NETWORKS)
    names = set()  # IP アドレスでないもの（テストの "testclient" など）
    for item in allowed_clients:
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            names.add(item)

    def is_allowed(host: str | None) -> bool:
        if not host:
            return False
        if host in names:
            return True
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return False
        if ip.version == 6 and ip.ipv4_mapped:  # ::ffff:192.168.137.5 の形
            ip = ip.ipv4_mapped
        return any(ip in network for network in networks)

    return is_allowed


def create_app(config: Config | None = None) -> FastAPI:
    config = config or load_config()
    is_allowed_client = client_checker(config.allowed_clients)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        db.init_db(config.db_path)  # テーブルがなければ作る（データは消さない）
        yield

    # /docs（Swagger UI）は CDN から読み込むので使わない
    app = FastAPI(title="お守り型歩数計 管理者用サイト", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.config = config

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        # まず、開いてよい機器か確かめる（--host 0.0.0.0 で起動しても、ほかの機器には何も見せない）
        if not is_allowed_client(request.client.host if request.client else None):
            return HTMLResponse(FORBIDDEN_PAGE, status_code=403)
        path = request.url.path
        if path.startswith("/static/"):
            response = await call_next(request)
            # CSS や JS を直したとき、古いものがブラウザに残らないよう毎回サーバーに確かめさせる
            response.headers["Cache-Control"] = "no-cache"
            return response
        if path in PUBLIC_PATHS:
            return await call_next(request)
        logged_in = auth.is_valid_session(config, request.cookies.get(auth.COOKIE_NAME))
        if path.startswith("/api/"):
            has_key = auth.is_valid_api_key(config, request.headers.get("X-API-Key"))
            # 歩数を書き込む /api/sync は API キーだけ。読むだけの API はログインしていても使える。
            if has_key or (logged_in and path != "/api/sync"):
                return await call_next(request)
            return JSONResponse({"detail": "ログインか API キー（X-API-Key）が必要です"}, status_code=401)
        if logged_in:
            return await call_next(request)
        return RedirectResponse("/login", status_code=303)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "time": db.to_jst_text(db.now_jst())}

    @app.get("/favicon.ico")
    def favicon() -> Response:
        return Response(status_code=204)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(api.router)
    app.include_router(pages.router)
    return app


app = create_app()
