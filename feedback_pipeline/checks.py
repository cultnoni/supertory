"""카드·리포트 규칙 검사. I/O·API 없음."""

from __future__ import annotations

import re
from typing import Any

from feedback_pipeline.paragraphs import (
    Paragraph,
    adjacent_outside_numbers,
    find_in_paragraph,
    normalize_text,
    to_paragraphs,
    unique_paragraphs_with_quote,
)

# _quote_end_original_index is private in paragraphs; reuse via slice path
from feedback_pipeline import paragraphs as _paras

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。…\n])\s+")
_NAME_HINT = re.compile(
    r"[A-Z][A-Za-z]{1,24}|"
    r"[가-힣]{2,6}(?:양|씨|부인|남작|대공|아가씨)"
)
LEAK_TERMS = ("tracked_facts", "impact", "fixable", "certainty", "range", "JSON")
MAX_RANGE_SPAN = 5


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        text = str(value or "").strip()
        if not text:
            return None
        return int(text)
    except (TypeError, ValueError):
        return None


def _nonempty_suggestion(suggestion: Any) -> str | None:
    if suggestion is None:
        return None
    text = str(suggestion).strip()
    if not text or text.lower() == "null":
        return None
    return text


def _para_by_number(paragraphs: list[Paragraph], number: int) -> Paragraph | None:
    for para in paragraphs:
        if para.number == number:
            return para
    return None


def check_v1_quotes_in_paragraphs(
    *,
    paragraphs: list[Paragraph],
    start_para: int,
    end_para: int,
    start_quote: str,
    end_quote: str,
) -> dict[str, Any]:
    """V1: start/end 인용이 해당 문단에 실제로 있는지."""
    start_q = _as_text(start_quote).strip()
    end_q = _as_text(end_quote).strip()
    start_hit = False
    end_hit = False
    first = _para_by_number(paragraphs, int(start_para or 0))
    last = _para_by_number(paragraphs, int(end_para or 0))
    if first is not None and start_q:
        start_hit = find_in_paragraph(first, start_q) is not None
        if not start_hit:
            for para in paragraphs:
                if min(start_para, end_para) <= para.number <= max(start_para, end_para):
                    if find_in_paragraph(para, start_q) is not None:
                        start_hit = True
                        break
    if last is not None and end_q:
        end_hit = find_in_paragraph(last, end_q) is not None
        if not end_hit:
            for para in paragraphs:
                if min(start_para, end_para) <= para.number <= max(start_para, end_para):
                    if find_in_paragraph(para, end_q) is not None:
                        end_hit = True
                        break
    ok = bool(start_q) and bool(end_q) and start_hit and end_hit
    return {
        "id": "V1",
        "ok": ok,
        "detail": {
            "start_quote_found": start_hit,
            "end_quote_found": end_hit,
            "start_para": start_para,
            "end_para": end_para,
        },
    }


def _project_names(project: dict[str, Any] | None) -> list[str]:
    if not project:
        return []
    names: list[str] = []
    for person in project.get("characters") or []:
        if isinstance(person, dict):
            name = str(person.get("name") or "").strip()
            if name:
                names.append(name)
            for alias in person.get("aliases") or []:
                text = str(alias or "").strip()
                if text:
                    names.append(text)
    for term in project.get("terms") or []:
        text = str(term or "").strip()
        if text:
            names.append(text)
    names.sort(key=len, reverse=True)
    return names


def check_v3_unknown_proper_nouns(
    *,
    suggestion: Any,
    original_text: str,
    project: dict[str, Any] | None,
) -> dict[str, Any]:
    """V3: 수정안에 원문·인물·용어에 없는 고유명이 있으면 실패."""
    text = _nonempty_suggestion(suggestion)
    if text is None:
        return {"id": "V3", "ok": True, "skipped": True, "unknown": []}
    original_n = normalize_text(original_text)
    whitelist = {normalize_text(n) for n in _project_names(project) if n}
    unknown: list[str] = []
    seen: set[str] = set()
    for match in _NAME_HINT.finditer(text):
        token = match.group(0).strip()
        if len(token) < 2:
            continue
        norm = normalize_text(token)
        if norm in seen:
            continue
        seen.add(norm)
        if norm in original_n or token in original_text:
            continue
        if any(norm == w or norm in w or w in norm for w in whitelist if w):
            continue
        unknown.append(token)
    return {"id": "V3", "ok": not unknown, "unknown": unknown}


