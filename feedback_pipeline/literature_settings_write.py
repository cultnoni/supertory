"""장편 설정집 쓰기: 빈 칸 〔토리〕, 충돌은 pending+알림. compose 경로 금지."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

import character_import_analysis
from character_import_analysis import is_field_empty, mark_tori_text
from character_scene_traits import _record_history
import tory_notifications
import world_import_analysis


def _norm_name(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def list_characters(conn, project_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT c.id, c.name, c.profile_md, c.role,
               GROUP_CONCAT(a.alias, '\n') AS aliases_joined
        FROM character AS c
        LEFT JOIN character_alias AS a ON a.character_id = c.id
        WHERE c.project_id = ? AND c.deleted_at IS NULL
        GROUP BY c.id
        ORDER BY c.sort_order, c.id
        """,
        (int(project_id),),
    ).fetchall()
    out = []
    for row in rows:
        aliases = [part for part in str(row["aliases_joined"] or "").split("\n") if part.strip()]
        out.append(
            {
                "id": int(row["id"]),
                "name": str(row["name"] or ""),
                "profile_md": str(row["profile_md"] or ""),
                "role": str(row["role"] or ""),
                "aliases": aliases,
            }
        )
    return out


def match_character(characters: list[dict[str, Any]], name: str) -> tuple[str, dict[str, Any] | None]:
    """exact | ambiguous | new."""
    needle = _norm_name(name)
    if not needle:
        return "new", None
    exact = []
    soft = []
    for row in characters:
        names = [_norm_name(row.get("name"))] + [_norm_name(alias) for alias in (row.get("aliases") or [])]
        names = [item for item in names if item]
        if needle in names:
            exact.append(row)
            continue
        # 성만 같거나 한쪽이 다른 쪽을 포함하면 애매
        for item in names:
            if needle in item or item in needle:
                soft.append(row)
                break
            if len(needle) >= 2 and len(item) >= 2 and needle[0] == item[0] and abs(len(needle) - len(item)) <= 1:
                soft.append(row)
                break
    if len(exact) == 1:
        return "exact", exact[0]
    if exact:
        return "ambiguous", exact[0]
    if soft:
        return "ambiguous", soft[0]
    return "new", None


CHAR_WRITE_FIELDS = frozenset(
    {
        "profile_md",
        "short_description",
        "aliases",
        "role",
        "author_notes_md",
    }
)
# 자유 메모 칸 — 정해진 칸에 못 맞출 때 여기로.
FREE_MEMO_FIELD = "author_notes_md"
FIELD_ALIASES = {
    "profile": "profile_md",
    "profile_md": "profile_md",
    "인물 설정": "profile_md",
    "설정": "profile_md",
    "소개": "short_description",
    "short": "short_description",
    "short_description": "short_description",
    "한줄": "short_description",
    "별칭": "aliases",
    "alias": "aliases",
    "aliases": "aliases",
    "역할": "role",
    "role": "role",
    "메모": "author_notes_md",
    "노트": "author_notes_md",
    "작가 메모": "author_notes_md",
    "author_notes": "author_notes_md",
    "author_notes_md": "author_notes_md",
}


def _normalize_section(section: object) -> str:
    raw = str(section or "").strip()
    if raw in {"인물", "캐릭터", "character", "characters"}:
        return "characters"
    if raw in {"모티프", "복선", "떡밥", "bait", "baits"}:
        return "baits"
    if raw in {"세계관", "world", "worldbuilding"}:
        return "world"
    return raw or "characters"


def resolve_character_field(field: object) -> tuple[str, str, str]:
    """요청 필드명을 (실제 칸, 경로, 원문)으로 바꾼다.

    경로: known | alias | memo
    - known/alias: 정해진 칸(또는 별칭)으로 매핑
    - memo: 맞출 칸이 없어 author_notes_md로
    """
    raw = str(field or "").strip() or "profile_md"
    if raw in CHAR_WRITE_FIELDS:
        return raw, "known", raw
    mapped = FIELD_ALIASES.get(raw)
    if mapped and mapped in CHAR_WRITE_FIELDS:
        return mapped, "alias", raw
    return FREE_MEMO_FIELD, "memo", raw


