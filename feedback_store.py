"""첨삭 피드백 실행·카드 SQLite 저장소.

연결은 호출자가 넘긴다. 커밋/롤백은 호출 쪽 트랜잭션을 따른다.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

ALLOWED_RUN_KINDS = frozenset({"analyze", "analyze_multi", "legacy"})
ALLOWED_RUN_STATUSES = frozenset({"running", "ok", "partial", "failed"})
ALLOWED_CARD_KINDS = frozenset({"correction", "consistency", "style", "structure"})
ALLOWED_CARD_STATUSES = frozenset(
    {"open", "applied", "applied_edited", "ignored", "alternate"}
)
ALLOWED_PRIORITIES = frozenset({"high", "medium", "low", "ref"})
ALLOWED_COMMENT_ROLES = frozenset({"user", "assistant"})
ALLOWED_RUN_UPDATE_FIELDS = frozenset(
    {
        "status",
        "report_json",
        "report_md",
        "raw_output",
        "planned_cards",
        "params_json",
        "finished_at",
        "model",
        "prompt_version",
    }
)
JSON_RUN_FIELDS = frozenset({"params_json", "report_json"})
JSON_OBJECT_DEFAULT = "{}"
JSON_ARRAY_DEFAULT = "[]"
APPLIED_STATUSES = frozenset({"applied", "applied_edited"})
LEGACY_MODES = frozenset({"analyze", "analyze_multi"})
CARD_COUNT_STATUSES = (
    "open",
    "applied",
    "applied_edited",
    "ignored",
    "alternate",
)


def _as_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def _now_sql(conn: sqlite3.Connection) -> str:
    return str(
        conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')").fetchone()[0]
    )


def _dumps(value: Any, default: str) -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _loads(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return fallback
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return fallback


def _int(value: Any, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name}이(가) 올바르지 않습니다.") from error


def _require_run(conn: sqlite3.Connection, run_id: int) -> dict:
    row = conn.execute(
        "SELECT id, project_id, run_kind, status FROM feedback_run WHERE id = ?",
        (int(run_id),),
    ).fetchone()
    if row is None:
        raise ValueError("피드백 실행을 찾을 수 없습니다.")
    return dict(row)


def _scene_in_project(conn: sqlite3.Connection, scene_id: int, project_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM scene WHERE id = ? AND project_id = ?",
        (int(scene_id), int(project_id)),
    ).fetchone()
    return row is not None


def create_run(
    conn: sqlite3.Connection,
    project_id: int,
    run_kind: str,
    model: str,
    prompt_version: str,
    params: Any,
) -> int:
    """실행 행을 running 상태로 만들고 id를 반환한다."""
    kind = str(run_kind or "").strip()
    if kind not in ALLOWED_RUN_KINDS:
        raise ValueError("지원하지 않는 실행 종류입니다.")
    cursor = conn.execute(
        """
        INSERT INTO feedback_run(
            project_id, run_kind, status, model, prompt_version, params_json
        ) VALUES (?, ?, 'running', ?, ?, ?)
        """,
        (
            _int(project_id, "project_id"),
            kind,
            str(model or ""),
            str(prompt_version or ""),
            _dumps(params if params is not None else {}, JSON_OBJECT_DEFAULT),
        ),
    )
    return int(cursor.lastrowid)


def add_run_scene(
    conn: sqlite3.Connection,
    run_id: int,
    project_id: int,
    scene_id: int,
    ord: int,
    scene_title: str,
    revision_no: int | None,
    source_hash: str,
    paragraphs: list | None,
) -> int:
    """실행에 회차 스냅샷을 붙인다. paragraphs는 JSON으로 저장한다."""
    run = _require_run(conn, run_id)
    project_id = _int(project_id, "project_id")
    scene_id = _int(scene_id, "scene_id")
    if int(run["project_id"]) != project_id:
        raise ValueError("실행과 프로젝트가 일치하지 않습니다.")
    if not _scene_in_project(conn, scene_id, project_id):
        raise ValueError("회차를 찾을 수 없습니다.")
    cursor = conn.execute(
        """
        INSERT INTO feedback_run_scene(
            run_id, project_id, scene_id, ord, scene_title, revision_no,
            source_hash, paragraphs_json, is_primary
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (
            int(run_id),
            project_id,
            scene_id,
            _int(ord, "ord"),
            str(scene_title or ""),
            None if revision_no is None else _int(revision_no, "revision_no"),
            str(source_hash or ""),
            _dumps(paragraphs if paragraphs is not None else [], JSON_ARRAY_DEFAULT),
        ),
    )
    return int(cursor.lastrowid)


