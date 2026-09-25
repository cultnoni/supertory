"""거절 기반 의도적 선택 제안.

문학 교정 카드가 ignored로 바뀔 때 같은 유형 거절이 쌓이면
문체 5번(의도적 선택) 초안을 토리 알림으로 제안한다.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from feedback_pipeline import prompt_loader
from feedback_pipeline.config import (
    HAIKU_45_MODEL,
    INTENT_SUGGEST_MIN_REJECTS,
    INTENT_SUGGEST_MIN_RUNS,
    INTENT_SUGGEST_PIPELINES,
)
from feedback_pipeline.literature_cache import with_schema_instruction
from feedback_pipeline.literature_cards import TAG_LABELS, _choice_hits
from feedback_pipeline.literature_text import loose_norm
import tory_notifications

KIND = "intent_suggest"
EXCLUDED_TAGS = frozenset({"settings_conflict"})


def intent_suggest_dedupe_key(project_id: int, tag: str) -> str:
    return f"intent_suggest:{int(project_id)}:{str(tag or '').strip()}"


def parse_issue_tags(raw: object) -> list[str]:
    if isinstance(raw, list):
        values = raw
    else:
        text = str(raw or "").strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return []
        values = parsed if isinstance(parsed, list) else []
    out: list[str] = []
    for item in values:
        tag = str(item or "").strip()
        if tag and tag not in out:
            out.append(tag)
    return out


def single_countable_tag(tags: list[str]) -> str | None:
    """유형이 정확히 하나이고 설정 모순이 아닐 때만 센다."""
    if len(tags) != 1:
        return None
    tag = tags[0]
    if tag in EXCLUDED_TAGS:
        return None
    return tag


def tag_label(tag: str) -> str:
    return TAG_LABELS.get(str(tag or "").strip(), str(tag or "").strip()) or str(tag or "")


def style_choice_covers_tag(style_choice: str, tag: str) -> bool:
    """문체 5번에 이미 그 유형 관련 내용이 있으면 True."""
    choice = str(style_choice or "").strip()
    if not choice:
        return False
    label = tag_label(tag)
    blob = loose_norm(choice)
    if label and loose_norm(label) in blob:
        return True
    if tag and loose_norm(tag) in blob:
        return True
    # 짧은 판정: 라벨·태그 단어가 칸에 있으면 관련으로 본다
    return _choice_hits(label or tag, choice)


def list_rejected_cards(
    conn: sqlite3.Connection,
    project_id: int,
    tag: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT c.id, c.run_id, c.original_text, c.reason, c.issue_tags_json, c.status,
               r.pipeline, r.status AS run_status
        FROM feedback_card AS c
        JOIN feedback_run AS r ON r.id = c.run_id
        WHERE c.project_id = ?
          AND c.status = 'ignored'
          AND r.pipeline IN ({pipes})
          AND r.status IN ('ok', 'partial')
        ORDER BY c.id
        """.format(
            pipes=", ".join("?" for _ in INTENT_SUGGEST_PIPELINES)
        ),
        (int(project_id), *sorted(INTENT_SUGGEST_PIPELINES)),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        tags = parse_issue_tags(row["issue_tags_json"])
        if single_countable_tag(tags) != tag:
            continue
        out.append(
            {
                "id": int(row["id"]),
                "run_id": int(row["run_id"]),
                "original_text": str(row["original_text"] or ""),
                "reason": str(row["reason"] or ""),
                "tags": tags,
            }
        )
    return out


def rejection_stats(
    conn: sqlite3.Connection,
    project_id: int,
    tag: str,
) -> dict[str, Any]:
    cards = list_rejected_cards(conn, project_id, tag)
    run_ids = sorted({int(item["run_id"]) for item in cards})
    return {
        "tag": tag,
        "count": len(cards),
        "run_count": len(run_ids),
        "run_ids": run_ids,
        "cards": cards,
    }


def should_propose(
    stats: dict[str, Any],
    *,
    style_choice: str = "",
    min_rejects: int = INTENT_SUGGEST_MIN_REJECTS,
    min_runs: int = INTENT_SUGGEST_MIN_RUNS,
) -> bool:
    if int(stats.get("count") or 0) < int(min_rejects):
        return False
    if int(stats.get("run_count") or 0) < int(min_runs):
        return False
    if style_choice_covers_tag(style_choice, str(stats.get("tag") or "")):
        return False
    return True


