"""장르 카드의 거절 기반 의도 선언 제안.

장르 카드(report_ref가 ``GENRE:``로 시작하는 지적형 카드)를 같은 유형으로 여러 번 적용하지
않으면 설정집 칸에 넣을 초안을 토리 알림으로 제안한다.
- 장르문학: 「장르 약속」의 「일부러 깬 약속」
- 웹소설: 「장르 클리셰」의 「의도적으로 쓴 클리셰」
세기·조건·알림 흐름은 문학의 의도적 선택 제안(literature_intent_suggest)과 같고,
센 대상 카드와 알림이 가리키는 칸만 다르다. 자동으로 추가하지 않는다.

카드 집계는 클러스터와 무관하다. 칸·문구만 ``cluster_id``로 가른다
(docs/webnovel-genre-pipeline.md 5단계, 결정 사항 1번: 장르문학 기본 경로는 그대로).

설계는 docs/genre-pipeline.md의 「거절 기반 의도적 선택 제안」과
docs/webnovel-genre-pipeline.md의 「설정집과 작가 선언」을 따른다.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import genre_track
import webnovel_genre_track
from feedback_pipeline import genre_layers, prompt_loader
from feedback_pipeline.config import (
    HAIKU_MODEL,
    INTENT_SUGGEST_MIN_REJECTS,
    INTENT_SUGGEST_MIN_RUNS,
)
from feedback_pipeline.literature_cache import with_schema_instruction
from feedback_pipeline.literature_cards import _choice_hits
from feedback_pipeline.literature_intent_suggest import (
    KIND,
    create_or_refresh_intent_suggest,
)
from feedback_pipeline.literature_text import loose_norm

PROMISE_FIELD = genre_track.PROMISE_BROKEN
CLICHE_FIELD = webnovel_genre_track.CLICHE_USED
REF_PREFIX = "GENRE:"

_PROMISE_SPEC = {
    "kind": "genre",
    "field": PROMISE_FIELD,
    "section": "promise",
    "prompt_file": "genre/intent_promise_draft_prompt.txt",
    "empty_draft": "{label}을(를) 일부러 그렇게 쓰는 것은 의도. (이유: )",
    "body": "의도한 설계라면 장르 약속의 '일부러 깬 약속'에 적어둘까요?",
}
_CLICHE_SPEC = {
    "kind": "webnovel",
    "field": CLICHE_FIELD,
    "section": "cliche",
    "prompt_file": "genre/intent_cliche_draft_prompt.txt",
    "empty_draft": "{label}을(를) 의도적으로 쓰는 것은 의도. (이유: )",
    "body": "의도한 클리셰라면 장르 클리셰의 '의도적으로 쓴 클리셰'에 적어둘까요?",
}


def spec_for_cluster(cluster_id: object) -> dict[str, str]:
    """알림이 여는 칸. 웹소설만 클리셰, 나머지는 장르문학 「장르 약속」."""
    if str(cluster_id or "").strip() == webnovel_genre_track.WEBNOVEL_CLUSTER:
        return dict(_CLICHE_SPEC)
    return dict(_PROMISE_SPEC)


def dedupe_key(project_id: int, tag: str, *, kind: str = "genre") -> str:
    return f"intent_suggest:{int(project_id)}:{kind}:{str(tag or '').strip()}"


def tag_label(tag: str) -> str:
    key = str(tag or "").strip()
    for layer in genre_layers.LAYERS.values():
        labels = getattr(layer, "CARD_LABELS", {})
        if key in labels:
            return labels[key]
    return key


def promise_covers_tag(promise_broken: str, tag: str) -> bool:
    """「일부러 깬 약속」에 이미 그 유형 이야기가 있으면 True."""
    promise = str(promise_broken or "").strip()
    if not promise:
        return False
    label = tag_label(tag)
    blob = loose_norm(promise)
    if label and loose_norm(label) in blob:
        return True
    if tag and loose_norm(tag) in blob:
        return True
    return _choice_hits(label or tag, promise)


def list_rejected_cards(conn: sqlite3.Connection, project_id: int, tag: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT c.id, c.run_id, c.original_text, c.reason
        FROM feedback_card AS c
        JOIN feedback_run AS r ON r.id = c.run_id
        WHERE c.project_id = ?
          AND c.status = 'ignored'
          AND c.report_ref LIKE 'GENRE:%'
          AND c.style_type = ?
          AND r.status IN ('ok', 'partial')
        ORDER BY c.id
        """,
        (int(project_id), str(tag)),
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "run_id": int(row["run_id"]),
            "original_text": str(row["original_text"] or ""),
            "reason": str(row["reason"] or ""),
            "tags": [str(tag)],
        }
        for row in rows
    ]


def rejection_stats(conn: sqlite3.Connection, project_id: int, tag: str) -> dict[str, Any]:
    cards = list_rejected_cards(conn, project_id, tag)
    run_ids = sorted({int(item["run_id"]) for item in cards})
    return {"tag": tag, "count": len(cards), "run_count": len(run_ids), "run_ids": run_ids, "cards": cards}


