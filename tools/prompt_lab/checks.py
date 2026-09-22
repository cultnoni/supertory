"""Rule checks for sentence-edit cards. Pure functions — no I/O, no Gemini.

Intended to move into the SuperTory server later. V-numbers match the lab spec.
"""

from __future__ import annotations

import re
from typing import Any

from manuscript import (
    Paragraph,
    adjacent_outside_numbers,
    find_in_paragraph,
    normalize_text,
    _quote_end_original_index,
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。…\n])\s+")
_QUOTE_SPAN = re.compile(
    r"[‘'「\"“]([^‘'」\"”]{4,})[’'」\"”]"
)
_NAME_HINT = re.compile(
    r"[A-Z][A-Za-z]{1,24}|"
    r"[가-힣]{2,6}(?:양|씨|부인|남작|대공|아가씨)"
)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


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
    """V1: model (or case) start_quote/end_quote actually appear in those paragraphs."""
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
    """V3: suggestion has a proper noun in neither original span, characters, nor terms."""
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
    return {
        "id": "V3",
        "ok": not unknown,
        "unknown": unknown,
    }


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
            end_idx = _quote_end_original_index(last, end_quote, at)
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
    """V4: suggestion copies a sentence from neighbor paragraphs or same-paragraph outside range."""
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


def check_v5_reason_quote_replayed(
    *,
    reason: Any,
    suggestion: Any,
    original_text: str = "",
    kind: str = "",
) -> dict[str, Any]:
    """V5 (style only): a 4+ char reason quote that is in original_text also appears in suggestion."""
    kind_n = str(kind or "").strip().lower()
    if kind_n != "style":
        return {
            "id": "V5",
            "ok": True,
            "skipped": True,
            "skip_reason": f"kind={kind_n or '(empty)'} (style만 검사)",
            "replayed": [],
        }
    sug = _nonempty_suggestion(suggestion)
    if sug is None:
        return {"id": "V5", "ok": True, "skipped": True, "replayed": []}
    reason_text = _as_text(reason)
    orig = _as_text(original_text)
    orig_n = normalize_text(orig)
    sug_n = normalize_text(sug)
    replayed: list[str] = []
    considered: list[str] = []
    for match in _QUOTE_SPAN.finditer(reason_text):
        inner = match.group(1).strip()
        if len(inner) < 4:
            continue
        inner_n = normalize_text(inner)
        in_original = bool(inner) and (inner in orig or (inner_n and inner_n in orig_n))
        if not in_original:
            continue
        considered.append(inner)
        if inner in sug or (inner_n and inner_n in sug_n):
            replayed.append(inner)
    return {
        "id": "V5",
        "ok": not replayed,
        "replayed": replayed,
        "considered": considered,
    }


