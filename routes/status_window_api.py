"""게임물 상태창 프리셋 HTTP API. app.py는 라우트만 연결한다.

GET  /api/projects/{id}/status-window-presets  → {"ok", "presets": [...]}
POST /api/projects/{id}/status-window-presets  → {"presets": [...]} 를 통째로 저장(정리한 값을 돌려준다)

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
        "SELECT id, status_window_presets_json FROM project WHERE id = ? AND deleted_at IS NULL",
        (project_id,),
    ).fetchone()
    if row is None:
        raise ValueError("소설을 찾을 수 없습니다.")
    return row


def get_presets(project_id: int) -> dict:
    with _database() as connection:
        row = _project_row(connection, project_id)
        return {"ok": True, "presets": status_window.normalize_presets(row["status_window_presets_json"])}


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
    return {"ok": True, "presets": presets}


_PATH = re.compile(r"/api/projects/(\d+)/status-window-presets")


def try_handle_get(handler, path: str) -> bool:
    match = _PATH.fullmatch(path)
    if not match:
        return False
    handler.send_json(get_presets(int(match.group(1))))
    return True


def try_handle_post(handler, path: str, body: dict | None) -> bool:
    match = _PATH.fullmatch(path)
    if not match:
        return False
    handler.send_json(save_presets(int(match.group(1)), body or {}))
    return True