def should_propose(
    stats: dict[str, Any],
    *,
    promise_broken: str = "",
    min_rejects: int = INTENT_SUGGEST_MIN_REJECTS,
    min_runs: int = INTENT_SUGGEST_MIN_RUNS,
) -> bool:
    if int(stats.get("count") or 0) < int(min_rejects):
        return False
    if int(stats.get("run_count") or 0) < int(min_runs):
        return False
    return not promise_covers_tag(promise_broken, str(stats.get("tag") or ""))


def draft_promise_sentence(
    claude: Any,
    *,
    tag: str,
    cards: list[dict[str, Any]],
    model: str = HAIKU_MODEL,
    prompt_file: str = "genre/intent_promise_draft_prompt.txt",
    empty_draft: str = "",
) -> dict[str, Any]:
    samples = [
        {"original": str(item.get("original_text") or "")[:200], "reason": str(item.get("reason") or "")[:160]}
        for item in cards[:5]
    ]
    schema = prompt_loader.load_json("literature/intent_choice_draft_schema.json")
    prompt = prompt_loader.fill_template(
        prompt_loader.load_text(prompt_file),
        {"tag_label": tag_label(tag), "cards": json.dumps(samples, ensure_ascii=False)},
    )
    result = claude.generate(
        with_schema_instruction(prompt, schema),
        model=model,
        system="초안만. 공통 패턴 범위 + (이유: ). 해석 금지. JSON만.",
        thinking="off",
        max_tokens=300,
        timeout=60.0,
        feature="feedback.webnovel.promise_draft",
    )
    parsed = result.get("parsed") if isinstance(result.get("parsed"), dict) else {}
    draft = str(parsed.get("draft") or "").strip()
    if not draft:
        template = empty_draft or _PROMISE_SPEC["empty_draft"]
        draft = template.format(label=tag_label(tag))
    if "(이유" not in draft.replace(" ", ""):
        draft = draft.rstrip(".。") + ". (이유: )"
    return {"draft": draft[:160], "result": result}


def build_actions(
    draft: str,
    *,
    section: str = "promise",
    field: str = PROMISE_FIELD,
) -> list[dict[str, Any]]:
    return [
        {
            "id": "add",
            "label": "추가",
            "type": "navigate",
            "resolve": True,
            "target": {
                "surface": "settings",
                "section": section,
                "field": field,
                "prefill": draft,
            },
        },
        {"id": "no", "label": "아니요", "type": "dismiss_forever", "target": {}},
        {"id": "later", "label": "나중에", "type": "snooze", "target": {"hours": 24}},
    ]


def propose_for_card(
    conn: sqlite3.Connection,
    project_id: int,
    tag: str,
    *,
    claude: Any = None,
) -> dict[str, Any]:
    """장르 카드 상태가 바뀐 뒤 호출한다. 조건이 되면 알림을 만든다."""
    project = conn.execute(
        "SELECT cluster_id, promise_broken, cliche_used FROM project WHERE id = ?",
        (int(project_id),),
    ).fetchone()
    spec = spec_for_cluster(project["cluster_id"] if project is not None else "")
    covered = str((project[spec["field"]] if project is not None else "") or "")
    stats = rejection_stats(conn, project_id, tag)
    if not should_propose(stats, promise_broken=covered):
        return {
            "skipped": True,
            "reason": "threshold_or_covered",
            "stats": {"count": stats["count"], "run_count": stats["run_count"]},
        }
    if claude is None:
        return {"skipped": True, "reason": "no_claude", "stats": stats}
    bundle = draft_promise_sentence(
        claude,
        tag=tag,
        cards=stats["cards"],
        prompt_file=spec["prompt_file"],
        empty_draft=spec["empty_draft"],
    )
    draft = str(bundle.get("draft") or "")
    label = tag_label(tag)
    created = create_or_refresh_intent_suggest(
        conn,
        project_id,
        tag=tag,
        stats=stats,
        draft=draft,
        dedupe=dedupe_key(project_id, tag, kind=spec["kind"]),
        title=f"최근 피드백에서 '{label}' 지적을 {stats['count']}번 적용하지 않으셨어요",
        body=spec["body"],
        actions=build_actions(draft, section=spec["section"], field=spec["field"]),
        extra_payload={"genre": True, "webnovel": spec["kind"] == "webnovel", "tag_label": label},
    )
    created["draft"] = draft
    created["stats"] = {"count": stats["count"], "run_count": stats["run_count"], "tag": tag}
    created["usage"] = (bundle.get("result") or {}).get("usage")
    return created


__all__ = [
    "KIND",
    "CLICHE_FIELD",
    "PROMISE_FIELD",
    "build_actions",
    "dedupe_key",
    "draft_promise_sentence",
    "list_rejected_cards",
    "promise_covers_tag",
    "propose_for_card",
    "rejection_stats",
    "should_propose",
    "spec_for_cluster",
    "tag_label",
]