def _normalize_field(field: object) -> str:
    return resolve_character_field(field)[0]


def apply_character_fill(
    conn,
    *,
    project_id: int,
    character_id: int,
    field: str,
    content: str,
    scene_id: int,
    conflict_id: str = "",
) -> dict[str, Any]:
    """빈 칸이면 〔토리〕로 채우고, 찬 칸이면 pending.

    반환: {result, field, requested_field, routing}
    result: filled|pending|missing
    """
    target, routing, requested = resolve_character_field(field)
    row = conn.execute(
        "SELECT * FROM character WHERE id = ? AND project_id = ?",
        (int(character_id), int(project_id)),
    ).fetchone()
    if row is None:
        return {
            "result": "missing",
            "field": target,
            "requested_field": requested,
            "routing": routing,
        }
    current = dict(row)
    if target != "aliases" and target not in current:
        target = FREE_MEMO_FIELD
        routing = "memo"

    body = str(content or "").strip()
    if routing == "memo" and requested not in CHAR_WRITE_FIELDS and requested != FREE_MEMO_FIELD:
        body = f"〔{requested}〕 {body}".strip()
    marked = mark_tori_text(body)

    if target == "aliases":
        empty = not list(
            conn.execute(
                "SELECT 1 FROM character_alias WHERE character_id = ? LIMIT 1",
                (int(character_id),),
            ).fetchall()
        )
    else:
        empty = is_field_empty(current.get(target))

    if empty:
        if target == "aliases":
            conn.execute(
                "INSERT OR IGNORE INTO character_alias(character_id, project_id, alias) VALUES (?, ?, ?)",
                (int(character_id), int(project_id), marked),
            )
        else:
            conn.execute(
                f"UPDATE character SET {target} = ? WHERE id = ?",
                (marked, int(character_id)),
            )
        _record_history(
            conn,
            character_id=int(character_id),
            project_id=int(project_id),
            scene_id=int(scene_id),
            field_name=target,
            content=marked,
            applied=True,
        )
        character_import_analysis._clear_pending(conn, int(character_id), target)
        return {
            "result": "filled",
            "field": target,
            "requested_field": requested,
            "routing": routing,
        }
    character_import_analysis._upsert_pending(
        conn,
        int(character_id),
        target,
        marked,
        conflict_id=str(conflict_id or ""),
    )
    _record_history(
        conn,
        character_id=int(character_id),
        project_id=int(project_id),
        scene_id=int(scene_id),
        field_name=target,
        content=marked,
        applied=False,
    )
    return {
        "result": "pending",
        "field": target,
        "requested_field": requested,
        "routing": routing,
        "conflict_id": str(conflict_id or ""),
    }


def create_character_row(conn, project_id: int, name: str, profile: str = "") -> int:
    order = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM character WHERE project_id = ?",
        (int(project_id),),
    ).fetchone()[0]
    marked_name = mark_tori_text(name)
    cur = conn.execute(
        "INSERT INTO character(project_id, name, role, profile_md, sort_order) VALUES (?, ?, '', ?, ?)",
        (int(project_id), marked_name, mark_tori_text(profile) if profile else "", int(order)),
    )
    return int(cur.lastrowid)


def apply_world_fill(conn, project_id: int, field: str, content: str) -> str:
    stats = world_import_analysis.apply_parsed_fields(conn, int(project_id), {field: content})
    if int(stats.get("filled") or 0):
        return "filled"
    if int(stats.get("pending") or 0):
        return "pending"
    return "skipped"


