"""일반문학 단편 교정 카드. 웹소설 카드 생성과는 별도다."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any

from feedback_pipeline import prompt_loader
from feedback_pipeline.claude_client import ClaudeError, is_sampling_locked_model
from feedback_pipeline.config import CARD_CONCURRENCY, LITERATURE_STAGE_MODELS, FEEDBACK_MODEL
from feedback_pipeline.literature_cache import (
    build_cached_system,
    cache_prefix,
    with_schema_instruction,
)
from feedback_pipeline.literature_text import format_numbered_manuscript, locate_quote, loose_norm, quote_in_text
from feedback_pipeline.runner import CostLimitExceeded, _add_usage, _check_cost

STRUCTURAL_KEYS = {"ending", "title", "economy", "scene_summary", "implication", "motif", "novelty", "opening"}
SUGGEST_TAGS = {"ambiguity", "pov", "style", "grammar", "excess"}
NOTE_TAGS = {"repeat", "translationese", "cliche", "rhythm", "image"}
HIGH_TAGS = {"ambiguity", "pov", "style", "grammar"}
TAG_LABELS = {
    "ambiguity": "의미 모호",
    "pov": "시점 이탈",
    "style": "문체 이탈",
    "grammar": "비문",
    "excess": "과잉 설명",
    "repeat": "반복 표현",
    "translationese": "번역투",
    "cliche": "상투적 표현",
    "rhythm": "리듬",
    "image": "감각·이미지",
    "settings_conflict": "설정 모순",
}
LOCAL_CHANGE_RATIO = 0.55
LENGTH_DROP_RATIO = 0.55
DETECT_PAGE_CHARS = 200
DETECT_BUNDLE_PAGES = 20
DETECT_BUNDLE_CHARS = DETECT_PAGE_CHARS * DETECT_BUNDLE_PAGES
DETECT_SELECT_TEMPERATURE = 0.0
_GENERIC_PROBLEMS = ("가독성이 떨어", "읽기 어렵", "문장이 어색", "자연스럽지 않")
_FIRST_PERSON = ("나는", "내가", "나의", "내게", "나도")
_THIRD_PERSON = ("그는", "그가", "그의", "그녀")
_PARTICLE_SUFFIXES = ("에서는", "에게는", "으로는", "에서", "에게", "으로", "처럼", "하며", "하고", "이다", "였다", "했다", "하는", "되는", "은", "는", "이", "가", "을", "를", "의", "와", "과", "도", "만", "로")
REWRITE_MARK = "〔수정안〕"
_ENDINGS = ("습니다", "니까", "까요", "데요", "네요", "어요", "아요", "예요", "이에요", "했다", "였다", "있다", "없다", "한다", "된다", "는다", "다")


def para_content_hash(text: str) -> str:
    """문단 내용 비교용. 공백·문장부호를 뺀 뒤 짧게 해시한다."""
    return hashlib.sha256(loose_norm(text).encode("utf-8")).hexdigest()[:16]


def card_type_key(tags: list[str] | object) -> str:
    if not isinstance(tags, list):
        try:
            tags = json.loads(str(tags or "[]"))
        except json.JSONDecodeError:
            tags = []
    return ",".join(sorted(str(tag or "").strip() for tag in (tags or []) if str(tag or "").strip()))


def card_match_key(quote: str, tags: list[str] | object) -> str:
    return para_content_hash(str(quote or "")) + "|" + card_type_key(tags)


def _parse_tags(raw: object) -> list[str]:
    if isinstance(raw, list):
        values = raw
    else:
        try:
            values = json.loads(str(raw or "[]"))
        except json.JSONDecodeError:
            values = []
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for item in values:
        tag = str(item or "").strip()
        if tag and tag not in out:
            out.append(tag)
    return out


def find_previous_literature_run(
    conn: sqlite3.Connection,
    project_id: int,
    *,
    pipeline: str,
    scene_ids: list[int] | None = None,
    exclude_run_id: int = 0,
) -> int | None:
    """같은 작품(단편) 또는 같은 단위 장면(장편)의 직전 성공 실행."""
    rows = conn.execute(
        """
        SELECT id FROM feedback_run
        WHERE project_id = ? AND pipeline = ? AND status IN ('ok', 'partial') AND id != ?
        ORDER BY id DESC
        """,
        (int(project_id), str(pipeline or ""), int(exclude_run_id or 0)),
    ).fetchall()
    wanted = {int(sid) for sid in (scene_ids or []) if int(sid or 0)}
    for row in rows:
        run_id = int(row[0])
        if not wanted:
            return run_id
        have = {
            int(item[0])
            for item in conn.execute(
                "SELECT scene_id FROM feedback_run_scene WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        }
        if have == wanted:
            return run_id
    return None


def load_previous_run_cards(conn: sqlite3.Connection, run_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, scene_id, status, start_para, end_para, start_quote, original_text, reason,
               suggestion, card_form, issue_tags_json, source_key, priority, intentional
        FROM feedback_card WHERE run_id = ? ORDER BY ord, id
        """,
        (int(run_id),),
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["tags"] = _parse_tags(item.pop("issue_tags_json", None))
        out.append(item)
    return out


def _global_para_for(assembled: dict[str, Any], scene_id: int, local: int) -> int | None:
    for row in assembled.get("paragraphs") or []:
        if int(row.get("scene_id") or 0) == int(scene_id) and int(row.get("local") or 0) == int(local):
            return int(row.get("n") or 0)
    return None


def _current_para_row(assembled: dict[str, Any], scene_id: int, local: int) -> dict[str, Any] | None:
    for row in assembled.get("paragraphs") or []:
        if int(row.get("scene_id") or 0) == int(scene_id) and int(row.get("local") or 0) == int(local):
            return row
    return None