def _char_len(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def check_v6_length(
    *,
    suggestion: Any,
    original_text: str,
    kind: str,
) -> dict[str, Any]:
    """V6: suggestion length outside bounds (style 40–180%, correction 80–120%)."""
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
    "한": 1,
    "하나": 1,
    "두": 2,
    "둘": 2,
    "세": 3,
    "셋": 3,
    "네": 4,
    "넷": 4,
    "다섯": 5,
    "여섯": 6,
    "일곱": 7,
    "여덟": 8,
    "아홉": 9,
    "열": 10,
    "스무": 20,
    "스물": 20,
    "서른": 30,
    "마흔": 40,
    "쉰": 50,
    "예순": 60,
    "일흔": 70,
    "여든": 80,
    "아흔": 90,
}
_SINO_DIGIT = {
    "영": 0,
    "공": 0,
    "일": 1,
    "이": 2,
    "삼": 3,
    "사": 4,
    "오": 5,
    "육": 6,
    "륙": 6,
    "칠": 7,
    "팔": 8,
    "구": 9,
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
            if not token:
                continue
            found.add(_normalize_num_token(token))
    return {item for item in found if item}


def check_v8_missing_terms(
    *,
    suggestion: Any,
    original_text: str,
    project: dict[str, Any] | None,
) -> dict[str, Any]:
    """V8 (warn): names, terms, or numbers from original_text missing in suggestion."""
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
    # unique, keep order
    seen: set[str] = set()
    ordered: list[str] = []
    for item in missing:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return {
        "id": "V8",
        "ok": True,
        "warn": bool(ordered),
        "missing": ordered,
    }


_JOSA_SUFFIX = (
    "으로부터",
    "에게서",
    "한테서",
    "에서부터",
    "으로서",
    "로써",
    "으로",
    "부터",
    "까지",
    "에게",
    "한테",
    "에서",
    "께서",
    "이라",
    "라고",
    "이며",
    "이나",
    "이랑",
    "의",
    "이",
    "가",
    "을",
    "를",
    "은",
    "는",
    "에",
    "도",
    "만",
    "와",
    "과",
    "로",
    "나",
    "랑",
    "고",
    "께",
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
    # unique
    seen: set[str] = set()
    out: list[str] = []
    for item in hits:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _has_geunyeo(text: str) -> bool:
    return "그녀" in (text or "")


def _has_pronoun_geu(text: str) -> bool:
    return bool(_PRONOUN_GEU.search(text or ""))


def check_v9_repeat_and_pronoun(
    *,
    suggestion: Any,
    original_text: str,
) -> dict[str, Any]:
    """V9 (warn): adjacent repeated stems, or 그/그녀 pronoun swap."""
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
    orig_she = _has_geunyeo(orig)
    sug_she = _has_geunyeo(sug)
    orig_he = _has_pronoun_geu(orig)
    sug_he = _has_pronoun_geu(sug)
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


def run_rule_checks(
    *,
    paragraphs: list[Paragraph],
    start_para: int,
    end_para: int,
    original_text: str,
    card: dict[str, Any],
    project: dict[str, Any] | None,
    kind: str,
    case_start_quote: str,
    case_end_quote: str,
    skip_v1: bool = False,
) -> list[dict[str, Any]]:
    """Run V1 (optional), V3–V6, V8–V9. original_text is sliced from the manuscript, not the model."""
    suggestion = card.get("suggestion") if isinstance(card, dict) else None
    reason = card.get("reason") if isinstance(card, dict) else None
    rows: list[dict[str, Any]] = []
    if not skip_v1:
        model_start_q = card.get("start_quote") if isinstance(card, dict) else None
        model_end_q = card.get("end_quote") if isinstance(card, dict) else None
        model_start_p = card.get("start_para") if isinstance(card, dict) else None
        model_end_p = card.get("end_para") if isinstance(card, dict) else None
        try:
            v1_start_p = int(model_start_p) if model_start_p not in (None, "") else int(start_para)
        except (TypeError, ValueError):
            v1_start_p = int(start_para)
        try:
            v1_end_p = int(model_end_p) if model_end_p not in (None, "") else int(end_para)
        except (TypeError, ValueError):
            v1_end_p = int(end_para)
        v1_start_q = _as_text(model_start_q) or case_start_quote
        v1_end_q = _as_text(model_end_q) or case_end_quote
        rows.append(
            check_v1_quotes_in_paragraphs(
                paragraphs=paragraphs,
                start_para=v1_start_p,
                end_para=v1_end_p,
                start_quote=v1_start_q,
                end_quote=v1_end_q,
            )
        )
    rows.extend(
        [
            check_v3_unknown_proper_nouns(
                suggestion=suggestion,
                original_text=original_text,
                project=project,
            ),
            check_v4_outside_sentence_copy(
                suggestion=suggestion,
                paragraphs=paragraphs,
                start_para=int(start_para),
                end_para=int(end_para),
                start_quote=case_start_quote,
                end_quote=case_end_quote,
            ),
            check_v5_reason_quote_replayed(
                reason=reason,
                suggestion=suggestion,
                original_text=original_text,
                kind=kind,
            ),
            check_v6_length(
                suggestion=suggestion,
                original_text=original_text,
                kind=kind,
            ),
            check_v8_missing_terms(
                suggestion=suggestion,
                original_text=original_text,
                project=project,
            ),
            check_v9_repeat_and_pronoun(
                suggestion=suggestion,
                original_text=original_text,
            ),
        ]
    )
    return rows
