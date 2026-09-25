"""Server-backed Tory notifications.

This stage is the store and the actions. Pipeline code does not create rows yet.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlite3

OPEN_STATUSES = ("unread", "read", "snoozed")
STATUSES = ("unread", "read", "resolved", "dismissed", "snoozed")
ACTION_TYPES = ("navigate", "resolve", "snooze", "dismiss_forever")
VISIBLE_STATUSES = ("unread", "read")
DEFAULT_SNOOZE_HOURS = 24


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_stamp(moment: datetime | None = None) -> str:
    value = moment or utc_now()
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def parse_stamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _loads(value: object, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    text = str(value or "").strip()
    if not text:
        return fallback
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return fallback
    return parsed


def normalize_actions(raw: object) -> list[dict]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = _loads(raw, [])
    if not isinstance(raw, list):
        raise ValueError("선택지 목록이 올바르지 않습니다.")
    actions: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("선택지 목록이 올바르지 않습니다.")
        action_id = str(item.get("id") or "").strip()[:80]
        label = str(item.get("label") or "").strip()[:80]
        action_type = str(item.get("type") or "").strip()
        if not action_id or not label or action_type not in ACTION_TYPES:
            raise ValueError("선택지에는 id, label, type이 필요합니다.")
        if action_id in seen:
            raise ValueError("선택지 id가 중복되었습니다.")
        seen.add(action_id)
        target = item.get("target") if isinstance(item.get("target"), dict) else {}
        resolve = bool(item.get("resolve") or target.get("resolve"))
        actions.append({
            "id": action_id,
            "label": label,
            "type": action_type,
            "target": target,
            "resolve": resolve,
        })
    return actions


def serialize_notification(row: sqlite3.Row | dict) -> dict:
    payload = _loads(row["payload_json"], {})
    actions = _loads(row["actions_json"], [])
    if not isinstance(payload, dict):
        payload = {}
    if not isinstance(actions, list):
        actions = []
    return {
        "id": int(row["id"]),
        "project_id": int(row["project_id"]),
        "kind": str(row["kind"] or ""),
        "title": str(row["title"] or ""),
        "body": str(row["body"] or ""),
        "payload": payload,
        "actions": actions,
        "status": str(row["status"] or "unread"),
        "snooze_until": row["snooze_until"] or None,
        "resolved_action_id": str(row["resolved_action_id"] or ""),
        "resolved_at": row["resolved_at"] or None,
        "dedupe_key": str(row["dedupe_key"] or ""),
        "dismissed_forever": bool(int(row["dismissed_forever"] or 0)),
        "created_at": row["created_at"] or "",
        "updated_at": row["updated_at"] or "",
    }


def _fetch(connection: sqlite3.Connection, notification_id: int, project_id: int) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM tory_notification WHERE id = ? AND project_id = ?",
        (int(notification_id), int(project_id)),
    ).fetchone()
    if row is None:
        raise LookupError("알림을 찾을 수 없습니다.")
    return row


def wake_expired_snoozes(connection: sqlite3.Connection, project_id: int, now: datetime | None = None) -> int:
    stamp = utc_stamp(now)
    cursor = connection.execute(
        "UPDATE tory_notification SET status = 'unread', snooze_until = NULL, updated_at = ? "
        "WHERE project_id = ? AND status = 'snoozed' "
        "AND snooze_until IS NOT NULL AND snooze_until <= ?",
        (stamp, int(project_id), stamp),
    )
    return int(cursor.rowcount or 0)


def create_tory_notification(
    connection: sqlite3.Connection,
    project_id: int,
    *,
    kind: object = "",
    title: object = "",
    body: object = "",
    payload: object = None,
    actions: object = None,
    dedupe_key: object = "",
) -> dict:
    """Insert a notification, or refresh the open row with the same dedupe key.

    A dismissed-forever key is not created again and is not rewritten.
    """
    project_id = int(project_id)
    kind_text = str(kind or "").strip()[:80]
    title_text = str(title or "").strip()[:200]
    body_text = str(body or "").strip()[:8000]
    if not title_text:
        raise ValueError("알림 제목을 적어 주세요.")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("알림 문맥 데이터가 올바르지 않습니다.")
    action_list = normalize_actions(actions)
    key = str(dedupe_key or "").strip()[:200]
    payload_json = json.dumps(payload, ensure_ascii=False)
    actions_json = json.dumps(action_list, ensure_ascii=False)
    stamp = utc_stamp()

    if key:
        blocked = connection.execute(
            "SELECT * FROM tory_notification WHERE project_id = ? AND dedupe_key = ? "
            "AND dismissed_forever = 1 ORDER BY id DESC LIMIT 1",
            (project_id, key),
        ).fetchone()
        if blocked is not None:
            return {
                "notification": serialize_notification(blocked),
                "created": False,
                "updated": False,
                "blocked": True,
            }
        open_row = connection.execute(
            "SELECT * FROM tory_notification WHERE project_id = ? AND dedupe_key = ? "
            "AND status IN ('unread', 'read', 'snoozed') ORDER BY id DESC LIMIT 1",
            (project_id, key),
        ).fetchone()
        if open_row is not None:
            connection.execute(
                "UPDATE tory_notification SET kind = ?, title = ?, body = ?, payload_json = ?, "
                "actions_json = ?, updated_at = ? WHERE id = ?",
                (kind_text, title_text, body_text, payload_json, actions_json, stamp, int(open_row["id"])),
            )
            refreshed = _fetch(connection, int(open_row["id"]), project_id)
            return {
                "notification": serialize_notification(refreshed),
                "created": False,
                "updated": True,
                "blocked": False,
            }

    cursor = connection.execute(
        "INSERT INTO tory_notification("
        "project_id, kind, title, body, payload_json, actions_json, status, dedupe_key, created_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, 'unread', ?, ?, ?)",
        (project_id, kind_text, title_text, body_text, payload_json, actions_json, key, stamp, stamp),
    )
    created = _fetch(connection, int(cursor.lastrowid), project_id)
    return {
        "notification": serialize_notification(created),
        "created": True,
        "updated": False,
        "blocked": False,
    }


def list_notifications(
    connection: sqlite3.Connection,
    project_id: int,
    *,
    statuses: list[str] | None = None,
) -> list[dict]:
    wake_expired_snoozes(connection, int(project_id))
    wanted = []
    for item in statuses or list(VISIBLE_STATUSES):
        key = str(item or "").strip()
        if key in STATUSES and key not in wanted:
            wanted.append(key)
    if not wanted:
        wanted = list(VISIBLE_STATUSES)
    placeholders = ", ".join("?" for _ in wanted)
    rows = connection.execute(
        f"SELECT * FROM tory_notification WHERE project_id = ? AND status IN ({placeholders}) "
        "ORDER BY id DESC",
        (int(project_id), *wanted),
    ).fetchall()
    return [serialize_notification(row) for row in rows]


def mark_read(connection: sqlite3.Connection, project_id: int, notification_id: int) -> dict:
    row = _fetch(connection, notification_id, project_id)
    if str(row["status"]) == "unread":
        connection.execute(
            "UPDATE tory_notification SET status = 'read', updated_at = ? WHERE id = ?",
            (utc_stamp(), int(notification_id)),
        )
        row = _fetch(connection, notification_id, project_id)
    return serialize_notification(row)


def _close(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    status: str,
    action_id: str = "",
    forever: bool = False,
    snooze_until: str | None = None,
) -> dict:
    stamp = utc_stamp()
    connection.execute(
        "UPDATE tory_notification SET status = ?, resolved_action_id = ?, resolved_at = ?, "
        "dismissed_forever = ?, snooze_until = ?, updated_at = ? WHERE id = ?",
        (
            status,
            action_id,
            stamp if status in {"resolved", "dismissed"} else None,
            1 if forever else int(row["dismissed_forever"] or 0),
            snooze_until,
            stamp,
            int(row["id"]),
        ),
    )
    return serialize_notification(_fetch(connection, int(row["id"]), int(row["project_id"])))


def snooze_notification(
    connection: sqlite3.Connection,
    project_id: int,
    notification_id: int,
    *,
    hours: object = None,
    until: object = None,
    action_id: str = "",
) -> dict:
    row = _fetch(connection, notification_id, project_id)
    if str(row["status"]) in {"resolved", "dismissed"}:
        raise ValueError("이미 닫힌 알림입니다.")
    moment = parse_stamp(until)
    if moment is None:
        try:
            span = float(hours if hours not in (None, "") else DEFAULT_SNOOZE_HOURS)
        except (TypeError, ValueError):
            span = float(DEFAULT_SNOOZE_HOURS)
        span = min(24 * 30, max(1 / 60, span))
        moment = utc_now() + timedelta(hours=span)
    return _close(
        connection,
        row,
        status="snoozed",
        action_id=action_id,
        snooze_until=utc_stamp(moment),
    )


def dismiss_notification(
    connection: sqlite3.Connection,
    project_id: int,
    notification_id: int,
    *,
    forever: bool = False,
    action_id: str = "",
) -> dict:
    row = _fetch(connection, notification_id, project_id)
    return _close(
        connection,
        row,
        status="dismissed",
        action_id=action_id,
        forever=forever,
    )


def execute_action(
    connection: sqlite3.Connection,
    project_id: int,
    notification_id: int,
    action_id: str,
) -> dict:
    row = _fetch(connection, notification_id, project_id)
    if str(row["status"]) in {"resolved", "dismissed"}:
        raise ValueError("이미 닫힌 알림입니다.")
    actions = normalize_actions(row["actions_json"])
    action = next((item for item in actions if item["id"] == str(action_id or "").strip()), None)
    if action is None:
        raise ValueError("선택지를 찾을 수 없습니다.")
    action_type = action["type"]
    target = dict(action.get("target") or {})
    if action_type == "navigate":
        notification = serialize_notification(row)
        if action.get("resolve"):
            # 설정 모순: 원고를 고친다 → 연결 pending 회수
            if str(row["kind"] or "") == "settings_conflict":
                try:
                    payload = json.loads(row["payload_json"] or "{}")
                except json.JSONDecodeError:
                    payload = {}
                cid = str(payload.get("conflict_id") or "")
                if cid:
                    try:
                        from character_import_analysis import reclaim_pending_by_conflict as _reclaim_char
                        from world_import_analysis import reclaim_pending_by_conflict as _reclaim_world

                        _reclaim_char(connection, cid)
                        _reclaim_world(connection, cid)
                    except Exception:  # noqa: BLE001
                        pass
            notification = _close(connection, row, status="resolved", action_id=action["id"])
        elif str(row["status"]) == "unread":
            notification = mark_read(connection, project_id, notification_id)
        return {"notification": notification, "effect": {"type": "navigate", "target": target}}
    if action_type == "resolve":
        notification = _close(connection, row, status="resolved", action_id=action["id"])
        return {"notification": notification, "effect": {"type": "resolve"}}
    if action_type == "snooze":
        if str(row["kind"] or "") == "intent_suggest":
            try:
                from feedback_pipeline.literature_intent_suggest import record_snooze_baseline

                record_snooze_baseline(connection, project_id, notification_id)
            except Exception:  # noqa: BLE001
                pass
        notification = snooze_notification(
            connection,
            project_id,
            notification_id,
            hours=target.get("hours"),
            until=target.get("until"),
            action_id=action["id"],
        )
        return {"notification": notification, "effect": {"type": "snooze"}}
    if action_type == "dismiss_forever":
        notification = dismiss_notification(
            connection, project_id, notification_id, forever=True, action_id=action["id"]
        )
        return {"notification": notification, "effect": {"type": "dismiss_forever"}}
    raise ValueError("지원하지 않는 선택지입니다.")


def _first_id(connection: sqlite3.Connection, sql: str, project_id: int) -> int | None:
    try:
        row = connection.execute(sql, (int(project_id),)).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    try:
        value = int(row[0] or 0)
    except (TypeError, ValueError):
        return None
    return value or None


def install_dev_fixtures(connection: sqlite3.Connection, project_id: int) -> list[dict]:
    """Three sample notifications for local UI checks. Not called by pipelines."""
    scene_id = _first_id(
        connection,
        "SELECT id FROM scene WHERE project_id = ? ORDER BY id LIMIT 1",
        project_id,
    )
    character_id = _first_id(
        connection,
        "SELECT id FROM character WHERE project_id = ? ORDER BY id LIMIT 1",
        project_id,
    )
    samples = [
        {
            "kind": "settings_conflict",
            "title": "설정과 원고가 어긋나 있어요",
            "body": "원고의 눈동자 색과 인물 칸이 달라요.",
            "dedupe_key": "fixture:settings_conflict",
            "payload": {
                "quote": "그는 푸른 눈을 깜빡였다.",
                "scene_id": scene_id,
                "character_id": character_id,
                "field": "profile_md",
            },
            "actions": [
                {
                    "id": "fix_manuscript",
                    "label": "원고를 고친다",
                    "type": "navigate",
                    "resolve": True,
                    "target": {"surface": "scene", "scene_id": scene_id},
                },
                {
                    "id": "fix_settings",
                    "label": "설정집을 고친다",
                    "type": "navigate",
                    "resolve": True,
                    "target": {
                        "surface": "character",
                        "character_id": character_id,
                        "field": "profile_md",
                    },
                },
            ],
        },
        {
            "kind": "style_choice",
            "title": "의도적 선택으로 적어 둘까요?",
            "body": "반복이 리듬이라면 문체 칸에 남겨 두면 다음부터 문제로 보지 않아요.",
            "dedupe_key": "fixture:style_choice",
            "payload": {
                "quotes": [
                    "비가 왔다. 비가 왔다.",
                    "또 비가 왔다.",
                ],
            },
            "actions": [
                {
                    "id": "add",
                    "label": "추가",
                    "type": "navigate",
                    "resolve": True,
                    "target": {
                        "surface": "settings",
                        "section": "style",
                        "field": "style_choice",
                        "prefill": "독백의 구절 반복은 리듬을 위한 의도",
                    },
                },
                {
                    "id": "no",
                    "label": "아니요",
                    "type": "dismiss_forever",
                    "target": {},
                },
                {
                    "id": "later",
                    "label": "나중에",
                    "type": "snooze",
                    "target": {"hours": 24},
                },
            ],
        },
        {
            "kind": "notice",
            "title": "문체 칸을 읽어 두었어요",
            "body": "1~4번 칸은 나중에 토리가 채울 수 있고, 5~6번은 작가만 씁니다.",
            "dedupe_key": "fixture:notice",
            "payload": {},
            "actions": [
                {"id": "ok", "label": "확인", "type": "resolve", "target": {}},
            ],
        },
    ]
    results = []
    for sample in samples:
        results.append(create_tory_notification(connection, project_id, **sample))
    return results


def settings_conflict_dedupe_key(*, character_id: object = 0, field: object = "", chapter_id: object = 0) -> str:
    """같은 인물·칸·장이면 알림이 중복되지 않게 하는 키."""
    try:
        character = int(character_id or 0)
    except (TypeError, ValueError):
        character = 0
    try:
        chapter = int(chapter_id or 0)
    except (TypeError, ValueError):
        chapter = 0
    name = str(field or "").strip()[:80]
    return f"settings-conflict:{character}:{name}:{chapter}"


def create_settings_conflict_notification(
    connection: sqlite3.Connection,
    project_id: int,
    *,
    character_id: object = 0,
    field: object = "",
    chapter_id: object = 0,
    title: object,
    body: object = "",
    scene_id: object = 0,
    quote: object = "",
    settings_section: object = "characters",
    settings_field: object = "",
) -> dict:
    """설정 모순 알림. '원고를 고친다'는 닫고 연결 pending을 회수한다."""
    key = settings_conflict_dedupe_key(
        character_id=character_id, field=field, chapter_id=chapter_id
    )
    try:
        scene = int(scene_id or 0)
    except (TypeError, ValueError):
        scene = 0
    try:
        character = int(character_id or 0)
    except (TypeError, ValueError):
        character = 0
    try:
        chapter = int(chapter_id or 0)
    except (TypeError, ValueError):
        chapter = 0
    section = str(settings_section or "characters").strip() or "characters"
    field_name = str(settings_field or field or "").strip()
    conflict_id = key
    actions = [
        {
            "id": "fix-manuscript",
            "label": "원고를 고친다",
            "type": "navigate",
            "resolve": True,
            "target": {
                "surface": "scene",
                "scene_id": scene,
                "quote": str(quote or "")[:500],
            },
        },
        {
            "id": "fix-settings",
            "label": "설정집을 고친다",
            "type": "navigate",
            "resolve": False,
            "target": {
                "surface": "character" if section == "characters" else "settings",
                "section": section,
                "field": field_name,
                "character_id": character,
            },
        },
    ]
    return create_tory_notification(
        connection,
        project_id,
        kind="settings_conflict",
        title=title,
        body=body,
        payload={
            "conflict_id": conflict_id,
            "character_id": character,
            "field": field_name,
            "chapter_id": chapter,
            "scene_id": scene,
            "quote": str(quote or "")[:500],
        },
        actions=actions,
        dedupe_key=key,
    )


def resolve_settings_conflicts(
    connection: sqlite3.Connection,
    project_id: int,
    active_keys: set[str] | list[str],
) -> int:
    """다음 실행에서 사라졌다고 보고된 충돌 알림을 닫는다."""
    open_keys = {str(key) for key in (active_keys or [])}
    rows = connection.execute(
        "SELECT * FROM tory_notification WHERE project_id = ? AND kind = 'settings_conflict' "
        "AND status NOT IN ('resolved', 'dismissed')",
        (int(project_id),),
    ).fetchall()
    closed = 0
    for row in rows:
        if str(row["dedupe_key"] or "") in open_keys:
            continue
        _close(connection, row, status="resolved")
        closed += 1
    return closed