def update_run(conn: sqlite3.Connection, run_id: int, **fields: Any) -> None:
    """실행의 허용된 필드만 갱신한다."""
    _require_run(conn, run_id)
    unknown = set(fields) - ALLOWED_RUN_UPDATE_FIELDS
    if unknown:
        raise ValueError("실행에서 바꿀 수 없는 필드입니다.")
    if not fields:
        return
    if "status" in fields:
        status = str(fields["status"] or "").strip()
        if status not in ALLOWED_RUN_STATUSES:
            raise ValueError("지원하지 않는 실행 상태입니다.")
        fields = {**fields, "status": status}
    assignments = []
    values: list[Any] = []
    for name, value in fields.items():
        if name in JSON_RUN_FIELDS:
            default = JSON_OBJECT_DEFAULT if name == "params_json" else "null"
            if name == "report_json" and value is None:
                assignments.append(f"{name} = ?")
                values.append(None)
                continue
            assignments.append(f"{name} = ?")
            values.append(_dumps(value, default if name == "params_json" else JSON_OBJECT_DEFAULT))
            continue
        if name == "planned_cards" and value is not None:
            value = _int(value, "planned_cards")
        assignments.append(f"{name} = ?")
        values.append(value)
    values.append(int(run_id))
    conn.execute(
        f"UPDATE feedback_run SET {', '.join(assignments)} WHERE id = ?",
        values,
    )