def _split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_SPLIT.split((text or "").strip())
    out: list[str] = []
    for part in parts:
        item = normalize_text(part).strip()
        if len(item) >= 6:
            out.append(item)
    return out


def _same_paragraph_outside_sentences(
    paragraphs: list[Paragraph],
    start_para: int,
    end_para: int,
    start_quote: str,
    end_quote: str,
) -> list[str]:
    by_n = {p.number: p for p in paragraphs}
    out: list[str] = []
    first = by_n.get(int(start_para or 0))
    last = by_n.get(int(end_para or 0))
    if first is not None and start_quote:
        at = find_in_paragraph(first, start_quote)
        if at:
            out.extend(_split_sentences(first.text[:at]))
    if last is not None and end_quote:
        at = find_in_paragraph(last, end_quote)
        if at is not None:
            end_idx = _paras._quote_end_original_index(last, end_quote, at)
            out.extend(_split_sentences(last.text[end_idx:]))
    return out


def check_v4_outside_sentence_copy(
    *,
    suggestion: Any,
    paragraphs: list[Paragraph],
    start_para: int,
    end_para: int,
    start_quote: str = "",
    end_quote: str = "",
) -> dict[str, Any]:
    """V4: 범위 밖 문장을 수정안에 복사하면 실패."""
    text = _nonempty_suggestion(suggestion)
    if text is None:
        return {"id": "V4", "ok": True, "skipped": True, "copied": []}
    total = paragraphs[-1].number if paragraphs else 0
    neighbors = adjacent_outside_numbers(start_para, end_para, total, window=3)
    neighbor_sentences: list[str] = []
    by_n = {p.number: p for p in paragraphs}
    for n in neighbors:
        para = by_n.get(n)
        if para is None:
            continue
        neighbor_sentences.extend(_split_sentences(para.text))
    outside_same = _same_paragraph_outside_sentences(
        paragraphs, start_para, end_para, start_quote, end_quote
    )
    copied: list[str] = []
    copied_from: list[str] = []
    for sent in _split_sentences(text):
        hit_src = None
        for other in neighbor_sentences:
            if sent == other or (len(sent) >= 8 and sent in other) or (len(other) >= 8 and other in sent):
                hit_src = "neighbor"
                break
        if hit_src is None:
            for other in outside_same:
                if sent == other or (len(sent) >= 8 and sent in other) or (len(other) >= 8 and other in sent):
                    hit_src = "same_paragraph_outside"
                    break
        if hit_src:
            copied.append(sent)
            copied_from.append(hit_src)
    return {
        "id": "V4",
        "ok": not copied,
        "copied": copied,
        "copied_from": copied_from,
        "neighbor_paras": neighbors,
    }