def classify_previous_cards(
    assembled: dict[str, Any],
    previous_cards: list[dict[str, Any]],
) -> dict[str, Any]:
    """문단 해시로 open 이어받기 / ignored·applied 재생성 금지를 나눈다."""
    carry_open: list[dict[str, Any]] = []
    skip_keys: set[str] = set()
    log: list[dict[str, str]] = []
    for raw in previous_cards or []:
        status = str(raw.get("status") or "open").strip()
        scene_id = int(raw.get("scene_id") or 0)
        local = int(raw.get("start_para") or 0)
        prev_text = str(raw.get("original_text") or "")
        if not scene_id or not local or not prev_text.strip():
            continue
        current = _current_para_row(assembled, scene_id, local)
        if current is None:
            continue
        if para_content_hash(prev_text) != para_content_hash(str(current.get("text") or "")):
            continue
        tags = list(raw.get("tags") or [])
        quote = str(raw.get("start_quote") or prev_text)[:200]
        key = card_match_key(prev_text, tags)
        quote_key = card_match_key(quote, tags)
        if status in {"ignored", "applied", "applied_edited"}:
            skip_keys.add(key)
            skip_keys.add(quote_key)
            log.append(
                {
                    "action": "carry_skip",
                    "target": card_type_key(tags) or "card",
                    "detail": f"이전 {status} 유지 (문단 동일)",
                }
            )
            continue
        if status != "open":
            continue
        global_para = _global_para_for(assembled, scene_id, local)
        form = "suggest" if str(raw.get("card_form") or "") == "suggest" and raw.get("suggestion") else "note"
        carry_open.append(
            {
                "quote": quote or prev_text[:120],
                "reason": str(raw.get("reason") or ""),
                "tags": tags,
                "form": form,
                "suggestion": str(raw.get("suggestion") or "") if form == "suggest" else "",
                "priority": str(raw.get("priority") or "low"),
                "source_key": str(raw.get("source_key") or ""),
                "intentional": bool(raw.get("intentional")),
                "scene_id": scene_id,
                "start_para": local,
                "end_para": int(raw.get("end_para") or local),
                "start_quote": quote[:80],
                "end_quote": quote[:80],
                "original_text": str(current.get("text") or prev_text),
                "global_para": int(global_para or 0),
                "reader_problem": "",
                "carried": True,
                "carried_from_status": "open",
            }
        )
        log.append(
            {
                "action": "carry_open",
                "target": card_type_key(tags) or "card",
                "detail": f"이전 open 이어받기 P{global_para or local}",
            }
        )
    return {"carry_open": carry_open, "skip_keys": skip_keys, "log": log}


def apply_card_carry(
    merged: list[dict[str, Any]],
    screened: list[dict[str, Any]],
    *,
    carry_open: list[dict[str, Any]],
    skip_keys: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]]:
    """ignored/applied는 빼고, open 이어받기를 선별 후보에 넣는다."""
    log: list[dict[str, str]] = []
    filtered_merged: list[dict[str, Any]] = []
    for card in merged:
        key = card_match_key(str(card.get("original_text") or card.get("quote") or ""), card.get("tags") or [])
        quote_key = card_match_key(str(card.get("quote") or ""), card.get("tags") or [])
        if key in skip_keys or quote_key in skip_keys:
            log.append(
                {
                    "action": "drop_card",
                    "target": ",".join(card.get("tags") or []),
                    "detail": "이전 ignored/applied 유지",
                }
            )
            continue
        filtered_merged.append(card)
    filtered_screened: list[dict[str, Any]] = []
    for card in screened:
        key = card_match_key(str(card.get("original_text") or card.get("quote") or ""), card.get("tags") or [])
        quote_key = card_match_key(str(card.get("quote") or ""), card.get("tags") or [])
        if key in skip_keys or quote_key in skip_keys:
            continue
        filtered_screened.append(card)
    for card in carry_open:
        if any(_quotes_overlap(card, existing) for existing in filtered_screened):
            continue
        key = card_match_key(str(card.get("original_text") or card.get("quote") or ""), card.get("tags") or [])
        if key in skip_keys:
            continue
        filtered_screened.append(card)
        log.append(
            {
                "action": "carry_into_select",
                "target": ",".join(card.get("tags") or []),
                "detail": "선별 후보에 이전 open 추가",
            }
        )
    return filtered_merged, filtered_screened, log


def _model(options: dict[str, Any], stage: str) -> str:
    custom = options.get("stage_models") if isinstance(options.get("stage_models"), dict) else {}
    return str(custom.get(stage) or LITERATURE_STAGE_MODELS.get(stage) or FEEDBACK_MODEL)


def _choice_hits(text: str, choice: str) -> bool:
    source = str(choice or "")
    if len(loose_norm(source)) < 4:
        return False
    blob = loose_norm(text)
    for raw in re.split(r"[,，.。\n]", source):
        phrase = loose_norm(raw)
        if len(phrase) >= 4 and phrase in blob:
            return True
    return False


def _habit_hits(tag: str, habit: str) -> bool:
    label = TAG_LABELS.get(tag, "")
    blob = str(habit or "")
    return bool(label and label in blob) or tag in blob


def _ending(text: str) -> str:
    compact = re.sub(r"\s+", "", str(text or ""))
    compact = re.sub(r"[.!?…\"'”’」』]+$", "", compact)
    for ending in _ENDINGS:
        if compact.endswith(ending):
            return ending
    return compact[-1:] if compact else ""


def _change_ratio(original: str, suggestion: str) -> float:
    left = loose_norm(original)
    right = loose_norm(suggestion)
    if not left:
        return 1.0
    shared = 0
    pool = list(right)
    for char in left:
        if char in pool:
            pool.remove(char)
            shared += 1
    return 1.0 - (shared / len(left))


