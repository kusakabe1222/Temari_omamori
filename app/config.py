"""設定の読み込み。

.env ファイル（KEY=VALUE の形）と環境変数から値を読み、Config にまとめる。
パスワードやキーはコードに書かず、必ず .env か環境変数で渡す。
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # プロジェクトのフォルダ

# サーバーごとに、なければ起動を止める値
ADMIN_REQUIRED = ("ADMIN_PASSWORD", "SYNC_API_KEY")  # 管理者用サイト（app.main）
BOT_REQUIRED = ("LINE_CHANNEL_SECRET",)               # LINE の入口（app.bot_main）。トークンは試し運転でなければ必要


def load_env_file(path: Path) -> dict[str, str]:
    """.env を読む。空行と # で始まる行は飛ばす。値を囲む引用符は外す。"""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


@dataclass(frozen=True)
class Config:
    admin_password: str
    sync_api_key: str
    session_secret: str
    db_path: Path
    line_channel_secret: str = ""        # LINE から届いたものか確かめる（署名の確認）に使う
    line_channel_access_token: str = ""  # LINE に返事を送るときに使う
    line_dry_run: bool = False           # True = LINE には送らず、返事を画面（とテスト）に出すだけ

    @property
    def line_bot_ready(self) -> bool:
        return bool(self.line_channel_secret and (self.line_channel_access_token or self.line_dry_run))


def load_config(env_file: Path | None = None, required: tuple[str, ...] = ADMIN_REQUIRED) -> Config:
    """環境変数を優先し、なければ .env の値を使う。必須の値がなければ起動を止める。"""
    file_values = load_env_file(env_file or BASE_DIR / ".env")

    def get(key: str, default: str = "") -> str:
        return os.environ.get(key, file_values.get(key, default)).strip()

    missing = [name for name in required if not get(name)]
    if missing:
        raise RuntimeError(
            f"{', '.join(missing)} が設定されていません。"
            ".env に値を入れてください（README の「準備」と「LINE（本人用）」）。"
        )

    db_path = Path(get("DB_PATH", "data/omamori.db"))
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path

    return Config(
        admin_password=get("ADMIN_PASSWORD"),
        sync_api_key=get("SYNC_API_KEY"),
        # 空なら起動のたびに作る（サーバーを再起動するとログインし直しになるだけ）
        session_secret=get("SESSION_SECRET") or secrets.token_hex(32),
        db_path=db_path,
        line_channel_secret=get("LINE_CHANNEL_SECRET"),
        line_channel_access_token=get("LINE_CHANNEL_ACCESS_TOKEN"),
        line_dry_run=get("LINE_DRY_RUN") in ("1", "true", "yes"),
    )
