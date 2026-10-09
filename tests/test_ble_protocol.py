"""PC直結 BLE 中継の、データの読み方・命令の作り方の確認（実機がなくても動く）。"""
from __future__ import annotations

import struct
from datetime import datetime, timezone

import pytest

from scripts import ble_gateway as gw


def packet(*records: tuple[int, int]) -> bytes:
    return bytes([len(records)]) + b"".join(struct.pack("<IH", ts, steps) for ts, steps in records)


def test_decode_packet():
    ts = int(datetime(2026, 10, 16, 0, 0, tzinfo=timezone.utc).timestamp())
    assert gw.decode_packet(packet((ts, 120), (ts + 600, 0))) == [(ts, 120), (ts + 600, 0)]
    assert gw.decode_packet(b"\x00") == []  # 送り終わりの合図


@pytest.mark.parametrize("data", [b"", b"\x02" + b"\x00" * 6, b"\x01" + b"\x00" * 7])
def test_decode_packet_rejects_wrong_length(data):
    with pytest.raises(ValueError):
        gw.decode_packet(data)


def test_commands():
    assert gw.time_sync_command(0x01020304) == b"\x01\x04\x03\x02\x01"
    assert gw.ack_command(1) == b"\x03\x01\x00\x00\x00"


def test_records_to_json_uses_japan_time():
    ts = int(datetime(2026, 10, 16, 0, 0, tzinfo=timezone.utc).timestamp())
    assert gw.records_to_json([(ts, 120)]) == [{"measured_at": "2026-10-16T09:00:00+09:00", "steps": 120}]