def draft_intent_sentence(
    claude: Any,
    *,
    tag: str,
    cards: list[dict[str, Any]],
    model: str = HAIKU_45_MODEL,
) -> dict[str, Any]:
    samples = []
    for item in cards[:5]:
        samples.append(
            {
                "original": str(item.get("original_text") or "")[:200],
                "reason": str(item.get("reason") or "")[:160],
            }
        )
    schema = prompt_loader.load_json("literature/intent_choice_draft_schema.json")
    prompt = prompt_loader.fill_template(
        prompt_loader.load_text("literature/intent_choice_draft_prompt.txt"),
        {
            "tag_label": tag_label(tag),
            "cards": json.dumps(samples, ensure_ascii=False),
        },
    )
    result = claude.generate(
        with_schema_instruction(prompt, schema),
        model=model,
        system="초안만. 공통 패턴 범위 + (이유: ). 해석·나레이터 금지. JSON만.",
        thinking="off",
        max_tokens=300,
        timeout=60.0,
    )
    parsed = result.get("parsed") if isinstance(result.get("parsed"), dict) else {}
    draft = str(parsed.get("draft") or "").strip()
    if not draft:
        draft = f"{tag_label(tag)}을(를) 의도적으로 쓰는 것은 의도. (이유: )"
    # 모델이 빈 칸을 빼먹으면 붙인다
    if "(이유" not in draft.replace(" ", ""):
        draft = draft.rstrip(".。") + ". (이유: )"
    draft = draft.replace("나레이터", "화자").replace("내레이터", "화자")
    return {"draft": draft[:160], "result": result}


def _quote_samples(cards: list[dict[str, Any]], *, limit: int = 3) -> list[str]:
    quotes: list[str] = []
    for item in cards:
        text = str(item.get("original_text") or "").strip()
        if not text:
            continue
        clipped = text[:120]
        if clipped not in quotes:
            quotes.append(clipped)
        if len(quotes) >= limit:
            break
    return quotes


def _find_notification(
    conn: sqlite3.Connection,
    project_id: int,
    dedupe_key: str,
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM tory_notification WHERE project_id = ? AND dedupe_key = ? "
        "ORDER BY id DESC LIMIT 1",
        (int(project_id), str(dedupe_key)),
    ).fetchone()


