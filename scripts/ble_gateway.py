"""PC直結の中継スクリプト: お守り端末（M5StickS3）から BLE で歩数を受け取り、サーバーの /api/sync に送る。

タブレットの代わりに、サーバーと同じ PC で動かす。
通信の決まり（UUID・データの形・手順）は docs/ble_protocol.md。端末側のプログラムと必ずそろえること。
必要なライブラリ: .\\.venv\\Scripts\\python.exe -m pip install -r requirements-ble.txt

使い方（サーバーを起動したまま、別の PowerShell で）:
    .\\.venv\\Scripts\\python.exe scripts\\ble_gateway.py --scan                   # 近くの BLE 機器と電波の強さを一覧
    .\\.venv\\Scripts\\python.exe scripts\\ble_gateway.py --once                   # 見つかったお守り端末と1回だけ同期
    .\\.venv\\Scripts\\python.exe scripts\\ble_gateway.py                          # 5分ごとに同期し続ける（Ctrl+C で止める）
    .\\.venv\\Scripts\\python.exe scripts\\ble_gateway.py --once --expect omamori-01 --note "距離3m"
        # 距離の検証用。omamori-01 が見つからなかったときも「失敗」として同期記録に残す
"""
from __future__ import annotations

import argparse
import asyncio
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from scripts.common import DEFAULT_SERVER, describe_result, explain_error, load_api_key, post_sync  # noqa: E402

# ---- 通信の決まり（docs/ble_protocol.md と同じ値にすること） ----
SERVICE_UUID = "9a680eec-1bbc-4a41-8d1a-5825119870c1"
DATA_CHAR_UUID = "49501394-5798-42c9-a6da-da5ca2b387b3"  # 端末 → PC（notify）: 歩数の記録
CTRL_CHAR_UUID = "33eeafb4-bd92-45e4-a92d-925ebcc8835d"  # PC → 端末（write）: 命令
CMD_TIME_SYNC = 0x01  # + uint32 今の UNIX 時刻（秒）: 端末の時計を合わせる
CMD_SEND = 0x02       # まだ送っていない記録を送ってもらう
CMD_ACK = 0x03        # + uint32 保存できた最後の measured_at: 端末はそこまでの記録を消してよい
RECORD = struct.Struct("<IH")  # 1件 = 測った時刻（UNIX 秒・uint32）＋ 歩数（uint16）、リトルエンディアン


def decode_packet(data: bytes) -> list[tuple[int, int]]:
    """DATA の通知1つを [(UNIX秒, 歩数), ...] にする。件数が 0 の通知は「送り終わり」の合図。"""
    if not data:
        raise ValueError("空のパケットです")
    count = data[0]
    expected = 1 + count * RECORD.size
    if len(data) != expected:
        raise ValueError(f"パケットの長さが合いません（{count}件なら {expected} バイト、実際は {len(data)} バイト）")
    return [RECORD.unpack_from(data, 1 + i * RECORD.size) for i in range(count)]


def time_sync_command(unix_seconds: int) -> bytes:
    return struct.pack("<BI", CMD_TIME_SYNC, unix_seconds)


def ack_command(last_unix_seconds: int) -> bytes:
    return struct.pack("<BI", CMD_ACK, last_unix_seconds)


def records_to_json(records: list[tuple[int, int]]) -> list[dict]:
    return [{"measured_at": db.to_jst_text(datetime.fromtimestamp(ts, tz=timezone.utc)), "steps": steps}
            for ts, steps in records]


def log(message: str) -> None:
    print(f"[{db.now_jst():%H:%M:%S}] {message}", flush=True)


def precise_now() -> datetime:
    """同期時間を測るので、ミリ秒まで持った今の時刻。"""
    return datetime.now(db.JST)


async def scan(prefix: str | None, timeout: float) -> list[tuple[object, str, int]]:
    """近くの BLE 機器を探す。戻り値: [(機器, 名前, RSSI), ...]"""
    from bleak import BleakScanner

    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    result = []
    for device, adv in found.values():
        name = adv.local_name or device.name or ""
        if prefix is None or name.startswith(prefix):
            result.append((device, name, adv.rssi))
    return sorted(result, key=lambda item: item[1])


async def report_failure(args, api_key: str, device_id: str, rssi: int | None,
                         started: datetime, error: str) -> None:
    payload = {"device_id": device_id, "source": "pc", "status": "failed", "rssi": rssi,
               "started_at": db.to_jst_text_ms(started), "finished_at": db.to_jst_text_ms(precise_now()),
               "note": args.note, "error": error[:500], "records": []}
    try:
        await asyncio.to_thread(post_sync, args.server, api_key, payload)
    except Exception as e:  # noqa: BLE001
        log(f"  失敗の記録もサーバーに送れませんでした: {explain_error(e, args.server)}")


