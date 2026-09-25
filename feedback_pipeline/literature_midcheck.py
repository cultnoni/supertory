"""장편 작품 중간 점검: 입력 조립·원문 확인·리포트."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from typing import Any

import literary_form
from feedback_pipeline import prompt_loader
from feedback_pipeline.config import (
    FEEDBACK_MODEL,
    HAIKU_45_MODEL,
    LITERATURE_MIDCHECK_PROMPT_VERSION,
    MAX_COST_USD_MIDCHECK,
    MIDCHECK_NUDGE_EVERY,
    MIDCHECK_VERIFY_LIMIT,
)
from feedback_pipeline.literature_cache import build_cached_system, with_schema_instruction
from feedback_pipeline.literature_context import (
    assemble_unit,
    build_prior_context,
    refresh_unit_summaries,
    unit_label,
    unit_source_hash,
    unit_summary_from_scenes,
)
from feedback_pipeline.literature_settings_write import list_baits, list_characters
from feedback_pipeline.literature_signals import collect_signals, dialogue_ratio_label
from feedback_pipeline.literature_text import load_scene_rows
from feedback_pipeline.literature_unit_work import (
    ensure_light_unit_works,
    resolve_unit_work,
    work_has_substance,
)
from feedback_pipeline.literature_units import load_literary_units
from feedback_pipeline.runner import CostLimitExceeded, _add_usage, _check_cost
from feedback_store import create_run, update_run
import tory_notifications

PIPELINE = "literature_midcheck"

_UNIT_REF = re.compile(r"(?P<num>\d+)\s*장(?!면)")
_UNIT_RANGE_REF = re.compile(
    r"(?P<a>\d+)\s*장\s*[~\-–—]\s*(?P<b>\d+)\s*장|(?P<c>\d+)\s*[~\-–—]\s*(?P<d>\d+)\s*장"
)
_TOOL_REVISION = re.compile(
    r"설정집|추가할까요|등록해|모티프[·\s]*복선에|도구\s*사용|칸에\s*추가"
)
_FORBIDDEN_JARGON = re.compile(
    r"summary_only|chapter_work|기록\s*공백|데이터\s*없음|요약\s*전용|요약\s*공백|"
    r"문서상\s*공백|기록상\s*공백|시스템\s*(사정|내부)|요약문\s*이\s*없|요약이\s*없",
    re.IGNORECASE,
)
_SPECULATIVE = re.compile(
    r"인상이다|보인다|불확실(?:하다|해|하고|한)?|확인이 필요(?:하다|해)?|것 같(?:다|은|아)|"
    r"짐작된다|추정된다|것으로 보이나"
)
_DECIMAL_RATIO = re.compile(r"\b0\.\d{2,}\b|\(\s*0\.\d+\s*\)")


def _default_summarizer(scene_id: int, body: dict[str, Any]) -> dict[str, Any]:
    text = str((body or {}).get("content_md") or "")
    plain = " ".join(text.replace("<", " <").split())
    return {
        "summary": json.dumps(
            {
                "events": plain[:400],
                "characters": [],
                "world_facts": [],
                "baits": [],
            },
            ensure_ascii=False,
        )
    }


def records_for_model(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """모델 입력용: 내부 플래그·필드명을 빼고 원고 판단에 쓸 내용만 남긴다."""
    out = []
    for item in records or []:
        work = item.get("chapter_work") if isinstance(item.get("chapter_work"), dict) else {}
        row = {
            "unit_no": item.get("unit_no"),
            "title": item.get("title") or "",
            "label": item.get("label") or "",
            "one_line": (work.get("one_line") if work else "") or item.get("scene_summary_line") or "",
            "chapter_work": work or None,
            "dialogue": item.get("dialogue_label") or dialogue_ratio_label(item.get("dialogue_ratio") or 0),
            "paper_pages": item.get("paper_pages") or 0,
        }
        out.append(row)
    return out


def scrub_system_jargon(text: str) -> str:
    """결과 문구에서 시스템 내부 사정 표현을 걷어낸다."""
    source = str(text or "")
    if not source.strip():
        return source
    cleaned = _FORBIDDEN_JARGON.sub("", source)
    cleaned = _DECIMAL_RATIO.sub("", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def count_speculative_phrases(text: str) -> list[str]:
    return [match.group(0) for match in _SPECULATIVE.finditer(str(text or ""))]


def scrub_speculative(text: str) -> tuple[str, list[str]]:
    """추측 표현을 걷어내고 제거된 표현 목록을 돌려준다."""
    found = count_speculative_phrases(text)
    cleaned = _SPECULATIVE.sub("", str(text or ""))
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,.。])", r"\1", cleaned)
    return cleaned.strip(), found


def apply_verify_revisions(
    text: str,
    verify_results: list[dict[str, Any]],
) -> tuple[str, list[dict[str, str]]]:
    """revise된 판단을 본문에 반영하고, 원 표현 잔여를 걸러낸다."""
    out = str(text or "")
    log: list[dict[str, str]] = []
    for item in verify_results or []:
        if not isinstance(item, dict) or item.get("decision") != "revise":
            continue
        claim = str(item.get("claim") or "").strip()
        revised = str(item.get("revised_claim") or "").strip()
        if not revised:
            continue
        if claim and claim in out:
            out = out.replace(claim, revised)
            log.append({"action": "apply_revise", "target": claim[:80], "detail": revised[:120]})
            continue
        # 문장 단위: 원 claim과 겹치는 문장을 revised로 교체
        parts = re.split(r"(?<=[.!?。…])\s+|\n+", out)
        rebuilt: list[str] = []
        claim_tokens = set(re.findall(r"[가-힣]{2,}", claim)) if claim else set()
        replaced = False
        for part in parts:
            chunk = part.strip()
            if not chunk:
                continue
            tokens = set(re.findall(r"[가-힣]{2,}", chunk))
            overlap = len(tokens & claim_tokens)
            speculative = bool(_SPECULATIVE.search(chunk))
            if claim_tokens and overlap >= max(2, len(claim_tokens) // 3) and (speculative or overlap >= 3):
                rebuilt.append(revised)
                replaced = True
                log.append({"action": "apply_revise_sentence", "target": chunk[:80], "detail": revised[:120]})
            else:
                rebuilt.append(chunk)
        if replaced:
            out = "\n".join(rebuilt)
        elif claim:
            # 잔여 핵심 구절 제거
            for phrase in re.findall(r"[가-힣]{4,}", claim):
                if phrase in out and phrase not in revised:
                    out = out.replace(phrase, "")
                    log.append({"action": "scrub_stale_claim", "target": phrase, "detail": claim[:80]})
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip(), log


def blurry_from_work(work: dict[str, Any] | None) -> tuple[bool, str]:
    if work_has_substance(work):
        return False, ""
    return True, "새 사건·인물 변화·새 정보가 없음"


def _motif_key(name: str) -> str:
    return re.sub(r"\s+", "", str(name or "").strip().lower())


def discover_motifs_from_records(
    records: list[dict[str, Any]],
    settings_baits: list[dict[str, Any]],
    draft_motifs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """설정집 항목 + 두 장 이상에 등장한 장 기록 모티프를 합친다."""
    bait_by_key: dict[str, dict[str, Any]] = {}
    for bait in settings_baits or []:
        summary = str(bait.get("summary") or "").strip()
        if not summary:
            continue
        bait_by_key[_motif_key(summary)] = bait

    hits: dict[str, dict[str, Any]] = {}
    for record in records or []:
        unit_no = int(record.get("unit_no") or 0)
        work = record.get("chapter_work") if isinstance(record.get("chapter_work"), dict) else {}
        names: list[str] = []
        for key in ("motifs_new", "motifs_returned", "baits_resolved"):
            for raw in work.get(key) or []:
                text = str(raw or "").strip()
                if text:
                    names.append(text)
        for name in names:
            key = _motif_key(name)
            if not key:
                continue
            slot = hits.setdefault(key, {"name": name, "units": set()})
            if len(name) < len(str(slot.get("name") or "")):
                slot["name"] = name
            slot["units"].add(unit_no)

    by_key: dict[str, dict[str, Any]] = {}
    for raw in draft_motifs or []:
        if not isinstance(raw, dict):
            continue
        name = scrub_system_jargon(str(raw.get("name") or ""))[:40]
        key = _motif_key(name) or _motif_key(str(raw.get("id") or ""))
        if not key:
            continue
        bait = bait_by_key.get(key)
        in_settings = bool(raw.get("in_settings")) if "in_settings" in raw else bool(bait)
        if bait:
            in_settings = True
        status = classify_motif_status(
            intentionally_open=bool(
                (bait or {}).get("intentionally_open") or raw.get("intentionally_open")
            ),
            status_hint=str(raw.get("status") or ""),
        )
        by_key[key] = {
            "id": str(raw.get("id") or (bait or {}).get("id") or f"discovered:{key}")[:40],
            "name": name or str((bait or {}).get("summary") or "")[:40],
            "first_unit": int(raw.get("first_unit") or 0),
            "last_unit": int(raw.get("last_unit") or 0),
            "status": status,
            "in_settings": in_settings,
            "propose_add": not in_settings,
        }

    for key, slot in hits.items():
        units = sorted(int(u) for u in slot["units"] if int(u) > 0)
        if len(units) < 2:
            continue
        bait = bait_by_key.get(key)
        name = str(slot.get("name") or "")[:40]
        if key in by_key:
            item = by_key[key]
            item["first_unit"] = min(u for u in units + [item["first_unit"]] if u)
            item["last_unit"] = max(u for u in units + [item["last_unit"]] if u)
            if bait:
                item["in_settings"] = True
                item["propose_add"] = False
                item["id"] = str(bait.get("id") or item["id"])[:40]
            continue
        in_settings = bool(bait)
        by_key[key] = {
            "id": str((bait or {}).get("id") or f"discovered:{key}")[:40],
            "name": (str((bait or {}).get("summary") or name))[:40],
            "first_unit": units[0],
            "last_unit": units[-1],
            "status": classify_motif_status(
                intentionally_open=bool((bait or {}).get("intentionally_open")),
                status_hint="진행 중",
            ),
            "in_settings": in_settings,
            "propose_add": not in_settings,
        }

    # 설정집 항목은 모델 출력·다장 발견에 잡힌 것만 (빈 0~0장 나열 방지)
    return sorted(by_key.values(), key=lambda item: (int(item.get("first_unit") or 0), str(item.get("name") or "")))


def rewrite_body_with_revisions(
    claude: Any,
    *,
    body: str,
    intent_gap: str,
    verify_results: list[dict[str, Any]],
    model: str,
) -> dict[str, Any]:
    revisions = [
        {
            "from": item.get("claim"),
            "to": item.get("revised_claim"),
            "note": item.get("note"),
        }
        for item in verify_results or []
        if isinstance(item, dict) and item.get("decision") == "revise" and item.get("revised_claim")
    ]
    if not revisions:
        return {"body": body, "intent_gap": intent_gap, "result": None, "skipped": True}
    result = _invoke(
        claude,
        prompt_loader.fill_template(
            prompt_loader.load_text("literature/midcheck_rewrite_prompt.txt"),
            {
                "body": body,
                "intent_gap": intent_gap,
                "revisions": json.dumps(revisions, ensure_ascii=False),
            },
        ),
        prompt_loader.load_json("literature/midcheck_rewrite_schema.json"),
        model,
        max_tokens=1600,
        cached_system="편집자. JSON만. revise만 반영하고 나머지는 유지.",
    )
    parsed = result.get("parsed") if isinstance(result.get("parsed"), dict) else {}
    return {
        "body": str(parsed.get("body") or body),
        "intent_gap": str(parsed.get("intent_gap") or intent_gap),
        "result": result,
        "skipped": False,
        "revisions": revisions,
    }


def check_revise_leftovers(
    claude: Any,
    *,
    body: str,
    verify_results: list[dict[str, Any]],
    model: str,
) -> dict[str, Any]:
    revisions = [
        {"claim": item.get("claim"), "revised_claim": item.get("revised_claim")}
        for item in verify_results or []
        if isinstance(item, dict) and item.get("decision") == "revise" and item.get("claim")
    ]
    if not revisions:
        return {"items": [], "result": None, "skipped": True}
    result = _invoke(
        claude,
        prompt_loader.fill_template(
            prompt_loader.load_text("literature/midcheck_revise_check_prompt.txt"),
            {
                "body": body,
                "revisions": json.dumps(revisions, ensure_ascii=False),
            },
        ),
        prompt_loader.load_json("literature/midcheck_revise_check_schema.json"),
        model,
        max_tokens=800,
        cached_system="검증만. JSON만.",
    )
    parsed = result.get("parsed") if isinstance(result.get("parsed"), dict) else {}
    items = parsed.get("items") if isinstance(parsed.get("items"), list) else []
    return {"items": items, "result": result, "skipped": False}


def drop_sentence_from_body(body: str, sentence: str) -> str:
    """지목된 문장을 본문에서 뺀다. 없으면 원문 유지."""
    source = str(body or "")
    target = str(sentence or "").strip()
    if not source or not target:
        return source
    if target in source:
        cleaned = source.replace(target, "", 1)
    else:
        compact = re.sub(r"\s+", "", target)
        cleaned = source
        for chunk in re.split(r"(?<=[.。!！?？\n])\s*", source):
            if compact and re.sub(r"\s+", "", chunk) == compact:
                cleaned = source.replace(chunk, "", 1)
                break
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def rewrite_leftover_sentences(
    claude: Any,
    *,
    body: str,
    intent_gap: str,
    leftovers: list[dict[str, Any]],
    verify_results: list[dict[str, Any]],
    model: str,
) -> dict[str, Any]:
    """still_present로 지목된 문장만 다시 고친다."""
    claim_to_revised = {
        str(item.get("claim") or ""): str(item.get("revised_claim") or "")
        for item in verify_results or []
        if isinstance(item, dict) and item.get("decision") == "revise"
    }
    revisions = []
    for item in leftovers or []:
        if not isinstance(item, dict) or not item.get("still_present"):
            continue
        sentence = str(item.get("leftover_sentence") or "").strip()
        if not sentence:
            continue
        claim = str(item.get("claim") or "")
        revisions.append(
            {
                "from": sentence,
                "to": claim_to_revised.get(claim) or item.get("note") or "",
                "note": "단정 잔여 문장만 수정",
            }
        )
    if not revisions:
        return {"body": body, "intent_gap": intent_gap, "result": None, "skipped": True, "revisions": []}
    result = _invoke(
        claude,
        prompt_loader.fill_template(
            prompt_loader.load_text("literature/midcheck_rewrite_prompt.txt"),
            {
                "body": body,
                "intent_gap": intent_gap,
                "revisions": json.dumps(revisions, ensure_ascii=False),
            },
        ),
        prompt_loader.load_json("literature/midcheck_rewrite_schema.json"),
        model,
        max_tokens=1600,
        cached_system="편집자. JSON만. 지목 문장만 고치고 나머지는 유지.",
    )
    parsed = result.get("parsed") if isinstance(result.get("parsed"), dict) else {}
    return {
        "body": str(parsed.get("body") or body),
        "intent_gap": str(parsed.get("intent_gap") or intent_gap),
        "result": result,
        "skipped": False,
        "revisions": revisions,
    }


def filter_manuscript_revisions(revisions: list[Any]) -> tuple[list[str], list[dict[str, str]]]:
    """설정집·도구 사용 제안은 퇴고 방향에서 뺀다."""
    kept: list[str] = []
    log: list[dict[str, str]] = []
    for raw in revisions or []:
        text = scrub_system_jargon(str(raw or "")).strip()
        if not text:
            continue
        if _TOOL_REVISION.search(text):
            log.append(
                {
                    "action": "drop_tool_revision",
                    "target": text[:80],
                    "detail": "settings/tool suggestion removed",
                }
            )
            continue
        kept.append(text[:120])
    return kept[:3], log


def rewrite_out_of_range_units(
    text: str,
    *,
    min_unit: int,
    max_unit: int,
) -> str:
    """범위 밖 N장 언급을 앞부분/뒷부분으로 바꾼다."""
    source = str(text or "")
    if not source.strip():
        return source

    def range_repl(match: re.Match[str]) -> str:
        a = int(match.group("a") or match.group("c") or 0)
        b = int(match.group("b") or match.group("d") or 0)
        lo, hi = (a, b) if a <= b else (b, a)
        if hi < min_unit:
            return "앞부분"
        if lo > max_unit:
            return "뒷부분"
        if lo < min_unit and hi > max_unit:
            return f"{min_unit}~{max_unit}장"
        if lo < min_unit:
            return f"앞부분~{hi}장" if hi >= min_unit else "앞부분"
        if hi > max_unit:
            return f"{lo}장~뒷부분" if lo <= max_unit else "뒷부분"
        return match.group(0)

    text2 = _UNIT_RANGE_REF.sub(range_repl, source)

    def one_repl(match: re.Match[str]) -> str:
        num = int(match.group("num") or 0)
        if num < min_unit:
            return "앞부분"
        if num > max_unit:
            return "뒷부분"
        return match.group(0)

    return _UNIT_REF.sub(one_repl, text2)


def filter_prev_for_range(prev: dict[str, Any] | None, known_unit_nos: set[int]) -> dict[str, Any] | None:
    if not isinstance(prev, dict):
        return None
    if not known_unit_nos:
        return None
    motifs = []
    for item in prev.get("motifs") or []:
        if not isinstance(item, dict):
            continue
        first = int(item.get("first_unit") or 0)
        last = int(item.get("last_unit") or 0)
        if first and last and (last < min(known_unit_nos) or first > max(known_unit_nos)):
            continue
        if first in known_unit_nos or last in known_unit_nos or (
            first and last and any(first <= n <= last for n in known_unit_nos)
        ):
            motifs.append(item)
    arcs = []
    for item in prev.get("character_arcs") or []:
        if not isinstance(item, dict):
            continue
        blob = f"{item.get('change') or ''} {item.get('stalled') or ''}"
        nums = {int(m.group("num")) for m in _UNIT_REF.finditer(blob)}
        if nums and not (nums & known_unit_nos):
            continue
        arcs.append(item)
    return {
        "reading": prev.get("reading"),
        "motifs": motifs,
        "character_arcs": arcs,
    }


def midcheck_top_notes(
    *,
    summary_only_count: int,
    failed_unit_nos: list[int],
) -> list[str]:
    notes = []
    # 가벼운 장 기록이 있으면 summary_only_count는 0으로 온다.
    if summary_only_count:
        notes.append(f"{int(summary_only_count)}개 장은 요약만으로 점검했어요")
    if failed_unit_nos:
        joined = ", ".join(f"{n}장" for n in failed_unit_nos)
        notes.append(f"{joined}은 장 기록 없이 점검했어요")
    return notes


def reading_section_title(report: dict[str, Any]) -> str:
    span = report.get("range") if isinstance(report.get("range"), dict) else {}
    if span.get("whole"):
        return "토리가 읽은 지금까지의 작품"
    start = span.get("start_unit_no") or ((int(span.get("start_ord") or 0) + 1) if span else None)
    end = span.get("end_unit_no") or ((int(span.get("end_ord") or 0) + 1) if span else None)
    if start and end:
        return f"토리가 읽은 {start}~{end}장"
    return "토리가 읽은 지금까지의 작품"


def midcheck_nudge_dedupe_key(unit_count: int) -> str:
    milestone = (int(unit_count) // MIDCHECK_NUDGE_EVERY) * MIDCHECK_NUDGE_EVERY
    return f"midcheck-nudge:{milestone}"


def maybe_create_midcheck_nudge(conn, project_id: int, unit_count: int) -> dict[str, Any] | None:
    """장편 단위 수가 10의 배수에 도달하면 권유 알림. 자동 실행 없음."""
    count = int(unit_count or 0)
    if count < MIDCHECK_NUDGE_EVERY or count % MIDCHECK_NUDGE_EVERY != 0:
        return None
    key = midcheck_nudge_dedupe_key(count)
    return tory_notifications.create_tory_notification(
        conn,
        project_id,
        kind="midcheck_nudge",
        title="지금까지 쓴 부분을 한 번 점검해볼까요?",
        body=f"분석 단위가 {count}개에 이르렀습니다. 작품 중간 점검으로 숲을 보면 좋습니다.",
        payload={"unit_count": count, "milestone": count},
        actions=[
            {
                "id": "open-midcheck",
                "label": "점검하기",
                "type": "navigate",
                "resolve": False,
                "target": {"surface": "midcheck", "mode": "literature_midcheck"},
            },
            {
                "id": "later",
                "label": "나중에",
                "type": "snooze",
                "resolve": False,
                "target": {"hours": 72},
            },
            {
                "id": "never",
                "label": "다시 묻지 않기",
                "type": "dismiss_forever",
                "resolve": True,
                "target": {},
            },
        ],
        dedupe_key=key,
    )


def _project_row(conn, project_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM project WHERE id = ?", (int(project_id),)).fetchone()
    if row is None:
        raise LookupError("작품을 찾을 수 없습니다.")
    return dict(row)


def _settings_blob(conn, project: dict[str, Any]) -> str:
    lines = [
        f"제목: {project.get('title') or '(없음)'}",
        f"작품소개: {str(project.get('intro_md') or '').strip() or '(비어 있음)'}",
        f"기획의도: {str(project.get('intent_md') or '').strip() or '(비어 있음)'}",
        "(기획의도와의 거리는 위 작품소개·기획의도만 근거로 쓴다.)",
        f"의도적 선택(작가 뜻): {str(project.get('style_choice') or '').strip() or '(비어 있음)'}",
        f"고치고 싶은 습관(작가 뜻): {str(project.get('style_habit') or '').strip() or '(비어 있음)'}",
        "문체 관찰(목표 아님·평가 근거 금지):",
        f"  서술 기본값: {str(project.get('style_narration') or '').strip() or '(비어 있음)'}",
        f"  문장: {str(project.get('style_sentence') or '').strip() or '(비어 있음)'}",
        f"  대화: {str(project.get('style_dialogue') or '').strip() or '(비어 있음)'}",
        f"  어휘와 이미지: {str(project.get('style_lexicon') or '').strip() or '(비어 있음)'}",
        "세계관:\n" + (str(project.get("worldbuilding_md") or "").strip() or "(비어 있음)"),
    ]
    for person in list_characters(conn, int(project["id"])):
        aliases = ", ".join(person.get("aliases") or [])
        lines.append(
            f"인물 {person['id']} {person.get('name')}"
            + (f" (별칭: {aliases})" if aliases else "")
            + f": {str(person.get('profile_md') or '')[:400] or '(비어 있음)'}"
        )
    for bait in list_baits(conn, int(project["id"])):
        open_mark = " [의도적으로 열어둠]" if bait.get("intentionally_open") else ""
        lines.append(f"모티프·복선 {bait.get('id')}: {bait.get('summary') or ''}{open_mark}")
    return "\n".join(lines)


def _scene_narration_ratio(scene_map: dict[str, Any] | None) -> dict[str, float]:
    scenes = (scene_map or {}).get("scenes") if isinstance(scene_map, dict) else None
    if not isinstance(scenes, list) or not scenes:
        return {"scene": 0.0, "summary": 0.0, "mixed": 0.0}
    counts = {"장면": 0, "요약": 0, "혼합": 0}
    for item in scenes:
        if not isinstance(item, dict):
            continue
        key = str(item.get("narration") or "")
        if key in counts:
            counts[key] += 1
    total = max(1, sum(counts.values()))
    return {
        "scene": round(counts["장면"] / total, 3),
        "summary": round(counts["요약"] / total, 3),
        "mixed": round(counts["혼합"] / total, 3),
    }


def _latest_unit_report(conn, project_id: int, unit: dict[str, Any]) -> dict[str, Any] | None:
    rows = conn.execute(
        """
        SELECT id, report_json, params_json FROM feedback_run
        WHERE project_id = ? AND pipeline = 'literature_long' AND status IN ('ok', 'partial')
        ORDER BY id DESC
        """,
        (int(project_id),),
    ).fetchall()
    kind = str(unit.get("kind") or "")
    unit_id = int(unit.get("id") or 0)
    for row in rows:
        try:
            params = json.loads(row["params_json"] or "{}")
        except json.JSONDecodeError:
            params = {}
        stored = params.get("unit") if isinstance(params.get("unit"), dict) else {}
        if str(stored.get("kind") or "") != kind or int(stored.get("id") or 0) != unit_id:
            continue
        try:
            report = json.loads(row["report_json"] or "{}")
        except json.JSONDecodeError:
            report = {}
        if isinstance(report, dict):
            report["_run_id"] = int(row["id"])
            report["_unit_source_hash"] = str(params.get("unit_source_hash") or "")
            return report
    return None


def build_unit_record(conn, project_id: int, unit: dict[str, Any]) -> dict[str, Any]:
    assembled = assemble_unit(conn, project_id, list(unit.get("scene_ids") or []))
    signals = collect_signals(assembled.get("paragraphs") or [], spell_error_count=0, contest={})
    report = _latest_unit_report(conn, project_id, unit)
    current_hash = unit_source_hash(assembled)
    resolved = resolve_unit_work(conn, project_id, unit, assembled=assembled)
    chapter_work = resolved.get("work") if isinstance(resolved.get("work"), dict) else None
    work_source = resolved.get("source")
    summary_only = not work_has_substance(chapter_work)
    scene_map = report.get("scene_map") if isinstance(report, dict) else None
    if report is not None and str(report.get("_unit_source_hash") or "") != current_hash:
        scene_map = None
    narration = _scene_narration_ratio(scene_map if isinstance(scene_map, dict) else None)
    scene_summary = unit_summary_from_scenes(conn, unit)
    one_line = ""
    if isinstance(chapter_work, dict) and chapter_work.get("one_line"):
        one_line = str(chapter_work.get("one_line") or "")[:120]
    elif isinstance(scene_summary, str) and scene_summary.strip():
        one_line = re.sub(r"\s+", " ", scene_summary).strip()[:120]
    work_line = ""
    if isinstance(chapter_work, dict):
        events = chapter_work.get("new_events") or []
        work_line = "; ".join(str(x) for x in events[:3] if str(x).strip())[:120]
    blurry, blurry_reason = blurry_from_work(chapter_work)
    dialogue_ratio = float(signals.get("dialogue_ratio") or 0)
    return {
        "unit_no": int(unit.get("ord") or 0) + 1,
        "unit_kind": unit.get("kind"),
        "unit_id": unit.get("id"),
        "title": unit.get("title") or "",
        "label": unit_label(unit),
        "scene_ids": list(unit.get("scene_ids") or []),
        "scene_summary_line": one_line,
        "chapter_work": chapter_work,
        "chapter_work_line": work_line,
        "work_source": work_source,
        "summary_only": summary_only,
        "blurry_forced": blurry,
        "blurry_reason": blurry_reason,
        "paper_pages": signals.get("paper_pages") or assembled.get("paper_pages") or 0,
        "dialogue_ratio": dialogue_ratio,
        "monologue_ratio": float(signals.get("monologue_ratio") or 0),
        "dialogue_label": signals.get("dialogue_label") or dialogue_ratio_label(dialogue_ratio),
        "narration_ratio": narration,
        "source_hash": current_hash,
    }


def select_units(
    units: list[dict[str, Any]],
    *,
    start_ord: int | None,
    end_ord: int | None,
) -> list[dict[str, Any]]:
    if not units:
        return []
    start = 0 if start_ord is None else max(0, int(start_ord))
    end = len(units) - 1 if end_ord is None else min(len(units) - 1, int(end_ord))
    if end < start:
        start, end = end, start
    return units[start : end + 1]


def previous_midcheck(conn, project_id: int, exclude_run_id: int = 0) -> dict[str, Any] | None:
    rows = conn.execute(
        """
        SELECT id, report_json, params_json FROM feedback_run
        WHERE project_id = ? AND pipeline = ? AND status IN ('ok', 'partial') AND id != ?
        ORDER BY id DESC LIMIT 1
        """,
        (int(project_id), PIPELINE, int(exclude_run_id or 0)),
    ).fetchall()
    if not rows:
        return None
    try:
        report = json.loads(rows[0]["report_json"] or "{}")
    except json.JSONDecodeError:
        report = {}
    if isinstance(report, dict):
        report["_run_id"] = int(rows[0]["id"])
        return report
    return None


def _excerpt_by_keywords(plain: str, keywords: list[str], *, limit: int = 1200) -> str:
    text = str(plain or "")
    if not text.strip():
        return ""
    hits: list[tuple[int, str]] = []
    for raw in keywords or []:
        key = str(raw or "").strip()
        if len(key) < 2:
            continue
        idx = text.find(key)
        if idx < 0:
            continue
        start = max(0, idx - 80)
        end = min(len(text), idx + len(key) + 200)
        hits.append((start, text[start:end]))
    if not hits:
        return text[:limit]
    chunks = []
    used = 0
    for _, chunk in sorted(hits, key=lambda item: item[0])[:5]:
        if used + len(chunk) > limit:
            break
        chunks.append(chunk)
        used += len(chunk)
    return "\n…\n".join(chunks)


def classify_motif_status(
    *,
    intentionally_open: bool,
    status_hint: str = "",
) -> str:
    """열어둔 복선은 방치 의심이 아니다."""
    if intentionally_open:
        return "열어둔 복선"
    hint = str(status_hint or "").strip()
    allowed = {"진행 중", "회수됨", "방치 의심", "열어둔 복선"}
    return hint if hint in allowed else (hint or "진행 중")


def soften_unverified_unit_claims(
    text: str,
    *,
    known_unit_nos: set[int],
    evidence: str,
) -> tuple[str, list[dict[str, str]]]:
    source = scrub_system_jargon(str(text or ""))
    if not source.strip():
        return source, []
    log: list[dict[str, str]] = []
    parts = re.split(r"(?<=[.!?。…])\s+|\n+", source)
    out: list[str] = []
    blob = evidence or ""
    for part in parts:
        chunk = part.strip()
        if not chunk:
            continue
        chunk = scrub_system_jargon(chunk)
        if not chunk:
            continue
        match = _UNIT_REF.search(chunk)
        if match is None:
            out.append(chunk)
            continue
        num = int(match.group("num") or 0)
        if num not in known_unit_nos:
            # 범위 밖은 질문으로 두지 않고 앞/뒷부분으로만 남긴다
            min_u = min(known_unit_nos) if known_unit_nos else 1
            max_u = max(known_unit_nos) if known_unit_nos else 1
            rewritten = rewrite_out_of_range_units(chunk, min_unit=min_u, max_unit=max_u)
            if rewritten.strip():
                out.append(rewritten)
            continue
        tokens = re.findall(r"[가-힣A-Za-z]{2,}", chunk)[:6]
        hits = sum(1 for token in tokens if token in blob)
        if tokens and hits < max(1, len(tokens) // 3):
            question = scrub_system_jargon(
                f"{num}장에서 그렇게 보인 것 같은데, 맞을까요? ({chunk[:100]})"
            )
            log.append({"action": "soften_midcheck_claim", "target": f"{num}장", "detail": chunk[:200]})
            out.append(question)
            continue
        out.append(chunk)
    return "\n".join(out), log


def apply_range_and_jargon(
    text: str,
    *,
    known_unit_nos: set[int],
) -> str:
    cleaned = scrub_system_jargon(text)
    if not known_unit_nos:
        return cleaned
    return rewrite_out_of_range_units(
        cleaned,
        min_unit=min(known_unit_nos),
        max_unit=max(known_unit_nos),
    )


def _compare_motif_status(prev: dict[str, Any] | None, current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not prev:
        return []
    old_items = prev.get("motifs") if isinstance(prev.get("motifs"), list) else []
    old_by = {
        str(item.get("id") or item.get("name") or ""): str(item.get("status") or "")
        for item in old_items
        if isinstance(item, dict)
    }
    changes = []
    for item in current or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id") or item.get("name") or "")
        before = old_by.get(key)
        after = str(item.get("status") or "")
        if before and before != after:
            changes.append({"id": key, "from": before, "to": after})
    return changes


def _invoke(claude, prompt: str, schema: dict[str, Any], model: str, *, max_tokens: int, cached_system) -> dict[str, Any]:
    return claude.generate(
        with_schema_instruction(prompt, schema),
        model=model,
        system=cached_system,
        thinking="off",
        max_tokens=max_tokens,
        timeout=180.0,
    )


def _save_params(conn, run_id: int, params: dict[str, Any]) -> None:
    update_run(conn, run_id, params_json=params)


def _progress(conn, run_id: int, params: dict[str, Any], stage: str, done: int, total: int, label: str = "") -> None:
    params["progress"] = {
        "stage": stage,
        "done": int(done),
        "total": max(1, int(total)),
        "label": label or stage,
    }
    _save_params(conn, run_id, params)


def render_midcheck_md(report: dict[str, Any]) -> str:
    lines = ["# 작품 중간 점검", ""]
    for note in report.get("top_notes") or []:
        if str(note).strip():
            lines.append(str(note).strip())
    if report.get("top_notes"):
        lines.append("")
    reading = report.get("reading") if isinstance(report.get("reading"), dict) else {}
    lines.append(f"## {reading_section_title(report)}")
    lines.append(str(reading.get("body") or ""))
    if reading.get("intent_gap"):
        lines.append("")
        lines.append("기획의도와의 거리: " + str(reading.get("intent_gap")))
    lines.append("")
    lines.append("## 장별 역할 지도")
    for item in report.get("role_map") or []:
        if not isinstance(item, dict):
            continue
        flags = []
        if item.get("similar_streak"):
            flags.append("역할 유사 연속")
        if item.get("blurry"):
            reason = str(item.get("blurry_reason") or "").strip()
            flags.append(f"역할 흐릿: {reason}" if reason else "역할 흐릿")
        mark = (" [" + ", ".join(flags) + "]") if flags else ""
        lines.append(f"- {item.get('unit_no')}장: {item.get('role') or ''}{mark}")
    lines.append("")
    lines.append("## 인물 궤적")
    for item in report.get("character_arcs") or []:
        if isinstance(item, dict):
            lines.append(
                f"- {item.get('name')}: {item.get('change') or ''} "
                f"(멈춤: {item.get('stalled') or '없음'})"
            )
    lines.append("")
    lines.append("## 모티프·복선 현황")
    for item in report.get("motifs") or []:
        if isinstance(item, dict):
            mark = ""
            if item.get("propose_add") or item.get("in_settings") is False:
                mark = " [설정집에 없음 · 모티프·복선에 추가할까요?]"
            lines.append(
                f"- {item.get('name')}: {item.get('first_unit')}장~{item.get('last_unit')}장 / "
                f"{item.get('status')}{mark}"
            )
    if report.get("motif_changes"):
        lines.append("이전 점검 대비:")
        for item in report.get("motif_changes") or []:
            lines.append(f"- {item.get('id')}: {item.get('from')} → {item.get('to')}")
    lines.append("")
    lines.append("## 반복 패턴")
    for item in report.get("patterns") or []:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## 속도")
    for item in report.get("pacing") or []:
        if isinstance(item, dict):
            lines.append(f"- {item.get('units')}: {item.get('note')}")
        else:
            lines.append(f"- {item}")
    lines.append("")
    lines.append("## 큰 퇴고 방향")
    for item in (report.get("revisions") or [])[:3]:
        lines.append(f"- {item}")
    return "\n".join(lines).strip()


def prepare_midcheck_run(
    conn: sqlite3.Connection,
    project_id: int,
    *,
    start_ord: int | None,
    end_ord: int | None,
    model: str,
    prompt_version: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    project = _project_row(conn, project_id)
    if literary_form.literary_track(project) != "long":
        raise ValueError("작품 중간 점검은 일반문학 장편에서만 사용할 수 있습니다.")
    units = load_literary_units(conn, project_id)
    selected = select_units(units, start_ord=start_ord, end_ord=end_ord)
    if not selected:
        raise ValueError("점검할 분석 단위가 없습니다.")
    params = dict(params or {})
    params["range"] = {
        "start_ord": int(selected[0]["ord"]),
        "end_ord": int(selected[-1]["ord"]),
        "start_unit_no": int(selected[0]["ord"]) + 1,
        "end_unit_no": int(selected[-1]["ord"]) + 1,
        "unit_count": len(selected),
        "whole": start_ord is None and end_ord is None,
    }
    params["progress"] = params.get("progress") or {"stage": "queued", "done": 0, "total": 4}
    run_id = create_run(
        conn,
        project_id,
        run_kind="analyze",
        model=model or FEEDBACK_MODEL,
        prompt_version=prompt_version or LITERATURE_MIDCHECK_PROMPT_VERSION,
        params=params,
        pipeline=PIPELINE,
    )
    return {"run_id": run_id, "units": selected, "params": params}


def run_literature_midcheck(
    conn: sqlite3.Connection,
    project_id: int,
    run_id: int,
    options: dict[str, Any] | None = None,
    *,
    claude: Any = None,
) -> dict[str, Any]:
    options = dict(options or {})
    max_cost = float(options.get("max_cost") or MAX_COST_USD_MIDCHECK)
    verify_limit = int(options.get("verify_limit") or MIDCHECK_VERIFY_LIMIT)
    caller = claude
    if caller is None:
        from feedback_pipeline.claude_client import LiveClaude

        caller = LiveClaude()

    row = conn.execute("SELECT * FROM feedback_run WHERE id = ?", (int(run_id),)).fetchone()
    if row is None:
        raise LookupError("실행을 찾을 수 없습니다.")
    try:
        params = json.loads(row["params_json"] or "{}")
    except json.JSONDecodeError:
        params = {}
    params.setdefault(
        "usage",
        {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "cost_input_usd": 0.0,
            "cost_cache_write_usd": 0.0,
            "cost_cache_read_usd": 0.0,
            "cost_output_usd": 0.0,
            "cost_usd": 0.0,
            "stages": [],
        },
    )

    project = _project_row(conn, project_id)
    units = load_literary_units(conn, project_id)
    span = params.get("range") if isinstance(params.get("range"), dict) else {}
    selected = select_units(
        units,
        start_ord=span.get("start_ord"),
        end_ord=span.get("end_ord"),
    )
    cancel_event = options.get("cancel_event")

    def cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("취소되었습니다.")

    cancelled()
    summarizer = options.get("summarize_scene_for_index") or _default_summarizer

    def on_summary_progress(done: int, total: int) -> None:
        params["progress"] = {
            "stage": "prior_summaries",
            "done": int(done),
            "total": max(1, int(total) or 1),
            "label": f"앞 장 요약을 준비하는 중 ({done}/{total})",
        }
        _save_params(conn, run_id, params)
        if options.get("autocommit"):
            conn.commit()

    # 회차 요약은 조용히 채우고, 진행 표시는 가벼운 장 기록(Haiku)에 쓴다.
    summary_prep = refresh_unit_summaries(
        conn,
        project_id,
        selected,
        summarizer,
        on_progress=None,
    )
    light_model = str(options.get("unit_work_model") or HAIKU_45_MODEL)
    light_started = time.perf_counter()

    def on_light_usage(stage: str, result: dict[str, Any], model: str) -> None:
        _add_usage(params, stage, result, model)
        if params.get("usage", {}).get("stages"):
            params["usage"]["stages"][-1]["elapsed_ms"] = int(
                (result or {}).get("_elapsed_ms") or 0
            )

    light_prep = ensure_light_unit_works(
        conn,
        project_id,
        selected,
        caller,
        model=light_model,
        on_progress=on_summary_progress,
        on_usage=on_light_usage,
    )
    # 각 unit_work 단계 elapsed는 on_usage에서 기록
    failed_unit_nos = sorted(
        set(list(summary_prep.get("failed_unit_nos") or []) + list(light_prep.get("failed") or []))
    )
    params["summary_prep"] = {
        "queued": len(summary_prep.get("queued") or []),
        "done": len(summary_prep.get("done") or []),
        "failed": len(summary_prep.get("failed") or []),
        "failed_unit_nos": list(summary_prep.get("failed_unit_nos") or []),
    }
    params["unit_work_prep"] = {
        "queued": int(light_prep.get("queued") or 0),
        "done": list(light_prep.get("done") or []),
        "failed": list(light_prep.get("failed") or []),
        "model": light_model,
        "elapsed_ms": int((time.perf_counter() - light_started) * 1000),
        "created": light_prep.get("created") or [],
    }
    _check_cost(params, max_cost)
    _progress(conn, run_id, params, "assemble", 1, 5, "단위 기록을 모으는 중")
    if options.get("autocommit"):
        conn.commit()
    cancelled()

    records = [build_unit_record(conn, project_id, unit) for unit in selected]
    summary_only_count = sum(1 for item in records if item.get("summary_only"))
    top_notes = midcheck_top_notes(
        summary_only_count=summary_only_count,
        failed_unit_nos=failed_unit_nos,
    )
    settings_text = _settings_blob(conn, project)
    known = {int(item["unit_no"]) for item in records}
    prev_raw = previous_midcheck(conn, project_id, exclude_run_id=run_id)
    prev = filter_prev_for_range(prev_raw, known)

    # 입력이 크면 범위 앞쪽 요약을 계층 압축으로 줄인다
    compress_note = ""
    model_records = records_for_model(records)
    unit_blob = json.dumps(model_records, ensure_ascii=False)
    if len(unit_blob) > 14000 and len(records) > 8:
        try:
            prior = build_prior_context(
                conn,
                project_id,
                units,
                int(selected[-1]["ord"]) + 1,
                claude=caller,
                on_usage=lambda stage, result, model: _add_usage(params, stage, result, model),
            )
            compress_note = str(prior.get("text") or "")[:6000]
            _check_cost(params, max_cost)
        except Exception:  # noqa: BLE001
            compress_note = ""

    shared = (
        f"중간 점검 범위: {records[0]['unit_no']}~{records[-1]['unit_no']}장\n"
        f"(범위 밖은 앞부분/뒷부분으로만 언급. 판단 대상은 범위 안 단위만.)\n\n"
        f"단위 기록:\n{unit_blob[:20000]}\n\n"
        f"압축 요약:\n{compress_note or '(없음)'}\n\n"
        f"설정집:\n{settings_text}\n\n"
        f"이전 중간 점검(범위 관련):\n"
        f"{json.dumps({'reading': (prev or {}).get('reading'), 'motifs': (prev or {}).get('motifs'), 'character_arcs': (prev or {}).get('character_arcs')}, ensure_ascii=False) if prev else '(없음)'}"
    )
    common = "너는 장편 소설의 편집자다. JSON만 짧게 출력한다. 시스템 내부 사정은 쓰지 않는다."
    cached_system = build_cached_system(shared, common)
    model = str(options.get("model") or FEEDBACK_MODEL)

    _progress(conn, run_id, params, "pass1", 2, 5, "숲을 읽는 중")
    if options.get("autocommit"):
        conn.commit()
    started = time.perf_counter()
    pass1 = _invoke(
        caller,
        prompt_loader.fill_template(
            prompt_loader.load_text("literature/midcheck_pass1_prompt.txt"),
            {"verify_limit": str(verify_limit)},
        ),
        prompt_loader.load_json("literature/midcheck_pass1_schema.json"),
        model,
        max_tokens=4500,
        cached_system=cached_system,
    )
    _add_usage(params, "pass1", pass1, model)
    params["usage"]["stages"][-1]["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    _check_cost(params, max_cost)
    draft = pass1.get("parsed") if isinstance(pass1.get("parsed"), dict) else {}

    # 원문 확인
    checks_raw = draft.get("needs_verify") if isinstance(draft.get("needs_verify"), list) else []
    verify_results: list[dict[str, Any]] = []
    unit_by_no = {int(item["unit_no"]): item for item in records}
    for index, raw in enumerate(checks_raw[: max(0, verify_limit)]):
        if not isinstance(raw, dict):
            continue
        _progress(
            conn,
            run_id,
            params,
            "verify",
            3,
            5,
            f"원문 확인 {index + 1}/{min(len(checks_raw), verify_limit)}",
        )
        if options.get("autocommit"):
            conn.commit()
        unit_nos = []
        for value in raw.get("unit_nos") or []:
            try:
                num = int(value)
            except (TypeError, ValueError):
                continue
            if num in known:
                unit_nos.append(num)
        keywords = [str(x) for x in (raw.get("keywords") or []) if str(x).strip()][:6]
        excerpts = []
        for num in unit_nos[:4]:
            record = unit_by_no.get(num)
            if not record:
                continue
            assembled = assemble_unit(conn, project_id, list(record.get("scene_ids") or []))
            excerpts.append(
                {
                    "unit_no": num,
                    "excerpt": _excerpt_by_keywords(str(assembled.get("plain") or ""), keywords),
                }
            )
        started = time.perf_counter()
        verify = _invoke(
            caller,
            prompt_loader.fill_template(
                prompt_loader.load_text("literature/midcheck_verify_prompt.txt"),
                {
                    "claim": scrub_system_jargon(str(raw.get("claim") or "")),
                    "reason": scrub_system_jargon(str(raw.get("reason") or "")),
                    "excerpts": json.dumps(excerpts, ensure_ascii=False),
                },
            ),
            prompt_loader.load_json("literature/midcheck_verify_schema.json"),
            model,
            max_tokens=800,
            cached_system=build_cached_system(
                "원문 확인만 한다. JSON만. 시스템 내부 사정은 쓰지 않는다.",
                "편집자. 짧게.",
            ),
        )
        _add_usage(params, f"verify:{index}", verify, model)
        params["usage"]["stages"][-1]["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        _check_cost(params, max_cost)
        parsed = verify.get("parsed") if isinstance(verify.get("parsed"), dict) else {}
        decision = str(parsed.get("decision") or "keep").strip()
        if decision not in {"keep", "revise"}:
            decision = "keep"
        verify_results.append(
            {
                "claim": scrub_system_jargon(str(raw.get("claim") or "")),
                "reason": scrub_system_jargon(str(raw.get("reason") or "")),
                "unit_nos": unit_nos,
                "keywords": keywords,
                "decision": decision,
                "revised_claim": scrub_system_jargon(str(parsed.get("revised_claim") or "")),
                "note": scrub_system_jargon(str(parsed.get("note") or ""))[:200],
            }
        )

    open_baits = {
        str(bait.get("id")): bool(bait.get("intentionally_open"))
        for bait in list_baits(conn, project_id)
    }
    settings_baits = list_baits(conn, project_id)
    draft_motifs = []
    for raw in draft.get("motifs") or []:
        if not isinstance(raw, dict):
            continue
        bait_id = str(raw.get("id") or "")
        draft_motifs.append(
            {
                "id": bait_id,
                "name": scrub_system_jargon(str(raw.get("name") or ""))[:40],
                "first_unit": int(raw.get("first_unit") or 0),
                "last_unit": int(raw.get("last_unit") or 0),
                "status": str(raw.get("status") or ""),
                "intentionally_open": bool(open_baits.get(bait_id) or raw.get("intentionally_open")),
                "in_settings": bool(raw.get("in_settings")) if "in_settings" in raw else bool(bait_id and bait_id in open_baits),
            }
        )
    motifs = discover_motifs_from_records(records, settings_baits, draft_motifs)
    for item in motifs:
        if item.get("first_unit") and item["first_unit"] not in known:
            item["first_unit"] = min(known) if known else 0
        if item.get("last_unit") and item["last_unit"] not in known:
            item["last_unit"] = max(known) if known else 0

    role_map = []
    for raw in draft.get("role_map") or []:
        if not isinstance(raw, dict):
            continue
        unit_no = int(raw.get("unit_no") or 0)
        if unit_no not in known:
            continue
        record = unit_by_no.get(unit_no) or {}
        forced_blurry, blurry_reason = blurry_from_work(
            record.get("chapter_work") if isinstance(record.get("chapter_work"), dict) else None
        )
        role_text = apply_range_and_jargon(str(raw.get("role") or ""), known_unit_nos=known)[:80]
        role_map.append(
            {
                "unit_no": unit_no,
                "role": role_text,
                "similar_streak": bool(raw.get("similar_streak")),
                "blurry": forced_blurry,
                "blurry_reason": blurry_reason if forced_blurry else "",
            }
        )

    reading = draft.get("reading") if isinstance(draft.get("reading"), dict) else {}
    body = apply_range_and_jargon(str(reading.get("body") or ""), known_unit_nos=known)
    intent_gap = apply_range_and_jargon(str(reading.get("intent_gap") or ""), known_unit_nos=known)
    verification_log: list[dict[str, str]] = []
    body_before_rewrite = body

    # revise가 있으면 본문 재작성 호출
    rewrite_bundle = rewrite_body_with_revisions(
        caller,
        body=body,
        intent_gap=intent_gap,
        verify_results=verify_results,
        model=model,
    )
    if not rewrite_bundle.get("skipped"):
        result = rewrite_bundle.get("result") or {}
        _add_usage(params, "rewrite", result, model)
        if params.get("usage", {}).get("stages"):
            params["usage"]["stages"][-1]["elapsed_ms"] = int(result.get("_elapsed_ms") or 0)
        _check_cost(params, max_cost)
        body = apply_range_and_jargon(str(rewrite_bundle.get("body") or body), known_unit_nos=known)
        intent_gap = apply_range_and_jargon(
            str(rewrite_bundle.get("intent_gap") or intent_gap), known_unit_nos=known
        )
        verification_log.append(
            {
                "action": "rewrite_with_revisions",
                "target": str(len(rewrite_bundle.get("revisions") or [])),
                "detail": "body rewritten from revise list",
            }
        )
        check_bundle = check_revise_leftovers(
            caller, body=body, verify_results=verify_results, model=model
        )
        leftover_items: list[dict[str, Any]] = []
        if not check_bundle.get("skipped"):
            check_result = check_bundle.get("result") or {}
            _add_usage(params, "revise_check", check_result, model)
            _check_cost(params, max_cost)
            for item in check_bundle.get("items") or []:
                if not isinstance(item, dict):
                    continue
                verification_log.append(
                    {
                        "action": "revise_leftover_check",
                        "target": str(item.get("claim") or "")[:80],
                        "detail": (
                            "still_present"
                            if item.get("still_present")
                            else "cleared"
                        )
                        + (": " + str(item.get("note") or "")[:100]),
                    }
                )
                if item.get("still_present"):
                    leftover_items.append(item)
        if leftover_items:
            patch = rewrite_leftover_sentences(
                caller,
                body=body,
                intent_gap=intent_gap,
                leftovers=leftover_items,
                verify_results=verify_results,
                model=model,
            )
            if not patch.get("skipped"):
                patch_result = patch.get("result") or {}
                _add_usage(params, "leftover_patch", patch_result, model)
                _check_cost(params, max_cost)
                body = apply_range_and_jargon(str(patch.get("body") or body), known_unit_nos=known)
                intent_gap = apply_range_and_jargon(
                    str(patch.get("intent_gap") or intent_gap), known_unit_nos=known
                )
                verification_log.append(
                    {
                        "action": "leftover_sentence_rewrite",
                        "target": str(len(patch.get("revisions") or [])),
                        "detail": "targeted rewrite of still_present sentences",
                    }
                )
            recheck = check_revise_leftovers(
                caller, body=body, verify_results=verify_results, model=model
            )
            if not recheck.get("skipped"):
                recheck_result = recheck.get("result") or {}
                _add_usage(params, "revise_check", recheck_result, model)
                _check_cost(params, max_cost)
                for item in recheck.get("items") or []:
                    if not isinstance(item, dict):
                        continue
                    verification_log.append(
                        {
                            "action": "revise_leftover_recheck",
                            "target": str(item.get("claim") or "")[:80],
                            "detail": (
                                "still_present"
                                if item.get("still_present")
                                else "cleared"
                            )
                            + (": " + str(item.get("note") or "")[:100]),
                        }
                    )
                    if not item.get("still_present"):
                        continue
                    sentence = str(item.get("leftover_sentence") or "").strip()
                    if not sentence:
                        # 검증이 문장을 못 주면 이전 지목 문장으로 시도
                        for prev in leftover_items:
                            if str(prev.get("claim") or "") == str(item.get("claim") or ""):
                                sentence = str(prev.get("leftover_sentence") or "").strip()
                                break
                    if not sentence:
                        continue
                    before = body
                    body = drop_sentence_from_body(body, sentence)
                    if body != before:
                        verification_log.append(
                            {
                                "action": "leftover_sentence_drop",
                                "target": sentence[:80],
                                "detail": "removed after second still_present",
                            }
                        )

    evidence = shared + "\n" + json.dumps(verify_results, ensure_ascii=False)
    body, soft = soften_unverified_unit_claims(body, known_unit_nos=known, evidence=evidence)
    verification_log.extend(soft)
    intent_gap, soft2 = soften_unverified_unit_claims(intent_gap, known_unit_nos=known, evidence=evidence)
    verification_log.extend(soft2)

    arcs = []
    for raw in draft.get("character_arcs") or []:
        if not isinstance(raw, dict):
            continue
        change = apply_range_and_jargon(str(raw.get("change") or ""), known_unit_nos=known)
        change, soft3 = soften_unverified_unit_claims(change, known_unit_nos=known, evidence=evidence)
        verification_log.extend(soft3)
        stalled = apply_range_and_jargon(str(raw.get("stalled") or ""), known_unit_nos=known)
        arcs.append(
            {
                "name": scrub_system_jargon(str(raw.get("name") or ""))[:40],
                "change": change[:200],
                "stalled": stalled[:80],
            }
        )

    patterns = [
        apply_range_and_jargon(str(x), known_unit_nos=known)[:120]
        for x in (draft.get("patterns") or [])
        if scrub_system_jargon(str(x)).strip()
    ][:8]
    pacing = []
    for item in draft.get("pacing") or []:
        if isinstance(item, dict):
            pacing.append(
                {
                    "units": apply_range_and_jargon(str(item.get("units") or ""), known_unit_nos=known)[:40],
                    "note": apply_range_and_jargon(str(item.get("note") or ""), known_unit_nos=known)[:120],
                }
            )
        else:
            pacing.append(
                {
                    "units": "",
                    "note": apply_range_and_jargon(str(item), known_unit_nos=known)[:120],
                }
            )
    pacing = pacing[:8]
    revisions_raw = [
        apply_range_and_jargon(str(x), known_unit_nos=known)[:120]
        for x in (draft.get("revisions") or [])
        if scrub_system_jargon(str(x)).strip()
    ]
    revisions, tool_rev_log = filter_manuscript_revisions(revisions_raw)
    verification_log.extend(tool_rev_log)

    # 추측 표현·소수점 비율 최종 정리
    speculative_hits: list[str] = []
    body, hits = scrub_speculative(body)
    speculative_hits.extend(hits)
    intent_gap, hits = scrub_speculative(intent_gap)
    speculative_hits.extend(hits)
    for item in arcs:
        item["change"], hits = scrub_speculative(item["change"])
        speculative_hits.extend(hits)
        item["stalled"], hits = scrub_speculative(item["stalled"])
        speculative_hits.extend(hits)
    cleaned_patterns = []
    for p in patterns:
        cleaned, hits = scrub_speculative(p)
        speculative_hits.extend(hits)
        if cleaned:
            cleaned_patterns.append(cleaned[:120])
    patterns = cleaned_patterns[:8]
    cleaned_pacing = []
    for item in pacing:
        note, hits = scrub_speculative(item.get("note") or "")
        speculative_hits.extend(hits)
        units, hits2 = scrub_speculative(item.get("units") or "")
        speculative_hits.extend(hits2)
        cleaned_pacing.append({"units": units[:40], "note": note[:120]})
    pacing = cleaned_pacing
    cleaned_revisions = []
    for item in revisions:
        text, hits = scrub_speculative(item)
        speculative_hits.extend(hits)
        if text and not _TOOL_REVISION.search(text):
            cleaned_revisions.append(text[:120])
    revisions = cleaned_revisions[:3]
    body = scrub_system_jargon(body)
    intent_gap = scrub_system_jargon(intent_gap)
    verification_log.append(
        {
            "action": "speculative_count",
            "target": str(len(speculative_hits)),
            "detail": ", ".join(speculative_hits[:20]),
        }
    )

    # revise 원 단정 잔여(모델 확인 후 보조)
    for item in verify_results:
        if item.get("decision") != "revise":
            continue
        claim = str(item.get("claim") or "")
        revised = str(item.get("revised_claim") or "")
        if claim and claim in body and revised:
            body = body.replace(claim, revised)
            verification_log.append(
                {"action": "scrub_leftover_claim", "target": claim[:80], "detail": "body"}
            )

    report = {
        "pipeline": PIPELINE,
        "range": params.get("range"),
        "reading": {"body": body[:900], "intent_gap": intent_gap[:400]},
        "reading_before_rewrite": {"body": body_before_rewrite[:900]},
        "role_map": role_map,
        "character_arcs": arcs,
        "motifs": motifs,
        "motif_changes": _compare_motif_status(prev, motifs),
        "arc_changes": [],
        "patterns": patterns,
        "pacing": pacing,
        "revisions": revisions,
        "verify": verify_results,
        "verification_log": verification_log,
        "summary_only_count": summary_only_count,
        "summary_only_note": top_notes[0] if top_notes else "",
        "failed_summary_unit_nos": failed_unit_nos,
        "top_notes": top_notes,
        "speculative_count": len(speculative_hits),
        "blurry_count": sum(1 for item in role_map if item.get("blurry")),
        "unit_records": records,
        "unit_work_prep": params.get("unit_work_prep"),
    }
    if prev and isinstance(prev.get("character_arcs"), list):
        old = {
            str(item.get("name") or ""): str(item.get("change") or "")
            for item in prev.get("character_arcs") or []
            if isinstance(item, dict)
        }
        for item in arcs:
            before = old.get(item["name"])
            if before and before != item["change"]:
                report["arc_changes"].append(
                    {"name": item["name"], "from": before[:120], "to": item["change"][:120]}
                )

    _progress(conn, run_id, params, "done", 5, 5, "완료")
    update_run(
        conn,
        run_id,
        status="ok",
        report_json=report,
        report_md=render_midcheck_md(report),
        params_json=params,
        finished_at=conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')").fetchone()[0],
    )
    if options.get("autocommit"):
        conn.commit()
    return report


__all__ = [
    "PIPELINE",
    "apply_range_and_jargon",
    "apply_verify_revisions",
    "build_unit_record",
    "check_revise_leftovers",
    "classify_motif_status",
    "count_speculative_phrases",
    "discover_motifs_from_records",
    "drop_sentence_from_body",
    "filter_manuscript_revisions",
    "filter_prev_for_range",
    "maybe_create_midcheck_nudge",
    "midcheck_nudge_dedupe_key",
    "midcheck_top_notes",
    "prepare_midcheck_run",
    "reading_section_title",
    "records_for_model",
    "render_midcheck_md",
    "rewrite_body_with_revisions",
    "rewrite_leftover_sentences",
    "rewrite_out_of_range_units",
    "run_literature_midcheck",
    "scrub_speculative",
    "scrub_system_jargon",
    "select_units",
    "soften_unverified_unit_claims",
]
