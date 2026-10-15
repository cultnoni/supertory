"""짓시(함께 쓰기 방) HTTP API. app.py는 라우트만 연결한다.

방 코드 발급·목록·기본방 지정·비활성화를 다룬다. 스키마 생성
(ensure_gitsi_rooms_table)은 initialise_database 흐름과 묶여 있어 app.py에 남아 있다.
"""

from __future__ import annotations

import random
import re
import secrets
import sqlite3
import unicodedata
from http import HTTPStatus
from urllib.parse import unquote


def _current_user():
    import app

    return app.get_current_user()


def _database():
    import app

    return app.database()


def _as_dict(row):
    import app

    return app.as_dict(row)


GITSI_ROOM_FIELDS = "id, room_code, created_by, created_at, is_active, is_default"

GITSI_ROOM_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,62}[A-Za-z0-9]$")

GITSI_ROOM_CODE_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"


def generate_gitsi_room_code() -> str:
    suffix = "".join(secrets.choice(GITSI_ROOM_CODE_ALPHABET) for _ in range(4))
    return f"supertory-{suffix}"


def normalize_gitsi_room_code(raw: str) -> str:
    text = unicodedata.normalize("NFKC", str(raw or "")).strip()
    text = re.sub(r"\s+", "-", text)
    return text.strip("-._")


def gitsi_room_public(row: sqlite3.Row | dict | None) -> dict | None:
    data = _as_dict(row) if row is not None and not isinstance(row, dict) else (dict(row) if row else None)
    if not data:
        return None
    data["is_active"] = 1 if data.get("is_active") in (1, True, "1") else 0
    data["is_default"] = 1 if data.get("is_default") in (1, True, "1") else 0
    return data


def list_gitsi_rooms(*, active_only: bool = True, limit: int = 40) -> list[dict]:
    cap = max(1, min(int(limit or 40), 100))
    with _database() as connection:
        if active_only:
            rows = connection.execute(
                f"SELECT {GITSI_ROOM_FIELDS} "
                "FROM gitsi_rooms WHERE is_active = 1 "
                "ORDER BY is_default DESC, datetime(created_at) DESC, id DESC LIMIT ?",
                (cap,),
            ).fetchall()
        else:
            rows = connection.execute(
                f"SELECT {GITSI_ROOM_FIELDS} "
                "FROM gitsi_rooms ORDER BY is_default DESC, datetime(created_at) DESC, id DESC LIMIT ?",
                (cap,),
            ).fetchall()
    return [gitsi_room_public(row) for row in rows]


def get_default_gitsi_room() -> dict | None:
    with _database() as connection:
        row = connection.execute(
            f"SELECT {GITSI_ROOM_FIELDS} FROM gitsi_rooms "
            "WHERE is_default = 1 AND is_active = 1 LIMIT 1"
        ).fetchone()
    return gitsi_room_public(row)