async def sync_device(device, device_id: str, rssi: int, args, api_key: str) -> None:
    from bleak import BleakClient

    started = precise_now()
    records: list[tuple[int, int]] = []
    problems: list[str] = []
    done = asyncio.Event()

    def on_data(_sender, data: bytearray) -> None:
        if done.is_set():
            return
        try:
            batch = decode_packet(bytes(data))
        except ValueError as e:
            problems.append(str(e))
            done.set()
            return
        if batch:
            records.extend(batch)
        else:
            done.set()

    log(f"{device_id}（RSSI {rssi}）に接続します")
    try:
        async with BleakClient(device, timeout=args.connect_timeout) as client:
            await client.start_notify(DATA_CHAR_UUID, on_data)
            await client.write_gatt_char(CTRL_CHAR_UUID, time_sync_command(int(time.time())), response=True)
            await client.write_gatt_char(CTRL_CHAR_UUID, bytes([CMD_SEND]), response=True)
            await asyncio.wait_for(done.wait(), timeout=args.transfer_timeout)
            if problems:
                raise RuntimeError(problems[0])
            finished = precise_now()
            payload = {"device_id": device_id, "source": "pc", "rssi": rssi,
                       "started_at": db.to_jst_text_ms(started), "finished_at": db.to_jst_text_ms(finished),
                       "note": args.note, "records": records_to_json(records)}
            try:
                result = await asyncio.to_thread(post_sync, args.server, api_key, payload)
            except Exception as e:  # noqa: BLE001
                # サーバーに保存できなかったので ACK は送らない（端末にデータを残しておく）
                log(f"  サーバーに送れませんでした。端末のデータは消さずに残します: {explain_error(e, args.server)}")
                return
            log(f"  {describe_result(result)}（BLE {(finished - started).total_seconds():.1f}秒）")
            if records:
                # サーバーが保存したので、端末にそこまでの記録を消してよいと伝える
                await client.write_gatt_char(CTRL_CHAR_UUID, ack_command(max(ts for ts, _ in records)), response=True)
    except Exception as e:  # noqa: BLE001 - BLE の失敗は種類が多いので、まとめて記録する
        message = "BLE の受け取りが時間切れになりました" if isinstance(e, TimeoutError) else f"{type(e).__name__}: {e}"
        log(f"  同期に失敗しました: {message}")
        await report_failure(args, api_key, device_id, rssi, started, message)


async def run(args, api_key: str) -> None:
    while True:
        log(f"「{args.prefix}」で始まる端末を探しています（{args.scan_timeout:.0f}秒）")
        targets = await scan(args.prefix, args.scan_timeout)
        names = {name for _, name, _ in targets}
        for expected in args.expect:
            if expected not in names:
                log(f"  {expected} が見つかりませんでした")
                await report_failure(args, api_key, expected, None, precise_now(), "スキャンで見つかりませんでした")
        if not targets:
            log("  見つかりませんでした")
        for device, name, rssi in targets:
            await sync_device(device, name, rssi, args, api_key)
        if args.once:
            return
        log(f"{args.interval}秒後にもう一度探します（止めるときは Ctrl+C）")
        await asyncio.sleep(args.interval)


async def show_scan(timeout: float, prefix: str) -> None:
    devices = await scan(None, timeout)
    if not devices:
        print("BLE 機器が見つかりませんでした。PC の Bluetooth がオンになっているか確かめてください。")
    for _, name, rssi in sorted(devices, key=lambda item: -item[2]):
        mark = "  ← お守り端末" if name.startswith(prefix) else ""
        print(f"{rssi:>5} dBm  {name or '（名前なし）'}{mark}")


def main() -> int:
    parser = argparse.ArgumentParser(description="PC直結の BLE 中継（お守り端末 → サーバー）")
    parser.add_argument("--server", default=DEFAULT_SERVER, help=f"サーバーの URL（初期値 {DEFAULT_SERVER}）")
    parser.add_argument("--prefix", default="omamori-", help="お守り端末の名前の始まり（初期値 omamori-）")
    parser.add_argument("--interval", type=int, default=300, help="同期の間隔・秒（初期値 300）")
    parser.add_argument("--once", action="store_true", help="1回だけ同期して終わる")
    parser.add_argument("--scan", action="store_true", help="近くの BLE 機器を一覧するだけ")
    parser.add_argument("--expect", action="append", default=[], metavar="DEVICE_ID",
                        help="必ずいるはずの端末。見つからなければ失敗として記録する（何度でも指定できる）")
    parser.add_argument("--note", help="同期記録に残す検証のメモ（例: 距離3m）")
    parser.add_argument("--scan-timeout", type=float, default=5.0, help="探す時間・秒（初期値 5）")
    parser.add_argument("--connect-timeout", type=float, default=15.0, help="接続を待つ時間・秒（初期値 15）")
    parser.add_argument("--transfer-timeout", type=float, default=30.0, help="記録を受け取り終わるまで待つ時間・秒（初期値 30）")
    args = parser.parse_args()

    try:
        import bleak  # noqa: F401
    except ImportError:
        print("bleak が入っていません。次を実行してください: .\\.venv\\Scripts\\python.exe -m pip install -r requirements-ble.txt")
        return 1

    try:
        if args.scan:
            asyncio.run(show_scan(args.scan_timeout, args.prefix))
        else:
            asyncio.run(run(args, load_api_key()))
    except KeyboardInterrupt:
        print("止めました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
