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
    line_channel_access_token: str = ""
    line_to: str = ""

    @property
    def line_configured(self) -> bool:
        return bool(self.line_channel_access_token and self.line_to)


def load_config(env_file: Path | None = None) -> Config:
    """環境変数を優先し、なければ .env の値を使う。必須の値がなければ起動を止める。"""
    file_values = load_env_file(env_file or BASE_DIR / ".env")

    def get(key: str, default: str = "") -> str:
        return os.environ.get(key, file_values.get(key, default)).strip()

    admin_password = get("ADMIN_PASSWORD")
    sync_api_key = get("SYNC_API_KEY")
    missing = [name for name, value in (("ADMIN_PASSWORD", admin_password), ("SYNC_API_KEY", sync_api_key)) if not value]
    if missing:
        raise RuntimeError(
            f"{', '.join(missing)} が設定されていません。"
            ".env.example をコピーして .env を作り、値を入れてください（README の「準備」）。"
        )

    db_path = Path(get("DB_PATH", "data/omamori.db"))
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path

    return Config(
        admin_password=admin_password,
        sync_api_key=sync_api_key,
        # 空なら起動のたびに作る（サーバーを再起動するとログインし直しになるだけ）
        session_secret=get("SESSION_SECRET") or secrets.token_hex(32),
        db_path=db_path,
        line_channel_access_token=get("LINE_CHANNEL_ACCESS_TOKEN"),
        line_to=get("LINE_TO"),
    )
