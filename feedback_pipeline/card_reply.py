"""첨삭 카드 한 장에 대한 짧은 의견 대화."""

from __future__ import annotations

import sqlite3
from typing import Any

import feedback_store
from feedback_pipeline import prompt_loader
from feedback_pipeline.claude_client import (
    ClaudeError,
    estimate_cost_usd,
    get_client,
)
from feedback_pipeline.config import FEEDBACK_MODEL
from feedback_pipeline.context import build_project_context
from feedback_pipeline.paragraphs import (
    context_numbers,
    format_para_block,
    to_paragraphs,
)

COMMENT_TURN_LIMIT = 20
COMMENT_HISTORY_LIMIT = 10
LIMIT_MESSAGE = (
    "이 카드는 대화를 너무 많이 나눴어요. 새로 분석해서 다시 확인해 보세요"
)
PROJECT_INFO_LIMIT = 1500
REPLY_MAX_TOKENS = 512


class CardCommentLimit(Exception):
    """카드당 대화 횟수 한도."""


def comment_limit_reached(count: int, limit: int = COMMENT_TURN_LIMIT) -> bool:
    return int(count or 0) >= int(limit)


def public_comment(row: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    return {
        "id": int(row["id"]),
        "card_id": int(row["card_id"]),
        "run_id": int(row["run_id"]),
        "project_id": int(row["project_id"]),
        "role": str(row.get("role") or ""),
        "body": str(row.get("body") or ""),
        "created_at": str(row.get("created_at") or ""),
    }


def format_history(
    comments: list[dict[str, Any]] | None,
    limit: int = COMMENT_HISTORY_LIMIT,
) -> str:
    rows = list(comments or [])[-max(1, int(limit or COMMENT_HISTORY_LIMIT)) :]
    if not rows:
        return "(아직 대화 없음)"
    lines: list[str] = []
    for row in rows:
        role = "사용자" if str(row.get("role") or "") == "user" else "토리"
        body = str(row.get("body") or "").strip()
        if body:
            lines.append(f"{role}: {body}")
    return "\n".join(lines) if lines else "(아직 대화 없음)"


def neighbor_paragraph_text(
    paragraphs: list | None,
    start_para: Any,
    end_para: Any,
) -> str:
    paras = to_paragraphs(paragraphs or [])
    total = paras[-1].number if paras else 0
    try:
        start = int(start_para or 1)
    except (TypeError, ValueError):
        start = 1
    try:
        end = int(end_para or start)
    except (TypeError, ValueError):
        end = start
    nums = context_numbers(start, end, total)
    return format_para_block(paras, nums)


def _truncate(text: str, limit: int) -> str:
    compact = str(text or "").strip()
    if len(compact) <= limit:
        return compact
    return compact[: max(1, limit - 1)].rstrip() + "…"


def fill_card_reply_prompt(
    *,
    card: dict[str, Any],
    paragraphs: list | None,
    project_info: str,
    history: list[dict[str, Any]] | None,
    user_message: str,
) -> str:
    suggestion = card.get("suggestion")
    if suggestion is None or not str(suggestion).strip():
        suggestion_text = "(없음)"
    else:
        suggestion_text = str(suggestion)
    values = {
        "title": str(card.get("title") or "").strip() or "(제목 없음)",
        "reason": str(card.get("reason") or "").strip() or "(없음)",
        "kind": str(card.get("kind") or ""),
        "style_type": str(card.get("style_type") or ""),
        "original_text": str(card.get("original_text") or "").strip() or "(없음)",
        "suggestion": suggestion_text,
        "neighbor_paragraphs": neighbor_paragraph_text(
            paragraphs, card.get("start_para"), card.get("end_para")
        ),
        "project_info": _truncate(str(project_info or "").strip(), PROJECT_INFO_LIMIT)
        or "(없음)",
        "history": format_history(history),
        "user_message": str(user_message or "").strip(),
    }
    return prompt_loader.fill_template(prompt_loader.card_reply_prompt(), values)


def _scene_paragraphs(
    conn: sqlite3.Connection, run_id: int, scene_id: int
) -> list:
    row = conn.execute(
        """
        SELECT paragraphs_json FROM feedback_run_scene
        WHERE run_id = ? AND scene_id = ?
        """,
        (int(run_id), int(scene_id)),
    ).fetchone()
    if row is None:
        return []
    return feedback_store._loads(row["paragraphs_json"], [])


def _load_comment(conn: sqlite3.Connection, comment_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT id, card_id, run_id, project_id, role, body, created_at
        FROM feedback_card_comment WHERE id = ?
        """,
        (int(comment_id),),
    ).fetchone()
    if row is None:
        raise LookupError("댓글을 찾을 수 없습니다.")
    return dict(row)


def reply_to_card_comment(
    conn: sqlite3.Connection,
    card_id: int,
    user_message: str,
    *,
    claude: Any | None = None,
) -> dict[str, Any]:
    """사용자 메시지를 저장하고 그 카드에만 짧게 답한다.

    호출 실패 시 사용자 메시지는 남기고 assistant는 남기지 않는다.
    """
    text = str(user_message or "").strip()
    if not text:
        raise ValueError("의견이 비어 있습니다.")
    card = feedback_store.get_card(conn, card_id)
    if card is None:
        raise LookupError("카드를 찾을 수 없습니다.")
    comments = feedback_store.list_card_comments(conn, int(card["id"]))
    if comment_limit_reached(len(comments)):
        raise CardCommentLimit(LIMIT_MESSAGE)
    last = comments[-1] if comments else None
    reuse_user = (
        last is not None
        and str(last.get("role") or "") == "user"
        and str(last.get("body") or "").strip() == text
    )
    if reuse_user:
        user_id = int(last["id"])
        history = comments[:-1]
    else:
        history = comments
        user_id = feedback_store.add_card_comment(conn, int(card["id"]), "user", text)
        conn.commit()
    project_info = build_project_context(conn, int(card["project_id"]))
    paragraphs = _scene_paragraphs(conn, int(card["run_id"]), int(card["scene_id"]))
    prompt = fill_card_reply_prompt(
        card=card,
        paragraphs=paragraphs,
        project_info=project_info,
        history=history,
        user_message=text,
    )
    client = claude if claude is not None else get_client()
    try:
        result = client.generate(
            prompt,
            model=FEEDBACK_MODEL,
            thinking="off",
            max_tokens=REPLY_MAX_TOKENS,
            timeout=60.0,
        )
    except ClaudeError:
        raise
    except Exception as error:  # noqa: BLE001
        raise ClaudeError(str(error)[:300] or "답을 받지 못했어요.", code="unknown") from error
    reply = ""
    usage: dict[str, Any] = {}
    if isinstance(result, dict):
        reply = str(result.get("text") or "").strip()
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    if not reply:
        raise ClaudeError("답을 받지 못했어요.", code="empty")
    assistant_id = feedback_store.add_card_comment(
        conn, int(card["id"]), "assistant", reply
    )
    inp = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    cost = estimate_cost_usd(FEEDBACK_MODEL, inp, out)
    return {
        "comments": [
            public_comment(_load_comment(conn, user_id)),
            public_comment(_load_comment(conn, assistant_id)),
        ],
        "usage": {
            "input_tokens": inp,
            "output_tokens": out,
            "cost_usd": round(cost, 6),
        },
    }