def seed_cards(
    diagnoses: list[dict[str, Any]],
    tasks: list[str],
    *,
    style_choice: str,
    style_habit: str,
) -> list[dict[str, Any]]:
    """문장으로 좁혀지는 진단만 카드 씨앗으로 만든다."""
    cards: list[dict[str, Any]] = []
    task_blob = "\n".join(str(task) for task in tasks or [])
    for item in diagnoses or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        if key in STRUCTURAL_KEYS or str(item.get("verdict") or "") == "works":
            continue
        for evidence in item.get("evidence") or []:
            if not isinstance(evidence, dict):
                continue
            quote = str(evidence.get("quote") or "").strip()
            if len(quote) < 4:
                continue
            tag = "pov" if key == "pov" else "style" if key == "style" else "ambiguity"
            form = "suggest"
            priority = "high" if tag in HIGH_TAGS or quote in task_blob or key in task_blob else "medium"
            if _habit_hits(tag, style_habit):
                form = "suggest"
                priority = "medium" if priority == "low" else priority
            reason = str(item.get("note") or "")
            if _choice_hits(quote + reason, style_choice):
                continue
            cards.append(
                {
                    "form": form,
                    "tags": [tag],
                    "priority": priority,
                    "quote": quote,
                    "reason": reason,
                    "reader_problem": reason,
                    "readings": [],
                    "suggestion": "",
                    "intentional": bool(item.get("intentional")),
                    "source_key": key,
                    "scene_id": evidence.get("scene_id"),
                    "para": evidence.get("local") or evidence.get("para"),
                    "global_para": evidence.get("para"),
                }
            )
    return cards


def _normalize_model_card(raw: dict[str, Any], *, style_choice: str, style_habit: str) -> dict[str, Any] | None:
    tag = str(raw.get("tag") or "").strip()
    if tag not in SUGGEST_TAGS | NOTE_TAGS:
        return None
    quote = str(raw.get("quote") or "").strip()
    reason = str(raw.get("reason") or "").strip()
    if len(quote) < 4 or not reason:
        return None
    if _choice_hits(quote + reason, style_choice):
        return None
    form = "suggest" if tag in SUGGEST_TAGS else "note"
    if _habit_hits(tag, style_habit) and tag in NOTE_TAGS:
        form = "suggest"
    readings = [str(item).strip() for item in (raw.get("readings") or []) if str(item).strip()]
    return {
        "form": form,
        "tags": [tag],
        "priority": "low",
        "quote": quote,
        "reason": reason,
        "reader_problem": str(raw.get("reader_problem") or "").strip(),
        "readings": readings,
        "suggestion": "",
        "intentional": bool(raw.get("intentional")),
        "source_key": str(raw.get("source_key") or ""),
    }


