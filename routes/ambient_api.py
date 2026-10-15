"""배경음(엠비언트) HTTP API. app.py는 라우트만 연결한다.

내장/사용자 배경음 카탈로그, 파일 서빙, 업로드·삭제, 트랙 오버라이드가 여기 있다.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import unicodedata
import uuid
from email.parser import BytesParser
from email.policy import default as email_policy
from http import HTTPStatus
from pathlib import Path
from urllib.parse import unquote


def _database():
    import app

    return app.database()


def _data_dir():
    import app

    return app.DATA_DIR


def _root():
    import app

    return app.ROOT


AMBIENT_SOUND_ROOT = _root() / "assets" / "sounds"

AMBIENT_SOUND_FOLDERS = ("frequency", "noise", "nature", "ambient")

CUSTOM_AMBIENT_FOLDER = "custom"

CUSTOM_AMBIENT_QUOTA_BYTES = 400 * 1024 * 1024

CUSTOM_AMBIENT_MAX_SECONDS = 20 * 60

CUSTOM_AMBIENT_MAX_UPLOAD_BYTES = 250 * 1024 * 1024

CUSTOM_AMBIENT_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg"}

CUSTOM_AMBIENT_FILE_RE = re.compile(r"^[0-9a-f]{32}\.mp3$", re.IGNORECASE)


def ambient_file_token(folder: str, filename: str) -> str:
    """ASCII-only token so Korean filenames never go through HTTP path encoding."""
    return f"{folder}/{filename}".encode("utf-8").hex()


def ambient_custom_dir() -> Path:
    path = _data_dir() / "ambient_custom"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_custom_ambient_file(filename: str) -> Path | None:
    name = Path(str(filename or "")).name
    if not CUSTOM_AMBIENT_FILE_RE.fullmatch(name):
        return None
    root = ambient_custom_dir().resolve()
    path = (root / name).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path if path.is_file() else None


def resolve_ambient_sound_file(folder: str, filename: str) -> Path | None:
    """Find an MP3 in assets/sounds, matching Unicode-normalized Korean names."""
    if folder == CUSTOM_AMBIENT_FOLDER:
        return resolve_custom_ambient_file(filename)
    if folder not in AMBIENT_SOUND_FOLDERS:
        return None
    root = AMBIENT_SOUND_ROOT.resolve()
    folder_dir = (root / folder).resolve()
    try:
        folder_dir.relative_to(root)
    except ValueError:
        return None
    if not folder_dir.is_dir():
        return None
    wanted_name = unicodedata.normalize("NFC", str(filename or ""))
    if not wanted_name or wanted_name in {".", ".."} or "/" in wanted_name or "\\" in wanted_name:
        return None
    direct = folder_dir / wanted_name
    if direct.is_file():
        return direct
    wanted_cf = wanted_name.casefold()
    wanted_stem = Path(wanted_name).stem.casefold()
    for item in folder_dir.iterdir():
        if not item.is_file() or item.suffix.lower() != ".mp3":
            continue
        name_n = unicodedata.normalize("NFC", item.name)
        if name_n.casefold() == wanted_cf or item.stem.casefold() == wanted_stem:
            return item
    return None


def list_ambient_sound_catalog() -> dict:
    """MP3 tracks under assets/sounds/{frequency,noise,nature,ambient}."""
    root = AMBIENT_SOUND_ROOT.resolve()
    categories = []
    for folder in AMBIENT_SOUND_FOLDERS:
        folder_dir = root / folder
        tracks = []
        if folder_dir.is_dir():
            for path in sorted(folder_dir.iterdir(), key=lambda item: item.name.lower()):
                if not path.is_file() or path.suffix.lower() != ".mp3":
                    continue
                tracks.append({
                    "id": f"{folder}:{path.stem}",
                    "category": folder,
                    "file": path.name,
                    "stem": path.stem,
                    "url": f"/api/ambient-file/{ambient_file_token(folder, path.name)}",
                })
        categories.append({"id": folder, "tracks": tracks})
    custom_tracks, usage = load_custom_ambient_tracks()
    categories.append({"id": CUSTOM_AMBIENT_FOLDER, "tracks": custom_tracks})
    apply_ambient_track_overrides(categories)
    return {"categories": categories, "usage": usage}


def _unlink_quiet(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _subprocess_hidden_kwargs() -> dict:
    kwargs: dict = {}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if flags:
        kwargs["creationflags"] = flags
    return kwargs


def find_ffmpeg_tools() -> tuple[str, str]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise ValueError("음원을 변환할 수 없어요. ffmpeg가 설치되어 있는지 확인해 주세요.")
    return ffmpeg, ffprobe


def transcode_to_ambient_mp3(source: Path, destination: Path) -> None:
    ffmpeg, _ffprobe = find_ffmpeg_tools()
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(source),
            "-b:a",
            "128k",
            "-ar",
            "44100",
            "-ac",
            "2",
            str(destination),
        ],
        capture_output=True,
        timeout=180,
        **_subprocess_hidden_kwargs(),
    )
    if result.returncode != 0 or not destination.is_file() or destination.stat().st_size < 32:
        raise ValueError("이 파일은 변환할 수 없어요. 파일이 손상됐거나 지원하지 않는 형식일 수 있어요.")


def probe_audio_duration_seconds(path: Path) -> float:
    _ffmpeg, ffprobe = find_ffmpeg_tools()
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        **_subprocess_hidden_kwargs(),
    )
    if result.returncode != 0:
        raise ValueError("이 파일은 변환할 수 없어요. 파일이 손상됐거나 지원하지 않는 형식일 수 있어요.")
    try:
        duration = float((result.stdout or "").strip())
    except (TypeError, ValueError) as error:
        raise ValueError("이 파일은 변환할 수 없어요. 파일이 손상됐거나 지원하지 않는 형식일 수 있어요.") from error
    if duration <= 0:
        raise ValueError("이 파일은 변환할 수 없어요. 파일이 손상됐거나 지원하지 않는 형식일 수 있어요.")
    return duration


def custom_ambient_usage(connection: sqlite3.Connection | None = None) -> dict:
    empty = {"used_bytes": 0, "limit_bytes": CUSTOM_AMBIENT_QUOTA_BYTES}

    def _read(conn: sqlite3.Connection) -> dict:
        used = conn.execute(
            "SELECT COALESCE(SUM(file_size_bytes), 0) FROM user_ambient_tracks"
        ).fetchone()[0]
        return {"used_bytes": int(used or 0), "limit_bytes": CUSTOM_AMBIENT_QUOTA_BYTES}

    if connection is not None:
        try:
            return _read(connection)
        except sqlite3.Error:
            return empty
    try:
        with _database() as conn:
            return _read(conn)
    except sqlite3.Error:
        return empty


def custom_ambient_track_public(row: sqlite3.Row | dict) -> dict:
    stored = str(row["stored_filename"])
    original = str(row["original_filename"])
    return {
        "id": f"custom:{int(row['id'])}",
        "category": CUSTOM_AMBIENT_FOLDER,
        "file": original,
        "stem": Path(original).stem,
        "url": f"/api/ambient-file/{ambient_file_token(CUSTOM_AMBIENT_FOLDER, stored)}",
        "custom": True,
        "custom_id": int(row["id"]),
        "duration_seconds": float(row["duration_seconds"] or 0),
        "file_size_bytes": int(row["file_size_bytes"] or 0),
    }


def load_custom_ambient_tracks() -> tuple[list[dict], dict]:
    tracks: list[dict] = []
    usage = custom_ambient_usage()
    try:
        with _database() as connection:
            rows = connection.execute(
                "SELECT id, original_filename, stored_filename, duration_seconds, "
                "file_size_bytes, category FROM user_ambient_tracks "
                "ORDER BY created_at, id"
            ).fetchall()
            usage = custom_ambient_usage(connection)
    except sqlite3.Error:
        return tracks, usage
    for row in rows:
        stored = str(row["stored_filename"])
        if resolve_custom_ambient_file(stored) is None:
            continue
        tracks.append(custom_ambient_track_public(row))
    return tracks, usage


def sanitize_ambient_upload_filename(name: str) -> str:
    cleaned = unicodedata.normalize("NFC", Path(str(name or "")).name).replace("\x00", "").strip()
    if len(cleaned) > 200:
        stem = Path(cleaned).stem[:180]
        suffix = Path(cleaned).suffix[:20]
        cleaned = f"{stem}{suffix}" if suffix else stem
    return cleaned or "audio.mp3"


def parse_multipart_form(handler) -> tuple[dict[str, str], dict[str, tuple[str, bytes]]]:
    content_type = handler.headers.get("Content-Type", "")
    if "multipart/form-data" not in content_type.lower():
        raise ValueError("파일 업로드 형식이 올바르지 않습니다.")
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except (TypeError, ValueError) as error:
        raise ValueError("파일 업로드 형식이 올바르지 않습니다.") from error
    if length <= 0:
        raise ValueError("업로드 파일이 비어 있습니다.")
    if length > CUSTOM_AMBIENT_MAX_UPLOAD_BYTES:
        remaining = length
        while remaining > 0:
            chunk = handler.rfile.read(min(65536, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
        raise ValueError("파일이 너무 큽니다.")
    raw = handler.rfile.read(length)
    header_block = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
    message = BytesParser(policy=email_policy).parsebytes(header_block + raw)
    fields: dict[str, str] = {}
    files: dict[str, tuple[str, bytes]] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True)
        if payload is None:
            payload = b""
        if filename:
            files[str(name)] = (str(filename), payload)
        else:
            charset = part.get_content_charset() or "utf-8"
            try:
                fields[str(name)] = payload.decode(charset, errors="replace")
            except LookupError:
                fields[str(name)] = payload.decode("utf-8", errors="replace")
    return fields, files


def save_custom_ambient_track(category: str, original_filename: str, file_bytes: bytes) -> dict:
    category = CUSTOM_AMBIENT_FOLDER
    if not file_bytes:
        raise ValueError("업로드 파일이 비어 있습니다.")
    original_filename = sanitize_ambient_upload_filename(original_filename)
    extension = Path(original_filename).suffix.lower()
    if extension not in CUSTOM_AMBIENT_EXTENSIONS:
        raise ValueError("mp3, wav, m4a, flac, ogg 파일만 올릴 수 있어요.")
    stored_filename = f"{uuid.uuid4().hex}.mp3"
    source_path: Path | None = None
    encoded_path: Path | None = None
    dest_path: Path | None = None
    try:
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=extension)
        try:
            handle.write(file_bytes)
            handle.flush()
        finally:
            handle.close()
        source_path = Path(handle.name)
        encoded_handle = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        encoded_handle.close()
        encoded_path = Path(encoded_handle.name)
        transcode_to_ambient_mp3(source_path, encoded_path)
        duration = probe_audio_duration_seconds(encoded_path)
        if duration > CUSTOM_AMBIENT_MAX_SECONDS:
            raise ValueError("배경음은 짧은 루프 소스를 권장해요")
        encoded_size = encoded_path.stat().st_size
        with _database() as connection:
            used = int(
                connection.execute(
                    "SELECT COALESCE(SUM(file_size_bytes), 0) FROM user_ambient_tracks"
                ).fetchone()[0]
                or 0
            )
            if used + encoded_size > CUSTOM_AMBIENT_QUOTA_BYTES:
                raise ValueError("저장 공간이 가득 찼어요 (400MB 제한)")
            dest_path = ambient_custom_dir() / stored_filename
            shutil.move(str(encoded_path), str(dest_path))
            encoded_path = None
            cursor = connection.execute(
                "INSERT INTO user_ambient_tracks("
                "original_filename, stored_filename, duration_seconds, file_size_bytes, category) "
                "VALUES (?, ?, ?, ?, ?)",
                (original_filename, stored_filename, duration, encoded_size, category),
            )
            track_id = int(cursor.lastrowid)
            row = connection.execute(
                "SELECT id, original_filename, stored_filename, duration_seconds, "
                "file_size_bytes, category FROM user_ambient_tracks WHERE id = ?",
                (track_id,),
            ).fetchone()
            usage = custom_ambient_usage(connection)
        return {"track": custom_ambient_track_public(row), "usage": usage}
    except Exception:
        _unlink_quiet(encoded_path)
        _unlink_quiet(dest_path)
        raise
    finally:
        _unlink_quiet(source_path)


def upload_custom_ambient_track_from_request(handler) -> dict:
    fields, files = parse_multipart_form(handler)
    upload = files.get("file")
    if not upload:
        raise ValueError("업로드 파일이 비어 있습니다.")
    filename, payload = upload
    category = str(fields.get("category") or "").strip()
    return save_custom_ambient_track(category, filename, payload)


def delete_custom_ambient_track(track_id: int) -> dict:
    with _database() as connection:
        row = connection.execute(
            "SELECT id, stored_filename FROM user_ambient_tracks WHERE id = ?",
            (int(track_id),),
        ).fetchone()
        if row is None:
            raise ValueError("음원을 찾을 수 없습니다.")
        stored = str(row["stored_filename"])
        connection.execute("DELETE FROM user_ambient_tracks WHERE id = ?", (int(track_id),))
        try:
            connection.execute(
                "DELETE FROM ambient_track_overrides WHERE track_id = ?",
                (f"custom:{int(track_id)}",),
            )
        except sqlite3.Error:
            pass
        usage = custom_ambient_usage(connection)
    path = resolve_custom_ambient_file(stored)
    if path is None:
        candidate = ambient_custom_dir() / Path(stored).name
        if candidate.is_file() and CUSTOM_AMBIENT_FILE_RE.fullmatch(candidate.name):
            path = candidate
    _unlink_quiet(path)
    return {"ok": True, "usage": usage}


def _json_flag(value, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        raise ValueError("토글박스 표시 값이 올바르지 않습니다.")
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, bool):
        return value
    raise ValueError("토글박스 표시 값이 올바르지 않습니다.")


def parse_ambient_track_id(track_id: str) -> tuple[str, str] | None:
    raw = unicodedata.normalize("NFC", unquote(str(track_id or "")).strip())
    if not raw or len(raw) > 240 or "/" in raw or "\\" in raw:
        return None
    folder, sep, stem = raw.partition(":")
    if not sep or not folder or not stem:
        return None
    if folder == CUSTOM_AMBIENT_FOLDER:
        if not stem.isdigit():
            return None
        return folder, str(int(stem))
    if folder not in AMBIENT_SOUND_FOLDERS:
        return None
    return folder, stem


def canonical_ambient_track_id(track_id: str) -> str | None:
    parsed = parse_ambient_track_id(track_id)
    if parsed is None:
        return None
    folder, stem = parsed
    return f"{folder}:{stem}"


def ambient_track_exists(track_id: str) -> bool:
    parsed = parse_ambient_track_id(track_id)
    if parsed is None:
        return False
    folder, stem = parsed
    if folder == CUSTOM_AMBIENT_FOLDER:
        try:
            with _database() as connection:
                row = connection.execute(
                    "SELECT id FROM user_ambient_tracks WHERE id = ?",
                    (int(stem),),
                ).fetchone()
            return row is not None
        except sqlite3.Error:
            return False
    return resolve_ambient_sound_file(folder, f"{stem}.mp3") is not None


def original_ambient_title(track_id: str) -> str:
    parsed = parse_ambient_track_id(track_id)
    if parsed is None:
        return ""
    folder, stem = parsed
    if folder != CUSTOM_AMBIENT_FOLDER:
        return stem
    try:
        with _database() as connection:
            row = connection.execute(
                "SELECT original_filename FROM user_ambient_tracks WHERE id = ?",
                (int(stem),),
            ).fetchone()
    except sqlite3.Error:
        return ""
    if row is None:
        return ""
    return Path(str(row["original_filename"] or "")).stem


def load_ambient_track_overrides(connection: sqlite3.Connection | None = None) -> dict[str, dict]:
    def _read(conn: sqlite3.Connection) -> dict[str, dict]:
        try:
            rows = conn.execute(
                "SELECT track_id, custom_title, enabled_in_popup FROM ambient_track_overrides"
            ).fetchall()
        except sqlite3.Error:
            return {}
        result: dict[str, dict] = {}
        for row in rows:
            tid = canonical_ambient_track_id(str(row["track_id"] or ""))
            if not tid:
                continue
            title = row["custom_title"]
            if title is not None:
                title = str(title).strip() or None
            enabled = row["enabled_in_popup"]
            result[tid] = {
                "custom_title": title,
                "enabled_in_popup": False if enabled in (0, "0", False) else True,
            }
        return result

    if connection is not None:
        return _read(connection)
    try:
        with _database() as conn:
            return _read(conn)
    except sqlite3.Error:
        return {}


def apply_ambient_track_overrides(categories: list[dict]) -> None:
    overrides = load_ambient_track_overrides()
    for category in categories:
        for track in category.get("tracks") or []:
            original = str(track.get("stem") or Path(str(track.get("file") or "")).stem or "").strip()
            ov = overrides.get(str(track.get("id") or "")) or {}
            custom = ov.get("custom_title")
            custom = str(custom).strip() if custom else None
            track["custom_title"] = custom
            track["display_title"] = custom or original
            track["enabled_in_popup"] = (
                bool(ov["enabled_in_popup"]) if "enabled_in_popup" in ov else True
            )


def upsert_ambient_track_override(track_id: str, payload: dict | None) -> dict:
    canonical = canonical_ambient_track_id(track_id)
    if not canonical or not ambient_track_exists(canonical):
        raise LookupError("트랙을 찾을 수 없습니다.")
    body = payload if isinstance(payload, dict) else {}
    has_title = "custom_title" in body
    has_enabled = "enabled_in_popup" in body
    if not has_title and not has_enabled:
        raise ValueError("변경할 값이 없습니다.")

    custom_title = None
    if has_title:
        raw = body.get("custom_title")
        if raw is None or str(raw).strip() == "":
            custom_title = None
        else:
            custom_title = unicodedata.normalize("NFC", str(raw).strip())[:80]

    enabled_value = None
    if has_enabled:
        enabled_value = 1 if _json_flag(body.get("enabled_in_popup"), default=True) else 0

    with _database() as connection:
        existing = connection.execute(
            "SELECT custom_title, enabled_in_popup FROM ambient_track_overrides WHERE track_id = ?",
            (canonical,),
        ).fetchone()
        if existing is None:
            connection.execute(
                "INSERT INTO ambient_track_overrides("
                "track_id, custom_title, enabled_in_popup, updated_at) "
                "VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
                (
                    canonical,
                    custom_title if has_title else None,
                    enabled_value if has_enabled else 1,
                ),
            )
        else:
            next_title = custom_title if has_title else existing["custom_title"]
            if next_title is not None:
                next_title = str(next_title).strip() or None
            next_enabled = enabled_value if has_enabled else existing["enabled_in_popup"]
            next_enabled = 1 if next_enabled in (1, True, "1") else 0
            connection.execute(
                "UPDATE ambient_track_overrides "
                "SET custom_title = ?, enabled_in_popup = ?, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE track_id = ?",
                (next_title, next_enabled, canonical),
            )
        row = connection.execute(
            "SELECT track_id, custom_title, enabled_in_popup FROM ambient_track_overrides "
            "WHERE track_id = ?",
            (canonical,),
        ).fetchone()
    custom = str(row["custom_title"]).strip() if row and row["custom_title"] else None
    enabled = True if row is None else bool(int(row["enabled_in_popup"] or 0))
    original = original_ambient_title(canonical)
    return {
        "track_id": canonical,
        "custom_title": custom,
        "enabled_in_popup": enabled,
        "display_title": custom or original,
    }


def serve_ambient_file(handler, token: str) -> None:
    try:
        relative = bytes.fromhex(token).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError("배경음 파일을 찾을 수 없습니다.") from error
    folder, sep, filename = relative.partition("/")
    if not sep:
        raise ValueError("배경음 파일을 찾을 수 없습니다.")
    path = resolve_ambient_sound_file(folder, filename)
    if path is None:
        raise ValueError("배경음 파일을 찾을 수 없습니다.")
    data = path.read_bytes()
    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", "audio/mpeg")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header(
        "Cache-Control",
        "private, no-store" if folder == CUSTOM_AMBIENT_FOLDER else "private, max-age=86400",
    )
    handler.end_headers()
    handler.wfile.write(data)


def try_handle_get(handler, path: str) -> bool:
    if path == "/api/ambient-tracks":
        handler.send_json(list_ambient_sound_catalog())
        return True
    match = re.fullmatch(r"/api/ambient-file/([0-9a-fA-F]+)", path)
    if match:
        serve_ambient_file(handler, match.group(1))
        return True
    return False


def try_handle_post(handler, path: str, body: dict | None = None) -> bool:
    if path == "/api/ambient/upload":
        handler.send_json(
            upload_custom_ambient_track_from_request(handler), HTTPStatus.CREATED
        )
        return True
    return False


def try_handle_patch(handler, path: str, body: dict | None) -> bool:
    match = re.fullmatch(r"/api/ambient/overrides/(.+)", path)
    if match is None:
        return False
    handler.send_json(
        upsert_ambient_track_override(unquote(match.group(1)), body or {})
    )
    return True


def try_handle_delete(handler, path: str) -> bool:
    match = re.fullmatch(r"/api/ambient/custom/(\d+)", path)
    if match is None:
        return False
    handler.send_json(delete_custom_ambient_track(int(match.group(1))))
    return True
