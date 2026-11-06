"""「내 문체」 프로필 HTTP API. app.py는 라우트만 연결한다.

GET  /api/projects/{id}/style-profile          → 프로필 + 프리셋 기본값
POST /api/projects/{id}/style-profile          → 보낸 키만 바꿔 저장
GET  /api/projects/{id}/style-profile/suggest  → 원고에서 센 값(문장 길이·대사 비중·자주 쓰는 낱말). AI 없음.

시점·시제·금지어는 토리 체크 API(/tory-check)가 저장한다. 설계: docs/design-ai-context-and-style-2026-11.md §3.
"""

from __future__ import annotations

import json
import re

import style_profile

SUGGEST_SCENE_LIMIT = 40
SUGGEST_CHAR_LIMIT = 80000


def _database():
    import app

    return app.database()


def _plain_text(content: str) -> str:
    import app

    return app.plain_text_from_content(content or "")


def _project_row(connection, project_id: int):
    row = connection.execute(
        "SELECT id, style_profile_json FROM project WHERE id = ? AND deleted_at IS NULL",
        (project_id,),
    ).fetchone()
    if row is None:
        raise ValueError("소설을 찾을 수 없습니다.")
    return row


def load_profile(connection, project_id: int) -> dict:
    """다른 모듈(AI 요청)에서 쓰는 읽기 도우미. 열이 없거나 작품이 없으면 빈 프로필."""
    try:
        row = connection.execute(
            "SELECT style_profile_json FROM project WHERE id = ?", (int(project_id),)
        ).fetchone()
    except Exception:
        return style_profile.empty_profile()
    return style_profile.normalize_profile(row[0] if row else "{}")


def _payload(row) -> dict:
    return {
        "ok": True,
        "profile": style_profile.normalize_profile(row["style_profile_json"]),
        "presets": {key: dict(info["values"]) for key, info in style_profile.PRESETS.items()},
    }


def get_style_profile(project_id: int) -> dict:
    with _database() as connection:
        return _payload(_project_row(connection, project_id))


def save_style_profile(project_id: int, body: dict) -> dict:
    patch = body.get("profile") if isinstance(body.get("profile"), dict) else body
    with _database() as connection:
        row = _project_row(connection, project_id)
        merged = style_profile.merge_profile(row["style_profile_json"], patch or {})
        connection.execute(
            "UPDATE project SET style_profile_json = ? WHERE id = ?",
            (json.dumps(merged, ensure_ascii=False), project_id),
        )
        return _payload(_project_row(connection, project_id))


def suggest_style_profile(project_id: int) -> dict:
    """완성 회차가 있으면 완성 회차만, 없으면 글이 있는 회차를 최근 순으로 센다."""
    with _database() as connection:
        _project_row(connection, project_id)
        rows = connection.execute(
            "SELECT s.status, r.content_md FROM scene s "
            "JOIN scene_revision r ON r.scene_id = s.id AND r.is_current = 1 "
            "WHERE s.project_id = ? AND s.deleted_at IS NULL "
            "ORDER BY s.updated_at DESC",
            (project_id,),
        ).fetchall()
    complete = [row for row in rows if str(row[0] or "") == "complete"]
    source = complete or list(rows)
    texts: list[str] = []
    used = 0
    for row in source[:SUGGEST_SCENE_LIMIT]:
        text = _plain_text(str(row[1] or ""))
        if not text.strip():
            continue
        texts.append(text[: max(0, SUGGEST_CHAR_LIMIT - used)])
        used += len(texts[-1])
        if used >= SUGGEST_CHAR_LIMIT:
            break
    result = style_profile.suggest_from_texts(texts)
    result["scene_count"] = len(texts)
    result["from_complete"] = bool(complete)
    return result


_PATH = re.compile(r"/api/projects/(\d+)/style-profile")
_SUGGEST_PATH = re.compile(r"/api/projects/(\d+)/style-profile/suggest")


def try_handle_get(handler, path: str) -> bool:
    match = _SUGGEST_PATH.fullmatch(path)
    if match:
        handler.send_json(suggest_style_profile(int(match.group(1))))
        return True
    match = _PATH.fullmatch(path)
    if not match:
        return False
    handler.send_json(get_style_profile(int(match.group(1))))
    return True


def try_handle_post(handler, path: str, body: dict | None) -> bool:
    match = _PATH.fullmatch(path)
    if not match:
        return False
    handler.send_json(save_style_profile(int(match.group(1)), body or {}))
    return True