def screen_detected_cards(cards: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """독자 문제가 구체적이지 않거나, 의미 모호에 두 해석이 없으면 뺀다."""
    log: list[dict[str, str]] = []
    kept: list[dict[str, Any]] = []
    for card in cards:
        problem = str(card.get("reader_problem") or "").strip()
        tags = card.get("tags") or []
        if len(problem) < 8 or any(phrase in problem for phrase in _GENERIC_PROBLEMS):
            log.append({"action": "drop_card", "target": ",".join(tags), "detail": "독자 문제가 일반론이거나 비어 있음"})
            continue
        if "ambiguity" in tags:
            readings = [item for item in (card.get("readings") or []) if len(item) >= 4]
            if len(set(readings)) < 2:
                log.append({"action": "drop_card", "target": "ambiguity", "detail": "가능한 두 해석이 없음"})
                continue
        kept.append(card)
    return kept, log


def assign_priority(card: dict[str, Any], tasks: list[str], habit: str) -> None:
    """중요도는 유형과 퇴고 과제로 정한다. 모델 점수는 쓰지 않는다."""
    tags = set(card.get("tags") or [])
    blob = "\n".join(str(task) for task in tasks or [])
    quote = str(card.get("quote") or "")
    source = str(card.get("source_key") or "")
    from_task = (quote and quote in blob) or (source and source in blob)
    if tags & {"pov", "style", "grammar", "ambiguity"} or from_task:
        card["priority"] = "high"
    elif "excess" in tags or any(_habit_hits(tag, habit) for tag in tags):
        card["priority"] = "medium"
    else:
        card["priority"] = "low"


def _letters(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", str(text or ""))


def _content_tokens(text: str) -> set[str]:
    found = set()
    for token in re.findall(r"[가-힣]{2,}", str(text or "")):
        for suffix in _PARTICLE_SUFFIXES:
            if token.endswith(suffix) and len(token) - len(suffix) >= 2:
                token = token[: -len(suffix)]
                break
        if token in {"나는", "내가", "그는", "그녀가", "그녀", "아내", "이것", "그것", "무엇"}:
            continue
        if len(token) >= 2:
            found.add(token)
    return found


def _allowed_edit_problem(
    original: str,
    suggestion: str,
    tags: set[str],
    *,
    style_narration: str,
    style_dialogue: str,
    manuscript: str,
) -> str:
    if any(word in original for word in _FIRST_PERSON) and not any(word in suggestion for word in _FIRST_PERSON):
        if any(word in suggestion for word in _THIRD_PERSON):
            return "1인칭이 3인칭으로 바뀜"
    narration = str(style_narration or "")
    if "1인칭" in narration and "3인칭" not in narration:
        if any(word in suggestion for word in _THIRD_PERSON) and not any(word in original for word in _THIRD_PERSON):
            return "문체 1번 인칭과 다름"
    if "3인칭" in narration and "1인칭" not in narration:
        if any(word in suggestion for word in _FIRST_PERSON) and not any(word in original for word in _FIRST_PERSON):
            return "문체 1번 인칭과 다름"
    dialogue = str(style_dialogue or "")
    if "따옴표" in dialogue or "“" in dialogue or '"' in dialogue:
        if any(mark in original for mark in "\"'“”‘’「」") and not any(mark in suggestion for mark in "\"'“”‘’「」"):
            return "대화 따옴표를 없앰"
    if _letters(original) == _letters(suggestion) and not ({"grammar", "ambiguity"} & tags):
        return "구두점만 바뀜"
    novel = _content_tokens(suggestion) - _content_tokens(original)
    if "ambiguity" in tags:
        novel = {token for token in novel if token not in manuscript}
    if novel and "excess" not in tags:
        return "원문에 없던 내용어: " + ", ".join(sorted(novel)[:3])
    return ""


def demote_cards(
    cards: list[dict[str, Any]],
    *,
    style_narration: str,
    style_choice: str,
    terms: list[str],
    meaning_ok,
    style_dialogue: str = "",
    manuscript: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """수정안을 규칙으로 검사하고, 실패하면 지적형으로 남긴다."""
    log: list[dict[str, str]] = []
    kept: list[dict[str, Any]] = []
    for card in cards:
        if _choice_hits(card.get("quote", "") + card.get("reason", ""), style_choice):
            log.append({"action": "drop_card", "target": ",".join(card.get("tags") or []), "detail": "의도적 선택"})
            continue
        if card.get("form") != "suggest":
            kept.append(card)
            continue
        suggestion = str(card.get("suggestion") or "").strip()
        original = str(card.get("quote") or "")
        tags = set(card.get("tags") or [])
        reason = ""
        if not suggestion:
            reason = "수정안이 비어 있음"
        else:
            reason = _allowed_edit_problem(
                original,
                suggestion,
                tags,
                style_narration=style_narration,
                style_dialogue=style_dialogue,
                manuscript=manuscript,
            )
        if not reason and suggestion and _ending(original) and _ending(suggestion) and _ending(original) != _ending(suggestion):
            if "문체" not in str(style_narration or "") or _ending(suggestion) not in str(style_narration):
                reason = "종결이 원문과 다름"
        if not reason and suggestion and "excess" not in tags and len(loose_norm(suggestion)) < len(loose_norm(original)) * LENGTH_DROP_RATIO:
            reason = "길이가 크게 줄음"
        elif not reason and suggestion and "excess" not in tags and "grammar" not in tags and _change_ratio(original, suggestion) > LOCAL_CHANGE_RATIO:
            reason = "바뀐 비율이 큼"
        elif not reason and suggestion:
            missing = [term for term in terms if term and term in original and term not in suggestion]
            if missing:
                reason = "용어가 빠짐: " + ", ".join(missing[:3])
            elif meaning_ok is not None and not meaning_ok(original, suggestion):
                reason = "의미가 달라짐"
        if reason:
            log.append({"action": "demote_card", "target": ",".join(tags), "detail": reason})
            card = {**card, "form": "note", "suggestion": ""}
        kept.append(card)
    return kept, log


def _quotes_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    a = loose_norm(str(left.get("quote") or ""))
    b = loose_norm(str(right.get("quote") or ""))
    if len(a) < 4 or len(b) < 4:
        return False
    if a == b or a in b or b in a:
        return True
    para_a = left.get("global_para")
    para_b = right.get("global_para")
    if not para_a or not para_b or int(para_a) != int(para_b):
        return False
    original = str(left.get("original_text") or right.get("original_text") or "")
    norm = loose_norm(original)
    start_a = norm.find(a)
    start_b = norm.find(b)
    if start_a < 0 or start_b < 0:
        return False
    end_a = start_a + len(a)
    end_b = start_b + len(b)
    return start_a < end_b and start_b < end_a


def _tagged_reason(card: dict[str, Any]) -> str:
    labels = [TAG_LABELS.get(tag, tag) for tag in (card.get("tags") or [])]
    prefix = " · ".join(labels)
    reason = str(card.get("reason") or "").strip()
    if prefix and not reason.startswith(prefix):
        return f"{prefix}: {reason}" if reason else prefix
    return reason


def merge_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 문장이나 겹치는 범위는 수정안 작성 전에 하나로 합친다."""
    merged: list[dict[str, Any]] = []
    rank = {"high": 0, "medium": 1, "low": 2}
    for card in cards:
        if len(loose_norm(str(card.get("quote") or ""))) < 4:
            continue
        current = next((item for item in merged if _quotes_overlap(item, card)), None)
        if current is None:
            item = {**card, "tags": list(card.get("tags") or []), "reason": _tagged_reason(card)}
            merged.append(item)
            continue
        for tag in card.get("tags") or []:
            if tag not in current["tags"]:
                current["tags"].append(tag)
        if rank.get(str(card.get("priority")), 9) < rank.get(str(current.get("priority")), 9):
            current["priority"] = card["priority"]
        if card.get("form") == "suggest":
            current["form"] = "suggest"
        if card.get("source_key") and not current.get("source_key"):
            current["source_key"] = card["source_key"]
        current["intentional"] = bool(current.get("intentional") or card.get("intentional"))
        extra = _tagged_reason(card)
        if extra and extra not in str(current.get("reason") or ""):
            current["reason"] = (str(current.get("reason") or "") + "\n" + extra).strip()
        if len(str(card.get("quote") or "")) > len(str(current.get("quote") or "")):
            current["quote"] = card["quote"]
    return merged


def _para_bounds(raw: str) -> tuple[int, int] | None:
    numbers = [int(item) for item in re.findall(r"\d+", str(raw or ""))]
    if not numbers:
        return None
    if len(numbers) == 1:
        return numbers[0], numbers[0]
    return numbers[0], numbers[1]


def _scene_blocks(assembled: dict[str, Any], scene_map: dict[str, Any]) -> list[dict[str, Any]]:
    """장면 지도의 장면마다 원문을 자른다. 지도가 없으면 회차 단위로 나눈다."""
    paragraphs = list(assembled.get("paragraphs") or [])
    mapped = [item for item in (scene_map.get("scenes") or []) if isinstance(item, dict)]
    blocks = []
    if mapped:
        summaries = [str(item.get("summary") or "") for item in mapped]
        for index, scene in enumerate(mapped):
            bounds = _para_bounds(str(scene.get("para_range") or ""))
            if bounds is None:
                continue
            rows = [row for row in paragraphs if bounds[0] <= int(row.get("n") or 0) <= bounds[1]]
            if not rows:
                continue
            blocks.append(
                {
                    "scene_id": int(rows[0]["scene_id"]),
                    "index": index,
                    "text": "\n".join(f"[P{row['n']}] {row['text']}" for row in rows),
                    "before": summaries[index - 1] if index else "(없음)",
                    "after": summaries[index + 1] if index + 1 < len(summaries) else "(없음)",
                }
            )
        if blocks:
            return blocks
    by_scene: dict[int, list[dict[str, Any]]] = {}
    for row in paragraphs:
        by_scene.setdefault(int(row["scene_id"]), []).append(row)
    for scene_id, rows in by_scene.items():
        blocks.append(
            {
                "scene_id": scene_id,
                "text": "\n".join(f"[P{row['n']}] {row['text']}" for row in rows),
                "before": "(없음)",
                "after": "(없음)",
            }
        )
    return blocks


def _detection_bundles(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """장면 경계에서 끊고, 한 묶음이 원고지 약 20매를 넘지 않게 한다."""
    bundles: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    size = 0
    for block in blocks:
        text = str(block.get("text") or "")
        weight = len(text.replace("\n", ""))
        if current and size + weight > DETECT_BUNDLE_CHARS:
            bundles.append(_bundle_of(current, len(bundles)))
            current = []
            size = 0
        current.append(block)
        size += weight
    if current:
        bundles.append(_bundle_of(current, len(bundles)))
    return bundles


def _bundle_of(blocks: list[dict[str, Any]], index: int) -> dict[str, Any]:
    return {
        "scene_id": blocks[0]["scene_id"],
        "index": index,
        "text": "\n\n".join(str(block.get("text") or "") for block in blocks),
        "before": blocks[0].get("before") or "(없음)",
        "after": blocks[-1].get("after") or "(없음)",
        "summary": " / ".join(str(block.get("before") or "") for block in blocks if block.get("before")),
    }


def _attach(assembled: dict[str, Any], card: dict[str, Any]) -> dict[str, Any] | None:
    located = locate_quote(assembled, str(card.get("quote") or ""), card.get("global_para"))
    if located is None or located.get("matched") != "quote":
        return None
    rows = assembled.get("paragraphs") or []
    paragraph = next((row for row in rows if int(row.get("n") or 0) == int(located["para"])), None)
    original = str(paragraph.get("text") if paragraph else card.get("quote") or "")
    return {
        **card,
        "scene_id": int(located["scene_id"]),
        "start_para": int(located["local"]),
        "end_para": int(located["local"]),
        "start_quote": str(card.get("quote") or "")[:80],
        "end_quote": str(card.get("quote") or "")[:80],
        "original_text": original,
        "global_para": int(located["para"]),
    }


def _neighbor_sentences(assembled: dict[str, Any], card: dict[str, Any]) -> tuple[str, str]:
    rows = list(assembled.get("paragraphs") or [])
    para_no = int(card.get("global_para") or 0)
    index = next((i for i, row in enumerate(rows) if int(row.get("n") or 0) == para_no), -1)
    if index < 0:
        return "(없음)", "(없음)"
    parts = [part.strip() for part in re.split(r"(?<=[.!?…])\s+", str(rows[index].get("text") or "")) if part.strip()]
    quote = loose_norm(str(card.get("quote") or ""))
    at = next((i for i, part in enumerate(parts) if quote and loose_norm(part).find(quote) >= 0), 0)
    before = parts[at - 1] if at > 0 else (str(rows[index - 1].get("text") or "") if index > 0 else "(없음)")
    after = parts[at + 1] if at + 1 < len(parts) else (str(rows[index + 1].get("text") or "") if index + 1 < len(rows) else "(없음)")
    return before or "(없음)", after or "(없음)"


def _excess_only(tags: list[str]) -> bool:
    return bool(tags) and set(tags) <= {"excess"}


def _deleted_remainder(original: str, span: str) -> str:
    if not span or span not in original:
        return ""
    return re.sub(r"\s{2,}", " ", original.replace(span, "", 1)).strip()


def apply_suggestion_fixes(
    cards: list[dict[str, Any]],
    fixes: list[dict[str, Any]],
    *,
    attempt: int,
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[dict[str, Any]]]:
    """2차 결과를 카드에 붙인다. 빈 값과 cannot_fix는 재시도 또는 강등이다."""
    by_id = {str(item.get("id") or ""): item for item in fixes if isinstance(item, dict)}
    log: list[dict[str, str]] = []
    retry: list[dict[str, Any]] = []
    for card in cards:
        fix = by_id.get(str(card.get("fix_id") or ""))
        tags = list(card.get("tags") or [])
        if fix is None:
            retry.append(card)
            continue
        if bool(fix.get("cannot_fix")):
            reason = str(fix.get("cannot_fix_reason") or "").strip() or "고칠 수 없음"
            log.append({"action": "demote_card", "target": ",".join(tags), "detail": "cannot_fix: " + reason})
            card["form"] = "note"
            card["suggestion"] = ""
            continue
        suggestion = str(fix.get("suggestion") or "").strip()
        if _excess_only(tags):
            suggestion = _deleted_remainder(str(card.get("quote") or ""), str(fix.get("delete_span") or "").strip())
        if not suggestion:
            retry.append(card)
            continue
        card["suggestion"] = suggestion
        card["form"] = "suggest"
    if retry and attempt == 1:
        log.append({"action": "retry_suggestion", "target": str(len(retry)), "detail": "수정안이 비어 한 번 더 요청"})
    elif retry:
        for card in retry:
            log.append({"action": "demote_card", "target": ",".join(card.get("tags") or []), "detail": "수정안이 비어 있음"})
            card["form"] = "note"
            card["suggestion"] = ""
        retry = []
    return cards, log, retry


def generate_literature_cards(
    conn,
    *,
    project: dict[str, Any],
    assembled: dict[str, Any],
    report: dict[str, Any],
    signals_summary: str,
    options: dict[str, Any],
    claude,
    params: dict[str, Any],
    max_cost: float,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """장면별 정밀 읽기와 리포트 씨앗으로 카드를 만든다."""
    choice = str(project.get("style_choice") or "")
    habit = str(project.get("style_habit") or "")
    narration = str(project.get("style_narration") or "")
    terms = [
        str(row["term"])
        for row in conn.execute(
            "SELECT term FROM custom_dictionary_terms WHERE project_id = ? ORDER BY id",
            (int(project.get("id") or 0),),
        ).fetchall()
    ]
    seeds = seed_cards(
        report.get("diagnoses") or [],
        report.get("tasks") or [],
        style_choice=choice,
        style_habit=habit,
    )
    settings = "\n".join(
        f"{label}: {str(project.get(key) or '').strip() or '(비어 있음)'}"
        for key, label in (
            ("style_narration", "서술"),
            ("style_sentence", "문장"),
            ("style_dialogue", "대화"),
            ("style_lexicon", "어휘"),
            ("style_choice", "의도적 선택"),
            ("style_habit", "고치고 싶은 습관"),
            ("intent_md", "기획의도"),
        )
    )
    if signals_summary:
        settings += "\n규칙 신호:\n" + signals_summary
    if terms:
        settings += "\n토리 사전: " + ", ".join(terms)
    # 캐시 앞부분에 문체·기획의도가 이미 있으므로, 단계 지시에는 신호·사전만 남긴다.
    extras = []
    if signals_summary:
        extras.append("규칙 신호:\n" + signals_summary)
    if terms:
        extras.append("토리 사전: " + ", ".join(terms))
    stage_settings = "\n".join(extras) if extras else "(추가 신호 없음)"
    model = _model(options, "cards")
    schema = prompt_loader.load_json("literature/card_schema.json")
    common_system = prompt_loader.load_text("literature/system.txt").rstrip()
    template = prompt_loader.load_text("literature/card_prompt.txt")
    drafts = list(seeds)
    blocks = _detection_bundles(_scene_blocks(assembled, report.get("scene_map") or {}))
    prompts = []
    for block in blocks:
        prompts.append(
            (
                block,
                prompt_loader.fill_template(
                    template,
                    {
                        "settings": stage_settings,
                        "before": block["before"],
                        "after": block["after"],
                        "scene": block["text"],
                    },
                ),
            )
        )

    prefix = str(options.get("cache_prefix") or "").strip()
    if not prefix:
        prefix = cache_prefix(format_numbered_manuscript(assembled), settings)
    cached_system = build_cached_system(prefix, common_system)

    def _one(
        prompt: str,
        *,
        call_model: str | None = None,
        call_schema: dict | None = None,
        max_tokens: int = 2500,
        timeout: float = 180.0,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        chosen = call_model or model
        body_schema = call_schema if call_schema is not None else schema
        kwargs: dict[str, Any] = {
            "model": chosen,
            "system": cached_system,
            "thinking": "off",
            "max_tokens": max_tokens,
            "timeout": timeout,
        }
        if temperature is not None and not is_sampling_locked_model(chosen):
            kwargs["temperature"] = float(temperature)
        return claude.generate(with_schema_instruction(prompt, body_schema), **kwargs)

    def _ingest_detect(block: dict[str, Any], result: dict[str, Any]) -> None:
        _add_usage(params, f"card_bundle:{block.get('index', 0)}", result, model)
        _check_cost(params, max_cost)
        parsed = result.get("parsed") if isinstance(result.get("parsed"), dict) else {}
        for raw in parsed.get("cards") or []:
            if not isinstance(raw, dict):
                continue
            card = _normalize_model_card(raw, style_choice=choice, style_habit=habit)
            if card is not None:
                card["scene_id"] = block["scene_id"]
                drafts.append(card)

    if prompts:
        # 첫 묶음으로 캐시를 예열한 뒤 나머지를 병렬로 보낸다.
        first_block, first_prompt = prompts[0]
        try:
            warm = _one(first_prompt, temperature=DETECT_SELECT_TEMPERATURE)
        except (ClaudeError, CostLimitExceeded):
            raise
        except Exception as error:  # noqa: BLE001
            warm = {"parsed": {"cards": []}, "usage": {"input_tokens": 0, "output_tokens": 0}, "error": str(error)}
        _ingest_detect(first_block, warm)
        rest = prompts[1:]
        if rest:
            workers = min(CARD_CONCURRENCY, len(rest))
            with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
                futures = {
                    pool.submit(_one, prompt, temperature=DETECT_SELECT_TEMPERATURE): block
                    for block, prompt in rest
                }
                pending = set(futures)
                while pending:
                    done, pending = wait(pending, return_when=FIRST_COMPLETED)
                    for future in done:
                        block = futures[future]
                        try:
                            result = future.result()
                        except (ClaudeError, CostLimitExceeded):
                            raise
                        except Exception as error:  # noqa: BLE001
                            result = {
                                "parsed": {"cards": []},
                                "usage": {"input_tokens": 0, "output_tokens": 0},
                                "error": str(error),
                            }
                        _ingest_detect(block, result)
    attached = []
    for card in drafts:
        located = _attach(assembled, card)
        if located is not None:
            attached.append(located)
    merged = merge_cards(attached)
    for card in merged:
        assign_priority(card, list(report.get("tasks") or []), habit)
    screened, screen_log = screen_detected_cards(merged)
    detect_log = [{"action": "detect_count", "target": str(len(merged)), "detail": "탐지 후보"}] + screen_log
    if is_sampling_locked_model(model):
        detect_log.append(
            {
                "action": "sampling",
                "target": str(model),
                "detail": "탐지·선별 temperature 미지원(모델 고정 샘플링)",
            }
        )
    else:
        detect_log.append(
            {
                "action": "sampling",
                "target": str(DETECT_SELECT_TEMPERATURE),
                "detail": "탐지·선별 temperature 고정",
            }
        )

    # 이전 실행 카드 이어받기 (바뀌지 않은 문단)
    pipeline = str(report.get("pipeline") or options.get("pipeline") or "literature_short")
    exclude_run_id = int(options.get("run_id") or options.get("exclude_run_id") or 0)
    scene_ids = [int(item.get("scene_id") or 0) for item in (assembled.get("scenes") or [])]
    scene_ids = [sid for sid in scene_ids if sid]
    prev_run_id = find_previous_literature_run(
        conn,
        int(project.get("id") or 0),
        pipeline=pipeline,
        scene_ids=scene_ids if pipeline == "literature_long" else None,
        exclude_run_id=exclude_run_id,
    )
    carry_open: list[dict[str, Any]] = []
    skip_keys: set[str] = set()
    # ignored/applied 재생성 금지는 이전 성공 실행 전체에서 모은다.
    prior_ids = [
        int(row[0])
        for row in conn.execute(
            """
            SELECT id FROM feedback_run
            WHERE project_id = ? AND pipeline = ? AND status IN ('ok', 'partial') AND id != ?
            ORDER BY id DESC
            """,
            (int(project.get("id") or 0), pipeline, exclude_run_id),
        ).fetchall()
    ]
    if pipeline == "literature_long" and scene_ids:
        filtered = []
        wanted = set(scene_ids)
        for rid in prior_ids:
            have = {
                int(item[0])
                for item in conn.execute(
                    "SELECT scene_id FROM feedback_run_scene WHERE run_id = ?",
                    (rid,),
                ).fetchall()
            }
            if have == wanted:
                filtered.append(rid)
        prior_ids = filtered
    for rid in prior_ids:
        classified = classify_previous_cards(assembled, load_previous_run_cards(conn, rid))
        skip_keys |= set(classified.get("skip_keys") or set())
        detect_log.extend(
            item
            for item in (classified.get("log") or [])
            if item.get("action") == "carry_skip"
        )
    # open 이어받기는 직전 성공 실행만
    if prev_run_id:
        classified = classify_previous_cards(assembled, load_previous_run_cards(conn, prev_run_id))
        carry_open = list(classified.get("carry_open") or [])
        for card in carry_open:
            assign_priority(card, list(report.get("tasks") or []), habit)
        detect_log.extend(
            item
            for item in (classified.get("log") or [])
            if item.get("action") == "carry_open"
        )
        detect_log.append(
            {
                "action": "carry_from_run",
                "target": str(prev_run_id),
                "detail": f"open {len(carry_open)} / skip {len(skip_keys)}",
            }
        )
    merged, screened, carry_log = apply_card_carry(
        merged, screened, carry_open=carry_open, skip_keys=skip_keys
    )
    detect_log.extend(carry_log)
    detect_log[0] = {"action": "detect_count", "target": str(len(merged)), "detail": "탐지 후보(이어받기 반영)"}

    select_schema = prompt_loader.load_json("literature/select_schema.json")
    select_template = prompt_loader.load_text("literature/select_prompt.txt")
    for index, card in enumerate(screened):
        card["fix_id"] = str(index)
    if screened:
        candidate_lines = []
        for card in screened:
            labels = ", ".join(TAG_LABELS.get(tag, tag) for tag in (card.get("tags") or []))
            prefix = "이어받기 " if card.get("carried") else ""
            candidate_lines.append(
                f"[{card['fix_id']}] {prefix}{labels}\n원문: {card.get('quote') or ''}\n이유: {card.get('reason') or ''}\n독자: {card.get('reader_problem') or ''}"
            )
        scene_summary = "\n".join(
            f"{item.get('scene_no', index + 1)}. {item.get('summary') or ''}"
            for index, item in enumerate((report.get("scene_map") or {}).get("scenes") or [])
            if isinstance(item, dict)
        )
        result = _one(
            prompt_loader.fill_template(
                select_template,
                {
                    "scenes": scene_summary or "(없음)",
                    "settings": stage_settings,
                    "tasks": "\n".join(str(task) for task in (report.get("tasks") or [])) or "(없음)",
                    "candidates": "\n\n".join(candidate_lines),
                },
            ),
            call_schema=select_schema,
            temperature=DETECT_SELECT_TEMPERATURE,
        )
        _add_usage(params, "card_select", result, model)
        _check_cost(params, max_cost)
        parsed = result.get("parsed") if isinstance(result.get("parsed"), dict) else {}
        kept_ids = {str(item.get("id")): item for item in (parsed.get("keep") or []) if isinstance(item, dict)}
        drop_ids = {str(item.get("id")): str(item.get("reason") or "") for item in (parsed.get("drop") or []) if isinstance(item, dict)}
        selected = []
        for card in screened:
            item = kept_ids.get(str(card.get("fix_id")))
            if item is None:
                reason = drop_ids.get(str(card.get("fix_id"))) or "작가가 손해를 보지 않음"
                prefix = "선별(이어받기 탈락): " if card.get("carried") else "선별: "
                detect_log.append(
                    {
                        "action": "drop_card",
                        "target": ",".join(card.get("tags") or []),
                        "detail": prefix + reason,
                    }
                )
                continue
            count = int(item.get("pattern_count") or 1)
            if count > 1:
                card["reason"] = (str(card.get("reason") or "") + f"\n같은 패턴 {count}곳").strip()
            selected.append(card)
        detect_log.append(
            {
                "action": "select_count",
                "target": str(len(selected)),
                "detail": f"탐지 {len(merged)} → 기준 통과 {len(screened)} → 선별 {len(selected)}",
            }
        )
    else:
        selected = []
    suggest_model = _model(options, "cards")
    suggest_schema = prompt_loader.load_json("literature/suggestion_schema.json")
    suggest_template = prompt_loader.load_text("literature/suggestion_prompt.txt")
    suggest_log: list[dict[str, str]] = []
    pending = [
        card
        for card in selected
        if card.get("form") == "suggest"
        and not (card.get("carried") and str(card.get("suggestion") or "").strip())
    ]
    for offset in range(0, len(pending), 8):
        batch = pending[offset : offset + 8]
        for index, card in enumerate(batch):
            card["fix_id"] = str(offset + index)
        still = list(batch)
        for attempt in (1, 2):
            if not still:
                break
            blocks = []
            for card in still:
                before, after = _neighbor_sentences(assembled, card)
                labels = ", ".join(TAG_LABELS.get(tag, tag) for tag in (card.get("tags") or []))
                blocks.append(
                    f"[{card['fix_id']}]\n유형: {labels}\n이유: {card.get('reason') or ''}\n"
                    f"앞 문장: {before}\n원문: {card.get('quote') or ''}\n뒤 문장: {after}"
                )
            result = _one(
                prompt_loader.fill_template(
                    suggest_template,
                    {"narration": narration or "(비어 있음)", "dialogue": str(project.get("style_dialogue") or "") or "(비어 있음)", "cards": "\n\n".join(blocks)},
                ),
                call_model=suggest_model,
                call_schema=suggest_schema,
            )
            _add_usage(params, f"card_suggestion:{attempt}:{offset}", result, suggest_model)
            _check_cost(params, max_cost)
            parsed = result.get("parsed") if isinstance(result.get("parsed"), dict) else {}
            _, step_log, still = apply_suggestion_fixes(still, list(parsed.get("fixes") or []), attempt=attempt)
            suggest_log.extend(step_log)
    meaning_schema = prompt_loader.load_json("literature/meaning_schema.json")
    meaning_cache: dict[tuple[str, str], bool] = {}

    def meaning_ok(original: str, suggestion: str) -> bool:
        key = (original, suggestion)
        if key in meaning_cache:
            return meaning_cache[key]
        verify_model = _model(options, "verify")
        result = _one(
            "원문 의미가 수정안에 유지되면 kept true, 아니면 false. 원문:\n"
            + original
            + "\n수정안:\n"
            + suggestion,
            call_model=verify_model,
            call_schema=meaning_schema,
            max_tokens=200,
            timeout=60.0,
        )
        _add_usage(params, "card_meaning", result, verify_model)
        _check_cost(params, max_cost)
        parsed = result.get("parsed") if isinstance(result.get("parsed"), dict) else {}
        meaning_cache[key] = bool(parsed.get("kept", True))
        return meaning_cache[key]

    checked, log = demote_cards(
        selected,
        style_narration=narration,
        style_choice=choice,
        terms=terms,
        meaning_ok=meaning_ok,
        style_dialogue=str(project.get("style_dialogue") or ""),
        manuscript="\n".join(str(row.get("text") or "") for row in (assembled.get("paragraphs") or [])),
    )
    checked.sort(key=lambda card: (int(card.get("global_para") or 0), str(card.get("quote") or "")))
    return checked, detect_log + suggest_log + log


def card_rows(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """feedback_card INSERT 행으로 바꾼다."""
    rows = []
    for card in cards:
        tags = list(card.get("tags") or [])
        form = "suggest" if card.get("form") == "suggest" and card.get("suggestion") else "note"
        rows.append(
            {
                "scene_id": int(card["scene_id"]),
                "kind": "correction" if form == "suggest" else "style",
                "style_type": tags[0] if tags else "",
                "priority": card.get("priority") or "low",
                "start_para": card.get("start_para"),
                "end_para": card.get("end_para"),
                "start_quote": card.get("start_quote") or "",
                "end_quote": card.get("end_quote") or "",
                "original_text": card.get("original_text") or card.get("quote") or "",
                "reason": (
                    str(card.get("reason") or "")
                    + (("\n독자: " + str(card.get("reader_problem"))) if card.get("reader_problem") and "독자:" not in str(card.get("reason") or "") else "")
                ).strip(),
                "edit_plan": "",
                "suggestion": card.get("suggestion") if form == "suggest" else None,
                "report_ref": card.get("source_key") or "",
                "title": " · ".join(TAG_LABELS.get(tag, tag) for tag in tags),
                "card_form": form,
                "issue_tags_json": tags,
                "source_key": card.get("source_key") or "",
                "intentional": 1 if card.get("intentional") else 0,
                "warnings_json": [{"code": "ai_check", "message": "AI 검사, 확인 필요"}],
                "perspectives_json": (
                    {"conflict_id": card.get("conflict_id"), "notification_id": card.get("notification_id")}
                    if card.get("conflict_id") or card.get("notification_id") is not None
                    else None
                ),
            }
        )
    return rows