def upsert_gitsi_room(room_code: str | None, created_by: str | None) -> dict:
    """Create a room if needed, or reactivate an existing code on join."""
    code = normalize_gitsi_room_code(room_code or "")
    if not code:
        for _ in range(8):
            candidate = generate_gitsi_room_code()
            with _database() as connection:
                exists = connection.execute(
                    "SELECT 1 FROM gitsi_rooms WHERE room_code = ?", (candidate,)
                ).fetchone()
            if exists is None:
                code = candidate
                break
        else:
            raise ValueError("룸 코드를 만들지 못했습니다. 잠시 후 다시 시도해 주세요.")
    if len(code) < 3 or len(code) > 64 or not GITSI_ROOM_CODE_RE.fullmatch(code):
        raise ValueError("룸 코드는 3~64자의 영문·숫자·하이픈만 사용할 수 있어요.")
    creator = (created_by or "").strip() or None
    with _database() as connection:
        existing = connection.execute(
            f"SELECT {GITSI_ROOM_FIELDS} FROM gitsi_rooms WHERE room_code = ?",
            (code,),
        ).fetchone()
        if existing is not None:
            connection.execute(
                "UPDATE gitsi_rooms SET is_active = 1 WHERE id = ?",
                (existing["id"],),
            )
            row = connection.execute(
                f"SELECT {GITSI_ROOM_FIELDS} FROM gitsi_rooms WHERE id = ?",
                (existing["id"],),
            ).fetchone()
            data = gitsi_room_public(row)
            data["created"] = False
            return data
        cursor = connection.execute(
            "INSERT INTO gitsi_rooms(room_code, created_by, is_active, is_default) "
            "VALUES (?, ?, 1, 0)",
            (code, creator),
        )
        row = connection.execute(
            f"SELECT {GITSI_ROOM_FIELDS} FROM gitsi_rooms WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    data = gitsi_room_public(row)
    data["created"] = True
    return data


def set_gitsi_room_default(room_code: str | None, enabled: bool, created_by: str | None) -> dict:
    """Mark one room as the default join target (clears any previous default)."""
    if enabled:
        data = upsert_gitsi_room(room_code, created_by)
        with _database() as connection:
            connection.execute("UPDATE gitsi_rooms SET is_default = 0 WHERE is_default = 1")
            connection.execute(
                "UPDATE gitsi_rooms SET is_default = 1, is_active = 1 WHERE id = ?",
                (data["id"],),
            )
            row = connection.execute(
                f"SELECT {GITSI_ROOM_FIELDS} FROM gitsi_rooms WHERE id = ?",
                (data["id"],),
            ).fetchone()
        result = gitsi_room_public(row)
        result["created"] = bool(data.get("created"))
        return result
    code = normalize_gitsi_room_code(room_code or "")
    if not code:
        raise ValueError("룸 코드를 입력해 주세요.")
    with _database() as connection:
        connection.execute(
            "UPDATE gitsi_rooms SET is_default = 0 WHERE room_code = ?",
            (code,),
        )
        row = connection.execute(
            f"SELECT {GITSI_ROOM_FIELDS} FROM gitsi_rooms WHERE room_code = ?",
            (code,),
        ).fetchone()
    if row is None:
        raise ValueError("그 룸을 찾지 못했어요.")
    return gitsi_room_public(row)


def deactivate_gitsi_room(room_code: str | None) -> dict:
    """Hide a room from the recent list. Joining the same code later reactivates it."""
    code = normalize_gitsi_room_code(room_code or "")
    if not code:
        raise ValueError("룸 코드를 입력해 주세요.")
    with _database() as connection:
        row = connection.execute(
            f"SELECT {GITSI_ROOM_FIELDS} FROM gitsi_rooms WHERE room_code = ?",
            (code,),
        ).fetchone()
        if row is None:
            raise ValueError("그 룸을 찾지 못했어요.")
        connection.execute(
            "UPDATE gitsi_rooms SET is_active = 0, is_default = 0 WHERE room_code = ?",
            (code,),
        )
        row = connection.execute(
            f"SELECT {GITSI_ROOM_FIELDS} FROM gitsi_rooms WHERE room_code = ?",
            (code,),
        ).fetchone()
    return gitsi_room_public(row)


def gitsi_creator_label() -> str:
    user = _current_user()
    if user:
        email = str(user.get("email") or "").strip()
        if email:
            return email[:120]
        user_id = str(user.get("id") or "").strip()
        if user_id:
            return user_id[:120]
    return "local"


def try_handle_get(handler, path: str) -> bool:
    if path == "/api/gitsi/rooms/default":
        handler.send_json({"room": get_default_gitsi_room()})
        return True
    if path == "/api/gitsi/rooms":
        handler.send_json({"rooms": list_gitsi_rooms()})
        return True
    if path == "/api/gitsi/suggest-code":
        handler.send_json({"room_code": generate_gitsi_room_code()})
        return True
    return False


def try_handle_post(handler, path: str, body: dict | None) -> bool:
    body = body or {}
    if path == "/api/gitsi/rooms/default":
        enabled = body.get("is_default")
        if enabled is None:
            enabled = True
        creator = str(body.get("created_by") or "").strip() or gitsi_creator_label()
        handler.send_json(
            set_gitsi_room_default(body.get("room_code"), bool(enabled), creator)
        )
        return True
    if path == "/api/gitsi/rooms":
        creator = str(body.get("created_by") or "").strip() or gitsi_creator_label()
        result = upsert_gitsi_room(body.get("room_code"), creator)
        status = HTTPStatus.CREATED if result.get("created") else HTTPStatus.OK
        handler.send_json(result, status)
        return True
    return False


def try_handle_delete(handler, path: str) -> bool:
    match = re.fullmatch(r"/api/gitsi/rooms/([^/]+)", path)
    if match is None:
        return False
    handler.send_json({"ok": True, "room": deactivate_gitsi_room(unquote(match.group(1)))})
    return True
