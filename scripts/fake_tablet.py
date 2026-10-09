"""にせタブレット: BLE が完成する前に、中継（PC・タブレット）の代わりに /api/sync へ歩数を送る。

使い方（サーバーを起動したまま、別の PowerShell で）:
    .\\.venv\\Scripts\\python.exe scripts\\fake_tablet.py                   # fake-01 の直近1時間分（10分ごと6件）を送る
    .\\.venv\\Scripts\\python.exe scripts\\fake_tablet.py --device fake-02 --count 12
    .\\.venv\\Scripts\\python.exe scripts\\fake_tablet.py --repeat          # 同じデータを2回送る（2回目は重複でスキップされるはず）
    .\\.venv\\Scripts\\python.exe scripts\\fake_tablet.py --steps 3000      # 1件あたりの歩数を決める（過負荷アラートの確認用）
    .\\.venv\\Scripts\\python.exe scripts\\fake_tablet.py --fail            # 失敗した同期の記録だけを送る
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from scripts.common import DEFAULT_SERVER, describe_result, explain_error, load_api_key, post_sync  # noqa: E402


def build_payload(args) -> dict:
    now = db.now_jst()
    # 区切りのよい時刻（10分ごと）にそろえる。同じ時間帯に何度送っても同じ measured_at になる。
    end = now.replace(minute=now.minute - now.minute % args.interval, second=0)
    started = now - timedelta(seconds=3)
    payload = {
        "device_id": args.device,
        "source": "fake",
        "rssi": args.rssi if args.rssi is not None else random.randint(-85, -55),
        "started_at": db.to_jst_text(started),
        "finished_at": db.to_jst_text(now),
        "note": args.note,
        "records": [],
    }
    if args.fail:
        payload.update(status="failed", error="にせタブレットで失敗を再現しました")
        return payload
    for i in range(args.count - 1, -1, -1):
        measured = end - timedelta(minutes=args.interval * i)
        # 時刻ごとに決まった乱数にして、--repeat のとき同じデータになるようにする
        steps = args.steps if args.steps is not None else random.Random(f"{args.device}-{measured.isoformat()}").randint(0, 300)
        payload["records"].append({"measured_at": db.to_jst_text(measured), "steps": steps})
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="にせタブレット（/api/sync へテストデータを送る）")
    parser.add_argument("--server", default=DEFAULT_SERVER, help=f"サーバーの URL（初期値 {DEFAULT_SERVER}）")
    parser.add_argument("--device", default="fake-01", help="端末 ID（初期値 fake-01）")
    parser.add_argument("--count", type=int, default=6, help="送る件数（初期値 6）")
    parser.add_argument("--interval", type=int, default=10, choices=(5, 10, 15, 30, 60), help="記録の間隔・分（初期値 10）")
    parser.add_argument("--steps", type=int, help="1件あたりの歩数（省くと 0〜300 の乱数）")
    parser.add_argument("--rssi", type=int, help="電波の強さ（省くと -85〜-55 の乱数）")
    parser.add_argument("--note", help="同期記録に残すメモ")
    parser.add_argument("--repeat", action="store_true", help="同じデータを2回送る")
    parser.add_argument("--fail", action="store_true", help="失敗した同期として送る")
    args = parser.parse_args()
    if not 1 <= args.count <= 500:
        parser.error("--count は 1〜500 にしてください")

    api_key = load_api_key()
    payload = build_payload(args)
    for attempt in range(2 if args.repeat else 1):
        try:
            result = post_sync(args.server, api_key, payload)
        except Exception as e:  # noqa: BLE001 - 理由を表示して終わる
            print(explain_error(e, args.server))
            return 1
        label = f"{attempt + 1}回目" if args.repeat else "送信"
        status = "失敗の記録" if args.fail else describe_result(result)
        print(f"{label}: {args.device} {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
