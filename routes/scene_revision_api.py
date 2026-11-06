"""회차 「이전 버전」 HTTP API. app.py는 라우트만 연결한다.

- GET  /api/scenes/{id}/revisions?limit=&offset=     목록(본문 없음, 최신순)
- GET  /api/scenes/{id}/revisions/{no}                한 버전의 본문(평문)
- GET  /api/scenes/{id}/revisions/{no}/diff           그 버전 → 지금 원고 비교
- POST /api/scenes/{id}/revisions/{no}/restore        새 버전으로 복원 {row_version}
- POST /api/scenes/{id}/checkpoint                    체크포인트 {revision_no?, reason?, label?, checkpoint?}
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from services.scene_revision_service import SceneRevisionService

_LIST = re.compile(r"/api/scenes/(\d+)/revisions")
_ONE = re.compile(r"/api/scenes/(\d+)/revisions/(\d+)")
_DIFF = re.compile(r"/api/scenes/(\d+)/revisions/(\d+)/diff")
_RESTORE = re.compile(r"/api/scenes/(\d+)/revisions/(\d+)/restore")
_CHECKPOINT = re.compile(r"/api/scenes/(\d+)/checkpoint")


def _service(handler) -> SceneRevisionService:
    import app

    def restore_writer(scene_id: int, content: str, row_version: int, note: str) -> dict:
        # 편집기 저장과 같은 경로: 낙관적 잠금·등장인물 색인·미러링을 그대로 쓴다.
        return handler.save_scene(
            scene_id,
            {"content_md": content, "row_version": row_version, "save_note": note},
        )

    return SceneRevisionService(
        database=app.database,
        plain_text=lambda html: app.plain_text_from_content(html, include_author_notes=True),
        restore_writer=restore_writer,
    )


def _query_int(handler, name: str, default: int) -> int:
    values = parse_qs(urlparse(handler.path).query).get(name) or []
    try:
        return int(values[0])
    except (IndexError, TypeError, ValueError):
        return default


def try_handle_get(handler, path: str) -> bool:
    match = _DIFF.fullmatch(path)
    if match:
        handler.send_json(
            _service(handler).diff_with_current(int(match.group(1)), int(match.group(2)))
        )
        return True
    match = _ONE.fullmatch(path)
    if match:
        handler.send_json(
            _service(handler).get_revision(int(match.group(1)), int(match.group(2)))
        )
        return True
    match = _LIST.fullmatch(path)
    if match:
        handler.send_json(
            _service(handler).list_revisions(
                int(match.group(1)),
                limit=_query_int(handler, "limit", 200),
                offset=_query_int(handler, "offset", 0),
            )
        )
        return True
    return False


def try_handle_post(handler, path: str, body: dict | None) -> bool:
    payload = body if isinstance(body, dict) else {}
    match = _RESTORE.fullmatch(path)
    if match:
        handler.send_json(
            _service(handler).restore(
                int(match.group(1)),
                int(match.group(2)),
                int(payload.get("row_version") or 0),
            )
        )
        return True
    match = _CHECKPOINT.fullmatch(path)
    if match:
        raw_no = payload.get("revision_no")
        handler.send_json(
            _service(handler).set_checkpoint(
                int(match.group(1)),
                revision_no=int(raw_no) if raw_no not in (None, "") else None,
                reason=str(payload.get("reason") or "manual"),
                label=str(payload.get("label") or ""),
                checkpoint=payload.get("checkpoint", True) is not False,
            )
        )
        return True
    return False
