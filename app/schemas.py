"""API で受け取るデータの形（足りない値やおかしな値はここで弾く）。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# 端末 ID に使ってよい文字（英数字と _ - : .）。MAC アドレスもそのまま使える。
DEVICE_ID_PATTERN = r"^[A-Za-z0-9_\-:.]{1,64}$"


class StepRecordIn(BaseModel):
    measured_at: datetime                  # 例: 2026-10-16T09:00:00+09:00
    steps: int = Field(ge=0, le=100_000)   # その時間帯の歩数


class SyncIn(BaseModel):
    device_id: str = Field(pattern=DEVICE_ID_PATTERN)
    rssi: int | None = Field(default=None, ge=-127, le=20)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    records: list[StepRecordIn] = Field(default_factory=list, max_length=5000)
    status: Literal["ok", "failed"] = "ok"         # 中継側で BLE 同期に失敗したときは failed で送る
    error: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=200)   # 検証のメモ（例: 距離3m）
    source: str = Field(default="unknown", max_length=20)    # pc / tablet / fake など
