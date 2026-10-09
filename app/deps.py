"""画面と API で共通に使う部品。"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request

from . import db


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    """リクエストごとに DB に接続し、終わったら閉じる。"""
    conn = db.connect(request.app.state.config.db_path)
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]
