"""첨삭 피드백 HTTP API. app.py는 라우트만 연결한다."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs, urlparse

import feedback_store
from feedback_pipeline.card_reply import (
    CardCommentLimit,
    LIMIT_MESSAGE,
    public_comment,
    reply_to_card_comment,
)
from feedback_pipeline.claude_client import ClaudeError, get_client, is_configured, is_fake_mode
from feedback_pipeline.config import FEEDBACK_MODEL, MAX_COST_USD_PER_RUN, PROMPT_VERSION
from feedback_pipeline.context import explanation_lens_for_project
from feedback_pipeline.paragraphs import paragraphs_from_html, source_hash
from feedback_pipeline.runner import run_feedback

MAX_CONCURRENT_RUNS = 2
_LENS = frozenset({"strong", "normal", "off"})

_lock = threading.Lock()
_active_threads: dict[int, threading.Thread] = {}
_cancel_events: dict[int, threading.Event] = {}


class FeedbackConflict(Exception):
    """409 응답으로 보낼 충돌."""


def reset_runtime_for_tests() -> None:
    """테스트 간 백그라운드 슬롯·취소 플래그를 비운다."""
    with _lock:
        _active_threads.clear()
        _cancel_events.clear()


def _connect() -> sqlite3.Connection:
    import app

    return app.connect()


def _database():
    import app

    return app.database()


def _now_sql(conn: sqlite3.Connection) -> str:
    return str(conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')").fetchone()[0])


def _loads_params(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def interrupt_stale_runs(conn: sqlite3.Connection | None = None) -> int:
    """status=running인 실행을 프로세스 재시작 잔여로 보고 마감한다."""
    own = conn is None
    if own:
        conn = _connect()
    assert conn is not None
    changed = 0
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'feedback_run'"
        ).fetchone()
        if exists is None:
            return 0
        rows = conn.execute(
            "SELECT id, report_json, params_json FROM feedback_run WHERE status = 'running'"
        ).fetchall()
        finished = _now_sql(conn)
        for row in rows:
            run_id = int(row["id"])
            params = _loads_params(row["params_json"])
            progress = params.get("progress") if isinstance(params.get("progress"), dict) else {}
            progress["stage"] = "interrupted"
            params["progress"] = progress
            status = "failed" if not row["report_json"] else "partial"
            feedback_store.update_run(
                conn,
                run_id,
                status=status,
                params_json=params,
                finished_at=finished,
            )
            changed += 1
        if own:
            conn.commit()
    finally:
        if own:
            conn.close()
    return changed


def _active_count() -> int:
    with _lock:
        dead = [rid for rid, thread in _active_threads.items() if not thread.is_alive()]
        for rid in dead:
            _active_threads.pop(rid, None)
        return len(_active_threads)


def _release(run_id: int) -> None:
    with _lock:
        _active_threads.pop(int(run_id), None)


def _cancel_event(run_id: int) -> threading.Event:
    with _lock:
        event = _cancel_events.get(int(run_id))
        if event is None:
            event = threading.Event()
            _cancel_events[int(run_id)] = event
        return event


def _query_int(handler, name: str) -> int | None:
    query = parse_qs(urlparse(handler.path).query)
    raw = (query.get(name) or [None])[0]
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name}이(가) 올바르지 않습니다.") from error


def _paragraph_warnings(paragraphs: list[Any]) -> list[dict[str, str]]:
    items = [row for row in paragraphs if isinstance(row, dict)]
    count = len(items)
    text_len = sum(len(str(row.get("text") or "")) for row in items)
    if count == 1 and text_len > 800:
        return [
            {
                "code": "single_paragraph",
                "message": "이 회차가 한 문단으로 인식됐어요. 위치 표시가 정확하지 않을 수 있어요",
            }
        ]
    return []


def _public_run(run: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(run, dict):
        raise LookupError("피드백 실행을 찾을 수 없습니다.")
    params = run.get("params") if isinstance(run.get("params"), dict) else {}
    scenes = run.get("scenes") if isinstance(run.get("scenes"), list) else []
    paragraphs: list[Any] = []
    if scenes:
        primary = next(
            (row for row in scenes if int(row.get("is_primary") or 0) == 1),
            scenes[0],
        )
        paragraphs = list(primary.get("paragraphs") or [])
    run["progress"] = params.get("progress")
    run["usage"] = params.get("usage")
    run["paragraphs"] = paragraphs
    run["paragraph_count"] = len(paragraphs)
    extra = list(params.get("warnings") or []) if isinstance(params.get("warnings"), list) else []
    run["warnings"] = extra + _paragraph_warnings(paragraphs)
    return run


def _load_scene(conn: sqlite3.Connection, project_id: int, scene_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT s.id, s.title, r.revision_no, r.content_md
        FROM scene s
        JOIN scene_revision r ON r.scene_id = s.id AND r.is_current = 1
        WHERE s.id = ? AND s.project_id = ? AND s.deleted_at IS NULL
        """,
        (int(scene_id), int(project_id)),
    ).fetchone()
    if row is None:
        raise LookupError("회차를 찾을 수 없습니다.")
    return dict(row)