def add_cards(conn: sqlite3.Connection, run_id: int, cards: list[dict]) -> list[int]:
    """카드 목록을 실행에 이어 붙인다. ord는 기존 최대값 다음부터 부여한다."""
    run = _require_run(conn, run_id)
    project_id = int(run["project_id"])
    run_id = int(run_id)
    scene_ids = {
        int(row[0])
        for row in conn.execute(
            "SELECT scene_id FROM feedback_run_scene WHERE run_id = ?",
            (run_id,),
        ).fetchall()
    }
    next_ord = int(
        conn.execute(
            "SELECT COALESCE(MAX(ord), -1) FROM feedback_card WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
    )
    created: list[int] = []
    for raw in cards or []:
        if not isinstance(raw, dict):
            raise ValueError("카드 형식이 올바르지 않습니다.")
        scene_id = _int(raw.get("scene_id"), "scene_id")
        if scene_id not in scene_ids:
            raise ValueError("실행에 없는 회차에는 카드를 넣을 수 없습니다.")
        if not _scene_in_project(conn, scene_id, project_id):
            raise ValueError("회차를 찾을 수 없습니다.")
        kind = str(raw.get("kind") or "").strip()
        if kind not in ALLOWED_CARD_KINDS:
            raise ValueError("지원하지 않는 카드 종류입니다.")
        priority = raw.get("priority")
        if priority is not None and str(priority) not in ALLOWED_PRIORITIES:
            raise ValueError("지원하지 않는 우선순위입니다.")
        status = str(raw.get("status") or "open").strip()
        if status not in ALLOWED_CARD_STATUSES:
            raise ValueError("지원하지 않는 카드 상태입니다.")
        next_ord += 1
        alternate_of = raw.get("alternate_of")
        cursor = conn.execute(
            """
            INSERT INTO feedback_card(
                run_id, project_id, scene_id, ord, kind, style_type, certainty,
                impact, priority, start_para, end_para, start_quote, end_quote,
                original_text, anchor_hash, reason, edit_plan, suggestion,
                added_facts_json, removed_facts_json, warnings_json, report_ref,
                perspectives_json, confidence, status, final_text, alternate_of,
                title
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                run_id,
                project_id,
                scene_id,
                next_ord,
                kind,
                str(raw.get("style_type") or ""),
                None if raw.get("certainty") is None else str(raw.get("certainty")),
                None if raw.get("impact") is None else _int(raw.get("impact"), "impact"),
                None if priority is None else str(priority),
                raw.get("start_para"),
                raw.get("end_para"),
                str(raw.get("start_quote") or ""),
                str(raw.get("end_quote") or ""),
                str(raw.get("original_text") or ""),
                str(raw.get("anchor_hash") or ""),
                str(raw.get("reason") or ""),
                str(raw.get("edit_plan") or ""),
                raw.get("suggestion"),
                _dumps(raw.get("added_facts_json", []), JSON_ARRAY_DEFAULT),
                _dumps(raw.get("removed_facts_json", []), JSON_ARRAY_DEFAULT),
                _dumps(raw.get("warnings_json", []), JSON_ARRAY_DEFAULT),
                raw.get("report_ref"),
                _dumps(raw.get("perspectives_json", []), JSON_ARRAY_DEFAULT),
                raw.get("confidence"),
                status,
                raw.get("final_text"),
                None if alternate_of is None else _int(alternate_of, "alternate_of"),
                str(raw.get("title") or ""),
            ),
        )
        created.append(int(cursor.lastrowid))
    return created


def update_card_priorities(
    conn: sqlite3.Connection, run_id: int, mapping: dict[int, str]
) -> None:
    """실행에 속한 카드의 priority만 바꾼다."""
    run = _require_run(conn, run_id)
    run_id = int(run["id"])
    for card_id, priority in (mapping or {}).items():
        value = str(priority or "").strip()
        if value not in ALLOWED_PRIORITIES:
            raise ValueError("지원하지 않는 우선순위입니다.")
        cursor = conn.execute(
            "UPDATE feedback_card SET priority = ? WHERE id = ? AND run_id = ?",
            (value, _int(card_id, "card_id"), run_id),
        )
        if cursor.rowcount == 0:
            raise ValueError("이 실행의 카드가 아닙니다.")


def list_runs(
    conn: sqlite3.Connection, project_id: int, scene_id: int | None = None
) -> list[dict]:
    """프로젝트 실행을 최신순으로 반환한다. 리포트 본문은 빼고 상태별 카드 수를 붙인다."""
    project_id = _int(project_id, "project_id")
    scene_filter = None if scene_id is None else _int(scene_id, "scene_id")
    rows = conn.execute(
        """
        SELECT
            r.id, r.project_id, r.run_kind, r.status, r.model, r.prompt_version,
            r.params_json, r.planned_cards, r.created_at, r.finished_at,
            COALESCE(cnt.cards_open, 0) AS cards_open,
            COALESCE(cnt.cards_applied, 0) AS cards_applied,
            COALESCE(cnt.cards_applied_edited, 0) AS cards_applied_edited,
            COALESCE(cnt.cards_ignored, 0) AS cards_ignored,
            COALESCE(cnt.cards_alternate, 0) AS cards_alternate,
            COALESCE(prim.is_primary, 0) AS is_primary,
            sc.scene_id AS scene_id,
            COALESCE(sc.scene_title, '') AS scene_title
        FROM feedback_run AS r
        LEFT JOIN (
            SELECT
                run_id,
                SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS cards_open,
                SUM(CASE WHEN status = 'applied' THEN 1 ELSE 0 END) AS cards_applied,
                SUM(CASE WHEN status = 'applied_edited' THEN 1 ELSE 0 END)
                    AS cards_applied_edited,
                SUM(CASE WHEN status = 'ignored' THEN 1 ELSE 0 END) AS cards_ignored,
                SUM(CASE WHEN status = 'alternate' THEN 1 ELSE 0 END) AS cards_alternate
            FROM feedback_card
            WHERE ? IS NULL OR scene_id = ?
            GROUP BY run_id
        ) AS cnt ON cnt.run_id = r.id
        LEFT JOIN (
            SELECT run_id, MAX(is_primary) AS is_primary
            FROM feedback_run_scene
            WHERE ? IS NULL OR scene_id = ?
            GROUP BY run_id
        ) AS prim ON prim.run_id = r.id
        LEFT JOIN feedback_run_scene AS sc
          ON sc.id = (
            SELECT s2.id
            FROM feedback_run_scene AS s2
            WHERE s2.run_id = r.id
              AND (? IS NULL OR s2.scene_id = ?)
            ORDER BY s2.is_primary DESC, s2.ord ASC, s2.id ASC
            LIMIT 1
          )
        WHERE r.project_id = ?
          AND (
              ? IS NULL
              OR EXISTS (
                  SELECT 1 FROM feedback_run_scene AS s
                  WHERE s.run_id = r.id AND s.scene_id = ?
              )
          )
        ORDER BY r.created_at DESC, r.id DESC
        """,
        (
            scene_filter,
            scene_filter,
            scene_filter,
            scene_filter,
            scene_filter,
            scene_filter,
            project_id,
            scene_filter,
            scene_filter,
        ),
    ).fetchall()
    results = []
    for row in rows:
        item = dict(row)
        item["params"] = _loads(item.pop("params_json"), {})
        item["is_primary"] = int(item.get("is_primary") or 0)
        item["scene_id"] = int(item["scene_id"]) if item.get("scene_id") is not None else None
        item["scene_title"] = str(item.get("scene_title") or "")
        item["card_counts"] = {
            status: int(item.pop(f"cards_{status}") or 0) for status in CARD_COUNT_STATUSES
        }
        results.append(item)
    return results


def get_run(conn: sqlite3.Connection, run_id: int) -> dict | None:
    """실행 한 건을 회차 스냅샷(문단 포함)·카드·리포트와 함께 반환한다."""
    row = conn.execute(
        "SELECT * FROM feedback_run WHERE id = ?",
        (_int(run_id, "run_id"),),
    ).fetchone()
    if row is None:
        return None
    run = dict(row)
    run["params"] = _loads(run.pop("params_json"), {})
    run["report"] = _loads(run.get("report_json"), None)
    scenes = []
    for scene_row in conn.execute(
        """
        SELECT * FROM feedback_run_scene
        WHERE run_id = ?
        ORDER BY ord, id
        """,
        (int(run["id"]),),
    ).fetchall():
        scene = dict(scene_row)
        scene["paragraphs"] = _loads(scene.pop("paragraphs_json"), [])
        scenes.append(scene)
    cards = []
    for card_row in conn.execute(
        """
        SELECT c.*, COALESCE(cc.n, 0) AS comment_count
        FROM feedback_card AS c
        LEFT JOIN (
            SELECT card_id, COUNT(*) AS n
            FROM feedback_card_comment
            GROUP BY card_id
        ) AS cc ON cc.card_id = c.id
        WHERE c.run_id = ?
        ORDER BY c.ord, c.id
        """,
        (int(run["id"]),),
    ).fetchall():
        card = dict(card_row)
        card["comment_count"] = int(card.get("comment_count") or 0)
        for key, fallback in (
            ("added_facts_json", []),
            ("removed_facts_json", []),
            ("warnings_json", []),
            ("perspectives_json", []),
        ):
            parsed_key = key.removesuffix("_json")
            card[parsed_key] = _loads(card.pop(key), fallback)
        cards.append(card)
    run["scenes"] = scenes
    run["cards"] = cards
    return run


def set_card_status(
    conn: sqlite3.Connection,
    card_id: int,
    status: str,
    final_text: str | None = None,
) -> None:
    """카드 상태만 바꾼다. final_text는 applied_edited일 때만 받는다."""
    card_id = _int(card_id, "card_id")
    status = str(status or "").strip()
    if status not in ALLOWED_CARD_STATUSES:
        raise ValueError("지원하지 않는 카드 상태입니다.")
    if final_text is not None and status != "applied_edited":
        raise ValueError("수정 반영 본문은 applied_edited에서만 저장합니다.")
    row = conn.execute(
        "SELECT id FROM feedback_card WHERE id = ?",
        (card_id,),
    ).fetchone()
    if row is None:
        raise ValueError("카드를 찾을 수 없습니다.")
    now = _now_sql(conn)
    assignments = ["status = ?", "status_changed_at = ?"]
    values: list[Any] = [status, now]
    if status in APPLIED_STATUSES:
        assignments.append("applied_at = ?")
        values.append(now)
    if status == "applied_edited" and final_text is not None:
        assignments.append("final_text = ?")
        values.append(final_text)
    values.append(card_id)
    conn.execute(
        f"UPDATE feedback_card SET {', '.join(assignments)} WHERE id = ?",
        values,
    )


def set_primary(conn: sqlite3.Connection, run_id: int, scene_id: int) -> None:
    """같은 회차의 기존 primary를 해제하고 이 실행을 primary로 지정한다."""
    run = _require_run(conn, run_id)
    run_id = int(run["id"])
    scene_id = _int(scene_id, "scene_id")
    row = conn.execute(
        "SELECT id FROM feedback_run_scene WHERE run_id = ? AND scene_id = ?",
        (run_id, scene_id),
    ).fetchone()
    if row is None:
        raise ValueError("이 실행에 해당 회차 스냅샷이 없습니다.")
    conn.execute(
        "UPDATE feedback_run_scene SET is_primary = 0 WHERE scene_id = ? AND is_primary = 1",
        (scene_id,),
    )
    conn.execute(
        "UPDATE feedback_run_scene SET is_primary = 1 WHERE run_id = ? AND scene_id = ?",
        (run_id, scene_id),
    )


def delete_run(conn: sqlite3.Connection, run_id: int) -> None:
    """카드·회차 스냅샷·실행을 휴지통 없이 삭제한다."""
    run = _require_run(conn, run_id)
    run_id = int(run["id"])
    conn.execute(
        "UPDATE feedback_card SET alternate_of = NULL WHERE run_id = ?",
        (run_id,),
    )
    conn.execute("DELETE FROM feedback_card WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM feedback_run_scene WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM feedback_run WHERE id = ?", (run_id,))


def delete_scene_feedback(conn: sqlite3.Connection, scene_id: int) -> None:
    """회차의 카드와 스냅샷을 지우고, 스냅샷이 비면 실행도 삭제한다."""
    scene_id = _int(scene_id, "scene_id")
    run_ids = [
        int(row[0])
        for row in conn.execute(
            """
            SELECT DISTINCT run_id FROM (
                SELECT run_id FROM feedback_run_scene WHERE scene_id = ?
                UNION
                SELECT run_id FROM feedback_card WHERE scene_id = ?
            )
            """,
            (scene_id, scene_id),
        ).fetchall()
    ]
    card_ids = [
        int(row[0])
        for row in conn.execute(
            "SELECT id FROM feedback_card WHERE scene_id = ?",
            (scene_id,),
        ).fetchall()
    ]
    if card_ids:
        placeholders = ",".join("?" * len(card_ids))
        conn.execute(
            f"UPDATE feedback_card SET alternate_of = NULL WHERE alternate_of IN ({placeholders})",
            card_ids,
        )
        conn.execute(
            f"DELETE FROM feedback_card WHERE id IN ({placeholders})",
            card_ids,
        )
    conn.execute("DELETE FROM feedback_run_scene WHERE scene_id = ?", (scene_id,))
    for run_id in run_ids:
        leftover = conn.execute(
            "SELECT 1 FROM feedback_run_scene WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if leftover is None:
            delete_run(conn, run_id)


def import_legacy_entries(
    conn: sqlite3.Connection, project_id: int, entries: list[dict]
) -> list[int]:
    """localStorage 히스토리를 run_kind=legacy로 넣는다. 같은 id는 건너뛴다."""
    project_id = _int(project_id, "project_id")
    created: list[int] = []
    for raw in entries or []:
        if not isinstance(raw, dict):
            continue
        mode = str(raw.get("mode") or "").strip()
        if mode not in LEGACY_MODES:
            continue
        legacy_id = str(raw.get("id") or "").strip()
        if not legacy_id:
            continue
        existing = conn.execute(
            """
            SELECT 1 FROM feedback_run
            WHERE project_id = ? AND run_kind = 'legacy'
              AND json_extract(params_json, '$.legacy_id') = ?
            """,
            (project_id, legacy_id),
        ).fetchone()
        if existing is not None:
            continue
        params = {
            "legacy_id": legacy_id,
            "mode": mode,
            "modeLabel": str(raw.get("modeLabel") or ""),
        }
        created_at = str(raw.get("createdAt") or "").strip() or None
        cursor = conn.execute(
            """
            INSERT INTO feedback_run(
                project_id, run_kind, status, model, prompt_version, params_json,
                report_md, planned_cards, created_at, finished_at
            ) VALUES (?, 'legacy', 'ok', '', '', ?, ?, 0, COALESCE(?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')), ?)
            """,
            (
                project_id,
                json.dumps(params, ensure_ascii=False),
                str(raw.get("text") or ""),
                created_at,
                created_at,
            ),
        )
        run_id = int(cursor.lastrowid)
        scene_raw = raw.get("sceneId")
        if scene_raw not in (None, ""):
            try:
                scene_id = int(scene_raw)
            except (TypeError, ValueError):
                scene_id = None
            if scene_id is not None and _scene_in_project(conn, scene_id, project_id):
                add_run_scene(
                    conn,
                    run_id,
                    project_id,
                    scene_id,
                    0,
                    str(raw.get("sceneTitle") or ""),
                    None,
                    "",
                    [],
                )
        created.append(run_id)
    return created


def get_card(conn: sqlite3.Connection, card_id: int) -> dict | None:
    """카드 한 건. 없으면 None. 댓글 개수를 붙인다."""
    row = conn.execute(
        """
        SELECT c.*, COALESCE(cc.n, 0) AS comment_count
        FROM feedback_card AS c
        LEFT JOIN (
            SELECT card_id, COUNT(*) AS n
            FROM feedback_card_comment
            GROUP BY card_id
        ) AS cc ON cc.card_id = c.id
        WHERE c.id = ?
        """,
        (_int(card_id, "card_id"),),
    ).fetchone()
    if row is None:
        return None
    card = dict(row)
    card["comment_count"] = int(card.get("comment_count") or 0)
    for key, fallback in (
        ("added_facts_json", []),
        ("removed_facts_json", []),
        ("warnings_json", []),
        ("perspectives_json", []),
    ):
        parsed_key = key.removesuffix("_json")
        card[parsed_key] = _loads(card.pop(key), fallback)
    return card


def add_card_comment(
    conn: sqlite3.Connection, card_id: int, role: str, body: str
) -> int:
    """카드에 댓글 한 줄을 붙이고 id를 반환한다. 커밋은 호출자가 한다."""
    card_id = _int(card_id, "card_id")
    role = str(role or "").strip()
    if role not in ALLOWED_COMMENT_ROLES:
        raise ValueError("지원하지 않는 댓글 역할입니다.")
    text = str(body or "").strip()
    if not text:
        raise ValueError("댓글 내용이 비어 있습니다.")
    card = conn.execute(
        "SELECT id, run_id, project_id FROM feedback_card WHERE id = ?",
        (card_id,),
    ).fetchone()
    if card is None:
        raise LookupError("카드를 찾을 수 없습니다.")
    cursor = conn.execute(
        """
        INSERT INTO feedback_card_comment(card_id, run_id, project_id, role, body)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            int(card["id"]),
            int(card["run_id"]),
            int(card["project_id"]),
            role,
            text,
        ),
    )
    return int(cursor.lastrowid)


def list_card_comments(conn: sqlite3.Connection, card_id: int) -> list[dict]:
    """카드 댓글을 오래된 것부터 반환한다."""
    card_id = _int(card_id, "card_id")
    rows = conn.execute(
        """
        SELECT id, card_id, run_id, project_id, role, body, created_at
        FROM feedback_card_comment
        WHERE card_id = ?
        ORDER BY created_at ASC, id ASC
        """,
        (card_id,),
    ).fetchall()
    return [dict(row) for row in rows]