def _char_len(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def check_v6_length(
    *,
    suggestion: Any,
    original_text: str,
    kind: str,
) -> dict[str, Any]:
    """V6: 수정안 길이가 허용 비율 밖이면 경고."""
    sug = _nonempty_suggestion(suggestion)
    if sug is None:
        return {"id": "V6", "ok": True, "skipped": True}
    orig_n = _char_len(original_text)
    sug_n = _char_len(sug)
    if orig_n <= 0:
        return {"id": "V6", "ok": False, "ratio": None, "original_len": orig_n, "suggestion_len": sug_n}
    ratio = sug_n / orig_n
    kind_n = str(kind or "").strip().lower()
    if kind_n == "correction":
        lo, hi = 0.80, 1.20
    elif kind_n == "style":
        lo, hi = 0.40, 1.80
    else:
        lo, hi = 0.30, 1.80
    ok = lo <= ratio <= hi
    return {
        "id": "V6",
        "ok": ok,
        "ratio": round(ratio, 3),
        "lo": lo,
        "hi": hi,
        "original_len": orig_n,
        "suggestion_len": sug_n,
    }


_NATIVE_NUM = {
    "한": 1, "하나": 1, "두": 2, "둘": 2, "세": 3, "셋": 3, "네": 4, "넷": 4,
    "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10,
    "스무": 20, "스물": 20, "서른": 30, "마흔": 40, "쉰": 50, "예순": 60,
    "일흔": 70, "여든": 80, "아흔": 90,
}
_SINO_DIGIT = {
    "영": 0, "공": 0, "일": 1, "이": 2, "삼": 3, "사": 4, "오": 5,
    "육": 6, "륙": 6, "칠": 7, "팔": 8, "구": 9,
}
_SINO_UNIT = {"십": 10, "백": 100, "천": 1000, "만": 10000}
_NUM_UNIT = ("년", "살", "세", "명", "시", "개", "번")
_DIGIT_SPAN = re.compile(r"\d+(?:\s*(?:년|살|세|명|시|개|번))?")
_KOREAN_NUM_SPAN = re.compile(
    r"(?:"
    r"(?:스무|스물|서른|마흔|쉰|예순|일흔|여든|아흔)"
    r"(?:\s*(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉))?"
    r"(?:\s*(?:년|살|세|명))?"
    r"|열(?:\s*(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉))?(?:\s*(?:년|살|세|명))"
    r"|(?:[일이삼사오육륙칠팔구]\s*)?[십백천](?:\s*[일이삼사오육륙칠팔구])?(?:\s*(?:년|살|세|명))"
    r"|(?:십|백|천)\s*년"
    r")"
)


def _korean_to_int(raw: str) -> int | None:
    s = re.sub(r"\s+", "", raw or "")
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s in _NATIVE_NUM:
        return _NATIVE_NUM[s]
    if s in _SINO_DIGIT:
        return _SINO_DIGIT[s]
    total = 0
    rest = s
    for unit_name, unit_val in (("만", 10000), ("천", 1000), ("백", 100), ("십", 10)):
        if unit_name not in rest:
            continue
        left, right = rest.split(unit_name, 1)
        coef = 1
        if left:
            coef = _korean_to_int(left)
            if coef is None:
                return None
        total += coef * unit_val
        rest = right
    if rest:
        extra = _korean_to_int(rest)
        if extra is None:
            return None
        total += extra
    return total if total or s in {"영", "공"} else None


def _normalize_num_token(token: str) -> str:
    t = re.sub(r"\s+", "", token or "")
    unit = ""
    for item in _NUM_UNIT:
        if t.endswith(item):
            unit = item
            t = t[: -len(item)]
            break
    if t.isdigit():
        return str(int(t)) + unit
    value = _korean_to_int(t)
    if value is not None:
        return str(value) + unit
    return t + unit


def _number_tokens(text: str) -> set[str]:
    found: set[str] = set()
    for pattern in (_DIGIT_SPAN, _KOREAN_NUM_SPAN):
        for match in pattern.finditer(text or ""):
            token = match.group(0).strip()
            if token:
                found.add(_normalize_num_token(token))
    return {item for item in found if item}


def check_v8_missing_terms(
    *,
    suggestion: Any,
    original_text: str,
    project: dict[str, Any] | None,
) -> dict[str, Any]:
    """V8 (경고): 원문의 이름·숫자·용어가 수정안에서 빠짐."""
    sug = _nonempty_suggestion(suggestion)
    if sug is None:
        return {"id": "V8", "ok": True, "skipped": True, "warn": False, "missing": []}
    orig = _as_text(original_text)
    orig_n = normalize_text(orig)
    sug_n = normalize_text(sug)
    missing: list[str] = []
    for name in _project_names(project):
        if not name or len(name) < 2:
            continue
        if name not in orig and normalize_text(name) not in orig_n:
            continue
        if name in sug or normalize_text(name) in sug_n:
            continue
        missing.append(name)
    orig_nums = _number_tokens(orig)
    sug_nums = _number_tokens(sug)
    for token in sorted(orig_nums):
        if token not in sug_nums:
            missing.append(token)
    seen: set[str] = set()
    ordered: list[str] = []
    for item in missing:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return {"id": "V8", "ok": True, "warn": bool(ordered), "missing": ordered}


def missing_terms_only_in_deleted_sentences(
    original_text: str,
    suggestion: Any,
    missing: list[str],
) -> bool:
    """수정안이 원문 문장 삭제뿐이고, 빠진 이름이 삭제된 문장에만 있으면 True."""
    sug = _nonempty_suggestion(suggestion)
    names = [str(item).strip() for item in (missing or []) if str(item).strip()]
    if not sug or not names:
        return False
    orig = _as_text(original_text)
    sents = [part.strip() for part in _SENTENCE_SPLIT.split(orig) if part and part.strip()]
    if not sents and orig.strip():
        sents = [orig.strip()]
    sug_n = normalize_text(sug)
    kept: list[str] = []
    deleted: list[str] = []
    for sent in sents:
        needle = normalize_text(sent)
        if needle and needle in sug_n:
            kept.append(sent)
        else:
            deleted.append(sent)
    if not deleted or not kept:
        return False
    kept_compact = re.sub(r"\s+", "", normalize_text("".join(kept)))
    sug_compact = re.sub(r"\s+", "", sug_n)
    if kept_compact != sug_compact:
        return False
    kept_blob = normalize_text(" ".join(kept))
    del_blob = normalize_text(" ".join(deleted))
    for name in names:
        nn = normalize_text(name)
        if nn and nn in kept_blob:
            return False
        if nn and nn not in del_blob and name not in "".join(deleted):
            return False
    return True


def classify_v8_warning(
    *,
    original_text: str,
    suggestion: Any,
    result: dict[str, Any] | None,
) -> dict[str, str] | None:
    if not result or not result.get("warn"):
        return None
    missing = [str(x) for x in (result.get("missing") or []) if str(x).strip()]
    if missing_terms_only_in_deleted_sentences(original_text, suggestion, missing):
        names = ", ".join(missing[:5])
        return {
            "code": "names_removed_by_deletion",
            "severity": "info",
            "message": f"삭제한 문장에 들어 있던 이름이에요: {names}",
        }
    warn = warning_from_check(result)
    if warn:
        warn["severity"] = "warn"
    return warn


_JOSA_SUFFIX = (
    "으로부터", "에게서", "한테서", "에서부터", "으로서", "로써", "으로",
    "부터", "까지", "에게", "한테", "에서", "께서", "이라", "라고", "이며",
    "이나", "이랑", "의", "이", "가", "을", "를", "은", "는", "에", "도",
    "만", "와", "과", "로", "나", "랑", "고", "께",
)
_PRONOUN_GEU = re.compile(
    r"(?<![가-힣])그(?:의|가|를|을|는|은|도|만|와|과|에게|한테|께)(?![가-힣])"
)


def _strip_josa(token: str) -> str:
    text = (token or "").strip()
    if len(text) < 2:
        return text
    for josa in _JOSA_SUFFIX:
        if text.endswith(josa) and len(text) - len(josa) >= 2:
            return text[: -len(josa)]
    return text


def _adjacent_stem_repeats(text: str) -> list[str]:
    hits: list[str] = []
    tokens = [t for t in re.split(r"\s+", (text or "").strip()) if t]
    for left, right in zip(tokens, tokens[1:]):
        stem_l = _strip_josa(left)
        stem_r = _strip_josa(right)
        if len(stem_l) >= 2 and stem_l == stem_r:
            hits.append(f"{left} {right}")
    compact = re.sub(r"\s+", "", text or "")
    for i in range(len(compact)):
        for width in range(2, 7):
            stem = compact[i : i + width]
            if len(stem) < 2 or not re.fullmatch(r"[가-힣]+", stem):
                continue
            rest = compact[i + width :]
            for josa in _JOSA_SUFFIX:
                if not rest.startswith(josa):
                    continue
                after = rest[len(josa) :]
                if after.startswith(stem):
                    trail = after[len(stem) :]
                    shown = stem + josa + stem
                    for j2 in _JOSA_SUFFIX:
                        if trail.startswith(j2):
                            shown = stem + josa + stem + j2
                            break
                    if shown not in hits:
                        hits.append(shown)
                break
    seen: set[str] = set()
    out: list[str] = []
    for item in hits:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def check_v9_repeat_and_pronoun(
    *,
    suggestion: Any,
    original_text: str,
) -> dict[str, Any]:
    """V9 (경고): 어절 반복, 그녀→그 교체."""
    sug = _nonempty_suggestion(suggestion)
    if sug is None:
        return {
            "id": "V9",
            "ok": True,
            "skipped": True,
            "warn": False,
            "repeats": [],
            "pronoun_shift": None,
        }
    repeats = _adjacent_stem_repeats(sug)
    orig = _as_text(original_text)
    orig_she = "그녀" in orig
    sug_she = "그녀" in sug
    orig_he = bool(_PRONOUN_GEU.search(orig))
    sug_he = bool(_PRONOUN_GEU.search(sug))
    shift = None
    if orig_she and (not sug_she) and sug_he and (not orig_he):
        shift = "그녀→그"
    elif orig_he and (not sug_he) and sug_she and (not orig_she):
        shift = "그→그녀"
    return {
        "id": "V9",
        "ok": True,
        "warn": bool(repeats) or bool(shift),
        "repeats": repeats,
        "pronoun_shift": shift,
    }


def check_item_range_quotes(
    item: dict[str, Any],
    paragraphs: list[Paragraph],
) -> dict[str, Any]:
    """R2a: 인용이 지정 문단에 있는지."""
    by_n = {p.number: p for p in paragraphs}
    rng = item.get("range")
    if rng is None:
        return {"ok": True, "null": True, "failures": []}
    if not isinstance(rng, dict):
        return {"ok": False, "null": False, "failures": ["range가 객체가 아님"]}
    failures: list[str] = []
    start = _as_int(rng.get("start_para"))
    end = _as_int(rng.get("end_para"))
    start_quote = _as_text(rng.get("start_quote")).strip()
    end_quote = _as_text(rng.get("end_quote")).strip()
    if start is None or end is None:
        return {"ok": False, "null": False, "failures": ["문단 번호 없음"]}
    first = by_n.get(start)
    last = by_n.get(end)
    if first is None:
        failures.append(f"start_para P{start} 없음")
    if last is None:
        failures.append(f"end_para P{end} 없음")
    if start > end:
        failures.append(f"start_para({start}) > end_para({end})")
    if first is not None and start_quote:
        if find_in_paragraph(first, start_quote) is None:
            failures.append("start_quote가 start_para에 없음")
    elif first is not None and not start_quote:
        failures.append("start_quote 비어 있음")
    if last is not None and end_quote:
        if find_in_paragraph(last, end_quote) is None:
            failures.append("end_quote가 end_para에 없음")
    elif last is not None and not end_quote:
        failures.append("end_quote 비어 있음")
    return {"ok": not failures, "null": False, "failures": failures}


def relocate_item_range(
    item: dict[str, Any],
    paragraphs: list[Paragraph],
) -> dict[str, Any] | None:
    """지정 문단에 인용이 없으면, 다른 문단에서 유일하게 찾아 고친다. 실패 시 None."""
    rng = item.get("range") if isinstance(item.get("range"), dict) else None
    if not rng:
        return None
    start_quote = _as_text(rng.get("start_quote")).strip()
    end_quote = _as_text(rng.get("end_quote")).strip()
    if not start_quote or not end_quote:
        return None
    result = check_item_range_quotes(item, paragraphs)
    if result.get("ok"):
        return dict(rng)
    start_hits = unique_paragraphs_with_quote(paragraphs, start_quote)
    end_hits = unique_paragraphs_with_quote(paragraphs, end_quote)
    start_n = _as_int(rng.get("start_para"))
    end_n = _as_int(rng.get("end_para"))
    if start_n is not None:
        first = _para_by_number(paragraphs, start_n)
        if first is None or find_in_paragraph(first, start_quote) is None:
            if len(start_hits) == 1:
                start_n = start_hits[0].number
            else:
                return None
    if end_n is not None:
        last = _para_by_number(paragraphs, end_n)
        if last is None or find_in_paragraph(last, end_quote) is None:
            if len(end_hits) == 1:
                end_n = end_hits[0].number
            else:
                return None
    if start_n is None or end_n is None:
        return None
    if start_n > end_n:
        start_n, end_n = end_n, start_n
    relocated = {
        "start_para": start_n,
        "end_para": end_n,
        "start_quote": start_quote,
        "end_quote": end_quote,
    }
    probe = {"range": relocated}
    if not check_item_range_quotes(probe, paragraphs).get("ok"):
        return None
    return relocated


def _count_term(text: str, term: str) -> int:
    if not text or not term:
        return 0
    if term.lower() == "json":
        count = 0
        start = 0
        low = text.lower()
        nlow = term.lower()
        while True:
            pos = low.find(nlow, start)
            if pos < 0:
                break
            count += 1
            start = pos + len(nlow)
        return count
    count = 0
    start = 0
    while True:
        pos = text.find(term, start)
        if pos < 0:
            break
        count += 1
        start = pos + len(term)
    return count


def check_leak_terms(blobs: list[str] | str) -> dict[str, Any]:
    """리포트 본문에 내부 용어가 새어 나왔는지."""
    if isinstance(blobs, str):
        joined = blobs
    else:
        joined = "\n".join(_as_text(x) for x in blobs)
    by_term: dict[str, int] = {}
    total = 0
    for term in LEAK_TERMS:
        n = _count_term(joined, term)
        by_term[term] = n
        total += n
    return {"leak_count": total, "leaks": by_term}


def impact_stage(impact: int) -> str:
    if impact >= 4:
        return "high"
    if impact == 3:
        return "medium"
    return "low"


def apply_card_priorities(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """impact로 단계를 매긴 뒤 바닥 보장. 전체가 3개 이하면 모두 high."""
    tagged = [dict(card) for card in cards]
    n = len(tagged)
    if n == 0:
        return tagged
    if n <= 3:
        for card in tagged:
            card["priority"] = "high"
        return tagged

    def _impact(card: dict[str, Any]) -> int:
        value = _as_int(card.get("impact"))
        return value if value is not None else 0

    def _start(card: dict[str, Any]) -> int:
        value = _as_int(card.get("start_para"))
        return value if value is not None else 10**9

    for card in tagged:
        card["priority"] = impact_stage(_impact(card))
    highs = [c for c in tagged if c.get("priority") == "high"]
    if len(highs) < 3:
        promotable = [
            c
            for c in tagged
            if c.get("priority") != "high" and _impact(c) >= 3
        ]
        promotable.sort(key=lambda c: (-_impact(c), _start(c)))
        need = 3 - len(highs)
        for card in promotable[:need]:
            card["priority"] = "high"
    highs = [c for c in tagged if c.get("priority") == "high"]
    if len(highs) < 3:
        ranked = sorted(tagged, key=lambda c: (-_impact(c), _start(c)))
        for card in ranked[:3]:
            card["priority"] = "ref"
    return tagged


def warning_from_check(result: dict[str, Any]) -> dict[str, str] | None:
    code = str(result.get("id") or "")
    if code == "V6" and not result.get("ok") and not result.get("skipped"):
        orig_n = int(result.get("original_len") or 0)
        sug_n = int(result.get("suggestion_len") or 0)
        if orig_n and sug_n < orig_n:
            return {"code": "V6", "message": "수정안이 원문보다 크게 짧아졌어요"}
        if orig_n and sug_n > orig_n:
            return {"code": "V6", "message": "수정안이 원문보다 많이 길어졌어요"}
        return {"code": "V6", "message": "수정안 길이가 원문과 너무 달라요"}
    if code == "V8" and result.get("warn"):
        missing = ", ".join(str(x) for x in (result.get("missing") or [])[:5])
        return {
            "code": "V8",
            "message": f"수정안에서 이름이나 용어가 빠졌어요: {missing}",
        }
    if code == "V9" and result.get("warn"):
        parts = []
        if result.get("repeats"):
            parts.append("같은 말이 이어져 반복됐어요: " + ", ".join(result["repeats"][:3]))
        if result.get("pronoun_shift"):
            parts.append("인칭이 바뀌었어요(" + str(result["pronoun_shift"]) + ")")
        return {
            "code": "V9",
            "message": "; ".join(parts) or "반복이나 인칭을 확인해 주세요",
        }
    return None