def _scene_has_running(conn: sqlite3.Connection, scene_id: int) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM feedback_run r
        JOIN feedback_run_scene s ON s.run_id = r.id
        WHERE s.scene_id = ? AND r.status = 'running'
        LIMIT 1
        """,
        (int(scene_id),),
    ).fetchone()
    return row is not None


def _worker(
    run_id: int,
    project_id: int,
    scene_id: int,
    paragraphs: list[dict[str, Any]],
    options: dict[str, Any],
) -> None:
    conn = _connect()
    try:
        payload = dict(options)
        payload["run_id"] = int(run_id)
        payload["autocommit"] = True
        payload["cancel_event"] = _cancel_event(run_id)
        run_feedback(
            conn,
            project_id,
            scene_id,
            paragraphs,
            payload,
            claude=get_client(),
        )
        conn.commit()
    except Exception as error:  # noqa: BLE001
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        try:
            params = {}
            row = conn.execute(
                "SELECT params_json, report_json, status FROM feedback_run WHERE id = ?",
                (int(run_id),),
            ).fetchone()
            if row is not None and str(row["status"] or "") == "running":
                params = _loads_params(row["params_json"])
                params["error"] = str(error)[:500]
                status = "failed" if not row["report_json"] else "partial"
                feedback_store.update_run(
                    conn,
                    run_id,
                    status=status,
                    raw_output=str(error)[:4000],
                    finished_at=_now_sql(conn),
                    params_json=params,
                )
                conn.commit()
        except sqlite3.Error:
            pass
    finally:
        conn.close()
        _release(run_id)


def _start_run(
    handler,
    project_id: int,
    body: dict[str, Any],
) -> None:
    if not is_configured():
        raise ValueError("ANTHROPIC_API_KEY가 설정되지 않았습니다.")
    scene_raw = body.get("scene_id")
    try:
        scene_id = int(scene_raw)
    except (TypeError, ValueError) as error:
        raise ValueError("scene_id가 올바르지 않습니다.") from error
    lens = str(body.get("explanation_lens") or "").strip()
    if lens and lens not in _LENS:
        raise ValueError("explanation_lens는 strong, normal, off 중 하나여야 합니다.")
    max_cost = body.get("max_cost")
    if max_cost is None or max_cost == "":
        cost_limit = MAX_COST_USD_PER_RUN
    else:
        try:
            cost_limit = float(max_cost)
        except (TypeError, ValueError) as error:
            raise ValueError("max_cost가 올바르지 않습니다.") from error

    if _active_count() >= MAX_CONCURRENT_RUNS:
        raise FeedbackConflict("잠시 후 다시 시도하세요")

    with _database() as conn:
        handler.require_project(conn, project_id)
        scene = _load_scene(conn, project_id, scene_id)
        if _scene_has_running(conn, scene_id):
            raise FeedbackConflict("이미 이 회차의 첨삭이 실행 중입니다.")
        html = str(scene.get("content_md") or "")
        paragraphs = paragraphs_from_html(html)
        digest = source_hash(paragraphs)
        params = {
            "progress": {"stage": "queued", "done": 0, "total": 5},
            "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "stages": []},
            "dropped": [],
            "explanation_lens": lens or None,
            "fake": bool(is_fake_mode()),
        }
        run_id = feedback_store.create_run(
            conn,
            project_id,
            "analyze",
            FEEDBACK_MODEL,
            PROMPT_VERSION,
            params,
        )
        feedback_store.add_run_scene(
            conn,
            run_id,
            project_id,
            scene_id,
            0,
            str(scene.get("title") or ""),
            scene.get("revision_no"),
            digest,
            paragraphs,
        )

    options = {
        "scene_title": str(scene.get("title") or ""),
        "revision_no": scene.get("revision_no"),
        "max_cost": cost_limit,
    }
    if lens:
        options["explanation_lens"] = lens
    event = _cancel_event(run_id)
    event.clear()
    worker = threading.Thread(
        target=_worker,
        args=(run_id, project_id, scene_id, paragraphs, options),
        daemon=True,
        name=f"feedback-run-{run_id}",
    )
    try:
        with _lock:
            alive = sum(1 for t in _active_threads.values() if t.is_alive())
            if alive >= MAX_CONCURRENT_RUNS:
                raise FeedbackConflict("잠시 후 다시 시도하세요")
            _active_threads[run_id] = worker
        worker.start()
    except FeedbackConflict:
        with _database() as conn:
            feedback_store.delete_run(conn, run_id)
        raise
    handler.send_json(
        {
            "run_id": run_id,
            "source_hash": digest,
            "paragraph_count": len(paragraphs),
            "warnings": _paragraph_warnings(paragraphs),
            "status": "running",
        },
        HTTPStatus.CREATED,
    )


def _get_status(handler) -> None:
    payload: dict[str, Any] = {
        "configured": bool(is_configured()),
        "model": FEEDBACK_MODEL,
    }
    if is_fake_mode():
        payload["fake"] = True
    try:
        project_id = _query_int(handler, "project_id")
    except ValueError:
        project_id = None
    if project_id is not None:
        with _database() as conn:
            handler.require_project(conn, project_id)
            cols = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(project)").fetchall()
            }
            select = ["main_genre", "sub_genre"]
            if "genre_detail" in cols:
                select.append("genre_detail")
            row = conn.execute(
                f"SELECT {', '.join(select)} FROM project WHERE id = ?",
                (int(project_id),),
            ).fetchone()
            if row is not None:
                data = dict(row)
                payload["default_explanation_lens"] = explanation_lens_for_project(
                    data.get("main_genre"),
                    data.get("sub_genre"),
                    data.get("genre_detail"),
                )
            else:
                payload["default_explanation_lens"] = "normal"
    handler.send_json(payload)


def _list_runs(handler, project_id: int) -> None:
    scene_id = _query_int(handler, "scene_id")
    with _database() as conn:
        handler.require_project(conn, project_id)
        rows = feedback_store.list_runs(conn, project_id, scene_id)
    handler.send_json(rows)


def _get_run(handler, run_id: int) -> None:
    with _database() as conn:
        run = feedback_store.get_run(conn, run_id)
    handler.send_json(_public_run(run))


def _put_card(handler, card_id: int, body: dict[str, Any]) -> None:
    status = str(body.get("status") or "").strip()
    final_text = body.get("final_text")
    if final_text is not None:
        final_text = str(final_text)
    with _database() as conn:
        row = conn.execute(
            "SELECT id FROM feedback_card WHERE id = ?",
            (int(card_id),),
        ).fetchone()
        if row is None:
            raise LookupError("카드를 찾을 수 없습니다.")
        feedback_store.set_card_status(conn, card_id, status, final_text)
        saved = conn.execute(
            "SELECT id, status, final_text FROM feedback_card WHERE id = ?",
            (int(card_id),),
        ).fetchone()
    handler.send_json(dict(saved) if saved is not None else {"id": card_id, "status": status})


def _list_card_comments(handler, card_id: int) -> None:
    with _database() as conn:
        card = feedback_store.get_card(conn, card_id)
        if card is None:
            raise LookupError("카드를 찾을 수 없습니다.")
        rows = feedback_store.list_card_comments(conn, card_id)
    handler.send_json({"comments": [public_comment(row) for row in rows]})


def _post_card_comment(handler, card_id: int, body: dict[str, Any]) -> None:
    message = str((body or {}).get("message") or "").strip()
    if not message:
        raise ValueError("의견이 비어 있습니다.")
    with _database() as conn:
        try:
            payload = reply_to_card_comment(
                conn, card_id, message, claude=get_client()
            )
        except CardCommentLimit as error:
            raise FeedbackConflict(str(error) or LIMIT_MESSAGE) from error
        except ClaudeError as error:
            saved = [
                public_comment(row)
                for row in feedback_store.list_card_comments(conn, card_id)
            ]
            handler.send_json(
                {
                    "error": str(error) or "답을 받지 못했어요.",
                    "comments": saved[-1:] if saved else [],
                },
                HTTPStatus.BAD_GATEWAY,
            )
            return
    handler.send_json(payload)


def _set_primary(handler, run_id: int, body: dict[str, Any]) -> None:
    try:
        scene_id = int(body.get("scene_id"))
    except (TypeError, ValueError) as error:
        raise ValueError("scene_id가 올바르지 않습니다.") from error
    with _database() as conn:
        if feedback_store.get_run(conn, run_id) is None:
            raise LookupError("피드백 실행을 찾을 수 없습니다.")
        feedback_store.set_primary(conn, run_id, scene_id)
    handler.send_json({"ok": True, "run_id": int(run_id), "scene_id": scene_id})


def _cancel_run(handler, run_id: int) -> None:
    with _database() as conn:
        run = conn.execute(
            "SELECT id, status FROM feedback_run WHERE id = ?",
            (int(run_id),),
        ).fetchone()
        if run is None:
            raise LookupError("피드백 실행을 찾을 수 없습니다.")
        if str(run["status"] or "") != "running":
            handler.send_json(
                {"ok": True, "run_id": int(run_id), "status": run["status"], "cancelled": False}
            )
            return
    _cancel_event(run_id).set()
    handler.send_json({"ok": True, "run_id": int(run_id), "status": "running", "cancelled": True})


def _delete_run(handler, run_id: int) -> None:
    with _database() as conn:
        run = conn.execute(
            "SELECT id, status FROM feedback_run WHERE id = ?",
            (int(run_id),),
        ).fetchone()
        if run is None:
            raise LookupError("피드백 실행을 찾을 수 없습니다.")
        if str(run["status"] or "") == "running":
            raise FeedbackConflict("실행 중인 첨삭은 삭제할 수 없습니다.")
        feedback_store.delete_run(conn, run_id)
    handler.send_json({"ok": True})


def _import_legacy(handler, project_id: int, body: dict[str, Any]) -> None:
    entries = body.get("entries")
    if not isinstance(entries, list):
        raise ValueError("entries가 올바르지 않습니다.")
    with _database() as conn:
        handler.require_project(conn, project_id)
        created = feedback_store.import_legacy_entries(conn, project_id, entries)
    skipped = max(0, len(entries) - len(created))
    handler.send_json({"imported": len(created), "skipped": skipped, "run_ids": created})


def try_handle_get(handler) -> bool:
    path = urlparse(handler.path).path.rstrip("/") or "/"
    try:
        if path == "/api/feedback/status":
            _get_status(handler)
            return True
        match = re.fullmatch(r"/api/projects/(\d+)/feedback/runs", path)
        if match:
            _list_runs(handler, int(match.group(1)))
            return True
        match = re.fullmatch(r"/api/feedback/runs/(\d+)", path)
        if match:
            _get_run(handler, int(match.group(1)))
            return True
        match = re.fullmatch(r"/api/feedback/cards/(\d+)/comments", path)
        if match:
            _list_card_comments(handler, int(match.group(1)))
            return True
    except FeedbackConflict as error:
        dispatch_conflict(handler, error)
        return True
    return False


def try_handle_post(handler, path: str, body: dict[str, Any] | None) -> bool:
    body = body or {}
    try:
        match = re.fullmatch(r"/api/projects/(\d+)/feedback/runs", path)
        if match:
            _start_run(handler, int(match.group(1)), body)
            return True
        match = re.fullmatch(r"/api/feedback/runs/(\d+)/primary", path)
        if match:
            _set_primary(handler, int(match.group(1)), body)
            return True
        match = re.fullmatch(r"/api/feedback/runs/(\d+)/cancel", path)
        if match:
            _cancel_run(handler, int(match.group(1)))
            return True
        match = re.fullmatch(r"/api/projects/(\d+)/feedback/import-legacy", path)
        if match:
            _import_legacy(handler, int(match.group(1)), body)
            return True
        match = re.fullmatch(r"/api/feedback/cards/(\d+)/comments", path)
        if match:
            _post_card_comment(handler, int(match.group(1)), body)
            return True
    except FeedbackConflict as error:
        dispatch_conflict(handler, error)
        return True
    return False


def try_handle_put(handler, path: str, body: dict[str, Any] | None) -> bool:
    try:
        match = re.fullmatch(r"/api/feedback/cards/(\d+)", path)
        if match:
            _put_card(handler, int(match.group(1)), body or {})
            return True
    except FeedbackConflict as error:
        dispatch_conflict(handler, error)
        return True
    return False


def try_handle_delete(handler, path: str) -> bool:
    try:
        match = re.fullmatch(r"/api/feedback/runs/(\d+)", path)
        if match:
            _delete_run(handler, int(match.group(1)))
            return True
    except FeedbackConflict as error:
        dispatch_conflict(handler, error)
        return True
    return False


def dispatch_conflict(handler, error: FeedbackConflict) -> None:
    handler.send_json({"error": str(error)}, HTTPStatus.CONFLICT)