def list_baits(conn, project_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, summary, quote, recover_content, intentionally_open FROM bait WHERE project_id = ? ORDER BY created_at, id",
        (int(project_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def add_bait_row(conn, project_id: int, summary: str, *, intentionally_open: bool = False) -> str:
    bait_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO bait(id, project_id, kind, summary, intentionally_open) VALUES (?, ?, 'plant', ?, ?)",
        (bait_id, int(project_id), mark_tori_text(summary), 1 if intentionally_open else 0),
    )
    return bait_id


def conflict_dedupe(
    *,
    unit_kind: str,
    unit_id: int,
    target: str,
    field: str,
    character_id: int = 0,
    bait_id: str = "",
) -> str:
    return (
        f"settings-conflict:{unit_kind}:{int(unit_id)}:"
        f"{int(character_id)}:{bait_id}:{target}:{str(field or '').strip()[:80]}"
    )


def emit_settings_conflict(
    conn,
    project_id: int,
    *,
    unit: dict[str, Any],
    scene_id: int,
    quote: str,
    title: str,
    body: str,
    target: str,
    field: str,
    character_id: int = 0,
    bait_id: str = "",
    draft: str = "",
    settings_section: str = "characters",
    conflict_id: str = "",
) -> dict[str, Any]:
    """한 감지 결과로 알림을 만든다. conflict_id로 카드와 연결한다."""
    key = conflict_dedupe(
        unit_kind=str(unit.get("kind") or ""),
        unit_id=int(unit.get("id") or 0),
        target=target,
        field=field,
        character_id=character_id,
        bait_id=bait_id,
    )
    cid = conflict_id or key
    section = settings_section
    actions = [
        {
            "id": "fix-manuscript",
            "label": "원고를 고친다",
            "type": "navigate",
            "resolve": True,
            "target": {
                "surface": "scene",
                "scene_id": int(scene_id),
                "quote": str(quote or "")[:500],
            },
        },
        {
            "id": "fix-settings",
            "label": "설정집을 고친다",
            "type": "navigate",
            "resolve": False,
            "target": {
                "surface": "bait" if section == "baits" else ("character" if section == "characters" else "settings"),
                "section": section,
                "field": field,
                "character_id": int(character_id or 0),
                "bait_id": str(bait_id or ""),
                "prefill": str(draft or "")[:2000],
            },
        },
    ]
    notif = tory_notifications.create_tory_notification(
        conn,
        project_id,
        kind="settings_conflict",
        title=title,
        body=body,
        payload={
            "conflict_id": cid,
            "unit_kind": str(unit.get("kind") or ""),
            "unit_id": int(unit.get("id") or 0),
            "unit_ord": int(unit.get("ord") or 0),
            "character_id": int(character_id or 0),
            "bait_id": str(bait_id or ""),
            "field": field,
            "scene_id": int(scene_id),
            "quote": str(quote or "")[:500],
            "draft": str(draft or "")[:2000],
        },
        actions=actions,
        dedupe_key=key,
    )
    return {"notification": notif, "dedupe_key": key, "conflict_id": cid}


def reclaim_conflict_pendings(conn, conflict_id: str) -> int:
    """충돌 id에 묶인 pending을 회수한다. 삭제하지 않는다."""
    cid = str(conflict_id or "").strip()
    if not cid:
        return 0
    n = character_import_analysis.reclaim_pending_by_conflict(conn, cid)
    n += world_import_analysis.reclaim_pending_by_conflict(conn, cid)
    # 모티프 초안(bait)에 conflict_id가 있으면 의도적 열어둠 표시만 유지하고 요약에서 〔토리〕 초안 표시는 유지
    # bait에는 status 컬럼이 없으므로 conflict_id 매칭 행의 summary에 회수 표시를 붙이지 않고 skip
    return n


def resolve_unit_conflicts(
    conn,
    project_id: int,
    *,
    unit_kind: str,
    unit_id: int,
    active_keys: set[str] | list[str],
) -> dict[str, int]:
    """같은 단위에서 이번 실행에 없는 충돌을 닫고, 연결 pending을 회수한다.

    알림이 이미 resolved여도, 아직 pending인 연결 행은 회수한다.
    """
    open_keys = {str(key) for key in (active_keys or [])}
    rows = conn.execute(
        "SELECT * FROM tory_notification WHERE project_id = ? AND kind = 'settings_conflict'",
        (int(project_id),),
    ).fetchall()
    closed = 0
    reclaimed = 0
    seen_cids: set[str] = set()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        if str(payload.get("unit_kind") or "") != str(unit_kind or ""):
            continue
        if int(payload.get("unit_id") or 0) != int(unit_id or 0):
            continue
        if str(row["dedupe_key"] or "") in open_keys:
            continue
        cid = str(payload.get("conflict_id") or "")
        if cid and cid not in seen_cids:
            reclaimed += reclaim_conflict_pendings(conn, cid)
            seen_cids.add(cid)
        if str(row["status"] or "") not in {"resolved", "dismissed"}:
            tory_notifications._close(conn, row, status="resolved")
            closed += 1
    return {"closed_notifications": closed, "reclaimed_pendings": reclaimed}


def apply_settings_writes(
    conn,
    project_id: int,
    *,
    unit: dict[str, Any],
    scene_id: int,
    discoveries: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
) -> dict[str, Any]:
    """모델이 낸 discoveries/conflicts를 설정집·pending·알림에 반영한다.

    conflicts는 이미 한 번 감지된 목록이다. 카드와 같은 conflict_id를 쓴다.
    """
    characters = list_characters(conn, project_id)
    filled: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    created: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    active_keys: set[str] = set()
    conflict_notifs: list[dict[str, Any]] = []
    verification_log: list[dict[str, str]] = []

    def _record_fill(entry: dict[str, Any], outcome: dict[str, Any]) -> None:
        result = str(outcome.get("result") or "")
        routing = str(outcome.get("routing") or "known")
        requested = str(outcome.get("requested_field") or entry.get("field") or "")
        target = str(outcome.get("field") or "")
        merged = {**entry, **outcome, "result": result}
        if routing == "alias":
            verification_log.append(
                {
                    "action": "settings_field_alias",
                    "target": requested,
                    "detail": target,
                }
            )
        elif routing == "memo":
            verification_log.append(
                {
                    "action": "settings_field_memo",
                    "target": requested,
                    "detail": f"{target}:{result}",
                }
            )
        if result == "filled":
            filled.append(merged)
        elif result == "pending":
            pending.append(merged)
        elif result and result != "missing":
            pending.append(merged)

    # 사라진 충돌을 먼저 회수해, 이후 discoveries가 같은 칸을 써도 회수 기록이 남게 한다.
    preview_keys: set[str] = set()
    for raw in conflicts or []:
        if not isinstance(raw, dict):
            continue
        preview_keys.add(
            conflict_dedupe(
                unit_kind=str(unit.get("kind") or ""),
                unit_id=int(unit.get("id") or 0),
                target=str(raw.get("target") or "character"),
                field=str(raw.get("field") or ""),
                character_id=int(raw.get("character_id") or 0),
                bait_id=str(raw.get("bait_id") or ""),
            )
        )
    closed_info = resolve_unit_conflicts(
        conn,
        project_id,
        unit_kind=str(unit.get("kind") or ""),
        unit_id=int(unit.get("id") or 0),
        active_keys=preview_keys,
    )

    for raw in discoveries or []:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "character_field")
        if kind == "new_character":
            name = str(raw.get("name") or "").strip()
            status, matched = match_character(characters, name)
            if status == "exact" and matched is not None:
                field = str(raw.get("field") or "profile_md")
                content = str(raw.get("content") or raw.get("profile") or "")
                if content:
                    outcome = apply_character_fill(
                        conn,
                        project_id=project_id,
                        character_id=int(matched["id"]),
                        field=field,
                        content=content,
                        scene_id=scene_id,
                    )
                    _record_fill(
                        {"name": name, "character_id": matched["id"], "field": field, "content": content},
                        outcome,
                    )
                continue
            if status == "ambiguous" and matched is not None:
                notif = tory_notifications.create_tory_notification(
                    conn,
                    project_id,
                    kind="settings_conflict",
                    title="새 인물인가요?",
                    body=f"‘{name}’이(가) 설정집의 ‘{matched.get('name')}’과 같은 인물인지 확인이 필요합니다.",
                    payload={
                        "conflict_id": f"ask-character:{unit.get('kind')}:{unit.get('id')}:{name}",
                        "unit_kind": str(unit.get("kind") or ""),
                        "unit_id": int(unit.get("id") or 0),
                        "unit_ord": int(unit.get("ord") or 0),
                        "character_id": int(matched["id"]),
                        "candidate_name": name,
                        "field": "name",
                        "scene_id": int(scene_id),
                    },
                    actions=[
                        {
                            "id": "same-character",
                            "label": f"{matched.get('name')}입니다",
                            "type": "navigate",
                            "resolve": False,
                            "target": {
                                "surface": "character",
                                "character_id": int(matched["id"]),
                                "section": "characters",
                            },
                        },
                        {
                            "id": "new-character",
                            "label": "새 인물입니다",
                            "type": "navigate",
                            "resolve": False,
                            "target": {"surface": "settings", "section": "characters", "prefill": name},
                        },
                    ],
                    dedupe_key=f"ask-character:{unit.get('kind')}:{unit.get('id')}:{_norm_name(name)}",
                )
                active_keys.add(str(notif.get("dedupe_key") or ""))
                questions.append({"name": name, "matched": matched.get("name"), "notification": notif})
                continue
            new_id = create_character_row(conn, project_id, name, str(raw.get("profile") or ""))
            characters = list_characters(conn, project_id)
            created.append({"name": name, "character_id": new_id})
            continue
        if kind == "character_field":
            character_id = int(raw.get("character_id") or 0)
            field = str(raw.get("field") or "profile_md")
            content = str(raw.get("content") or "")
            if not character_id or not content:
                continue
            outcome = apply_character_fill(
                conn,
                project_id=project_id,
                character_id=character_id,
                field=field,
                content=content,
                scene_id=scene_id,
            )
            _record_fill(
                {"character_id": character_id, "field": field, "content": content},
                outcome,
            )
            continue
        if kind == "world_field":
            field = str(raw.get("field") or "")
            content = str(raw.get("content") or "")
            if not field or not content:
                continue
            result = apply_world_fill(conn, project_id, field, content)
            entry = {"field": field, "result": result, "content": content}
            (filled if result == "filled" else pending).append(entry)
            continue
        if kind == "new_bait":
            summary = str(raw.get("summary") or raw.get("content") or "").strip()
            if not summary:
                continue
            bait_id = add_bait_row(
                conn,
                project_id,
                summary,
                intentionally_open=bool(raw.get("intentionally_open")),
            )
            created.append({"bait_id": bait_id, "summary": summary})

    for raw in conflicts or []:
        if not isinstance(raw, dict):
            continue
        conflict_id = str(raw.get("conflict_id") or raw.get("id") or "")
        emitted = emit_settings_conflict(
            conn,
            project_id,
            unit=unit,
            scene_id=int(raw.get("scene_id") or scene_id),
            quote=str(raw.get("quote") or ""),
            title=str(raw.get("title") or "설정집과 어긋납니다"),
            body=str(raw.get("note") or raw.get("body") or ""),
            target=str(raw.get("target") or "character"),
            field=str(raw.get("field") or ""),
            character_id=int(raw.get("character_id") or 0),
            bait_id=str(raw.get("bait_id") or ""),
            draft=str(raw.get("draft") or raw.get("settings_draft") or ""),
            settings_section=_normalize_section(raw.get("settings_section") or "characters"),
            conflict_id=conflict_id,
        )
        active_keys.add(emitted["dedupe_key"])
        # 찬 칸과 다른 내용이면 pending에도 남긴다 (conflict_id 연결)
        cid = str(emitted.get("conflict_id") or conflict_id or "")
        if int(raw.get("character_id") or 0) and (raw.get("draft") or raw.get("note")):
            outcome = apply_character_fill(
                conn,
                project_id=project_id,
                character_id=int(raw["character_id"]),
                field=str(raw.get("field") or "profile_md"),
                content=str(raw.get("draft") or raw.get("note") or ""),
                scene_id=int(raw.get("scene_id") or scene_id),
                conflict_id=cid,
            )
            _record_fill(
                {
                    "character_id": int(raw["character_id"]),
                    "field": str(raw.get("field") or ""),
                    "conflict_id": cid,
                },
                outcome,
            )
        conflict_notifs.append(emitted)

    return {
        "filled": filled,
        "pending": pending,
        "created": created,
        "questions": questions,
        "conflicts": conflict_notifs,
        "active_keys": sorted(active_keys | preview_keys),
        "closed_notifications": int(closed_info.get("closed_notifications") or 0),
        "reclaimed_pendings": int(closed_info.get("reclaimed_pendings") or 0),
        "verification_log": verification_log,
    }
