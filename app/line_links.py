"""LINE のアカウントと利用者をつなぐ（登録の番号の発行・登録・解除）。

- 職員が管理者用サイトで利用者ごとに6けたの番号を作り、本人や家族に渡す
- 本人や家族が LINE で「登録 123456」と送ると、その LINE アカウントが利用者とつながる
- 番号は作り直すまで何度でも使える（家族の分も同じ番号で登録できる）。作り直すと前の番号は使えなくなる
"""
from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime

from . import db


def get_code(conn: sqlite3.Connection, user_id: int) -> str | None:
    row = conn.execute("SELECT code FROM line_link_codes WHERE user_id = ?", (user_id,)).fetchone()
    return row["code"] if row else None


def issue_code(conn: sqlite3.Connection, user_id: int, now: datetime) -> str:
    """番号を作る（作り直す）。ほかの人と同じ番号にはならない。"""
    for _ in range(20):
        code = f"{secrets.randbelow(1_000_000):06d}"
        try:
            conn.execute(
                """INSERT INTO line_link_codes (user_id, code, created_at) VALUES (?, ?, ?)
                   ON CONFLICT (user_id) DO UPDATE SET code = excluded.code, created_at = excluded.created_at""",
                (user_id, code, db.to_jst_text(now)),
            )
            conn.commit()
            return code
        except sqlite3.IntegrityError:  # ほかの人の番号と同じだった
            conn.rollback()
    raise RuntimeError("番号を作れませんでした。もう一度ためしてください")


def link(conn: sqlite3.Connection, line_user_id: str, code: str, now: datetime) -> sqlite3.Row | None:
    """番号が正しければ、その LINE アカウントを利用者につなぐ。戻り値はつながった利用者（番号が違えば None）。"""
    row = conn.execute(
        "SELECT u.* FROM line_link_codes c JOIN users u ON u.id = c.user_id WHERE c.code = ?", (code,)
    ).fetchone()
    if row is None:
        return None
    # 1つの LINE アカウントは1人の利用者とだけつながる（別の人の番号を送ったら、つなぎ直す）
    conn.execute(
        """INSERT INTO line_links (line_user_id, user_id, linked_at) VALUES (?, ?, ?)
           ON CONFLICT (line_user_id) DO UPDATE SET user_id = excluded.user_id, linked_at = excluded.linked_at""",
        (line_user_id, row["id"], db.to_jst_text(now)),
    )
    conn.commit()
    return row


def linked_user(conn: sqlite3.Connection, line_user_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT u.* FROM line_links l JOIN users u ON u.id = l.user_id WHERE l.line_user_id = ?", (line_user_id,)
    ).fetchone()


def unlink_line_user(conn: sqlite3.Connection, line_user_id: str) -> bool:
    cur = conn.execute("DELETE FROM line_links WHERE line_user_id = ?", (line_user_id,))
    conn.commit()
    return cur.rowcount > 0


def unlink_by_id(conn: sqlite3.Connection, user_id: int, link_id: int) -> bool:
    cur = conn.execute("DELETE FROM line_links WHERE id = ? AND user_id = ?", (link_id, user_id))
    conn.commit()
    return cur.rowcount > 0


def links_for_user(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM line_links WHERE user_id = ? ORDER BY linked_at", (user_id,)).fetchall()
    # 画面には LINE のユーザーID を全部は出さない
    return [{**dict(row), "masked_id": mask(row["line_user_id"])} for row in rows]


def mask(line_user_id: str) -> str:
    return line_user_id[:3] + "…" + line_user_id[-4:] if len(line_user_id) > 8 else "…"
