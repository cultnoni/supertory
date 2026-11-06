"""게임물 상태창 프리셋·켜기 설정 HTTP API. app.py는 라우트만 연결한다.

GET  /api/projects/{id}/status-window-presets  → {"ok", "presets": [...], "mode", "has_blocks"}
POST /api/projects/{id}/status-window-presets  → {"presets": [...]} 를 통째로 저장(정리한 값을 돌려준다)
POST /api/projects/{id}/status-window-mode     → {"mode": "auto" | "on" | "off"}

- mode: 세계관의 「이 작품에 상태창이 나와요」 스위치. auto면 편집기가 장르 키워드·프리셋·has_blocks로 정한다.
- has_blocks: 이 작품의 지금 원고(어느 회차든)에 상태창 블록이 하나라도 있는지.

기본 템플릿은 화면 언어에 맞춰 편집기(web/app.js)가 들고 있다. 여기에는 작가가 저장한 프리셋만 둔다.
설계: docs/design-ai-context-and-style-2026-11.md §13.
"""

from __future__ import annotations

import json
import re

import status_window


def _database():
    import app

    return app.database()


def _project_row(connection, project_id: int):
    row = connection.execute(
        "SELECT id, status_window_presets_json, status_window_mode FROM project "
        "WHERE id = ? AND deleted_at IS NULL",
        (project_id,),
    ).fetchone()
    if row is None:
        raise ValueError("소설을 찾을 수 없습니다.")
    return row


def _has_blocks(connection, project_id: int) -> bool:
    row = connection.execute(
        "SELECT 1 FROM scene s JOIN scene_revision r ON r.scene_id = s.id AND r.is_current = 1 "
        "WHERE s.project_id = ? AND s.deleted_at IS NULL AND r.content_md LIKE '%data-status-window%' LIMIT 1",
        (project_id,),
    ).fetchone()
    return row is not None


def _payload(connection, row) -> dict:
    return {
        "ok": True,
        "presets": status_window.normalize_presets(row["status_window_presets_json"]),
        "mode": status_window.normalize_mode(row["status_window_mode"]),
        "has_blocks": _has_blocks(connection, int(row["id"])),
    }


def get_presets(project_id: int) -> dict:
    with _database() as connection:
        return _payload(connection, _project_row(connection, project_id))


def save_presets(project_id: int, body: dict) -> dict:
    raw = body.get("presets") if isinstance(body, dict) else None
    if not isinstance(raw, list):
        raise ValueError("프리셋 목록이 필요합니다.")
    presets = status_window.normalize_presets(raw)
    with _database() as connection:
        _project_row(connection, project_id)
        connection.execute(
            "UPDATE project SET status_window_presets_json = ? WHERE id = ?",
            (json.dumps(presets, ensure_ascii=False), project_id),
        )
        return _payload(connection, _project_row(connection, project_id))


def save_mode(project_id: int, body: dict) -> dict:
    raw = body.get("mode") if isinstance(body, dict) else None
    if raw not in status_window.MODES:
        raise ValueError("상태창 설정 값이 올바르지 않습니다.")
    with _database() as connection:
        _project_row(connection, project_id)
        connection.execute("UPDATE project SET status_window_mode = ? WHERE id = ?", (raw, project_id))
        return _payload(connection, _project_row(connection, project_id))


_PATH = re.compile(r"/api/projects/(\d+)/status-window-presets")
_MODE_PATH = re.compile(r"/api/projects/(\d+)/status-window-mode")


def try_handle_get(handler, path: str) -> bool:
    match = _PATH.fullmatch(path)
    if not match:
        return False
    handler.send_json(get_presets(int(match.group(1))))
    return True


def try_handle_post(handler, path: str, body: dict | None) -> bool:
    match = _MODE_PATH.fullmatch(path)
    if match:
        handler.send_json(save_mode(int(match.group(1)), body or {}))
        return True
    match = _PATH.fullmatch(path)
    if not match:
        return False
    handler.send_json(save_presets(int(match.group(1)), body or {}))
    return True