def _payload_loads(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {}
    try:
        raw = json.loads(row["payload_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def build_actions(draft: str) -> list[dict[str, Any]]:
    return [
        {
            "id": "add",
            "label": "추가",
            "type": "navigate",
            "resolve": True,
            "target": {
                "surface": "settings",
                "section": "style",
                "field": "style_choice",
                "prefill": draft,
            },
        },
        {"id": "no", "label": "아니요", "type": "dismiss_forever", "target": {}},
        {"id": "later", "label": "나중에", "type": "snooze", "target": {"hours": 24}},
    ]


def create_or_refresh_intent_suggest(
    conn: sqlite3.Connection,
    project_id: int,
    *,
    tag: str,
    stats: dict[str, Any],
    draft: str,
) -> dict[str, Any]:
    label = tag_label(tag)
    count = int(stats.get("count") or 0)
    quotes = _quote_samples(stats.get("cards") or [])
    dedupe = intent_suggest_dedupe_key(project_id, tag)
    title = f"최근 피드백에서 '{label}' 제안을 {count}번 적용하지 않으셨어요"
    body = (
        f"의도적인 선택이라면 문체의 '의도적 선택'에 적어둘까요?"
    )
    payload = {
        "tag": tag,
        "tag_label": label,
        "reject_count": count,
        "run_ids": list(stats.get("run_ids") or []),
        "quotes": quotes,
        "draft": draft,
        "card_ids": [int(item["id"]) for item in (stats.get("cards") or [])[:8]],
    }
    existing = _find_notification(conn, project_id, dedupe)
    if existing is not None and int(existing["dismissed_forever"] or 0):
        return {"created": False, "updated": False, "blocked": True, "reason": "dismissed_forever"}
    if existing is not None and str(existing["status"]) == "snoozed":
        prev = _payload_loads(existing)
        baseline = int(prev.get("snooze_baseline_count") or prev.get("reject_count") or 0)
        if count < baseline + INTENT_SUGGEST_MIN_REJECTS:
            return {
                "created": False,
                "updated": False,
                "blocked": False,
                "reason": "snooze_waiting",
                "baseline": baseline,
                "count": count,
            }
        # 기준만큼 새 거절이 쌓였으면 다시 묻는다
        stamp = tory_notifications.utc_stamp()
        conn.execute(
            "UPDATE tory_notification SET status = 'unread', snooze_until = NULL, "
            "kind = ?, title = ?, body = ?, payload_json = ?, actions_json = ?, updated_at = ? "
            "WHERE id = ?",
            (
                KIND,
                title,
                body,
                json.dumps(payload, ensure_ascii=False),
                json.dumps(build_actions(draft), ensure_ascii=False),
                stamp,
                int(existing["id"]),
            ),
        )
        row = tory_notifications._fetch(conn, int(existing["id"]), int(project_id))
        return {
            "created": False,
            "updated": True,
            "blocked": False,
            "reason": "snooze_reasked",
            "notification": tory_notifications.serialize_notification(row),
        }
    result = tory_notifications.create_tory_notification(
        conn,
        project_id,
        kind=KIND,
        title=title,
        body=body,
        payload=payload,
        actions=build_actions(draft),
        dedupe_key=dedupe,
    )
    result["reason"] = "created" if result.get("created") else ("updated" if result.get("updated") else "blocked")
    return result


def record_snooze_baseline(
    conn: sqlite3.Connection,
    project_id: int,
    notification_id: int,
) -> None:
    """나중에(snooze) 선택 시 현재 거절 수를 기준으로 저장한다."""
    row = conn.execute(
        "SELECT * FROM tory_notification WHERE id = ? AND project_id = ?",
        (int(notification_id), int(project_id)),
    ).fetchone()
    if row is None or str(row["kind"] or "") != KIND:
        return
    payload = _payload_loads(row)
    tag = str(payload.get("tag") or "")
    if not tag:
        return
    stats = rejection_stats(conn, project_id, tag)
    payload["snooze_baseline_count"] = int(stats.get("count") or 0)
    payload["reject_count"] = int(stats.get("count") or 0)
    conn.execute(
        "UPDATE tory_notification SET payload_json = ?, updated_at = ? WHERE id = ?",
        (
            json.dumps(payload, ensure_ascii=False),
            tory_notifications.utc_stamp(),
            int(notification_id),
        ),
    )


def on_card_status_changed(
    conn: sqlite3.Connection,
    card_id: int,
    *,
    claude: Any = None,
) -> dict[str, Any] | None:
    """카드 상태 변경 후 호출. 제안 조건이면 알림을 만든다."""
    row = conn.execute(
        """
        SELECT c.id, c.project_id, c.status, c.issue_tags_json, r.pipeline
        FROM feedback_card AS c
        JOIN feedback_run AS r ON r.id = c.run_id
        WHERE c.id = ?
        """,
        (int(card_id),),
    ).fetchone()
    if row is None:
        return None
    if str(row["pipeline"] or "") not in INTENT_SUGGEST_PIPELINES:
        return {"skipped": True, "reason": "not_literature"}
    tags = parse_issue_tags(row["issue_tags_json"])
    tag = single_countable_tag(tags)
    if not tag:
        return {"skipped": True, "reason": "multi_or_excluded_tag"}
    # ignored가 아니어도 셈은 다시 계산(적용 전환 시 차감 → 조건 미달이면 새로 만들지 않음)
    project_id = int(row["project_id"])
    project = conn.execute(
        "SELECT style_choice FROM project WHERE id = ?",
        (project_id,),
    ).fetchone()
    style_choice = str(project["style_choice"] or "") if project is not None else ""
    stats = rejection_stats(conn, project_id, tag)
    if not should_propose(stats, style_choice=style_choice):
        return {
            "skipped": True,
            "reason": "threshold_or_covered",
            "stats": {"count": stats["count"], "run_count": stats["run_count"]},
        }
    if claude is None:
        return {"skipped": True, "reason": "no_claude", "stats": stats}
    draft_bundle = draft_intent_sentence(claude, tag=tag, cards=stats["cards"])
    created = create_or_refresh_intent_suggest(
        conn,
        project_id,
        tag=tag,
        stats=stats,
        draft=str(draft_bundle.get("draft") or ""),
    )
    created["draft"] = draft_bundle.get("draft")
    created["stats"] = {"count": stats["count"], "run_count": stats["run_count"], "tag": tag}
    created["usage"] = (draft_bundle.get("result") or {}).get("usage")
    return created


__all__ = [
    "KIND",
    "build_actions",
    "create_or_refresh_intent_suggest",
    "draft_intent_sentence",
    "intent_suggest_dedupe_key",
    "list_rejected_cards",
    "on_card_status_changed",
    "parse_issue_tags",
    "record_snooze_baseline",
    "rejection_stats",
    "should_propose",
    "single_countable_tag",
    "style_choice_covers_tag",
    "tag_label",
]
