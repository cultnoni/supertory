"""SuperTory: a small local writing app with no installation step.

Double-click start_supertory.bat on Windows.  The app opens in a browser, but
all writing stays in the local data/supertory.sqlite3 file.

Each work also gets a Scrivener-style external file under projects/이름.stg.
Double-click that file to open SuperTory focused on that work.
"""

from __future__ import annotations

import base64
import binascii
import html as html_lib
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
import uuid
import webbrowser
from contextlib import contextmanager
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Timer
from urllib.parse import parse_qs, quote, urlparse

import chapter_match
import document_export
import document_import
import env_loader
import folder_tree
import gemini_client
import import_hierarchy
import korean_speller
import project_package
import proof_clean
import proof_diff
import proof_extract
import proof_pipeline
import success_pattern

def _is_frozen() -> bool:
    """True when running as a PyInstaller (or similar) bundle."""
    return bool(getattr(sys, "frozen", False))


def _resolve_root() -> Path:
    """App root: source tree, or PyInstaller extract dir (web/ + db/)."""
    if _is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _load_dotenv_files() -> None:
    """Load .env (source / frozen paths) then apply build-time key defaults."""
    env_loader.load_all_dotenv()


# Load .env / bundled defaults as early as possible so GEMINI_API_KEY is available.
# (gemini_client also calls load_all_dotenv on import; this covers direct app paths.)
_load_dotenv_files()


ROOT = _resolve_root()
SCHEMA_PATH = ROOT / "db" / "001_initial_schema.sql"
MIGRATION_002_PATH = ROOT / "db" / "002_project_purpose.sql"
MIGRATION_003_PATH = ROOT / "db" / "003_project_package.sql"
MIGRATION_004_PATH = ROOT / "db" / "004_scene_illustration.sql"
MIGRATION_005_PATH = ROOT / "db" / "005_scene_reference_links.sql"
MIGRATION_006_PATH = ROOT / "db" / "006_scene_goal_metric.sql"
MIGRATION_007_PATH = ROOT / "db" / "007_idea_bank.sql"
MIGRATION_008_PATH = ROOT / "db" / "008_project_worldbuilding.sql"
MIGRATION_009_PATH = ROOT / "db" / "009_project_logline.sql"
MIGRATION_010_PATH = ROOT / "db" / "010_project_genre.sql"
MIGRATION_011_PATH = ROOT / "db" / "011_project_intro_intent.sql"
MIGRATION_012_PATH = ROOT / "db" / "012_project_keywords.sql"
MIGRATION_013_PATH = ROOT / "db" / "013_writing_log.sql"
MIGRATION_014_PATH = ROOT / "db" / "014_writing_first_met.sql"
MIGRATION_015_PATH = ROOT / "db" / "015_character_strengths_weaknesses.sql"
MIGRATION_016_PATH = ROOT / "db" / "016_scene_parent.sql"
MIGRATION_017_PATH = ROOT / "db" / "017_project_list_order.sql"
MIGRATION_018_PATH = ROOT / "db" / "018_tory_priority.sql"
MIGRATION_019_PATH = ROOT / "db" / "019_project_index.sql"
MIGRATION_020_PATH = ROOT / "db" / "020_project_index_queue.sql"
MIGRATION_021_PATH = ROOT / "db" / "021_chapter_parent_scene.sql"
MIGRATION_022_PATH = ROOT / "db" / "022_outline_summary.sql"
MIGRATION_023_PATH = ROOT / "db" / "023_bait.sql"
MIGRATION_024_PATH = ROOT / "db" / "024_character_portrait.sql"
MIGRATION_025_PATH = ROOT / "db" / "025_success_pattern_profile.sql"
MIGRATION_026_PATH = ROOT / "db" / "026_linked_success_profile.sql"
MIGRATION_027_PATH = ROOT / "db" / "027_writing_track_modes.sql"
MIGRATION_028_PATH = ROOT / "db" / "028_folder_tree_parallel.sql"
MIGRATION_029_PATH = ROOT / "db" / "029_folder_color_pin.sql"
MIGRATION_030_PATH = ROOT / "db" / "030_folder_action_log.sql"
MIGRATION_031_PATH = ROOT / "db" / "031_folder_bookmark.sql"
MIGRATION_032_PATH = ROOT / "db" / "032_writing_include_phone.sql"
WEB_ROOT = ROOT / "web"
GOAL_METRICS = {"chars_with_space", "chars_no_space", "words", "letters"}
IDEA_COLORS = {"yellow", "pink", "blue", "green", "orange", "purple"}
FOLDER_COLORS = {"red", "orange", "yellow", "green", "blue", "purple", "gray"}
# Electron (and other shells) may point data/projects at a writable user dir.
# Prefer SUPERTORY_*; accept legacy STORYGUIDE_* env vars from older Electron shells.
_DATA_DIR_ENV = (
    os.environ.get("SUPERTORY_DATA_DIR") or os.environ.get("STORYGUIDE_DATA_DIR") or ""
).strip()
if _DATA_DIR_ENV:
    DATA_DIR = Path(_DATA_DIR_ENV).expanduser()
elif _is_frozen():
    # Never write into the read-only PyInstaller bundle (_MEIPASS).
    DATA_DIR = Path(sys.executable).resolve().parent / "data"
else:
    DATA_DIR = ROOT / "data"
DATABASE_PATH = DATA_DIR / "supertory.sqlite3"
_PROJECTS_DIR_ENV = (
    os.environ.get("SUPERTORY_PROJECTS_DIR") or os.environ.get("STORYGUIDE_PROJECTS_DIR") or ""
).strip()
HOST = "127.0.0.1"
PORT = 8765
# When launched from Electron, skip opening a system browser tab.
ELECTRON_MODE = (
    os.environ.get("SUPERTORY_ELECTRON") or os.environ.get("STORYGUIDE_ELECTRON") or ""
).strip() in {"1", "true", "yes"}
NO_BROWSER = ELECTRON_MODE or (
    os.environ.get("SUPERTORY_NO_BROWSER") or os.environ.get("STORYGUIDE_NO_BROWSER") or ""
).strip() in {
    "1",
    "true",
    "yes",
}
MAX_ILLUSTRATION_BYTES = 12 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


@contextmanager
def database() -> sqlite3.Connection:
    """Commit successful requests and always close the Windows file handle."""
    connection = connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def projects_root() -> Path:
    """External .stg files live next to the data folder (or under the app root)."""
    if _PROJECTS_DIR_ENV:
        return Path(_PROJECTS_DIR_ENV).expanduser()
    # Prefer a sibling of data/ so tests that swap DATA_DIR stay isolated.
    return project_package.projects_dir(DATA_DIR.parent if DATA_DIR.name == "data" else ROOT)


def default_export_dir() -> Path:
    """Default folder for manuscript exports: Downloads (다운로드)."""
    home = Path.home()
    for name in ("Downloads", "다운로드"):
        candidate = home / name
        if candidate.is_dir():
            return candidate
    # Prefer creating the common English name if neither exists yet.
    return home / "Downloads"


def export_prefs_path() -> Path:
    return DATA_DIR / "export_prefs.json"


def load_export_prefs() -> dict:
    path = export_prefs_path()
    prefs: dict = {
        "export_dir": str(default_export_dir()),
        "save_to_folder": True,
        "reveal_after_save": True,
    }
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                if raw.get("export_dir"):
                    prefs["export_dir"] = str(raw["export_dir"])
                if "save_to_folder" in raw:
                    prefs["save_to_folder"] = bool(raw["save_to_folder"])
                if "reveal_after_save" in raw:
                    prefs["reveal_after_save"] = bool(raw["reveal_after_save"])
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    return prefs


def save_export_prefs(prefs: dict) -> dict:
    DATA_DIR.mkdir(exist_ok=True)
    cleaned = {
        "export_dir": str(prefs.get("export_dir") or default_export_dir()),
        "save_to_folder": bool(prefs.get("save_to_folder", True)),
        "reveal_after_save": bool(prefs.get("reveal_after_save", True)),
    }
    export_prefs_path().write_text(
        json.dumps(cleaned, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return cleaned


def resolve_export_dir(raw: str | None = None, *, create: bool = True) -> Path:
    """
    Resolve and validate an export directory on this machine.
    Allowed roots: user home, Documents, Desktop, Downloads, app data/projects.
    """
    prefs = load_export_prefs()
    text = str(raw if raw is not None else prefs.get("export_dir") or "").strip()
    if not text:
        path = default_export_dir()
    else:
        path = Path(text).expanduser()
    try:
        path = path.resolve()
    except OSError as error:
        raise ValueError(f"폴더 경로를 해석할 수 없습니다: {error}") from error

    home = Path.home().resolve()
    allowed_roots = [
        home,
        home / "Documents",
        home / "Desktop",
        home / "Downloads",
        home / "OneDrive",
        home / "OneDrive" / "Documents",
        DATA_DIR.resolve(),
        projects_root().resolve(),
        ROOT.resolve(),
    ]
    # Also allow any existing path under home
    under_home = False
    try:
        path.relative_to(home)
        under_home = True
    except ValueError:
        under_home = False
    if not under_home:
        ok = False
        for root in allowed_roots:
            try:
                if root.exists():
                    path.relative_to(root)
                    ok = True
                    break
            except ValueError:
                continue
        if not ok:
            raise ValueError(
                "저장 폴더는 사용자 폴더(내 문서·바탕화면 등) 아래로만 지정할 수 있어요."
            )

    if create:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise ValueError(f"폴더를 만들 수 없습니다: {error}") from error
    if path.exists() and not path.is_dir():
        raise ValueError("지정한 경로는 폴더가 아닙니다.")
    return path


def safe_export_filename(name: str) -> str:
    base = str(name or "export").strip() or "export"
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base)
    base = base.strip(" .") or "export"
    return base[:180]


def write_export_file(
    data: bytes,
    filename: str,
    *,
    directory: str | None = None,
    reveal: bool | None = None,
) -> dict:
    """Write export bytes to the configured (or given) folder on this PC."""
    prefs = load_export_prefs()
    folder = resolve_export_dir(directory, create=True)
    name = safe_export_filename(filename)
    target = folder / name
    # Avoid overwrite: add (2), (3)...
    if target.exists():
        stem = target.stem
        suffix = target.suffix
        n = 2
        while True:
            candidate = folder / f"{stem} ({n}){suffix}"
            if not candidate.exists():
                target = candidate
                break
            n += 1
            if n > 999:
                raise ValueError("같은 이름 파일이 너무 많아 저장하지 못했어요.")
    try:
        target.write_bytes(data)
    except OSError as error:
        raise ValueError(f"파일 저장에 실패했습니다: {error}") from error
    do_reveal = prefs.get("reveal_after_save", True) if reveal is None else bool(reveal)
    if do_reveal:
        try:
            reveal_in_explorer(target)
        except Exception:
            pass
    return {
        "path": str(target),
        "folder": str(folder),
        "filename": target.name,
        "bytes": len(data),
        "revealed": bool(do_reveal),
    }


def _migrate_legacy_database_file() -> None:
    """Rename storyguide.sqlite3 → supertory.sqlite3 when only the old name exists."""
    new_path = Path(DATABASE_PATH)
    if new_path.is_file():
        return
    legacy = new_path.with_name("storyguide.sqlite3")
    if not legacy.is_file():
        return
    try:
        legacy.rename(new_path)
        for suffix in ("-wal", "-shm"):
            old_side = Path(str(legacy) + suffix)
            new_side = Path(str(new_path) + suffix)
            if old_side.is_file() and not new_side.exists():
                old_side.rename(new_side)
        print(f"데이터베이스 파일 이름 변경: {legacy.name} → {new_path.name}")
    except OSError as error:
        print(f"레거시 DB 이름 변경 실패 ({legacy} → {new_path}): {error}")


def initialise_database() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "illustrations").mkdir(exist_ok=True)
    projects_root()
    _migrate_legacy_database_file()
    with database() as connection:
        migration_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migration'"
        ).fetchone()
        if migration_table is None:
            connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        applied = {
            row[0]
            for row in connection.execute("SELECT version FROM schema_migration").fetchall()
        }
        if 1 not in applied:
            raise RuntimeError("지원하지 않는 SuperTory 데이터베이스 버전입니다.")
        if 2 not in applied:
            connection.executescript(MIGRATION_002_PATH.read_text(encoding="utf-8"))
        if 3 not in applied:
            connection.executescript(MIGRATION_003_PATH.read_text(encoding="utf-8"))
        if 4 not in applied:
            connection.executescript(MIGRATION_004_PATH.read_text(encoding="utf-8"))
        if 5 not in applied:
            connection.executescript(MIGRATION_005_PATH.read_text(encoding="utf-8"))
        if 6 not in applied:
            connection.executescript(MIGRATION_006_PATH.read_text(encoding="utf-8"))
        if 7 not in applied:
            connection.executescript(MIGRATION_007_PATH.read_text(encoding="utf-8"))
        if 8 not in applied:
            connection.executescript(MIGRATION_008_PATH.read_text(encoding="utf-8"))
        if 9 not in applied:
            connection.executescript(MIGRATION_009_PATH.read_text(encoding="utf-8"))
        if 10 not in applied:
            connection.executescript(MIGRATION_010_PATH.read_text(encoding="utf-8"))
        if 11 not in applied:
            connection.executescript(MIGRATION_011_PATH.read_text(encoding="utf-8"))
        if 12 not in applied:
            connection.executescript(MIGRATION_012_PATH.read_text(encoding="utf-8"))
        if 13 not in applied:
            connection.executescript(MIGRATION_013_PATH.read_text(encoding="utf-8"))
        if 14 not in applied:
            connection.executescript(MIGRATION_014_PATH.read_text(encoding="utf-8"))
        if 15 not in applied:
            connection.executescript(MIGRATION_015_PATH.read_text(encoding="utf-8"))
        if 16 not in applied:
            connection.executescript(MIGRATION_016_PATH.read_text(encoding="utf-8"))
        if 17 not in applied:
            connection.executescript(MIGRATION_017_PATH.read_text(encoding="utf-8"))
        if 18 not in applied:
            connection.executescript(MIGRATION_018_PATH.read_text(encoding="utf-8"))
        if 19 not in applied:
            connection.executescript(MIGRATION_019_PATH.read_text(encoding="utf-8"))
        if 20 not in applied:
            connection.executescript(MIGRATION_020_PATH.read_text(encoding="utf-8"))
        if 21 not in applied:
            connection.executescript(MIGRATION_021_PATH.read_text(encoding="utf-8"))
        if 22 not in applied:
            connection.executescript(MIGRATION_022_PATH.read_text(encoding="utf-8"))
        if 23 not in applied:
            connection.executescript(MIGRATION_023_PATH.read_text(encoding="utf-8"))
        if 24 not in applied:
            connection.executescript(MIGRATION_024_PATH.read_text(encoding="utf-8"))
        if 25 not in applied:
            connection.executescript(MIGRATION_025_PATH.read_text(encoding="utf-8"))
        if 26 not in applied:
            connection.executescript(MIGRATION_026_PATH.read_text(encoding="utf-8"))
        if 27 not in applied:
            connection.executescript(MIGRATION_027_PATH.read_text(encoding="utf-8"))
        if 28 not in applied:
            connection.executescript(MIGRATION_028_PATH.read_text(encoding="utf-8"))
        if 29 not in applied:
            connection.executescript(MIGRATION_029_PATH.read_text(encoding="utf-8"))
        if 30 not in applied:
            connection.executescript(MIGRATION_030_PATH.read_text(encoding="utf-8"))
        if 31 not in applied:
            connection.executescript(MIGRATION_031_PATH.read_text(encoding="utf-8"))
        if 32 not in applied:
            connection.executescript(MIGRATION_032_PATH.read_text(encoding="utf-8"))
        ensure_writing_first_met_day(connection)
        ensure_all_project_packages(connection)


def illustration_dir_for(project_id: int) -> Path:
    path = DATA_DIR / "illustrations" / str(project_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def character_portrait_dir_for(project_id: int) -> Path:
    path = illustration_dir_for(project_id) / "characters"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _day_key_valid(value: object) -> str:
    day = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise ValueError("날짜 형식이 올바르지 않습니다. (YYYY-MM-DD)")
    return day


def ensure_writing_first_met_day(connection: sqlite3.Connection) -> str:
    """Stamp the local install / first-meet day once (토리와 처음 만난 날)."""
    connection.execute("INSERT OR IGNORE INTO writing_prefs(id) VALUES (1)")
    # Column may be missing on very old connections mid-migration; safe after 014.
    try:
        row = connection.execute(
            "SELECT first_met_day FROM writing_prefs WHERE id = 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return ""
    current = str((row["first_met_day"] if row else "") or "").strip()
    if current and re.fullmatch(r"\d{4}-\d{2}-\d{2}", current):
        return current
    # Prefer earliest project creation day (local), else today UTC date as fallback.
    first_met = ""
    try:
        proj = connection.execute(
            "SELECT MIN(created_at) AS c FROM project WHERE deleted_at IS NULL"
        ).fetchone()
        raw = (proj["c"] if proj else None) or ""
        if raw:
            # created_at is ISO UTC-ish; use calendar date portion
            first_met = str(raw)[:10]
    except sqlite3.Error:
        first_met = ""
    if not first_met or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", first_met):
        first_met = time.strftime("%Y-%m-%d", time.localtime())
    connection.execute(
        "UPDATE writing_prefs SET first_met_day = ?, "
        "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = 1",
        (first_met,),
    )
    return first_met


def writing_prefs_row(connection: sqlite3.Connection) -> dict:
    connection.execute("INSERT OR IGNORE INTO writing_prefs(id) VALUES (1)")
    first_met = ensure_writing_first_met_day(connection)
    row = connection.execute("SELECT * FROM writing_prefs WHERE id = 1").fetchone()
    data = as_dict(row) or {}
    mode = str(data.get("project_list_mode") or "recent").strip().lower()
    if mode not in {"recent", "manual"}:
        mode = "recent"
    # Defaults: chars auto on, time only while "기록" is on (time_auto off).
    chars_auto_raw = data.get("chars_auto")
    time_auto_raw = data.get("time_auto")
    include_phone_raw = data.get("include_phone_log")
    chars_auto = True if chars_auto_raw is None else bool(int(chars_auto_raw))
    time_auto = False if time_auto_raw is None else bool(int(time_auto_raw))
    include_phone_log = True if include_phone_raw is None else bool(int(include_phone_raw))
    return {
        "goal_chars": int(data.get("goal_chars") or 2000),
        "goal_notify": bool(int(data.get("goal_notify") or 0)),
        "lonely_days": int(data.get("lonely_days") or 3),
        "lonely_notify": bool(int(data.get("lonely_notify") or 0)),
        "idle_minutes": int(data.get("idle_minutes") or 30),
        "chars_auto": chars_auto,
        "time_auto": time_auto,
        "include_phone_log": include_phone_log,
        "last_goal_notified_day": str(data.get("last_goal_notified_day") or ""),
        "last_lonely_notified_day": str(data.get("last_lonely_notified_day") or ""),
        "first_met_day": str(data.get("first_met_day") or first_met or ""),
        "project_list_mode": mode,
    }


def project_list_mode(connection: sqlite3.Connection) -> str:
    prefs = writing_prefs_row(connection)
    mode = str(prefs.get("project_list_mode") or "recent").strip().lower()
    return mode if mode in {"recent", "manual"} else "recent"


def set_project_list_mode(connection: sqlite3.Connection, mode: str) -> str:
    normalised = str(mode or "recent").strip().lower()
    if normalised not in {"recent", "manual"}:
        raise ValueError("작품 목록 정렬 방식이 올바르지 않습니다.")
    connection.execute("INSERT OR IGNORE INTO writing_prefs(id) VALUES (1)")
    connection.execute(
        "UPDATE writing_prefs SET project_list_mode = ?, "
        "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = 1",
        (normalised,),
    )
    return normalised


def utc_timestamp_now() -> str:
    """UTC timestamp with microseconds for list-order keys.

    Prefer this over SQLite ``strftime(..., 'now')`` for ``last_opened_at``:
    SQLite's ``now`` can collide at millisecond (or coarser) resolution when
    projects are created/touched in rapid succession, and ties then fall through
    to ``id DESC`` (wrong "recent open" order).
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def touch_project_opened(connection: sqlite3.Connection, project_id: int) -> str:
    """Stamp last_opened_at so recent-first list ordering stays current."""
    stamp = utc_timestamp_now()
    connection.execute(
        "UPDATE project SET last_opened_at = ? "
        "WHERE id = ? AND deleted_at IS NULL",
        (stamp, project_id),
    )
    row = connection.execute(
        "SELECT last_opened_at FROM project WHERE id = ?",
        (project_id,),
    ).fetchone()
    return str((row["last_opened_at"] if row else "") or stamp)


def project_list_order_sql(mode: str) -> str:
    if mode == "manual":
        return "ORDER BY list_sort_order ASC, id ASC"
    # Default: most recently opened first; never-opened fall back to created_at.
    return (
        "ORDER BY COALESCE(last_opened_at, created_at) DESC, id DESC"
    )


def serialize_project_list_row(row: sqlite3.Row | dict) -> dict:
    item = as_dict(row)
    item["synopsis_md"] = item.get("description_md") or ""
    item["logline_md"] = item.get("logline_md") or ""
    item["intro_md"] = item.get("intro_md") or ""
    item["intent_md"] = item.get("intent_md") or ""
    item["tory_priority_md"] = item.get("tory_priority_md") or ""
    item["outline_summary"] = item.get("outline_summary") or ""
    try:
        raw_link = item.get("linked_success_profile_id")
        item["linked_success_profile_id"] = (
            int(raw_link) if raw_link not in (None, "", 0, "0") else None
        )
    except (TypeError, ValueError):
        item["linked_success_profile_id"] = None
    item["main_genre"] = item.get("main_genre") or ""
    item["sub_genre"] = item.get("sub_genre") or ""
    item["keywords"] = parse_project_keywords(item.get("keywords"))
    item["last_opened_at"] = item.get("last_opened_at") or None
    try:
        item["list_sort_order"] = int(item.get("list_sort_order") or 0)
    except (TypeError, ValueError):
        item["list_sort_order"] = 0
    return item


def list_projects_payload(connection: sqlite3.Connection) -> list[dict]:
    mode = project_list_mode(connection)
    # linked_success_profile_id added in migration 026 — tolerate older DBs mid-migrate.
    cols = "id, title, description_md, logline_md, worldbuilding_md, intro_md, intent_md, "
    cols += "tory_priority_md, outline_summary, "
    cols += "default_language, goal_word_count, "
    cols += "purpose, main_genre, sub_genre, keywords, uuid, package_path, "
    cols += "last_opened_at, list_sort_order, created_at, updated_at"
    try:
        connection.execute("SELECT linked_success_profile_id FROM project LIMIT 1")
        cols += ", linked_success_profile_id"
    except sqlite3.OperationalError:
        pass
    rows = connection.execute(
        f"SELECT {cols} "
        f"FROM project WHERE deleted_at IS NULL {project_list_order_sql(mode)}"
    ).fetchall()
    payload = [serialize_project_list_row(row) for row in rows]
    for item in payload:
        item["list_mode"] = mode
    return payload


def writing_day_payload(row: sqlite3.Row | dict | None) -> dict | None:
    if not row:
        return None
    data = dict(row) if not isinstance(row, dict) else row
    breakdown = {}
    raw = data.get("breakdown_json") or "{}"
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(parsed, dict):
            breakdown = {
                str(k): max(0, int(v or 0))
                for k, v in parsed.items()
            }
    except (TypeError, ValueError, json.JSONDecodeError):
        breakdown = {}
    return {
        "day": data.get("day_key") or data.get("day") or "",
        "chars_added": int(data.get("chars_added") or 0),
        "active_seconds": int(data.get("active_seconds") or 0),
        "session_count": int(data.get("session_count") or 0),
        "first_start_at": data.get("first_start_at") or None,
        "last_active_at": data.get("last_active_at") or None,
        "breakdown": breakdown,
    }


def parse_project_keywords(raw: object) -> list[str]:
    """Normalise project keyword tags to a unique ordered list of short labels."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                raw = parsed
            else:
                # Plain comma / newline separated text
                raw = re.split(r"[,，/\n|]+", text)
        except json.JSONDecodeError:
            raw = re.split(r"[,，/\n|]+", text)
    if not isinstance(raw, (list, tuple)):
        raise ValueError("키워드 형식이 올바르지 않습니다.")
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in raw:
        label = str(item or "").strip()[:40]
        if not label or label in seen:
            continue
        seen.add(label)
        cleaned.append(label)
        if len(cleaned) >= 40:
            break
    return cleaned


def parse_overlays(raw: object) -> list[dict]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError("삽화 위 글 정보가 올바르지 않습니다.") from error
    if not isinstance(raw, list):
        raise ValueError("삽화 위 글 정보가 올바르지 않습니다.")
    cleaned: list[dict] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        overlay_id = str(item.get("id") or f"ov-{index + 1}")[:64]
        text = str(item.get("text", ""))[:4000]
        try:
            x = max(0.0, min(95.0, float(item.get("x", 10))))
            y = max(0.0, min(95.0, float(item.get("y", 10))))
            width = max(10.0, min(100.0, float(item.get("width", 40))))
            font_size = max(10, min(72, int(item.get("fontSize", 18) or 18)))
        except (TypeError, ValueError) as error:
            raise ValueError("삽화 위 글 위치 정보가 올바르지 않습니다.") from error
        color = str(item.get("color", "#24211d"))[:32]
        if not re.fullmatch(r"#[0-9A-Fa-f]{3,8}|[a-zA-Z]+", color):
            color = "#24211d"
        align = str(item.get("align", "left"))
        if align not in {"left", "center", "right"}:
            align = "left"
        cleaned.append({
            "id": overlay_id,
            "text": text,
            "x": round(x, 2),
            "y": round(y, 2),
            "width": round(width, 2),
            "fontSize": font_size,
            "color": color,
            "align": align,
        })
    return cleaned[:40]


def parse_reference_links(raw: object) -> list[dict]:
    """Normalise scene reference materials: links and file refs.

    Link:  {id, kind:'link', title, url}
    File:  {id, kind:'file', title, sourceId, fileName, fileExt, viewer, url?}
    """
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError("참고 자료 정보가 올바르지 않습니다.") from error
    if not isinstance(raw, list):
        raise ValueError("참고 자료 정보가 올바르지 않습니다.")
    cleaned: list[dict] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "link").strip().lower()
        if kind not in {"link", "file"}:
            kind = "file" if (item.get("fileName") or item.get("sourceId") or item.get("source_id")) else "link"
        link_id = str(item.get("id") or f"ref-{index + 1}")[:64]

        if kind == "file":
            title = str(item.get("title", "")).strip()[:200]
            file_name = str(item.get("fileName") or item.get("file_name") or "").strip()[:260]
            source_id = str(item.get("sourceId") or item.get("source_id") or "").strip()[:80]
            file_ext = str(item.get("fileExt") or item.get("file_ext") or "").strip()[:20]
            viewer = str(item.get("viewer") or "").strip()[:20]
            if not title and not file_name and not source_id:
                continue
            if not title:
                title = (file_name or "파일")[:80]
            entry: dict = {
                "id": link_id,
                "kind": "file",
                "title": title,
                "url": "",
                "sourceId": source_id,
                "fileName": file_name,
            }
            if file_ext:
                entry["fileExt"] = file_ext
            if viewer in {"pdf", "text"}:
                entry["viewer"] = viewer
            # Optional external url kept if present
            raw_url = str(item.get("url", "")).strip()
            if raw_url and re.match(r"^https?://", raw_url, re.IGNORECASE):
                entry["url"] = raw_url[:2000]
            cleaned.append(entry)
            continue

        url = str(item.get("url", "")).strip()
        if not url:
            continue
        # Allow bare domains by prefixing https://
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", url):
            url = "https://" + url
        if not re.match(r"^https?://", url, re.IGNORECASE):
            raise ValueError("참고 링크는 http 또는 https 주소만 넣을 수 있어요.")
        if len(url) > 2000:
            raise ValueError("참고 링크 주소가 너무 깁니다.")
        title = str(item.get("title", "")).strip()[:200]
        if not title:
            # Fallback label from host/path
            title = re.sub(r"^https?://(www\.)?", "", url, flags=re.IGNORECASE).split("/")[0] or "링크"
            title = title[:80]
        entry = {"id": link_id, "kind": "link", "title": title, "url": url}
        source_id = str(item.get("sourceId") or item.get("source_id") or "").strip()[:80]
        if source_id:
            entry["sourceId"] = source_id
        cleaned.append(entry)
    return cleaned[:40]


def illustration_public(row: sqlite3.Row | dict) -> dict:
    data = dict(row)
    data["overlays"] = parse_overlays(data.pop("overlays_json", "[]"))
    data["image_url"] = f"/api/illustrations/{data['id']}/image"
    return data


def as_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def plain_text_from_content(content: str) -> str:
    """Strip simple HTML so word counts ignore tags from the rich editor."""
    text = content or ""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = re.sub(r"(?i)</div\s*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def first_sentence_preview(content: str, limit: int = 160) -> str:
    """First sentence (or line) of manuscript body for binder untitled titles."""
    plain = plain_text_from_content(content or "")
    if not plain:
        return ""
    compact = re.sub(r"\s+", " ", plain).strip()
    match = re.match(r"^(.+?(?:[.。!?？…]|$))(?:\s|$)", compact)
    sentence = (match.group(1) if match else compact).strip()
    if len(sentence) > limit:
        return sentence[: max(1, limit - 1)].rstrip() + "…"
    return sentence


def word_count(markdown: str) -> int:
    """Count whitespace-separated tokens; HTML tags are ignored."""
    return len(re.findall(r"\S+", plain_text_from_content(markdown)))


def ensure_project_package(connection: sqlite3.Connection, project_id: int) -> dict:
    """Create/refresh the external .stg file for a project; return path fields."""
    row = connection.execute(
        "SELECT id, title, purpose, uuid, package_path FROM project "
        "WHERE id = ? AND deleted_at IS NULL",
        (project_id,),
    ).fetchone()
    if row is None:
        raise ValueError("작품을 찾을 수 없습니다.")
    project_uuid = row["uuid"] or project_package.new_project_uuid()
    purpose = row["purpose"] if "purpose" in row.keys() else "general_novel"
    package = project_package.create_or_update_package(
        projects_root().parent,
        project_uuid=project_uuid,
        title=row["title"],
        purpose=purpose or "general_novel",
        project_id=project_id,
        existing_path=row["package_path"],
    )
    connection.execute(
        "UPDATE project SET uuid = ?, package_path = ? WHERE id = ?",
        (project_uuid, str(package), project_id),
    )
    return {
        "uuid": project_uuid,
        "package_path": str(package),
        "package_name": package.name,
    }


def ensure_all_project_packages(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT id FROM project WHERE deleted_at IS NULL ORDER BY id"
    ).fetchall()
    for row in rows:
        ensure_project_package(connection, row["id"])


def resolve_package_file(path: Path) -> int:
    """Return project id for a .stg package path."""
    data = project_package.read_package(path)
    project_uuid = str(data["uuid"])
    with database() as connection:
        row = connection.execute(
            "SELECT id, package_path FROM project WHERE uuid = ? AND deleted_at IS NULL",
            (project_uuid,),
        ).fetchone()
        if row is None and data.get("project_id") is not None:
            row = connection.execute(
                "SELECT id, package_path FROM project WHERE id = ? AND deleted_at IS NULL",
                (int(data["project_id"]),),
            ).fetchone()
        if row is None:
            raise ValueError(
                "이 .stg 파일에 해당하는 작품을 찾지 못했습니다. "
                "SuperTory 데이터 폴더가 그대로인지 확인해 주세요."
            )
        # Keep package_path in sync if the user moved/renamed the file.
        resolved = str(path.resolve())
        if row["package_path"] != resolved:
            connection.execute(
                "UPDATE project SET package_path = ? WHERE id = ?",
                (resolved, row["id"]),
            )
            # Refresh manifest contents at the new location.
            project = connection.execute(
                "SELECT id, title, purpose, uuid FROM project WHERE id = ?",
                (row["id"],),
            ).fetchone()
            project_package.write_package(
                path.resolve(),
                project_uuid=project["uuid"],
                title=project["title"],
                purpose=project["purpose"] or "general_novel",
                project_id=project["id"],
            )
        return int(row["id"])


def port_is_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.35)
        return sock.connect_ex((host, port)) == 0


def listening_pids(port: int) -> list[int]:
    """Return PIDs listening on TCP port (Windows netstat)."""
    if sys.platform != "win32":
        return []
    try:
        completed = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return []
    pids: list[int] = []
    needle = f":{port}"
    for line in completed.stdout.splitlines():
        if "LISTENING" not in line.upper():
            continue
        if needle not in line:
            continue
        # Typical: TCP    127.0.0.1:8765    0.0.0.0:0    LISTENING    1234
        parts = line.split()
        if len(parts) < 5:
            continue
        local = parts[1] if len(parts) >= 2 else ""
        if not local.endswith(needle):
            continue
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        if pid > 0 and pid not in pids:
            pids.append(pid)
    return pids


def stop_server_on_port(port: int) -> bool:
    """Stop an already-running SuperTORY instance so restarts load new code."""
    pids = listening_pids(port)
    if not pids:
        return False
    stopped = False
    for pid in pids:
        if pid == os.getpid():
            continue
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True,
                    check=False,
                )
            else:
                os.kill(pid, 15)
            stopped = True
            print(f"이전 SuperTORY 서버(PID {pid})를 종료하고 새 버전으로 다시 시작합니다.")
        except OSError as error:
            print(f"이전 서버(PID {pid})를 종료하지 못했습니다: {error}")
    # Brief wait so the port is released.
    if stopped:
        for _ in range(20):
            if not port_is_open(HOST, port):
                break
            time.sleep(0.1)
    return stopped


def reveal_in_explorer(path: Path) -> None:
    path = Path(path)
    if sys.platform != "win32":
        return
    if path.is_file():
        subprocess.Popen(["explorer", "/select,", str(path.resolve())])  # noqa: S603
    elif path.is_dir():
        subprocess.Popen(["explorer", str(path.resolve())])  # noqa: S603
    else:
        subprocess.Popen(["explorer", str(projects_root())])  # noqa: S603


class SuperToryHandler(SimpleHTTPRequestHandler):
    """Serves the app and a deliberately small JSON API."""

    def translate_path(self, path: str) -> str:
        parsed = urlparse(path).path
        if parsed == "/":
            parsed = "/index.html"
        return str((WEB_ROOT / parsed.lstrip("/")).resolve())

    def log_message(self, format: str, *args: object) -> None:
        # Keep the launch window quiet; errors are returned to the browser instead.
        return

    def send_json(self, value: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def send_download(
        self,
        data: bytes,
        *,
        filename: str,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        """Send a file download with ASCII fallback + UTF-8 filename*."""
        # HTTP headers are Latin-1; keep the quoted filename ASCII-only.
        # (\w is Unicode-aware in Python 3 and would leave Hangul in the header.)
        safe_ascii = re.sub(r"[^A-Za-z0-9._\-]+", "_", filename).strip("._") or "export"
        if not re.search(r"\.[A-Za-z0-9]+$", safe_ascii):
            # Preserve extension from original when title was all non-ASCII.
            ext_match = re.search(r"(\.[A-Za-z0-9]+)$", filename)
            if ext_match:
                safe_ascii = f"export{ext_match.group(1)}"
        disposition = (
            f'attachment; filename="{safe_ascii}"; '
            f"filename*=UTF-8''{quote(filename)}"
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", disposition)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def api_error(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        self.send_json({"error": message}, status)

    def require_project(self, connection: sqlite3.Connection, project_id: int) -> None:
        if connection.execute(
            "SELECT 1 FROM project WHERE id = ? AND deleted_at IS NULL", (project_id,)
        ).fetchone() is None:
            raise ValueError("소설을 찾을 수 없습니다.")

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            if path == "/api/meta/work-purposes":
                self.send_json([
                    {"key": key, "label": label}
                    for key, label in document_import.WORK_PURPOSES.items()
                ])
                return

            if path == "/api/ai/status":
                self.send_json(gemini_client.status())
                return

            if path == "/api/success-pattern/profiles":
                self.send_json(self.list_success_pattern_profiles())
                return

            match = re.fullmatch(r"/api/success-pattern/profiles/(\d+)", path)
            if match:
                self.send_json(self.get_success_pattern_profile(int(match.group(1))))
                return

            if path == "/api/success-pattern/meta":
                self.send_json({
                    "max_total_episodes": success_pattern.MAX_TOTAL_EPISODES,
                    "recommended_total_episodes": success_pattern.RECOMMENDED_TOTAL_EPISODES,
                    "max_total_chars": success_pattern.MAX_TOTAL_CHARS,
                    "recommended_total_chars": success_pattern.RECOMMENDED_TOTAL_CHARS,
                    "default_window": success_pattern.DEFAULT_WINDOW,
                    "section_labels": success_pattern.SECTION_LABELS,
                })
                return

            if path == "/api/proof-parsers":
                self.send_json(proof_extract.parser_status())
                return

            if path == "/api/writing/prefs":
                with database() as connection:
                    self.send_json(writing_prefs_row(connection))
                return

            if path == "/api/writing/days":
                query = parse_qs(urlparse(self.path).query)
                from_day = (query.get("from") or [""])[0]
                to_day = (query.get("to") or [""])[0]
                self.send_json(self.list_writing_days(from_day, to_day))
                return

            match = re.fullmatch(r"/api/writing/days/(\d{4}-\d{2}-\d{2})", path)
            if match:
                self.send_json(self.get_writing_day(match.group(1)))
                return

            if path == "/api/writing/pair":
                self.send_json(self.get_writing_pair())
                return

            if path == "/api/writing/inbox":
                self.send_json(self.list_mobile_inbox())
                return

            if path == "/api/projects":
                with database() as connection:
                    payload = list_projects_payload(connection)
                self.send_json(payload)
                return

            match = re.fullmatch(r"/api/projects/(\d+)/outline", path)
            if match:
                self.send_json(self.project_outline(int(match.group(1))))
                return

            match = re.fullmatch(r"/api/projects/(\d+)/undo-status", path)
            if match:
                self.send_json(self.project_undo_status(int(match.group(1))))
                return

            match = re.fullmatch(r"/api/projects/(\d+)/manuscript-stats", path)
            if match:
                self.send_json(self.project_manuscript_stats(int(match.group(1))))
                return

            match = re.fullmatch(r"/api/projects/(\d+)/export", path)
            if match:
                query = parse_qs(urlparse(self.path).query)
                fmt = (query.get("format") or ["txt"])[0]
                # Optional: ?scene_ids=1,2,3 for partial export (full manuscript if omitted)
                scene_ids_raw = (query.get("scene_ids") or query.get("scenes") or [""])[0]
                scene_ids = None
                if str(scene_ids_raw).strip():
                    scene_ids = []
                    for part in str(scene_ids_raw).replace(" ", "").split(","):
                        if not part:
                            continue
                        try:
                            scene_ids.append(int(part))
                        except ValueError as error:
                            raise ValueError("회차 id 목록이 올바르지 않습니다.") from error
                exported = self.export_project(
                    int(match.group(1)),
                    fmt,
                    scene_ids=scene_ids,
                )
                self.send_download(
                    exported.data,
                    filename=exported.filename,
                    content_type=exported.mime,
                )
                return

            if path == "/api/meta/export-formats":
                self.send_json([
                    {"key": key, **meta}
                    for key, meta in document_export.EXPORT_FORMATS.items()
                ])
                return

            if path in {"/api/meta/text-export-formats", "/api/reference/export-formats"}:
                self.send_json([
                    {"key": key, **document_export.EXPORT_FORMATS[key]}
                    for key in document_export.TEXT_EXPORT_FORMATS
                    if key in document_export.EXPORT_FORMATS
                ])
                return

            if path == "/api/export/prefs":
                prefs = load_export_prefs()
                try:
                    folder = resolve_export_dir(prefs.get("export_dir"), create=False)
                    folder_ok = folder.is_dir()
                    folder_path = str(folder)
                except ValueError:
                    folder_ok = False
                    folder_path = str(prefs.get("export_dir") or default_export_dir())
                self.send_json({
                    **prefs,
                    "export_dir": folder_path,
                    "folder_ok": folder_ok,
                    "default_export_dir": str(default_export_dir()),
                })
                return

            match = re.fullmatch(r"/api/projects/(\d+)/trash", path)
            if match:
                self.send_json(self.list_trash(int(match.group(1))))
                return

            match = re.fullmatch(r"/api/projects/(\d+)/characters", path)
            if match:
                project_id = int(match.group(1))
                with database() as connection:
                    self.require_project(connection, project_id)
                    rows = connection.execute(
                        "SELECT id, name, role, short_description, profile_md, "
                        "strengths_md, weaknesses_md FROM character "
                        "WHERE project_id = ? AND deleted_at IS NULL ORDER BY sort_order, id",
                        (project_id,),
                    ).fetchall()
                self.send_json([as_dict(row) for row in rows])
                return

            match = re.fullmatch(r"/api/projects/(\d+)/ideas", path)
            if match:
                self.send_json(self.list_ideas(int(match.group(1))))
                return

            match = re.fullmatch(r"/api/projects/(\d+)/baits", path)
            if match:
                self.send_json(self.list_baits(int(match.group(1))))
                return

            match = re.fullmatch(r"/api/scenes/(\d+)", path)
            if match:
                self.send_json(self.scene_detail(int(match.group(1))))
                return

            match = re.fullmatch(r"/api/scenes/(\d+)/summary", path)
            if match:
                self.send_json(self.get_scene_summary(int(match.group(1))))
                return

            match = re.fullmatch(r"/api/projects/(\d+)/index", path)
            if match:
                self.send_json(self.get_project_index(int(match.group(1))))
                return

            match = re.fullmatch(r"/api/scenes/(\d+)/characters", path)
            if match:
                scene_id = int(match.group(1))
                with database() as connection:
                    rows = connection.execute(
                        "SELECT character_id, appearance_role, is_pov FROM scene_character "
                        "WHERE scene_id = ? ORDER BY character_id",
                        (scene_id,),
                    ).fetchall()
                self.send_json([as_dict(row) for row in rows])
                return

            match = re.fullmatch(r"/api/scenes/(\d+)/illustrations", path)
            if match:
                self.send_json(self.list_illustrations(int(match.group(1))))
                return

            match = re.fullmatch(r"/api/illustrations/(\d+)/image", path)
            if match:
                self.serve_illustration_image(int(match.group(1)))
                return

            match = re.fullmatch(r"/api/characters/(\d+)", path)
            if match:
                self.send_json(self.character_detail(int(match.group(1))))
                return

            match = re.fullmatch(r"/api/characters/(\d+)/portrait", path)
            if match:
                self.send_character_portrait(int(match.group(1)))
                return
        except ValueError as error:
            self.api_error(str(error), HTTPStatus.NOT_FOUND)
            return
        except sqlite3.Error as error:
            self.api_error(f"데이터베이스 오류: {error}", HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if path.startswith("/api/"):
            self.api_error("알 수 없는 요청입니다.", HTTPStatus.NOT_FOUND)
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/")
        try:
            body = self.read_json()
            # Reference file text extract — register early (DOCX/HWP viewer upload).
            if path in {"/api/extract-text", "/api/reference/extract-text"}:
                self.send_json(self.extract_reference_text(body))
                return

            if path == "/api/success-pattern/recommend-ranges":
                total = body.get("total_chapters") or body.get("totalChapters") or 1
                window = body.get("window") or success_pattern.DEFAULT_WINDOW
                self.send_json({
                    "total_chapters": max(1, int(total)),
                    "ranges": success_pattern.recommend_ranges(int(total), int(window)),
                })
                return

            if path == "/api/success-pattern/parse":
                self.send_json(self.parse_success_pattern_document(body))
                return

            if path == "/api/success-pattern/check-budget":
                self.send_json(self.check_success_pattern_budget(body))
                return

            if path == "/api/success-pattern/run":
                self.send_json(self.run_success_pattern_analysis(body))
                return

            # Export a single plain document (edited reference material, not full manuscript).
            if path in {"/api/export-text", "/api/reference/export-text"}:
                exported = self.export_plain_text_document(body)
                self.send_download(
                    exported.data,
                    filename=exported.filename,
                    content_type=exported.mime,
                )
                return

            # Export prefs: { export_dir, save_to_folder, reveal_after_save }
            if path == "/api/export/prefs":
                prefs = load_export_prefs()
                if "export_dir" in body:
                    folder = resolve_export_dir(str(body.get("export_dir") or ""), create=True)
                    prefs["export_dir"] = str(folder)
                if "save_to_folder" in body:
                    prefs["save_to_folder"] = bool(body.get("save_to_folder"))
                if "reveal_after_save" in body:
                    prefs["reveal_after_save"] = bool(body.get("reveal_after_save"))
                saved = save_export_prefs(prefs)
                self.send_json({
                    **saved,
                    "folder_ok": True,
                    "default_export_dir": str(default_export_dir()),
                })
                return

            if path == "/api/export/open-folder":
                prefs = load_export_prefs()
                folder = resolve_export_dir(
                    str(body.get("export_dir") or prefs.get("export_dir") or ""),
                    create=True,
                )
                reveal_in_explorer(folder)
                self.send_json({"ok": True, "export_dir": str(folder)})
                return

            # POST export with JSON body: { format, scene_ids?, save_to_folder?, export_dir? }
            match = re.fullmatch(r"/api/projects/(\d+)/export", path)
            if match:
                fmt = str(body.get("format") or body.get("format_key") or "docx").strip()
                raw_ids = body.get("scene_ids") if body.get("scene_ids") is not None else body.get("scenes")
                scene_ids = None
                if raw_ids is not None:
                    if not isinstance(raw_ids, list):
                        raise ValueError("scene_ids는 회차 id 배열이어야 합니다.")
                    scene_ids = []
                    for item in raw_ids:
                        try:
                            scene_ids.append(int(item))
                        except (TypeError, ValueError) as error:
                            raise ValueError("회차 id 목록이 올바르지 않습니다.") from error
                exported = self.export_project(
                    int(match.group(1)),
                    fmt,
                    scene_ids=scene_ids,
                    export_title=str(body.get("title") or "").strip() or None,
                )
                prefs = load_export_prefs()
                # save_to_folder: request overrides prefs
                if "save_to_folder" in body:
                    save_to_folder = bool(body.get("save_to_folder"))
                else:
                    save_to_folder = bool(prefs.get("save_to_folder", True))
                if save_to_folder:
                    reveal = body.get("reveal_after_save")
                    if reveal is None:
                        reveal = prefs.get("reveal_after_save", True)
                    saved = write_export_file(
                        exported.data,
                        exported.filename,
                        directory=str(body.get("export_dir") or prefs.get("export_dir") or ""),
                        reveal=bool(reveal),
                    )
                    self.send_json({
                        "ok": True,
                        "saved": True,
                        "filename": exported.filename,
                        "mime": exported.mime,
                        **saved,
                    })
                    return
                self.send_download(
                    exported.data,
                    filename=exported.filename,
                    content_type=exported.mime,
                )
                return

            if path == "/api/projects":
                title = str(body.get("title", "")).strip()
                if not title:
                    raise ValueError("작품 제목을 입력해 주세요.")
                purpose = document_import.normalise_purpose(body.get("purpose"))
                main_genre = str(body.get("main_genre") or "").strip()[:80]
                sub_genre = str(body.get("sub_genre") or "").strip()[:80]
                if not main_genre:
                    raise ValueError("장르를 선택해 주세요. 토리 학습에 필요해요.")
                with database() as connection:
                    # Append after current max manual order so manual lists stay stable.
                    max_order_row = connection.execute(
                        "SELECT COALESCE(MAX(list_sort_order), -1) AS m FROM project WHERE deleted_at IS NULL"
                    ).fetchone()
                    next_order = int(max_order_row["m"] if max_order_row else -1) + 1
                    opened_stamp = utc_timestamp_now()
                    cursor = connection.execute(
                        "INSERT INTO project(title, purpose, main_genre, sub_genre, last_opened_at, list_sort_order) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (title, purpose, main_genre, sub_genre, opened_stamp, next_order),
                    )
                    project_id = int(cursor.lastrowid)
                    package_info = ensure_project_package(connection, project_id)
                self.send_json(
                    {
                        "id": project_id,
                        "purpose": purpose,
                        "main_genre": main_genre,
                        "sub_genre": sub_genre,
                        **package_info,
                    },
                    HTTPStatus.CREATED,
                )
                return

            match = re.fullmatch(r"/api/projects/(\d+)/reveal-package", path)
            if match:
                project_id = int(match.group(1))
                with database() as connection:
                    self.require_project(connection, project_id)
                    package_info = ensure_project_package(connection, project_id)
                reveal_in_explorer(Path(package_info["package_path"]))
                self.send_json({"ok": True, **package_info})
                return

            match = re.fullmatch(r"/api/projects/(\d+)/touch-open", path)
            if match:
                project_id = int(match.group(1))
                with database() as connection:
                    self.require_project(connection, project_id)
                    stamp = touch_project_opened(connection, project_id)
                    mode = project_list_mode(connection)
                self.send_json({
                    "ok": True,
                    "id": project_id,
                    "last_opened_at": stamp,
                    "list_mode": mode,
                })
                return

            match = re.fullmatch(r"/api/projects/(\d+)/parts", path)
            if match:
                self.send_json(
                    self.create_part(int(match.group(1)), body),
                    HTTPStatus.CREATED,
                )
                return

            match = re.fullmatch(r"/api/projects/(\d+)/chapters", path)
            if match:
                project_id = int(match.group(1))
                title = str(body.get("title", "새 챕터")).strip() or "새 챕터"
                insert_index = body.get("insert_index", None)
                insert_before_id = body.get("insert_before_id", None)
                raw_parent_scene = body.get("parent_scene_id", None)
                parent_scene_id = None
                if raw_parent_scene is not None and str(raw_parent_scene).strip() != "":
                    try:
                        parent_scene_id = int(raw_parent_scene)
                    except (TypeError, ValueError) as error:
                        raise ValueError("상위 원고 정보가 올바르지 않습니다.") from error
                raw_part = body.get("part_id", None)
                if raw_part is None or raw_part == "" or str(raw_part).lower() == "null":
                    part_id = None
                else:
                    try:
                        part_id = int(raw_part)
                    except (TypeError, ValueError) as error:
                        raise ValueError("권/부 정보가 올바르지 않습니다.") from error
                with database() as connection:
                    self.require_project(connection, project_id)
                    if parent_scene_id is not None:
                        parent_scene = connection.execute(
                            "SELECT s.id, s.project_id, s.chapter_id, c.part_id "
                            "FROM scene s "
                            "JOIN chapter c ON c.id = s.chapter_id "
                            "WHERE s.id = ? AND s.deleted_at IS NULL "
                            "AND c.deleted_at IS NULL",
                            (parent_scene_id,),
                        ).fetchone()
                        if parent_scene is None or int(parent_scene["project_id"]) != project_id:
                            raise ValueError("상위 원고를 찾을 수 없습니다.")
                        # Inherit part from the manuscript's folder for trash/move consistency.
                        part_id = (
                            int(parent_scene["part_id"])
                            if parent_scene["part_id"] is not None
                            else None
                        )
                        # Folder parent = host scene's chapter folder (promote under host folder)
                        host_chapter_id = int(parent_scene["chapter_id"])
                        host_folder_id = folder_tree.folder_id_for_source(
                            connection, project_id, "chapter", host_chapter_id
                        )
                        if host_folder_id is None:
                            self._mirror_project_folders(connection, project_id)
                            host_folder_id = folder_tree.folder_id_for_source(
                                connection, project_id, "chapter", host_chapter_id
                            )
                        sort_order = connection.execute(
                            "SELECT COALESCE(MAX(sort_order) + 1, 0) FROM chapter "
                            "WHERE project_id = ? AND parent_scene_id = ? "
                            "AND deleted_at IS NULL",
                            (project_id, parent_scene_id),
                        ).fetchone()[0]
                        try:
                            folder_sort = folder_tree.next_folder_sibling_sort(
                                connection, project_id, host_folder_id
                            )
                        except sqlite3.OperationalError:
                            folder_sort = int(sort_order)
                        folder_sort_order = max(int(sort_order), int(folder_sort))
                        # 1) folder first
                        try:
                            new_folder_id = folder_tree._insert_folder(
                                connection,
                                project_id=project_id,
                                parent_id=host_folder_id,
                                title=title,
                                notes_md="",
                                is_box=0,
                                sort_order=folder_sort_order,
                                source_kind=None,
                                source_id=None,
                            )
                        except sqlite3.OperationalError:
                            new_folder_id = None
                        # 2) legacy chapter
                        cursor = connection.execute(
                            "INSERT INTO chapter("
                            "project_id, part_id, parent_scene_id, title, sort_order"
                            ") VALUES (?, ?, ?, ?, ?)",
                            (project_id, part_id, parent_scene_id, title, sort_order),
                        )
                        new_id = int(cursor.lastrowid)
                        # 3) bind
                        if new_folder_id is not None:
                            folder_tree.bind_folder_source(
                                connection, new_folder_id, "chapter", new_id
                            )
                        else:
                            self._mirror_project_folders(connection, project_id)
                        self._log_folder_create(
                            connection,
                            project_id=project_id,
                            folder_id=new_folder_id,
                            source_kind="chapter",
                            source_id=new_id,
                            title=title,
                        )
                        self.send_json(
                            {
                                "id": new_id,
                                "part_id": part_id,
                                "parent_scene_id": parent_scene_id,
                            },
                            HTTPStatus.CREATED,
                        )
                        return

                    if part_id is not None:
                        part_ok = connection.execute(
                            "SELECT id FROM part "
                            "WHERE id = ? AND project_id = ? AND deleted_at IS NULL",
                            (part_id, project_id),
                        ).fetchone()
                        if part_ok is None:
                            raise ValueError("권/부를 찾을 수 없습니다.")
                    group_sql, group_params = self._chapter_group_filter_sql(part_id)
                    existing = connection.execute(
                        f"SELECT id FROM chapter "
                        f"WHERE project_id = ? AND deleted_at IS NULL AND {group_sql} "
                        f"ORDER BY sort_order, id",
                        (project_id, *group_params),
                    ).fetchall()
                    existing_ids = [int(row["id"]) for row in existing]
                    # Park new row at end first, then reorder if an insert position is given.
                    sort_order = connection.execute(
                        f"SELECT COALESCE(MAX(sort_order) + 1, 0) FROM chapter "
                        f"WHERE project_id = ? AND deleted_at IS NULL AND {group_sql}",
                        (project_id, *group_params),
                    ).fetchone()[0]
                    parent_folder_id = None
                    if part_id is not None:
                        parent_folder_id = folder_tree.folder_id_for_source(
                            connection, project_id, "part", int(part_id)
                        )
                        if parent_folder_id is None:
                            self._mirror_project_folders(connection, project_id)
                            parent_folder_id = folder_tree.folder_id_for_source(
                                connection, project_id, "part", int(part_id)
                            )
                    try:
                        folder_sort = folder_tree.next_folder_sibling_sort(
                            connection, project_id, parent_folder_id
                        )
                    except sqlite3.OperationalError:
                        folder_sort = int(sort_order)
                    folder_sort_order = max(int(sort_order), int(folder_sort))
                    # 1) folder first
                    try:
                        new_folder_id = folder_tree._insert_folder(
                            connection,
                            project_id=project_id,
                            parent_id=parent_folder_id,
                            title=title,
                            notes_md="",
                            is_box=0,
                            sort_order=folder_sort_order,
                            source_kind=None,
                            source_id=None,
                        )
                    except sqlite3.OperationalError:
                        new_folder_id = None
                    # 2) legacy chapter
                    cursor = connection.execute(
                        "INSERT INTO chapter(project_id, part_id, title, sort_order) "
                        "VALUES (?, ?, ?, ?)",
                        (project_id, part_id, title, sort_order),
                    )
                    new_id = int(cursor.lastrowid)
                    # 3) bind source_id to legacy chapter.id
                    if new_folder_id is not None:
                        folder_tree.bind_folder_source(
                            connection, new_folder_id, "chapter", new_id
                        )
                    else:
                        self._mirror_project_folders(connection, project_id)

                    target_index = None
                    if insert_before_id is not None and str(insert_before_id).strip() != "":
                        try:
                            before_id = int(insert_before_id)
                        except (TypeError, ValueError) as error:
                            raise ValueError("삽입 위치가 올바르지 않습니다.") from error
                        if before_id in existing_ids:
                            target_index = existing_ids.index(before_id)
                        else:
                            raise ValueError("삽입 기준 챕터를 찾을 수 없습니다.")
                    elif insert_index is not None and str(insert_index).strip() != "":
                        try:
                            target_index = int(insert_index)
                        except (TypeError, ValueError) as error:
                            raise ValueError("삽입 위치가 올바르지 않습니다.") from error
                        target_index = max(0, min(target_index, len(existing_ids)))

                    if target_index is not None:
                        ordered = existing_ids[:]
                        ordered.insert(target_index, new_id)
                        self._assign_chapter_sort_orders(
                            connection, project_id, ordered, part_id=part_id
                        )
                        folder_tree.reapply_chapter_folder_order(
                            connection, project_id, ordered, parent_folder_id
                        )
                    self._log_folder_create(
                        connection,
                        project_id=project_id,
                        folder_id=new_folder_id,
                        source_kind="chapter",
                        source_id=new_id,
                        title=title,
                    )
                self.send_json(
                    {
                        "id": new_id,
                        "part_id": part_id,
                        "insert_index": target_index if target_index is not None else len(existing_ids),
                    },
                    HTTPStatus.CREATED,
                )
                return

            match = re.fullmatch(r"/api/chapters/(\d+)/scenes", path)
            if match:
                self.send_json(
                    self.create_scene(int(match.group(1)), body),
                    HTTPStatus.CREATED,
                )
                return

            match = re.fullmatch(r"/api/projects/(\d+)/characters", path)
            if match:
                project_id = int(match.group(1))
                name = str(body.get("name", "새 캐릭터")).strip() or "새 캐릭터"
                with database() as connection:
                    self.require_project(connection, project_id)
                    sort_order = connection.execute(
                        "SELECT COALESCE(MAX(sort_order) + 1, 0) FROM character "
                        "WHERE project_id = ? AND deleted_at IS NULL",
                        (project_id,),
                    ).fetchone()[0]
                    cursor = connection.execute(
                        "INSERT INTO character(project_id, name, sort_order) VALUES (?, ?, ?)",
                        (project_id, name, sort_order),
                    )
                self.send_json({"id": cursor.lastrowid}, HTTPStatus.CREATED)
                return

            match = re.fullmatch(r"/api/projects/(\d+)/ideas", path)
            if match:
                self.send_json(self.create_idea(int(match.group(1)), body), HTTPStatus.CREATED)
                return

            match = re.fullmatch(r"/api/projects/(\d+)/baits", path)
            if match:
                self.send_json(self.create_bait(int(match.group(1)), body), HTTPStatus.CREATED)
                return

            match = re.fullmatch(r"/api/projects/(\d+)/baits/import", path)
            if match:
                self.send_json(self.import_baits(int(match.group(1)), body))
                return

            if path == "/api/ai/assist":
                self.send_json(self.ai_assist(body))
                return

            if path == "/api/spellcheck":
                self.send_json(self.spellcheck(body))
                return

            match = re.fullmatch(r"/api/characters/(\d+)/portrait", path)
            if match:
                self.send_json(self.save_character_portrait(int(match.group(1)), body))
                return

            match = re.fullmatch(r"/api/characters/(\d+)/aliases", path)
            if match:
                character_id = int(match.group(1))
                alias = str(body.get("alias", "")).strip()
                if not alias:
                    raise ValueError("별칭을 입력해 주세요.")
                with database() as connection:
                    character = connection.execute(
                        "SELECT project_id FROM character WHERE id = ? AND deleted_at IS NULL", (character_id,)
                    ).fetchone()
                    if character is None:
                        raise ValueError("캐릭터를 찾을 수 없습니다.")
                    cursor = connection.execute(
                        "INSERT INTO character_alias(character_id, project_id, alias, alias_type) VALUES (?, ?, ?, ?)",
                        (character_id, character["project_id"], alias, str(body.get("alias_type", "other"))),
                    )
                self.send_json({"id": cursor.lastrowid}, HTTPStatus.CREATED)
                return

            match = re.fullmatch(r"/api/scenes/(\d+)/illustrations", path)
            if match:
                self.send_json(self.create_illustration(int(match.group(1)), body), HTTPStatus.CREATED)
                return

            match = re.fullmatch(r"/api/scenes/(\d+)/duplicate", path)
            if match:
                self.send_json(self.duplicate_scene(int(match.group(1))), HTTPStatus.CREATED)
                return

            match = re.fullmatch(r"/api/scenes/(\d+)/summarize", path)
            if match:
                self.send_json(self.summarize_scene_for_index(int(match.group(1)), body))
                return

            match = re.fullmatch(r"/api/projects/(\d+)/index/merge", path)
            if match:
                self.send_json(self.merge_project_index(int(match.group(1)), body or {}))
                return

            match = re.fullmatch(r"/api/scenes/(\d+)/trash", path)
            if match:
                self.send_json(self.trash_scene(int(match.group(1))))
                return

            match = re.fullmatch(r"/api/chapters/(\d+)/trash", path)
            if match:
                self.send_json(self.trash_chapter(int(match.group(1))))
                return

            match = re.fullmatch(r"/api/parts/(\d+)/trash", path)
            if match:
                self.send_json(self.trash_part(int(match.group(1))))
                return

            match = re.fullmatch(r"/api/chapters/(\d+)/move", path)
            if match:
                self.send_json(self.move_chapter(int(match.group(1)), body))
                return

            # Folder tree reparent (unlimited depth; may leave legacy part/chapter incomplete)
            match = re.fullmatch(r"/api/folders/(\d+)/reparent", path)
            if match:
                self.send_json(self.reparent_folder(int(match.group(1)), body))
                return

            match = re.fullmatch(r"/api/projects/(\d+)/undo", path)
            if match:
                self.send_json(self.undo_project_folder_action(int(match.group(1)), body))
                return

            match = re.fullmatch(r"/api/projects/(\d+)/redo", path)
            if match:
                self.send_json(self.redo_project_folder_action(int(match.group(1)), body))
                return

            match = re.fullmatch(r"/api/scenes/(\d+)/reparent", path)
            if match:
                self.send_json(self.reparent_scene(int(match.group(1)), body))
                return

            match = re.fullmatch(r"/api/scenes/(\d+)/move", path)
            if match:
                self.send_json(self.move_scene(int(match.group(1)), body))
                return

            match = re.fullmatch(r"/api/scenes/(\d+)/restore", path)
            if match:
                self.send_json(self.restore_scene(int(match.group(1))))
                return

            if path == "/api/import":
                self.send_json(self.import_document(body), HTTPStatus.CREATED)
                return

            # Lightweight text extract for reference-file viewer (no project import).
            if path == "/api/extract-text":
                self.send_json(self.extract_reference_text(body))
                return

            match = re.fullmatch(r"/api/projects/(\d+)/import", path)
            if match:
                self.send_json(self.import_document(body, project_id=int(match.group(1))), HTTPStatus.CREATED)
                return

            # Match uploaded proof text to the closest existing episode (회차/scene).
            match = re.fullmatch(r"/api/projects/(\d+)/match-episode", path)
            if match:
                self.send_json(self.match_project_episode(int(match.group(1)), body))
                return

            # Compare original vs editor-revised manuscript (교정/교열 보고서).
            match = re.fullmatch(r"/api/projects/(\d+)/proof-diff", path)
            if match:
                self.send_json(self.proof_diff_project(int(match.group(1)), body))
                return

            # Clean HWP proof extract → pure body text.
            if path == "/api/proof-clean":
                self.send_json(self.clean_proof_text_api(body))
                return
            match = re.fullmatch(r"/api/projects/(\d+)/proof-clean", path)
            if match:
                with database() as connection:
                    self.require_project(connection, int(match.group(1)))
                self.send_json(self.clean_proof_text_api(body))
                return

            # Unified HWP/DOCX proof pipeline (extract → match → clean → diff).
            match = re.fullmatch(r"/api/projects/(\d+)/proof-pipeline", path)
            if match:
                self.send_json(self.run_proof_pipeline_api(int(match.group(1)), body))
                return

            # Project settings (also available via PUT).
            match = re.fullmatch(r"/api/projects/(\d+)/settings", path)
            if match:
                self.send_json(self.update_project_settings(int(match.group(1)), body))
                return

            # Bulk-apply scene goal count/metric to every active scene in a project.
            match = re.fullmatch(r"/api/projects/(\d+)/scene-goals", path)
            if match:
                self.send_json(self.bulk_set_scene_goals(int(match.group(1)), body))
                return

            # Graceful app exit (header X / future desktop shell).
            if path == "/api/app/quit":
                self.send_json({"ok": True})
                # shutdown() must not run on the request thread (deadlock).
                Timer(0.15, self.server.shutdown).start()
                return

            if path == "/api/writing/prefs":
                self.send_json(self.update_writing_prefs(body))
                return

            if path == "/api/writing/heartbeat":
                self.send_json(self.writing_heartbeat(body))
                return

            if path == "/api/writing/days/clear":
                self.send_json(self.clear_writing_days(body))
                return

            if path == "/api/writing/pair":
                self.send_json(self.issue_writing_pair(body))
                return

            match = re.fullmatch(r"/api/writing/inbox/(\d+)/read", path)
            if match:
                self.send_json(self.mark_mobile_inbox_read(int(match.group(1))))
                return

            if path == "/api/mobile/push":
                self.send_json(self.mobile_push_text(body), HTTPStatus.CREATED)
                return
        except ValueError as error:
            self.api_error(str(error))
            return
        except sqlite3.IntegrityError as error:
            self.api_error(f"저장할 수 없습니다: {error}")
            return
        except sqlite3.Error as error:
            self.api_error(f"데이터베이스 오류: {error}", HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self.api_error("알 수 없는 요청입니다.", HTTPStatus.NOT_FOUND)

    def do_PUT(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/")
        try:
            body = self.read_json()
            match = re.fullmatch(r"/api/scenes/(\d+)", path)
            if match:
                self.send_json(self.save_scene(int(match.group(1)), body))
                return

            match = re.fullmatch(r"/api/scenes/(\d+)/summary", path)
            if match:
                self.send_json(self.upsert_scene_summary(int(match.group(1)), body))
                return

            match = re.fullmatch(r"/api/chapters/(\d+)", path)
            if match:
                self.save_chapter(int(match.group(1)), body)
                self.send_json({"ok": True})
                return

            match = re.fullmatch(r"/api/parts/(\d+)", path)
            if match:
                self.send_json(self.save_part(int(match.group(1)), body))
                return

            match = re.fullmatch(r"/api/folders/(\d+)", path)
            if match:
                self.send_json(self.save_folder(int(match.group(1)), body))
                return

            match = re.fullmatch(r"/api/scenes/(\d+)/characters", path)
            if match:
                self.save_scene_characters(int(match.group(1)), body)
                self.send_json({"ok": True})
                return

            match = re.fullmatch(r"/api/characters/(\d+)", path)
            if match:
                self.save_character(int(match.group(1)), body)
                self.send_json({"ok": True})
                return

            match = re.fullmatch(r"/api/illustrations/(\d+)", path)
            if match:
                self.send_json(self.update_illustration(int(match.group(1)), body))
                return

            match = re.fullmatch(r"/api/projects/(\d+)/chapters/reorder", path)
            if match:
                self.reorder_chapters(int(match.group(1)), body)
                self.send_json({"ok": True})
                return

            if path == "/api/projects/reorder":
                self.send_json(self.reorder_projects(body))
                return

            if path == "/api/projects/list-mode":
                self.send_json(self.set_projects_list_mode(body))
                return

            match = re.fullmatch(r"/api/projects/(\d+)/parts/reorder", path)
            if match:
                self.reorder_parts(int(match.group(1)), body)
                self.send_json({"ok": True})
                return

            match = re.fullmatch(r"/api/projects/(\d+)/chapters/renumber-titles", path)
            if match:
                self.send_json(self.renumber_chapter_titles(int(match.group(1)), body))
                return

            match = re.fullmatch(r"/api/projects/(\d+)/settings", path)
            if match:
                self.send_json(self.update_project_settings(int(match.group(1)), body))
                return

            match = re.fullmatch(r"/api/ideas/(\d+)", path)
            if match:
                self.send_json(self.update_idea(int(match.group(1)), body))
                return

            match = re.fullmatch(r"/api/baits/([^/]+)", path)
            if match:
                self.send_json(self.update_bait(match.group(1), body))
                return
        except ValueError as error:
            self.api_error(str(error))
            return
        except sqlite3.IntegrityError as error:
            self.api_error(f"저장할 수 없습니다: {error}")
            return
        except sqlite3.Error as error:
            self.api_error(f"데이터베이스 오류: {error}", HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self.api_error("알 수 없는 요청입니다.", HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/")
        try:
            match = re.fullmatch(r"/api/illustrations/(\d+)", path)
            if match:
                self.delete_illustration(int(match.group(1)))
                self.send_json({"ok": True})
                return

            match = re.fullmatch(r"/api/ideas/(\d+)", path)
            if match:
                self.delete_idea(int(match.group(1)))
                self.send_json({"ok": True})
                return

            match = re.fullmatch(r"/api/baits/([^/]+)", path)
            if match:
                self.delete_bait(match.group(1))
                self.send_json({"ok": True})
                return

            match = re.fullmatch(r"/api/scenes/(\d+)/purge", path)
            if match:
                self.send_json(self.purge_scene(int(match.group(1))))
                return

            match = re.fullmatch(r"/api/projects/(\d+)/trash", path)
            if match:
                self.send_json(self.empty_trash(int(match.group(1))))
                return

            match = re.fullmatch(r"/api/projects/(\d+)", path)
            if match:
                self.send_json(self.trash_project(int(match.group(1))))
                return

            match = re.fullmatch(r"/api/characters/(\d+)", path)
            if match:
                self.send_json(self.trash_character(int(match.group(1))))
                return

            match = re.fullmatch(r"/api/characters/(\d+)/portrait", path)
            if match:
                self.send_json(self.clear_character_portrait(int(match.group(1))))
                return
        except ValueError as error:
            self.api_error(str(error), HTTPStatus.NOT_FOUND)
            return
        except sqlite3.Error as error:
            self.api_error(f"데이터베이스 오류: {error}", HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self.api_error("알 수 없는 요청입니다.", HTTPStatus.NOT_FOUND)

    def spellcheck(self, body: dict) -> dict:
        """Korean spelling/spacing check (external 바른한글 service, optional Gemini fallback)."""
        raw = body.get("text", "")
        text = plain_text_from_content(str(raw or ""))
        if not text.strip():
            raise ValueError("검사할 글을 먼저 적어 주세요.")

        prefer = str(body.get("prefer") or "auto").strip().lower()
        gemini_ready = bool(gemini_client.status().get("configured"))

        # Cloudflare often blocks the public checker from servers/apps.
        # When Gemini is configured, prefer it for reliability unless user asks public-first.
        if prefer == "public":
            try:
                return korean_speller.check_text(text)
            except korean_speller.SpellerError as error:
                if not gemini_ready:
                    raise ValueError(str(error)) from error
                return self._spellcheck_with_gemini(text, public_error=str(error))

        if prefer == "gemini" or (prefer == "auto" and gemini_ready):
            if not gemini_ready:
                raise ValueError("Gemini API 키가 없습니다. .env 에 GEMINI_API_KEY 를 넣어 주세요.")
            # auto+gemini: try Gemini first; on Gemini failure try public.
            try:
                return self._spellcheck_with_gemini(text, public_error=None)
            except ValueError as gemini_error:
                if prefer == "gemini":
                    raise
                try:
                    result = korean_speller.check_text(text)
                    result["message"] = (
                        f"Gemini 검사 실패 후 공개 검사기로 검사했습니다. {result.get('message', '')}"
                    ).strip()
                    return result
                except korean_speller.SpellerError as public_error:
                    raise ValueError(
                        f"{gemini_error} / 공개 검사기도 실패: {public_error}"
                    ) from public_error

        # No Gemini key — public only.
        try:
            return korean_speller.check_text(text)
        except korean_speller.SpellerError as error:
            raise ValueError(
                f"{error} (Gemini API 키가 있으면 보조 검사가 가능합니다.)"
            ) from error

    def _spellcheck_with_gemini(self, text: str, public_error: str | None = None) -> dict:
        """Gemini-backed spelling suggestions when the public checker is unavailable."""
        try:
            system = (
                "당신은 한국어 맞춤법·띄어쓰기·문장 교정 전문가입니다. "
                "틀린 표기, 잘못된 띄어쓰기, 분명한 문법 오류만 찾습니다. "
                "문체·창작 표현·고유명사는 오류로 잡지 마세요. "
                "반드시 JSON 배열만 출력하세요. 키: original(원문 부분 문자열), "
                "suggestions(교정 후보 문자열 배열, 1개 이상), help(짧은 한국어 설명). "
                "코드펜스·서문·후기 없이 JSON만 출력하세요."
            )
            prompt = (
                "다음 한국어 글에서 맞춤법·띄어쓰기·분명한 표기 오류를 모두 찾아 "
                "JSON 배열로 알려 주세요. 오류가 정말 없으면 [] 만 출력하세요.\n\n"
                f"{text[:8000]}"
            )
            reply = gemini_client.generate_text(prompt, system=system)
            payload = (reply or "").strip()
            if payload.startswith("```"):
                payload = re.sub(r"^```(?:json)?\s*", "", payload)
                payload = re.sub(r"\s*```$", "", payload)
            # Tolerate leading junk before the array.
            bracket = payload.find("[")
            if bracket > 0:
                payload = payload[bracket:]
            end = payload.rfind("]")
            if end >= 0:
                payload = payload[: end + 1]
            errors_raw = json.loads(payload)
            if not isinstance(errors_raw, list):
                raise ValueError("형식 오류")
            errors = []
            for item in errors_raw[:50]:
                if not isinstance(item, dict):
                    continue
                original = str(item.get("original") or "").strip()
                if not original:
                    continue
                suggestions = item.get("suggestions") or []
                if isinstance(suggestions, str):
                    suggestions = [suggestions]
                suggestions = [str(s).strip() for s in suggestions if str(s).strip() and str(s).strip() != original]
                if not suggestions:
                    continue
                # Only keep suggestions that can be found / applied in the text.
                if original not in text and not any(s in text for s in []):
                    # still show if original is substring-ish; skip if totally missing
                    if original not in text:
                        continue
                errors.append(
                    {
                        "original": original,
                        "suggestions": suggestions[:8],
                        "help": str(item.get("help") or "").strip(),
                        "start": text.find(original),
                        "end": text.find(original) + len(original) if original in text else -1,
                        "method": 0,
                        "method_label": "AI 교정",
                    }
                )
            prefix = ""
            if public_error:
                prefix = f"공개 검사기 실패({public_error}) → Gemini 보조 검사. "
            return {
                "ok": True,
                "provider": "gemini-fallback" if public_error else "gemini",
                "provider_label": "Gemini 보조 검사" if public_error else "Gemini 맞춤법 검사",
                "errors": errors,
                "error_count": len(errors),
                "checked_chars": len(text),
                "chunk_count": 1,
                "public_error": public_error,
                "message": (
                    f"{prefix}오류 {len(errors)}건을 제안합니다."
                    if errors
                    else f"{prefix}눈에 띄는 오류를 찾지 못했습니다."
                ),
            }
        except Exception as fallback_error:
            if public_error:
                raise ValueError(
                    f"{public_error} (Gemini 보조 검사도 실패: {fallback_error})"
                ) from fallback_error
            raise ValueError(f"Gemini 맞춤법 검사 실패: {fallback_error}") from fallback_error

    def ai_assist(self, body: dict) -> dict:
        """Writing helper powered by Gemini (.env GEMINI_API_KEY)."""
        mode = str(body.get("mode", "free") or "free").strip().lower()
        allowed = {
            "continue", "rewrite", "summarize", "summarize_multi",
            "ideas", "ideas_next_exists",
            "analyze", "analyze_multi", "brainstorm", "brainstorm_next_exists",
            "foreshadow", "plottwist", "worldscan", "worldscan_multi",
            "worlddesc", "dupcheck", "free", "chat", "subsynopsis", "styleblend",
        }
        if mode not in allowed:
            raise ValueError("지원하지 않는 AI 도움 방식입니다.")

        # Deferred project-index merge before any Tory feature runs.
        project_id_raw = body.get("project_id")
        try:
            project_id_for_index = int(project_id_raw) if project_id_raw not in (None, "") else 0
        except (TypeError, ValueError):
            project_id_for_index = 0
        index_merge_info = None
        if project_id_for_index:
            try:
                index_merge_info = self.merge_project_index(
                    project_id_for_index,
                    {"quiet": True, "only_if_dirty": True},
                )
            except Exception:
                index_merge_info = {"ok": False, "skipped": True}

        # Contract: { system_instruction_vars: {...}, user_prompt / prompt }
        siv = body.get("system_instruction_vars")
        if not isinstance(siv, dict):
            siv = {}
        user_prompt = str(
            body.get("user_prompt") or body.get("prompt") or ""
        ).strip()
        scene_title = str(body.get("scene_title", "")).strip()
        scene_synopsis = str(body.get("scene_synopsis", "")).strip()
        scene_content = plain_text_from_content(str(body.get("scene_content", "") or ""))
        project_title = str(body.get("project_title", "")).strip()
        world_setting_text = plain_text_from_content(
            str(body.get("world_setting_text") or body.get("worldbuilding_md") or "")
        )
        focus_only = bool(body.get("focus_scene_only") or body.get("tory_focus"))
        purpose = document_import.normalise_purpose(body.get("purpose") or "general_novel")
        purpose_label = document_import.WORK_PURPOSES.get(purpose, purpose)
        main_genre_key = str(body.get("main_genre") or "").strip()
        sub_genre_key = str(body.get("sub_genre") or "").strip()
        # Prefer system_instruction_vars labels when provided (absolute project context)
        main_genre_label = self._genre_display_label(
            siv.get("project_genre_main") or body.get("main_genre_label") or body.get("project_genre_main"),
            main_genre_key,
        )
        sub_genre_label = self._genre_display_label(
            siv.get("project_genre_sub") or body.get("sub_genre_label") or body.get("project_genre_sub"),
            sub_genre_key,
        )
        keywords = parse_project_keywords(body.get("keywords"))
        keywords_from_siv = str(
            siv.get("world_setting_keywords")
            or body.get("world_setting_keywords")
            or ""
        ).strip()
        keywords_label = (
            keywords_from_siv
            or (", ".join(keywords) if keywords else "미정")
        )
        # World / character context may be injected by the client (dynamic override)
        world_for_context = plain_text_from_content(
            str(
                siv.get("world_setting")
                or body.get("world_setting")
                or body.get("world_setting_text")
                or body.get("worldbuilding_md")
                or world_setting_text
                or keywords_from_siv
                or ""
            )
        )
        if len(world_for_context) > 6000:
            world_for_context = world_for_context[:6000] + "…"
        character_profiles_raw = (
            siv.get("character_profiles")
            if siv.get("character_profiles") is not None
            else body.get("character_profiles")
        )
        character_profiles_text = self._format_character_profiles(character_profiles_raw)
        if len(character_profiles_text) > 4000:
            character_profiles_text = character_profiles_text[:4000] + "…"

        tory_priority = str(
            siv.get("tory_priority_md")
            or body.get("tory_priority_md")
            or body.get("author_priority")
            or ""
        ).strip()
        # Prefer saved project value when client omitted it but project_id is known.
        if not tory_priority:
            project_id_raw = body.get("project_id")
            try:
                project_id_for_priority = int(project_id_raw) if project_id_raw not in (None, "") else 0
            except (TypeError, ValueError):
                project_id_for_priority = 0
            if project_id_for_priority:
                try:
                    with database() as connection:
                        self.require_project(connection, project_id_for_priority)
                        prow = connection.execute(
                            "SELECT tory_priority_md FROM project WHERE id = ?",
                            (project_id_for_priority,),
                        ).fetchone()
                        if prow and "tory_priority_md" in prow.keys():
                            tory_priority = str(prow["tory_priority_md"] or "").strip()
                except Exception:
                    tory_priority = tory_priority

        active_project_context = self._tory_active_project_context(
            main_genre_label=main_genre_label,
            sub_genre_label=sub_genre_label,
            purpose_label=purpose_label,
            keywords_label=keywords_label,
            world_setting=world_for_context,
            character_profiles=character_profiles_text or character_profiles_raw,
            project_title=project_title,
        )
        genre_system = (
            self._tory_author_priority_system_prompt(tory_priority)
            + self._tory_core_identity_system_prompt()
            + self._tory_dynamic_context_system_prompt(
                main_genre_label=main_genre_label,
                sub_genre_label=sub_genre_label,
                world_setting_keywords=keywords_label,
                character_profiles=character_profiles_text or character_profiles_raw,
            )
        )
        genre_context = (
            f"메인 장르: {main_genre_label}\n"
            f"서브 장르: {sub_genre_label}\n"
            f"키워드/태그: {keywords_label}"
        )
        persona_mode = str(
            body.get("persona_mode")
            or body.get("tory_persona")
            or siv.get("persona_mode")
            or "default"
        ).strip().lower()
        persona_system = self._tory_persona_system_prompt(persona_mode)

        # ── 1:1 chat with Tory (multi-turn) ──────────────────────────
        if mode == "chat":
            if not user_prompt:
                raise ValueError("토리에게 할 말을 적어 주세요.")
            chat_mode = str(
                body.get("chat_mode") or body.get("chatMode") or body.get("chat_session") or "general"
            ).strip()
            is_success_analysis = chat_mode in {
                "successAnalysis",
                "success_analysis",
                "successanalysis",
                "success",
            }
            history_raw = body.get("history") or body.get("messages") or []
            history_lines: list[str] = []
            if isinstance(history_raw, list):
                for item in history_raw[-24:]:
                    if not isinstance(item, dict):
                        continue
                    role = str(item.get("role") or "").strip().lower()
                    content = str(item.get("content") or item.get("text") or "").strip()
                    if not content:
                        continue
                    if len(content) > 4000:
                        content = content[:4000] + "…"
                    who = "작가" if role in {"user", "human", "author"} else "토리"
                    history_lines.append(f"{who}: {content}")
            system = (
                genre_system
                + persona_system
                + "[Chat Mode]\n"
                "작가와 1:1로 대화하며 창작을 돕습니다. "
                "원고를 통째로 다시 쓰지 말고, 대화·조언·브레인스토밍·짧은 예시를 중심으로 하세요. "
                f"작품 종류는 '{purpose_label}'입니다. "
                "답변 전에 반드시 [현재 프로젝트 메타데이터]와 [Current Active Project Context]를 먼저 읽고 "
                "해당 장르 문법·톤앤매너만 적용하세요. "
                "[Tory Core Identity]를 항상 유지하고, "
                "말투만 [Current Persona Mode] 톤을 끝까지 따르세요."
            )
            if is_success_analysis:
                sp_raw = body.get("success_profile") or body.get("successProfile") or {}
                if not isinstance(sp_raw, dict):
                    sp_raw = {}
                # Allow nested profile from API row
                if isinstance(sp_raw.get("profile"), dict):
                    nested = sp_raw.get("profile") or {}
                    sp_raw = {**nested, **{k: v for k, v in sp_raw.items() if k != "profile"}}
                system += "\n" + success_pattern.build_success_analyst_chat_scope(sp_raw)
            context_bits = [
                active_project_context,
                f"작품 제목: {project_title or '(없음)'}",
                f"작품 종류: {purpose_label}",
            ]
            if scene_title or scene_content:
                context_bits.append(f"현재 열린 회차: {scene_title or '(제목 없음)'}")
                if scene_synopsis:
                    context_bits.append(f"회차 요약: {scene_synopsis}")
                if scene_content:
                    # Light context only — chat is conversation-first
                    snippet = scene_content[-6000:] if len(scene_content) > 6000 else scene_content
                    context_bits.append(f"현재 원고(참고):\n{snippet}")
            transcript = "\n".join(history_lines) if history_lines else "(이전 대화 없음)"
            full_prompt = (
                "아래는 작가와 토리의 이전 대화, 작품 맥락, 그리고 작가의 새 메시지입니다. "
                "토리로서 새 메시지에만 이어서 답하세요. 이름 접두어(토리:)는 붙이지 마세요.\n"
                "이전 대화에 다른 장르 클리셰가 섞여 있어도, "
                "지금 주입된 [현재 프로젝트 메타데이터]·[Current Active Project Context]만 절대 기준으로 삼으세요.\n\n"
                f"[작품·원고 맥락]\n" + "\n".join(context_bits) + "\n\n"
                f"[이전 대화]\n{transcript}\n\n"
                f"[작가의 새 메시지]\n{user_prompt}"
            )
            try:
                text = gemini_client.generate_text(full_prompt, system=system, temperature=0.9)
            except gemini_client.GeminiError as error:
                raise ValueError(str(error)) from error
            return {
                "mode": "chat",
                "chat_mode": "successAnalysis" if is_success_analysis else "general",
                "text": text,
                "model": gemini_client.model_name(),
                "provider": "google-gemini",
                "main_genre": main_genre_key,
                "sub_genre": sub_genre_key,
                "main_genre_label": main_genre_label,
                "sub_genre_label": sub_genre_label,
            }

        # Cap context size for local responsiveness.
        # multi modes: multi-episode bodies (per-episode cap client-side).
        if mode in {"worldscan_multi", "analyze_multi", "summarize_multi"}:
            if mode == "worldscan_multi":
                multi_cap = 60000  # up to 5 × 12k
            elif mode == "analyze_multi":
                multi_cap = 36000  # up to 3 × 12k
            else:
                multi_cap = 240000  # up to 20 × 12k (summarize_multi)
            if len(scene_content) > multi_cap:
                scene_content = scene_content[:multi_cap]
        elif len(scene_content) > 12000:
            scene_content = scene_content[-12000:]
        if len(world_setting_text) > 20000:
            world_setting_text = world_setting_text[:20000]

        foreshadow = body.get("foreshadow") if isinstance(body.get("foreshadow"), dict) else {}
        foreshadow_title = str(foreshadow.get("title") or "").strip()
        foreshadow_target = str(foreshadow.get("target") or "").strip()
        buildup_raw = foreshadow.get("buildup") or []
        if isinstance(buildup_raw, str):
            buildup_list = [line.strip() for line in buildup_raw.splitlines() if line.strip()]
        elif isinstance(buildup_raw, list):
            buildup_list = [str(item).strip() for item in buildup_raw if str(item).strip()]
        else:
            buildup_list = []

        if mode == "free" and not user_prompt:
            raise ValueError("AI에게 요청할 내용을 적어 주세요.")
        # Lore Keeper structured fields (worldscan / optional override of scene_content)
        lore_payload = body.get("lore_payload") if isinstance(body.get("lore_payload"), dict) else {}
        world_setting_field = plain_text_from_content(
            str(
                body.get("world_setting")
                or lore_payload.get("world_setting")
                or world_setting_text
                or ""
            )
        )
        genre_rules_field = str(
            body.get("genre_rules")
            or lore_payload.get("genre_rules")
            or ""
        ).strip()
        if not genre_rules_field:
            genre_rules_field = (
                f"{main_genre_label}"
                + (f" / {sub_genre_label}" if sub_genre_label and sub_genre_label != "미정" else "")
                + (f" · 키워드: {keywords_label}" if keywords_label != "미정" else "")
            ).strip(" ·")
        character_profiles_raw = (
            body.get("character_profiles")
            if body.get("character_profiles") is not None
            else lore_payload.get("character_profiles")
        )
        character_profiles_text = self._format_character_profiles(character_profiles_raw)
        try:
            sensitivity_level = int(
                body.get("sensitivity_level")
                if body.get("sensitivity_level") is not None
                else lore_payload.get("sensitivity_level", 4)
            )
        except (TypeError, ValueError):
            sensitivity_level = 4
        sensitivity_level = max(1, min(5, sensitivity_level))
        target_text_override = plain_text_from_content(
            str(body.get("target_text") or lore_payload.get("target_text") or "")
        )
        if mode == "worldscan" and target_text_override.strip():
            scene_content = target_text_override
        if mode == "worldscan" and world_setting_field.strip():
            world_setting_text = world_setting_field
        if mode == "worldscan_multi" and world_setting_field.strip():
            world_setting_text = world_setting_field
        # Re-cap after lore overrides
        if mode in {"worldscan_multi", "analyze_multi", "summarize_multi"}:
            if mode == "worldscan_multi":
                multi_cap = 60000
            elif mode == "analyze_multi":
                multi_cap = 36000
            else:
                multi_cap = 240000
            if len(scene_content) > multi_cap:
                scene_content = scene_content[:multi_cap]
        elif len(scene_content) > 12000:
            scene_content = scene_content[-12000:]
        if len(world_setting_text) > 20000:
            world_setting_text = world_setting_text[:20000]

        if mode == "worldscan":
            if not scene_content:
                raise ValueError("검사할 원고를 먼저 열어 주세요.")
        if mode == "worldscan_multi":
            if not scene_content:
                raise ValueError("검사할 원고를 먼저 열어 주세요.")
        if mode == "analyze":
            if not scene_content:
                raise ValueError("피드백할 원고를 먼저 열어 주세요.")
        if mode == "analyze_multi":
            if not scene_content:
                raise ValueError("피드백할 원고를 먼저 열어 주세요.")
        if mode == "summarize_multi":
            if not scene_content:
                raise ValueError("요약할 원고를 먼저 열어 주세요.")
        if mode == "dupcheck":
            if not scene_content:
                raise ValueError("중복 체크할 원고를 먼저 열어 주세요.")
        if mode in {"foreshadow", "plottwist"}:
            if not foreshadow_title:
                raise ValueError("검수할 복선 제목을 등록·선택해 주세요.")
            if not foreshadow_target:
                raise ValueError("반전 목표 장(예: 12장)을 적어 주세요.")
            if not buildup_list:
                raise ValueError("빌드업·단서를 한 줄 이상 적어 주세요.")
            if not scene_content:
                raise ValueError("검수할 현재 원고를 먼저 열어 주세요.")
        if mode in {"continue", "rewrite", "summarize", "analyze", "brainstorm", "worlddesc"} and not scene_content and not user_prompt:
            raise ValueError("먼저 원고를 쓰거나, 요청 내용을 적어 주세요.")
        if mode == "subsynopsis":
            # Index + outline_summary only — manuscript not required.
            pass

        if mode == "worldscan":
            # 설정 붕괴 감지기 — task prompt only (no Core Identity re-declaration).
            indexed_worldscan = str(body.get("indexed_prompt") or "").strip()
            system = genre_system
            if indexed_worldscan:
                instruction = indexed_worldscan
            else:
                instruction = self._build_setting_break_scan_prompt(scene_content)
            context_parts = [
                active_project_context,
                f"작품 제목: {project_title or '(없음)'}",
                f"작품 종류: {purpose_label}",
                genre_context,
                f"씬 제목: {scene_title or '(없음)'}",
                f"씬 요약: {scene_synopsis or '(없음)'}",
            ]
            if world_setting_text.strip():
                context_parts.append(f"[설정집 · 세계관]\n{world_setting_text[:12000]}")
            if character_profiles_text.strip():
                context_parts.append(f"[캐릭터 프로필]\n{character_profiles_text[:8000]}")
            elif isinstance(character_profiles_raw, (dict, list)) and character_profiles_raw:
                try:
                    profiles_dump = json.dumps(character_profiles_raw, ensure_ascii=False, indent=2)
                except (TypeError, ValueError):
                    profiles_dump = str(character_profiles_raw)
                if len(profiles_dump) > 8000:
                    profiles_dump = profiles_dump[:8000] + "\n…(truncated)"
                context_parts.append(f"[캐릭터 프로필]\n{profiles_dump}")
            if user_prompt:
                context_parts.append(f"작가 추가 요청:\n{user_prompt}")
            # Manuscript already embedded in indexed/task prompt — do not duplicate below.
        elif mode == "worldscan_multi":
            # Multi-episode setting-break scan — same lore context as worldscan, one call.
            indexed_multi = str(body.get("indexed_prompt") or "").strip()
            system = genre_system
            if indexed_multi:
                instruction = indexed_multi
            else:
                instruction = self._build_setting_break_scan_multi_prompt(scene_content)
            context_parts = [
                active_project_context,
                f"작품 제목: {project_title or '(없음)'}",
                f"작품 종류: {purpose_label}",
                genre_context,
                f"검사 회차: {scene_title or '(없음)'}",
                f"씬 요약: {scene_synopsis or '(없음)'}",
            ]
            if world_setting_text.strip():
                context_parts.append(f"[설정집 · 세계관]\n{world_setting_text[:12000]}")
            if character_profiles_text.strip():
                context_parts.append(f"[캐릭터 프로필]\n{character_profiles_text[:8000]}")
            elif isinstance(character_profiles_raw, (dict, list)) and character_profiles_raw:
                try:
                    profiles_dump = json.dumps(
                        character_profiles_raw, ensure_ascii=False, indent=2
                    )
                except (TypeError, ValueError):
                    profiles_dump = str(character_profiles_raw)
                if len(profiles_dump) > 8000:
                    profiles_dump = profiles_dump[:8000] + "\n…(truncated)"
                context_parts.append(f"[캐릭터 프로필]\n{profiles_dump}")
            if user_prompt:
                context_parts.append(f"작가 추가 요청:\n{user_prompt}")
            # Manuscript already embedded in indexed/task prompt — do not duplicate below.
        elif mode == "dupcheck":
            # Neighbor episode texts (±4) + optional local phrase hits from client
            neighbors_raw = body.get("neighbor_scenes") or body.get("neighbors") or []
            neighbor_blocks = []
            if isinstance(neighbors_raw, list):
                for item in neighbors_raw[:8]:
                    if not isinstance(item, dict):
                        continue
                    n_title = str(item.get("title") or item.get("label") or "").strip()
                    n_index = item.get("index") or item.get("episode_index") or ""
                    n_text = plain_text_from_content(str(item.get("content") or item.get("text") or ""))
                    if len(n_text) > 6000:
                        n_text = n_text[:6000] + "…"
                    if not n_text.strip():
                        continue
                    neighbor_blocks.append(
                        f"--- 회차 {n_index} · {n_title or '(제목 없음)'} ---\n{n_text}"
                    )
            local_hits = body.get("local_hits") or body.get("phrase_hits") or []
            local_block = ""
            if isinstance(local_hits, list) and local_hits:
                lines = []
                for hit in local_hits[:40]:
                    if isinstance(hit, dict):
                        phrase = str(hit.get("phrase") or hit.get("text") or "").strip()
                        where = str(hit.get("where") or hit.get("neighbor") or "").strip()
                        kind = str(hit.get("kind") or "표현").strip()
                        if phrase:
                            lines.append(f"- [{kind}] 「{phrase[:120]}」 ↔ {where or '인근 회차'}")
                    elif str(hit).strip():
                        lines.append(f"- {str(hit).strip()[:160]}")
                if lines:
                    local_block = "\n".join(lines)

            system = (
                genre_system
                + "[Tory Core Identity]를 유지한 채, 지금은 원고의 중복을 찾아 관찰하는 역할에 집중하세요. "
                "현재 회차 안에서 비슷한 표현이 반복되는지, 그리고 앞뒤 인근 회차(최대 ±4)와 "
                "같은 정보를 다시 설명하는 부분이 있는지 찾아줘. "
                "발견한 것을 고치라고 지시하지 말고, 사실과 짧은 관찰만 전달해. "
                "어떻게 할지는 작가가 결정할 몫이야. "
                "답변은 한국어로 간결하게 작성해."
            )
            instruction = (
                "중복 체크 목적: 원고 안에서 반복되는 표현과, 앞뒤 인근 회차와 겹치는 "
                "설명(정보)을 찾아 작가에게 보여준다. 무엇을 고칠지는 전적으로 작가의 "
                "판단이므로, 반드시 고쳐야 한다고 단정하거나 지시하지 않는다.\n\n"
                "다음 형식으로 답해 주세요.\n\n"
                "## 중복 표현 (현재 회차 안에서)\n"
                "- 비슷한 구조·어휘의 문장이 같은 회차 안에 반복되는 경우를 짚는다.\n"
                "  (예: \"눈썹을 치켜떴다\" / \"눈썹을 찡그렸다\"처럼 유사한 동작 묘사가\n"
                "  거듭될 때, \"머리가 바람에 휘날렸다\" / \"머리칼이 바람에 휘날리기\n"
                "  시작했다\"처럼 표현만 살짝 바뀐 문장이 반복될 때)\n"
                "- 각 항목: 문장 A ↔ 문장 B (위치) + 짧은 관찰 코멘트\n"
                "  (예: \"바로 앞 문단에 나온 표현이라, 비슷한 표현이 또 나오면\n"
                "  다소 반복적으로 느껴질 수 있어요.\")\n"
                "- 없으면 「없음」\n\n"
                "## 중복 설명 (앞뒤 4회차 이내)\n"
                "- 같은 정보나 배경 설명이 인근 회차에서 이미 전달된 적 있는지 확인한다.\n"
                "  (예: \"회귀자라서 알고 있었다\"는 설명이 여러 회차에서 각기 다른 문장으로\n"
                "  반복 등장하는 경우)\n"
                "- 각 항목: 현재 회차의 문장 ↔ 어느 회차의 어떤 설명 (인용) + 짧은 관찰 코멘트\n"
                "  (예: \"이 정보는 3화에서 이미 전달돼서, 다시 설명하지 않아도 독자가\n"
                "  알고 있을 가능성이 높아요.\")\n"
                "- 없으면 「없음」\n\n"
                "주의:\n"
                "- 발견한 것을 \"문제\"나 \"수정 필요\"로 단정하지 않는다. 사실을 보여주고\n"
                "  짧은 관찰을 덧붙이는 데 그친다 (\"~해 보여요\", \"~일 수 있어요\" 같은\n"
                "  표현을 쓴다).\n"
                "- 장르 클리셰나 관용적 표현, 고유명사 반복만으로 과도하게 잡지 않는다.\n"
                "- 정말 미묘하거나 확신이 없는 경우는 포함하지 않는다."
            )
            context_parts = [
                f"작품 제목: {project_title or '(없음)'}",
                f"작품 종류: {purpose_label}",
                genre_context,
                f"현재 회차 제목: {scene_title or '(없음)'}",
                f"현재 회차 요약: {scene_synopsis or '(없음)'}",
                f"[현재 회차 원고]\n{scene_content}",
            ]
            if neighbor_blocks:
                context_parts.append(
                    "[인근 회차 원고 (앞뒤 최대 4회차)]\n" + "\n\n".join(neighbor_blocks)
                )
            else:
                context_parts.append("[인근 회차 원고] (없음 — 현재 회차만 검토)")
            if local_block:
                context_parts.append(
                    "[자동 탐지된 후보 표현 (참고)]\n" + local_block
                )
            if user_prompt:
                context_parts.append(f"작가 추가 요청:\n{user_prompt}")
        elif mode in {"foreshadow", "plottwist"}:
            buildup_text = "\n".join(f"- {step}" for step in buildup_list)
            if mode == "foreshadow":
                registered_block = (
                    f"[등록된 복선·단서 목록 — 단서 심기 검수용]\n"
                    f'- title: "{foreshadow_title}"\n'
                    f'- target: "{foreshadow_target}"\n'
                    f"- buildup:\n{buildup_text}"
                )
                current_block = (
                    f"[현재 원고 — 단서가 심겼는지 확인할 구간] "
                    f"({foreshadow_target or scene_title or '현재 장'} / "
                    f"씬: {scene_title or '(제목 없음)'})\n{scene_content}"
                )
            else:
                registered_block = (
                    f"[반전 지지용 등록 빌드업 — 개연성 평가 근거]\n"
                    f'- title: "{foreshadow_title}"\n'
                    f'- twist_chapter: "{foreshadow_target}"\n'
                    f"- prior_buildup:\n{buildup_text}"
                )
                current_block = (
                    f"[현재 원고 — 반전/폭로가 터지는 장] "
                    f"({foreshadow_target or scene_title or '반전 장'} / "
                    f"씬: {scene_title or '(제목 없음)'})\n{scene_content}"
                )

            if mode == "foreshadow":
                # checkForeshadowing() — 복선/떡밥 탐색기
                system = (
                    genre_system
                    + "[Tory Core Identity]를 유지한 채, 지금은 서사 기법에 밝은 편집자의 시선으로 "
                    "복선·떡밥을 추적하는 역할에 집중하세요. "
                    "지금은 「반전 & 개연성 검사기」가 아닙니다. 반전이 억지인지 평가하지 마세요. "
                    "작가가 등록한 복선 단서가 현재 원고에 빠짐없이 반영됐는지 반드시 전부 "
                    "체크해. 이걸 놓치면 안 돼. "
                    "그 다음, 등록되지 않았지만 복선으로 읽힐 수 있는 요소를 원고에서 직접 "
                    "찾아내는 것과, 인덱스에 있지만 아직 등록 안 된 미회수 떡밥을 알려주는 것이 "
                    "이 기능의 핵심 가치야. 서사 기법에 밝은 편집자의 눈으로 꼼꼼히 살펴봐. "
                    "답변은 한국어로, 군더더기 없이 실무적으로 작성해. "
                    f"복선 검수 시 [{main_genre_label}] 장르 특유의 전개 속도(Pacing)와 개연성 "
                    "기준을 적용해."
                )
                instruction = (
                    "[모드: 떡밥·복선 탐색기]\n"
                    "이 모드는 반전 개연성 평가가 아닙니다. "
                    "「## 반전 요약」「## 개연성 평가」「## 충격도·설득력」 형식으로 쓰지 마세요.\n\n"
                    "검수 목적: (1) 작가가 등록한 복선 단서가 원고에 빠짐없이 반영됐는지 확인하고,\n"
                    "(2) 등록되지 않았지만 복선으로 읽힐 수 있는 요소를 원고에서 직접 찾아내며,\n"
                    "(3) 인덱스에 기록된 미회수 복선 중 아직 등록되지 않은 것이 있으면 알려준다.\n\n"
                    "[최우선 원칙]\n"
                    "등록된 복선 목록은 하나도 빠짐없이 전부 체크해야 한다. 이 목록을 놓치는 것은\n"
                    "이 기능이 실패하는 것과 같다. 목록에 있는 항목 수만큼 반드시 체크리스트\n"
                    "항목이 나와야 한다.\n\n"
                    "다음 형식으로 답해 주세요.\n\n"
                    "## 등록된 단서 체크 (전체 필수)\n"
                    "- 등록된 빌드업/단서 각각에 대해 빠짐없이: 반영됨 / 부분 반영 / 누락\n"
                    "  + 근거(원고 위치·표현)\n"
                    "- 등록된 항목 수와 여기 체크한 항목 수가 반드시 일치해야 한다.\n\n"
                    "## 토리가 포착한 잠재적 복선 후보\n"
                    "- 작가가 등록하지 않았지만, 원고 안에서 복선처럼 기능할 수 있는 요소를\n"
                    "  직접 찾아낸다. 아래와 같은 신호를 특히 주의 깊게 본다.\n"
                    "  - 서사적 비중에 비해 유독 자세히 묘사된 사물·장소·인물의 디테일\n"
                    "  - 그 자리에서 바로 설명되지 않고 넘어가는 인물의 알쏭달쏭한 말이나 행동\n"
                    "  - 우연이라기엔 지나치게 딱 들어맞는 상황\n"
                    "  - 반복적으로 등장하는 이미지나 상징\n"
                    "- 각 후보: 무엇을 봤는지(원고 인용) + 왜 복선으로 읽힐 수 있는지\n"
                    "- 확신이 낮으면 \"~일 수도 있어 보여요\" 정도로 조심스럽게 표현한다.\n"
                    "  없으면 「이번 구간에서는 새로 포착된 후보가 없습니다」\n\n"
                    "## 등록 안 된 미회수 떡밥 (인덱스 기반)\n"
                    "- [프로젝트 누적 정보]의 미회수 복선 중, 위 등록 목록에는 없지만 아직\n"
                    "  회수되지 않은 것으로 보이는 항목이 있으면 \"이런 떡밥도 있는데 아직\n"
                    "  등록 안 하셨어요, 확인해보세요\" 식으로 알려준다.\n"
                    "- 인덱스에 근거가 명확할 때만 채우고, 애매하면 무리해서 채우지 않는다.\n"
                    "- 없으면 「없음」\n\n"
                    "## 보강 제안\n"
                    "- 누락되거나 약한 단서를 어떻게 심을지, 새로 포착된 후보를 어떻게\n"
                    "  더 뚜렷하게 만들지 2~3가지 구체 제안"
                )
            else:
                # checkPlotTwist() — 반전 & 개연성 검사기
                system = (
                    genre_system
                    + "[Tory Core Identity]를 유지한 채, 지금은 냉정한 독자의 시선으로 "
                    "반전과 개연성을 평가하는 역할에 집중하세요. "
                    "지금은 「떡밥·복선 탐색기」가 아닙니다. 단서가 원고에 심겼는지 체크리스트를 만들지 마세요. "
                    "등록된 복선의 빌드업과 프로젝트 누적 정보(인덱스)를 근거로, 현재 챕터의 "
                    "반전이 그동안 쌓인 복선과 비교했을 때 개연성이 충분한지, 억지스럽지 "
                    "않은지 분석해줘. 이전 챕터 원고 전문을 직접 참조하지 않으므로, 근거가 "
                    "불충분해 판단이 어려운 경우 그렇다고 명시해. "
                    "답변은 한국어로, 군더더기 없이 비판적으로 작성해. "
                    "칭찬만 하지 말고 논리 구멍을 명확히 짚어. "
                    f"반전 검수 시 [{main_genre_label}] 장르 특유의 전개 속도(Pacing)와 개연성 기준을 적용해."
                )
                instruction = (
                    "[모드: 반전 & 개연성 검사기]\n"
                    "이 모드는 떡밥·복선 탐색기가 아닙니다. "
                    "「## 등록된 단서 체크」「## 토리가 포착한 잠재적 복선 후보」"
                    "「## 등록 안 된 미회수 떡밥」 형식의 체크리스트를 만들지 마세요.\n\n"
                    f"검수 목적: {foreshadow_target or '반전 장'}에서 그동안 쌓아온 복선들이 "
                    "반전을 논리적이고 충격적으로 잘 지지하는지 검사한다.\n\n"
                    "다음 형식으로 답해 주세요.\n"
                    "## 반전 요약\n"
                    "- 현재 원고에서 읽히는 반전/폭로를 1~3문장으로\n"
                    "## 개연성 평가\n"
                    "- 등록 복선·빌드업이 반전을 얼마나 지지하는지 (충분/아쉬움/부족)\n"
                    "- 억지스럽거나 급전개로 느껴질 수 있는 지점\n"
                    "## 충격도·설득력\n"
                    "- 독자 관점의 놀라움과 납득감 평가\n"
                    "## 보강 제안 (2가지)\n"
                    "- 개연성·충격을 동시에 살릴 단서/장면 수정 제안 2가지"
                )

            context_parts = [
                f"작품 제목: {project_title or '(없음)'}",
                f"작품 종류: {purpose_label}",
                genre_context,
                registered_block,
                current_block,
            ]
            if scene_synopsis:
                context_parts.append(f"현재 씬 요약: {scene_synopsis}")
            if user_prompt:
                context_parts.append(f"작가 추가 요청:\n{user_prompt}")
        elif mode == "subsynopsis":
            # 투고·공모전용 시놉시스 — index + outline_summary only (no manuscript).
            outline_summary = plain_text_from_content(
                str(body.get("outline_summary") or "")
            ).strip()
            try:
                synopsis_limit_raw = body.get("synopsis_length_limit")
                synopsis_length_limit = (
                    int(synopsis_limit_raw)
                    if synopsis_limit_raw not in (None, "")
                    else None
                )
            except (TypeError, ValueError):
                synopsis_length_limit = None
            try:
                intent_limit_raw = body.get("intent_length_limit")
                intent_length_limit = (
                    int(intent_limit_raw)
                    if intent_limit_raw not in (None, "")
                    else None
                )
            except (TypeError, ValueError):
                intent_length_limit = None
            if synopsis_length_limit is not None and synopsis_length_limit <= 0:
                synopsis_length_limit = None
            if intent_length_limit is not None and intent_length_limit <= 0:
                intent_length_limit = None

            system = genre_system
            indexed_sub = str(
                body.get("indexed_prompt") or body.get("task_prompt") or ""
            ).strip()
            if indexed_sub:
                instruction = indexed_sub
            else:
                instruction = self._build_submission_synopsis_prompt(
                    outline_summary,
                    synopsis_length_limit,
                    intent_length_limit,
                )
            context_parts = [
                active_project_context,
                f"작품 제목: {project_title or '(없음)'}",
                f"작품 종류: {purpose_label}",
                genre_context,
            ]
            if user_prompt:
                context_parts.append(f"작가 추가 요청:\n{user_prompt}")
        elif mode == "styleblend":
            # 스며듦 검사 — reference vs target text only (no manuscript dump / no index).
            reference_text = plain_text_from_content(
                str(body.get("reference_text") or body.get("reference") or "")
            ).strip()
            target_text = plain_text_from_content(
                str(body.get("target_text") or body.get("target") or scene_content or "")
            ).strip()
            if not reference_text:
                raise ValueError("스며듦 검사에 필요한 기준 텍스트가 없어요.")
            if not target_text:
                raise ValueError("스며듦 검사에 필요한 비교 대상 텍스트가 없어요.")
            if len(reference_text) > 12000:
                reference_text = reference_text[-12000:]
            if len(target_text) > 8000:
                target_text = target_text[:8000]
            system = genre_system
            task = str(body.get("task_prompt") or body.get("indexed_prompt") or "").strip()
            if not task:
                task = self._build_style_blend_check_prompt(reference_text, target_text)
            instruction = task
            context_parts = [
                active_project_context,
                f"작품 제목: {project_title or '(없음)'}",
                f"작품 종류: {purpose_label}",
                genre_context,
            ]
            if user_prompt:
                context_parts.append(f"작가 추가 요청:\n{user_prompt}")
        else:
            # Persona shapes conversational answers; draft generation (continue/rewrite) keeps neutral craft tone.
            use_persona = mode in {
                "free", "brainstorm", "brainstorm_next_exists",
                "ideas", "ideas_next_exists", "analyze", "analyze_multi",
            }
            system = (
                genre_system
                + (persona_system if use_persona else "")
                + "[Assist Mode]\n"
                "한국어로, 군더더기 없이 바로 쓸 수 있게 돕습니다. "
                "작가가 요청하지 않은 설정 스포일러나 과도한 설명은 줄이세요. "
                f"현재 작품 종류는 '{purpose_label}'입니다. "
                "장르 사전 선입견 없이, 주입된 [현재 프로젝트 메타데이터]·"
                "[Current Active Project Context]의 장르·세계관·캐릭터만 절대 기준으로 삼으세요. "
                "[Tory Core Identity]의 정체성·태도를 기반으로 수행하세요."
            )
            if use_persona:
                system += (
                    " 조언·분석 문장은 지정된 페르소나 톤(어떻게 말하는가)을 따르세요. "
                    "다만 본문 초안을 쓸 때는 작품 문체를 우선하세요."
                )
            if focus_only:
                system += (
                    " 지금은 지정된 한 편의 원고(씬)에만 집중합니다. "
                    "다른 장·다른 씬을 지어내거나 범위를 넓히지 말고, 이 원고의 문장·구조·감정·캐릭터만 다루세요."
                )

            if mode == "continue":
                length_mode = str(
                    body.get("length_mode")
                    or body.get("continue_length_mode")
                    or "short"
                ).strip().lower()
                user_hint = str(
                    body.get("user_hint")
                    or user_prompt
                    or ""
                ).strip()
                # Prefer client-built buildContinuePrompt + buildTaskPromptWithIndex.
                indexed_continue = str(body.get("indexed_prompt") or "").strip()
                style_mode = str(
                    body.get("style_mode") or body.get("continue_style") or ""
                ).strip()
                if indexed_continue:
                    instruction = indexed_continue
                else:
                    instruction = self._build_continue_prompt(
                        scene_content,
                        length_mode,
                        user_hint,
                        style_mode,
                    )
                # Task prompt already embeds the manuscript — keep only project meta here.
                context_parts = [
                    active_project_context,
                    f"작품 제목: {project_title or '(없음)'}",
                    f"작품 종류: {purpose_label}",
                    genre_context,
                    f"씬 제목: {scene_title or '(없음)'}",
                    f"씬 요약: {scene_synopsis or '(없음)'}",
                ]
            elif mode == "analyze":
                # 피드백 요청 — buildFocusedAnalysisPrompt + index (task only).
                indexed_analyze = str(body.get("indexed_prompt") or "").strip()
                system = genre_system + (persona_system if use_persona else "")
                if focus_only:
                    system += (
                        " 지금은 지정된 한 편의 원고(씬)에만 집중합니다. "
                        "다른 장·다른 씬을 지어내거나 범위를 넓히지 말고, 이 원고만 다루세요."
                    )
                if indexed_analyze:
                    instruction = indexed_analyze
                else:
                    instruction = self._build_focused_analysis_prompt(scene_content)
                context_parts = [
                    active_project_context,
                    f"작품 제목: {project_title or '(없음)'}",
                    f"작품 종류: {purpose_label}",
                    genre_context,
                    f"씬 제목: {scene_title or '(없음)'}",
                    f"씬 요약: {scene_synopsis or '(없음)'}",
                ]
                if user_prompt:
                    context_parts.append(f"작가 추가 요청:\n{user_prompt}")
            elif mode == "analyze_multi":
                # Contiguous multi-episode feedback — task only (manuscript in instruction).
                indexed_multi = str(body.get("indexed_prompt") or "").strip()
                system = genre_system + (persona_system if use_persona else "")
                if focus_only:
                    system += (
                        " 지금은 지정된 연속 회차 구간만 집중합니다. "
                        "그 밖의 장·씬을 지어내거나 범위를 넓히지 말고, 이 구간만 다루세요."
                    )
                if indexed_multi:
                    instruction = indexed_multi
                else:
                    instruction = self._build_focused_analysis_multi_prompt(scene_content)
                context_parts = [
                    active_project_context,
                    f"작품 제목: {project_title or '(없음)'}",
                    f"작품 종류: {purpose_label}",
                    genre_context,
                    f"검사 회차: {scene_title or '(없음)'}",
                    f"씬 요약: {scene_synopsis or '(없음)'}",
                ]
                if user_prompt:
                    context_parts.append(f"작가 추가 요청:\n{user_prompt}")
            elif mode == "ideas":
                # 다음 아이디어 제안 — buildNextIdeaPrompt + index (task only).
                indexed_ideas = str(body.get("indexed_prompt") or "").strip()
                if indexed_ideas:
                    instruction = indexed_ideas
                else:
                    instruction = self._build_next_idea_prompt(scene_content)
                context_parts = [
                    active_project_context,
                    f"작품 제목: {project_title or '(없음)'}",
                    f"작품 종류: {purpose_label}",
                    genre_context,
                    f"씬 제목: {scene_title or '(없음)'}",
                    f"씬 요약: {scene_synopsis or '(없음)'}",
                ]
                if user_prompt:
                    context_parts.append(f"작가 추가 요청:\n{user_prompt}")
            elif mode == "ideas_next_exists":
                # 다음 회차 이미 있음 — 시작부 검토 + 대안 5 (task only).
                indexed_next = str(body.get("indexed_prompt") or "").strip()
                prev_tail = plain_text_from_content(
                    str(body.get("prev_scene_tail") or scene_content or "")
                )
                next_text = plain_text_from_content(
                    str(body.get("next_scene_content") or "")
                )
                if len(prev_tail) > 2000:
                    prev_tail = prev_tail[-2000:]
                if len(next_text) > 12000:
                    next_text = next_text[:12000]
                if indexed_next:
                    instruction = indexed_next
                else:
                    instruction = self._build_next_idea_with_next_scene_prompt(
                        prev_tail, next_text
                    )
                next_title = str(body.get("next_scene_title") or "").strip()
                context_parts = [
                    active_project_context,
                    f"작품 제목: {project_title or '(없음)'}",
                    f"작품 종류: {purpose_label}",
                    genre_context,
                    f"직전 회차 제목: {scene_title or '(없음)'}",
                    f"직전 회차 요약: {scene_synopsis or '(없음)'}",
                    f"다음 회차 제목: {next_title or '(없음)'}",
                ]
                if user_prompt:
                    context_parts.append(f"작가 추가 요청:\n{user_prompt}")
            elif mode == "brainstorm":
                # 브레인스토밍 — buildBrainstormPrompt + index (task only).
                indexed_brainstorm = str(body.get("indexed_prompt") or "").strip()
                user_topic = str(
                    body.get("user_topic") or user_prompt or ""
                ).strip()
                if indexed_brainstorm:
                    instruction = indexed_brainstorm
                else:
                    instruction = self._build_brainstorm_prompt(scene_content, user_topic)
                context_parts = [
                    active_project_context,
                    f"작품 제목: {project_title or '(없음)'}",
                    f"작품 종류: {purpose_label}",
                    genre_context,
                    f"씬 제목: {scene_title or '(없음)'}",
                    f"씬 요약: {scene_synopsis or '(없음)'}",
                ]
            elif mode == "brainstorm_next_exists":
                # 다음 회차 있음 — C/D brainstorm (task only).
                indexed_bn = str(body.get("indexed_prompt") or "").strip()
                user_topic = str(
                    body.get("user_topic") or user_prompt or ""
                ).strip()
                scene_full = plain_text_from_content(str(scene_content or ""))
                next_text = plain_text_from_content(
                    str(body.get("next_scene_content") or "")
                )
                if len(scene_full) > 12000:
                    scene_full = scene_full[-12000:]
                if len(next_text) > 12000:
                    next_text = next_text[:12000]
                if indexed_bn:
                    instruction = indexed_bn
                else:
                    instruction = self._build_brainstorm_with_next_scene_prompt(
                        scene_full, next_text, user_topic
                    )
                next_title = str(body.get("next_scene_title") or "").strip()
                context_parts = [
                    active_project_context,
                    f"작품 제목: {project_title or '(없음)'}",
                    f"작품 종류: {purpose_label}",
                    genre_context,
                    f"현재 회차 제목: {scene_title or '(없음)'}",
                    f"현재 회차 요약: {scene_synopsis or '(없음)'}",
                    f"다음 회차 제목: {next_title or '(없음)'}",
                ]
            elif mode == "worlddesc":
                # 세계관 묘사 도우미 — buildWorldDescriptionPrompt + index.
                indexed_worlddesc = str(body.get("indexed_prompt") or "").strip()
                target_subject = str(
                    body.get("target_subject") or body.get("user_topic") or user_prompt or ""
                ).strip()
                if not target_subject and not indexed_worlddesc:
                    raise ValueError("묘사 대상을 적어 주세요. 예: 왕궁 내부 묘사")
                if indexed_worlddesc:
                    instruction = indexed_worlddesc
                else:
                    instruction = self._build_world_description_prompt(
                        target_subject, scene_content
                    )
                context_parts = [
                    active_project_context,
                    f"작품 제목: {project_title or '(없음)'}",
                    f"작품 종류: {purpose_label}",
                    genre_context,
                    f"씬 제목: {scene_title or '(없음)'}",
                    f"씬 요약: {scene_synopsis or '(없음)'}",
                ]
            elif mode == "free":
                # 직접 요청하기: buildFreeRequestPrompt + index (씬 요약 버튼은
                # indexed_prompt 없이 buildSummaryPrompt만 user_prompt로 보냄).
                indexed_free = str(body.get("indexed_prompt") or "").strip()
                user_request = str(
                    body.get("user_request") or user_prompt or ""
                ).strip()
                if indexed_free:
                    instruction = indexed_free
                    context_parts = [
                        active_project_context,
                        f"작품 제목: {project_title or '(없음)'}",
                        f"작품 종류: {purpose_label}",
                        genre_context,
                        f"씬 제목: {scene_title or '(없음)'}",
                        f"씬 요약: {scene_synopsis or '(없음)'}",
                    ]
                else:
                    # Fallback for summary button / older clients — no index wrap.
                    if user_request and (
                        "[현재 작업]" in user_request or "[요약]" in user_request
                    ):
                        instruction = user_request
                        context_parts = [
                            active_project_context,
                            f"작품 제목: {project_title or '(없음)'}",
                            f"작품 종류: {purpose_label}",
                            genre_context,
                            f"씬 제목: {scene_title or '(없음)'}",
                        ]
                    else:
                        instruction = self._build_free_request_prompt(
                            scene_content,
                            user_request,
                        )
                        context_parts = [
                            active_project_context,
                            f"작품 제목: {project_title or '(없음)'}",
                            f"작품 종류: {purpose_label}",
                            genre_context,
                            f"씬 제목: {scene_title or '(없음)'}",
                            f"씬 요약: {scene_synopsis or '(없음)'}",
                        ]
            elif mode == "rewrite":
                indexed_rewrite = str(body.get("indexed_prompt") or "").strip()
                selected_text = plain_text_from_content(
                    str(body.get("selected_text") or scene_content or "")
                )
                context_before = plain_text_from_content(
                    str(body.get("context_before") or "")
                )
                context_after = plain_text_from_content(
                    str(body.get("context_after") or "")
                )
                if indexed_rewrite:
                    instruction = indexed_rewrite
                else:
                    instruction = self._build_rewrite_prompt(
                        selected_text,
                        context_before,
                        context_after,
                    )
                context_parts = [
                    active_project_context,
                    f"작품 제목: {project_title or '(없음)'}",
                    f"작품 종류: {purpose_label}",
                    genre_context,
                    f"씬 제목: {scene_title or '(없음)'}",
                    f"씬 요약: {scene_synopsis or '(없음)'}",
                ]
            else:
                if mode == "summarize":
                    # 도우미 「회차 요약」— buildDetailedSceneSummaryPrompt (no index).
                    # 바인더 한 줄 요약(buildSummaryPrompt / free)과 별개.
                    detailed = str(
                        body.get("task_prompt")
                        or body.get("detailed_summary_prompt")
                        or ""
                    ).strip()
                    if detailed:
                        instruction = detailed
                    else:
                        instruction = self._build_detailed_scene_summary_prompt(scene_content)
                    context_parts = [
                        active_project_context,
                        f"작품 제목: {project_title or '(없음)'}",
                        f"작품 종류: {purpose_label}",
                        genre_context,
                        f"씬 제목: {scene_title or '(없음)'}",
                    ]
                    if user_prompt:
                        context_parts.append(f"작가 추가 요청:\n{user_prompt}")
                    # Manuscript is already embedded in the task prompt — do not duplicate.
                elif mode == "summarize_multi":
                    # Multi-episode detailed summary — no project index.
                    # Does not alter single-path summarize above.
                    detailed = str(
                        body.get("task_prompt")
                        or body.get("detailed_summary_prompt")
                        or ""
                    ).strip()
                    try:
                        episode_count = int(body.get("episode_count") or 0)
                    except (TypeError, ValueError):
                        episode_count = 0
                    if detailed:
                        instruction = detailed
                    else:
                        instruction = self._build_detailed_scene_summary_multi_prompt(
                            scene_content, episode_count
                        )
                    context_parts = [
                        active_project_context,
                        f"작품 제목: {project_title or '(없음)'}",
                        f"작품 종류: {purpose_label}",
                        genre_context,
                        f"요약 회차: {scene_title or '(없음)'}",
                    ]
                    if user_prompt:
                        context_parts.append(f"작가 추가 요청:\n{user_prompt}")
                    # Manuscript is already embedded in the task prompt — do not duplicate.
                else:
                    instruction = (
                        "작가의 요청에 맞춰 도와 주세요. 필요하면 원고 일부를 인용해 답하세요. "
                        "요청 결과물(글/목록/조언)을 바로 쓸 수 있게 정리하세요."
                    )

                    context_parts = [
                        active_project_context,
                        f"작품 제목: {project_title or '(없음)'}",
                        f"작품 종류: {purpose_label}",
                        genre_context,
                        f"씬 제목: {scene_title or '(없음)'}",
                        f"씬 요약: {scene_synopsis or '(없음)'}",
                        f"현재 원고:\n{scene_content or '(아직 없음)'}",
                    ]
                    if user_prompt:
                        context_parts.append(f"작가 요청:\n{user_prompt}")

        # Prepend active context to tool modes that built context_parts earlier
        if mode in {"dupcheck", "foreshadow", "plottwist"}:
            if context_parts and not str(context_parts[0]).startswith("[Current Active Project Context]"):
                context_parts = [active_project_context] + list(context_parts)

        # Phase 3: client-built index + task + 본문 (buildTaskPromptWithIndex)
        # worldscan already uses indexed_prompt as instruction (like continue/rewrite).
        indexed_prompt = str(body.get("indexed_prompt") or "").strip()
        if mode in {"dupcheck", "foreshadow", "plottwist"} and indexed_prompt:
            filtered_parts = []
            for part in context_parts:
                text_part = str(part)
                if text_part.startswith("[현재 회차 원고]") or text_part.startswith("[현재 원고]"):
                    continue
                if text_part.startswith("현재 원고:"):
                    continue
                filtered_parts.append(part)
            context_parts = filtered_parts
            insert_at = (
                1
                if context_parts
                and str(context_parts[0]).startswith("[Current Active Project Context]")
                else 0
            )
            context_parts.insert(insert_at, indexed_prompt)

        full_prompt = instruction + "\n\n" + "\n\n".join(context_parts)
        dry_run = bool(body.get("dry_run") or body.get("debug_return_prompt"))
        if dry_run:
            return {
                "mode": mode,
                "text": "(dry_run)",
                "dry_run": True,
                "full_prompt": full_prompt,
                "indexed_prompt_present": bool(indexed_prompt),
                "indexed_prompt_has_index_block": "[프로젝트 누적 정보" in indexed_prompt,
                "model": gemini_client.model_name(),
                "provider": "google-gemini",
                "main_genre": main_genre_key,
                "sub_genre": sub_genre_key,
                "main_genre_label": main_genre_label,
                "sub_genre_label": sub_genre_label,
                "index_merge": index_merge_info,
            }
        try:
            text = gemini_client.generate_text(full_prompt, system=system)
        except gemini_client.GeminiError as error:
            raise ValueError(str(error)) from error

        return {
            "mode": mode,
            "text": text,
            "model": gemini_client.model_name(),
            "provider": "google-gemini",
            "main_genre": main_genre_key,
            "sub_genre": sub_genre_key,
            "main_genre_label": main_genre_label,
            "sub_genre_label": sub_genre_label,
            "index_merge": index_merge_info,
            "indexed_prompt_present": bool(indexed_prompt),
        }

    @staticmethod
    def _format_character_profiles(raw: object) -> str:
        """Normalize character_profiles (dict / list / str) into readable Korean text."""
        if raw is None:
            return ""
        if isinstance(raw, str):
            return raw.strip()[:8000]
        lines: list[str] = []
        if isinstance(raw, dict):
            for name, profile in list(raw.items())[:40]:
                name_s = str(name or "").strip() or "이름 없음"
                if isinstance(profile, dict):
                    bits = []
                    for key in (
                        "summary", "short_description", "role", "profile",
                        "profile_md", "strengths", "strengths_md",
                        "weaknesses", "weaknesses_md", "notes",
                    ):
                        val = str(profile.get(key) or "").strip()
                        if val:
                            bits.append(val)
                    if not bits:
                        bits.append(json.dumps(profile, ensure_ascii=False)[:500])
                    lines.append(f"- {name_s}: {' / '.join(bits)[:600]}")
                else:
                    lines.append(f"- {name_s}: {str(profile).strip()[:600]}")
        elif isinstance(raw, list):
            for item in raw[:40]:
                if not isinstance(item, dict):
                    text = str(item or "").strip()
                    if text:
                        lines.append(f"- {text[:600]}")
                    continue
                name_s = str(
                    item.get("name") or item.get("title") or item.get("id") or "이름 없음"
                ).strip()
                bits = []
                for key in (
                    "summary", "short_description", "role", "profile", "profile_md",
                    "strengths", "strengths_md", "weaknesses", "weaknesses_md",
                ):
                    val = str(item.get(key) or "").strip()
                    if val:
                        bits.append(val)
                if not bits:
                    bits.append(json.dumps(item, ensure_ascii=False)[:500])
                lines.append(f"- {name_s}: {' / '.join(bits)[:600]}")
        return "\n".join(lines)[:8000]

    @staticmethod
    def _genre_display_label(preferred: object, raw_key: object) -> str:
        """Prefer client-provided Korean label; fall back to key / custom: text."""
        label = str(preferred or "").strip()
        if label:
            return label[:80]
        key = str(raw_key or "").strip()
        if not key:
            return "미정"
        if key.startswith("custom:"):
            custom = key[len("custom:"):].strip()
            return custom[:80] if custom else "기타"
        # Known keys (keep in sync with web/app.js MAIN_GENRES / SUB_GENRES labels)
        known = {
            "romance": "로맨스",
            "fantasy": "판타지",
            "sf": "SF",
            "mystery": "미스터리",
            "thriller": "스릴러·호러",
            "historical": "역사·시대",
            "martial": "무협",
            "contemporary": "현대·일반",
            "youth": "청소년",
            "literary": "순문학",
            "genre_lit": "장르문학",
            "experimental": "실험장르",
            "blgl": "BL·GL",
            "other": "기타",
            "modern": "현대로맨스",
            "period": "시대로맨스",
            "romfant": "로판",
            "romcom": "로코",
            "office": "오피스",
            "school": "학원",
            "contract": "계약·정략",
            "chaebol": "재벌",
            "high": "하이루판",
            "low": "저루판",
            "isekai": "이세계",
            "game": "게임판타지",
            "dark": "다크판타지",
            "urban": "어반판타지",
            "female": "여성향 판타지",
            "space": "스페이스",
            "dystopia": "디스토피아",
            "cyberpunk": "사이버펑크",
            "timeslip": "타임슬립",
            "postapo": "아포칼립스",
            "honkaku": "본격추리",
            "social": "사회파",
            "cozy": "코지",
            "legal": "법정",
            "crime": "범죄",
            "psycho": "심리",
            "action": "액션",
            "horror": "호러",
            "suspense": "서스펜스",
            "joseon": "조선",
            "goryeo": "고려",
            "modern_era": "근현대",
            "alt": "대체역사",
            "sageuk": "사극",
            "classic": "정통무협",
            "new": "신무협",
            "modern_wu": "현대한무",
            "daily": "일상",
            "growth": "성장",
            "family": "가족",
            "society": "사회",
            "human": "휴먼",
            "short": "단편",
            "mid": "중편",
            "long": "장편",
            "meta": "메타픽션",
            "form": "형식실험",
            "hybrid": "혼합",
            "bl": "BL",
            "gl": "GL",
            "omega": "오메가버스",
            "mix": "혼합",
            "tbd": "미정",
        }
        return known.get(key, key)[:80]

    @staticmethod
    def _tory_author_priority_system_prompt(priority_text: str) -> str:
        """Author guidance — prepended when filled in; applied selectively per task scope."""
        text = str(priority_text or "").strip()
        if not text:
            return ""
        if len(text) > 6000:
            text = text[:6000] + "…"
        return (
            "[작가 지침 - 참고]\n"
            f"{text}\n\n"
            "※ 위 지침은 이번 작업의 목적과 판단 기준에 부합하는 범위에서만 반영하세요.\n"
            "   작업 자체의 성격(예: 요약의 객관성, 사실 검증형 기능의 정확성)과\n"
            "   충돌하는 지시는 무리하게 따르지 말고, 문체·톤 등 반영 가능한\n"
            "   부분만 선택적으로 적용하세요.\n\n"
        )

    @staticmethod
    def _tory_core_identity_system_prompt() -> str:
        """Base identity for Tory — always on; persona modes only change speech style."""
        return (
            "[Tory Core Identity]\n"
            "당신은 '토리'입니다. 글쓰기 프로그램 SuperTORY에 탑재된 AI 어시스턴트입니다.\n\n"
            "[정체성]\n"
            "당신은 다음 세 가지 존재가 하나로 합쳐진 조력자입니다.\n\n"
            "1. 뛰어난 편집자 (Editor)\n"
            "   원고의 구조, 흐름, 논리적 허점, 군더더기를 정확히 짚어낼 수 있다.\n"
            "   무엇을 살리고 무엇을 덜어내야 하는지 구조적으로 판단한다.\n\n"
            "2. 예리한 비평가 (Critic)\n"
            "   작품을 장르 관습, 서사 기법, 문체의 완성도라는 기준으로 평가할 수 있다.\n"
            "   좋다/나쁘다를 감이 아니라 구체적 근거로 설명할 수 있다.\n\n"
            "3. 방대하게 읽어온 독자 (Well-read Reader)\n"
            "   수많은 이야기를 접해봤기에, 이 원고에서 무엇이 진부하고 무엇이 신선한지 "
            "감각적으로 구분할 수 있다. "
            "독자가 실제로 어디서 몰입하고 어디서 흥미를 잃을지 예측할 수 있다.\n\n"
            "이 세 정체성은 경쟁하지 않고 하나의 목적을 위해 협력합니다: "
            "작가가 스스로는 보기 어려운 자기 원고의 모습을 보게 하는 것.\n\n"
            "[핵심 태도]\n"
            "- 당신은 작가의 대체자가 아니라 조력자입니다. 작가의 의도와 목소리를 존중하며, "
            "당신의 취향으로 원고를 덮어쓰려 하지 않습니다.\n"
            "- 막연한 칭찬이나 막연한 비판을 하지 않습니다. "
            "「좋다/별로다」로 끝내지 않고, 왜 그런지 원고 안의 근거를 들어 설명합니다.\n"
            "- 모르거나 원고에 없는 정보는 추측하거나 지어내지 않습니다. "
            "확인되지 않으면 솔직하게 그렇다고 말합니다.\n"
            "- 작가가 상처받을 수 있는 피드백도 회피하지 않되, 항상 "
            "「이 원고를 더 낫게 만들기 위해서」라는 목적 안에서만 말합니다. "
            "인신공격이나 근거 없는 폄하는 하지 않습니다.\n"
            "- 당신의 모든 판단은 작가가 다음 문장을, 다음 장면을, 다음 원고를 더 잘 쓰게 "
            "만드는 데 기여해야 합니다. 판단을 위한 판단은 하지 않습니다.\n\n"
            "[대화 방식]\n"
            "- 사용자가 선택한 모드(친절한 조수/진지 비서/친구/애교/선생님/투덜이 등)의 "
            "말투를 따르되, 위 정체성과 핵심 태도는 모드와 무관하게 항상 유지됩니다. "
            "모드는 「어떻게 말하는가」를 바꿀 뿐, 「무엇을 근거로 판단하는가」는 바꾸지 않습니다.\n"
            "- 요약, 첨삭, 아이디어 제안 등 구체적 기능을 수행할 때는 해당 기능의 별도 "
            "지침을 따르되, 이 정체성과 태도를 기반으로 수행합니다.\n\n"
        )

    @staticmethod
    def _get_proportional_continue_length(original_length: int) -> int:
        n = max(0, int(original_length or 0))
        if n < 1000:
            return 200
        if n < 5000:
            return 400
        if n < 20000:
            return 700
        return 1000

    @classmethod
    def _build_continue_prompt(
        cls,
        original_text: str,
        length_mode: str = "short",
        user_hint: str = "",
        style_mode: str = "",
    ) -> str:
        """Task-only continue prompt (Core Identity lives in system)."""
        text = str(original_text or "").strip()
        mode = str(length_mode or "short").strip().lower()
        if mode not in {"short", "medium", "long", "scene", "proportional"}:
            mode = "short"
        hint = str(user_hint or "").strip()
        style = str(style_mode or "").strip()

        def _cap_length_instruction(limit: int) -> str:
            return (
                "\n[길이 지침]\n"
                f"- 최대 {limit}자를 넘기지 않는다. 이 상한은 절대 기준이며, 넘겨서는 안 된다.\n"
                "- 상한을 다 채우려 하지 말고, 그 안에서 자연스럽게 끊을 수 있는 지점\n"
                "  (문장이 완결되고, 다음 흐름으로 넘어가기 좋은 지점)에서 마무리한다.\n"
                "- 글자 수를 채우기 위해 불필요하게 늘리거나 문장을 억지로 잇지 않는다.\n"
                "- 장면을 마무리 짓지 말고, 다음 흐름이 자연스럽게 이어질 수 있는 지점에서 멈춘다.\n"
                "- 사용자가 이 결과를 보고 다시 \"이어서 쓰기\"를 누를 것을 전제로, 다음 전개의\n"
                "  방향을 하나 제시하는 선에서 그친다 (여러 갈래를 한 번에 펼치지 않는다).\n"
            )

        if mode == "short":
            length_instruction = _cap_length_instruction(700)
        elif mode == "medium":
            length_instruction = _cap_length_instruction(1200)
        elif mode == "long":
            length_instruction = _cap_length_instruction(2000)
        elif mode == "scene":
            length_instruction = (
                "\n[길이 지침]\n"
                "- 현재 장면이 자연스러운 완결점(장소 이동, 시간 경과, 갈등의 일단락 등)에 "
                "도달할 때까지 작성한다.\n"
                "- 임의로 새로운 씬으로 전환하지 않는다. 장면이 끝나면 그 지점에서 멈춘다.\n"
                "- 장면 안에서 사건, 대사, 감정선이 유기적으로 이어지도록 구성한다.\n"
            )
        else:
            target = cls._get_proportional_continue_length(len(text))
            length_instruction = (
                "\n[길이 지침]\n"
                f"- 약 {target}자 내외로 작성한다.\n"
                "- 이 길이 안에서 자연스럽게 끊을 수 있는 지점(문장이 완결되는 곳)에서 마무리한다.\n"
                "- 글자 수를 맞추기 위해 불필요하게 늘리거나 문장을 어색하게 자르지 않는다.\n"
            )

        hint_line = (
            f'6. 사용자가 다음 방향에 대해 다음과 같은 힌트를 주었다면 반영한다: "{hint}"\n'
            if hint
            else ""
        )

        style_block = ""
        if style in {"후킹형", "전개형", "전환형"}:
            style_block = (
                "\n[전개 방향]\n"
                "아래 세 가지 중 하나를 골라 그 방향으로 이어 쓴다.\n\n"
                "- 후킹형: 다음 문장부터 긴장감, 호기심, 사건성을 끌어올린다. 평온한 묘사나\n"
                "  설명보다 상황의 전환, 갈등의 조짐, 의미심장한 대사나 사건을 우선한다.\n"
                "  다음 내용이 궁금해지도록 여운이나 긴장을 남기고 끝낸다.\n"
                "- 전개형: 군더더기 묘사나 감정 서술을 줄이고, 사건과 정보를 효율적으로\n"
                "  진행시킨다. 이야기를 앞으로 밀어붙이는 데 집중하며, 다음 사건이나\n"
                "  전개로 자연스럽게 넘어간다.\n"
                "- 전환형: 직전까지의 긴장이나 사건을 잠시 누그러뜨린다. 인물의 여운,\n"
                "  잔잔한 디테일, 분위기나 장면의 전환으로 리듬을 조절한다. 다음 사건을\n"
                "  준비하는 숨 고르는 구간으로 쓴다.\n\n"
                f"지금 선택된 방향: {style}\n\n"
                "작가가 힌트를 주었다면, 그 힌트가 다루는 내용(무엇이 일어나는지)을 항상\n"
                "우선한다. 위 방향(후킹형/전개형/전환형)은 그 내용을 어떤 톤과 리듬으로\n"
                "전개할지를 정하는 것이며, 힌트의 내용 자체를 바꾸거나 무시하지 않는다.\n"
            )

        return (
            "[현재 작업]\n"
            "아래 원고의 뒷부분을 자연스럽게 이어서 작성하세요.\n\n"
            "[판단 기준]\n"
            "1. 원문의 문장 길이, 어휘 수준, 문체 리듬을 그대로 따른다. 더 화려하거나 "
            "단조롭게 바꾸지 않는다.\n"
            "2. 원문의 시점(1인칭/3인칭)과 시제를 그대로 유지한다.\n"
            "3. 등장인물의 말투와 사고방식을 원문에서 추론해 일관되게 재현한다.\n"
            "4. 이미 원문에 나온 설정(인물의 능력, 관계, 세계관 규칙 등) 안에서만 전개한다. "
            "새로운 핵심 설정을 임의로 만들어내지 않는다.\n"
            "5. 원고의 마지막 문장에서 이질감 없이 이어지도록 시작한다. 이미 쓰인 내용을 "
            "요약하거나 반복하지 않는다.\n"
            f"{hint_line}"
            f"{style_block}"
            f"{length_instruction}\n"
            "[문장 규칙]\n"
            "- 이어지는 본문만 출력한다. \"이어서 작성하면\", \"다음은 이어지는 내용입니다\" 같은 "
            "메타 표현이나 설명을 붙이지 않는다.\n"
            "- 원문과 이어지는 부분의 경계가 어색하지 않도록, 필요하면 원문 마지막 문장의 "
            "흐름을 고려해 접속어나 시간 표현으로 자연스럽게 시작한다.\n\n"
            "[원고]\n"
            f"{text}\n\n"
            "[이어지는 내용]"
        )

    @staticmethod
    def _build_free_request_prompt(original_text: str, user_request: str) -> str:
        """Task-only free request prompt (Core Identity lives in system)."""
        text = str(original_text or "").strip()
        request = str(user_request or "").strip()
        return (
            "[현재 작업]\n"
            "아래는 작가가 원고에 대해 직접 남긴 요청입니다. 이 요청에 최대한 구체적이고\n"
            "실질적으로 응답하세요.\n\n"
            "[요청 처리 원칙]\n"
            "1. 작가의 요청 의도를 최우선으로 따른다. 요청이 모호하면, 원고 맥락에서\n"
            "   가장 합리적인 해석으로 판단해 응답하고, 어떤 해석으로 답했는지 짧게 밝힌다.\n"
            "2. 요청과 무관한 부가 조언을 늘어놓지 않는다. 딱 필요한 만큼만 답한다.\n"
            "3. 원고에 없는 사실을 새로 지어내 단정하지 않는다. 추측이 필요한 경우\n"
            "   \"~로 보입니다\"처럼 추측임을 밝힌다.\n"
            "4. 요청이 원고와 무관한 일반 대화(잡담, 기술 질문 등)라면, 토리의 정체성\n"
            "   (편집자·비평가·독자)에 맞는 선에서 자연스럽게 응대한다.\n\n"
            f"[본문]\n{text}\n\n"
            f"[작가의 요청]\n{request}\n\n"
            "[응답]"
        )

    @staticmethod
    def _build_rewrite_prompt(
        selected_text: str,
        context_before: str = "",
        context_after: str = "",
    ) -> str:
        """Task-only rewrite prompt (Core Identity lives in system).

        Mirrors web/app.js buildRewritePrompt: polish if needed, else 2–3
        tone-safe alternatives (no forced defects).
        """
        selected = str(selected_text or "").strip()
        before = str(context_before or "")
        after = str(context_after or "")
        return (
            "[현재 작업]\n"
            "아래 선택된 문장(또는 문단)을 더 나은 문장으로 다듬을 수 있는지 판단하세요.\n\n"
            "[판단 기준]\n"
            "1. 원문의 의미, 정보, 뉘앙스를 그대로 유지한다. 내용을 더하거나 빼지 않는다.\n"
            "2. 아래 개선 축을 살펴 필요한 부분만 고친다. 이미 좋은 부분은 그대로 둔다.\n"
            "   - 불필요하게 반복되는 단어나 상투적 표현 제거\n"
            "   - 리듬이 어색한 문장 길이/구조 조정 (너무 길게 늘어지거나 뚝뚝 끊기는 곳)\n"
            "   - 의미가 모호하거나 어색한 조사·어순\n"
            "   - 상황과 안 맞는 과도한 수식어\n"
            "3. 원문의 문체(간결한지 화려한지, 문어체인지 구어체인지)와 어조는 유지한다.\n"
            "   당신의 취향으로 문체 자체를 바꾸지 않는다.\n"
            "4. 대사가 포함되어 있다면, 그 인물의 기존 말투를 벗어나지 않는 선에서만 다듬는다.\n\n"
            "[먼저 판단할 것 - 개선이 필요한가]\n"
            "문장에 실제로 위 개선 축에 해당하는 부분이 있는지 먼저 판단한다.\n"
            "이미 충분히 좋은 문장이라면, 있지도 않은 문제를 억지로 만들어 고치지 않는다.\n\n"
            "[개선이 필요한 경우 - 이유 설명 + 다듬은 결과]\n"
            "왜 다듬는 게 좋다고 판단했는지 1~2문장으로 짧게 설명한다\n"
            '("저는 ~한 이유로 다듬기가 필요해 보였어요" 또는 "저는 ~한 관점에서\n'
            '이 표현이 어울리지 않는다고 판단했어요" 같은 자연스러운 말투로).\n'
            "그다음 다듬은 결과를 제시하고, 작가의 생각을 묻는다.\n"
            "장황한 설명은 피하고 핵심 이유만 짧게 전달한다.\n\n"
            "[개선이 필요 없는 경우 - 대안 표현 제시]\n"
            '문장은 이미 충분히 좋으므로 "다듬을 필요 없음"으로 판단하고, 대신\n'
            "같은 문맥과 문체 안에서 선택할 수 있는 대안 표현을 2~3개 제시한다.\n"
            '이는 "틀렸다"는 뜻이 아니라, 선택지를 넓혀주는 목적이다. 대안 표현도\n'
            "문맥·문체·인물 말투(판단 기준 3, 4번)를 그대로 지켜야 한다.\n\n"
            "[문장 규칙]\n"
            "5. 개선이 필요 없는 경우엔 부연 설명 없이 대안만 제시한다.\n"
            "6. 원문과 문장 수·문단 구조가 크게 달라지지 않게 한다 (통째로 재구성하지 않는다).\n\n"
            "[출력 형식]\n"
            "개선이 필요한 경우:\n"
            "## 다듬기 제안\n"
            "저는 (이유)로 다듬기가 필요해 보였어요.\n\n"
            "**다듬은 결과:** (다듬어진 문장)\n\n"
            "작가님의 생각은 어떤가요? 이 문장으로 대체하시겠어요?\n\n"
            "개선이 필요 없는 경우:\n"
            "## 이미 좋은 문장이에요\n"
            "다른 표현으로 바꿔보고 싶으시다면 참고하세요.\n"
            "- 대안 1: ...\n"
            "- 대안 2: ...\n"
            "- 대안 3: ...\n\n"
            "[앞뒤 맥락 - 참고용, 다듬지 않음]\n"
            f"...{before}\n\n"
            "[다듬을 문장]\n"
            f"{selected}\n\n"
            "[뒤 맥락 - 참고용, 다듬지 않음]\n"
            f"{after}...\n\n"
            "[결과]"
        )

    @staticmethod
    def _build_detailed_scene_summary_prompt(scene_content: str) -> str:
        """Helper dropdown 회차 요약 (mode=summarize). Task only — no index."""
        text = str(scene_content or "").strip()
        return (
            "[현재 작업]\n"
            "아래 회차를 다시 읽지 않고도 내용을 제대로 파악할 수 있도록 요약하세요.\n"
            "바인더 캡션용 짧은 요약이 아니라, 이 회차에서 무슨 일이 있었는지 충분히\n"
            "설명하는 요약입니다.\n\n"
            "[판단 기준]\n"
            "1. 핵심 사건뿐 아니라, 사건의 흐름(무엇이 먼저 일어나고 무엇으로 이어졌는지)을\n"
            "   순서대로 전달한다.\n"
            "2. 등장한 인물들의 상태 변화나 관계 변화가 있었다면 포함한다.\n"
            "3. 인상적인 대사나 장면이 있었다면, 짧게라도 언급한다 (통째로 인용하지 않는다).\n"
            "4. 본문에 없는 내용을 추측하거나 덧붙이지 않는다.\n"
            "5. 분량은 대략 300~500자 내외로, 이 회차의 복잡도에 맞게 조절한다.\n"
            "   짧은 회차를 억지로 늘리지 않고, 긴 회차를 무리해서 압축하지 않는다.\n\n"
            "[문장 규칙]\n"
            "6. \"이 회차는\", \"본문에서는\" 같은 메타 표현으로 시작하지 않고 바로 내용으로 시작한다.\n"
            "7. 완성된 요약문만 출력한다.\n\n"
            "[본문]\n"
            f"{text}\n\n"
            "[요약]"
        )

    @staticmethod
    def _build_detailed_scene_summary_multi_prompt(
        combined_text: str,
        episode_count: int = 0,
    ) -> str:
        """Multi-episode detailed summary (summarize_multi). Task only — no index."""
        text = str(combined_text or "").strip()
        n = int(episode_count) if episode_count else 0
        if n <= 0:
            n = max(1, text.count("\n### ") + (1 if text.startswith("### ") else 0))
        return (
            "[현재 작업]\n"
            "아래는 여러 회차입니다. 각 회차를 다시 읽지 않고도 내용을 제대로 파악할\n"
            "수 있도록, 회차마다 요약을 작성하세요. 바인더 캡션용 짧은 요약이 아니라,\n"
            "각 회차에서 무슨 일이 있었는지 충분히 설명하는 요약입니다.\n\n"
            "[판단 기준]\n"
            "1. 핵심 사건뿐 아니라, 사건의 흐름(무엇이 먼저 일어나고 무엇으로 이어졌는지)을\n"
            "   순서대로 전달한다.\n"
            "2. 등장한 인물들의 상태 변화나 관계 변화가 있었다면 포함한다.\n"
            "3. 인상적인 대사나 장면이 있었다면, 짧게라도 언급한다 (통째로 인용하지 않는다).\n"
            "4. 본문에 없는 내용을 추측하거나 덧붙이지 않는다.\n"
            "5. 분량은 회차당 대략 300~500자 내외로, 각 회차의 복잡도에 맞게 조절한다.\n"
            "6. 각 회차의 요약은 그 회차 안의 내용만으로 작성한다. 다른 회차의 사건을\n"
            "   섞어 넣거나 미리 언급하지 않는다.\n\n"
            "[문장 규칙]\n"
            "7. \"이 회차는\", \"본문에서는\" 같은 메타 표현으로 시작하지 않고 바로 내용으로\n"
            "   시작한다.\n"
            "8. 각 회차 요약 앞에 아래 [출력 형식]의 제목만 붙이고, 그 외 완성된 요약문만\n"
            "   출력한다.\n\n"
            "[출력 형식]\n"
            "### {회차 제목 1}\n"
            "{요약}\n\n"
            "### {회차 제목 2}\n"
            "{요약}\n\n"
            "(선택한 회차 수만큼 반복)\n\n"
            f"[본문 - {n}개 회차, 순서대로]\n"
            f"{text}\n\n"
            "[요약 결과]"
        )

    @staticmethod
    def _build_submission_synopsis_prompt(
        outline_summary: str,
        synopsis_length_limit: int | None = None,
        intent_length_limit: int | None = None,
    ) -> str:
        """투고·공모전용 시놉시스 task prompt. No Core Identity re-declaration."""
        outline = str(outline_summary or "").strip()
        if outline:
            outline_block = (
                "[작가가 제공한 줄거리 개요 - 시작부터 결말까지]\n" + outline
            )
        else:
            outline_block = (
                "[작가가 제공한 줄거리 개요]\n"
                "(제공되지 않음 - 지금까지 쓰인 원고만\n"
                "       근거로 작성하며, 결말 관련 내용은 추측하지 않고 빈 부분으로 남긴다)"
            )
        if synopsis_length_limit:
            synopsis_length_note = (
                f"시놉시스는 {int(synopsis_length_limit)}자 이내로 작성한다."
            )
        else:
            synopsis_length_note = (
                "시놉시스 길이 제한은 없다. 내용을 충분히 전달할 수 있는 분량으로 작성한다."
            )
        if intent_length_limit:
            intent_length_note = (
                f"작품의도는 {int(intent_length_limit)}자 이내로 작성한다."
            )
        else:
            intent_length_note = (
                "작품의도 길이 제한은 없다. 1~2문단 정도로 작성한다."
            )
        return (
            "[현재 작업]\n"
            "투고·공모전 제출용 자료를 작성하세요. 아래 세 가지를 준비합니다.\n\n"
            "[작품의도]\n"
            "- 이 작품을 통해 작가가 전달하고자 하는 주제의식이나 문제의식을 정리한다.\n"
            f"- {intent_length_note}\n"
            "- [프로젝트 누적 정보]와 [작가가 제공한 줄거리 개요]에서 근거를 찾고,\n"
            "  지어내지 않는다.\n\n"
            "[로그라인 후보]\n"
            "- 이 작품을 한두 문장으로 압축한 로그라인을 5개 제시한다.\n"
            "- 각기 다른 강조점(인물/갈등/세계관/반전/정서 중심)으로 다양화한다.\n"
            "- 주인공이 누구인지, 무엇을 원하는지, 무엇이 가로막는지가 드러나야 한다.\n"
            "- 과장된 클리셰 수식어를 피하고 구체적인 인물·상황으로 승부한다.\n\n"
            "[시놉시스 - 기승전결 구조]\n"
            "- 이야기를 기(도입)-승(전개)-전(전환/절정)-결(결말) 순서로, 심사자가\n"
            "  전체 줄거리를 파악할 수 있도록 서술형으로 정리한다.\n"
            "- 이미 쓰인 부분은 [프로젝트 누적 정보]를, 결말을 포함해 아직 쓰이지\n"
            "  않은 부분은 [작가가 제공한 줄거리 개요]를 근거로 삼는다.\n"
            f"- {synopsis_length_note}\n"
            "- 문학적 표현보다 명확한 전달을 우선한다. 반전이나 결말도 숨기지 않고\n"
            "  솔직하게 서술한다 (독자용 홍보문이 아니라 심사용 자료이므로).\n\n"
            "[출력 형식]\n"
            "## 작품의도\n"
            "(내용)\n\n"
            "## 로그라인 후보\n"
            "1. (로그라인) — [강조점]\n"
            "2. ...\n"
            "(5개)\n\n"
            "## 시놉시스\n"
            "### 기 (도입)\n"
            "(내용)\n"
            "### 승 (전개)\n"
            "(내용)\n"
            "### 전 (전환/절정)\n"
            "(내용)\n"
            "### 결 (결말)\n"
            "(내용)\n\n"
            f"{outline_block}\n\n"
            "[제출용 자료]"
        )

    @staticmethod
    def _build_style_blend_check_prompt(reference_text: str, target_text: str) -> str:
        """스며듦 검사 task prompt. No Core Identity re-declaration."""
        reference = str(reference_text or "").strip()
        target = str(target_text or "").strip()
        return (
            "[현재 작업]\n"
            "아래 [비교 대상 텍스트]가 [기준 텍스트]와 문체·어투·리듬 면에서 자연스럽게\n"
            "어우러지는지 확인하세요.\n\n"
            "[판단 기준]\n"
            "1. 어휘 수준, 문장 길이의 리듬, 어투(존댓말/반말), 인물의 말투가 기준\n"
            "   텍스트와 일관되는지 비교한다.\n"
            "2. 기준 텍스트에서 비교 대상 텍스트로 넘어가는 경계 지점이 부자연스럽게\n"
            "   튀는지 특히 주의 깊게 본다.\n"
            "3. 상투적이거나 기계적으로 느껴지는 표현 패턴이 있는지 확인한다.\n"
            "   (예: 과도한 대구법, 상투적인 헤지 표현의 반복, 나열식 문장 구조 반복,\n"
            "   감정을 설명으로 덧붙이는 문장 등)\n"
            "4. 발견한 것을 \"문제\"로 단정하지 않는다. 관찰과 근거만 전달한다\n"
            "   (\"~해 보여요\", \"~일 수 있어요\" 표현을 쓴다).\n"
            "5. 특별히 튀는 부분이 없다면, 억지로 지적을 만들어내지 않고 자연스럽게\n"
            "   잘 어우러진다고 알려준다.\n\n"
            "[출력 형식]\n"
            "## 스며듦 체크 결과\n"
            "- 전반적 판단: 잘 어우러짐 / 약간 다르게 느껴짐 / 뚜렷하게 튐\n"
            "- 근거: (구체적인 문장이나 표현을 들어 설명)\n"
            "- (다르게 느껴지는 경우) 어느 지점이 특히 그런지, 왜 그런지\n\n"
            "이 결과는 문제 여부를 판정한 것이 아니라 관찰입니다. 유지할지 수정할지는\n"
            "작가님의 선택입니다.\n\n"
            "[기준 텍스트 - 원래 문체]\n"
            f"{reference}\n\n"
            "[비교 대상 텍스트 - 새로 생성/수정된 부분]\n"
            f"{target}\n\n"
            "[스며듦 체크 결과]"
        )

    @staticmethod
    def _build_focused_analysis_prompt(scene_content: str) -> str:
        """Feedback request / focused analysis (analyze). Task scope only."""
        text = str(scene_content or "").strip()
        return (
            "[현재 작업]\n"
            "아래 회차를 편집자 관점과 독자 관점에서 분석해 피드백을 제공하세요.\n\n"
            "[분석 원칙]\n"
            "1. 장점과 개선점을 균형 있게 다룬다. 어느 한쪽으로 치우치지 않는다.\n"
            "2. 막연한 칭찬(\"좋아요\", \"잘 쓰셨어요\")이나 막연한 비판(\"별로예요\")을 하지 않는다.\n"
            "   반드시 원고 안의 구체적인 근거(어떤 장면, 어떤 문장, 어떤 흐름)를 들어 설명한다.\n"
            "3. 개선점을 지적할 때는 왜 문제인지에서 그치지 않고, 어떻게 고칠 수 있을지\n"
            "   방향을 함께 제시한다.\n"
            "4. 작가의 의도된 스타일(예: 담백한 문체, 느린 전개)을 결함으로 오인하지 않는다.\n"
            "   의도된 것으로 보이면 그 자체를 지적하지 않고, 의도가 실제로 잘 구현되고\n"
            "   있는지를 본다.\n"
            "5. 이 원고의 장르·설정([프로젝트 누적 정보] 참고)에 맞는 기준으로 평가한다.\n"
            "   장르 관습과 무관한 일반적 기준을 들이대지 않는다.\n\n"
            "[편집자 관점 - 구조와 기법]\n"
            "- 전개 속도: 정보/사건이 너무 빠르게 혹은 느리게 배치되지 않았는지\n"
            "- 장면 구성: 장면 전환, 시점 처리, 묘사와 대사의 균형이 적절한지\n"
            "- 문장 기법: 반복되는 문장 패턴, 어색한 리듬, 정보 과잉/부족 여부\n\n"
            "[독자 관점 - 몰입 경험]\n"
            "- 흥미 유지: 어느 지점에서 몰입이 잘 되고, 어느 지점에서 흥미가 떨어질 수 있는지\n"
            "- 감정적 반응: 의도된 감정(긴장, 설렘, 슬픔 등)이 실제로 전달되는지\n"
            "- 다음 화 기대감: 이 회차가 다음 내용을 궁금하게 만드는지\n\n"
            "[출력 형식]\n"
            "## 편집자 관점\n"
            "**좋은 점**\n"
            "- (구체적 근거와 함께 1~3개)\n"
            "**개선점**\n"
            "- (구체적 근거 + 방향 제안과 함께 1~3개)\n\n"
            "## 독자 관점\n"
            "**좋은 점**\n"
            "- (구체적 근거와 함께 1~3개)\n"
            "**개선점**\n"
            "- (구체적 근거 + 방향 제안과 함께 1~3개)\n\n"
            "## 한 줄 총평\n"
            "(이 회차 전체를 관통하는 핵심 조언 한 문장)\n\n"
            "[본문]\n"
            f"{text}\n\n"
            "[분석 결과]"
        )

    @staticmethod
    def _build_focused_analysis_multi_prompt(combined_text: str) -> str:
        """Contiguous multi-episode feedback (analyze_multi). Task scope only."""
        text = str(combined_text or "").strip()
        episode_count = max(1, text.count("\n### ") + (1 if text.startswith("### ") else 0))
        return (
            "[현재 작업]\n"
            "아래는 연속된 여러 회차입니다. 개별 회차 단위가 아니라, 이 구간 전체를\n"
            "하나의 흐름으로 보고 편집자 관점과 독자 관점에서 분석해 피드백을 제공하세요.\n\n"
            "[분석 원칙]\n"
            "1. 장점과 개선점을 균형 있게 다룬다. 어느 한쪽으로 치우치지 않는다.\n"
            "2. 막연한 칭찬이나 막연한 비판을 하지 않는다. 반드시 몇 화의 어떤 장면·\n"
            "   문장·흐름인지 구체적으로 짚어 설명한다.\n"
            "3. 개선점을 지적할 때는 왜 문제인지에서 그치지 않고, 어떻게 고칠 수 있을지\n"
            "   방향을 함께 제시한다. 대부분의 경우 방향 제안에 그치되, 장르 관습을\n"
            "   명백히 벗어나거나 개연성이 무너지는 등 원문 자체가 그대로 두기 어려운\n"
            "   수준이라고 판단되면, 문제를 정확히 설명한 뒤 대체 가능한 전개나 장면을\n"
            "   직접 예시로 써서 제시한다. 다만 최종 선택은 작가의 몫임을 분명히 하고,\n"
            "   이 경우에도 \"반드시 이렇게 고쳐야 한다\"고 단정하지 않는다.\n"
            "4. 작가의 의도된 스타일(예: 담백한 문체, 느린 전개)을 결함으로 오인하지\n"
            "   않는다. 의도가 실제로 잘 구현되고 있는지를 본다.\n"
            "5. 이 원고의 장르·설정([프로젝트 누적 정보] 참고)에 맞는 기준으로 평가한다.\n\n"
            "[편집자 관점 - 구조와 기법]\n"
            "- 회차 간 강약 조절: 회차마다 긴장도·정보량이 적절히 완급 조절되는지,\n"
            "  특정 화만 처지거나 과열되지 않는지\n"
            "- 전개 속도: 이 구간 전체에서 사건이 너무 빠르게 혹은 느리게 배치되지\n"
            "  않았는지\n"
            "- 개연성: 회차를 넘어가며 사건·설정·인물 반응이 논리적으로 이어지는지\n"
            "- 캐릭터 일관성: 여러 회차에 걸쳐 인물의 성격·말투·가치관이 일관되게\n"
            "  유지되는지, 근거 없이 흔들리는 지점이 있는지\n"
            "- 문장 기법: 회차마다 반복되는 문장 패턴이나 상투적 표현이 누적되고\n"
            "  있지 않은지\n\n"
            "[독자 관점 - 몰입 경험]\n"
            "- 흡입력: 중간에 멈추지 않고 이 구간을 쭉 읽어나갈 만큼 매 화가 다음\n"
            "  화를 궁금하게 만드는지, 흐름이 끊기는 지점이 있는지\n"
            "- 감정적 반응: 의도된 감정이 회차를 거치며 축적되고 전달되는지\n"
            "- 시장성: 이 흐름이 독자층에게 소구할 만한 훅과 페이스를 갖추고 있는지\n"
            "  (장르 관습·연재 플랫폼 관행을 참고 기준으로 삼는다)\n\n"
            "[출력 형식]\n"
            "## 편집자 관점\n"
            "**좋은 점**\n"
            "- (몇 화의 어떤 부분인지 근거와 함께 1~3개)\n"
            "**개선점**\n"
            "- (근거 + 구체적 수정 방향과 함께 1~3개)\n\n"
            "## 독자 관점\n"
            "**좋은 점**\n"
            "- (근거와 함께 1~3개)\n"
            "**개선점**\n"
            "- (근거 + 구체적 수정 방향과 함께 1~3개)\n\n"
            "## 회차 간 흐름 총평\n"
            "이 구간 전체를 하나의 아크로 봤을 때 강약·속도·흡입력이 어떤지 3~5문장으로.\n\n"
            "## 한 줄 총평\n"
            "(이 구간 전체를 관통하는 핵심 조언 한 문장)\n\n"
            f"[본문 - {episode_count}개 회차, 순서대로]\n"
            f"{text}\n\n"
            "[분석 결과]"
        )

    @staticmethod
    def _build_next_idea_prompt(scene_content: str) -> str:
        """Next-idea suggestions (ideas mode). Task scope only."""
        text = str(scene_content or "").strip()
        return (
            "[현재 작업]\n"
            "아래는 방금 작성된 회차입니다. 이 흐름에서 자연스럽게 이어질 다음 전개\n"
            "아이디어를 3~5개 제안하세요.\n\n"
            "[판단 기준]\n"
            "1. 지금 회차의 마지막 장면에서 개연성 있게 이어지는 전개만 제안한다.\n"
            "   원고의 전체 흐름과 동떨어진 뜬금없는 사건을 제안하지 않는다.\n"
            "2. [프로젝트 누적 정보]에 있는 인물 성격, 세계관 규칙, 미회수 복선을 참고해\n"
            "   그 작품다운 방향으로 제안한다. 미회수 복선이 있다면 그것을 회수하거나\n"
            "   진전시키는 아이디어를 최소 1개 포함한다.\n"
            "3. 후보들은 서로 겹치지 않게, 각기 다른 방향(예: 갈등 심화 / 관계 변화 /\n"
            "   새로운 정보 공개 / 반전 등)을 다루도록 다양성을 준다.\n"
            "4. 이미 회수된 떡밥이나 이미 밝혀진 정보를 다시 반복해서 제안하지 않는다.\n\n"
            "[출력 형식]\n"
            "각 후보는 아래 형식으로 제시한다.\n"
            "**후보 N: (짧은 제목)**\n"
            "- 무엇을 하는 전개인지 1~2문장\n"
            "- 왜 이 시점에 자연스러운지 (근거 1문장)\n\n"
            "후보 수는 3~5개로 하고, 마지막에 \"이 중 어떤 방향이든 편하게 말씀해주시면\n"
            "더 구체적으로 함께 풀어볼게요.\" 같은 짧은 안내를 덧붙인다.\n\n"
            "[현재 회차 본문]\n"
            f"{text}\n\n"
            "[다음 아이디어 제안]"
        )

    @staticmethod
    def _build_next_idea_with_next_scene_prompt(prev_tail: str, next_text: str) -> str:
        """Next-idea when following episode already exists (ideas_next_exists)."""
        prev = str(prev_tail or "").strip()
        nxt = str(next_text or "").strip()
        return (
            "[현재 작업]\n"
            "아래는 방금 마무리된 회차와, 그 다음으로 이미 작성된 회차의 시작 부분입니다.\n"
            "다음 회차의 시작 전개가 적절한지 짧게 의견을 드리고, 이를 대체할 수 있는\n"
            "다른 전개·묘사 아이디어를 5개 제안하세요.\n\n"
            "[판단 기준]\n"
            "1. 직전 회차의 흐름(감정선, 사건의 여운, 마지막 장면)에서 다음 회차 시작이\n"
            "   자연스럽게 이어지는지 평가한다.\n"
            "2. 이미 매력적이고 개연성 있게 시작됐다면 그 점을 짧게 인정하고 넘어간다.\n"
            "   억지로 문제를 만들어내지 않는다.\n"
            "3. 평가와 별개로, 다른 방향에서 시작할 수 있는 후킹 있는 대안을 5개 제시한다.\n"
            "   기존 시작부를 재활용한 변주(같은 장면, 다른 묘사)와, 아예 다른 지점에서\n"
            "   시작하는 전개(다른 장면, 다른 사건)를 섞어서 다양성을 준다.\n"
            "4. 각 대안이 왜 이 시점에 효과적인지 짧게 근거를 단다.\n"
            "5. [프로젝트 누적 정보]의 인물 성격·세계관 규칙·미회수 복선을 참고해\n"
            "   그 작품다운 방향을 벗어나지 않는다.\n\n"
            "[출력 형식]\n"
            "## 지금 시작부에 대한 의견\n"
            "(2~3문장, 강요하지 않는 톤)\n\n"
            "## 대체 가능한 다른 시작 5가지\n"
            "**대안 N: (짧은 제목)**\n"
            "- 어떤 전개/묘사로 시작하는지 1~2문장\n"
            "- 왜 효과적인지 (근거 1문장)\n\n"
            "마지막에 \"어떤 방향이든 편하게 골라주시면 이어서 함께 다듬어볼게요.\" 같은\n"
            "짧은 안내를 덧붙인다.\n\n"
            "[직전 회차 마지막 부분]\n"
            f"{prev}\n\n"
            "[다음 회차 시작부 (이미 작성됨)]\n"
            f"{nxt}\n\n"
            "[검토 결과]"
        )

    @staticmethod
    def _build_brainstorm_prompt(scene_content: str, user_topic: str = "") -> str:
        """Brainstorming (brainstorm mode). Task scope only."""
        text = str(scene_content or "").strip()
        topic = str(user_topic or "").strip()
        if topic:
            topic_instruction = (
                "[작가가 지정한 주제]\n"
                f'"{topic}"에 대해 집중적으로 브레인스토밍한다.'
            )
        else:
            topic_instruction = (
                "[주제]\n"
                "작가가 특정 주제를 지정하지 않았다. 현재 회차와 지금까지의 흐름을\n"
                "       참고해, 이 작품이 확장될 수 있는 다양한 방향을 자유롭게 탐색한다."
            )
        return (
            "[현재 작업]\n"
            "아래 원고를 바탕으로 이 작품에 적용할 수 있는 아이디어를 5~8개 브레인스토밍하세요.\n\n"
            f"{topic_instruction}\n\n"
            "[판단 기준]\n"
            "1. \"다음 회차에 바로 이어지는 전개\"로 범위를 좁히지 않는다. 서브플롯, 반전,\n"
            "   새로운 인물, 세계관 확장, 관계 구도 변화, 주제 의식 등 다양한 층위에서\n"
            "   아이디어를 던진다.\n"
            "2. 지금 당장 실현 가능한지보다, 이 작품을 더 풍부하게 만들 가능성에 무게를\n"
            "   둔다. 다소 과감하거나 실험적인 아이디어도 배제하지 않는다.\n"
            "3. [프로젝트 누적 정보]에 있는 인물·세계관 설정과 완전히 모순되지 않는\n"
            "   범위 안에서 자유롭게 확장한다 (설정을 깨는 것과 설정을 확장하는 것은 다르다).\n"
            "4. 아이디어끼리 서로 다른 층위(플롯/인물/세계관/주제)를 다루도록 다양성을 준다.\n"
            "   같은 층위의 아이디어만 나열하지 않는다.\n\n"
            "[출력 형식]\n"
            "각 아이디어는 아래 형식으로 제시한다.\n"
            "**아이디어 N: (짧은 제목)** [층위: 플롯/인물/세계관/주제 중 표시]\n"
            "- 무엇인지 1~2문장\n"
            "- 이 작품에 어떤 재미나 깊이를 더할 수 있는지 1문장\n\n"
            "[현재 회차 또는 최근 원고]\n"
            f"{text}\n\n"
            "[브레인스토밍 결과]"
        )

    @staticmethod
    def _build_brainstorm_with_next_scene_prompt(
        prev_tail: str, next_text: str, user_topic: str = ""
    ) -> str:
        """Brainstorm when next episode exists (brainstorm_next_exists). C/D by topic."""
        scene_content = str(prev_tail or "").strip()
        next_scene_content = str(next_text or "").strip()
        topic = str(user_topic or "").strip()
        if topic:
            return (
                "[현재 작업]\n"
                "아래는 현재 회차와, 그 뒤로 이미 작성된 다음 회차입니다. 작가가 다음 전개에\n"
                "확신이 없거나 이미 쓴 다음 회차의 방향이 마음에 들지 않아 이 브레인스토밍을\n"
                "요청했을 수 있습니다. 두 회차의 흐름을 모두 참고해, 아래 주제를 중심으로\n"
                "이 지점에서 작품을 확장하거나 다른 방향으로 풀어갈 수 있는 아이디어를\n"
                "5~8개 제시하세요.\n\n"
                "[작가가 지정한 주제]\n"
                f'"{topic}"에 대해 집중적으로 브레인스토밍한다.\n\n'
                "[판단 기준]\n"
                "1. 이미 쓰인 다음 회차의 방향을 그대로 평가하거나 지적하지 않는다. 그 내용은\n"
                "   참고 맥락일 뿐이다.\n"
                "2. 지정된 주제를 중심으로 전개하되, 다음 회차의 방향을 살리는 아이디어와\n"
                "   완전히 다른 방향으로 전환하는 아이디어를 균형 있게 섞는다.\n"
                "3. 지금 당장 실현 가능한지보다, 이 작품을 더 풍부하게 만들 가능성에 무게를 둔다.\n"
                "4. [프로젝트 누적 정보]에 있는 인물·세계관 설정과 완전히 모순되지 않는\n"
                "   범위 안에서 자유롭게 확장한다.\n"
                "5. 아이디어끼리 서로 다른 층위(플롯/인물/세계관/주제)를 다루도록 다양성을 준다.\n\n"
                "[출력 형식]\n"
                "**아이디어 N: (짧은 제목)** [층위: 플롯/인물/세계관/주제 중 표시]\n"
                "- 무엇인지 1~2문장\n"
                "- 이 작품에 어떤 재미나 깊이를 더할 수 있는지 1문장\n\n"
                "[현재 회차]\n"
                f"{scene_content}\n\n"
                "[다음 회차 (이미 작성됨)]\n"
                f"{next_scene_content}\n\n"
                "[브레인스토밍 결과]"
            )
        return (
            "[현재 작업]\n"
            "아래는 현재 회차와, 그 뒤로 이미 작성된 다음 회차입니다. 작가가 다음 전개에\n"
            "확신이 없거나 이미 쓴 다음 회차의 방향이 마음에 들지 않아 이 브레인스토밍을\n"
            "요청했을 수 있습니다. 두 회차의 흐름을 모두 참고해, 이 지점에서 작품을\n"
            "확장하거나 다른 방향으로 풀어갈 수 있는 아이디어를 5~8개 제시하세요.\n\n"
            "[주제]\n"
            "작가가 특정 주제를 지정하지 않았다. 두 회차의 흐름을 참고해, 이 지점에서\n"
            "작품이 확장될 수 있는 다양한 방향을 자유롭게 탐색한다.\n\n"
            "[판단 기준]\n"
            "1. 이미 쓰인 다음 회차의 방향을 그대로 평가하거나 지적하지 않는다. 그 내용은\n"
            "   참고 맥락일 뿐이다.\n"
            "2. 다음 회차의 방향을 살리는 아이디어와, 완전히 다른 방향으로 전환하는\n"
            "   아이디어를 균형 있게 섞는다.\n"
            "3. \"다음 회차에 바로 이어지는 전개\"로만 범위를 좁히지 않는다. 서브플롯, 반전,\n"
            "   새로운 인물, 세계관 확장, 관계 구도 변화, 주제 의식 등 다양한 층위에서\n"
            "   아이디어를 던진다.\n"
            "4. 지금 당장 실현 가능한지보다, 이 작품을 더 풍부하게 만들 가능성에 무게를 둔다.\n"
            "5. [프로젝트 누적 정보]에 있는 인물·세계관 설정과 완전히 모순되지 않는\n"
            "   범위 안에서 자유롭게 확장한다.\n"
            "6. 아이디어끼리 서로 다른 층위(플롯/인물/세계관/주제)를 다루도록 다양성을 준다.\n\n"
            "[출력 형식]\n"
            "**아이디어 N: (짧은 제목)** [층위: 플롯/인물/세계관/주제 중 표시]\n"
            "- 무엇인지 1~2문장\n"
            "- 이 작품에 어떤 재미나 깊이를 더할 수 있는지 1문장\n\n"
            "[현재 회차]\n"
            f"{scene_content}\n\n"
            "[다음 회차 (이미 작성됨)]\n"
            f"{next_scene_content}\n\n"
            "[브레인스토밍 결과]"
        )

    @staticmethod
    def _build_world_description_prompt(target_subject: str, scene_content: str) -> str:
        """세계관 묘사 도우미 (worlddesc). Task scope only."""
        subject = str(target_subject or "").strip()
        text = str(scene_content or "").strip()
        return (
            "[현재 작업]\n"
            "아래 대상에 대해, 이 작품의 문체와 세계관에 맞는 묘사 문장을 작성하세요.\n"
            "작가가 원고에 바로 이어 붙이거나 참고해서 쓸 수 있는 수준으로 씁니다.\n\n"
            "[묘사 대상]\n"
            f"{subject}\n\n"
            "[판단 기준]\n"
            "1. 시스템 메시지의 장르·세계관 키워드와 [프로젝트 누적 정보]에 이미 확립된\n"
            "   설정(세계관 규칙, 지금까지 등장한 배경)에 부합하는 묘사를 만든다.\n"
            "   장르에 안 맞는 클리셰(예: 동양풍 세계관에 서구식 성 묘사)를 섞지 않는다.\n"
            "2. 현재 회차의 문체(문장 길이, 어휘 수준, 시점)와 어울리게 쓴다. 원고\n"
            "   전체와 톤이 튀지 않아야 한다.\n"
            "3. 오감(시각/청각/후각/촉각) 중 최소 2가지 이상을 활용해 입체적으로 묘사한다.\n"
            "   단, 모든 감각을 억지로 다 채우지 않는다.\n"
            "4. 정보 나열이 아니라 장면 속에서 자연스럽게 읽히는 묘사로 쓴다\n"
            "   (\"이곳은 ~한 곳이다\" 같은 설명체보다, 인물의 시선이나 행동에 녹인\n"
            "   묘사를 우선한다).\n"
            "5. 이미 확립된 설정과 모순되는 새로운 설정을 지어내지 않는다. 다만\n"
            "   기존 설정을 구체화하는 선에서는 세부 디테일을 자유롭게 채운다.\n\n"
            "[출력 형식]\n"
            "2~3개의 버전을 제공한다. 서로 다른 각도(예: 웅장함 강조 / 스산함 강조 /\n"
            "인물의 감정과 연결 등)로 다양화한다.\n\n"
            "**버전 1** [강조점: ...]\n"
            "(묘사 문장)\n\n"
            "**버전 2** [강조점: ...]\n"
            "(묘사 문장)\n\n"
            "**버전 3** [강조점: ...]\n"
            "(묘사 문장)\n\n"
            "[현재 회차 - 문체 참고용]\n"
            f"{text}\n\n"
            "[묘사 제안]"
        )

    @staticmethod
    def _build_setting_break_scan_prompt(original_text: str) -> str:
        """Setting-break detector task prompt (worldscan). No Core Identity block."""
        text = str(original_text or "").strip()
        return (
            "[현재 작업]\n"
            "아래 원고에서 이 작품의 세계관 또는 캐릭터 설정과 어긋나는 지점을 찾아내세요.\n\n"
            "[판단 근거 우선순위]\n"
            "1. 시스템 메시지에 이미 제공된 메인 장르·세계관 키워드·캐릭터 프로필을 최우선 기준으로 삼는다.\n"
            "2. [프로젝트 누적 정보]에 담긴 등장인물 특징·세계관 설정·지금까지의 줄거리를 다음 기준으로 삼는다.\n"
            "3. 위 두 곳에 명시되지 않은 부분은, 원고 안에서 이미 반복적으로 확립된 패턴(예: 이 인물이\n"
            "   지금까지 써온 말투)을 기준으로 삼는다.\n"
            "4. 위 어디에도 근거가 없으면 지적하지 않는다. 확실하지 않은 것을 추측해서 지적하지 않는다.\n\n"
            "[세계관 검사 기준]\n"
            "5. 이 작품의 장르·시대·문화적 배경과 맞지 않는 어휘, 개념, 사물, 존칭 등을 찾는다.\n"
            "   (예: 동양풍 세계관에 \"드래곤\"이 나오거나, 시대와 안 맞는 현대적 표현이 나오는 경우)\n"
            "6. 예외: 회귀·빙의·환생(회빙환) 설정이 확인된 인물이라면, 그 인물 본인의 내적 독백이나\n"
            "   발화에서 현대적 어휘·개념이 나오는 것은 설정상 자연스러울 수 있다. 다만 이 경우에도\n"
            "   서술자 시점의 지문(내레이션)이나 그 세계 토착 인물들의 발화에까지 그런 표현이 섞여\n"
            "   있다면 문제로 판단한다.\n\n"
            "[캐릭터 일관성 검사 기준]\n"
            "7. 인물의 행동·대사·가치관이 지금까지 확립된 성격에서 근거 없이 벗어나는지 확인한다.\n"
            "8. 판단 기준은 현실 세계의 일반적 도덕이 아니라, \"이 작품의 세계관 안에서 통용되는 규범\"이다.\n"
            "   예를 들어 폭력성이 높게 설정된 세계관에서 전투 중 살상이 일어나는 것은 그 자체로\n"
            "   문제가 아니다. 문제로 판단해야 하는 경우는, 이전까지 온건하게 확립된 인물이 서사적\n"
            "   맥락(동기, 계기) 없이 갑자기 그 세계관의 평균치를 넘어서는 행동을 보이는 등,\n"
            "   \"그 인물 자신의 확립된 캐릭터\"에서 벗어나는 지점이다.\n"
            "9. 서술자(전지적 작가) 시점의 지문이 캐릭터를 직접 규정하는 것과, 작중 다른 인물의\n"
            "   주관적 인상·반응으로 캐릭터가 묘사되는 것을 구분한다. 확립된 성격과 어긋나는\n"
            "   비유·어휘로 서술자가 캐릭터를 직접 규정하면 문제로 판단한다. 반면 다른 인물의\n"
            "   시선에서 \"~처럼 보였다\", \"~같았다\"와 같이 주관적으로 포착된 인상은, 그 자체로\n"
            "   캐릭터의 성격이 바뀐 것이 아니므로 문제로 판단하지 않는다.\n\n"
            "[출력 형식]\n"
            "발견된 항목이 있으면 아래 형식으로 나열한다.\n"
            "- 유형: [세계관 / 캐릭터]\n"
            "- 위치: 어느 부분인지 간단히 설명 (원문을 그대로 길게 인용하지 않는다)\n"
            "- 문제: 무엇이 왜 어긋나는지\n"
            "- 제안: 어떻게 고치면 좋을지 짧게\n\n"
            "발견된 항목이 없으면 \"이번 구간에서는 설정과 어긋나는 지점이 발견되지 않았습니다.\"라고만 답한다.\n"
            "과잉 지적하지 않는다. 확실한 것만 표시한다.\n\n"
            "[본문]\n"
            f"{text}\n\n"
            "[검사 결과]"
        )

    @staticmethod
    def _build_setting_break_scan_multi_prompt(combined_text: str) -> str:
        """Multi-episode setting-break detector (worldscan_multi). No Core Identity."""
        text = str(combined_text or "").strip()
        episode_count = max(1, text.count("\n### ") + (1 if text.startswith("### ") else 0))
        if episode_count < 1:
            episode_count = 1
        return (
            "[현재 작업]\n"
            "아래는 여러 회차입니다. 각 회차에서 이 작품의 세계관 또는 캐릭터 설정과\n"
            "어긋나는 지점을 찾아내세요. 회차별로 구분해서 결과를 제시합니다.\n\n"
            "[판단 근거 우선순위]\n"
            "1. 시스템 메시지에 이미 제공된 메인 장르·세계관 키워드·캐릭터 프로필을 최우선 기준으로 삼는다.\n"
            "2. [프로젝트 누적 정보]에 담긴 등장인물 특징·세계관 설정·지금까지의 줄거리를 다음 기준으로 삼는다.\n"
            "3. 위 두 곳에 명시되지 않은 부분은, 원고 안에서 이미 반복적으로 확립된 패턴(예: 이 인물이\n"
            "   지금까지 써온 말투)을 기준으로 삼는다.\n"
            "4. 위 어디에도 근거가 없으면 지적하지 않는다. 확실하지 않은 것을 추측해서 지적하지 않는다.\n\n"
            "[세계관 검사 기준]\n"
            "5. 이 작품의 장르·시대·문화적 배경과 맞지 않는 어휘, 개념, 사물, 존칭 등을 찾는다.\n"
            "   (예: 동양풍 세계관에 \"드래곤\"이 나오거나, 시대와 안 맞는 현대적 표현이 나오는 경우)\n"
            "6. 예외: 회귀·빙의·환생(회빙환) 설정이 확인된 인물이라면, 그 인물 본인의 내적 독백이나\n"
            "   발화에서 현대적 어휘·개념이 나오는 것은 설정상 자연스러울 수 있다. 다만 이 경우에도\n"
            "   서술자 시점의 지문(내레이션)이나 그 세계 토착 인물들의 발화에까지 그런 표현이 섞여\n"
            "   있다면 문제로 판단한다.\n\n"
            "[캐릭터 일관성 검사 기준]\n"
            "7. 인물의 행동·대사·가치관이 지금까지 확립된 성격에서 근거 없이 벗어나는지 확인한다.\n"
            "8. 판단 기준은 현실 세계의 일반적 도덕이 아니라, \"이 작품의 세계관 안에서 통용되는 규범\"이다.\n"
            "   예를 들어 폭력성이 높게 설정된 세계관에서 전투 중 살상이 일어나는 것은 그 자체로\n"
            "   문제가 아니다. 문제로 판단해야 하는 경우는, 이전까지 온건하게 확립된 인물이 서사적\n"
            "   맥락(동기, 계기) 없이 갑자기 그 세계관의 평균치를 넘어서는 행동을 보이는 등,\n"
            "   \"그 인물 자신의 확립된 캐릭터\"에서 벗어나는 지점이다.\n"
            "9. 서술자(전지적 작가) 시점의 지문이 캐릭터를 직접 규정하는 것과, 작중 다른 인물의\n"
            "   주관적 인상·반응으로 캐릭터가 묘사되는 것을 구분한다. 확립된 성격과 어긋나는\n"
            "   비유·어휘로 서술자가 캐릭터를 직접 규정하면 문제로 판단한다. 반면 다른 인물의\n"
            "   시선에서 \"~처럼 보였다\", \"~같았다\"와 같이 주관적으로 포착된 인상은, 그 자체로\n"
            "   캐릭터의 성격이 바뀐 것이 아니므로 문제로 판단하지 않는다.\n"
            "10. 각 회차의 판정은 그 회차 안의 내용만을 근거로 한다. 다른 회차에서 발견한 문제를\n"
            "    엉뚱한 회차의 결과에 섞어 넣지 않는다.\n\n"
            "[출력 형식]\n"
            "회차마다 아래 형식으로 결과를 제시한다.\n\n"
            "### {회차 제목}\n"
            "발견된 항목이 있으면 아래 형식으로 나열한다.\n"
            "- 유형: [세계관 / 캐릭터]\n"
            "- 위치: 어느 부분인지 간단히 설명 (원문을 그대로 길게 인용하지 않는다)\n"
            "- 문제: 무엇이 왜 어긋나는지\n"
            "- 제안: 어떻게 고치면 좋을지 짧게\n\n"
            "발견된 항목이 없으면 \"이 회차에서는 설정과 어긋나는 지점이 발견되지 않았습니다.\"\n"
            "라고만 답한다.\n\n"
            "(선택한 회차 수만큼 반복)\n\n"
            "과잉 지적하지 않는다. 확실한 것만 표시한다.\n\n"
            f"[본문 - {episode_count}개 회차, 순서대로]\n"
            f"{text}\n\n"
            "[검사 결과]"
        )

    @staticmethod
    def _tory_persona_system_prompt(persona_mode: str) -> str:
        """Tone / speech-style control for Tory ([Current Persona Mode])."""
        key = str(persona_mode or "default").strip().lower()
        # Accept Korean aliases and English keys
        aliases = {
            "기본": "default",
            "기본모드": "default",
            "default": "default",
            "base": "default",
            "assistant": "default",
            "진지": "secretary",
            "비서": "secretary",
            "진지비서": "secretary",
            "secretary": "secretary",
            "formal": "secretary",
            "친구": "friend",
            "friend": "friend",
            "buddy": "friend",
            "애교": "aegyo",
            "aegyo": "aegyo",
            "cute": "aegyo",
            "선생님": "teacher",
            "teacher": "teacher",
            "mentor": "teacher",
            "투덜이": "grumbler",
            "투덜": "grumbler",
            "팩폭": "grumbler",
            "grumbler": "grumbler",
            "cynical": "grumbler",
            "complainer": "grumbler",
        }
        key = aliases.get(
            key,
            key if key in {"default", "secretary", "friend", "aegyo", "teacher", "grumbler"} else "default",
        )
        modes = {
            "default": (
                "친절하고 유능한 AI 조수 톤. "
                "존댓말을 쓰되 부담 없이 또렷하게. 과장 없이 도움이 되는 설명을 한다."
            ),
            "secretary": (
                "진지 비서 모드: 경어체 사용. 사설 없이 명확·신속·격식 있는 보고서 톤. "
                "불릿·번호로 정리하고 감정 표현·감탄사는 거의 쓰지 않는다."
            ),
            "friend": (
                "친구 모드: 반말과 편한 존댓말을 자연스럽게 섞는다. "
                "격식 없는 찐친 느낌. 감탄사와 리액션을 적극 활용한다. "
                "다만 욕설·비하는 피한다."
            ),
            "aegyo": (
                "애교 모드: 문장 끝을 '~했어용', '~이랍니당'처럼 애교 있게. "
                "하트·이모지를 적극 쓰고 극강의 우쭈쭈·응원 톤. "
                "중요한 정보는 애교 속에서도 빠뜨리지 않는다."
            ),
            "teacher": (
                "선생님 모드: 단정하고 차분한 어조. "
                "조언과 피드백 위주의 지도자 톤. 칭찬과 개선점을 균형 있게 제시한다."
            ),
            "grumbler": (
                "투덜이 모드: 팩폭이 필요할 때 쓰는 톤. "
                "객관적 판단 기준(후킹·개연성·캐릭터 동기·리듬·세계관 이해도 등)은 다른 모드와 동일하다. "
                "달라지는 것은 표현뿐이다. "
                "좋은 점은 시니컬·투덜거리는 칭찬으로 말한다 "
                "(예: '질투가 나는군', '너무 재밌어서 잠을 못 잘 것 같으니 수면에 방해가 되겠군'). "
                "단점은 포장 없이, 그러나 무례하지 않게, 작품에 대한 1인칭 독자 반응으로 말한다 "
                "(예: '후킹이 없어서 심심해', '읽다 보니 지루해지는군', '앞뒤가 안 맞는군', "
                "'세계관이 낯설어서 난 적응 못하겠네', '이게 무슨 뜻인지 모르겠군', "
                "'주인공이 왜 이러는지 도통 이해가 안 가', '주인공의 행동이 맘에 안 들어'). "
                "절대 금지: 작가를 비난·몰아세우기, 인신공격, "
                "'이런 걸 누가 읽어?'처럼 작품·독자를 공격하는 말. "
                "오직 작품에 대한 관점만 말한다. 반말·투덜거리는 '~군/~네'체를 쓰되 욕설은 쓰지 않는다."
            ),
        }
        guide = modes[key]
        return (
            f"[Current Persona Mode: {key}]\n"
            f"- {guide}\n"
            "- 이 모드는 「어떻게 말하는가」(말투·어조)만 바꾼다. "
            "[Tory Core Identity]의 정체성·핵심 태도·판단 근거는 바꾸지 않는다.\n"
            "- 위 톤을 모든 문장에 일관되게 적용한다. "
            "작가 요청이 톤 변경이 아니면 다른 페르소나로 바꾸지 않는다.\n"
        )

    @staticmethod
    def _tory_genre_specialist_title(main_label: str) -> str:
        """Short specialist identity for the *current* project genre (context switch)."""
        main = (main_label or "").strip()
        if not main or main in {"미정", "없음", "-"}:
            return "전 장르 베테랑 전문 에디터"
        # Compact nicknames for common web-novel labels (optional polish)
        compact = {
            "로맨스 판타지": "로판",
            "로맨스판타지": "로판",
            "현대 판타지": "현판",
            "현대판타지": "현판",
            "무협": "무협",
            "판타지": "판타지",
            "SF": "SF",
            "미스터리": "미스터리",
            "스릴러": "스릴러",
            "호러": "호러",
            "에세이": "에세이",
            "수필": "에세이",
            "순수문학": "순수문학",
            "문학": "문학",
            "실용서": "실용서",
            "라이트노벨": "라노벨",
            "BL": "BL",
            "GL": "GL",
            "현대로맨스": "현로",
            "현대 로맨스": "현로",
            "역사": "역사",
            "대체역사": "대체역사",
            "게임": "게임",
            "스포츠": "스포츠",
            "드라마": "드라마",
            "일상": "일상",
        }
        nick = compact.get(main, main)
        return f"{nick} 전문 에디터"

    @classmethod
    def _tory_dynamic_context_system_prompt(
        cls,
        main_genre_label: str = "",
        sub_genre_label: str = "",
        world_setting_keywords: str = "",
        character_profiles: object = None,
        # Back-compat positional aliases
        main_label: str = "",
        sub_label: str = "",
    ) -> str:
        """
        Rebuild system_instruction on every request from live project metadata.
        Project A (로판) → 로판 전문 에디터 / Project B (에세이) → 에세이 전문 에디터.
        """
        main = (
            (main_genre_label or main_label or "미정").strip() or "미정"
        )
        sub = (
            (sub_genre_label or sub_label or "미정").strip() or "미정"
        )
        keywords = (world_setting_keywords or "").strip() or "미정"
        if isinstance(character_profiles, str):
            chars = character_profiles.strip() or "(캐릭터 미입력)"
        elif character_profiles:
            chars = cls._format_character_profiles(character_profiles) or "(캐릭터 미입력)"
        else:
            chars = "(캐릭터 미입력)"
        if len(chars) > 2500:
            chars = chars[:2500] + "…"

        specialist = cls._tory_genre_specialist_title(main)
        # Lead line matches the product contract:
        # "너는 로판 전문 에디터야. 메인장르: 로맨스판타지..."
        # "너는 에세이 전문 에디터야. 메인장르: 에세이..."
        active_identity = (
            f"너는 {specialist}야. "
            f"메인장르: {main}"
            + (f" · 서브장르: {sub}" if sub and sub != "미정" else "")
            + "."
        )

        return (
            "[Active Project System Instruction — rebuilt every request]\n"
            f"{active_identity}\n"
            "이번 요청에서는 위 정체성만 사용하세요. "
            "이전 프로젝트·이전 장르 페르소나는 전부 버리고(Flush) 이 문장으로 시야를 교체하세요.\n\n"
            "[Role & Capability]\n"
            f"- 당신은 [Tory Core Identity]를 유지한 채, 지금 '{main}' 작품만을 담당하는 "
            f"'{specialist}' 토리입니다.\n"
            "- 바탕에는 웹소설·순수문학·에세이·실용서 등 전 장르를 섭렵한 베테랑 역량이 있으나, "
            "현재 세션에서는 반드시 위 메인장르 전문 모드로만 사고·답변하세요.\n"
            "- 해당 장르의 문법, 톤앤매너, 클리셰, 서사 구조, 독자 기대를 깊이 있게 적용하세요.\n"
            "- 답변은 한국어로 합니다.\n\n"
            "[Dynamic Genre & Lore Adaptation Rules]\n"
            "1. 프로젝트 스위칭 (Context Switch):\n"
            "   - 특정 장르에 영구 고정된 선입견을 갖지 마세요.\n"
            "   - 유저가 프로젝트를 바꾸면 system_instruction 자체가 위처럼 다시 쓰입니다. "
            "예: 로판 작업 중 → '로판 전문 에디터' / 에세이 프로젝트로 전환 후 스캔·대화 클릭 → "
            "'에세이 전문 에디터'로 즉시 전환.\n"
            "   - 이전 작품의 캐릭터·세계관·톤을 현재 답변에 섞지 마세요.\n\n"
            "2. 현재 주입된 프로젝트 메타데이터 (이번 요청 절대 기준):\n"
            f"   - 메인 장르 (project_genre_main): {main}  ← 기본 디폴트·전문 모드 기준\n"
            f"   - 서브 장르 (project_genre_sub): {sub}\n"
            f"   - 세계관 & 설정집 키워드 (world_setting_keywords): {keywords}\n"
            f"   - 캐릭터 설정 (character_profiles): {chars}\n\n"
            "3. 원고 해석 지침:\n"
            f"   - 지금은 '{main}' 전문 에디터 시선으로만 원고를 읽고 의견을 주세요.\n"
            "   - 예: 메인 장르가 '로맨스 판타지'/'로판'이면 관계성·감정선·세계관 감성으로, "
            "'에세이'면 담백하고 진솔한 수필의 시선으로, "
            "'무협'이면 무협 어조·무림 세계관 시선으로 바라보세요.\n"
            "   - 추가로 전달되는 [Current Active Project Context](설정집 본문·캐릭터 상세)가 있으면 "
            "위 메타데이터와 함께 절대 기준으로 사용하세요.\n"
        )

    @classmethod
    def _tory_active_project_context(
        cls,
        *,
        main_genre_label: str,
        sub_genre_label: str,
        purpose_label: str = "",
        keywords_label: str = "",
        world_setting: str = "",
        character_profiles: object = None,
        project_title: str = "",
    ) -> str:
        """Serialize [Current Active Project Context] for prompts."""
        world = (world_setting or "").strip() or "(설정집 미입력)"
        if isinstance(character_profiles, str):
            chars = character_profiles.strip() or "(캐릭터 미입력)"
        elif character_profiles:
            chars = cls._format_character_profiles(character_profiles) or "(캐릭터 미입력)"
        else:
            chars = "(캐릭터 미입력)"
        keywords = (keywords_label or "").strip() or "미정"
        purpose = (purpose_label or "").strip() or "미정"
        title = (project_title or "").strip() or "(제목 없음)"
        return (
            "[Current Active Project Context] (= 현재 프로젝트 메타데이터 상세)\n"
            f"* 작품 제목: {title}\n"
            f"* 작품 종류: {purpose}\n"
            f"* 메인 장르 (project_genre_main): {main_genre_label or '미정'}\n"
            f"* 서브 장르 (project_genre_sub): {sub_genre_label or '미정'}\n"
            f"* 세계관 & 설정집 키워드 (world_setting_keywords): {keywords}\n"
            f"* 세계관·설정집 본문 (world_setting):\n{world}\n"
            f"* 캐릭터 설정 (character_profiles):\n{chars}\n"
        )

    # Back-compat alias used by older call sites
    @classmethod
    def _tory_genre_system_prompt(cls, main_label: str, sub_label: str) -> str:
        return cls._tory_dynamic_context_system_prompt(
            main_genre_label=main_label,
            sub_genre_label=sub_label,
        )

    def list_ideas(self, project_id: int) -> list[dict]:
        with database() as connection:
            self.require_project(connection, project_id)
            rows = connection.execute(
                "SELECT id, project_id, title, body_md, color, sort_order, created_at, updated_at "
                "FROM idea_note WHERE project_id = ? ORDER BY sort_order, id",
                (project_id,),
            ).fetchall()
        return [as_dict(row) for row in rows]

    def create_idea(self, project_id: int, body: dict) -> dict:
        title = str(body.get("title", "")).strip()[:120]
        body_md = str(body.get("body_md", "")).strip()[:8000]
        color = str(body.get("color", "yellow") or "yellow")
        if color not in IDEA_COLORS:
            color = "yellow"
        if not title and not body_md:
            title = "새 메모"
        with database() as connection:
            self.require_project(connection, project_id)
            sort_order = connection.execute(
                "SELECT COALESCE(MAX(sort_order) + 1, 0) FROM idea_note WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0]
            cursor = connection.execute(
                "INSERT INTO idea_note(project_id, title, body_md, color, sort_order) VALUES (?, ?, ?, ?, ?)",
                (project_id, title, body_md, color, sort_order),
            )
            row = connection.execute(
                "SELECT id, project_id, title, body_md, color, sort_order, created_at, updated_at "
                "FROM idea_note WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        return as_dict(row)  # type: ignore[return-value]

    def update_idea(self, idea_id: int, body: dict) -> dict:
        with database() as connection:
            row = connection.execute(
                "SELECT id, project_id, title, body_md, color, sort_order FROM idea_note WHERE id = ?",
                (idea_id,),
            ).fetchone()
            if row is None:
                raise ValueError("아이디어 메모를 찾을 수 없습니다.")
            title = row["title"]
            body_md = row["body_md"]
            color = row["color"]
            sort_order = row["sort_order"]
            if "title" in body:
                title = str(body.get("title", "")).strip()[:120]
            if "body_md" in body:
                body_md = str(body.get("body_md", "")).strip()[:8000]
            if "color" in body:
                next_color = str(body.get("color", "yellow") or "yellow")
                if next_color not in IDEA_COLORS:
                    raise ValueError("메모 색이 올바르지 않습니다.")
                color = next_color
            if "sort_order" in body:
                try:
                    sort_order = max(0, int(body.get("sort_order", 0)))
                except (TypeError, ValueError) as error:
                    raise ValueError("정렬 순서가 올바르지 않습니다.") from error
            connection.execute(
                "UPDATE idea_note SET title = ?, body_md = ?, color = ?, sort_order = ?, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                (title, body_md, color, sort_order, idea_id),
            )
            updated = connection.execute(
                "SELECT id, project_id, title, body_md, color, sort_order, created_at, updated_at "
                "FROM idea_note WHERE id = ?",
                (idea_id,),
            ).fetchone()
        return as_dict(updated)  # type: ignore[return-value]

    def delete_idea(self, idea_id: int) -> None:
        with database() as connection:
            row = connection.execute(
                "SELECT id FROM idea_note WHERE id = ?", (idea_id,)
            ).fetchone()
            if row is None:
                raise ValueError("아이디어 메모를 찾을 수 없습니다.")
            connection.execute("DELETE FROM idea_note WHERE id = ?", (idea_id,))

    # ── bait (떡밥 던지기) ──────────────────────────────────────────────

    @staticmethod
    def _bait_optional_scene_id(value: object) -> int | None:
        if value is None or value == "":
            return None
        try:
            number = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as error:
            raise ValueError("회차 id가 올바르지 않습니다.") from error
        return number if number > 0 else None

    def _resolve_bait_scene_id(
        self,
        connection: sqlite3.Connection,
        project_id: int,
        value: object,
        *,
        strict: bool = False,
    ) -> int | None:
        """Map a scene id to an active scene in this project.

        Empty/null clears the link. Non-empty ids that do not match a live scene:
        - strict=True (create/update UI): raise a clear validation error
        - strict=False (localStorage import): drop the link so migration still succeeds
        """
        scene_id = self._bait_optional_scene_id(value)
        if scene_id is None:
            return None
        row = connection.execute(
            "SELECT id FROM scene WHERE id = ? AND project_id = ? AND deleted_at IS NULL",
            (scene_id, project_id),
        ).fetchone()
        if row is None:
            if strict:
                raise ValueError(
                    "이 작품에 없는 회차예요. 심기·회수 회차를 다시 골라 주세요."
                )
            # Soft-deleted / other project / missing: drop the link rather than fail import.
            return None
        return scene_id

    def _serialize_bait_row(self, row: sqlite3.Row | dict) -> dict:
        item = as_dict(row)
        notify = item.get("notify_on_recover")
        notify_on = notify not in (0, "0", False, "false", None)
        return {
            "id": str(item.get("id") or ""),
            "project_id": int(item.get("project_id") or 0),
            "kind": "idea" if item.get("kind") == "idea" else "plant",
            "quote": item.get("quote") or "",
            "summary": item.get("summary") or "",
            "recover_content": item.get("recover_content") or "",
            "recover_at": item.get("recover_at") or "",
            "recover_scene_id": item.get("recover_scene_id"),
            "plant_scene_id": item.get("plant_scene_id"),
            "source_scene_id": item.get("source_scene_id"),
            "plant_at_note": item.get("plant_at_note") or "",
            "source_title": item.get("source_title") or "",
            "notify_on_recover": notify_on,
            "snooze_until": item.get("snooze_until") or None,
            "created_at": item.get("created_at") or "",
            # camelCase aliases for the existing frontend shape
            "recoverContent": item.get("recover_content") or "",
            "recoverAt": item.get("recover_at") or "",
            "recoverSceneId": item.get("recover_scene_id"),
            "plantSceneId": item.get("plant_scene_id"),
            "sourceSceneId": item.get("source_scene_id"),
            "plantAtNote": item.get("plant_at_note") or "",
            "sourceTitle": item.get("source_title") or "",
            "notifyOnRecover": notify_on,
            "snoozeUntil": item.get("snooze_until") or None,
            "createdAt": item.get("created_at") or "",
        }

    def _parse_bait_fields(
        self,
        connection: sqlite3.Connection,
        project_id: int,
        body: dict,
        *,
        existing: dict | None = None,
        strict_scenes: bool = True,
    ) -> dict:
        base = existing or {}
        kind_raw = body.get("kind", base.get("kind") or "plant")
        kind = "idea" if str(kind_raw or "plant") == "idea" else "plant"

        def text_field(key: str, camel: str, limit: int) -> str:
            if key in body:
                return str(body.get(key) or "")[:limit]
            if camel in body:
                return str(body.get(camel) or "")[:limit]
            return str(base.get(key) or "")[:limit]

        quote = text_field("quote", "quote", 20000)
        if not quote.strip() and not existing:
            raise ValueError("떡밥 내용을 적어 주세요.")
        if not quote.strip() and existing:
            quote = str(existing.get("quote") or "")

        summary = text_field("summary", "summary", 500)
        recover_content = text_field("recover_content", "recoverContent", 8000)
        recover_at = text_field("recover_at", "recoverAt", 200)
        plant_at_note = text_field("plant_at_note", "plantAtNote", 200)
        source_title = text_field("source_title", "sourceTitle", 200)

        def scene_from(body_key: str, camel: str, base_key: str) -> int | None:
            if body_key in body or camel in body:
                raw = body.get(body_key) if body_key in body else body.get(camel)
                return self._resolve_bait_scene_id(
                    connection, project_id, raw, strict=strict_scenes
                )
            return self._bait_optional_scene_id(base.get(base_key))

        recover_scene_id = scene_from("recover_scene_id", "recoverSceneId", "recover_scene_id")
        plant_scene_id = scene_from("plant_scene_id", "plantSceneId", "plant_scene_id")
        if "source_scene_id" in body or "sourceSceneId" in body:
            source_scene_id = self._resolve_bait_scene_id(
                connection,
                project_id,
                body.get("source_scene_id") if "source_scene_id" in body else body.get("sourceSceneId"),
                strict=strict_scenes,
            )
        elif plant_scene_id is not None and (
            "plant_scene_id" in body or "plantSceneId" in body
        ):
            source_scene_id = plant_scene_id
        else:
            source_scene_id = self._bait_optional_scene_id(base.get("source_scene_id"))
            if source_scene_id is None:
                source_scene_id = plant_scene_id

        if "notify_on_recover" in body or "notifyOnRecover" in body:
            raw_notify = body.get("notify_on_recover") if "notify_on_recover" in body else body.get("notifyOnRecover")
            notify_on = 0 if raw_notify in (False, 0, "0", "false", "False", None, "") else 1
        else:
            notify_on = 0 if base.get("notify_on_recover") in (0, False, "0", "false") else 1

        if "snooze_until" in body or "snoozeUntil" in body:
            raw_snooze = body.get("snooze_until") if "snooze_until" in body else body.get("snoozeUntil")
            if raw_snooze is None or raw_snooze == "":
                snooze_until = None
            else:
                snooze_until = str(raw_snooze).strip()[:64] or None
                if snooze_until and snooze_until != "next_open":
                    # Allow ISO-ish timestamps; reject random long junk.
                    snooze_until = snooze_until[:40]
        else:
            snooze_until = base.get("snooze_until")

        bait_id = None
        if "id" in body and body.get("id"):
            bait_id = str(body.get("id")).strip()[:80]
        elif existing:
            bait_id = str(existing.get("id") or "")

        created_at = None
        if "created_at" in body or "createdAt" in body:
            created_at = str(body.get("created_at") or body.get("createdAt") or "").strip()[:40] or None
        elif existing:
            created_at = existing.get("created_at")

        return {
            "id": bait_id,
            "kind": kind,
            "quote": quote,
            "summary": summary,
            "recover_content": recover_content,
            "recover_at": recover_at,
            "recover_scene_id": recover_scene_id,
            "plant_scene_id": plant_scene_id,
            "source_scene_id": source_scene_id,
            "plant_at_note": plant_at_note,
            "source_title": source_title,
            "notify_on_recover": notify_on,
            "snooze_until": snooze_until,
            "created_at": created_at,
        }

    def list_baits(self, project_id: int) -> list[dict]:
        with database() as connection:
            self.require_project(connection, project_id)
            rows = connection.execute(
                "SELECT id, project_id, kind, quote, summary, recover_content, recover_at, "
                "recover_scene_id, plant_scene_id, source_scene_id, plant_at_note, source_title, "
                "notify_on_recover, snooze_until, created_at "
                "FROM bait WHERE project_id = ? "
                "ORDER BY datetime(created_at) DESC, id DESC",
                (project_id,),
            ).fetchall()
        return [self._serialize_bait_row(row) for row in rows]

    def create_bait(self, project_id: int, body: dict) -> dict:
        with database() as connection:
            self.require_project(connection, project_id)
            fields = self._parse_bait_fields(connection, project_id, body or {})
            bait_id = fields["id"] or f"bait-{uuid.uuid4().hex[:16]}"
            if connection.execute("SELECT 1 FROM bait WHERE id = ?", (bait_id,)).fetchone():
                raise ValueError("이미 같은 id의 떡밥이 있어요.")
            connection.execute(
                "INSERT INTO bait("
                "id, project_id, kind, quote, summary, recover_content, recover_at, "
                "recover_scene_id, plant_scene_id, source_scene_id, plant_at_note, source_title, "
                "notify_on_recover, snooze_until, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "COALESCE(?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')))",
                (
                    bait_id,
                    project_id,
                    fields["kind"],
                    fields["quote"],
                    fields["summary"],
                    fields["recover_content"],
                    fields["recover_at"],
                    fields["recover_scene_id"],
                    fields["plant_scene_id"],
                    fields["source_scene_id"],
                    fields["plant_at_note"],
                    fields["source_title"],
                    fields["notify_on_recover"],
                    fields["snooze_until"],
                    fields["created_at"],
                ),
            )
            row = connection.execute(
                "SELECT id, project_id, kind, quote, summary, recover_content, recover_at, "
                "recover_scene_id, plant_scene_id, source_scene_id, plant_at_note, source_title, "
                "notify_on_recover, snooze_until, created_at FROM bait WHERE id = ?",
                (bait_id,),
            ).fetchone()
        return self._serialize_bait_row(row)  # type: ignore[arg-type]

    def import_baits(self, project_id: int, body: dict) -> dict:
        """Upsert bait rows (used when migrating localStorage → SQLite)."""
        items = body.get("items") if isinstance(body, dict) else None
        if not isinstance(items, list):
            raise ValueError("items 배열이 필요해요.")
        imported = 0
        updated = 0
        with database() as connection:
            self.require_project(connection, project_id)
            for raw in items:
                if not isinstance(raw, dict):
                    continue
                # Import is lenient: unknown scene ids are cleared, not rejected.
                fields = self._parse_bait_fields(
                    connection, project_id, raw, strict_scenes=False
                )
                bait_id = fields["id"] or f"bait-{uuid.uuid4().hex[:16]}"
                existing = connection.execute(
                    "SELECT id FROM bait WHERE id = ? AND project_id = ?",
                    (bait_id, project_id),
                ).fetchone()
                if existing:
                    connection.execute(
                        "UPDATE bait SET kind = ?, quote = ?, summary = ?, recover_content = ?, "
                        "recover_at = ?, recover_scene_id = ?, plant_scene_id = ?, source_scene_id = ?, "
                        "plant_at_note = ?, source_title = ?, notify_on_recover = ?, snooze_until = ? "
                        "WHERE id = ? AND project_id = ?",
                        (
                            fields["kind"],
                            fields["quote"],
                            fields["summary"],
                            fields["recover_content"],
                            fields["recover_at"],
                            fields["recover_scene_id"],
                            fields["plant_scene_id"],
                            fields["source_scene_id"],
                            fields["plant_at_note"],
                            fields["source_title"],
                            fields["notify_on_recover"],
                            fields["snooze_until"],
                            bait_id,
                            project_id,
                        ),
                    )
                    updated += 1
                else:
                    # Do not overwrite a row that belongs to another project.
                    if connection.execute("SELECT 1 FROM bait WHERE id = ?", (bait_id,)).fetchone():
                        bait_id = f"bait-{uuid.uuid4().hex[:16]}"
                    connection.execute(
                        "INSERT INTO bait("
                        "id, project_id, kind, quote, summary, recover_content, recover_at, "
                        "recover_scene_id, plant_scene_id, source_scene_id, plant_at_note, source_title, "
                        "notify_on_recover, snooze_until, created_at"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                        "COALESCE(?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')))",
                        (
                            bait_id,
                            project_id,
                            fields["kind"],
                            fields["quote"],
                            fields["summary"],
                            fields["recover_content"],
                            fields["recover_at"],
                            fields["recover_scene_id"],
                            fields["plant_scene_id"],
                            fields["source_scene_id"],
                            fields["plant_at_note"],
                            fields["source_title"],
                            fields["notify_on_recover"],
                            fields["snooze_until"],
                            fields["created_at"],
                        ),
                    )
                    imported += 1
        return {"ok": True, "imported": imported, "updated": updated, "total": imported + updated}

    def update_bait(self, bait_id: str, body: dict) -> dict:
        bait_id = str(bait_id or "").strip()
        if not bait_id:
            raise ValueError("떡밥 id가 없어요.")
        with database() as connection:
            row = connection.execute(
                "SELECT id, project_id, kind, quote, summary, recover_content, recover_at, "
                "recover_scene_id, plant_scene_id, source_scene_id, plant_at_note, source_title, "
                "notify_on_recover, snooze_until, created_at FROM bait WHERE id = ?",
                (bait_id,),
            ).fetchone()
            if row is None:
                raise ValueError("떡밥을 찾을 수 없습니다.")
            project_id = int(row["project_id"])
            fields = self._parse_bait_fields(
                connection,
                project_id,
                body or {},
                existing=as_dict(row),
            )
            connection.execute(
                "UPDATE bait SET kind = ?, quote = ?, summary = ?, recover_content = ?, "
                "recover_at = ?, recover_scene_id = ?, plant_scene_id = ?, source_scene_id = ?, "
                "plant_at_note = ?, source_title = ?, notify_on_recover = ?, snooze_until = ? "
                "WHERE id = ?",
                (
                    fields["kind"],
                    fields["quote"],
                    fields["summary"],
                    fields["recover_content"],
                    fields["recover_at"],
                    fields["recover_scene_id"],
                    fields["plant_scene_id"],
                    fields["source_scene_id"],
                    fields["plant_at_note"],
                    fields["source_title"],
                    fields["notify_on_recover"],
                    fields["snooze_until"],
                    bait_id,
                ),
            )
            updated = connection.execute(
                "SELECT id, project_id, kind, quote, summary, recover_content, recover_at, "
                "recover_scene_id, plant_scene_id, source_scene_id, plant_at_note, source_title, "
                "notify_on_recover, snooze_until, created_at FROM bait WHERE id = ?",
                (bait_id,),
            ).fetchone()
        return self._serialize_bait_row(updated)  # type: ignore[arg-type]

    def delete_bait(self, bait_id: str) -> None:
        bait_id = str(bait_id or "").strip()
        if not bait_id:
            raise ValueError("떡밥 id가 없어요.")
        with database() as connection:
            row = connection.execute(
                "SELECT id FROM bait WHERE id = ?", (bait_id,)
            ).fetchone()
            if row is None:
                raise ValueError("떡밥을 찾을 수 없습니다.")
            connection.execute("DELETE FROM bait WHERE id = ?", (bait_id,))

    # ── success pattern (흥행 공식 분석) ─────────────────────────────────

    def parse_success_pattern_document(self, body: dict) -> dict:
        filename = str(body.get("filename") or body.get("file_name") or "upload.txt")
        raw_b64 = body.get("content_base64") or body.get("contentBase64") or ""
        if not raw_b64:
            raise ValueError("파일 내용이 없어요.")
        try:
            data = base64.b64decode(str(raw_b64), validate=False)
        except (binascii.Error, ValueError) as error:
            raise ValueError("파일 데이터를 읽지 못했어요.") from error
        split_mode = str(body.get("split_mode") or body.get("splitMode") or "headings")
        episodes = success_pattern.parse_document_to_episodes(
            filename, data, split_mode=split_mode
        )
        total_chars = sum(int(ep.get("length") or 0) for ep in episodes)
        return {
            "ok": True,
            "filename": filename,
            "episode_count": len(episodes),
            "total_chars": total_chars,
            "episodes": episodes,
        }

    def _normalize_uploaded_sections(self, body: dict) -> list[success_pattern.UploadedSection]:
        raw_sections = body.get("sections") or body.get("uploaded_sections") or []
        if not isinstance(raw_sections, list) or not raw_sections:
            raise ValueError("분석할 구간 데이터가 없어요.")
        sections: list[success_pattern.UploadedSection] = []
        for raw in raw_sections:
            if not isinstance(raw, dict):
                continue
            key = str(raw.get("key") or "").strip().lower()
            if key not in success_pattern.SECTION_KEYS:
                # accept Korean labels
                for k, label in success_pattern.SECTION_LABELS.items():
                    if key in {k, label}:
                        key = k
                        break
            if key not in success_pattern.SECTION_KEYS:
                raise ValueError(f"알 수 없는 구간입니다: {raw.get('key')}")
            try:
                start_ep = int(raw.get("start_ep") or raw.get("start") or 1)
                end_ep = int(raw.get("end_ep") or raw.get("end") or start_ep)
            except (TypeError, ValueError) as error:
                raise ValueError("구간 화수 범위가 올바르지 않아요.") from error
            episodes: list[success_pattern.EpisodeUnit] = []
            for i, ep in enumerate(raw.get("episodes") or raw.get("chapters") or []):
                if isinstance(ep, str):
                    text = ep
                    title = f"{i + 1}화"
                elif isinstance(ep, dict):
                    text = str(ep.get("text") or ep.get("content") or "")
                    title = str(ep.get("title") or f"{i + 1}화")
                else:
                    continue
                episodes.append(
                    success_pattern.EpisodeUnit(title=title, text=text, index=i + 1)
                )
            sections.append(
                success_pattern.UploadedSection(
                    key=key,
                    start_ep=start_ep,
                    end_ep=end_ep,
                    episodes=episodes,
                )
            )
        if not sections:
            raise ValueError("업로드된 구간이 없어요.")
        return sections

    def check_success_pattern_budget(self, body: dict) -> dict:
        sections = self._normalize_uploaded_sections(body)
        episode_total = sum(max(s.uploaded_count, s.episode_count) for s in sections)
        # Prefer actual uploaded episode counts when present
        uploaded_eps = sum(s.uploaded_count for s in sections)
        if uploaded_eps:
            episode_total = uploaded_eps
        ep_budget = success_pattern.check_episode_budget(episode_total)
        char_budget = success_pattern.check_character_budget(sections)
        stats = success_pattern.compute_quantitative_stats(sections)
        status = "ok"
        if ep_budget["status"] == "blocked" or char_budget["status"] == "blocked":
            status = "blocked"
        elif ep_budget["status"] == "warning" or char_budget["status"] == "warning":
            status = "warning"
        return {
            "status": status,
            "episode_budget": ep_budget,
            "character_budget": char_budget,
            "stats": stats,
        }

    def run_success_pattern_analysis(self, body: dict) -> dict:
        work_title = str(body.get("work_title") or body.get("workTitle") or "").strip()
        if not work_title:
            raise ValueError("작품명을 입력해 주세요.")
        try:
            total_chapters = int(body.get("total_chapters") or body.get("totalChapters") or 0)
        except (TypeError, ValueError) as error:
            raise ValueError("총 회차 수가 올바르지 않아요.") from error
        if total_chapters < 1:
            raise ValueError("총 회차 수를 1 이상으로 입력해 주세요.")

        sections = self._normalize_uploaded_sections(body)
        uploaded_eps = sum(s.uploaded_count for s in sections)
        if uploaded_eps < 1:
            raise ValueError("분석할 회차 본문이 없어요. 구간 파일을 올려 주세요.")

        ep_budget = success_pattern.check_episode_budget(uploaded_eps)
        if ep_budget["status"] == "blocked":
            raise ValueError(ep_budget["message"])
        char_budget = success_pattern.check_character_budget(sections)
        if char_budget["status"] == "blocked":
            raise ValueError(char_budget["message"])

        dry_run = bool(body.get("dry_run") or body.get("dryRun"))
        # Cap per-episode text sent to model
        max_scene_chars = 12000
        chapter_notes: list[dict] = []
        for section in sections:
            for ep in section.episodes:
                content = ep.text or ""
                if len(content) > max_scene_chars:
                    content = content[:max_scene_chars] + "\n…(이하 생략)"
                prompt = success_pattern.build_structural_observation_prompt(content)
                if dry_run or not gemini_client.is_configured():
                    note = success_pattern.mock_observation_note(ep.text or "")
                    used_mock = True
                else:
                    try:
                        # Task-only: no Tory Core Identity system block.
                        raw = gemini_client.generate_text(
                            prompt,
                            system=None,
                            temperature=0.35,
                            max_output_tokens=1024,
                        )
                        note = success_pattern.extract_json_object(raw)
                        used_mock = False
                    except Exception:
                        note = success_pattern.mock_observation_note(ep.text or "")
                        used_mock = True
                chapter_notes.append({
                    "section_key": section.key,
                    "section_label": section.label,
                    "episode_title": ep.title,
                    "episode_index": ep.index,
                    "char_count": ep.length,
                    "observation": note,
                    "mock": used_mock,
                })

        quantitative = success_pattern.compute_quantitative_stats(sections)
        merge_prompt = success_pattern.build_success_pattern_merge_prompt(
            quantitative, chapter_notes
        )
        profile_mock = True
        if dry_run or not gemini_client.is_configured() or any(n.get("mock") for n in chapter_notes):
            profile = success_pattern.mock_merge_profile(quantitative, chapter_notes)
        else:
            try:
                raw_profile = gemini_client.generate_text(
                    merge_prompt,
                    system=None,
                    temperature=0.4,
                    max_output_tokens=2048,
                )
                profile = success_pattern.extract_json_object(raw_profile)
                profile_mock = False
            except Exception:
                profile = success_pattern.mock_merge_profile(quantitative, chapter_notes)
                profile_mock = True

        analyzed_sections = [
            {
                "key": s.key,
                "label": s.label,
                "start_ep": s.start_ep,
                "end_ep": s.end_ep,
                "episode_count": s.uploaded_count,
                "char_count": s.char_count,
            }
            for s in sections
        ]

        with database() as connection:
            cursor = connection.execute(
                "INSERT INTO success_pattern_profile("
                "work_title, total_chapters, analyzed_sections_json, profile_json, quantitative_json"
                ") VALUES (?, ?, ?, ?, ?)",
                (
                    work_title[:200],
                    total_chapters,
                    json.dumps(analyzed_sections, ensure_ascii=False),
                    json.dumps(profile, ensure_ascii=False),
                    json.dumps(quantitative, ensure_ascii=False),
                ),
            )
            profile_id = int(cursor.lastrowid)
            row = connection.execute(
                "SELECT id, work_title, total_chapters, analyzed_sections_json, profile_json, "
                "quantitative_json, built_at FROM success_pattern_profile WHERE id = ?",
                (profile_id,),
            ).fetchone()
        return {
            "ok": True,
            "profile": self._serialize_success_pattern_row(row),
            "chapter_notes": chapter_notes,
            "used_mock": profile_mock,
            "episode_budget": ep_budget,
            "character_budget": char_budget,
        }

    def _serialize_success_pattern_row(self, row: sqlite3.Row | dict | None) -> dict:
        if row is None:
            raise ValueError("프로파일을 찾을 수 없습니다.")
        item = as_dict(row) or {}

        def _loads(key: str, default):
            raw = item.get(key)
            if isinstance(raw, (dict, list)):
                return raw
            try:
                return json.loads(raw or ("[]" if isinstance(default, list) else "{}"))
            except (json.JSONDecodeError, TypeError):
                return default

        return {
            "id": int(item.get("id") or 0),
            "work_title": item.get("work_title") or "",
            "total_chapters": item.get("total_chapters"),
            "analyzed_sections": _loads("analyzed_sections_json", []),
            "profile": _loads("profile_json", {}),
            "quantitative": _loads("quantitative_json", {}),
            "built_at": item.get("built_at") or "",
        }

    def list_success_pattern_profiles(self) -> list[dict]:
        with database() as connection:
            rows = connection.execute(
                "SELECT id, work_title, total_chapters, analyzed_sections_json, profile_json, "
                "quantitative_json, built_at FROM success_pattern_profile "
                "ORDER BY datetime(built_at) DESC, id DESC LIMIT 100"
            ).fetchall()
        return [self._serialize_success_pattern_row(r) for r in rows]

    def get_success_pattern_profile(self, profile_id: int) -> dict:
        with database() as connection:
            row = connection.execute(
                "SELECT id, work_title, total_chapters, analyzed_sections_json, profile_json, "
                "quantitative_json, built_at FROM success_pattern_profile WHERE id = ?",
                (profile_id,),
            ).fetchone()
        return self._serialize_success_pattern_row(row)

    def save_chapter(self, chapter_id: int, body: dict) -> None:
        """Rename a chapter folder.

        Pilot (3-3-b-1): write **folder first**, then legacy ``chapter`` (still dual-write).
        """
        title = str(body.get("title", "")).strip()
        if not title:
            raise ValueError("챕터 이름을 입력해 주세요.")
        if len(title) > 200:
            raise ValueError("챕터 이름이 너무 깁니다.")
        with database() as connection:
            chapter = connection.execute(
                "SELECT id, project_id, title FROM chapter WHERE id = ? AND deleted_at IS NULL",
                (chapter_id,),
            ).fetchone()
            if chapter is None:
                raise ValueError("챕터를 찾을 수 없습니다.")
            project_id = int(chapter["project_id"])
            old_title = str(chapter["title"] or "")
            folder_id = None
            try:
                folder_id = folder_tree.folder_id_for_source(
                    connection, project_id, "chapter", int(chapter_id)
                )
            except sqlite3.OperationalError:
                folder_id = None
            # 1) Primary write: parallel folder row (source_kind/chapter map)
            try:
                cur = connection.execute(
                    """
                    UPDATE folder
                    SET title = ?,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                        row_version = row_version + 1
                    WHERE project_id = ?
                      AND source_kind = 'chapter'
                      AND source_id = ?
                      AND deleted_at IS NULL
                    """,
                    (title, project_id, int(chapter_id)),
                )
                folder_updated = cur.rowcount > 0
            except sqlite3.OperationalError:
                folder_updated = False
            # 2) Legacy write: chapter table (compat / dual-write)
            connection.execute(
                "UPDATE chapter SET title = ? WHERE id = ?",
                (title, chapter_id),
            )
            # If folder map was missing, rebuild once so outline folder path stays complete
            if not folder_updated:
                self._mirror_project_folders(connection, project_id)
                try:
                    folder_id = folder_tree.folder_id_for_source(
                        connection, project_id, "chapter", int(chapter_id)
                    )
                except sqlite3.OperationalError:
                    folder_id = None
            if folder_id is not None and old_title != title:
                short = old_title if len(old_title) <= 24 else old_title[:23] + "…"
                folder_tree.append_folder_action_log(
                    connection,
                    project_id,
                    "folder.rename",
                    f"「{short or '폴더'}」 이름 변경",
                    folder_tree.build_patch_action_payload(
                        folder_id, "title", old_title, title
                    ),
                )

    def _chapter_group_filter_sql(self, part_id) -> tuple[str, tuple]:
        """SQL fragment + params for top-level chapters in one binder group.

        Chapters nested under a manuscript (parent_scene_id set) are excluded.
        """
        if part_id is None:
            return "part_id IS NULL AND parent_scene_id IS NULL", ()
        return "part_id = ? AND parent_scene_id IS NULL", (int(part_id),)

    def _assign_chapter_sort_orders(
        self,
        connection: sqlite3.Connection,
        project_id: int,
        chapter_ids: list[int],
        part_id=None,
    ) -> None:
        """Park then assign 0..n-1 to avoid unique(sort_order) clashes within a group."""
        base = 1_000_000
        for index, chapter_id in enumerate(chapter_ids):
            connection.execute(
                "UPDATE chapter SET sort_order = ? "
                "WHERE id = ? AND project_id = ? AND deleted_at IS NULL",
                (base + index, chapter_id, project_id),
            )
        for index, chapter_id in enumerate(chapter_ids):
            connection.execute(
                "UPDATE chapter SET sort_order = ? "
                "WHERE id = ? AND project_id = ? AND deleted_at IS NULL",
                (index, chapter_id, project_id),
            )

    def _assign_part_sort_orders(
        self, connection: sqlite3.Connection, project_id: int, part_ids: list[int]
    ) -> None:
        base = 1_000_000
        for index, part_id in enumerate(part_ids):
            connection.execute(
                "UPDATE part SET sort_order = ? "
                "WHERE id = ? AND project_id = ? AND deleted_at IS NULL",
                (base + index, part_id, project_id),
            )
        for index, part_id in enumerate(part_ids):
            connection.execute(
                "UPDATE part SET sort_order = ? "
                "WHERE id = ? AND project_id = ? AND deleted_at IS NULL",
                (index, part_id, project_id),
            )

    def _next_part_title(
        self, connection: sqlite3.Connection, project_id: int, style: str | None = None
    ) -> str:
        rows = connection.execute(
            "SELECT title FROM part WHERE project_id = ? AND deleted_at IS NULL "
            "ORDER BY sort_order, id",
            (project_id,),
        ).fetchall()
        titles = [str(row["title"] or "") for row in rows]
        suffix = "권"
        hint = str(style or "").strip().lower()
        if hint in {"bu", "부", "part"}:
            suffix = "부"
        elif hint in {"kwon", "권", "volume", "vol"}:
            suffix = "권"
        else:
            bu_hits = sum(1 for t in titles if re.search(r"부\s*$", t) or "부" in t)
            kwon_hits = sum(1 for t in titles if re.search(r"권\s*$", t) or "권" in t)
            if bu_hits > kwon_hits:
                suffix = "부"
        return f"{len(titles) + 1}{suffix}"

    def create_part(self, project_id: int, body: dict) -> dict:
        """Create a binder volume/part (1권, 2부, …) that groups folders.

        Optional body.chapter_id moves that folder into the new 권/부 immediately.
        3-3-b-3: folder-first dual-write (insert folder, then part, then bind source_id).
        """
        move_chapter_id = None
        raw_ch = body.get("chapter_id")
        if raw_ch is not None and str(raw_ch).strip() != "":
            try:
                move_chapter_id = int(raw_ch)
            except (TypeError, ValueError) as error:
                raise ValueError("넣을 폴더 정보가 올바르지 않습니다.") from error

        with database() as connection:
            self.require_project(connection, project_id)
            style = body.get("style")
            title = str(body.get("title") or "").strip()
            if not title:
                title = self._next_part_title(connection, project_id, style)
            if len(title) > 200:
                raise ValueError("권/부 이름이 너무 깁니다.")
            # Keep sort_order aligned across legacy part and root folders
            part_sort = connection.execute(
                "SELECT COALESCE(MAX(sort_order) + 1, 0) FROM part "
                "WHERE project_id = ? AND deleted_at IS NULL",
                (project_id,),
            ).fetchone()[0]
            try:
                folder_sort = folder_tree.next_folder_sibling_sort(
                    connection, project_id, None
                )
            except sqlite3.OperationalError:
                folder_sort = int(part_sort)
            sort_order = max(int(part_sort), int(folder_sort))

            # 1) folder first (source bound after legacy id is known)
            try:
                new_folder_id = folder_tree._insert_folder(
                    connection,
                    project_id=project_id,
                    parent_id=None,
                    title=title,
                    synopsis_md="",
                    notes_md="",
                    goal_word_count=0,
                    is_box=1,
                    sort_order=sort_order,
                    source_kind=None,
                    source_id=None,
                )
            except sqlite3.OperationalError:
                new_folder_id = None

            # 2) legacy part
            cursor = connection.execute(
                "INSERT INTO part(project_id, title, sort_order) VALUES (?, ?, ?)",
                (project_id, title, sort_order),
            )
            part_id = int(cursor.lastrowid)

            # 3) bind map (folder.id ≠ part.id)
            if new_folder_id is not None:
                folder_tree.bind_folder_source(
                    connection, new_folder_id, "part", part_id
                )
            else:
                self._mirror_project_folders(connection, project_id)
                new_folder_id = folder_tree.folder_id_for_source(
                    connection, project_id, "part", part_id
                )

            moved_chapter_id = None
            if move_chapter_id is not None:
                chapter = connection.execute(
                    "SELECT id, project_id, part_id FROM chapter "
                    "WHERE id = ? AND deleted_at IS NULL",
                    (move_chapter_id,),
                ).fetchone()
                if chapter is None or int(chapter["project_id"]) != project_id:
                    raise ValueError("권/부에 넣을 폴더를 찾을 수 없습니다.")
                old_part = (
                    int(chapter["part_id"]) if chapter["part_id"] is not None else None
                )
                next_order = connection.execute(
                    "SELECT COALESCE(MAX(sort_order) + 1, 0) FROM chapter "
                    "WHERE project_id = ? AND part_id = ? AND parent_scene_id IS NULL "
                    "AND deleted_at IS NULL",
                    (project_id, part_id),
                ).fetchone()[0]
                connection.execute(
                    "UPDATE chapter SET part_id = ?, sort_order = ?, "
                    "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), "
                    "row_version = row_version + 1 "
                    "WHERE id = ?",
                    (part_id, next_order, move_chapter_id),
                )
                src_sql, src_params = self._chapter_group_filter_sql(old_part)
                remaining = [
                    int(row["id"])
                    for row in connection.execute(
                        f"SELECT id FROM chapter "
                        f"WHERE project_id = ? AND deleted_at IS NULL AND {src_sql} "
                        f"ORDER BY sort_order, id",
                        (project_id, *src_params),
                    ).fetchall()
                ]
                if remaining:
                    self._assign_chapter_sort_orders(
                        connection, project_id, remaining, part_id=old_part
                    )
                # Mirror move into folder tree without full rebuild when possible
                ch_folder_id = folder_tree.folder_id_for_source(
                    connection, project_id, "chapter", move_chapter_id
                )
                if ch_folder_id is not None and new_folder_id is not None:
                    connection.execute(
                        """
                        UPDATE folder
                        SET parent_id = ?,
                            sort_order = ?,
                            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                            row_version = row_version + 1
                        WHERE id = ?
                        """,
                        (new_folder_id, int(next_order), ch_folder_id),
                    )
                else:
                    self._mirror_project_folders(connection, project_id)
                moved_chapter_id = move_chapter_id

            # U3: log folder.create only when no chapter was moved in (policy A)
            if (
                moved_chapter_id is None
                and new_folder_id is not None
                and folder_tree.action_log_table_ready(connection)
            ):
                short = title if len(title) <= 24 else title[:23] + "…"
                try:
                    fr = connection.execute(
                        "SELECT parent_id, sort_order FROM folder WHERE id = ?",
                        (int(new_folder_id),),
                    ).fetchone()
                    parent_id = None
                    so = int(sort_order or 0)
                    if fr is not None:
                        raw_p = fr["parent_id"] if hasattr(fr, "keys") else fr[0]
                        parent_id = int(raw_p) if raw_p is not None else None
                        so = int(
                            (fr["sort_order"] if hasattr(fr, "keys") else fr[1]) or 0
                        )
                    folder_tree.append_folder_action_log(
                        connection,
                        project_id,
                        "folder.create",
                        f"「{short or '폴더'}」 생성",
                        folder_tree.build_create_action_payload(
                            folder_id=int(new_folder_id),
                            source_kind="part",
                            source_id=int(part_id),
                            parent_id=parent_id,
                            sort_order=so,
                        ),
                    )
                except sqlite3.OperationalError:
                    pass

        return {
            "id": part_id,
            "title": title,
            "sort_order": int(sort_order or 0),
            "chapter_id": moved_chapter_id,
        }

    def save_part(self, part_id: int, body: dict) -> dict:
        """Rename a part (권/부) folder.

        Pilot extension (3-3-b-2): write **folder first**, then legacy ``part`` (still dual-write).
        """
        title = str(body.get("title", "")).strip()
        if not title:
            raise ValueError("권/부 이름을 입력해 주세요.")
        if len(title) > 200:
            raise ValueError("권/부 이름이 너무 깁니다.")
        with database() as connection:
            part = connection.execute(
                "SELECT id, project_id, title FROM part WHERE id = ? AND deleted_at IS NULL",
                (part_id,),
            ).fetchone()
            if part is None:
                raise ValueError("권/부를 찾을 수 없습니다.")
            project_id = int(part["project_id"])
            old_title = str(part["title"] or "")
            folder_id = None
            try:
                folder_id = folder_tree.folder_id_for_source(
                    connection, project_id, "part", int(part_id)
                )
            except sqlite3.OperationalError:
                folder_id = None
            # 1) Primary write: parallel folder row (source_kind/part map)
            try:
                cur = connection.execute(
                    """
                    UPDATE folder
                    SET title = ?,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                        row_version = row_version + 1
                    WHERE project_id = ?
                      AND source_kind = 'part'
                      AND source_id = ?
                      AND deleted_at IS NULL
                    """,
                    (title, project_id, int(part_id)),
                )
                folder_updated = cur.rowcount > 0
            except sqlite3.OperationalError:
                folder_updated = False
            # 2) Legacy write: part table (compat / dual-write)
            connection.execute(
                "UPDATE part SET title = ?, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), "
                "row_version = row_version + 1 "
                "WHERE id = ?",
                (title, part_id),
            )
            if not folder_updated:
                self._mirror_project_folders(connection, project_id)
                try:
                    folder_id = folder_tree.folder_id_for_source(
                        connection, project_id, "part", int(part_id)
                    )
                except sqlite3.OperationalError:
                    folder_id = None
            if folder_id is not None and old_title != title:
                short = old_title if len(old_title) <= 24 else old_title[:23] + "…"
                folder_tree.append_folder_action_log(
                    connection,
                    project_id,
                    "folder.rename",
                    f"「{short or '폴더'}」 이름 변경",
                    folder_tree.build_patch_action_payload(
                        folder_id, "title", old_title, title
                    ),
                )
        return {"ok": True, "id": part_id, "title": title}

    def trash_part(self, part_id: int) -> dict:
        """Soft-delete a volume/part and cascade to its folders/scenes.

        3-3-b-4: folder-first soft-delete (folder subtree, then legacy part/chapter/scene).
        """
        with database() as connection:
            part = connection.execute(
                "SELECT id, project_id, title FROM part "
                "WHERE id = ? AND deleted_at IS NULL",
                (part_id,),
            ).fetchone()
            if part is None:
                raise ValueError("권/부를 찾을 수 없습니다. 이미 버려졌을 수 있어요.")
            project_id = int(part["project_id"])
            chapter_ids = [
                int(row[0])
                for row in connection.execute(
                    "SELECT id FROM chapter WHERE part_id = ? AND deleted_at IS NULL",
                    (part_id,),
                ).fetchall()
            ]
            scene_ids = [
                int(row[0])
                for row in connection.execute(
                    "SELECT s.id FROM scene s "
                    "JOIN chapter c ON c.id = s.chapter_id "
                    "WHERE c.part_id = ? AND s.deleted_at IS NULL AND c.deleted_at IS NULL",
                    (part_id,),
                ).fetchall()
            ]
            # U3: snapshot before soft-delete
            trash_payload = None
            part_title = str(part["title"] or "")
            try:
                root_fid = folder_tree.folder_id_for_source(
                    connection, project_id, "part", int(part_id)
                )
                if root_fid is not None and folder_tree.action_log_table_ready(
                    connection
                ):
                    trash_payload = folder_tree.snapshot_folder_trash(
                        connection,
                        project_id,
                        int(root_fid),
                        part_ids=[int(part_id)],
                        chapter_ids=chapter_ids,
                        scene_ids=scene_ids,
                    )
            except (sqlite3.OperationalError, ValueError):
                trash_payload = None

            now_sql = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
            # 1) folder first: part folder + descendants (chapter folders under it)
            try:
                folder_tree.soft_delete_folder_for_source(
                    connection, project_id, "part", int(part_id), cascade_children=True
                )
                # Safety: soft-delete chapter folders by source_id even if tree parent drifted
                for cid in chapter_ids:
                    folder_tree.soft_delete_folder_for_source(
                        connection,
                        project_id,
                        "chapter",
                        cid,
                        cascade_children=True,
                    )
            except sqlite3.OperationalError:
                pass
            # 2) legacy cascade
            connection.execute(
                f"UPDATE part SET deleted_at = {now_sql}, "
                f"updated_at = {now_sql}, "
                f"row_version = row_version + 1 "
                "WHERE id = ? AND deleted_at IS NULL",
                (part_id,),
            )
            if chapter_ids:
                connection.execute(
                    f"UPDATE chapter SET deleted_at = COALESCE(deleted_at, {now_sql}), "
                    f"updated_at = {now_sql} "
                    "WHERE part_id = ? AND deleted_at IS NULL",
                    (part_id,),
                )
            if scene_ids:
                connection.execute(
                    f"UPDATE scene SET deleted_at = COALESCE(deleted_at, {now_sql}), "
                    f"updated_at = {now_sql} "
                    "WHERE id IN ({}) AND deleted_at IS NULL".format(
                        ",".join("?" * len(scene_ids))
                    ),
                    scene_ids,
                )
            if trash_payload is not None:
                short = (
                    part_title
                    if len(part_title) <= 24
                    else part_title[:23] + "…"
                )
                folder_tree.append_folder_action_log(
                    connection,
                    project_id,
                    "folder.trash",
                    f"「{short or '폴더'}」 버리기",
                    trash_payload,
                )
        return {
            "ok": True,
            "id": part_id,
            "title": part["title"],
            "project_id": project_id,
            "chapter_count": len(chapter_ids),
            "scene_count": len(scene_ids),
            "chapter_ids": chapter_ids,
            "scene_ids": scene_ids,
        }

    def reorder_parts(self, project_id: int, body: dict) -> None:
        """3-3-b-5: folder-first part reorder, then legacy part.sort_order."""
        raw_ids = body.get("part_ids", [])
        if not isinstance(raw_ids, list) or not raw_ids:
            raise ValueError("바꿀 권/부 목록이 비어 있습니다.")
        try:
            part_ids = [int(value) for value in raw_ids]
        except (TypeError, ValueError) as error:
            raise ValueError("권/부 목록이 올바르지 않습니다.") from error
        if len(part_ids) != len(set(part_ids)):
            raise ValueError("권/부 목록에 중복이 있습니다.")
        with database() as connection:
            self.require_project(connection, project_id)
            rows = connection.execute(
                "SELECT id FROM part "
                "WHERE project_id = ? AND deleted_at IS NULL "
                "ORDER BY sort_order, id",
                (project_id,),
            ).fetchall()
            existing_ids = [int(row["id"]) for row in rows]
            if sorted(existing_ids) != sorted(part_ids):
                raise ValueError(
                    "현재 작품의 권/부 목록과 일치하지 않습니다. 새로고침 후 다시 시도해 주세요."
                )
            # 1) folder first
            try:
                folder_tree.reapply_part_folder_order(connection, project_id, part_ids)
            except sqlite3.OperationalError:
                pass
            # 2) legacy
            self._assign_part_sort_orders(connection, project_id, part_ids)

    def move_chapter(self, chapter_id: int, body: dict) -> dict:
        """Move a folder into a 권/부 or back to ungrouped.

        3-3-b-5: folder-first parent/sort update, then legacy chapter.part_id.
        """
        raw_part = body.get("part_id", None)
        if raw_part is None or raw_part == "" or raw_part is False:
            target_part_id = None
        else:
            try:
                target_part_id = int(raw_part)
            except (TypeError, ValueError) as error:
                raise ValueError("옮길 권/부가 올바르지 않습니다.") from error

        with database() as connection:
            chapter = connection.execute(
                "SELECT id, project_id, part_id, title FROM chapter "
                "WHERE id = ? AND deleted_at IS NULL",
                (chapter_id,),
            ).fetchone()
            if chapter is None:
                raise ValueError("챕터를 찾을 수 없습니다.")
            project_id = int(chapter["project_id"])
            old_part_id = chapter["part_id"]
            if old_part_id is not None:
                old_part_id = int(old_part_id)

            if target_part_id is not None:
                part = connection.execute(
                    "SELECT id FROM part "
                    "WHERE id = ? AND project_id = ? AND deleted_at IS NULL",
                    (target_part_id, project_id),
                ).fetchone()
                if part is None:
                    raise ValueError("옮길 권/부를 찾을 수 없습니다.")

            if old_part_id == target_part_id:
                return {
                    "ok": True,
                    "id": chapter_id,
                    "part_id": target_part_id,
                    "moved": False,
                }

            group_sql, group_params = self._chapter_group_filter_sql(target_part_id)
            next_order = connection.execute(
                f"SELECT COALESCE(MAX(sort_order) + 1, 0) FROM chapter "
                f"WHERE project_id = ? AND deleted_at IS NULL AND {group_sql}",
                (project_id, *group_params),
            ).fetchone()[0]

            # 1) folder first
            folder_ok = False
            try:
                folder_ok = folder_tree.move_chapter_folder_to_part(
                    connection,
                    project_id,
                    int(chapter_id),
                    target_part_id,
                    int(next_order),
                )
            except sqlite3.OperationalError:
                folder_ok = False

            # 2) legacy
            connection.execute(
                "UPDATE chapter SET part_id = ?, sort_order = ?, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), "
                "row_version = row_version + 1 "
                "WHERE id = ?",
                (target_part_id, next_order, chapter_id),
            )
            # Compact sort orders in the source group (legacy + folder)
            src_sql, src_params = self._chapter_group_filter_sql(old_part_id)
            remaining = [
                int(row["id"])
                for row in connection.execute(
                    f"SELECT id FROM chapter "
                    f"WHERE project_id = ? AND deleted_at IS NULL AND {src_sql} "
                    f"ORDER BY sort_order, id",
                    (project_id, *src_params),
                ).fetchall()
            ]
            if remaining:
                self._assign_chapter_sort_orders(
                    connection, project_id, remaining, part_id=old_part_id
                )
                try:
                    folder_tree.recompact_chapter_folders_under_part(
                        connection, project_id, old_part_id
                    )
                except sqlite3.OperationalError:
                    pass
            # Destination group compact for folder siblings
            try:
                folder_tree.recompact_chapter_folders_under_part(
                    connection, project_id, target_part_id
                )
            except sqlite3.OperationalError:
                pass
            if not folder_ok:
                self._mirror_project_folders(connection, project_id)
        return {
            "ok": True,
            "id": chapter_id,
            "title": chapter["title"],
            "part_id": target_part_id,
            "moved": True,
        }

    def reorder_projects(self, body: dict) -> dict:
        """Persist manual project list order and switch list mode to manual."""
        raw_ids = body.get("project_ids", [])
        if not isinstance(raw_ids, list) or not raw_ids:
            raise ValueError("바꿀 작품 목록이 비어 있습니다.")
        try:
            project_ids = [int(value) for value in raw_ids]
        except (TypeError, ValueError) as error:
            raise ValueError("작품 목록이 올바르지 않습니다.") from error
        if len(project_ids) != len(set(project_ids)):
            raise ValueError("작품 목록에 중복이 있습니다.")

        with database() as connection:
            rows = connection.execute(
                "SELECT id FROM project WHERE deleted_at IS NULL ORDER BY id"
            ).fetchall()
            existing_ids = [int(row["id"]) for row in rows]
            if sorted(existing_ids) != sorted(project_ids):
                raise ValueError(
                    "현재 작품 목록과 일치하지 않습니다. 새로고침 후 다시 시도해 주세요."
                )
            # Two-phase update avoids unique conflicts if a unique index is added later.
            for offset, project_id in enumerate(project_ids):
                connection.execute(
                    "UPDATE project SET list_sort_order = ? WHERE id = ?",
                    (10_000 + offset, project_id),
                )
            for order, project_id in enumerate(project_ids):
                connection.execute(
                    "UPDATE project SET list_sort_order = ? WHERE id = ?",
                    (order, project_id),
                )
            mode = set_project_list_mode(connection, "manual")
            projects = list_projects_payload(connection)
        return {"ok": True, "list_mode": mode, "projects": projects}

    def set_projects_list_mode(self, body: dict) -> dict:
        """Switch between recent-open order and manual list order."""
        mode = str(body.get("mode") or "recent").strip().lower()
        if mode not in {"recent", "manual"}:
            raise ValueError("작품 목록 정렬 방식이 올바르지 않습니다.")
        with database() as connection:
            if mode == "manual":
                # Seed manual order from current recent order if switching into manual.
                current = list_projects_payload(connection)
                ids = [int(item["id"]) for item in current]
                for offset, project_id in enumerate(ids):
                    connection.execute(
                        "UPDATE project SET list_sort_order = ? WHERE id = ?",
                        (10_000 + offset, project_id),
                    )
                for order, project_id in enumerate(ids):
                    connection.execute(
                        "UPDATE project SET list_sort_order = ? WHERE id = ?",
                        (order, project_id),
                    )
            saved = set_project_list_mode(connection, mode)
            projects = list_projects_payload(connection)
        return {"ok": True, "list_mode": saved, "projects": projects}

    def reorder_chapters(self, project_id: int, body: dict) -> None:
        """Set chapter sort_order to match the given id list within one binder group."""
        raw_ids = body.get("chapter_ids", [])
        if not isinstance(raw_ids, list) or not raw_ids:
            raise ValueError("바꿀 챕터 목록이 비어 있습니다.")
        try:
            chapter_ids = [int(value) for value in raw_ids]
        except (TypeError, ValueError) as error:
            raise ValueError("챕터 목록이 올바르지 않습니다.") from error
        if len(chapter_ids) != len(set(chapter_ids)):
            raise ValueError("챕터 목록에 중복이 있습니다.")

        raw_part = body.get("part_id", None)
        if raw_part is None or raw_part == "" or str(raw_part).lower() == "null":
            part_id = None
        else:
            try:
                part_id = int(raw_part)
            except (TypeError, ValueError) as error:
                raise ValueError("권/부 정보가 올바르지 않습니다.") from error

        with database() as connection:
            self.require_project(connection, project_id)
            group_sql, group_params = self._chapter_group_filter_sql(part_id)
            rows = connection.execute(
                f"SELECT id FROM chapter "
                f"WHERE project_id = ? AND deleted_at IS NULL AND {group_sql} "
                f"ORDER BY sort_order, id",
                (project_id, *group_params),
            ).fetchall()
            existing_ids = [int(row["id"]) for row in rows]
            if sorted(existing_ids) != sorted(chapter_ids):
                raise ValueError(
                    "현재 그룹의 챕터 목록과 일치하지 않습니다. 새로고침 후 다시 시도해 주세요."
                )
            parent_folder_id = None
            if part_id is not None:
                parent_folder_id = folder_tree.folder_id_for_source(
                    connection, project_id, "part", int(part_id)
                )
            # 1) folder first
            try:
                folder_tree.reapply_chapter_folder_order(
                    connection, project_id, chapter_ids, parent_folder_id
                )
            except sqlite3.OperationalError:
                pass
            # 2) legacy
            self._assign_chapter_sort_orders(
                connection, project_id, chapter_ids, part_id=part_id
            )

    def renumber_chapter_titles(self, project_id: int, body: dict) -> dict:
        """Rewrite chapter titles to sequential numbers (1장, 2장, …), keeping residual titles.

        When parts (권/부) exist, numbering restarts inside each part, then ungrouped.
        Logs one folder.renumber_titles undo entry when any title actually changes.
        """
        style = str(body.get("style", "jang") or "jang").strip().lower()
        if style not in {"jang", "je_jang", "dot"}:
            style = "jang"

        number_prefix = re.compile(
            r"^\s*(?:제\s*)?\d+\s*(?:장|화|부|편)?\s*[.．:：\-–—]?\s*",
            re.UNICODE,
        )
        number_dot = re.compile(r"^\s*\d+\s*[.)．]\s*", re.UNICODE)

        def strip_number(title: str) -> str:
            text = str(title or "").strip()
            text = number_prefix.sub("", text)
            text = number_dot.sub("", text)
            return text.strip()

        def format_title(index: int, base: str) -> str:
            n = index + 1
            if style == "je_jang":
                return f"제{n}장 {base}".strip() if base else f"제{n}장"
            if style == "dot":
                return f"{n}. {base}".strip() if base else f"{n}."
            return f"{n}장 {base}".strip() if base else f"{n}장"

        with database() as connection:
            self.require_project(connection, project_id)
            parts = connection.execute(
                "SELECT id FROM part WHERE project_id = ? AND deleted_at IS NULL "
                "ORDER BY sort_order, id",
                (project_id,),
            ).fetchall()
            groups: list[tuple] = [(int(row["id"]),) for row in parts]
            groups.append((None,))  # ungrouped last

            updated = []
            changed_items: list[dict] = []
            for (group_part_id,) in groups:
                group_sql, group_params = self._chapter_group_filter_sql(group_part_id)
                rows = connection.execute(
                    f"SELECT id, title FROM chapter "
                    f"WHERE project_id = ? AND deleted_at IS NULL AND {group_sql} "
                    f"ORDER BY sort_order, id",
                    (project_id, *group_params),
                ).fetchall()
                for index, row in enumerate(rows):
                    chapter_id = int(row["id"])
                    old_title = str(row["title"] or "")
                    base = strip_number(old_title)
                    new_title = format_title(index, base)
                    if new_title != old_title:
                        connection.execute(
                            "UPDATE chapter SET title = ? WHERE id = ? AND project_id = ?",
                            (new_title, chapter_id, project_id),
                        )
                        folder_id = None
                        try:
                            folder_id = folder_tree.folder_id_for_source(
                                connection, project_id, "chapter", chapter_id
                            )
                        except sqlite3.OperationalError:
                            folder_id = None
                        changed_items.append(
                            {
                                "chapter_id": chapter_id,
                                "folder_id": folder_id,
                                "old": old_title,
                                "new": new_title,
                            }
                        )
                    updated.append(
                        {
                            "id": chapter_id,
                            "title": new_title,
                            "previous_title": old_title,
                            "part_id": group_part_id,
                        }
                    )
            self._mirror_project_folders(connection, project_id)
            if changed_items and folder_tree.action_log_table_ready(connection):
                folder_tree.append_folder_action_log(
                    connection,
                    project_id,
                    "folder.renumber_titles",
                    "순번 정리",
                    folder_tree.build_renumber_titles_action_payload(
                        style=style,
                        items=changed_items,
                    ),
                )
        return {
            "ok": True,
            "style": style,
            "chapters": updated,
            "count": len(updated),
            "changed": len(changed_items),
        }

    def project_manuscript_stats(self, project_id: int) -> dict:
        """Aggregate plain-text stats across all active scenes in a project."""
        with database() as connection:
            self.require_project(connection, project_id)
            rows = connection.execute(
                "SELECT r.content_md "
                "FROM scene s "
                "JOIN scene_revision r ON r.scene_id = s.id AND r.is_current = 1 "
                "WHERE s.project_id = ? AND s.deleted_at IS NULL "
                "ORDER BY s.chapter_id, s.sort_order, s.id",
                (project_id,),
            ).fetchall()
        parts: list[str] = []
        for row in rows:
            text = plain_text_from_content(row["content_md"] or "")
            if text:
                parts.append(text)
        combined = "\n\n".join(parts)
        # Mirror web/app.js computeTextStats
        chars_with_space = len(combined)
        chars_no_space = len(re.sub(r"\s+", "", combined))
        words = len(re.findall(r"\S+", combined))
        letters = len(re.findall(r"[\w\uAC00-\uD7A3]", combined, flags=re.UNICODE))
        return {
            "chars_with_space": chars_with_space,
            "chars_no_space": chars_no_space,
            "words": words,
            "letters": letters,
            "scenes": len(rows),
            "scenes_with_text": len(parts),
        }

    def export_plain_text_document(self, body: dict) -> document_export.ExportFile:
        """Export one plain document (reference material) to txt/md/html/rtf/docx/hwpx."""
        title = str(body.get("title") or body.get("filename") or "참고자료").strip() or "참고자료"
        text = str(body.get("text") or body.get("content") or body.get("body") or "")
        format_key = str(body.get("format") or body.get("format_key") or "docx").strip().lower()
        if not text.strip() and format_key not in {"txt", "md"}:
            # Still allow empty-ish export for draft notes
            text = text or ""
        try:
            return document_export.export_plain_document(
                format_key,
                title=title,
                text=text,
            )
        except ValueError:
            raise
        except Exception as error:  # noqa: BLE001
            raise ValueError(f"내보내기에 실패했습니다: {error}") from error

    def export_project(
        self,
        project_id: int,
        format_key: str,
        scene_ids: list[int] | None = None,
        export_title: str | None = None,
    ) -> document_export.ExportFile:
        """Build a downloadable manuscript file for the given project.

        scene_ids: when set, export only those 회차 (episodes). Full work when None/empty.
        """
        selected: set[int] | None = None
        if scene_ids:
            selected = {int(x) for x in scene_ids if int(x) > 0}
            if not selected:
                raise ValueError("내보낼 회차를 하나 이상 선택해 주세요.")

        with database() as connection:
            self.require_project(connection, project_id)
            project = connection.execute(
                "SELECT id, title, purpose, uuid, package_path FROM project "
                "WHERE id = ? AND deleted_at IS NULL",
                (project_id,),
            ).fetchone()
            if project is None:
                raise ValueError("작품을 찾을 수 없습니다.")
            chapters_rows = connection.execute(
                "SELECT id, title, sort_order FROM chapter "
                "WHERE project_id = ? AND deleted_at IS NULL "
                "ORDER BY sort_order, id",
                (project_id,),
            ).fetchall()
            scenes_rows = connection.execute(
                "SELECT s.id, s.chapter_id, s.title, s.sort_order, r.content_md "
                "FROM scene s "
                "JOIN scene_revision r ON r.scene_id = s.id AND r.is_current = 1 "
                "WHERE s.project_id = ? AND s.deleted_at IS NULL "
                "ORDER BY s.sort_order, s.id",
                (project_id,),
            ).fetchall()

            stg_bytes: bytes | None = None
            if str(format_key or "").lower() == "stg":
                if selected is not None:
                    raise ValueError(
                        "SuperTORY 연결 파일(.stg)은 작품 전체만 내보낼 수 있어요. "
                        "회차별 내보내기는 Word·한글·텍스트 등을 이용해 주세요."
                    )
                # Refresh package handle then read bytes for download.
                package_info = ensure_project_package(connection, project_id)
                package_path = package_info.get("package_path") or project["package_path"]
                if not package_path:
                    raise ValueError("연결 파일(.stg) 경로를 만들지 못했습니다.")
                path = Path(package_path)
                if not path.is_file():
                    raise ValueError("연결 파일(.stg)을 찾지 못했습니다.")
                stg_bytes = path.read_bytes()

        # Filter to selected scenes when requested
        if selected is not None:
            found = {int(s["id"]) for s in scenes_rows}
            missing = selected - found
            if missing:
                raise ValueError("선택한 회차 중 일부를 찾지 못했어요.")
            scenes_rows = [s for s in scenes_rows if int(s["id"]) in selected]
            if not scenes_rows:
                raise ValueError("내보낼 회차 내용이 없어요.")

        scenes_by_chapter: dict[int, list[dict]] = {}
        single_scene_title = ""
        for scene in scenes_rows:
            scenes_by_chapter.setdefault(scene["chapter_id"], []).append({
                "title": scene["title"] or "",
                "content_plain": plain_text_from_content(scene["content_md"] or ""),
            })
            if selected is not None and len(selected) == 1:
                single_scene_title = str(scene["title"] or "").strip()

        chapters = [
            {
                "title": chapter["title"] or "",
                "scenes": scenes_by_chapter.get(chapter["id"], []),
            }
            for chapter in chapters_rows
            if scenes_by_chapter.get(chapter["id"])
        ]
        # Title: full project keeps a document heading.
        # Partial (회차 선택) export omits the top heading so UI labels like
        # "선택회차2" / "2개 회차" never appear inside the manuscript file.
        project_title = str(project["title"] or "작품").strip() or "작품"
        include_doc_title = selected is None
        raw_export_title = str(export_title or "").strip()
        ui_hint = bool(
            not raw_export_title
            or raw_export_title in {"이 회차", "선택 회차", "현재 회차"}
            or re.fullmatch(r"선택회차\d*", raw_export_title)
            or re.fullmatch(r"\d+개 회차", raw_export_title)
        )
        if selected is None:
            file_title = raw_export_title if not ui_hint else project_title
        elif not ui_hint:
            # Custom title only affects the download filename for partial exports
            file_title = raw_export_title
        elif len(selected) == 1 and single_scene_title:
            file_title = f"{project_title} - {single_scene_title}"
        else:
            file_title = project_title

        return document_export.export_bytes(
            format_key,
            project_title=file_title,
            chapters=chapters,
            stg_bytes=stg_bytes,
            include_title=include_doc_title,
        )

    def _build_scene_tree(self, flat_scenes: list[dict]) -> list[dict]:
        """Nest scenes by parent_scene_id; roots first, depth-first children."""
        by_parent: dict[int | None, list[dict]] = {}
        for scene in flat_scenes:
            raw = scene.get("parent_scene_id")
            parent_key = int(raw) if raw is not None else None
            node = {**scene, "children": []}
            by_parent.setdefault(parent_key, []).append(node)
        # Preserve sibling order from SQL (sort_order, id)
        for siblings in by_parent.values():
            siblings.sort(
                key=lambda s: (int(s.get("sort_order") or 0), int(s.get("id") or 0))
            )

        def attach(parent_key: int | None) -> list[dict]:
            nodes = by_parent.get(parent_key, [])
            for node in nodes:
                node["children"] = attach(int(node["id"]))
            return nodes

        roots = attach(None)
        # Orphans (parent missing / deleted) float to root
        known_ids = {int(s["id"]) for s in flat_scenes}
        for parent_key, nodes in list(by_parent.items()):
            if parent_key is None:
                continue
            if parent_key not in known_ids:
                for node in nodes:
                    if node not in roots:
                        roots.append(node)
        return roots

    def _attach_child_chapters_to_scenes(
        self,
        scene_roots: list[dict],
        by_parent_scene: dict[int, list[dict]],
        flat_by_chapter: dict[int, list[dict]],
    ) -> None:
        """Attach folders nested under manuscripts (chapter.parent_scene_id)."""

        def walk(nodes: list[dict]) -> None:
            for node in nodes or []:
                sid = int(node["id"])
                nested_payloads: list[dict] = []
                for ch in by_parent_scene.get(sid, []):
                    ch_copy = dict(ch)
                    flat = flat_by_chapter.get(int(ch_copy["id"]), [])
                    ch_copy["scenes"] = self._build_scene_tree(flat)
                    ch_copy["scenes_flat"] = self._flatten_scene_tree(ch_copy["scenes"])
                    self._attach_child_chapters_to_scenes(
                        ch_copy["scenes"], by_parent_scene, flat_by_chapter
                    )
                    nested_payloads.append(ch_copy)
                node["child_chapters"] = nested_payloads
                walk(node.get("children") or [])

        walk(scene_roots)

    def _mirror_project_folders(
        self, connection: sqlite3.Connection, project_id: int | None
    ) -> None:
        """Dual-write: rebuild folder tree + scene.folder_id from part/chapter for a project."""
        if project_id is None:
            return
        try:
            folder_tree.sync_project_folder_tree(connection, int(project_id))
        except sqlite3.OperationalError:
            # Schema not migrated yet (pre-028) — ignore
            pass

    def project_outline(self, project_id: int) -> dict:
        """Binder outline.

        Prefer folder table when fully synced. Force folder path when the tree
        is deeper than part→chapter (depth > 2) or no longer legacy-shaped
        after reparent — never fall back to legacy in those cases.
        """
        with database() as connection:
            self.require_project(connection, project_id)
            if not folder_tree.folder_table_ready(connection):
                return self._project_outline_from_legacy(connection, project_id)
            max_depth = folder_tree.max_folder_depth(connection, project_id)
            legacy_ok = folder_tree.project_folder_tree_is_legacy_compatible(
                connection, project_id
            )
            mapping_ok = folder_tree.project_folder_mapping_complete(
                connection, project_id
            )
            # depth > 2 or non-legacy shape → folder only (legacy would misrepresent)
            force_folder = max_depth > 2 or not legacy_ok
            if force_folder or mapping_ok:
                return self._project_outline_from_folder(connection, project_id)
            return self._project_outline_from_legacy(connection, project_id)

    def reparent_folder(self, folder_id: int, body: dict) -> dict:
        """POST /api/folders/{id}/reparent — move folder among unlimited-depth tree.

        Body: {
          new_parent_id: number | null,  # null = root; omit to derive from target
          position: "before" | "after" | "inside" | "index",
          target_id: number,            # required for before/after; for inside = parent
          index: number                 # required when position is "index" (undo)
        }
        """
        if not isinstance(body, dict):
            raise ValueError("요청 본문이 올바르지 않습니다.")
        position = str(body.get("position") or "inside").strip().lower()

        new_parent_id_provided = "new_parent_id" in body
        raw_parent = body.get("new_parent_id", None)
        if raw_parent is None or raw_parent == "" or raw_parent is False:
            new_parent_id = None
        else:
            try:
                new_parent_id = int(raw_parent)
            except (TypeError, ValueError) as error:
                raise ValueError("new_parent_id가 올바르지 않습니다.") from error

        raw_target = body.get("target_id", None)
        target_id = None
        if raw_target is not None and raw_target != "":
            try:
                target_id = int(raw_target)
            except (TypeError, ValueError) as error:
                raise ValueError("target_id가 올바르지 않습니다.") from error

        index_val = None
        if "index" in body and body.get("index") is not None and body.get("index") != "":
            try:
                index_val = int(body.get("index"))
            except (TypeError, ValueError) as error:
                raise ValueError("index가 올바르지 않습니다.") from error

        with database() as connection:
            try:
                result = folder_tree.reparent_folder(
                    connection,
                    int(folder_id),
                    new_parent_id=new_parent_id,
                    position=position,
                    target_id=target_id,
                    new_parent_id_provided=new_parent_id_provided,
                    index=index_val,
                )
            except sqlite3.OperationalError as error:
                raise ValueError(f"폴더를 옮길 수 없습니다: {error}") from error

            # U2: log successful moves for undo (skip pure no-ops)
            if result.get("moved") and folder_tree.action_log_table_ready(connection):
                title_row = connection.execute(
                    "SELECT title FROM folder WHERE id = ?",
                    (int(folder_id),),
                ).fetchone()
                title = ""
                if title_row is not None:
                    title = str(
                        title_row["title"]
                        if hasattr(title_row, "keys")
                        else title_row[0]
                        or ""
                    )
                short = title if len(title) <= 24 else title[:23] + "…"
                old_parent = result.get("old_parent_id")
                new_parent = result.get("parent_id")
                same_parent = old_parent == new_parent
                action_label = (
                    f"「{short or '폴더'}」 위치 변경"
                    if same_parent
                    else f"「{short or '폴더'}」 이동"
                )
                project_id = int(result.get("project_id") or 0)
                if project_id:
                    folder_tree.append_folder_action_log(
                        connection,
                        project_id,
                        "folder.reparent",
                        action_label,
                        folder_tree.build_reparent_action_payload(
                            folder_id=int(folder_id),
                            old_parent_id=result.get("old_parent_id"),
                            old_sort_order=int(result.get("old_sort_order") or 0),
                            old_index=int(result.get("old_index") or 0),
                            old_sibling_ids=list(result.get("old_sibling_ids") or []),
                            new_parent_id=result.get("parent_id"),
                            new_sort_order=int(result.get("sort_order") or 0),
                            new_position=str(result.get("position") or position),
                        ),
                    )
            # drop internal-only snapshot fields from HTTP response
            for key in (
                "old_parent_id",
                "old_sort_order",
                "old_index",
                "old_sibling_ids",
            ):
                result.pop(key, None)
            return result

    def save_folder(self, folder_id: int, body: dict) -> dict:
        """PUT /api/folders/{id} — partial update of color, is_pinned, is_box, is_bookmarked.

        Body may include:
          color: preset key | null | "" (clear)
          is_pinned: bool | 0 | 1
          is_box: bool | 0 | 1  (visual shell only; independent of parent/child)
          is_bookmarked: bool | 0 | 1  (binder display only; no sort effect)
        Does not change sort_order (pin only affects display ORDER BY).
        """
        if not isinstance(body, dict):
            raise ValueError("요청 본문이 올바르지 않습니다.")
        if not body:
            raise ValueError("수정할 항목이 없습니다.")

        updates: list[str] = []
        params: list[object] = []

        if "color" in body:
            raw_color = body.get("color")
            if raw_color is None or raw_color == "" or raw_color is False:
                color_val = None
            else:
                color_val = str(raw_color).strip().lower()
                if color_val not in FOLDER_COLORS:
                    raise ValueError(
                        "폴더 색이 올바르지 않습니다. "
                        f"({', '.join(sorted(FOLDER_COLORS))} 또는 없음)"
                    )
            updates.append("color = ?")
            params.append(color_val)

        if "is_pinned" in body:
            raw_pin = body.get("is_pinned")
            if isinstance(raw_pin, str):
                pin_val = 1 if raw_pin.strip().lower() in ("1", "true", "yes", "on") else 0
            else:
                pin_val = 1 if raw_pin else 0
            updates.append("is_pinned = ?")
            params.append(pin_val)

        if "is_box" in body:
            raw_box = body.get("is_box")
            if isinstance(raw_box, str):
                box_val = 1 if raw_box.strip().lower() in ("1", "true", "yes", "on") else 0
            else:
                box_val = 1 if raw_box else 0
            updates.append("is_box = ?")
            params.append(box_val)

        if "is_bookmarked" in body:
            raw_bm = body.get("is_bookmarked")
            if isinstance(raw_bm, str):
                bm_val = 1 if raw_bm.strip().lower() in ("1", "true", "yes", "on") else 0
            else:
                bm_val = 1 if raw_bm else 0
            updates.append("is_bookmarked = ?")
            params.append(bm_val)

        if not updates:
            raise ValueError(
                "수정할 항목이 없습니다. "
                "(color, is_pinned, is_box 또는 is_bookmarked)"
            )

        updates.append("updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')")
        updates.append("row_version = row_version + 1")
        params.append(int(folder_id))

        # Track field changes for undo (U1)
        log_entries: list[tuple[str, str, dict]] = []

        with database() as connection:
            try:
                row = connection.execute(
                    """
                    SELECT id, project_id, color, is_pinned, is_box, is_bookmarked, title
                    FROM folder
                    WHERE id = ? AND deleted_at IS NULL
                    """,
                    (int(folder_id),),
                ).fetchone()
            except sqlite3.OperationalError as error:
                # Pre-029/031 columns, or missing folder table
                try:
                    row = connection.execute(
                        """
                        SELECT id, project_id, color, is_pinned, is_box, title
                        FROM folder
                        WHERE id = ? AND deleted_at IS NULL
                        """,
                        (int(folder_id),),
                    ).fetchone()
                except sqlite3.OperationalError:
                    try:
                        row = connection.execute(
                            """
                            SELECT id, project_id, is_box, title
                            FROM folder
                            WHERE id = ? AND deleted_at IS NULL
                            """,
                            (int(folder_id),),
                        ).fetchone()
                    except sqlite3.OperationalError as err2:
                        raise ValueError(
                            "폴더 기능을 쓸 수 없습니다. "
                            "앱을 재시작해 마이그레이션을 적용해 주세요."
                        ) from err2
                if "is_bookmarked" in body:
                    raise ValueError(
                        "폴더 북마크 기능이 아직 준비되지 않았습니다. "
                        "앱을 재시작해 마이그레이션을 적용해 주세요."
                    ) from error
                if "color" in body or "is_pinned" in body:
                    raise ValueError(
                        "폴더 색/고정 기능이 아직 준비되지 않았습니다. "
                        "앱을 재시작해 마이그레이션을 적용해 주세요."
                    ) from error
            if row is None:
                raise ValueError("폴더를 찾을 수 없습니다.")

            project_id = int(row["project_id"])
            title_for_label = str(row["title"] or "폴더")
            short = (
                title_for_label
                if len(title_for_label) <= 24
                else title_for_label[:23] + "…"
            )

            def _norm_color(v):
                if v is None or v == "":
                    return None
                return str(v).strip().lower() or None

            if "color" in body:
                try:
                    old_color = _norm_color(row["color"])
                except (KeyError, IndexError, TypeError):
                    old_color = None
                raw_color = body.get("color")
                if raw_color is None or raw_color == "" or raw_color is False:
                    new_color = None
                else:
                    new_color = str(raw_color).strip().lower()
                if old_color != new_color:
                    log_entries.append(
                        (
                            "folder.color",
                            f"「{short}」 색 변경",
                            folder_tree.build_patch_action_payload(
                                int(folder_id), "color", old_color, new_color
                            ),
                        )
                    )

            if "is_pinned" in body:
                try:
                    old_pin = 1 if int(row["is_pinned"] or 0) else 0
                except (KeyError, IndexError, TypeError):
                    old_pin = 0
                raw_pin = body.get("is_pinned")
                if isinstance(raw_pin, str):
                    new_pin = 1 if raw_pin.strip().lower() in ("1", "true", "yes", "on") else 0
                else:
                    new_pin = 1 if raw_pin else 0
                if old_pin != new_pin:
                    log_entries.append(
                        (
                            "folder.pin",
                            f"「{short}」 " + ("상단 고정" if new_pin else "고정 해제"),
                            folder_tree.build_patch_action_payload(
                                int(folder_id), "is_pinned", old_pin, new_pin
                            ),
                        )
                    )

            if "is_box" in body:
                try:
                    old_box = 1 if int(row["is_box"] or 0) else 0
                except (KeyError, IndexError, TypeError):
                    old_box = 0
                raw_box = body.get("is_box")
                if isinstance(raw_box, str):
                    new_box = 1 if raw_box.strip().lower() in ("1", "true", "yes", "on") else 0
                else:
                    new_box = 1 if raw_box else 0
                if old_box != new_box:
                    log_entries.append(
                        (
                            "folder.box",
                            f"「{short}」 " + ("박스로 묶기" if new_box else "박스 해제"),
                            folder_tree.build_patch_action_payload(
                                int(folder_id), "is_box", old_box, new_box
                            ),
                        )
                    )

            if "is_bookmarked" in body:
                try:
                    old_bm = 1 if int(row["is_bookmarked"] or 0) else 0
                except (KeyError, IndexError, TypeError):
                    old_bm = 0
                raw_bm = body.get("is_bookmarked")
                if isinstance(raw_bm, str):
                    new_bm = 1 if raw_bm.strip().lower() in ("1", "true", "yes", "on") else 0
                else:
                    new_bm = 1 if raw_bm else 0
                if old_bm != new_bm:
                    log_entries.append(
                        (
                            "folder.bookmark",
                            f"「{short}」 " + ("북마크" if new_bm else "북마크 해제"),
                            folder_tree.build_patch_action_payload(
                                int(folder_id), "is_bookmarked", old_bm, new_bm
                            ),
                        )
                    )

            try:
                connection.execute(
                    f"UPDATE folder SET {', '.join(updates)} "
                    "WHERE id = ? AND deleted_at IS NULL",
                    params,
                )
            except sqlite3.OperationalError as error:
                raise ValueError(f"폴더를 저장할 수 없습니다: {error}") from error

            for type_, label, payload in log_entries:
                folder_tree.append_folder_action_log(
                    connection, project_id, type_, label, payload
                )

            try:
                out = connection.execute(
                    """
                    SELECT id, project_id, parent_id, title, is_box, sort_order,
                           color, is_pinned, is_bookmarked, source_kind, source_id
                    FROM folder WHERE id = ?
                    """,
                    (int(folder_id),),
                ).fetchone()
            except sqlite3.OperationalError:
                try:
                    out = connection.execute(
                        """
                        SELECT id, project_id, parent_id, title, is_box, sort_order,
                               color, is_pinned, source_kind, source_id
                        FROM folder WHERE id = ?
                        """,
                        (int(folder_id),),
                    ).fetchone()
                except sqlite3.OperationalError:
                    out = connection.execute(
                        """
                        SELECT id, project_id, parent_id, title, is_box, sort_order,
                               source_kind, source_id
                        FROM folder WHERE id = ?
                        """,
                        (int(folder_id),),
                    ).fetchone()

        data = as_dict(out) or {}
        if "color" not in data:
            data["color"] = None
        if "is_pinned" not in data:
            data["is_pinned"] = 0
        else:
            data["is_pinned"] = int(data.get("is_pinned") or 0)
        if "is_bookmarked" not in data:
            data["is_bookmarked"] = 0
        else:
            data["is_bookmarked"] = int(data.get("is_bookmarked") or 0)
        data["is_box"] = 1 if int(data.get("is_box") or 0) else 0
        if data.get("color") == "":
            data["color"] = None
        data["ok"] = True
        return data

    def project_undo_status(self, project_id: int) -> dict:
        """GET /api/projects/{id}/undo-status — undo/redo availability for binder history."""
        with database() as connection:
            self.require_project(connection, project_id)
            empty = {
                "can_undo": False,
                "can_redo": False,
                "label_ko": None,
                "redo_label_ko": None,
                "type": None,
                "redo_type": None,
            }
            if not folder_tree.action_log_table_ready(connection):
                return empty
            top = folder_tree.fetch_undo_stack_top(connection, project_id)
            redo_top = folder_tree.fetch_redo_stack_top(connection, project_id)
            out = dict(empty)
            if top is not None:
                out["can_undo"] = True
                out["label_ko"] = str(
                    top["label_ko"] if hasattr(top, "keys") else top[4] or ""
                ) or None
                out["type"] = str(
                    top["type"] if hasattr(top, "keys") else top[3] or ""
                ) or None
            if redo_top is not None:
                out["can_redo"] = True
                out["redo_label_ko"] = str(
                    redo_top["label_ko"] if hasattr(redo_top, "keys") else redo_top[4] or ""
                ) or None
                out["redo_type"] = str(
                    redo_top["type"] if hasattr(redo_top, "keys") else redo_top[3] or ""
                ) or None
            return out

    def undo_project_folder_action(self, project_id: int, body: dict | None = None) -> dict:
        """POST /api/projects/{id}/undo — pop one U1/U2/U3 folder action and reverse it.

        U1 collision: current != forward.new and != forward.old → skip entry.
        U2 reparent: parent+index must still match forward (new) state.
        Skip marks are committed before raising so they are not rolled back.
        """
        _ = body  # reserved for future options
        # Raise only after DB transaction commits so "skip" marks are not rolled back.
        pending_error: str | None = None
        result: dict | None = None

        with database() as connection:
            self.require_project(connection, project_id)
            if not folder_tree.action_log_table_ready(connection):
                raise ValueError(
                    "되돌리기 기능이 아직 준비되지 않았습니다. "
                    "앱을 재시작해 주세요."
                )
            top = folder_tree.fetch_undo_stack_top(connection, project_id)
            if top is None:
                raise ValueError("되돌릴 작업이 없어요.")

            log_id = int(top["id"] if hasattr(top, "keys") else top[0])
            type_ = str(top["type"] if hasattr(top, "keys") else top[3])
            label_ko = str(top["label_ko"] if hasattr(top, "keys") else top[4] or "")
            raw_payload = top["payload_json"] if hasattr(top, "keys") else top[5]
            try:
                payload = json.loads(raw_payload)
            except (TypeError, ValueError):
                folder_tree.mark_action_log_undone(connection, log_id)
                pending_error = "되돌리기 기록이 손상되어 건너뛰었어요."
                payload = None

            if payload is not None and type_ not in folder_tree.UNDOABLE_ACTION_TYPES:
                folder_tree.mark_action_log_undone(connection, log_id)
                pending_error = "이 작업은 아직 되돌릴 수 없어요."
                payload = None

            if payload is not None and type_ == "folder.create":
                result, pending_error = self._undo_folder_create_action(
                    connection,
                    log_id=log_id,
                    type_=type_,
                    label_ko=label_ko,
                    payload=payload,
                )
            elif payload is not None and type_ == "folder.trash":
                result, pending_error = self._undo_folder_trash_action(
                    connection,
                    project_id=project_id,
                    log_id=log_id,
                    type_=type_,
                    label_ko=label_ko,
                    payload=payload,
                )
            elif payload is not None and type_ == "folder.renumber_titles":
                result, pending_error = self._undo_folder_renumber_titles_action(
                    connection,
                    project_id=project_id,
                    log_id=log_id,
                    type_=type_,
                    label_ko=label_ko,
                    payload=payload,
                )
            elif payload is not None and type_ in folder_tree.U2_ACTION_TYPES:
                result, pending_error = self._undo_folder_reparent_action(
                    connection,
                    project_id=project_id,
                    log_id=log_id,
                    type_=type_,
                    label_ko=label_ko,
                    payload=payload,
                )
            elif payload is not None:
                result, pending_error = self._undo_folder_patch_action(
                    connection,
                    log_id=log_id,
                    type_=type_,
                    label_ko=label_ko,
                    payload=payload,
                )

        if pending_error:
            raise ValueError(pending_error)
        if result is None:
            raise ValueError("되돌리기에 실패했어요.")
        return result

    def redo_project_folder_action(self, project_id: int, body: dict | None = None) -> dict:
        """POST /api/projects/{id}/redo — re-apply the most recently undone action."""
        _ = body
        pending_error: str | None = None
        result: dict | None = None

        with database() as connection:
            self.require_project(connection, project_id)
            if not folder_tree.action_log_table_ready(connection):
                raise ValueError(
                    "되돌리기 기능이 아직 준비되지 않았습니다. "
                    "앱을 재시작해 주세요."
                )
            top = folder_tree.fetch_redo_stack_top(connection, project_id)
            if top is None:
                raise ValueError("다시 실행할 작업이 없어요.")

            log_id = int(top["id"] if hasattr(top, "keys") else top[0])
            type_ = str(top["type"] if hasattr(top, "keys") else top[3])
            label_ko = str(top["label_ko"] if hasattr(top, "keys") else top[4] or "")
            raw_payload = top["payload_json"] if hasattr(top, "keys") else top[5]
            try:
                payload = json.loads(raw_payload)
            except (TypeError, ValueError):
                folder_tree.delete_action_log(connection, log_id)
                pending_error = "다시 실행 기록이 손상되어 건너뛰었어요."
                payload = None

            if payload is not None and type_ not in folder_tree.UNDOABLE_ACTION_TYPES:
                folder_tree.delete_action_log(connection, log_id)
                pending_error = "이 작업은 다시 실행할 수 없어요."
                payload = None

            if payload is not None and type_ == "folder.create":
                result, pending_error = self._redo_folder_create_action(
                    connection, log_id=log_id, type_=type_, label_ko=label_ko, payload=payload
                )
            elif payload is not None and type_ == "folder.trash":
                result, pending_error = self._redo_folder_trash_action(
                    connection,
                    project_id=project_id,
                    log_id=log_id,
                    type_=type_,
                    label_ko=label_ko,
                    payload=payload,
                )
            elif payload is not None and type_ == "folder.renumber_titles":
                result, pending_error = self._redo_folder_renumber_titles_action(
                    connection,
                    project_id=project_id,
                    log_id=log_id,
                    type_=type_,
                    label_ko=label_ko,
                    payload=payload,
                )
            elif payload is not None and type_ in folder_tree.U2_ACTION_TYPES:
                result, pending_error = self._redo_folder_reparent_action(
                    connection,
                    project_id=project_id,
                    log_id=log_id,
                    type_=type_,
                    label_ko=label_ko,
                    payload=payload,
                )
            elif payload is not None:
                result, pending_error = self._redo_folder_patch_action(
                    connection,
                    log_id=log_id,
                    type_=type_,
                    label_ko=label_ko,
                    payload=payload,
                )

        if pending_error:
            raise ValueError(pending_error)
        if result is None:
            raise ValueError("다시 실행에 실패했어요.")
        return result

    def _apply_chapter_title_bulk(
        self,
        connection: sqlite3.Connection,
        project_id: int,
        items: list[dict],
        *,
        title_key: str,
    ) -> None:
        """Set chapter (+ mapped folder) titles from items[title_key]."""
        now_sql = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
        for raw in items:
            chapter_id = int(raw["chapter_id"])
            title = str(raw.get(title_key) or "")
            connection.execute(
                """
                UPDATE chapter SET title = ?
                WHERE id = ? AND project_id = ? AND deleted_at IS NULL
                """,
                (title, chapter_id, project_id),
            )
            folder_id = raw.get("folder_id")
            if folder_id is None:
                try:
                    folder_id = folder_tree.folder_id_for_source(
                        connection, project_id, "chapter", chapter_id
                    )
                except sqlite3.OperationalError:
                    folder_id = None
            if folder_id is not None:
                connection.execute(
                    f"""
                    UPDATE folder
                    SET title = ?,
                        updated_at = {now_sql},
                        row_version = row_version + 1
                    WHERE id = ? AND project_id = ? AND deleted_at IS NULL
                    """,
                    (title, int(folder_id), project_id),
                )

    def _undo_folder_renumber_titles_action(
        self,
        connection: sqlite3.Connection,
        *,
        project_id: int,
        log_id: int,
        type_: str,
        label_ko: str,
        payload: dict,
    ) -> tuple[dict | None, str | None]:
        """Strict bulk restore of chapter titles after renumber."""
        forward = payload.get("forward") or {}
        reverse = payload.get("reverse") or {}
        fwd_items = list(forward.get("items") or [])
        rev_items = list(reverse.get("items") or [])
        if not fwd_items or not rev_items:
            folder_tree.mark_action_log_undone(connection, log_id)
            return None, "되돌리기 기록이 올바르지 않아 건너뛰었어요."

        # Strict: every chapter must still show forward.new (or already old)
        all_old = True
        for it in fwd_items:
            cid = int(it["chapter_id"])
            old = str(it.get("old") or "")
            new = str(it.get("new") or "")
            row = connection.execute(
                "SELECT title, deleted_at FROM chapter WHERE id = ? AND project_id = ?",
                (cid, project_id),
            ).fetchone()
            if row is None or (
                (row["deleted_at"] if hasattr(row, "keys") else row[1]) is not None
            ):
                folder_tree.mark_action_log_undone(connection, log_id)
                return None, "그 사이 폴더가 바뀌어 되돌릴 수 없어요."
            current = str(
                (row["title"] if hasattr(row, "keys") else row[0]) or ""
            )
            if current == old:
                continue
            all_old = False
            if current != new:
                folder_tree.mark_action_log_undone(connection, log_id)
                return None, "그 사이 폴더가 바뀌어 되돌릴 수 없어요."

        if all_old:
            folder_tree.mark_action_log_undone(connection, log_id)
            return {
                "ok": True,
                "label_ko": label_ko,
                "type": type_,
                "folder_id": 0,
                "noop": True,
            }, None

        try:
            self._apply_chapter_title_bulk(
                connection, project_id, rev_items, title_key="title"
            )
        except sqlite3.Error as error:
            folder_tree.mark_action_log_undone(connection, log_id)
            return None, f"되돌리기에 실패했어요: {error}"

        folder_tree.mark_action_log_undone(connection, log_id)
        return {
            "ok": True,
            "label_ko": label_ko,
            "type": type_,
            "folder_id": 0,
            "noop": False,
            "count": len(rev_items),
        }, None

    def _redo_folder_renumber_titles_action(
        self,
        connection: sqlite3.Connection,
        *,
        project_id: int,
        log_id: int,
        type_: str,
        label_ko: str,
        payload: dict,
    ) -> tuple[dict | None, str | None]:
        """Strict bulk re-apply renumbered titles."""
        forward = payload.get("forward") or {}
        fwd_items = list(forward.get("items") or [])
        if not fwd_items:
            folder_tree.delete_action_log(connection, log_id)
            return None, "다시 실행 기록이 올바르지 않아 건너뛰었어요."

        all_new = True
        for it in fwd_items:
            cid = int(it["chapter_id"])
            old = str(it.get("old") or "")
            new = str(it.get("new") or "")
            row = connection.execute(
                "SELECT title, deleted_at FROM chapter WHERE id = ? AND project_id = ?",
                (cid, project_id),
            ).fetchone()
            if row is None or (
                (row["deleted_at"] if hasattr(row, "keys") else row[1]) is not None
            ):
                folder_tree.delete_action_log(connection, log_id)
                return None, "그 사이 폴더가 바뀌어 다시 실행할 수 없어요."
            current = str(
                (row["title"] if hasattr(row, "keys") else row[0]) or ""
            )
            if current == new:
                continue
            all_new = False
            if current != old:
                folder_tree.delete_action_log(connection, log_id)
                return None, "그 사이 폴더가 바뀌어 다시 실행할 수 없어요."

        if all_new:
            folder_tree.clear_action_log_undone(connection, log_id)
            return {
                "ok": True,
                "label_ko": label_ko,
                "type": type_,
                "folder_id": 0,
                "noop": True,
            }, None

        try:
            self._apply_chapter_title_bulk(
                connection, project_id, fwd_items, title_key="new"
            )
        except sqlite3.Error as error:
            folder_tree.delete_action_log(connection, log_id)
            return None, f"다시 실행에 실패했어요: {error}"

        folder_tree.clear_action_log_undone(connection, log_id)
        return {
            "ok": True,
            "label_ko": label_ko,
            "type": type_,
            "folder_id": 0,
            "noop": False,
            "count": len(fwd_items),
        }, None

    def _redo_folder_patch_action(
        self,
        connection: sqlite3.Connection,
        *,
        log_id: int,
        type_: str,
        label_ko: str,
        payload: dict,
    ) -> tuple[dict | None, str | None]:
        """Re-apply U1 patch forward.new."""
        folder_id = int(payload.get("folder_id") or 0)
        forward = payload.get("forward") or {}
        field = str(forward.get("field") or "")
        old_val = forward.get("old")
        new_val = forward.get("new")
        if not folder_id or not field:
            folder_tree.delete_action_log(connection, log_id)
            return None, "다시 실행 기록이 올바르지 않아 건너뛰었어요."

        try:
            row = connection.execute(
                """
                SELECT id, project_id, title, color, is_box, is_pinned, is_bookmarked, deleted_at
                FROM folder WHERE id = ?
                """,
                (folder_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            try:
                row = connection.execute(
                    """
                    SELECT id, project_id, title, color, is_box, is_pinned, deleted_at
                    FROM folder WHERE id = ?
                    """,
                    (folder_id,),
                ).fetchone()
            except sqlite3.OperationalError:
                row = connection.execute(
                    "SELECT id, project_id, title, is_box, deleted_at FROM folder WHERE id = ?",
                    (folder_id,),
                ).fetchone()

        if row is None or (
            hasattr(row, "keys") and row["deleted_at"] is not None
        ):
            folder_tree.delete_action_log(connection, log_id)
            return None, "그 사이 폴더가 바뀌어 다시 실행할 수 없어요."

        def _get(r, key, default=None):
            try:
                if hasattr(r, "keys"):
                    return r[key] if key in r.keys() else default
                return default
            except Exception:
                return default

        def _norm_field(name, val):
            if name == "color":
                if val is None or val == "":
                    return None
                return str(val).strip().lower() or None
            if name in ("is_box", "is_pinned", "is_bookmarked"):
                return 1 if val else 0
            if name == "title":
                return str(val or "")
            return val

        if field == "title":
            current = _norm_field("title", _get(row, "title", ""))
        elif field == "color":
            current = _norm_field("color", _get(row, "color", None))
        elif field == "is_box":
            current = _norm_field("is_box", _get(row, "is_box", 0))
        elif field == "is_pinned":
            current = _norm_field("is_pinned", _get(row, "is_pinned", 0))
        elif field == "is_bookmarked":
            current = _norm_field("is_bookmarked", _get(row, "is_bookmarked", 0))
        else:
            folder_tree.delete_action_log(connection, log_id)
            return None, "이 작업은 다시 실행할 수 없어요."

        old_n = _norm_field(field, old_val)
        new_n = _norm_field(field, new_val)

        if current == new_n:
            folder_tree.clear_action_log_undone(connection, log_id)
            return {
                "ok": True,
                "label_ko": label_ko,
                "type": type_,
                "folder_id": folder_id,
                "noop": True,
            }, None
        if current != old_n:
            folder_tree.delete_action_log(connection, log_id)
            return None, "그 사이 폴더가 바뀌어 다시 실행할 수 없어요."

        col_map = {
            "title": "title",
            "color": "color",
            "is_box": "is_box",
            "is_pinned": "is_pinned",
            "is_bookmarked": "is_bookmarked",
        }
        col = col_map[field]
        try:
            connection.execute(
                f"""
                UPDATE folder
                SET {col} = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    row_version = row_version + 1
                WHERE id = ? AND deleted_at IS NULL
                """,
                (new_n, folder_id),
            )
        except sqlite3.OperationalError as error:
            folder_tree.delete_action_log(connection, log_id)
            return None, f"다시 실행에 실패했어요: {error}"

        if field == "title":
            src = connection.execute(
                "SELECT source_kind, source_id FROM folder WHERE id = ?",
                (folder_id,),
            ).fetchone()
            if src is not None:
                sk = src["source_kind"] if hasattr(src, "keys") else src[0]
                sid = src["source_id"] if hasattr(src, "keys") else src[1]
                if sk == "chapter" and sid is not None:
                    connection.execute(
                        "UPDATE chapter SET title = ? WHERE id = ? AND deleted_at IS NULL",
                        (new_n, int(sid)),
                    )
                elif sk == "part" and sid is not None:
                    connection.execute(
                        "UPDATE part SET title = ?, "
                        "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), "
                        "row_version = row_version + 1 "
                        "WHERE id = ? AND deleted_at IS NULL",
                        (new_n, int(sid)),
                    )

        folder_tree.clear_action_log_undone(connection, log_id)
        return {
            "ok": True,
            "label_ko": label_ko,
            "type": type_,
            "folder_id": folder_id,
            "noop": False,
        }, None

    def _redo_folder_reparent_action(
        self,
        connection: sqlite3.Connection,
        *,
        project_id: int,
        log_id: int,
        type_: str,
        label_ko: str,
        payload: dict,
    ) -> tuple[dict | None, str | None]:
        folder_id = int(payload.get("folder_id") or 0)
        forward = payload.get("forward") or {}
        if not folder_id:
            folder_tree.delete_action_log(connection, log_id)
            return None, "다시 실행 기록이 올바르지 않아 건너뛰었어요."

        old_parent_id = folder_tree._norm_parent_id(forward.get("old_parent_id"))
        new_parent_id = folder_tree._norm_parent_id(forward.get("new_parent_id"))
        try:
            old_index = int(forward.get("old_index", forward.get("old_sort_order", 0)))
        except (TypeError, ValueError):
            old_index = 0
        try:
            new_sort_order = int(forward.get("new_sort_order", 0))
        except (TypeError, ValueError):
            new_sort_order = 0

        row = folder_tree._load_active_folder(connection, folder_id)
        if row is None:
            folder_tree.delete_action_log(connection, log_id)
            return None, "그 사이 폴더가 바뀌어 다시 실행할 수 없어요."
        if int(row["project_id"]) != int(project_id):
            folder_tree.delete_action_log(connection, log_id)
            return None, "그 사이 폴더가 바뀌어 다시 실행할 수 없어요."

        raw_cur = row["parent_id"]
        cur_parent = int(raw_cur) if raw_cur is not None else None
        cur_index = folder_tree.folder_sibling_index(
            connection, project_id, folder_id, cur_parent
        )
        if cur_index is None:
            folder_tree.delete_action_log(connection, log_id)
            return None, "그 사이 폴더가 바뀌어 다시 실행할 수 없어요."

        # Already at forward target
        if cur_parent == new_parent_id and cur_index == new_sort_order:
            folder_tree.clear_action_log_undone(connection, log_id)
            return {
                "ok": True,
                "label_ko": label_ko,
                "type": type_,
                "folder_id": folder_id,
                "noop": True,
            }, None

        # Expect undo state (old parent+index)
        if not (cur_parent == old_parent_id and cur_index == old_index):
            folder_tree.delete_action_log(connection, log_id)
            return None, "그 사이 폴더가 바뀌어 다시 실행할 수 없어요."

        try:
            restore = folder_tree.reparent_folder(
                connection,
                folder_id,
                new_parent_id=new_parent_id,
                position="index",
                new_parent_id_provided=True,
                index=new_sort_order,
            )
        except ValueError as error:
            folder_tree.delete_action_log(connection, log_id)
            return None, f"그 사이 폴더가 바뀌어 다시 실행할 수 없어요. ({error})"
        except sqlite3.Error as error:
            folder_tree.delete_action_log(connection, log_id)
            return None, f"다시 실행에 실패했어요: {error}"

        folder_tree.clear_action_log_undone(connection, log_id)
        return {
            "ok": True,
            "label_ko": label_ko,
            "type": type_,
            "folder_id": folder_id,
            "noop": False,
            "parent_id": restore.get("parent_id"),
            "sort_order": restore.get("sort_order"),
        }, None

    def _redo_folder_create_action(
        self,
        connection: sqlite3.Connection,
        *,
        log_id: int,
        type_: str,
        label_ko: str,
        payload: dict,
    ) -> tuple[dict | None, str | None]:
        """Redo create = restore soft-deleted empty folder (undo had trashed it)."""
        folder_id = int(payload.get("folder_id") or 0)
        if not folder_id:
            folder_tree.delete_action_log(connection, log_id)
            return None, "다시 실행 기록이 올바르지 않아 건너뛰었어요."

        row = connection.execute(
            """
            SELECT id, project_id, parent_id, sort_order, source_kind, source_id, deleted_at
            FROM folder WHERE id = ?
            """,
            (folder_id,),
        ).fetchone()
        if row is None:
            folder_tree.delete_action_log(connection, log_id)
            return None, "그 사이 폴더가 바뀌어 다시 실행할 수 없어요."

        deleted = row["deleted_at"] if hasattr(row, "keys") else row[6]
        project_id = int(row["project_id"] if hasattr(row, "keys") else row[1])
        parent_raw = row["parent_id"] if hasattr(row, "keys") else row[2]
        parent_id = int(parent_raw) if parent_raw is not None else None
        sk = row["source_kind"] if hasattr(row, "keys") else row[4]
        sid = row["source_id"] if hasattr(row, "keys") else row[5]
        now_sql = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"

        if deleted is None:
            # already active
            folder_tree.clear_action_log_undone(connection, log_id)
            return {
                "ok": True,
                "label_ko": label_ko,
                "type": type_,
                "folder_id": folder_id,
                "noop": True,
            }, None

        # Parent must be active
        if parent_id is not None:
            prow = connection.execute(
                "SELECT deleted_at FROM folder WHERE id = ?",
                (parent_id,),
            ).fetchone()
            if prow is None or (
                (prow["deleted_at"] if hasattr(prow, "keys") else prow[0]) is not None
            ):
                folder_tree.delete_action_log(connection, log_id)
                return None, "그 사이 폴더가 바뀌어 다시 실행할 수 없어요."

        # Restore with temp sort then recompact
        connection.execute(
            f"""
            UPDATE folder
            SET deleted_at = NULL,
                sort_order = ?,
                updated_at = {now_sql},
                row_version = row_version + 1
            WHERE id = ?
            """,
            (9_000_000 + folder_id, folder_id),
        )
        if sk == "part" and sid is not None:
            connection.execute(
                f"""
                UPDATE part SET deleted_at = NULL,
                    updated_at = {now_sql},
                    row_version = row_version + 1
                WHERE id = ? AND deleted_at IS NOT NULL
                """,
                (int(sid),),
            )
        elif sk == "chapter" and sid is not None:
            connection.execute(
                f"""
                UPDATE chapter SET deleted_at = NULL,
                    updated_at = {now_sql},
                    row_version = row_version + 1
                WHERE id = ? AND deleted_at IS NOT NULL
                """,
                (int(sid),),
            )
        folder_tree.recompact_folder_siblings(connection, project_id, parent_id)
        folder_tree.clear_action_log_undone(connection, log_id)
        return {
            "ok": True,
            "label_ko": label_ko,
            "type": type_,
            "folder_id": folder_id,
            "noop": False,
        }, None

    def _redo_folder_trash_action(
        self,
        connection: sqlite3.Connection,
        *,
        project_id: int,
        log_id: int,
        type_: str,
        label_ko: str,
        payload: dict,
    ) -> tuple[dict | None, str | None]:
        """Redo trash = soft-delete cascade again from snapshot ids."""
        reverse = payload.get("forward") or payload.get("reverse") or payload
        root_id = int(reverse.get("root_folder_id") or payload.get("folder_id") or 0)
        folder_ids = [int(x) for x in (reverse.get("folder_ids") or [])]
        part_ids = [int(x) for x in (reverse.get("part_ids") or [])]
        chapter_ids = [int(x) for x in (reverse.get("chapter_ids") or [])]
        scene_ids = [int(x) for x in (reverse.get("scene_ids") or [])]
        if not root_id:
            folder_tree.delete_action_log(connection, log_id)
            return None, "다시 실행 기록이 올바르지 않아 건너뛰었어요."
        if root_id not in folder_ids:
            folder_ids = [root_id] + [x for x in folder_ids if x != root_id]

        root = connection.execute(
            "SELECT id, project_id, deleted_at FROM folder WHERE id = ?",
            (root_id,),
        ).fetchone()
        if root is None:
            folder_tree.delete_action_log(connection, log_id)
            return None, "그 사이 폴더가 바뀌어 다시 실행할 수 없어요."
        if int(root["project_id"] if hasattr(root, "keys") else root[1]) != int(
            project_id
        ):
            folder_tree.delete_action_log(connection, log_id)
            return None, "그 사이 폴더가 바뀌어 다시 실행할 수 없어요."

        root_deleted = root["deleted_at"] if hasattr(root, "keys") else root[2]
        if root_deleted is not None:
            folder_tree.clear_action_log_undone(connection, log_id)
            return {
                "ok": True,
                "label_ko": label_ko,
                "type": type_,
                "folder_id": root_id,
                "noop": True,
            }, None

        now_sql = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
        try:
            folder_tree.soft_delete_folder_ids(connection, folder_ids)
            for pid in part_ids:
                connection.execute(
                    f"""
                    UPDATE part SET deleted_at = {now_sql},
                        updated_at = {now_sql},
                        row_version = row_version + 1
                    WHERE id = ? AND deleted_at IS NULL
                    """,
                    (int(pid),),
                )
            for cid in chapter_ids:
                connection.execute(
                    f"""
                    UPDATE chapter SET deleted_at = {now_sql},
                        updated_at = {now_sql},
                        row_version = row_version + 1
                    WHERE id = ? AND deleted_at IS NULL
                    """,
                    (int(cid),),
                )
            if scene_ids:
                ph = ",".join("?" * len(scene_ids))
                connection.execute(
                    f"""
                    UPDATE scene SET deleted_at = COALESCE(deleted_at, {now_sql}),
                        updated_at = {now_sql}
                    WHERE id IN ({ph}) AND deleted_at IS NULL
                    """,
                    scene_ids,
                )
        except sqlite3.Error as error:
            folder_tree.delete_action_log(connection, log_id)
            return None, f"다시 실행에 실패했어요: {error}"

        folder_tree.clear_action_log_undone(connection, log_id)
        return {
            "ok": True,
            "label_ko": label_ko,
            "type": type_,
            "folder_id": root_id,
            "noop": False,
        }, None

    def _log_folder_create(
        self,
        connection: sqlite3.Connection,
        *,
        project_id: int,
        folder_id: int | None,
        source_kind: str,
        source_id: int,
        title: str,
    ) -> None:
        """U3: append folder.create log for part/chapter creation."""
        if folder_id is None:
            try:
                folder_id = folder_tree.folder_id_for_source(
                    connection, project_id, source_kind, int(source_id)
                )
            except sqlite3.OperationalError:
                folder_id = None
        if folder_id is None or not folder_tree.action_log_table_ready(connection):
            return
        try:
            fr = connection.execute(
                "SELECT parent_id, sort_order FROM folder WHERE id = ?",
                (int(folder_id),),
            ).fetchone()
            parent_id = None
            so = 0
            if fr is not None:
                raw_p = fr["parent_id"] if hasattr(fr, "keys") else fr[0]
                parent_id = int(raw_p) if raw_p is not None else None
                so = int((fr["sort_order"] if hasattr(fr, "keys") else fr[1]) or 0)
            short = title if len(title) <= 24 else title[:23] + "…"
            folder_tree.append_folder_action_log(
                connection,
                project_id,
                "folder.create",
                f"「{short or '폴더'}」 생성",
                folder_tree.build_create_action_payload(
                    folder_id=int(folder_id),
                    source_kind=source_kind,
                    source_id=int(source_id),
                    parent_id=parent_id,
                    sort_order=so,
                ),
            )
        except sqlite3.OperationalError:
            pass

    def _undo_folder_create_action(
        self,
        connection: sqlite3.Connection,
        *,
        log_id: int,
        type_: str,
        label_ko: str,
        payload: dict,
    ) -> tuple[dict | None, str | None]:
        """Reverse folder.create → soft-delete one folder (children block keeps stack)."""
        folder_id = int(payload.get("folder_id") or 0)
        if not folder_id:
            folder_tree.mark_action_log_undone(connection, log_id)
            return None, "되돌리기 기록이 올바르지 않아 건너뛰었어요."

        # Missing folder row → permanent conflict, drop stack entry
        exists = connection.execute(
            "SELECT id, deleted_at FROM folder WHERE id = ?",
            (folder_id,),
        ).fetchone()
        if exists is None:
            folder_tree.mark_action_log_undone(connection, log_id)
            return None, "그 사이 폴더가 바뀌어 되돌릴 수 없어요."

        try:
            out = folder_tree.trash_one_created_folder(connection, folder_id)
        except ValueError as error:
            msg = str(error)
            # Children present: keep stack entry so user can retry
            if msg == folder_tree.CREATE_UNDO_BLOCKED_MSG or "하위 항목" in msg:
                return None, msg
            # Other permanent failures: skip
            folder_tree.mark_action_log_undone(connection, log_id)
            return None, msg

        folder_tree.mark_action_log_undone(connection, log_id)
        return {
            "ok": True,
            "label_ko": label_ko,
            "type": type_,
            "folder_id": folder_id,
            "skipped": False,
            "noop": bool(out.get("noop")),
        }, None

    def _undo_folder_trash_action(
        self,
        connection: sqlite3.Connection,
        *,
        project_id: int,
        log_id: int,
        type_: str,
        label_ko: str,
        payload: dict,
    ) -> tuple[dict | None, str | None]:
        """Reverse folder.trash → cascade restore from snapshot."""
        try:
            out = folder_tree.restore_folder_trash_snapshot(
                connection, project_id, payload
            )
        except ValueError as error:
            folder_tree.mark_action_log_undone(connection, log_id)
            return None, str(error)
        except sqlite3.Error as error:
            folder_tree.mark_action_log_undone(connection, log_id)
            return None, f"되돌리기에 실패했어요: {error}"

        folder_tree.mark_action_log_undone(connection, log_id)
        return {
            "ok": True,
            "label_ko": label_ko,
            "type": type_,
            "folder_id": int(out.get("folder_id") or payload.get("folder_id") or 0),
            "skipped": False,
            "noop": bool(out.get("noop")),
            "restored_folders": out.get("restored_folders"),
            "restored_scenes": out.get("restored_scenes"),
        }, None

    def _undo_folder_reparent_action(
        self,
        connection: sqlite3.Connection,
        *,
        project_id: int,
        log_id: int,
        type_: str,
        label_ko: str,
        payload: dict,
    ) -> tuple[dict | None, str | None]:
        """Reverse one folder.reparent log entry. Returns (result, pending_error)."""
        folder_id = int(payload.get("folder_id") or 0)
        forward = payload.get("forward") or {}
        reverse = payload.get("reverse") or {}
        if not folder_id:
            folder_tree.mark_action_log_undone(connection, log_id)
            return None, "되돌리기 기록이 올바르지 않아 건너뛰었어요."

        old_parent_id = folder_tree._norm_parent_id(
            reverse.get("parent_id", forward.get("old_parent_id"))
        )
        new_parent_id = folder_tree._norm_parent_id(forward.get("new_parent_id"))
        try:
            old_index = int(
                reverse.get("index", forward.get("old_index", forward.get("old_sort_order", 0)))
            )
        except (TypeError, ValueError):
            old_index = 0
        try:
            new_sort_order = int(forward.get("new_sort_order", -1))
        except (TypeError, ValueError):
            new_sort_order = -1

        row = folder_tree._load_active_folder(connection, folder_id)
        if row is None:
            folder_tree.mark_action_log_undone(connection, log_id)
            return None, "그 사이 폴더가 바뀌어 되돌릴 수 없어요."
        if int(row["project_id"]) != int(project_id):
            folder_tree.mark_action_log_undone(connection, log_id)
            return None, "그 사이 폴더가 바뀌어 되돌릴 수 없어요."

        raw_cur_parent = row["parent_id"]
        cur_parent = (
            int(raw_cur_parent) if raw_cur_parent is not None else None
        )
        cur_index = folder_tree.folder_sibling_index(
            connection, project_id, folder_id, cur_parent
        )
        if cur_index is None:
            folder_tree.mark_action_log_undone(connection, log_id)
            return None, "그 사이 폴더가 바뀌어 되돌릴 수 없어요."

        # Already at reverse target (parent + index)
        if cur_parent == old_parent_id and cur_index == old_index:
            folder_tree.mark_action_log_undone(connection, log_id)
            return {
                "ok": True,
                "label_ko": label_ko,
                "type": type_,
                "folder_id": folder_id,
                "skipped": False,
                "noop": True,
            }, None

        # Still in forward (post-move) state: same parent and same sibling index
        if cur_parent == new_parent_id and cur_index == new_sort_order:
            try:
                restore = folder_tree.reparent_folder(
                    connection,
                    folder_id,
                    new_parent_id=old_parent_id,
                    position="index",
                    new_parent_id_provided=True,
                    index=old_index,
                )
            except ValueError as error:
                folder_tree.mark_action_log_undone(connection, log_id)
                return None, f"그 사이 폴더가 바뀌어 되돌릴 수 없어요. ({error})"
            except sqlite3.OperationalError as error:
                folder_tree.mark_action_log_undone(connection, log_id)
                return None, f"되돌리기에 실패했어요: {error}"

            # Undo reparent must not leave a second log entry
            # (reparent_folder itself does not log — only the HTTP wrapper does)

            folder_tree.mark_action_log_undone(connection, log_id)
            return {
                "ok": True,
                "label_ko": label_ko,
                "type": type_,
                "folder_id": folder_id,
                "skipped": False,
                "noop": False,
                "parent_id": restore.get("parent_id"),
                "sort_order": restore.get("sort_order"),
            }, None

        # Parent/index no longer match forward state → conflict skip
        folder_tree.mark_action_log_undone(connection, log_id)
        return None, "그 사이 폴더가 바뀌어 되돌릴 수 없어요."

    def _undo_folder_patch_action(
        self,
        connection: sqlite3.Connection,
        *,
        log_id: int,
        type_: str,
        label_ko: str,
        payload: dict,
    ) -> tuple[dict | None, str | None]:
        """Reverse one U1 patch (rename/color/box/pin). Returns (result, pending_error)."""
        folder_id = int(payload.get("folder_id") or 0)
        forward = payload.get("forward") or {}
        reverse = payload.get("reverse") or {}
        field = str(forward.get("field") or "")
        old_val = forward.get("old")
        new_val = forward.get("new")
        reverse_fields = reverse.get("fields") or {}

        if not folder_id or not field:
            folder_tree.mark_action_log_undone(connection, log_id)
            return None, "되돌리기 기록이 올바르지 않아 건너뛰었어요."

        try:
            row = connection.execute(
                """
                SELECT id, project_id, title, color, is_box, is_pinned, is_bookmarked, deleted_at
                FROM folder WHERE id = ?
                """,
                (folder_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            try:
                row = connection.execute(
                    """
                    SELECT id, project_id, title, color, is_box, is_pinned, deleted_at
                    FROM folder WHERE id = ?
                    """,
                    (folder_id,),
                ).fetchone()
            except sqlite3.OperationalError:
                row = connection.execute(
                    "SELECT id, project_id, title, is_box, deleted_at "
                    "FROM folder WHERE id = ?",
                    (folder_id,),
                ).fetchone()

        if row is None or (
            hasattr(row, "keys") and row["deleted_at"] is not None
        ):
            folder_tree.mark_action_log_undone(connection, log_id)
            return None, "그 사이 폴더가 바뀌어 되돌릴 수 없어요."

        def _get(r, key, default=None):
            try:
                if hasattr(r, "keys"):
                    return r[key] if key in r.keys() else default
                return default
            except Exception:
                return default

        def _norm_field(name, val):
            if name == "color":
                if val is None or val == "":
                    return None
                return str(val).strip().lower() or None
            if name in ("is_box", "is_pinned", "is_bookmarked"):
                return 1 if val else 0
            if name == "title":
                return str(val or "")
            return val

        if field == "title":
            current = _norm_field("title", _get(row, "title", ""))
        elif field == "color":
            current = _norm_field("color", _get(row, "color", None))
        elif field == "is_box":
            current = _norm_field("is_box", _get(row, "is_box", 0))
        elif field == "is_pinned":
            current = _norm_field("is_pinned", _get(row, "is_pinned", 0))
        elif field == "is_bookmarked":
            current = _norm_field("is_bookmarked", _get(row, "is_bookmarked", 0))
        else:
            folder_tree.mark_action_log_undone(connection, log_id)
            return None, "이 작업은 아직 되돌릴 수 없어요."

        old_n = _norm_field(field, old_val)
        new_n = _norm_field(field, new_val)

        if current == old_n:
            folder_tree.mark_action_log_undone(connection, log_id)
            return {
                "ok": True,
                "label_ko": label_ko,
                "type": type_,
                "folder_id": folder_id,
                "skipped": False,
                "noop": True,
            }, None
        if current != new_n:
            folder_tree.mark_action_log_undone(connection, log_id)
            return None, "그 사이 폴더가 바뀌어 되돌릴 수 없어요."

        restore_val = reverse_fields.get(field, old_val)
        restore_val = _norm_field(field, restore_val)
        col_map = {
            "title": "title",
            "color": "color",
            "is_box": "is_box",
            "is_pinned": "is_pinned",
            "is_bookmarked": "is_bookmarked",
        }
        col = col_map[field]
        try:
            connection.execute(
                f"""
                UPDATE folder
                SET {col} = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                    row_version = row_version + 1
                WHERE id = ? AND deleted_at IS NULL
                """,
                (restore_val, folder_id),
            )
        except sqlite3.OperationalError as error:
            folder_tree.mark_action_log_undone(connection, log_id)
            return None, f"되돌리기에 실패했어요: {error}"

        if field == "title":
            src = connection.execute(
                "SELECT source_kind, source_id FROM folder WHERE id = ?",
                (folder_id,),
            ).fetchone()
            if src is not None:
                sk = src["source_kind"] if hasattr(src, "keys") else src[0]
                sid = src["source_id"] if hasattr(src, "keys") else src[1]
                if sk == "chapter" and sid is not None:
                    connection.execute(
                        "UPDATE chapter SET title = ? "
                        "WHERE id = ? AND deleted_at IS NULL",
                        (restore_val, int(sid)),
                    )
                elif sk == "part" and sid is not None:
                    connection.execute(
                        "UPDATE part SET title = ?, "
                        "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), "
                        "row_version = row_version + 1 "
                        "WHERE id = ? AND deleted_at IS NULL",
                        (restore_val, int(sid)),
                    )

        folder_tree.mark_action_log_undone(connection, log_id)
        return {
            "ok": True,
            "label_ko": label_ko,
            "type": type_,
            "folder_id": folder_id,
            "skipped": False,
            "noop": False,
        }, None

    def _project_meta_payload(self, project_row: sqlite3.Row | None) -> dict:
        project_data = as_dict(project_row) or {}
        if not project_data:
            return project_data
        project_data["synopsis_md"] = project_data.get("description_md") or ""
        project_data["logline_md"] = project_data.get("logline_md") or ""
        project_data["worldbuilding_md"] = project_data.get("worldbuilding_md") or ""
        project_data["intro_md"] = project_data.get("intro_md") or ""
        project_data["intent_md"] = project_data.get("intent_md") or ""
        project_data["tory_priority_md"] = project_data.get("tory_priority_md") or ""
        project_data["outline_summary"] = project_data.get("outline_summary") or ""
        project_data["main_genre"] = project_data.get("main_genre") or ""
        project_data["sub_genre"] = project_data.get("sub_genre") or ""
        project_data["keywords"] = parse_project_keywords(project_data.get("keywords"))
        project_data["goal_word_count"] = int(project_data.get("goal_word_count") or 0)
        try:
            link = project_data.get("linked_success_profile_id")
            project_data["linked_success_profile_id"] = (
                int(link) if link not in (None, "", 0, "0") else None
            )
        except (TypeError, ValueError):
            project_data["linked_success_profile_id"] = None
        return project_data

    def _load_outline_project_row(
        self, connection: sqlite3.Connection, project_id: int
    ) -> sqlite3.Row | None:
        try:
            return connection.execute(
                "SELECT title, purpose, main_genre, sub_genre, keywords, uuid, package_path, "
                "description_md, logline_md, worldbuilding_md, intro_md, intent_md, "
                "tory_priority_md, outline_summary, goal_word_count, linked_success_profile_id "
                "FROM project WHERE id = ?",
                (project_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return connection.execute(
                "SELECT title, purpose, main_genre, sub_genre, keywords, uuid, package_path, "
                "description_md, logline_md, worldbuilding_md, intro_md, intent_md, "
                "tory_priority_md, outline_summary, goal_word_count "
                "FROM project WHERE id = ?",
                (project_id,),
            ).fetchone()

    def _load_outline_scenes(
        self, connection: sqlite3.Connection, project_id: int
    ) -> list[sqlite3.Row]:
        # Prefer folder_id when migration 028+ is present (one query either way).
        try:
            return connection.execute(
                "SELECT s.id, s.chapter_id, s.folder_id, s.parent_scene_id, s.title, s.status, "
                "s.synopsis_md, s.sort_order, r.word_count, "
                "substr(r.content_md, 1, 1200) AS content_head "
                "FROM scene s JOIN scene_revision r ON r.scene_id = s.id AND r.is_current = 1 "
                "WHERE s.project_id = ? AND s.deleted_at IS NULL "
                "ORDER BY s.chapter_id, s.sort_order, s.id",
                (project_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            try:
                return connection.execute(
                    "SELECT s.id, s.chapter_id, s.parent_scene_id, s.title, s.status, "
                    "s.synopsis_md, s.sort_order, r.word_count, "
                    "substr(r.content_md, 1, 1200) AS content_head "
                    "FROM scene s JOIN scene_revision r ON r.scene_id = s.id AND r.is_current = 1 "
                    "WHERE s.project_id = ? AND s.deleted_at IS NULL "
                    "ORDER BY s.chapter_id, s.sort_order, s.id",
                    (project_id,),
                ).fetchall()
            except sqlite3.OperationalError:
                return connection.execute(
                    "SELECT s.id, s.chapter_id, s.title, s.status, "
                    "s.synopsis_md, s.sort_order, r.word_count, "
                    "substr(r.content_md, 1, 1200) AS content_head "
                    "FROM scene s JOIN scene_revision r ON r.scene_id = s.id AND r.is_current = 1 "
                    "WHERE s.project_id = ? AND s.deleted_at IS NULL "
                    "ORDER BY s.chapter_id, s.sort_order, s.id",
                    (project_id,),
                ).fetchall()

    def _assemble_outline_payload(
        self,
        *,
        project_data: dict,
        parts_rows: list[dict],
        chapters_rows: list[dict],
        scenes_rows: list[sqlite3.Row],
    ) -> dict:
        """Shared parts/chapters/scenes assembly (legacy-shaped)."""
        flat_by_chapter: dict[int, list[dict]] = {}
        untitled_titles = {"", "제목 없음", "새 씬", "새 하위 원고"}
        for scene in scenes_rows:
            data = as_dict(scene)
            if "parent_scene_id" not in data:
                data["parent_scene_id"] = None
            title = str(data.get("title") or "").strip()
            content_head = data.pop("content_head", None)
            if title in untitled_titles:
                data["body_preview"] = first_sentence_preview(content_head or "")
            else:
                data["body_preview"] = ""
            flat_by_chapter.setdefault(int(scene["chapter_id"]), []).append(data)

        by_parent_scene: dict[int, list[dict]] = {}
        top_level_chapters: list[dict] = []
        for chapter in chapters_rows:
            ch = dict(chapter)
            if "parent_scene_id" not in ch:
                ch["parent_scene_id"] = None
            flat = flat_by_chapter.get(int(ch["id"]), [])
            ch["scenes"] = self._build_scene_tree(flat)
            ch["scenes_flat"] = self._flatten_scene_tree(ch["scenes"])
            ch["transparent"] = import_hierarchy.is_transparent_chapter(
                ch.get("notes_md"), ch.get("title")
            )
            raw_parent = ch.get("parent_scene_id")
            if raw_parent is not None:
                by_parent_scene.setdefault(int(raw_parent), []).append(ch)
            else:
                top_level_chapters.append(ch)

        for group in by_parent_scene.values():
            group.sort(key=lambda c: (int(c.get("sort_order") or 0), int(c.get("id") or 0)))

        for ch in top_level_chapters:
            self._attach_child_chapters_to_scenes(
                ch["scenes"], by_parent_scene, flat_by_chapter
            )

        chapters_by_part: dict[int, list[dict]] = {}
        ungrouped: list[dict] = []
        for chapter in top_level_chapters:
            pid = chapter.get("part_id")
            if pid is None:
                ungrouped.append(chapter)
            else:
                chapters_by_part.setdefault(int(pid), []).append(chapter)
        for pid, group in chapters_by_part.items():
            group.sort(key=lambda c: (int(c.get("sort_order") or 0), int(c.get("id") or 0)))
        ungrouped.sort(key=lambda c: (int(c.get("sort_order") or 0), int(c.get("id") or 0)))
        parts_payload = [
            {
                **part,
                "chapters": chapters_by_part.get(int(part["id"]), []),
            }
            for part in parts_rows
        ]
        return {
            "project": project_data,
            # Legacy-shaped fields (part → chapter). When folder depth > 2 or the
            # tree is no longer legacy-compatible after reparent, these may be
            # approximate or incomplete — prefer `folders` for binder UI.
            "chapters": [
                ch
                for part in parts_payload
                for ch in part["chapters"]
            ] + ungrouped,
            "parts": parts_payload,
            "ungrouped_chapters": ungrouped,
        }

    def _prepare_outline_scene_dicts(
        self, scenes_rows: list[sqlite3.Row]
    ) -> list[dict]:
        """Normalize scene rows for outline trees (body_preview, parent_scene_id)."""
        untitled_titles = {"", "제목 없음", "새 씬", "새 하위 원고"}
        prepared: list[dict] = []
        for scene in scenes_rows:
            data = as_dict(scene)
            if "parent_scene_id" not in data:
                data["parent_scene_id"] = None
            title = str(data.get("title") or "").strip()
            content_head = data.pop("content_head", None)
            if title in untitled_titles:
                data["body_preview"] = first_sentence_preview(content_head or "")
            else:
                data["body_preview"] = ""
            prepared.append(data)
        return prepared

    def _build_folders_tree_from_db(
        self,
        connection: sqlite3.Connection,
        project_id: int,
        scenes_rows: list[sqlite3.Row] | None = None,
    ) -> list[dict]:
        """Unlimited-depth folder forest from bulk folder + scene queries (no N+1)."""
        if not folder_tree.folder_table_ready(connection):
            return []
        folder_rows = folder_tree.load_project_folder_rows(connection, project_id)
        if not folder_rows:
            return []

        # chapter source_id → folder id (for scenes missing folder_id)
        chapter_folder_by_source: dict[int, int] = {}
        for row in folder_rows:
            if row.get("source_kind") == "chapter" and row.get("source_id") is not None:
                chapter_folder_by_source[int(row["source_id"])] = int(row["id"])

        if scenes_rows is None:
            scenes_rows = self._load_outline_scenes(connection, project_id)
        prepared = self._prepare_outline_scene_dicts(scenes_rows)
        flat_by_folder: dict[int, list[dict]] = {}
        for data in prepared:
            # Prefer scene.folder_id; fall back to chapter→folder map
            fid = data.get("folder_id")
            if fid is not None and fid != "":
                try:
                    folder_key = int(fid)
                except (TypeError, ValueError):
                    folder_key = None
            else:
                folder_key = None
            if folder_key is None:
                ch_id = data.get("chapter_id")
                if ch_id is not None:
                    folder_key = chapter_folder_by_source.get(int(ch_id))
            if folder_key is None:
                continue
            # Keep folder_id on payload for clients that reparent scenes later
            data["folder_id"] = folder_key
            flat_by_folder.setdefault(folder_key, []).append(data)

        scenes_by_folder = {
            fid: self._build_scene_tree(flats)
            for fid, flats in flat_by_folder.items()
        }
        return folder_tree.build_folder_forest(folder_rows, scenes_by_folder)

    def _synthesize_folders_from_legacy_shape(self, payload: dict) -> list[dict]:
        """When folder rows are missing, mirror parts/chapters into a folders tree."""
        roots: list[dict] = []
        for part in payload.get("parts") or []:
            children = []
            for ch in part.get("chapters") or []:
                children.append(
                    {
                        "id": int(ch["id"]),
                        "title": ch.get("title") or "",
                        "is_box": False,
                        "synopsis_md": "",
                        "notes_md": ch.get("notes_md") or "",
                        "sort_order": int(ch.get("sort_order") or 0),
                        "source_kind": "chapter",
                        "source_id": int(ch["id"]),
                        "children": [],
                        "scenes": list(ch.get("scenes") or []),
                    }
                )
            roots.append(
                {
                    "id": int(part["id"]),
                    "title": part.get("title") or "",
                    "is_box": True,
                    "synopsis_md": "",
                    "notes_md": "",
                    "sort_order": int(part.get("sort_order") or 0),
                    "source_kind": "part",
                    "source_id": int(part["id"]),
                    "children": children,
                    "scenes": [],
                }
            )
        for ch in payload.get("ungrouped_chapters") or []:
            roots.append(
                {
                    "id": int(ch["id"]),
                    "title": ch.get("title") or "",
                    "is_box": False,
                    "synopsis_md": "",
                    "notes_md": ch.get("notes_md") or "",
                    "sort_order": int(ch.get("sort_order") or 0),
                    "source_kind": "chapter",
                    "source_id": int(ch["id"]),
                    "children": [],
                    "scenes": list(ch.get("scenes") or []),
                }
            )
        roots.sort(key=lambda n: (int(n.get("sort_order") or 0), int(n["id"])))
        return roots

    def _attach_folders_field(
        self,
        connection: sqlite3.Connection,
        project_id: int,
        payload: dict,
        scenes_rows: list[sqlite3.Row] | None = None,
    ) -> dict:
        """Add recursive `folders` tree; keep legacy parts/chapters fields intact."""
        folders: list[dict] = []
        if folder_tree.folder_table_ready(connection):
            folders = self._build_folders_tree_from_db(
                connection, project_id, scenes_rows=scenes_rows
            )
        if not folders:
            folders = self._synthesize_folders_from_legacy_shape(payload)
        payload["folders"] = folders
        # Document legacy field limits for deep / reparented trees
        payload["outline_shape"] = {
            "folders": "canonical unlimited-depth binder tree (folder.id)",
            "parts_chapters": (
                "legacy part→chapter shape for older clients; "
                "may be approximate or incomplete when depth > 2 "
                "or after non-legacy folder reparent — prefer folders"
            ),
        }
        return payload

    def _project_outline_from_legacy(
        self, connection: sqlite3.Connection, project_id: int
    ) -> dict:
        parts = connection.execute(
            "SELECT id, title, sort_order FROM part "
            "WHERE project_id = ? AND deleted_at IS NULL "
            "ORDER BY sort_order, id",
            (project_id,),
        ).fetchall()
        try:
            chapters = connection.execute(
                "SELECT id, part_id, parent_scene_id, title, notes_md, sort_order "
                "FROM chapter "
                "WHERE project_id = ? AND deleted_at IS NULL "
                "ORDER BY "
                "CASE WHEN part_id IS NULL THEN 1 ELSE 0 END, "
                "part_id, sort_order, id",
                (project_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            chapters = connection.execute(
                "SELECT id, part_id, title, notes_md, sort_order FROM chapter "
                "WHERE project_id = ? AND deleted_at IS NULL "
                "ORDER BY "
                "CASE WHEN part_id IS NULL THEN 1 ELSE 0 END, "
                "part_id, sort_order, id",
                (project_id,),
            ).fetchall()
        scenes = self._load_outline_scenes(connection, project_id)
        project = self._load_outline_project_row(connection, project_id)
        payload = self._assemble_outline_payload(
            project_data=self._project_meta_payload(project),
            parts_rows=[as_dict(p) for p in parts],
            chapters_rows=[as_dict(c) for c in chapters],
            scenes_rows=scenes,
        )
        return self._attach_folders_field(
            connection, project_id, payload, scenes_rows=scenes
        )

    def _project_outline_from_folder(
        self, connection: sqlite3.Connection, project_id: int
    ) -> dict:
        """Read binder structure from folder; expose legacy part/chapter ids via source_id."""
        part_folders = connection.execute(
            """
            SELECT id AS folder_id, source_id AS id, title, sort_order
            FROM folder
            WHERE project_id = ? AND deleted_at IS NULL
              AND source_kind = 'part' AND source_id IS NOT NULL
            ORDER BY sort_order, source_id
            """,
            (project_id,),
        ).fetchall()
        # chapter folders + legacy fields needed for parent_scene nesting / part_id
        chapter_folders = connection.execute(
            """
            SELECT f.id AS folder_id, f.source_id AS id, f.title, f.notes_md,
                   f.sort_order, f.parent_id,
                   c.part_id, c.parent_scene_id
            FROM folder f
            JOIN chapter c ON c.id = f.source_id AND c.project_id = f.project_id
            WHERE f.project_id = ? AND f.deleted_at IS NULL
              AND f.source_kind = 'chapter' AND f.source_id IS NOT NULL
            ORDER BY f.sort_order, f.source_id
            """,
            (project_id,),
        ).fetchall()
        folder_by_id = {
            int(r["folder_id"]): r
            for r in connection.execute(
                """
                SELECT id AS folder_id, source_kind, source_id, parent_id
                FROM folder
                WHERE project_id = ? AND deleted_at IS NULL
                """,
                (project_id,),
            ).fetchall()
        }
        # Order chapters under each part by folder sibling order (parent = part folder)
        part_folder_id_by_source = {
            int(p["id"]): int(p["folder_id"]) for p in part_folders
        }
        chapters_rows: list[dict] = []
        for ch in chapter_folders:
            parent_folder_id = ch["parent_id"]
            parent_meta = folder_by_id.get(int(parent_folder_id)) if parent_folder_id is not None else None
            # Prefer live chapter.part_id for response field; order comes from folder
            row = {
                "id": int(ch["id"]),
                "part_id": (
                    int(ch["part_id"]) if ch["part_id"] is not None else None
                ),
                "parent_scene_id": (
                    int(ch["parent_scene_id"])
                    if ch["parent_scene_id"] is not None
                    else None
                ),
                "title": ch["title"],
                "notes_md": ch["notes_md"] or "",
                "sort_order": int(ch["sort_order"] or 0),
                "_parent_is_part": bool(
                    parent_meta
                    and parent_meta["source_kind"] == "part"
                ),
                "_parent_is_root": parent_folder_id is None,
            }
            # If mapped under a part folder, force part_id to that part's source id
            if parent_meta and parent_meta["source_kind"] == "part" and parent_meta["source_id"] is not None:
                row["part_id"] = int(parent_meta["source_id"])
            chapters_rows.append(row)

        # Stable order: under-part chapters by (part folder order, chapter folder sort)
        # then ungrouped (parent root)
        part_order = {int(p["id"]): i for i, p in enumerate(part_folders)}

        def chapter_sort_key(c: dict) -> tuple:
            if c.get("parent_scene_id") is not None:
                return (2, 0, int(c.get("sort_order") or 0), int(c["id"]))
            if c.get("part_id") is not None:
                return (
                    0,
                    part_order.get(int(c["part_id"]), 9999),
                    int(c.get("sort_order") or 0),
                    int(c["id"]),
                )
            return (1, 0, int(c.get("sort_order") or 0), int(c["id"]))

        chapters_rows.sort(key=chapter_sort_key)
        # Strip private keys before assemble
        clean_chapters = [
            {k: v for k, v in c.items() if not k.startswith("_")}
            for c in chapters_rows
        ]
        parts_rows = [
            {
                "id": int(p["id"]),
                "title": p["title"],
                "sort_order": int(p["sort_order"] or 0),
            }
            for p in part_folders
        ]
        scenes = self._load_outline_scenes(connection, project_id)
        project = self._load_outline_project_row(connection, project_id)
        payload = self._assemble_outline_payload(
            project_data=self._project_meta_payload(project),
            parts_rows=parts_rows,
            chapters_rows=clean_chapters,
            scenes_rows=scenes,
        )
        return self._attach_folders_field(
            connection, project_id, payload, scenes_rows=scenes
        )

    def _flatten_scene_tree(self, nodes: list[dict]) -> list[dict]:
        out: list[dict] = []
        for node in nodes or []:
            kids = node.get("children") or []
            copy = {k: v for k, v in node.items() if k != "children"}
            out.append(copy)
            out.extend(self._flatten_scene_tree(kids))
        return out

    def create_scene(self, chapter_id: int, body: dict) -> dict:
        title = str(body.get("title", "새 씬")).strip() or "새 씬"
        raw_parent = body.get("parent_scene_id", body.get("parent_id", None))
        if raw_parent is None or raw_parent == "" or str(raw_parent).lower() == "null":
            parent_scene_id = None
        else:
            try:
                parent_scene_id = int(raw_parent)
            except (TypeError, ValueError) as error:
                raise ValueError("상위 원고 정보가 올바르지 않습니다.") from error

        with database() as connection:
            chapter = connection.execute(
                "SELECT project_id FROM chapter WHERE id = ? AND deleted_at IS NULL",
                (chapter_id,),
            ).fetchone()
            if chapter is None:
                raise ValueError("챕터를 찾을 수 없습니다.")
            project_id = int(chapter["project_id"])
            if parent_scene_id is not None:
                parent = connection.execute(
                    "SELECT id, chapter_id FROM scene "
                    "WHERE id = ? AND deleted_at IS NULL",
                    (parent_scene_id,),
                ).fetchone()
                if parent is None:
                    raise ValueError("상위 원고를 찾을 수 없습니다.")
                if int(parent["chapter_id"]) != chapter_id:
                    raise ValueError("상위 원고는 같은 폴더 안에 있어야 합니다.")
            if parent_scene_id is None:
                sort_order = connection.execute(
                    "SELECT COALESCE(MAX(sort_order) + 1, 0) FROM scene "
                    "WHERE chapter_id = ? AND parent_scene_id IS NULL AND deleted_at IS NULL",
                    (chapter_id,),
                ).fetchone()[0]
            else:
                sort_order = connection.execute(
                    "SELECT COALESCE(MAX(sort_order) + 1, 0) FROM scene "
                    "WHERE chapter_id = ? AND parent_scene_id = ? AND deleted_at IS NULL",
                    (chapter_id, parent_scene_id),
                ).fetchone()[0]
            # Resolve chapter's folder first (folder-first attachment for leaf scene)
            chapter_folder_id = folder_tree.folder_id_for_source(
                connection, project_id, "chapter", int(chapter_id)
            )
            if chapter_folder_id is None:
                self._mirror_project_folders(connection, project_id)
                chapter_folder_id = folder_tree.folder_id_for_source(
                    connection, project_id, "chapter", int(chapter_id)
                )
            try:
                if chapter_folder_id is not None:
                    cursor = connection.execute(
                        "INSERT INTO scene("
                        "project_id, chapter_id, parent_scene_id, title, sort_order, folder_id"
                        ") VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            project_id,
                            chapter_id,
                            parent_scene_id,
                            title,
                            sort_order,
                            chapter_folder_id,
                        ),
                    )
                else:
                    cursor = connection.execute(
                        "INSERT INTO scene(project_id, chapter_id, parent_scene_id, title, sort_order) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (project_id, chapter_id, parent_scene_id, title, sort_order),
                    )
            except sqlite3.OperationalError:
                # Pre-migration fallback (no parent_scene / no folder_id column)
                cursor = connection.execute(
                    "INSERT INTO scene(project_id, chapter_id, title, sort_order) "
                    "VALUES (?, ?, ?, ?)",
                    (project_id, chapter_id, title, sort_order),
                )
                parent_scene_id = None
            scene_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT INTO scene_revision(scene_id, revision_no, content_md, word_count) "
                "VALUES (?, 1, '', 0)",
                (scene_id,),
            )
            # Ensure folder_id even if INSERT path lacked column on first try
            if chapter_folder_id is not None:
                try:
                    connection.execute(
                        "UPDATE scene SET folder_id = ? WHERE id = ? AND folder_id IS NULL",
                        (chapter_folder_id, scene_id),
                    )
                except sqlite3.OperationalError:
                    pass
        return {
            "id": scene_id,
            "chapter_id": chapter_id,
            "parent_scene_id": parent_scene_id,
            "title": title,
        }

    def reparent_scene(self, scene_id: int, body: dict) -> dict:
        """Move a manuscript under another manuscript (or to folder root)."""
        return self.move_scene(scene_id, {
            "parent_scene_id": body.get("parent_scene_id", body.get("parent_id", None)),
        })

    @staticmethod
    def _parse_optional_int(value, field_label: str) -> int | None:
        if value is None or value == "" or str(value).lower() == "null":
            return None
        try:
            return int(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{field_label}이(가) 올바르지 않습니다.") from error

    def _scene_descendant_ids(
        self,
        connection: sqlite3.Connection,
        root_id: int,
    ) -> set[int]:
        """All active descendants of root (not including root)."""
        found: set[int] = set()
        frontier = [root_id]
        guard = 0
        while frontier and guard < 5000:
            guard += 1
            current = frontier.pop()
            rows = connection.execute(
                "SELECT id FROM scene WHERE parent_scene_id = ? AND deleted_at IS NULL",
                (current,),
            ).fetchall()
            for row in rows:
                child_id = int(row["id"])
                if child_id in found:
                    continue
                found.add(child_id)
                frontier.append(child_id)
        return found

    def _list_scene_sibling_ids(
        self,
        connection: sqlite3.Connection,
        chapter_id: int,
        parent_scene_id: int | None,
        exclude_ids: set[int] | None = None,
    ) -> list[int]:
        exclude = exclude_ids or set()
        if parent_scene_id is None:
            rows = connection.execute(
                "SELECT id FROM scene "
                "WHERE chapter_id = ? AND parent_scene_id IS NULL AND deleted_at IS NULL "
                "ORDER BY sort_order, id",
                (chapter_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT id FROM scene "
                "WHERE chapter_id = ? AND parent_scene_id = ? AND deleted_at IS NULL "
                "ORDER BY sort_order, id",
                (chapter_id, parent_scene_id),
            ).fetchall()
        return [int(r["id"]) for r in rows if int(r["id"]) not in exclude]

    def _assign_scene_sibling_orders(
        self,
        connection: sqlite3.Connection,
        chapter_id: int,
        parent_scene_id: int | None,
        ordered_ids: list[int],
    ) -> None:
        """Park then assign 0..n-1 within one sibling group."""
        base = 1_000_000
        for index, sid in enumerate(ordered_ids):
            connection.execute(
                "UPDATE scene SET sort_order = ? WHERE id = ? AND chapter_id = ?",
                (base + index, sid, chapter_id),
            )
        for index, sid in enumerate(ordered_ids):
            connection.execute(
                "UPDATE scene SET sort_order = ? WHERE id = ?",
                (index, sid),
            )
        # Ensure parent pointers match (in case of drift).
        for sid in ordered_ids:
            connection.execute(
                "UPDATE scene SET parent_scene_id = ? WHERE id = ?",
                (parent_scene_id, sid),
            )

    def move_scene(self, scene_id: int, body: dict) -> dict:
        """Move a manuscript anywhere: reorder, nest, promote, or change folder."""
        before_id = self._parse_optional_int(
            body.get("before_scene_id", body.get("before_id")),
            "앞쪽 원고",
        )
        after_id = self._parse_optional_int(
            body.get("after_scene_id", body.get("after_id")),
            "뒤쪽 원고",
        )
        if before_id is not None and after_id is not None:
            raise ValueError("앞쪽·뒤쪽 위치를 동시에 지정할 수 없습니다.")

        parent_in_body = "parent_scene_id" in body or "parent_id" in body
        raw_parent = body.get("parent_scene_id", body.get("parent_id", None))
        requested_parent = (
            self._parse_optional_int(raw_parent, "상위 원고")
            if parent_in_body
            else None
        )

        chapter_in_body = "chapter_id" in body
        requested_chapter = (
            self._parse_optional_int(body.get("chapter_id"), "폴더")
            if chapter_in_body
            else None
        )
        if chapter_in_body and requested_chapter is None:
            raise ValueError("폴더 정보가 올바르지 않습니다.")

        with database() as connection:
            scene = connection.execute(
                "SELECT id, project_id, chapter_id, parent_scene_id, title "
                "FROM scene WHERE id = ? AND deleted_at IS NULL",
                (scene_id,),
            ).fetchone()
            if scene is None:
                raise ValueError("원고를 찾을 수 없습니다.")

            project_id = int(scene["project_id"])
            old_chapter_id = int(scene["chapter_id"])
            old_parent_raw = scene["parent_scene_id"]
            old_parent_id = int(old_parent_raw) if old_parent_raw is not None else None

            anchor = None
            anchor_kind = None
            if before_id is not None:
                anchor = connection.execute(
                    "SELECT id, project_id, chapter_id, parent_scene_id FROM scene "
                    "WHERE id = ? AND deleted_at IS NULL",
                    (before_id,),
                ).fetchone()
                if anchor is None:
                    raise ValueError("앞쪽 원고를 찾을 수 없습니다.")
                anchor_kind = "before"
            elif after_id is not None:
                anchor = connection.execute(
                    "SELECT id, project_id, chapter_id, parent_scene_id FROM scene "
                    "WHERE id = ? AND deleted_at IS NULL",
                    (after_id,),
                ).fetchone()
                if anchor is None:
                    raise ValueError("뒤쪽 원고를 찾을 수 없습니다.")
                anchor_kind = "after"

            if anchor is not None:
                if int(anchor["project_id"]) != project_id:
                    raise ValueError("같은 작품 안의 원고로만 옮길 수 있습니다.")
                new_chapter_id = (
                    requested_chapter
                    if chapter_in_body
                    else int(anchor["chapter_id"])
                )
                if parent_in_body:
                    new_parent_id = requested_parent
                else:
                    ap = anchor["parent_scene_id"]
                    new_parent_id = int(ap) if ap is not None else None
            else:
                new_chapter_id = requested_chapter if chapter_in_body else old_chapter_id
                if parent_in_body:
                    new_parent_id = requested_parent
                else:
                    new_parent_id = old_parent_id

            chapter = connection.execute(
                "SELECT id, project_id FROM chapter "
                "WHERE id = ? AND deleted_at IS NULL",
                (new_chapter_id,),
            ).fetchone()
            if chapter is None:
                raise ValueError("폴더를 찾을 수 없습니다.")
            if int(chapter["project_id"]) != project_id:
                raise ValueError("같은 작품 안의 폴더로만 옮길 수 있습니다.")

            if new_parent_id is not None:
                if new_parent_id == scene_id:
                    raise ValueError("자기 자신을 상위 원고로 둘 수 없습니다.")
                parent = connection.execute(
                    "SELECT id, project_id, chapter_id, parent_scene_id FROM scene "
                    "WHERE id = ? AND deleted_at IS NULL",
                    (new_parent_id,),
                ).fetchone()
                if parent is None:
                    raise ValueError("상위 원고를 찾을 수 없습니다.")
                if int(parent["project_id"]) != project_id:
                    raise ValueError("같은 작품 안의 원고로만 옮길 수 있습니다.")
                if int(parent["chapter_id"]) != new_chapter_id:
                    raise ValueError("상위 원고와 같은 폴더로 옮겨 주세요.")
                descendants = self._scene_descendant_ids(connection, scene_id)
                if new_parent_id in descendants:
                    raise ValueError("하위 원고 아래로 자신을 넣을 수 없습니다.")

            subtree = self._scene_descendant_ids(connection, scene_id)
            subtree.add(scene_id)
            if anchor is not None and int(anchor["id"]) in subtree and anchor_kind:
                # Dropping before/after own descendant would require illegal parent.
                if new_parent_id in subtree:
                    raise ValueError("자기 하위 구조 안으로는 그렇게 옮길 수 없습니다.")

            exclude = set(subtree)
            siblings = self._list_scene_sibling_ids(
                connection, new_chapter_id, new_parent_id, exclude_ids=exclude
            )

            if anchor_kind == "before":
                try:
                    insert_at = siblings.index(before_id)
                except ValueError as error:
                    raise ValueError("앞쪽 원고가 같은 목록에 없습니다.") from error
            elif anchor_kind == "after":
                try:
                    insert_at = siblings.index(after_id) + 1
                except ValueError as error:
                    raise ValueError("뒤쪽 원고가 같은 목록에 없습니다.") from error
            else:
                insert_at = len(siblings)

            ordered = siblings[:insert_at] + [scene_id] + siblings[insert_at:]

            # Detect no-op (same chapter/parent/order).
            old_siblings = self._list_scene_sibling_ids(
                connection, old_chapter_id, old_parent_id, exclude_ids=set()
            )
            same_place = (
                old_chapter_id == new_chapter_id
                and old_parent_id == new_parent_id
                and old_siblings == ordered
            )
            if same_place:
                return {
                    "ok": True,
                    "id": scene_id,
                    "title": scene["title"],
                    "chapter_id": new_chapter_id,
                    "parent_scene_id": new_parent_id,
                    "moved": False,
                }

            # Resolve target chapter folder for folder_id (folder-first attachment)
            target_folder_id = folder_tree.folder_id_for_source(
                connection, project_id, "chapter", int(new_chapter_id)
            )
            if target_folder_id is None:
                self._mirror_project_folders(connection, project_id)
                target_folder_id = folder_tree.folder_id_for_source(
                    connection, project_id, "chapter", int(new_chapter_id)
                )

            # Park mover with a unique temporary order, then relocate.
            connection.execute(
                "UPDATE scene SET sort_order = ? WHERE id = ?",
                (9_000_000 + scene_id, scene_id),
            )
            # folder_id first when column exists, then chapter/parent
            try:
                connection.execute(
                    "UPDATE scene SET folder_id = ? WHERE id = ?",
                    (target_folder_id, scene_id),
                )
            except sqlite3.OperationalError:
                pass
            connection.execute(
                "UPDATE scene SET chapter_id = ?, parent_scene_id = ?, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), "
                "row_version = row_version + 1 "
                "WHERE id = ?",
                (new_chapter_id, new_parent_id, scene_id),
            )
            if new_chapter_id != old_chapter_id and subtree:
                for sid in subtree:
                    if sid == scene_id:
                        continue
                    try:
                        connection.execute(
                            "UPDATE scene SET folder_id = ? WHERE id = ?",
                            (target_folder_id, sid),
                        )
                    except sqlite3.OperationalError:
                        pass
                    connection.execute(
                        "UPDATE scene SET chapter_id = ?, "
                        "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), "
                        "row_version = row_version + 1 "
                        "WHERE id = ?",
                        (new_chapter_id, sid),
                    )

            self._assign_scene_sibling_orders(
                connection, new_chapter_id, new_parent_id, ordered
            )

            if (old_chapter_id, old_parent_id) != (new_chapter_id, new_parent_id):
                self._compact_scene_siblings(connection, old_chapter_id, old_parent_id)

        return {
            "ok": True,
            "id": scene_id,
            "title": scene["title"],
            "chapter_id": new_chapter_id,
            "parent_scene_id": new_parent_id,
            "moved": True,
        }

    def _compact_scene_siblings(
        self,
        connection: sqlite3.Connection,
        chapter_id: int,
        parent_scene_id: int | None,
    ) -> None:
        ids = self._list_scene_sibling_ids(connection, chapter_id, parent_scene_id)
        self._assign_scene_sibling_orders(connection, chapter_id, parent_scene_id, ids)

    def update_project_settings(self, project_id: int, body: dict) -> dict:
        with database() as connection:
            self.require_project(connection, project_id)
            has_link_col = True
            try:
                row = connection.execute(
                    "SELECT description_md, logline_md, worldbuilding_md, intro_md, intent_md, "
                    "tory_priority_md, outline_summary, "
                    "main_genre, sub_genre, keywords, purpose, goal_word_count, "
                    "linked_success_profile_id "
                    "FROM project WHERE id = ?",
                    (project_id,),
                ).fetchone()
            except sqlite3.OperationalError:
                has_link_col = False
                row = connection.execute(
                    "SELECT description_md, logline_md, worldbuilding_md, intro_md, intent_md, "
                    "tory_priority_md, outline_summary, "
                    "main_genre, sub_genre, keywords, purpose, goal_word_count "
                    "FROM project WHERE id = ?",
                    (project_id,),
                ).fetchone()
            synopsis = row["description_md"] if row else ""
            logline = row["logline_md"] if row else ""
            worldbuilding = row["worldbuilding_md"] if row else ""
            intro = row["intro_md"] if row and "intro_md" in row.keys() else ""
            intent = row["intent_md"] if row and "intent_md" in row.keys() else ""
            tory_priority = (
                row["tory_priority_md"] if row and "tory_priority_md" in row.keys() else ""
            )
            outline_summary = (
                row["outline_summary"] if row and "outline_summary" in row.keys() else ""
            )
            main_genre = row["main_genre"] if row else ""
            sub_genre = row["sub_genre"] if row else ""
            keywords = parse_project_keywords(
                row["keywords"] if row and "keywords" in row.keys() else []
            )
            purpose = row["purpose"] if row and "purpose" in row.keys() else "general_novel"
            goal_word_count = int(row["goal_word_count"] or 0) if row else 0
            linked_success_profile_id = None
            if has_link_col and row and "linked_success_profile_id" in row.keys():
                raw_link = row["linked_success_profile_id"]
                try:
                    linked_success_profile_id = (
                        int(raw_link) if raw_link not in (None, "", 0, "0") else None
                    )
                except (TypeError, ValueError):
                    linked_success_profile_id = None
            if "synopsis_md" in body:
                synopsis = str(body.get("synopsis_md", ""))[:50000]
            if "description_md" in body and "synopsis_md" not in body:
                synopsis = str(body.get("description_md", ""))[:50000]
            if "logline_md" in body:
                logline = str(body.get("logline_md", ""))[:20000]
            if "worldbuilding_md" in body:
                worldbuilding = str(body.get("worldbuilding_md", ""))[:50000]
            if "intro_md" in body:
                intro = str(body.get("intro_md", ""))[:50000]
            if "intent_md" in body:
                intent = str(body.get("intent_md", ""))[:50000]
            if "tory_priority_md" in body:
                tory_priority = str(body.get("tory_priority_md", ""))[:8000]
            if "outline_summary" in body:
                outline_summary = str(body.get("outline_summary", ""))[:20000]
            if "main_genre" in body:
                main_genre = str(body.get("main_genre") or "").strip()[:80]
            if "sub_genre" in body:
                sub_genre = str(body.get("sub_genre") or "").strip()[:80]
            if "keywords" in body:
                keywords = parse_project_keywords(body.get("keywords"))
            if "purpose" in body:
                purpose = document_import.normalise_purpose(body.get("purpose"))
            if "goal_word_count" in body:
                try:
                    goal_word_count = max(0, int(body.get("goal_word_count") or 0))
                except (TypeError, ValueError) as error:
                    raise ValueError("전체 목표 글자 수가 올바르지 않습니다.") from error
            if has_link_col and "linked_success_profile_id" in body:
                raw = body.get("linked_success_profile_id")
                if raw in (None, "", 0, "0", False):
                    linked_success_profile_id = None
                else:
                    try:
                        linked_success_profile_id = int(raw)
                    except (TypeError, ValueError) as error:
                        raise ValueError("흥행 프로파일 연결 정보가 올바르지 않아요.") from error
                    exists = connection.execute(
                        "SELECT 1 FROM success_pattern_profile WHERE id = ?",
                        (linked_success_profile_id,),
                    ).fetchone()
                    if exists is None:
                        raise ValueError("연결할 흥행 프로파일을 찾을 수 없어요.")
            keywords_json = json.dumps(keywords, ensure_ascii=False)
            if has_link_col:
                connection.execute(
                    "UPDATE project SET description_md = ?, logline_md = ?, worldbuilding_md = ?, "
                    "intro_md = ?, intent_md = ?, tory_priority_md = ?, outline_summary = ?, "
                    "main_genre = ?, sub_genre = ?, keywords = ?, purpose = ?, goal_word_count = ?, "
                    "linked_success_profile_id = ? WHERE id = ?",
                    (
                        synopsis, logline, worldbuilding, intro, intent, tory_priority, outline_summary,
                        main_genre, sub_genre, keywords_json, purpose, goal_word_count,
                        linked_success_profile_id, project_id,
                    ),
                )
            else:
                connection.execute(
                    "UPDATE project SET description_md = ?, logline_md = ?, worldbuilding_md = ?, "
                    "intro_md = ?, intent_md = ?, tory_priority_md = ?, outline_summary = ?, "
                    "main_genre = ?, sub_genre = ?, keywords = ?, purpose = ?, goal_word_count = ? WHERE id = ?",
                    (
                        synopsis, logline, worldbuilding, intro, intent, tory_priority, outline_summary,
                        main_genre, sub_genre, keywords_json, purpose, goal_word_count, project_id,
                    ),
                )
            # Keep .stg package purpose in sync when kind changes.
            if "purpose" in body:
                try:
                    ensure_project_package(connection, project_id)
                except Exception:
                    pass
        return {
            "ok": True,
            "synopsis_md": synopsis,
            "logline_md": logline,
            "worldbuilding_md": worldbuilding,
            "intro_md": intro,
            "intent_md": intent,
            "tory_priority_md": tory_priority,
            "outline_summary": outline_summary,
            "main_genre": main_genre,
            "sub_genre": sub_genre,
            "keywords": keywords,
            "purpose": purpose,
            "goal_word_count": goal_word_count,
            "linked_success_profile_id": linked_success_profile_id,
        }

    def list_writing_days(self, from_day: str, to_day: str) -> dict:
        start = _day_key_valid(from_day) if from_day else "1970-01-01"
        end = _day_key_valid(to_day) if to_day else "9999-12-31"
        if start > end:
            start, end = end, start
        with database() as connection:
            rows = connection.execute(
                "SELECT * FROM writing_day WHERE day_key >= ? AND day_key <= ? ORDER BY day_key",
                (start, end),
            ).fetchall()
            prefs = writing_prefs_row(connection)
            last = connection.execute(
                "SELECT last_active_at, day_key FROM writing_day "
                "WHERE last_active_at IS NOT NULL AND last_active_at != '' "
                "ORDER BY last_active_at DESC LIMIT 1"
            ).fetchone()
        days = [writing_day_payload(row) for row in rows]
        return {
            "days": days,
            "prefs": prefs,
            "last_active_at": (last["last_active_at"] if last else None),
            "last_active_day": (last["day_key"] if last else None),
        }

    def get_writing_day(self, day_key: str) -> dict:
        day = _day_key_valid(day_key)
        with database() as connection:
            row = connection.execute(
                "SELECT * FROM writing_day WHERE day_key = ?",
                (day,),
            ).fetchone()
            prefs = writing_prefs_row(connection)
        payload = writing_day_payload(row) or {
            "day": day,
            "chars_added": 0,
            "active_seconds": 0,
            "session_count": 0,
            "first_start_at": None,
            "last_active_at": None,
            "breakdown": {},
        }
        payload["prefs"] = prefs
        return payload

    def clear_writing_days(self, body: dict) -> dict:
        """Delete writing-day rows. Body: {all: true} or {days: ["YYYY-MM-DD", ...]}."""
        clear_all = bool(body.get("all"))
        raw_days = body.get("days") if isinstance(body.get("days"), list) else []
        days: list[str] = []
        for item in raw_days:
            try:
                days.append(_day_key_valid(str(item or "")))
            except ValueError as error:
                raise ValueError("날짜 형식이 올바르지 않습니다.") from error
        # unique preserve order
        seen: set[str] = set()
        unique_days = []
        for day in days:
            if day in seen:
                continue
            seen.add(day)
            unique_days.append(day)

        if not clear_all and not unique_days:
            raise ValueError("지울 날짜를 선택해 주세요.")

        with database() as connection:
            if clear_all:
                cursor = connection.execute("DELETE FROM writing_day")
                deleted = int(cursor.rowcount or 0)
                deleted_days = []
            else:
                deleted_days = []
                for day in unique_days:
                    cursor = connection.execute(
                        "DELETE FROM writing_day WHERE day_key = ?",
                        (day,),
                    )
                    if cursor.rowcount:
                        deleted_days.append(day)
                deleted = len(deleted_days)
            connection.commit()
        return {
            "ok": True,
            "deleted": deleted,
            "all": clear_all,
            "days": deleted_days if not clear_all else [],
        }

    def update_writing_prefs(self, body: dict) -> dict:
        with database() as connection:
            prefs = writing_prefs_row(connection)
            if "goal_chars" in body:
                try:
                    prefs["goal_chars"] = max(100, min(1_000_000, int(body.get("goal_chars") or 2000)))
                except (TypeError, ValueError) as error:
                    raise ValueError("목표 글자 수가 올바르지 않습니다.") from error
            if "goal_notify" in body:
                prefs["goal_notify"] = 1 if body.get("goal_notify") else 0
            else:
                prefs["goal_notify"] = 1 if prefs["goal_notify"] else 0
            if "lonely_days" in body:
                try:
                    prefs["lonely_days"] = max(1, min(365, int(body.get("lonely_days") or 3)))
                except (TypeError, ValueError) as error:
                    raise ValueError("미집필 알림 기간이 올바르지 않습니다.") from error
            if "lonely_notify" in body:
                prefs["lonely_notify"] = 1 if body.get("lonely_notify") else 0
            else:
                prefs["lonely_notify"] = 1 if prefs["lonely_notify"] else 0
            if "idle_minutes" in body:
                try:
                    prefs["idle_minutes"] = max(5, min(240, int(body.get("idle_minutes") or 30)))
                except (TypeError, ValueError) as error:
                    raise ValueError("미입력 차감 시간이 올바르지 않습니다.") from error
            if "chars_auto" in body:
                prefs["chars_auto"] = 1 if body.get("chars_auto") else 0
            else:
                prefs["chars_auto"] = 1 if prefs.get("chars_auto", True) else 0
            if "time_auto" in body:
                prefs["time_auto"] = 1 if body.get("time_auto") else 0
            else:
                prefs["time_auto"] = 1 if prefs.get("time_auto") else 0
            if "include_phone_log" in body:
                prefs["include_phone_log"] = 1 if body.get("include_phone_log") else 0
            else:
                prefs["include_phone_log"] = 1 if prefs.get("include_phone_log", True) else 0
            if "last_goal_notified_day" in body:
                day = str(body.get("last_goal_notified_day") or "").strip()
                if day:
                    _day_key_valid(day)
                prefs["last_goal_notified_day"] = day
            if "last_lonely_notified_day" in body:
                day = str(body.get("last_lonely_notified_day") or "").strip()
                if day:
                    _day_key_valid(day)
                prefs["last_lonely_notified_day"] = day
            connection.execute(
                "UPDATE writing_prefs SET goal_chars = ?, goal_notify = ?, lonely_days = ?, "
                "lonely_notify = ?, idle_minutes = ?, chars_auto = ?, time_auto = ?, "
                "include_phone_log = ?, "
                "last_goal_notified_day = ?, last_lonely_notified_day = ?, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = 1",
                (
                    prefs["goal_chars"],
                    1 if prefs["goal_notify"] else 0,
                    prefs["lonely_days"],
                    1 if prefs["lonely_notify"] else 0,
                    prefs["idle_minutes"],
                    1 if prefs.get("chars_auto", True) else 0,
                    1 if prefs.get("time_auto") else 0,
                    1 if prefs.get("include_phone_log", True) else 0,
                    prefs.get("last_goal_notified_day") or "",
                    prefs.get("last_lonely_notified_day") or "",
                ),
            )
            return writing_prefs_row(connection)

    def writing_heartbeat(self, body: dict) -> dict:
        day = _day_key_valid(body.get("day") or body.get("day_key"))
        try:
            chars_delta = max(0, int(body.get("chars_delta") or 0))
            active_delta = max(0, int(body.get("active_seconds_delta") or 0))
            session_bump = 1 if body.get("session_start") else 0
        except (TypeError, ValueError) as error:
            raise ValueError("기록 수치가 올바르지 않습니다.") from error
        # Cap single heartbeat to avoid runaway clients
        chars_delta = min(chars_delta, 200_000)
        active_delta = min(active_delta, 6 * 3600)
        project_id = body.get("project_id")
        project_key = str(int(project_id)) if project_id not in (None, "") else ""
        now_iso = str(body.get("last_active_at") or "").strip() or _iso_now()
        session_start = str(body.get("session_started_at") or "").strip() or None

        with database() as connection:
            row = connection.execute(
                "SELECT * FROM writing_day WHERE day_key = ?",
                (day,),
            ).fetchone()
            if row:
                data = as_dict(row) or {}
                chars = int(data.get("chars_added") or 0) + chars_delta
                active = int(data.get("active_seconds") or 0) + active_delta
                sessions = int(data.get("session_count") or 0) + session_bump
                first_start = data.get("first_start_at") or session_start or now_iso
                try:
                    breakdown = json.loads(data.get("breakdown_json") or "{}")
                    if not isinstance(breakdown, dict):
                        breakdown = {}
                except json.JSONDecodeError:
                    breakdown = {}
            else:
                chars = chars_delta
                active = active_delta
                sessions = session_bump
                first_start = session_start or now_iso
                breakdown = {}
            if project_key and chars_delta:
                breakdown[project_key] = int(breakdown.get(project_key) or 0) + chars_delta
            connection.execute(
                "INSERT INTO writing_day("
                "day_key, chars_added, active_seconds, session_count, "
                "first_start_at, last_active_at, breakdown_json, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) "
                "ON CONFLICT(day_key) DO UPDATE SET "
                "chars_added = excluded.chars_added, "
                "active_seconds = excluded.active_seconds, "
                "session_count = excluded.session_count, "
                "first_start_at = COALESCE(writing_day.first_start_at, excluded.first_start_at), "
                "last_active_at = excluded.last_active_at, "
                "breakdown_json = excluded.breakdown_json, "
                "updated_at = excluded.updated_at",
                (
                    day,
                    chars,
                    active,
                    sessions,
                    first_start,
                    now_iso,
                    json.dumps(breakdown, ensure_ascii=False),
                ),
            )
            prefs = writing_prefs_row(connection)
            day_row = connection.execute(
                "SELECT * FROM writing_day WHERE day_key = ?",
                (day,),
            ).fetchone()
        payload = writing_day_payload(day_row) or {}
        payload["prefs"] = prefs
        payload["ok"] = True
        # Client may celebrate when chars_added crossed goal today
        goal = int(prefs.get("goal_chars") or 2000)
        payload["goal_reached"] = bool(
            prefs.get("goal_notify")
            and chars >= goal
            and (prefs.get("last_goal_notified_day") or "") != day
        )
        return payload

    def get_writing_pair(self) -> dict:
        with database() as connection:
            row = connection.execute(
                "SELECT * FROM mobile_device WHERE revoked_at IS NULL "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if not row:
                return {
                    "pair_code": None,
                    "device_name": "",
                    "paired": False,
                    "paired_at": None,
                    "last_seen_at": None,
                    "hint": "코드를 발급하면 나중에 휴대폰 앱에서 연결할 수 있어요.",
                }
            data = as_dict(row) or {}
            return {
                "pair_code": data.get("pair_code"),
                "device_name": data.get("device_name") or "",
                "paired": bool(data.get("paired_at")),
                "paired_at": data.get("paired_at"),
                "last_seen_at": data.get("last_seen_at"),
                "hint": "휴대폰 전용 앱에서 이 코드로 SuperTORY에 연결할 예정이에요.",
            }

    def issue_writing_pair(self, body: dict) -> dict:
        code = f"{uuid.uuid4().int % 1_000_000:06d}"
        name = str(body.get("device_name") or "").strip()[:80]
        with database() as connection:
            # Revoke previous open codes (single active pairing for now)
            connection.execute(
                "UPDATE mobile_device SET revoked_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE revoked_at IS NULL"
            )
            connection.execute(
                "INSERT INTO mobile_device(pair_code, device_name) VALUES (?, ?)",
                (code, name),
            )
        return self.get_writing_pair()

    def list_mobile_inbox(self) -> dict:
        with database() as connection:
            rows = connection.execute(
                "SELECT id, device_id, title, body_md, source, created_at, read_at "
                "FROM mobile_inbox ORDER BY created_at DESC, id DESC LIMIT 100"
            ).fetchall()
        items = []
        for row in rows:
            data = as_dict(row) or {}
            body = str(data.get("body_md") or "")
            items.append({
                "id": data.get("id"),
                "title": data.get("title") or "제목 없음",
                "body_md": body,
                "preview": body.replace("\n", " ").strip()[:120],
                "source": data.get("source") or "phone",
                "created_at": data.get("created_at"),
                "read": bool(data.get("read_at")),
            })
        return {"items": items}

    def mark_mobile_inbox_read(self, item_id: int) -> dict:
        with database() as connection:
            row = connection.execute(
                "SELECT id FROM mobile_inbox WHERE id = ?",
                (item_id,),
            ).fetchone()
            if not row:
                raise ValueError("받은 글을 찾지 못했어요.")
            connection.execute(
                "UPDATE mobile_inbox SET read_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE id = ? AND read_at IS NULL",
                (item_id,),
            )
        return {"ok": True, "id": item_id}

    def mobile_push_text(self, body: dict) -> dict:
        """Phone companion stub: accept text with pair_code and store in inbox."""
        code = str(body.get("pair_code") or "").strip()
        if not re.fullmatch(r"\d{6}", code):
            raise ValueError("연동 코드 6자리가 필요해요.")
        title = str(body.get("title") or "휴대폰에서 보낸 글").strip()[:200]
        text = str(body.get("body_md") or body.get("text") or "").strip()[:100000]
        if not text:
            raise ValueError("보낼 글이 비어 있어요.")
        device_name = str(body.get("device_name") or "phone").strip()[:80]
        with database() as connection:
            device = connection.execute(
                "SELECT id FROM mobile_device WHERE pair_code = ? AND revoked_at IS NULL",
                (code,),
            ).fetchone()
            if not device:
                raise ValueError("유효한 연동 코드가 아니에요. PC에서 코드를 다시 발급해 주세요.")
            device_id = int(device["id"])
            connection.execute(
                "UPDATE mobile_device SET device_name = COALESCE(NULLIF(?, ''), device_name), "
                "paired_at = COALESCE(paired_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')), "
                "last_seen_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                (device_name, device_id),
            )
            cursor = connection.execute(
                "INSERT INTO mobile_inbox(device_id, title, body_md, source) VALUES (?, ?, ?, 'phone')",
                (device_id, title, text),
            )
            item_id = int(cursor.lastrowid)
        return {"ok": True, "id": item_id, "title": title}

    def bulk_set_scene_goals(self, project_id: int, body: dict) -> dict:
        """Set the same goal_word_count on all active scenes; optionally also goal_metric."""
        try:
            goal_count = max(0, int(body.get("goal_word_count", 0) or 0))
        except (TypeError, ValueError) as error:
            raise ValueError("목표 글자 수가 올바르지 않습니다.") from error
        apply_metric = bool(body.get("apply_metric", False))
        if "goal_metric" in body and body.get("apply_metric") is None:
            # Backward-compatible: sending goal_metric without flag still applies metric.
            apply_metric = True
        goal_metric = str(body.get("goal_metric", "chars_with_space") or "chars_with_space")
        if goal_metric not in GOAL_METRICS:
            goal_metric = "chars_with_space"
        with database() as connection:
            self.require_project(connection, project_id)
            if apply_metric:
                cursor = connection.execute(
                    "UPDATE scene SET goal_word_count = ?, goal_metric = ?, "
                    "row_version = row_version + 1, "
                    "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                    "WHERE project_id = ? AND deleted_at IS NULL",
                    (goal_count, goal_metric, project_id),
                )
            else:
                cursor = connection.execute(
                    "UPDATE scene SET goal_word_count = ?, "
                    "row_version = row_version + 1, "
                    "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                    "WHERE project_id = ? AND deleted_at IS NULL",
                    (goal_count, project_id),
                )
            count = int(cursor.rowcount or 0)
        return {
            "ok": True,
            "count": count,
            "goal_word_count": goal_count,
            "goal_metric": goal_metric if apply_metric else None,
            "apply_metric": apply_metric,
        }

    def scene_detail(self, scene_id: int) -> dict:
        with database() as connection:
            scene = connection.execute(
                "SELECT s.id, s.project_id, s.chapter_id, s.title, s.synopsis_md, s.notes_md, s.status, "
                "s.goal_word_count, s.goal_metric, s.row_version, s.reference_links_json, "
                "r.content_md, r.word_count, r.revision_no, "
                "p.purpose AS project_purpose "
                "FROM scene s "
                "JOIN scene_revision r ON r.scene_id = s.id AND r.is_current = 1 "
                "JOIN project p ON p.id = s.project_id "
                "WHERE s.id = ? AND s.deleted_at IS NULL",
                (scene_id,),
            ).fetchone()
        if scene is None:
            raise ValueError("씬을 찾을 수 없습니다.")
        detail = as_dict(scene)  # type: ignore[assignment]
        if detail.get("goal_metric") not in GOAL_METRICS:
            detail["goal_metric"] = "chars_with_space"
        detail["reference_links"] = parse_reference_links(detail.pop("reference_links_json", "[]"))
        detail["illustrations"] = self.list_illustrations(scene_id)
        return detail

    def duplicate_scene(self, scene_id: int) -> dict:
        """Clone a scene (content, cast, links, illustrations) right after the source."""
        with database() as connection:
            source = connection.execute(
                "SELECT s.id, s.project_id, s.chapter_id, s.title, s.synopsis_md, s.notes_md, s.status, "
                "s.goal_word_count, s.goal_metric, s.reference_links_json, s.sort_order "
                "FROM scene s WHERE s.id = ? AND s.deleted_at IS NULL",
                (scene_id,),
            ).fetchone()
            if source is None:
                raise ValueError("씬을 찾을 수 없습니다.")

            revision = connection.execute(
                "SELECT content_md, word_count FROM scene_revision "
                "WHERE scene_id = ? AND is_current = 1",
                (scene_id,),
            ).fetchone()
            content_md = revision["content_md"] if revision else ""
            word_count = int(revision["word_count"] or 0) if revision else 0

            chapter_id = int(source["chapter_id"])
            project_id = int(source["project_id"])
            ordered = connection.execute(
                "SELECT id FROM scene "
                "WHERE chapter_id = ? AND deleted_at IS NULL "
                "ORDER BY sort_order, id",
                (chapter_id,),
            ).fetchall()
            ordered_ids = [int(row["id"]) for row in ordered]
            try:
                insert_at = ordered_ids.index(int(source["id"])) + 1
            except ValueError:
                insert_at = len(ordered_ids)

            base_title = str(source["title"] or "씬").strip() or "씬"
            # Avoid endless " (복제) (복제)" growth for repeated clones.
            clone_title = re.sub(r"(?:\s*\(복제\))+$", "", base_title).strip() or "씬"
            clone_title = f"{clone_title} (복제)"
            if len(clone_title) > 200:
                clone_title = clone_title[:197] + "…"

            # Park existing rows, then insert clone and reassign 0..n.
            base = 1_000_000
            for index, existing_id in enumerate(ordered_ids):
                connection.execute(
                    "UPDATE scene SET sort_order = ? WHERE id = ? AND deleted_at IS NULL",
                    (base + index, existing_id),
                )

            goal_metric = source["goal_metric"] if source["goal_metric"] in GOAL_METRICS else "chars_with_space"
            cursor = connection.execute(
                "INSERT INTO scene("
                "project_id, chapter_id, title, synopsis_md, notes_md, status, "
                "goal_word_count, goal_metric, reference_links_json, sort_order"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    project_id,
                    chapter_id,
                    clone_title,
                    str(source["synopsis_md"] or ""),
                    str(source["notes_md"] or ""),
                    str(source["status"] or "idea"),
                    int(source["goal_word_count"] or 0),
                    goal_metric,
                    str(source["reference_links_json"] or "[]"),
                    base + len(ordered_ids),  # temporary
                ),
            )
            new_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT INTO scene_revision(scene_id, revision_no, content_md, word_count, save_note, is_current) "
                "VALUES (?, 1, ?, ?, ?, 1)",
                (new_id, content_md, word_count, "복제 생성"),
            )

            # Cast cast
            cast_rows = connection.execute(
                "SELECT character_id, appearance_role, is_pov FROM scene_character WHERE scene_id = ?",
                (scene_id,),
            ).fetchall()
            for row in cast_rows:
                connection.execute(
                    "INSERT INTO scene_character(scene_id, character_id, project_id, appearance_role, is_pov) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        new_id,
                        int(row["character_id"]),
                        project_id,
                        str(row["appearance_role"] or "appears"),
                        int(row["is_pov"] or 0),
                    ),
                )

            # Illustrations: copy image files so originals stay independent.
            illust_rows = connection.execute(
                "SELECT file_name, mime_type, caption_md, overlays_json, sort_order "
                "FROM scene_illustration WHERE scene_id = ? ORDER BY sort_order, id",
                (scene_id,),
            ).fetchall()
            for row in illust_rows:
                old_name = str(row["file_name"] or "")
                src_path = illustration_dir_for(project_id) / old_name
                extension = Path(old_name).suffix or ".bin"
                new_name = f"{uuid.uuid4().hex}{extension}"
                dest_path = illustration_dir_for(project_id) / new_name
                if src_path.is_file():
                    dest_path.write_bytes(src_path.read_bytes())
                else:
                    # Still keep metadata row if file missing.
                    dest_path.write_bytes(b"")
                connection.execute(
                    "INSERT INTO scene_illustration("
                    "project_id, scene_id, file_name, mime_type, caption_md, overlays_json, sort_order"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        project_id,
                        new_id,
                        new_name,
                        str(row["mime_type"] or "image/jpeg"),
                        str(row["caption_md"] or ""),
                        str(row["overlays_json"] or "[]"),
                        int(row["sort_order"] or 0),
                    ),
                )

            final_order = ordered_ids[:]
            final_order.insert(insert_at, new_id)
            for index, sid in enumerate(final_order):
                connection.execute(
                    "UPDATE scene SET sort_order = ? WHERE id = ? AND deleted_at IS NULL",
                    (index, sid),
                )

            self._mirror_project_folders(connection, project_id)
        return {"id": new_id, "title": clone_title, "source_id": scene_id, "chapter_id": chapter_id}

    def list_trash(self, project_id: int) -> dict:
        """Soft-deleted scenes for the project trash bin."""
        with database() as connection:
            self.require_project(connection, project_id)
            rows = connection.execute(
                "SELECT s.id, s.title, s.chapter_id, s.deleted_at, s.status, s.updated_at, "
                "c.title AS chapter_title, "
                "c.deleted_at AS chapter_deleted_at, "
                "COALESCE(r.word_count, 0) AS word_count "
                "FROM scene s "
                "LEFT JOIN chapter c ON c.id = s.chapter_id "
                "LEFT JOIN scene_revision r ON r.scene_id = s.id AND r.is_current = 1 "
                "WHERE s.project_id = ? AND s.deleted_at IS NOT NULL "
                "ORDER BY s.deleted_at DESC, s.id DESC",
                (project_id,),
            ).fetchall()
        items = []
        for row in rows:
            item = as_dict(row)
            item["chapter_trashed"] = item.get("chapter_deleted_at") is not None
            items.append(item)
        return {"items": items, "count": len(items)}

    def trash_scene(self, scene_id: int) -> dict:
        """Move a scene to trash (soft-delete).

        3-3-b-4: scenes have no folder row; soft-delete legacy only (folder_id left for audit).
        """
        with database() as connection:
            scene = connection.execute(
                "SELECT id, project_id, chapter_id, title, sort_order "
                "FROM scene WHERE id = ? AND deleted_at IS NULL",
                (scene_id,),
            ).fetchone()
            if scene is None:
                raise ValueError("씬을 찾을 수 없습니다.")
            # No folder row for scenes — folder-first N/A; soft-delete scene only
            connection.execute(
                "UPDATE scene SET deleted_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE id = ?",
                (scene_id,),
            )
        return {
            "ok": True,
            "id": scene_id,
            "title": scene["title"],
            "project_id": int(scene["project_id"]),
        }

    def trash_chapter(self, chapter_id: int) -> dict:
        """Move a chapter (folder) and its scenes to trash (soft-delete).

        3-3-b-4: folder-first soft-delete of chapter folder (+ nested folders), then legacy.
        Scenes are cascaded by DB trigger; also explicit safety net.
        """
        with database() as connection:
            chapter = connection.execute(
                "SELECT id, project_id, title "
                "FROM chapter WHERE id = ? AND deleted_at IS NULL",
                (chapter_id,),
            ).fetchone()
            if chapter is None:
                raise ValueError("챕터(폴더)를 찾을 수 없습니다. 이미 버려졌을 수 있어요.")
            project_id = int(chapter["project_id"])
            scene_ids = [
                int(row[0])
                for row in connection.execute(
                    "SELECT id FROM scene WHERE chapter_id = ? AND deleted_at IS NULL",
                    (chapter_id,),
                ).fetchall()
            ]
            scene_count = len(scene_ids)
            chapter_title = str(chapter["title"] or "")
            trash_payload = None
            try:
                root_fid = folder_tree.folder_id_for_source(
                    connection, project_id, "chapter", int(chapter_id)
                )
                if root_fid is not None and folder_tree.action_log_table_ready(
                    connection
                ):
                    # Include nested chapter folders' scenes as well
                    folder_ids_preview = folder_tree.collect_folder_descendant_ids(
                        connection, int(root_fid)
                    )
                    nested_chapter_ids = [int(chapter_id)]
                    for fid in folder_ids_preview:
                        fr = connection.execute(
                            "SELECT source_kind, source_id FROM folder WHERE id = ?",
                            (int(fid),),
                        ).fetchone()
                        if fr is None:
                            continue
                        sk = fr["source_kind"] if hasattr(fr, "keys") else fr[0]
                        sid = fr["source_id"] if hasattr(fr, "keys") else fr[1]
                        if sk == "chapter" and sid is not None:
                            nested_chapter_ids.append(int(sid))
                    nested_chapter_ids = list(dict.fromkeys(nested_chapter_ids))
                    all_scene_ids = list(scene_ids)
                    if nested_chapter_ids:
                        ph = ",".join("?" * len(nested_chapter_ids))
                        extra = connection.execute(
                            f"""
                            SELECT id FROM scene
                            WHERE chapter_id IN ({ph}) AND deleted_at IS NULL
                            """,
                            nested_chapter_ids,
                        ).fetchall()
                        for row in extra:
                            sid = int(row["id"] if hasattr(row, "keys") else row[0])
                            if sid not in all_scene_ids:
                                all_scene_ids.append(sid)
                    trash_payload = folder_tree.snapshot_folder_trash(
                        connection,
                        project_id,
                        int(root_fid),
                        part_ids=[],
                        chapter_ids=nested_chapter_ids,
                        scene_ids=all_scene_ids,
                    )
            except (sqlite3.OperationalError, ValueError):
                trash_payload = None

            now_sql = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
            # 1) folder first
            try:
                folder_tree.soft_delete_folder_for_source(
                    connection,
                    project_id,
                    "chapter",
                    int(chapter_id),
                    cascade_children=True,
                )
            except sqlite3.OperationalError:
                pass
            # 2) legacy chapter + scenes
            connection.execute(
                f"UPDATE chapter SET deleted_at = {now_sql}, "
                f"updated_at = {now_sql}, "
                f"row_version = row_version + 1 "
                "WHERE id = ? AND deleted_at IS NULL",
                (chapter_id,),
            )
            if scene_ids:
                connection.execute(
                    f"UPDATE scene SET deleted_at = COALESCE(deleted_at, {now_sql}), "
                    f"updated_at = {now_sql} "
                    "WHERE chapter_id = ? AND deleted_at IS NULL",
                    (chapter_id,),
                )
            still = connection.execute(
                "SELECT id FROM chapter WHERE id = ? AND deleted_at IS NULL",
                (chapter_id,),
            ).fetchone()
            if still is not None:
                raise ValueError("폴더를 휴지통으로 옮기지 못했어요. 다시 시도해 주세요.")
            if trash_payload is not None:
                short = (
                    chapter_title
                    if len(chapter_title) <= 24
                    else chapter_title[:23] + "…"
                )
                folder_tree.append_folder_action_log(
                    connection,
                    project_id,
                    "folder.trash",
                    f"「{short or '폴더'}」 버리기",
                    trash_payload,
                )
        return {
            "ok": True,
            "id": chapter_id,
            "title": chapter["title"],
            "project_id": project_id,
            "scene_count": int(scene_count or 0),
            "scene_ids": scene_ids,
        }

    def restore_scene(self, scene_id: int) -> dict:
        """Restore a scene from trash back into its chapter binder.

        Restores soft-deleted chapter / parent manuscripts when needed.
        """
        with database() as connection:
            try:
                scene = connection.execute(
                    "SELECT id, project_id, chapter_id, parent_scene_id, title, sort_order "
                    "FROM scene WHERE id = ? AND deleted_at IS NOT NULL",
                    (scene_id,),
                ).fetchone()
            except sqlite3.OperationalError:
                scene = connection.execute(
                    "SELECT id, project_id, chapter_id, title, sort_order "
                    "FROM scene WHERE id = ? AND deleted_at IS NOT NULL",
                    (scene_id,),
                ).fetchone()
            if scene is None:
                raise ValueError("휴지통에서 해당 원고를 찾을 수 없습니다.")
            chapter_id = int(scene["chapter_id"])
            parent_scene_id = None
            try:
                if scene["parent_scene_id"] is not None:
                    parent_scene_id = int(scene["parent_scene_id"])
            except (KeyError, IndexError, TypeError):
                parent_scene_id = None

            chapter = connection.execute(
                "SELECT id, title, deleted_at, part_id FROM chapter WHERE id = ?",
                (chapter_id,),
            ).fetchone()
            if chapter is None:
                raise ValueError("원래 폴더(챕터)를 찾을 수 없어 복원할 수 없습니다.")

            chapter_restored = False
            parent_scene_restored = 0
            now_sql = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
            if chapter["deleted_at"] is not None:
                connection.execute(
                    f"UPDATE chapter SET deleted_at = NULL, "
                    f"updated_at = {now_sql}, "
                    f"row_version = row_version + 1 "
                    "WHERE id = ? AND deleted_at IS NOT NULL",
                    (chapter_id,),
                )
                chapter_restored = True

            # Restore ancestor manuscripts (deepest parent first via stack)
            if parent_scene_id is not None:
                chain: list[int] = []
                walk = parent_scene_id
                guard = 0
                while walk is not None and guard < 500:
                    chain.append(walk)
                    row = connection.execute(
                        "SELECT parent_scene_id, deleted_at FROM scene WHERE id = ?",
                        (walk,),
                    ).fetchone()
                    if row is None:
                        break
                    walk = (
                        int(row["parent_scene_id"])
                        if row["parent_scene_id"] is not None
                        else None
                    )
                    guard += 1
                for ancestor_id in reversed(chain):
                    row = connection.execute(
                        "SELECT deleted_at FROM scene WHERE id = ?",
                        (ancestor_id,),
                    ).fetchone()
                    if row is None:
                        continue
                    if row["deleted_at"] is not None:
                        connection.execute(
                            f"UPDATE scene SET deleted_at = NULL, "
                            f"updated_at = {now_sql} "
                            "WHERE id = ? AND deleted_at IS NOT NULL",
                            (ancestor_id,),
                        )
                        parent_scene_restored += 1

            if parent_scene_id is None:
                preferred = connection.execute(
                    "SELECT COALESCE(MAX(sort_order) + 1, 0) FROM scene "
                    "WHERE chapter_id = ? AND parent_scene_id IS NULL AND deleted_at IS NULL",
                    (chapter_id,),
                ).fetchone()[0]
            else:
                preferred = connection.execute(
                    "SELECT COALESCE(MAX(sort_order) + 1, 0) FROM scene "
                    "WHERE chapter_id = ? AND parent_scene_id = ? AND deleted_at IS NULL",
                    (chapter_id, parent_scene_id),
                ).fetchone()[0]

            connection.execute(
                f"UPDATE scene SET deleted_at = NULL, sort_order = ?, "
                f"updated_at = {now_sql} "
                "WHERE id = ?",
                (preferred, scene_id),
            )
        return {
            "ok": True,
            "id": scene_id,
            "title": scene["title"],
            "chapter_id": chapter_id,
            "chapter_title": chapter["title"],
            "chapter_restored": chapter_restored,
            "parent_scene_restored": parent_scene_restored,
            "parent_scene_id": parent_scene_id,
            "sort_order": preferred,
        }

    def _hard_delete_scene(self, connection: sqlite3.Connection, scene_id: int) -> None:
        """Permanently remove a scene and related rows/files. Scene must already be in trash."""
        scene = connection.execute(
            "SELECT id, project_id FROM scene WHERE id = ? AND deleted_at IS NOT NULL",
            (scene_id,),
        ).fetchone()
        if scene is None:
            raise ValueError("휴지통에서 해당 원고를 찾을 수 없습니다.")
        project_id = int(scene["project_id"])

        illust_rows = connection.execute(
            "SELECT id, file_name FROM scene_illustration WHERE scene_id = ?",
            (scene_id,),
        ).fetchall()
        for row in illust_rows:
            file_name = str(row["file_name"] or "")
            if file_name:
                path = illustration_dir_for(project_id) / file_name
                try:
                    if path.is_file():
                        path.unlink()
                except OSError:
                    pass
            connection.execute("DELETE FROM scene_illustration WHERE id = ?", (int(row["id"]),))

        connection.execute("DELETE FROM scene_character WHERE scene_id = ?", (scene_id,))
        connection.execute("DELETE FROM scene_search_content WHERE scene_id = ?", (scene_id,))
        # scene_tag may exist on older DBs with tags enabled.
        try:
            connection.execute("DELETE FROM scene_tag WHERE scene_id = ?", (scene_id,))
        except sqlite3.Error:
            pass

        # Schema retains revisions by default; allow purge only for trashed scenes.
        connection.execute("DROP TRIGGER IF EXISTS scene_revision_no_delete")
        try:
            connection.execute("DELETE FROM scene_revision WHERE scene_id = ?", (scene_id,))
            connection.execute("DELETE FROM scene WHERE id = ?", (scene_id,))
        finally:
            connection.execute(
                """
                CREATE TRIGGER scene_revision_no_delete
                BEFORE DELETE ON scene_revision
                BEGIN
                    SELECT RAISE(ABORT, 'scene revisions are retained');
                END;
                """
            )

    def purge_scene(self, scene_id: int) -> dict:
        """Permanently delete one trashed scene."""
        with database() as connection:
            scene = connection.execute(
                "SELECT id, title FROM scene WHERE id = ? AND deleted_at IS NOT NULL",
                (scene_id,),
            ).fetchone()
            if scene is None:
                raise ValueError("휴지통에서 해당 원고를 찾을 수 없습니다.")
            title = scene["title"]
            self._hard_delete_scene(connection, scene_id)
        return {"ok": True, "id": scene_id, "title": title}

    def empty_trash(self, project_id: int) -> dict:
        """Permanently delete all trashed scenes for a project."""
        with database() as connection:
            self.require_project(connection, project_id)
            rows = connection.execute(
                "SELECT id FROM scene WHERE project_id = ? AND deleted_at IS NOT NULL",
                (project_id,),
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
            for scene_id in ids:
                self._hard_delete_scene(connection, scene_id)
        return {"ok": True, "purged": len(ids)}

    def list_illustrations(self, scene_id: int) -> list[dict]:
        with database() as connection:
            scene = connection.execute(
                "SELECT id FROM scene WHERE id = ? AND deleted_at IS NULL", (scene_id,)
            ).fetchone()
            if scene is None:
                raise ValueError("씬을 찾을 수 없습니다.")
            rows = connection.execute(
                "SELECT id, project_id, scene_id, file_name, mime_type, caption_md, overlays_json, sort_order "
                "FROM scene_illustration WHERE scene_id = ? ORDER BY sort_order, id",
                (scene_id,),
            ).fetchall()
        return [illustration_public(row) for row in rows]

    def create_illustration(self, scene_id: int, body: dict) -> dict:
        raw_b64 = str(body.get("content_base64", "")).strip()
        if not raw_b64:
            raise ValueError("이미지 파일이 비어 있습니다.")
        if "," in raw_b64 and raw_b64.lower().startswith("data:"):
            header, raw_b64 = raw_b64.split(",", 1)
            mime_from_data = "image/jpeg"
            match = re.search(r"data:([^;]+);", header)
            if match:
                mime_from_data = match.group(1).lower()
        else:
            mime_from_data = str(body.get("mime_type", "image/jpeg")).lower()
        try:
            data = base64.b64decode(raw_b64, validate=False)
        except (binascii.Error, ValueError) as error:
            raise ValueError("이미지를 읽지 못했습니다.") from error
        if not data:
            raise ValueError("이미지 파일이 비어 있습니다.")
        if len(data) > MAX_ILLUSTRATION_BYTES:
            raise ValueError(f"이미지가 너무 큽니다. {MAX_ILLUSTRATION_BYTES // (1024 * 1024)}MB 이하만 넣을 수 있어요.")
        mime_type = mime_from_data if mime_from_data in ALLOWED_IMAGE_TYPES else str(body.get("mime_type", "")).lower()
        if mime_type not in ALLOWED_IMAGE_TYPES:
            # sniff simple magic numbers
            if data[:3] == b"\xff\xd8\xff":
                mime_type = "image/jpeg"
            elif data[:8] == b"\x89PNG\r\n\x1a\n":
                mime_type = "image/png"
            elif data[:6] in (b"GIF87a", b"GIF89a"):
                mime_type = "image/gif"
            elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
                mime_type = "image/webp"
            else:
                raise ValueError("JPG, PNG, WEBP, GIF 이미지만 넣을 수 있어요.")
        extension = ALLOWED_IMAGE_TYPES[mime_type]
        caption = str(body.get("caption_md", "")).strip()[:500]
        overlays = parse_overlays(body.get("overlays", []))

        with database() as connection:
            scene = connection.execute(
                "SELECT id, project_id FROM scene WHERE id = ? AND deleted_at IS NULL",
                (scene_id,),
            ).fetchone()
            if scene is None:
                raise ValueError("씬을 찾을 수 없습니다.")
            project_id = int(scene["project_id"])
            sort_order = connection.execute(
                "SELECT COALESCE(MAX(sort_order) + 1, 0) FROM scene_illustration WHERE scene_id = ?",
                (scene_id,),
            ).fetchone()[0]
            file_name = f"{uuid.uuid4().hex}{extension}"
            target = illustration_dir_for(project_id) / file_name
            target.write_bytes(data)
            cursor = connection.execute(
                "INSERT INTO scene_illustration(project_id, scene_id, file_name, mime_type, caption_md, overlays_json, sort_order) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (project_id, scene_id, file_name, mime_type, caption, json.dumps(overlays, ensure_ascii=False), sort_order),
            )
            illustration_id = int(cursor.lastrowid)
            row = connection.execute(
                "SELECT id, project_id, scene_id, file_name, mime_type, caption_md, overlays_json, sort_order "
                "FROM scene_illustration WHERE id = ?",
                (illustration_id,),
            ).fetchone()
        return illustration_public(row)

    def update_illustration(self, illustration_id: int, body: dict) -> dict:
        with database() as connection:
            row = connection.execute(
                "SELECT id, project_id, scene_id, file_name, mime_type, caption_md, overlays_json, sort_order "
                "FROM scene_illustration WHERE id = ?",
                (illustration_id,),
            ).fetchone()
            if row is None:
                raise ValueError("삽화를 찾을 수 없습니다.")
            caption = row["caption_md"]
            overlays_json = row["overlays_json"]
            sort_order = row["sort_order"]
            if "caption_md" in body:
                caption = str(body.get("caption_md", "")).strip()[:500]
            if "overlays" in body:
                overlays_json = json.dumps(parse_overlays(body.get("overlays")), ensure_ascii=False)
            if "sort_order" in body:
                try:
                    sort_order = max(0, int(body.get("sort_order", 0)))
                except (TypeError, ValueError) as error:
                    raise ValueError("정렬 순서가 올바르지 않습니다.") from error
            connection.execute(
                "UPDATE scene_illustration SET caption_md = ?, overlays_json = ?, sort_order = ?, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                (caption, overlays_json, sort_order, illustration_id),
            )
            updated = connection.execute(
                "SELECT id, project_id, scene_id, file_name, mime_type, caption_md, overlays_json, sort_order "
                "FROM scene_illustration WHERE id = ?",
                (illustration_id,),
            ).fetchone()
        return illustration_public(updated)

    def delete_illustration(self, illustration_id: int) -> None:
        with database() as connection:
            row = connection.execute(
                "SELECT id, project_id, file_name FROM scene_illustration WHERE id = ?",
                (illustration_id,),
            ).fetchone()
            if row is None:
                raise ValueError("삽화를 찾을 수 없습니다.")
            connection.execute("DELETE FROM scene_illustration WHERE id = ?", (illustration_id,))
            path = illustration_dir_for(int(row["project_id"])) / row["file_name"]
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def serve_illustration_image(self, illustration_id: int) -> None:
        with database() as connection:
            row = connection.execute(
                "SELECT project_id, file_name, mime_type FROM scene_illustration WHERE id = ?",
                (illustration_id,),
            ).fetchone()
        if row is None:
            raise ValueError("삽화를 찾을 수 없습니다.")
        path = illustration_dir_for(int(row["project_id"])) / row["file_name"]
        if not path.is_file():
            raise ValueError("삽화 이미지 파일을 찾을 수 없습니다.")
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", row["mime_type"] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    def _character_has_portrait_columns(self, connection: sqlite3.Connection) -> bool:
        cols = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(character)").fetchall()
        }
        return "portrait_file" in cols

    def _serialize_character_row(self, row: sqlite3.Row | dict) -> dict:
        item = as_dict(row)
        portrait_file = str(item.get("portrait_file") or "").strip()
        item["portrait_file"] = portrait_file
        item["portrait_mime"] = str(item.get("portrait_mime") or "").strip()
        item["portrait_url"] = (
            f"/api/characters/{item['id']}/portrait?v={item.get('row_version') or 1}"
            if portrait_file
            else ""
        )
        return item

    def character_detail(self, character_id: int) -> dict:
        with database() as connection:
            has_portrait = self._character_has_portrait_columns(connection)
            portrait_cols = ", portrait_file, portrait_mime" if has_portrait else ""
            character = connection.execute(
                "SELECT id, project_id, name, sort_name, role, short_description, profile_md, "
                f"strengths_md, weaknesses_md, author_notes_md, row_version{portrait_cols} "
                "FROM character WHERE id = ? AND deleted_at IS NULL",
                (character_id,),
            ).fetchone()
            if character is None:
                raise ValueError("캐릭터를 찾을 수 없습니다.")
            aliases = connection.execute(
                "SELECT id, alias, alias_type FROM character_alias WHERE character_id = ? ORDER BY id", (character_id,)
            ).fetchall()
            char_data = self._serialize_character_row(character)
            if not has_portrait:
                char_data["portrait_file"] = ""
                char_data["portrait_mime"] = ""
                char_data["portrait_url"] = ""
        return {"character": char_data, "aliases": [as_dict(alias) for alias in aliases]}

    def trash_project(self, project_id: int) -> dict:
        """Soft-delete a whole work so it leaves the project picker (admin)."""
        with database() as connection:
            row = connection.execute(
                "SELECT id, title FROM project WHERE id = ? AND deleted_at IS NULL",
                (project_id,),
            ).fetchone()
            if row is None:
                raise ValueError("작품을 찾을 수 없습니다. 이미 삭제되었을 수 있어요.")
            connection.execute(
                "UPDATE project SET deleted_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE id = ?",
                (project_id,),
            )
        return {
            "ok": True,
            "id": int(project_id),
            "title": row["title"],
        }

    def trash_character(self, character_id: int) -> dict:
        """Soft-delete a character and detach scene memberships."""
        with database() as connection:
            row = connection.execute(
                "SELECT id, project_id, name FROM character WHERE id = ? AND deleted_at IS NULL",
                (character_id,),
            ).fetchone()
            if row is None:
                raise ValueError("캐릭터를 찾을 수 없습니다. 이미 삭제되었을 수 있어요.")
            connection.execute(
                "UPDATE character SET deleted_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE id = ?",
                (character_id,),
            )
            connection.execute(
                "DELETE FROM scene_character WHERE character_id = ?",
                (character_id,),
            )
        return {
            "ok": True,
            "id": int(character_id),
            "project_id": int(row["project_id"]),
            "name": row["name"],
        }

    def save_character_portrait(self, character_id: int, body: dict) -> dict:
        filename = str(body.get("filename") or body.get("file_name") or "portrait.jpg").strip()
        mime = str(body.get("mime_type") or body.get("mime") or "image/jpeg").strip() or "image/jpeg"
        raw_b64 = str(body.get("content_base64") or body.get("data") or "").strip()
        if not raw_b64:
            raise ValueError("이미지 데이터가 비어 있어요.")
        if "," in raw_b64 and raw_b64.lower().startswith("data:"):
            raw_b64 = raw_b64.split(",", 1)[1]
        try:
            data = base64.b64decode(raw_b64, validate=False)
        except (binascii.Error, ValueError) as error:
            raise ValueError("이미지 데이터를 읽지 못했어요.") from error
        if not data:
            raise ValueError("이미지 데이터가 비어 있어요.")
        if len(data) > 12 * 1024 * 1024:
            raise ValueError("이미지는 12MB 이하로 올려 주세요.")
        if not mime.startswith("image/"):
            raise ValueError("이미지 파일만 올릴 수 있어요.")
        ext = Path(filename).suffix.lower()
        if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            ext = {
                "image/jpeg": ".jpg",
                "image/png": ".png",
                "image/webp": ".webp",
                "image/gif": ".gif",
            }.get(mime, ".jpg")
        with database() as connection:
            if not self._character_has_portrait_columns(connection):
                raise ValueError("캐릭터 이미지 기능을 쓰려면 앱을 다시 시작해 주세요.")
            row = connection.execute(
                "SELECT id, project_id, portrait_file, row_version FROM character "
                "WHERE id = ? AND deleted_at IS NULL",
                (character_id,),
            ).fetchone()
            if row is None:
                raise ValueError("캐릭터를 찾을 수 없습니다.")
            project_id = int(row["project_id"])
            old_name = str(row["portrait_file"] or "").strip()
            file_name = f"char-{character_id}{ext}"
            target = character_portrait_dir_for(project_id) / file_name
            target.write_bytes(data)
            if old_name and old_name != file_name:
                old_path = character_portrait_dir_for(project_id) / old_name
                try:
                    if old_path.is_file():
                        old_path.unlink()
                except OSError:
                    pass
            connection.execute(
                "UPDATE character SET portrait_file = ?, portrait_mime = ?, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), "
                "row_version = row_version + 1 "
                "WHERE id = ?",
                (file_name, mime, character_id),
            )
            version = connection.execute(
                "SELECT row_version FROM character WHERE id = ?", (character_id,)
            ).fetchone()
        return {
            "ok": True,
            "id": character_id,
            "portrait_file": file_name,
            "portrait_mime": mime,
            "portrait_url": f"/api/characters/{character_id}/portrait?v={int(version['row_version']) if version else 1}",
            "row_version": int(version["row_version"]) if version else 1,
        }

    def clear_character_portrait(self, character_id: int) -> dict:
        with database() as connection:
            if not self._character_has_portrait_columns(connection):
                raise ValueError("캐릭터 이미지 기능을 쓰려면 앱을 다시 시작해 주세요.")
            row = connection.execute(
                "SELECT id, project_id, portrait_file FROM character "
                "WHERE id = ? AND deleted_at IS NULL",
                (character_id,),
            ).fetchone()
            if row is None:
                raise ValueError("캐릭터를 찾을 수 없습니다.")
            old_name = str(row["portrait_file"] or "").strip()
            if old_name:
                path = character_portrait_dir_for(int(row["project_id"])) / old_name
                try:
                    if path.is_file():
                        path.unlink()
                except OSError:
                    pass
            connection.execute(
                "UPDATE character SET portrait_file = '', portrait_mime = '', "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), "
                "row_version = row_version + 1 "
                "WHERE id = ?",
                (character_id,),
            )
        return {"ok": True, "id": character_id, "portrait_url": ""}

    def send_character_portrait(self, character_id: int) -> None:
        with database() as connection:
            if not self._character_has_portrait_columns(connection):
                raise ValueError("캐릭터 이미지를 찾을 수 없습니다.")
            row = connection.execute(
                "SELECT project_id, portrait_file, portrait_mime FROM character "
                "WHERE id = ? AND deleted_at IS NULL",
                (character_id,),
            ).fetchone()
            if row is None or not str(row["portrait_file"] or "").strip():
                raise ValueError("캐릭터 이미지를 찾을 수 없습니다.")
            path = character_portrait_dir_for(int(row["project_id"])) / row["portrait_file"]
            if not path.is_file():
                raise ValueError("캐릭터 이미지 파일을 찾을 수 없습니다.")
            data = path.read_bytes()
            mime = str(row["portrait_mime"] or "image/jpeg")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    # ── Project auto-index (scene_summary + project_index) ───────────────

    @staticmethod
    def _parse_json_list(raw: object, fallback: list | None = None) -> list:
        if fallback is None:
            fallback = []
        if isinstance(raw, list):
            return raw
        text = str(raw or "").strip()
        if not text:
            return list(fallback)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return list(fallback)
        return data if isinstance(data, list) else list(fallback)

    @staticmethod
    def _parse_json_object(raw: object) -> dict:
        if isinstance(raw, dict):
            return raw
        text = str(raw or "").strip()
        if not text:
            return {}
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _extract_json_object(text: str) -> dict:
        cleaned = str(text or "").strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            data = json.loads(cleaned)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if not match:
            raise ValueError("인덱싱 응답에서 JSON을 찾지 못했습니다.")
        data = json.loads(match.group(0))
        if not isinstance(data, dict):
            raise ValueError("인덱싱 응답 JSON 형식이 올바르지 않습니다.")
        return data

    @staticmethod
    def build_scene_summary_prompt(scene_content: str, previous_context: str) -> str:
        body = str(scene_content or "")
        context = str(previous_context or "").strip() or "(누적 맥락 없음)"
        return (
            "[현재 작업]\n"
            "아래 회차 내용을 인덱싱용으로 구조화해 요약하세요. 이 결과는 작가에게\n"
            "보여지는 것이 아니라, 이후 다른 기능들이 참고할 내부 데이터로 사용됩니다.\n\n"
            "[출력 형식 - 반드시 JSON만 출력]\n"
            "{\n"
            '  "event_summary": "이 회차에서 일어난 핵심 사건 (100자 내외)",\n'
            '  "characters_involved": ["등장한 인물 이름들"],\n'
            '  "new_world_facts": ["이 회차에서 새로 확립된 설정이 있다면 나열, 없으면 빈 배열"],\n'
            '  "new_threads": ["이 회차에서 새로 생긴 복선/떡밥, 없으면 빈 배열"],\n'
            '  "resolved_threads": ["이 회차에서 회수된 복선/떡밥, 없으면 빈 배열"]\n'
            "}\n\n"
            "[판단 기준]\n"
            "1. 사실만 추출한다. 해석이나 평가를 덧붙이지 않는다.\n"
            '2. 이전 맥락과 비교해 "새로 생긴 것"과 "이미 있던 것"을 구분한다.\n'
            "3. JSON 외의 텍스트(설명, 마크다운 코드블록 표시 등)는 출력하지 않는다.\n\n"
            "[이전까지의 누적 맥락 - 참고용]\n"
            f"{context}\n\n"
            "[이번 회차 본문]\n"
            f"{body}\n\n"
            "[JSON 출력]"
        )

    @staticmethod
    def build_index_merge_prompt(existing_index: dict, new_scene_summaries: list) -> str:
        existing_text = json.dumps(existing_index or {}, ensure_ascii=False, indent=2)
        summaries_text = json.dumps(new_scene_summaries or [], ensure_ascii=False, indent=2)
        return (
            "[현재 작업]\n"
            "기존 프로젝트 인덱스와 새로 생성된 회차 요약들을 통합해 "
            "갱신된 프로젝트 인덱스를 만드세요. "
            "결과는 내부 참고용이며 작가에게 그대로 보여주지 않습니다.\n\n"
            "[출력 형식 - 반드시 JSON만 출력]\n"
            "{\n"
            '  "characters": ["작품에 등장하는 인물 이름들"],\n'
            '  "world_rules": ["확립된 세계관·설정 규칙"],\n'
            '  "timeline": ["시간순 핵심 사건 요약"],\n'
            '  "open_threads": ["아직 회수되지 않은 복선/떡밥"]\n'
            "}\n\n"
            "[판단 기준]\n"
            "1. 사실만 유지한다. 해석·평가·추측을 넣지 않는다.\n"
            "2. 기존 인덱스의 정보를 보존하되, 새 요약의 신규 사실·회수된 떡밥을 반영한다.\n"
            "3. resolved_threads에 해당하는 항목은 open_threads에서 제거한다.\n"
            "4. new_world_facts와 new_threads의 내용이 실질적으로 같은 사실을 가리키면 "
            "중복으로 두 곳에 반영하지 말고, world_rules 또는 open_threads 중 "
            "더 적합한 한쪽에만 반영한다.\n"
            "5. JSON 외의 텍스트(설명, 마크다운 코드블록 표시 등)는 출력하지 않는다.\n\n"
            "[기존 프로젝트 인덱스]\n"
            f"{existing_text}\n\n"
            "[새로 추가할 회차 요약들]\n"
            f"{summaries_text}\n\n"
            "[JSON 출력]"
        )

    def _ensure_project_index_row(self, connection: sqlite3.Connection, project_id: int) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO project_index(project_id) VALUES (?)",
            (project_id,),
        )

    def _mark_project_index_dirty(
        self,
        connection: sqlite3.Connection,
        project_id: int,
        scene_id: int | None = None,
    ) -> None:
        self._ensure_project_index_row(connection, project_id)
        row = connection.execute(
            "SELECT pending_scene_ids_json FROM project_index WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        pending = self._parse_json_list(row["pending_scene_ids_json"] if row else "[]")
        pending_ids = []
        for item in pending:
            try:
                pending_ids.append(int(item))
            except (TypeError, ValueError):
                continue
        if scene_id is not None and int(scene_id) not in pending_ids:
            pending_ids.append(int(scene_id))
        connection.execute(
            "UPDATE project_index SET index_dirty = 1, pending_scene_ids_json = ?, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE project_id = ?",
            (json.dumps(pending_ids, ensure_ascii=False), project_id),
        )

    def _project_index_previous_context(self, connection: sqlite3.Connection, project_id: int) -> str:
        row = connection.execute(
            "SELECT characters_json, world_rules_json, timeline_json, open_threads_json "
            "FROM project_index WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        recent = connection.execute(
            "SELECT s.title, ss.summary FROM scene_summary ss "
            "JOIN scene s ON s.id = ss.scene_id "
            "WHERE s.project_id = ? AND s.deleted_at IS NULL "
            "ORDER BY ss.updated_at DESC, ss.scene_id DESC LIMIT 8",
            (project_id,),
        ).fetchall()
        parts: list[str] = []
        if row:
            chars = self._parse_json_list(row["characters_json"])
            rules = self._parse_json_list(row["world_rules_json"])
            timeline = self._parse_json_list(row["timeline_json"])
            threads = self._parse_json_list(row["open_threads_json"])
            if chars:
                parts.append("인물: " + ", ".join(str(c) for c in chars[:40]))
            if rules:
                parts.append("설정: " + " / ".join(str(r) for r in rules[:20]))
            if timeline:
                parts.append("타임라인: " + " → ".join(str(t) for t in timeline[:20]))
            if threads:
                parts.append("열린 떡밥: " + " / ".join(str(t) for t in threads[:20]))
        for item in reversed(list(recent)):
            summary_obj = self._parse_json_object(item["summary"])
            event = str(summary_obj.get("event_summary") or "").strip()
            title = str(item["title"] or "").strip() or "회차"
            if event:
                parts.append(f"{title}: {event}")
        return "\n".join(parts)

    def get_project_index(self, project_id: int) -> dict:
        with database() as connection:
            self.require_project(connection, project_id)
            self._ensure_project_index_row(connection, project_id)
            row = connection.execute(
                "SELECT project_id, characters_json, world_rules_json, timeline_json, "
                "open_threads_json, last_synced_scene_id, index_dirty, "
                "pending_scene_ids_json, updated_at "
                "FROM project_index WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        data = as_dict(row) or {}
        return {
            "project_id": project_id,
            "characters": self._parse_json_list(data.get("characters_json")),
            "world_rules": self._parse_json_list(data.get("world_rules_json")),
            "timeline": self._parse_json_list(data.get("timeline_json")),
            "open_threads": self._parse_json_list(data.get("open_threads_json")),
            "last_synced_scene_id": data.get("last_synced_scene_id"),
            "index_dirty": int(data.get("index_dirty") or 0),
            "pending_scene_ids": self._parse_json_list(data.get("pending_scene_ids_json")),
            "updated_at": data.get("updated_at"),
            "previous_context": self._project_index_previous_context_readonly(project_id),
        }

    def _project_index_previous_context_readonly(self, project_id: int) -> str:
        with database() as connection:
            self.require_project(connection, project_id)
            return self._project_index_previous_context(connection, project_id)

    def get_scene_summary(self, scene_id: int) -> dict:
        with database() as connection:
            scene = connection.execute(
                "SELECT id, project_id FROM scene WHERE id = ? AND deleted_at IS NULL",
                (scene_id,),
            ).fetchone()
            if scene is None:
                raise ValueError("회차를 찾을 수 없습니다.")
            row = connection.execute(
                "SELECT scene_id, summary, updated_at FROM scene_summary WHERE scene_id = ?",
                (scene_id,),
            ).fetchone()
        if row is None:
            return {"scene_id": scene_id, "summary": None, "updated_at": None}
        parsed = self._parse_json_object(row["summary"])
        return {
            "scene_id": scene_id,
            "summary": parsed or row["summary"],
            "summary_raw": row["summary"],
            "updated_at": row["updated_at"],
        }

    def upsert_scene_summary(self, scene_id: int, body: dict) -> dict:
        raw = body.get("summary")
        if isinstance(raw, dict):
            summary_obj = raw
        else:
            text = str(raw or "").strip()
            if not text:
                raise ValueError("요약 JSON이 비어 있습니다.")
            summary_obj = self._extract_json_object(text) if text[:1] != "{" else self._parse_json_object(text)
            if not summary_obj:
                summary_obj = self._extract_json_object(text)
        required = (
            "event_summary",
            "characters_involved",
            "new_world_facts",
            "new_threads",
            "resolved_threads",
        )
        for key in required:
            if key not in summary_obj:
                summary_obj[key] = [] if key != "event_summary" else ""
        summary_json = json.dumps(summary_obj, ensure_ascii=False)
        with database() as connection:
            scene = connection.execute(
                "SELECT id, project_id FROM scene WHERE id = ? AND deleted_at IS NULL",
                (scene_id,),
            ).fetchone()
            if scene is None:
                raise ValueError("회차를 찾을 수 없습니다.")
            project_id = int(scene["project_id"])
            connection.execute(
                "INSERT INTO scene_summary(scene_id, summary, updated_at) VALUES (?, ?, "
                "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) "
                "ON CONFLICT(scene_id) DO UPDATE SET "
                "summary = excluded.summary, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
                (scene_id, summary_json),
            )
            self._mark_project_index_dirty(connection, project_id, scene_id)
        return {
            "scene_id": scene_id,
            "project_id": project_id,
            "summary": summary_obj,
            "index_dirty": True,
        }

    def summarize_scene_for_index(self, scene_id: int, body: dict | None = None) -> dict:
        """Build scene summary via Gemini and upsert scene_summary (+ dirty queue)."""
        body = body or {}
        with database() as connection:
            scene = connection.execute(
                "SELECT s.id, s.project_id, s.title, r.content_md "
                "FROM scene s "
                "JOIN scene_revision r ON r.scene_id = s.id AND r.is_current = 1 "
                "WHERE s.id = ? AND s.deleted_at IS NULL",
                (scene_id,),
            ).fetchone()
            if scene is None:
                raise ValueError("회차를 찾을 수 없습니다.")
            project_id = int(scene["project_id"])
            content = str(body.get("content_md") or scene["content_md"] or "")
            content = plain_text_from_content(content).strip()
            if len(content) < 20:
                raise ValueError("인덱싱할 본문이 너무 짧습니다.")
            previous = str(body.get("previous_context") or "").strip()
            if not previous:
                previous = self._project_index_previous_context(connection, project_id)
            prompt = str(body.get("prompt") or "").strip()
            if not prompt:
                prompt = self.build_scene_summary_prompt(content, previous)

        try:
            raw = gemini_client.generate_text(prompt, temperature=0.2, max_output_tokens=2048)
        except gemini_client.GeminiError as error:
            raise ValueError(str(error)) from error

        summary_obj = self._extract_json_object(raw)
        result = self.upsert_scene_summary(scene_id, {"summary": summary_obj})
        result["model"] = gemini_client.model_name()
        result["raw_text"] = raw
        result["prompt_chars"] = len(prompt)
        return result

    def merge_project_index(self, project_id: int, body: dict | None = None) -> dict:
        """Merge pending scene_summary rows into project_index when dirty."""
        body = body or {}
        only_if_dirty = bool(body.get("only_if_dirty"))
        quiet = bool(body.get("quiet"))
        with database() as connection:
            self.require_project(connection, project_id)
            self._ensure_project_index_row(connection, project_id)
            row = connection.execute(
                "SELECT characters_json, world_rules_json, timeline_json, open_threads_json, "
                "index_dirty, pending_scene_ids_json, last_synced_scene_id "
                "FROM project_index WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            dirty = int(row["index_dirty"] or 0) if row else 0
            pending = self._parse_json_list(row["pending_scene_ids_json"] if row else "[]")
            if only_if_dirty and not dirty:
                return {"ok": True, "merged": False, "reason": "not_dirty", "project_id": project_id}
            if not pending:
                # Fallback: all summaries newer than last sync — or all summaries
                pending_rows = connection.execute(
                    "SELECT ss.scene_id, ss.summary FROM scene_summary ss "
                    "JOIN scene s ON s.id = ss.scene_id "
                    "WHERE s.project_id = ? AND s.deleted_at IS NULL "
                    "ORDER BY ss.updated_at, ss.scene_id",
                    (project_id,),
                ).fetchall()
            else:
                placeholders = ",".join("?" for _ in pending)
                pending_rows = connection.execute(
                    f"SELECT ss.scene_id, ss.summary FROM scene_summary ss "
                    f"WHERE ss.scene_id IN ({placeholders}) ORDER BY ss.updated_at, ss.scene_id",
                    [int(x) for x in pending],
                ).fetchall()
            if not pending_rows:
                connection.execute(
                    "UPDATE project_index SET index_dirty = 0, pending_scene_ids_json = '[]', "
                    "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE project_id = ?",
                    (project_id,),
                )
                return {"ok": True, "merged": False, "reason": "no_summaries", "project_id": project_id}

            existing_index = {
                "characters": self._parse_json_list(row["characters_json"]),
                "world_rules": self._parse_json_list(row["world_rules_json"]),
                "timeline": self._parse_json_list(row["timeline_json"]),
                "open_threads": self._parse_json_list(row["open_threads_json"]),
            }
            new_summaries = []
            last_scene_id = None
            for item in pending_rows:
                last_scene_id = int(item["scene_id"])
                parsed = self._parse_json_object(item["summary"])
                if parsed:
                    parsed = {**parsed, "scene_id": last_scene_id}
                    new_summaries.append(parsed)

        if not new_summaries:
            return {"ok": True, "merged": False, "reason": "empty_summaries", "project_id": project_id}

        prompt = str(body.get("prompt") or "").strip() or self.build_index_merge_prompt(
            existing_index, new_summaries
        )
        try:
            raw = gemini_client.generate_text(prompt, temperature=0.2, max_output_tokens=4096)
        except gemini_client.GeminiError as error:
            if quiet:
                return {"ok": False, "merged": False, "error": str(error), "project_id": project_id}
            raise ValueError(str(error)) from error

        try:
            merged = self._extract_json_object(raw)
        except (ValueError, json.JSONDecodeError) as error:
            if quiet:
                return {"ok": False, "merged": False, "error": str(error), "project_id": project_id}
            raise ValueError(str(error)) from error

        characters = merged.get("characters")
        world_rules = merged.get("world_rules")
        timeline = merged.get("timeline")
        open_threads = merged.get("open_threads")
        if not isinstance(characters, list):
            characters = existing_index["characters"]
        if not isinstance(world_rules, list):
            world_rules = existing_index["world_rules"]
        if not isinstance(timeline, list):
            timeline = existing_index["timeline"]
        if not isinstance(open_threads, list):
            open_threads = existing_index["open_threads"]

        with database() as connection:
            self._ensure_project_index_row(connection, project_id)
            connection.execute(
                "UPDATE project_index SET "
                "characters_json = ?, world_rules_json = ?, timeline_json = ?, "
                "open_threads_json = ?, last_synced_scene_id = ?, "
                "index_dirty = 0, pending_scene_ids_json = '[]', "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE project_id = ?",
                (
                    json.dumps(characters, ensure_ascii=False),
                    json.dumps(world_rules, ensure_ascii=False),
                    json.dumps(timeline, ensure_ascii=False),
                    json.dumps(open_threads, ensure_ascii=False),
                    last_scene_id,
                    project_id,
                ),
            )
        return {
            "ok": True,
            "merged": True,
            "project_id": project_id,
            "index": {
                "characters": characters,
                "world_rules": world_rules,
                "timeline": timeline,
                "open_threads": open_threads,
            },
            "merged_scene_ids": [int(s["scene_id"]) for s in new_summaries if "scene_id" in s],
            "model": gemini_client.model_name(),
        }

    def save_scene(self, scene_id: int, body: dict) -> dict:
        title = str(body.get("title", "")).strip()
        if not title:
            raise ValueError("씬 제목을 입력해 주세요.")
        status = str(body.get("status", "idea"))
        if status not in {"idea", "outline", "draft", "revision", "complete"}:
            raise ValueError("올바르지 않은 씬 상태입니다.")
        content = str(body.get("content_md", ""))
        expected_version = int(body.get("row_version", 0))
        goal_count = max(0, int(body.get("goal_word_count", 0) or 0))
        goal_metric = str(body.get("goal_metric", "chars_with_space") or "chars_with_space")
        if goal_metric not in GOAL_METRICS:
            raise ValueError("목표 글자 수 기준이 올바르지 않습니다.")
        save_note = str(body.get("save_note", "") or "").strip() or "저장"
        links_json = None
        if "reference_links" in body:
            links_json = json.dumps(parse_reference_links(body.get("reference_links")), ensure_ascii=False)
        with database() as connection:
            scene = connection.execute(
                "SELECT row_version FROM scene WHERE id = ? AND deleted_at IS NULL", (scene_id,)
            ).fetchone()
            if scene is None:
                raise ValueError("씬을 찾을 수 없습니다.")
            if expected_version and scene["row_version"] != expected_version:
                raise ValueError("다른 화면에서 이 씬이 변경되었습니다. 새로 열고 다시 저장해 주세요.")
            if links_json is None:
                connection.execute(
                    "UPDATE scene SET title = ?, synopsis_md = ?, notes_md = ?, status = ?, "
                    "goal_word_count = ?, goal_metric = ?, row_version = row_version + 1 WHERE id = ?",
                    (title, str(body.get("synopsis_md", "")), str(body.get("notes_md", "")), status,
                     goal_count, goal_metric, scene_id),
                )
            else:
                connection.execute(
                    "UPDATE scene SET title = ?, synopsis_md = ?, notes_md = ?, status = ?, "
                    "goal_word_count = ?, goal_metric = ?, reference_links_json = ?, "
                    "row_version = row_version + 1 WHERE id = ?",
                    (title, str(body.get("synopsis_md", "")), str(body.get("notes_md", "")), status,
                     goal_count, goal_metric, links_json, scene_id),
                )
            current = connection.execute(
                "SELECT id, revision_no, content_md, word_count FROM scene_revision "
                "WHERE scene_id = ? AND is_current = 1",
                (scene_id,),
            ).fetchone()
            if current is None:
                raise ValueError("현재 원고를 찾을 수 없습니다.")
            revision_no = int(current["revision_no"])
            words = int(current["word_count"] or 0)
            if current["content_md"] != content:
                words = word_count(content)
                cursor = connection.execute(
                    "INSERT INTO scene_revision(scene_id, revision_no, content_md, word_count, save_note, is_current) "
                    "VALUES (?, ?, ?, ?, ?, 0)",
                    (scene_id, current["revision_no"] + 1, content, words, save_note),
                )
                connection.execute(
                    "UPDATE scene_revision SET is_current = CASE "
                    "WHEN id = ? THEN 1 WHEN id = ? THEN 0 ELSE is_current END "
                    "WHERE id IN (?, ?)",
                    (cursor.lastrowid, current["id"], cursor.lastrowid, current["id"]),
                )
                revision_no = int(current["revision_no"]) + 1
            row = connection.execute(
                "SELECT row_version FROM scene WHERE id = ?", (scene_id,)
            ).fetchone()
            return {
                "ok": True,
                "row_version": int(row["row_version"]),
                "revision_no": revision_no,
                "word_count": words,
            }

    def save_scene_characters(self, scene_id: int, body: dict) -> None:
        character_ids = [int(value) for value in body.get("character_ids", [])]
        pov_id = body.get("pov_id")
        pov_id = int(pov_id) if pov_id not in (None, "") else None
        if pov_id is not None and pov_id not in character_ids:
            raise ValueError("시점 인물은 등장인물 중에서 골라 주세요.")
        with database() as connection:
            scene = connection.execute(
                "SELECT project_id FROM scene WHERE id = ? AND deleted_at IS NULL", (scene_id,)
            ).fetchone()
            if scene is None:
                raise ValueError("씬을 찾을 수 없습니다.")
            valid_count = connection.execute(
                "SELECT COUNT(*) FROM character WHERE project_id = ? AND deleted_at IS NULL "
                f"AND id IN ({','.join('?' for _ in character_ids) or 'NULL'})",
                (scene["project_id"], *character_ids),
            ).fetchone()[0]
            if valid_count != len(set(character_ids)):
                raise ValueError("다른 소설의 캐릭터는 연결할 수 없습니다.")
            connection.execute("DELETE FROM scene_character WHERE scene_id = ?", (scene_id,))
            for character_id in dict.fromkeys(character_ids):
                connection.execute(
                    "INSERT INTO scene_character(scene_id, character_id, project_id, appearance_role, is_pov) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (scene_id, character_id, scene["project_id"], "primary" if character_id == pov_id else "supporting",
                     1 if character_id == pov_id else 0),
                )

    def save_character(self, character_id: int, body: dict) -> None:
        name = str(body.get("name", "")).strip()
        if not name:
            raise ValueError("캐릭터 이름을 입력해 주세요.")
        role = str(body.get("role", "supporting"))
        if role not in {"protagonist", "antagonist", "supporting", "minor"}:
            raise ValueError("올바르지 않은 캐릭터 역할입니다.")
        expected_version = int(body.get("row_version", 0))
        with database() as connection:
            character = connection.execute(
                "SELECT row_version FROM character WHERE id = ? AND deleted_at IS NULL", (character_id,)
            ).fetchone()
            if character is None:
                raise ValueError("캐릭터를 찾을 수 없습니다.")
            if expected_version and character["row_version"] != expected_version:
                raise ValueError("다른 화면에서 이 캐릭터가 변경되었습니다. 새로 열고 다시 저장해 주세요.")
            connection.execute(
                "UPDATE character SET name = ?, sort_name = ?, role = ?, short_description = ?, "
                "profile_md = ?, strengths_md = ?, weaknesses_md = ?, author_notes_md = ? "
                "WHERE id = ?",
                (
                    name,
                    str(body.get("sort_name", "")),
                    role,
                    str(body.get("short_description", "")),
                    str(body.get("profile_md", "")),
                    str(body.get("strengths_md", "")),
                    str(body.get("weaknesses_md", "")),
                    str(body.get("author_notes_md", "")),
                    character_id,
                ),
            )

    def _list_episode_candidates(self, connection: sqlite3.Connection, project_id: int) -> list[chapter_match.EpisodeCandidate]:
        """Active scenes in reading order with first-100-char body previews."""
        rows = connection.execute(
            "SELECT s.id AS scene_id, s.chapter_id, s.title AS scene_title, "
            "c.title AS chapter_title, c.sort_order AS chapter_sort, s.sort_order AS scene_sort, "
            "r.content_md "
            "FROM scene s "
            "JOIN chapter c ON c.id = s.chapter_id AND c.deleted_at IS NULL "
            "JOIN scene_revision r ON r.scene_id = s.id AND r.is_current = 1 "
            "WHERE s.project_id = ? AND s.deleted_at IS NULL "
            "ORDER BY c.sort_order, c.id, s.sort_order, s.id",
            (project_id,),
        ).fetchall()
        episodes: list[chapter_match.EpisodeCandidate] = []
        for index, row in enumerate(rows, start=1):
            plain = plain_text_from_content(row["content_md"] or "")
            episodes.append(
                chapter_match.EpisodeCandidate(
                    scene_id=int(row["scene_id"]),
                    chapter_id=int(row["chapter_id"]),
                    episode_number=index,
                    title=str(row["scene_title"] or "") or f"{index}화",
                    preview=chapter_match.plain_preview(plain, 100),
                    chapter_title=str(row["chapter_title"] or ""),
                )
            )
        return episodes

    def match_project_episode(self, project_id: int, body: dict) -> dict:
        """Find the existing 회차 (scene) that best matches uploaded / pasted text."""
        use_ai = str(body.get("use_ai", "1")).lower() not in {"0", "false", "no"}
        target_text = str(body.get("target_text") or body.get("text") or "").strip()
        target_title = str(body.get("target_title") or body.get("title") or "").strip()
        filename = str(body.get("filename", "")).strip()

        if not target_text and body.get("content_base64"):
            raw_b64 = str(body.get("content_base64", "")).strip()
            if "," in raw_b64 and raw_b64.lower().startswith("data:"):
                raw_b64 = raw_b64.split(",", 1)[1]
            try:
                data = base64.b64decode(raw_b64, validate=False)
            except (binascii.Error, ValueError) as error:
                raise ValueError("파일을 읽지 못했습니다. 다시 선택해 주세요.") from error
            if not filename:
                filename = "upload.txt"
            extracted = document_import.extract_document(filename, data)
            target_text = extracted.text
            if not target_title:
                target_title = extracted.title

        if not target_text.strip():
            raise ValueError("비교할 원고 텍스트가 비어 있습니다.")

        with database() as connection:
            self.require_project(connection, project_id)
            episodes = self._list_episode_candidates(connection, project_id)

        result = chapter_match.match_episode(
            target_text,
            episodes,
            target_title=target_title,
            use_ai=use_ai,
        )
        payload = result.to_dict()
        payload["episode_count"] = len(episodes)
        payload["target_title"] = target_title
        payload["target_preview"] = chapter_match.plain_preview(target_text, 160)
        return payload

    def _extract_upload_text(self, body: dict) -> tuple[str, str]:
        """Return (text, title) from target_text or filename+content_base64."""
        target_text = str(body.get("target_text") or body.get("text") or body.get("revised_text") or "").strip()
        target_title = str(body.get("target_title") or body.get("title") or "").strip()
        filename = str(body.get("filename", "")).strip()
        if target_text:
            return target_text, target_title
        raw_b64 = str(body.get("content_base64", "")).strip()
        if not raw_b64:
            return "", target_title
        if "," in raw_b64 and raw_b64.lower().startswith("data:"):
            raw_b64 = raw_b64.split(",", 1)[1]
        try:
            data = base64.b64decode(raw_b64, validate=False)
        except (binascii.Error, ValueError) as error:
            raise ValueError("파일을 읽지 못했습니다. 다시 선택해 주세요.") from error
        if not filename:
            filename = "upload.txt"
        extracted = document_import.extract_document(filename, data)
        if not target_title:
            target_title = extracted.title
        return extracted.text, target_title

    def extract_reference_text(self, body: dict) -> dict:
        """
        Extract plain text from an uploaded reference file for the side viewer.
        Supports txt/md/docx/hwpx (+ hwp via proof_extract when available).
        PDF is intended for browser-native viewing (no server extract).
        """
        filename, data = self._decode_upload_bytes(body)
        ext = Path(filename).suffix.lower()
        if ext == ".pdf":
            return {
                "filename": filename,
                "format": "pdf",
                "title": Path(filename).stem or "PDF",
                "text": "",
                "viewer": "pdf",
                "warnings": ["PDF는 브라우저 뷰어로 엽니다. 텍스트 추출은 하지 않습니다."],
            }
        if ext in {".txt", ".text", ".md", ".markdown", ".csv"}:
            text = document_import.normalise_whitespace(document_import.decode_text_bytes(data))
            return {
                "filename": filename,
                "format": ext.lstrip(".") or "txt",
                "title": Path(filename).stem or "텍스트",
                "text": text[:200_000],
                "viewer": "text",
                "warnings": [],
            }
        # Prefer proof_extract for binary HWP / DOCX when available
        if ext in {".hwp", ".docx", ".hwpx"}:
            try:
                unified = proof_extract.extract_proof_document(filename, data)
                text = str(getattr(unified, "text", "") or "").strip()
                if not text and hasattr(unified, "to_dict"):
                    text = str((unified.to_dict() or {}).get("text") or "").strip()
                if text:
                    return {
                        "filename": filename,
                        "format": ext.lstrip("."),
                        "title": Path(filename).stem or "문서",
                        "text": text[:200_000],
                        "viewer": "text",
                        "warnings": list(getattr(unified, "warnings", None) or [])[:8],
                    }
            except Exception:
                # Fall through to document_import / error message
                pass
        try:
            extracted = document_import.extract_document(filename, data)
            return {
                "filename": filename,
                "format": extracted.format_name,
                "title": extracted.title,
                "text": (extracted.text or "")[:200_000],
                "viewer": "text",
                "warnings": list(extracted.warnings or [])[:8],
            }
        except ValueError as error:
            raise ValueError(str(error)) from error

    def clean_proof_text_api(self, body: dict) -> dict:
        """Strip editor/typesetting junk from HWP proof extract → pure body."""
        raw = str(body.get("text") or body.get("target_text") or body.get("revised_text") or "").strip()
        if not raw and body.get("content_base64"):
            raw, _title = self._extract_upload_text(body)
        if not str(raw or "").strip():
            raise ValueError("정제할 텍스트가 비어 있습니다.")
        do_clean = str(body.get("clean", "1")).lower() not in {"0", "false", "no"}
        if not do_clean:
            return {"clean_full_text": str(raw)}
        result = proof_clean.clean_to_dict(raw)
        # Optional: also return char stats for UI
        cleaned = result["clean_full_text"]
        result["source_chars"] = len(str(raw))
        result["clean_chars"] = len(cleaned)
        return result

    def _decode_upload_bytes(self, body: dict) -> tuple[str, bytes]:
        filename = str(body.get("filename", "")).strip() or "upload.bin"
        raw_b64 = str(body.get("content_base64", "")).strip()
        if not raw_b64:
            raise ValueError("파일 내용이 비어 있습니다.")
        if "," in raw_b64 and raw_b64.lower().startswith("data:"):
            raw_b64 = raw_b64.split(",", 1)[1]
        try:
            data = base64.b64decode(raw_b64, validate=False)
        except (binascii.Error, ValueError) as error:
            raise ValueError("파일을 읽지 못했습니다. 다시 선택해 주세요.") from error
        return filename, data

    def run_proof_pipeline_api(self, project_id: int, body: dict) -> dict:
        """
        HWP/DOCX → unified extract → (1) match (2) clean (3) proof-diff.
        Optional apply_clean: write clean_full_text onto matched scene.
        """
        use_ai = str(body.get("use_ai", "1")).lower() not in {"0", "false", "no"}
        apply_clean = str(body.get("apply_clean", "0")).lower() in {"1", "true", "yes"}
        scene_hint = body.get("scene_id")
        try:
            scene_hint_id = int(scene_hint) if scene_hint not in (None, "") else None
        except (TypeError, ValueError):
            scene_hint_id = None

        filename, data = self._decode_upload_bytes(body)

        with database() as connection:
            self.require_project(connection, project_id)
            episodes = self._list_episode_candidates(connection, project_id)

            original_text = str(body.get("original_text") or "").strip()
            # Resolve original from hint or leave for pipeline match first
            if not original_text and scene_hint_id is not None:
                row = connection.execute(
                    "SELECT r.content_md FROM scene s "
                    "JOIN scene_revision r ON r.scene_id = s.id AND r.is_current = 1 "
                    "WHERE s.id = ? AND s.project_id = ? AND s.deleted_at IS NULL",
                    (scene_hint_id, project_id),
                ).fetchone()
                if row:
                    original_text = plain_text_from_content(row["content_md"] or "")

        result = proof_pipeline.run_proof_pipeline(
            filename=filename,
            data=data,
            episodes=episodes,
            original_text=original_text or None,
            use_ai=use_ai,
            scene_id_hint=scene_hint_id,
        )

        # If original was empty, load from matched scene and re-run step 3 only
        match = result.get("step1_match") or {}
        matched_id = match.get("matched_scene_id")
        if not original_text and matched_id and not (result.get("step3_proof") or {}).get("diff_chunks"):
            with database() as connection:
                row = connection.execute(
                    "SELECT content_md FROM scene_revision "
                    "WHERE scene_id = ? AND is_current = 1",
                    (int(matched_id),),
                ).fetchone()
            if row:
                original_text = plain_text_from_content(row["content_md"] or "")
                clean_text = (result.get("step2_clean") or {}).get("clean_full_text") or ""
                if original_text and clean_text:
                    report = proof_diff.analyze_proof(original_text, clean_text, use_ai=use_ai)
                    step3 = report.to_dict()
                    # merge extract memos
                    for memo in (result.get("extract") or {}).get("memos") or []:
                        step3.setdefault("editor_memos", []).append(memo)
                    step3["summary"]["editor_memos_count"] = len(step3.get("editor_memos") or [])
                    step3["clean_full_text"] = clean_text
                    step3["scene_id"] = int(matched_id)
                    result["step3_proof"] = step3

        if matched_id:
            result["scene_id"] = int(matched_id)

        applied = False
        if apply_clean and matched_id:
            clean_text = result.get("clean_full_text") or ""
            if not clean_text.strip():
                raise ValueError("정제된 본문이 비어 덮어쓸 수 없습니다.")
            with database() as connection:
                scene = connection.execute(
                    "SELECT id, project_id FROM scene WHERE id = ? AND deleted_at IS NULL",
                    (int(matched_id),),
                ).fetchone()
                if scene is None or int(scene["project_id"]) != project_id:
                    raise ValueError("매칭된 회차를 찾을 수 없습니다.")
                self._write_scene_content(
                    connection,
                    int(matched_id),
                    clean_text,
                    save_note=f"교정 파이프라인 반영: {filename}",
                )
                connection.execute(
                    "UPDATE scene SET status = CASE WHEN status = 'idea' THEN 'draft' ELSE status END WHERE id = ?",
                    (int(matched_id),),
                )
            applied = True
        result["applied"] = applied
        return result

    def proof_diff_project(self, project_id: int, body: dict) -> dict:
        """Build a 교정/교열 report comparing SuperTORY original vs editor revised text."""
        use_ai = str(body.get("use_ai", "1")).lower() not in {"0", "false", "no"}
        original_text = str(body.get("original_text") or "").strip()
        revised_text = str(body.get("revised_text") or body.get("target_text") or "").strip()
        scene_id = body.get("scene_id")
        match_info: dict | None = None
        auto_clean = str(body.get("clean", "1")).lower() not in {"0", "false", "no"}

        # Revised may arrive as file upload
        if not revised_text and body.get("content_base64"):
            revised_text, _title = self._extract_upload_text(body)

        pre_memos: list = []
        if revised_text and auto_clean:
            # Pull editor comments first, then strip HWP junk for pure-body diff.
            _raw_for_memo, pre_memos = proof_diff.extract_memos(revised_text)
            revised_text = proof_clean.clean_proof_text(revised_text)

        with database() as connection:
            self.require_project(connection, project_id)

            # Resolve original from scene_id, or auto-match from revised text
            if original_text:
                pass
            elif scene_id not in (None, ""):
                scene_id = int(scene_id)
                row = connection.execute(
                    "SELECT s.id, s.title, s.project_id, r.content_md "
                    "FROM scene s "
                    "JOIN scene_revision r ON r.scene_id = s.id AND r.is_current = 1 "
                    "WHERE s.id = ? AND s.deleted_at IS NULL",
                    (scene_id,),
                ).fetchone()
                if row is None or int(row["project_id"]) != project_id:
                    raise ValueError("원본 회차를 찾을 수 없습니다.")
                original_text = plain_text_from_content(row["content_md"] or "")
            elif revised_text:
                episodes = self._list_episode_candidates(connection, project_id)
                matched = chapter_match.match_episode(
                    revised_text,
                    episodes,
                    target_title=str(body.get("target_title") or body.get("title") or ""),
                    use_ai=use_ai,
                )
                match_info = matched.to_dict()
                if matched.matched_scene_id is None:
                    raise ValueError(
                        matched.match_reason
                        or "교정고와 맞는 원본 회차를 찾지 못했습니다. 회차를 연 뒤 다시 시도해 주세요."
                    )
                scene_id = matched.matched_scene_id
                row = connection.execute(
                    "SELECT content_md FROM scene_revision "
                    "WHERE scene_id = ? AND is_current = 1",
                    (scene_id,),
                ).fetchone()
                original_text = plain_text_from_content((row["content_md"] if row else "") or "")
            else:
                raise ValueError("교정 원고 텍스트나 파일을 넣어 주세요.")

        if not revised_text.strip():
            raise ValueError("편집자 교정 원고가 비어 있습니다.")
        if not original_text.strip():
            raise ValueError("원본 원고가 비어 있습니다. 해당 회차에 본문이 있는지 확인해 주세요.")

        report = proof_diff.analyze_proof(original_text, revised_text, use_ai=use_ai)
        payload = report.to_dict()
        # Merge memos extracted before cleaning (clean_proof_text drops them)
        if pre_memos:
            existing = {
                (m.get("location_context"), m.get("memo_content"))
                for m in payload.get("editor_memos") or []
                if isinstance(m, dict)
            }
            for memo in pre_memos:
                key = (memo.location_context, memo.memo_content)
                if key in existing:
                    continue
                payload.setdefault("editor_memos", []).append(memo.to_dict())
                existing.add(key)
            payload["summary"]["editor_memos_count"] = len(payload.get("editor_memos") or [])
        if auto_clean:
            payload["clean_full_text"] = revised_text
        if scene_id not in (None, ""):
            try:
                payload["scene_id"] = int(scene_id)
            except (TypeError, ValueError):
                payload["scene_id"] = scene_id
        if match_info is not None:
            payload["match"] = match_info
        return payload

    def import_document(self, body: dict, project_id: int | None = None) -> dict:
        filename = str(body.get("filename", "")).strip()
        if not filename:
            raise ValueError("파일 이름을 알려 주세요.")
        raw_b64 = str(body.get("content_base64", "")).strip()
        if not raw_b64:
            raise ValueError("파일 내용이 비어 있습니다.")
        # Allow data-URL prefixes from some browsers.
        if "," in raw_b64 and raw_b64.lower().startswith("data:"):
            raw_b64 = raw_b64.split(",", 1)[1]
        try:
            data = base64.b64decode(raw_b64, validate=False)
        except (binascii.Error, ValueError) as error:
            raise ValueError("파일을 읽지 못했습니다. 다시 선택해 주세요.") from error

        # HWP/DOCX 교정고: 통합 추출기(pyhwp / python-docx) 우선
        destination_early = str(body.get("destination", "new_chapter") or "new_chapter")
        document_kind = str(
            body.get("document_kind") or body.get("document_role") or ""
        ).strip().lower()
        is_proof_doc = bool(
            body.get("is_proof")
            or document_kind in {
                "proof",
                "editor_proof",
                "proof_copy",
                "교정본",
                "교정고",
            }
            or str(body.get("tory_task") or "").startswith("proof")
            or destination_early in {
                "match_replace_scene",
                "proof_compare",
                "proof_pipeline",
            }
        )
        ext_lower = Path(filename).suffix.lower()
        use_proof_extract = (
            ext_lower in {".hwp", ".docx", ".hwpx"}
            or is_proof_doc
            or destination_early in {
                "match_replace_scene",
                "proof_compare",
                "proof_pipeline",
            }
        )
        unified_extract: dict | None = None
        if use_proof_extract and ext_lower in {".hwp", ".docx", ".hwpx"}:
            try:
                unified = proof_extract.extract_proof_document(filename, data)
                unified_extract = unified.to_dict()
                extracted = document_import.ExtractedDocument(
                    title=unified.title or document_import.title_from_filename(filename),
                    text=unified.text,
                    format_name=unified.format,
                    warnings=tuple(unified.warnings),
                )
            except ValueError:
                if ext_lower == ".hwp":
                    raise
                extracted = document_import.extract_document(filename, data)
        else:
            extracted = document_import.extract_document(filename, data)

        split_mode = str(body.get("split", "none") or "none")
        destination = destination_early
        if destination not in {
            "new_chapter",
            "existing_chapter",
            "replace_scene",
            "match_replace_scene",
            "proof_compare",
            "proof_pipeline",
            "new_project",
        }:
            raise ValueError("가져오기 위치가 올바르지 않습니다.")

        # 교정고 자동 매칭/비교/파이프라인은 통째로 한 회차 단위.
        if destination in {"match_replace_scene", "proof_compare", "proof_pipeline"}:
            split_mode = "none"

        default_title = str(body.get("scene_title", "")).strip() or extracted.title
        plan = document_import.build_import_plan(extracted.text, split_mode, default_title)
        sections = plan.sections
        chapter_title = str(body.get("chapter_title", "")).strip() or extracted.title
        project_title = str(body.get("project_title", "")).strip() or extracted.title
        purpose = document_import.normalise_purpose(body.get("purpose"))

        if destination in {"replace_scene", "match_replace_scene", "proof_compare", "proof_pipeline"} and plan.section_count > 1:
            raise ValueError("현재 씬에 덮어쓸 때는 글을 나누지 않는 방식을 골라 주세요.")

        match_info: dict | None = None
        proof_report: dict | None = None

        # proof_pipeline: extract → match → clean → diff (optional apply)
        if destination == "proof_pipeline":
            if project_id is None:
                raise ValueError("교정 파이프라인은 기존 작품에서만 사용할 수 있어요.")
            pipe_body = {
                "filename": filename,
                "content_base64": body.get("content_base64"),
                "use_ai": body.get("use_ai", True),
                "apply_clean": body.get("apply_clean", False),
                "scene_id": body.get("scene_id"),
            }
            pipeline = self.run_proof_pipeline_api(int(project_id), pipe_body)
            scene_ids = [pipeline["scene_id"]] if pipeline.get("scene_id") else []
            return {
                "project_id": int(project_id),
                "destination": "proof_pipeline",
                "document_kind": "proof" if is_proof_doc else document_kind or "manuscript",
                "document_role": "editor_proof" if is_proof_doc else "manuscript",
                "tory_task": str(body.get("tory_task") or "proof_identify_and_compare"),
                "is_proof": True,
                "title": extracted.title,
                "format": extracted.format_name,
                "purpose": purpose,
                "section_count": 0,
                "chapter_count": 0,
                "word_count": word_count(pipeline.get("clean_full_text") or extracted.text),
                "scene_ids": scene_ids,
                "warnings": list(extracted.warnings) + list(
                    (pipeline.get("extract") or {}).get("warnings") or []
                ),
                "pipeline": pipeline,
                "proof": pipeline.get("step3_proof"),
                "match": pipeline.get("step1_match"),
                "clean_full_text": pipeline.get("clean_full_text"),
                "extract": unified_extract or pipeline.get("extract"),
                "applied": pipeline.get("applied"),
            }

        # proof_compare: analyze only (no manuscript write)
        if destination == "proof_compare":
            if project_id is None:
                raise ValueError("교정 비교는 기존 작품에서만 사용할 수 있어요.")
            use_ai = str(body.get("use_ai", "1")).lower() not in {"0", "false", "no"}
            scene_hint = body.get("scene_id")
            proof_body = {
                "revised_text": extracted.text,
                "target_title": extracted.title or default_title,
                "use_ai": use_ai,
            }
            if scene_hint not in (None, ""):
                proof_body["scene_id"] = scene_hint
            report = self.proof_diff_project(int(project_id), proof_body)
            if unified_extract:
                report["extract"] = unified_extract
            return {
                "project_id": int(project_id),
                "destination": "proof_compare",
                "document_kind": "proof" if is_proof_doc else document_kind or "manuscript",
                "document_role": "editor_proof" if is_proof_doc else "manuscript",
                "tory_task": str(body.get("tory_task") or "proof_identify_and_compare"),
                "is_proof": True,
                "title": extracted.title,
                "format": extracted.format_name,
                "purpose": purpose,
                "section_count": 0,
                "chapter_count": 0,
                "word_count": word_count(extracted.text),
                "scene_ids": [report["scene_id"]] if report.get("scene_id") else [],
                "warnings": list(extracted.warnings),
                "proof": report,
                "match": report.get("match"),
                "extract": unified_extract,
            }

        with database() as connection:
            created_project = False
            package_info: dict = {}
            if destination == "new_project" or project_id is None:
                if destination in {"match_replace_scene", "proof_compare"}:
                    raise ValueError("회차 자동 매칭은 기존 작품에서만 사용할 수 있어요.")
                main_genre = str(body.get("main_genre") or "").strip()[:80]
                sub_genre = str(body.get("sub_genre") or "").strip()[:80]
                if not main_genre:
                    raise ValueError("장르를 선택해 주세요. 토리 학습에 필요해요.")
                # Explicit last_opened_at (μs) so import-created works sort correctly vs rapid creates.
                max_order_row = connection.execute(
                    "SELECT COALESCE(MAX(list_sort_order), -1) AS m FROM project WHERE deleted_at IS NULL"
                ).fetchone()
                next_order = int(max_order_row["m"] if max_order_row else -1) + 1
                cursor = connection.execute(
                    "INSERT INTO project(title, purpose, main_genre, sub_genre, last_opened_at, list_sort_order) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        project_title,
                        purpose,
                        main_genre,
                        sub_genre,
                        utc_timestamp_now(),
                        next_order,
                    ),
                )
                project_id = int(cursor.lastrowid)
                created_project = True
                destination = "new_chapter"
                package_info = ensure_project_package(connection, project_id)
            else:
                self.require_project(connection, project_id)
                # Optional: update purpose / genre when provided on import into existing project.
                if body.get("purpose") not in (None, ""):
                    connection.execute(
                        "UPDATE project SET purpose = ? WHERE id = ?",
                        (purpose, project_id),
                    )
                main_genre = str(body.get("main_genre") or "").strip()[:80]
                sub_genre = str(body.get("sub_genre") or "").strip()[:80]
                if main_genre:
                    connection.execute(
                        "UPDATE project SET main_genre = ?, sub_genre = ? WHERE id = ?",
                        (main_genre, sub_genre, project_id),
                    )
                package_info = ensure_project_package(connection, project_id)

            chapter_id = body.get("chapter_id")
            scene_id = body.get("scene_id")
            scene_ids: list[int] = []
            chapter_ids: list[int] = []
            part_ids: list[int] = []
            used_hierarchy = False

            if destination == "match_replace_scene":
                episodes = self._list_episode_candidates(connection, project_id)
                use_ai = str(body.get("use_ai", "1")).lower() not in {"0", "false", "no"}
                matched = chapter_match.match_episode(
                    extracted.text,
                    episodes,
                    target_title=extracted.title or default_title,
                    use_ai=use_ai,
                )
                match_info = matched.to_dict()
                if matched.matched_scene_id is None:
                    raise ValueError(
                        matched.match_reason
                        or "업로드한 교정고와 맞는 회차를 찾지 못했습니다. "
                        "회차를 연 뒤 ‘지금 열린 씬에 덮어쓰기’를 사용해 주세요."
                    )
                if matched.confidence_score < chapter_match.LOCAL_ACCEPT_THRESHOLD:
                    raise ValueError(
                        f"매칭 신뢰도가 낮습니다 ({matched.confidence_score:.0%}). "
                        f"후보: {matched.matched_episode_number}화 「{matched.matched_title}」 — "
                        "확인 후 해당 회차를 열고 덮어쓰기 하거나, 더 긴 본문이 있는 파일을 올려 주세요."
                    )
                scene_id = matched.matched_scene_id
                scene = connection.execute(
                    "SELECT id, project_id, title FROM scene "
                    "WHERE id = ? AND deleted_at IS NULL",
                    (scene_id,),
                ).fetchone()
                if scene is None or int(scene["project_id"]) != project_id:
                    raise ValueError("매칭된 회차를 찾을 수 없습니다.")
                # HWP 교정고 잔재(메모·조판 기호) 제거 후 순수 본문만 반영
                content = proof_clean.clean_proof_text(sections[0].content)
                if not content.strip():
                    raise ValueError("정제 후 본문이 비어 있습니다. 원문 추출을 확인해 주세요.")
                self._write_scene_content(
                    connection,
                    scene_id,
                    content,
                    save_note=f"교정고 자동 매칭 가져오기: {filename}",
                )
                # Keep existing title by default so binder labels stay stable.
                keep_title = str(body.get("keep_scene_title", "1")).lower() not in {"0", "false", "no"}
                if keep_title:
                    connection.execute(
                        "UPDATE scene SET status = CASE WHEN status = 'idea' THEN 'draft' ELSE status END WHERE id = ?",
                        (scene_id,),
                    )
                else:
                    connection.execute(
                        "UPDATE scene SET title = ?, status = CASE WHEN status = 'idea' THEN 'draft' ELSE status END WHERE id = ?",
                        (sections[0].title, scene_id),
                    )
                scene_ids = [int(scene_id)]
                chapter_id = matched.matched_chapter_id
                chapter_ids = [int(chapter_id)] if chapter_id is not None else []
            elif destination == "replace_scene":
                if scene_id in (None, ""):
                    raise ValueError("덮어쓸 씬을 먼저 열어 주세요.")
                scene_id = int(scene_id)
                scene = connection.execute(
                    "SELECT id, project_id, row_version, title FROM scene "
                    "WHERE id = ? AND deleted_at IS NULL",
                    (scene_id,),
                ).fetchone()
                if scene is None:
                    raise ValueError("씬을 찾을 수 없습니다.")
                if scene["project_id"] != project_id:
                    raise ValueError("다른 작품의 씬에는 가져올 수 없습니다.")
                content = sections[0].content
                self._write_scene_content(connection, scene_id, content, save_note=f"문서 가져오기: {filename}")
                if not str(body.get("keep_scene_title", "")).lower() in {"1", "true", "yes"}:
                    connection.execute(
                        "UPDATE scene SET title = ?, status = CASE WHEN status = 'idea' THEN 'draft' ELSE status END WHERE id = ?",
                        (sections[0].title, scene_id),
                    )
                else:
                    connection.execute(
                        "UPDATE scene SET status = CASE WHEN status = 'idea' THEN 'draft' ELSE status END WHERE id = ?",
                        (scene_id,),
                    )
                scene_ids = [scene_id]
                chapter_id = connection.execute(
                    "SELECT chapter_id FROM scene WHERE id = ?", (scene_id,)
                ).fetchone()[0]
                chapter_ids = [chapter_id]
            elif destination == "existing_chapter":
                if chapter_id in (None, ""):
                    raise ValueError("글을 넣을 챕터를 골라 주세요.")
                chapter_id = int(chapter_id)
                chapter = connection.execute(
                    "SELECT id, project_id FROM chapter WHERE id = ? AND deleted_at IS NULL",
                    (chapter_id,),
                ).fetchone()
                if chapter is None:
                    raise ValueError("챕터를 찾을 수 없습니다.")
                if chapter["project_id"] != project_id:
                    raise ValueError("다른 작품의 챕터에는 가져올 수 없습니다.")
                chapter_ids = [chapter_id]
                for section in sections:
                    scene_ids.append(
                        self._insert_imported_scene(
                            connection, project_id, chapter_id, section.title, section.content, filename
                        )
                    )
            elif destination == "new_chapter" and plan.is_hierarchy and getattr(plan, "hierarchy", None) is not None:
                inserted = self._insert_hierarchy_import(
                    connection, project_id, plan.hierarchy, filename
                )
                scene_ids = inserted["scene_ids"]
                chapter_ids = inserted["chapter_ids"]
                chapter_id = chapter_ids[-1] if chapter_ids else None
                part_ids = inserted["part_ids"]
                used_hierarchy = True
            else:
                # new_chapter (also after new_project): honour multi-chapter plans (headings…).
                multi_chapter = len(plan.chapters) > 1
                for chapter_plan in plan.chapters:
                    title_for_chapter = chapter_plan.title if multi_chapter else chapter_title
                    if not multi_chapter and len(plan.chapters) == 1 and split_mode in {"none", ""}:
                        title_for_chapter = chapter_title
                    sort_order = connection.execute(
                        "SELECT COALESCE(MAX(sort_order) + 1, 0) FROM chapter "
                        "WHERE project_id = ? AND part_id IS NULL AND deleted_at IS NULL",
                        (project_id,),
                    ).fetchone()[0]
                    cursor = connection.execute(
                        "INSERT INTO chapter(project_id, title, sort_order) VALUES (?, ?, ?)",
                        (project_id, title_for_chapter, sort_order),
                    )
                    new_chapter_id = int(cursor.lastrowid)
                    chapter_ids.append(new_chapter_id)
                    chapter_id = new_chapter_id
                    for section in chapter_plan.scenes:
                        scene_ids.append(
                            self._insert_imported_scene(
                                connection, project_id, new_chapter_id, section.title, section.content, filename
                            )
                        )

            total_words = sum(word_count(section.content) for section in sections)
            if not package_info:
                package_info = ensure_project_package(connection, project_id)
            # Parallel folder map (outline may read folder when complete)
            if not used_hierarchy:
                try:
                    folder_tree.sync_project_folder_tree(connection, project_id)
                except sqlite3.OperationalError:
                    pass
            result = {
                "project_id": project_id,
                "chapter_id": chapter_id,
                "chapter_ids": chapter_ids,
                "scene_ids": scene_ids,
                "created_project": created_project,
                "title": extracted.title,
                "format": extracted.format_name,
                "purpose": purpose,
                "section_count": plan.section_count,
                "chapter_count": len(chapter_ids),
                "word_count": total_words,
                "warnings": list(extracted.warnings) + list(plan.warnings),
                **package_info,
            }
            if used_hierarchy:
                result["hierarchy"] = True
                result["toc_source"] = getattr(plan.hierarchy, "toc_source", None)
                result["part_ids"] = part_ids
                result["part_count"] = len(part_ids)
            if match_info is not None:
                result["match"] = match_info
            return result

    def _insert_hierarchy_import(
        self,
        connection: sqlite3.Connection,
        project_id: int,
        hierarchy: import_hierarchy.HierarchyImportPlan,
        filename: str,
    ) -> dict:
        """Insert 목차 part + prologue/volumes/epilogue from a hierarchy plan."""
        scene_ids: list[int] = []
        chapter_ids: list[int] = []
        part_ids: list[int] = []

        def next_part_sort() -> int:
            return connection.execute(
                "SELECT COALESCE(MAX(sort_order) + 1, 0) FROM part "
                "WHERE project_id = ? AND deleted_at IS NULL",
                (project_id,),
            ).fetchone()[0]

        def insert_part(title: str) -> int:
            cursor = connection.execute(
                "INSERT INTO part(project_id, title, sort_order) VALUES (?, ?, ?)",
                (project_id, title, next_part_sort()),
            )
            part_id = int(cursor.lastrowid)
            part_ids.append(part_id)
            return part_id

        def next_chapter_sort(part_id: int) -> int:
            return connection.execute(
                "SELECT COALESCE(MAX(sort_order) + 1, 0) FROM chapter "
                "WHERE project_id = ? AND part_id = ? AND deleted_at IS NULL",
                (project_id, part_id),
            ).fetchone()[0]

        def insert_chapter(
            part_id: int,
            title: str,
            *,
            transparent: bool = False,
        ) -> int:
            notes = (
                import_hierarchy.TRANSPARENT_CHAPTER_MARKER
                if transparent
                else ""
            )
            cursor = connection.execute(
                "INSERT INTO chapter(project_id, part_id, title, notes_md, sort_order) "
                "VALUES (?, ?, ?, ?, ?)",
                (project_id, part_id, title, notes, next_chapter_sort(part_id)),
            )
            chapter_id = int(cursor.lastrowid)
            chapter_ids.append(chapter_id)
            return chapter_id

        def insert_episode(chapter_id: int, title: str, content: str) -> None:
            scene_ids.append(
                self._insert_imported_scene(
                    connection, project_id, chapter_id, title, content, filename
                )
            )

        # 1) 목차만 권 밖 최상위 part
        toc_part_id = insert_part(import_hierarchy.TOC_PART_TITLE)
        toc_chapter_id = insert_chapter(toc_part_id, import_hierarchy.TOC_CHAPTER_TITLE)
        insert_episode(toc_chapter_id, import_hierarchy.TOC_SCENE_TITLE, hierarchy.toc_text)

        # 2) 권(들): 프롤로그·소개·부/장/화·에필로그·미정회차 모두 권 안에 문서 순서대로
        for volume in hierarchy.volumes:
            vol_part = insert_part(volume.title)
            for folder in volume.folders:
                ch_id = insert_chapter(
                    vol_part,
                    folder.title,
                    transparent=folder.transparent,
                )
                for episode in folder.episodes:
                    insert_episode(ch_id, episode.title, episode.content)

        # Keep parallel folder tree in sync so outline can read via folder path.
        try:
            folder_tree.sync_project_folder_tree(connection, project_id)
        except sqlite3.OperationalError:
            pass

        return {
            "scene_ids": scene_ids,
            "chapter_ids": chapter_ids,
            "part_ids": part_ids,
        }

    def _insert_imported_scene(
        self,
        connection: sqlite3.Connection,
        project_id: int,
        chapter_id: int,
        title: str,
        content: str,
        filename: str,
    ) -> int:
        scene_sort = connection.execute(
            "SELECT COALESCE(MAX(sort_order) + 1, 0) FROM scene "
            "WHERE chapter_id = ? AND deleted_at IS NULL",
            (chapter_id,),
        ).fetchone()[0]
        cursor = connection.execute(
            "INSERT INTO scene(project_id, chapter_id, title, status, sort_order) "
            "VALUES (?, ?, ?, 'draft', ?)",
            (project_id, chapter_id, title, scene_sort),
        )
        new_scene_id = int(cursor.lastrowid)
        connection.execute(
            "INSERT INTO scene_revision(scene_id, revision_no, content_md, word_count, save_note) "
            "VALUES (?, 1, ?, ?, ?)",
            (new_scene_id, content, word_count(content), f"문서 가져오기: {filename}"),
        )
        return new_scene_id

    def _write_scene_content(
        self,
        connection: sqlite3.Connection,
        scene_id: int,
        content: str,
        save_note: str = "문서 가져오기",
    ) -> None:
        current = connection.execute(
            "SELECT id, revision_no, content_md FROM scene_revision WHERE scene_id = ? AND is_current = 1",
            (scene_id,),
        ).fetchone()
        if current is None:
            connection.execute(
                "INSERT INTO scene_revision(scene_id, revision_no, content_md, word_count, save_note) "
                "VALUES (?, 1, ?, ?, ?)",
                (scene_id, content, word_count(content), save_note),
            )
            return
        if current["content_md"] == content:
            return
        cursor = connection.execute(
            "INSERT INTO scene_revision(scene_id, revision_no, content_md, word_count, save_note, is_current) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (scene_id, current["revision_no"] + 1, content, word_count(content), save_note),
        )
        connection.execute(
            "UPDATE scene_revision SET is_current = CASE "
            "WHEN id = ? THEN 1 WHEN id = ? THEN 0 ELSE is_current END "
            "WHERE id IN (?, ?)",
            (cursor.lastrowid, current["id"], cursor.lastrowid, current["id"]),
        )


def parse_launch_args(argv: list[str]) -> Path | None:
    for argument in argv:
        if argument.startswith("-"):
            continue
        path = Path(argument)
        if path.suffix.lower() == project_package.PACKAGE_EXTENSION:
            return path
    return None


def build_app_url(project_id: int | None = None) -> str:
    if project_id is None:
        return f"http://{HOST}:{PORT}/"
    return f"http://{HOST}:{PORT}/?project={project_id}"


def main(argv: list[str] | None = None) -> None:
    # Windows consoles often use cp949; paths under OneDrive/文档 must not crash prints.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    argv = list(sys.argv[1:] if argv is None else argv)
    package_path = parse_launch_args(argv)

    initialise_database()
    # Electron owns shell integration; skip for frozen/Electron launches.
    if not ELECTRON_MODE and not _is_frozen():
        project_package.register_windows_file_association(ROOT, sys.executable)

    project_id: int | None = None
    if package_path is not None:
        try:
            project_id = resolve_package_file(package_path.expanduser().resolve())
            print(f"작품 파일 열기: {package_path.name}")
        except (ValueError, OSError) as error:
            print(f"작품 파일을 열 수 없습니다: {error}")
            project_id = None

    url = build_app_url(project_id)

    # Always load the latest app.py. An old background process on :8765 caused
    # "알 수 없는 요청" for new APIs (e.g. spellcheck) after users only refreshed the browser.
    if port_is_open(HOST, PORT):
        stop_server_on_port(PORT)
        if port_is_open(HOST, PORT):
            print("이미 실행 중인 SuperTORY를 종료하지 못했습니다.")
            print("작업 관리자에서 python.exe 를 종료한 뒤 start_supertory.bat 을 다시 실행해 주세요.")
            print(url)
            if not NO_BROWSER:
                webbrowser.open(url)
            return

    server = ThreadingHTTPServer((HOST, PORT), SuperToryHandler)
    if ELECTRON_MODE:
        print("\nSuperTory (Electron) 서버가 준비되었습니다.")
        print(f"URL: {url}")
        print(f"데이터: {DATA_DIR}")
        print(f"작품 파일 폴더: {projects_root()}\n")
    else:
        print("\nSuperTory가 열렸습니다.")
        print(f"브라우저가 열리지 않으면 {url} 을 주소창에 입력해 주세요.")
        print(f"작품 파일 폴더: {projects_root()}")
        print("이 창을 닫으면 앱도 종료됩니다.\n")
    if not NO_BROWSER:
        Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    if _is_frozen():
        import multiprocessing

        multiprocessing.freeze_support()
    main()
