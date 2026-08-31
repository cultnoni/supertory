"""Infer character-to-character relations from the 관계 field text.

Same judgment bar as 인물 특징 자동채움: only explicit statements count.
Guesswork, atmosphere, and unnamed counterparts are skipped.
"""

from __future__ import annotations

import json
import re

import character_import_analysis

MAX_FIELD_CHARS = 2000
MAX_LABEL_CHARS = 40
MIN_NAME_CHARS = 2

_HEADING_SPLIT = re.compile(r"(?=^\[[^\]]+\]\s*$)", re.M)
_HEADING_LINE = re.compile(r"^\[([^\]]+)\]\s*")
_SPECULATIVE = re.compile(
    r"것 같|듯하|듯싶|인가 싶|아마 |추정|짐작|인 듯|일지도|"
    r"가능성|느껴진다|보이기도|보인다|보여요|아닐까|싶은|한 듯|"
    r"사이가 나빠진 것|가까운 사이인 것"
)
_VAGUE_LABELS = frozenset({
    "관계", "관련", "연관", "사이", "관계있음", "관련있음", "연결", "인연",
})
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。\n])\s+")


def extract_relations_text(profile_md: object) -> str:
    """Prefer the [관계] section; otherwise the whole profile (unstructured sheets)."""
    text = str(profile_md or "").replace("\r\n", "\n").strip()
    prefix = character_import_analysis.TORI_TEXT_PREFIX
    if text.startswith(prefix):
        text = text[len(prefix):].strip()
    if not text:
        return ""
    if "[관계]" not in text:
        return text[:MAX_FIELD_CHARS]
    for part in _HEADING_SPLIT.split(text):
        stripped = part.strip()
        match = _HEADING_LINE.match(stripped)
        if not match or match.group(1).strip() != "관계":
            continue
        body = _HEADING_LINE.sub("", stripped, count=1).strip()
        return body[:MAX_FIELD_CHARS]
    return ""


def ordered_pair(left: int, right: int) -> tuple[int, int]:
    a = int(left)
    b = int(right)
    if a == b:
        raise ValueError("같은 인물끼리는 관계를 이을 수 없습니다.")
    return (a, b) if a < b else (b, a)


def _name_keys(character: dict) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for raw in [character.get("name"), *(character.get("aliases") or [])]:
        key = character_import_analysis.normalise_name(raw)
        if len(key) < MIN_NAME_CHARS:
            continue
        folded = key.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        names.append(key)
    names.sort(key=len, reverse=True)
    return names


def name_mentioned(text: str, character: dict) -> bool:
    haystack = character_import_analysis.normalise_name(text)
    if not haystack:
        return False
    folded = haystack.casefold()
    for name in _name_keys(character):
        if name.casefold() in folded:
            return True
    return False


def pair_is_explicit(left: dict, right: dict) -> bool:
    """True only when the other registered name appears in a 관계 text."""
    left_text = str(left.get("relations_text") or "")
    right_text = str(right.get("relations_text") or "")
    return (
        name_mentioned(left_text, right)
        or name_mentioned(right_text, left)
    )


def is_speculative(text: object) -> bool:
    blob = str(text or "").strip()
    if not blob:
        return False
    return bool(_SPECULATIVE.search(blob))


def supporting_snippet(left: dict, right: dict) -> str:
    chunks: list[str] = []
    left_text = str(left.get("relations_text") or "")
    right_text = str(right.get("relations_text") or "")
    if name_mentioned(left_text, right):
        chunks.append(left_text)
    if name_mentioned(right_text, left):
        chunks.append(right_text)
    blob = "\n".join(chunks)
    if not blob:
        return ""
    names = _name_keys(left) + _name_keys(right)
    for sentence in _SENTENCE_SPLIT.split(blob):
        piece = sentence.strip()
        if piece and any(name.casefold() in character_import_analysis.normalise_name(piece).casefold() for name in names):
            return piece[:280]
    return blob[:280]


def clean_label(value: object) -> str:
    label = re.sub(r"\s+", " ", str(value or "")).strip()
    label = re.sub(r"[.。]+$", "", label).strip()
    label = re.sub(r"^(추정|아마)\s*", "", label).strip()
    label = re.sub(r"\s*(추정|같음)$", "", label).strip()
    if len(label) > MAX_LABEL_CHARS:
        label = label[:MAX_LABEL_CHARS].rstrip()
    if not label or label.casefold() in _VAGUE_LABELS:
        return ""
    if is_speculative(label):
        return ""
    return label


def parse_relations_json(raw: object, roster: list[dict]) -> list[dict]:
    cleaned = str(raw or "").strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.S)
    if fence:
        cleaned = fence.group(1).strip()
    data: object
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
    rows: object = data
    if isinstance(data, dict):
        rows = data.get("relations") or data.get("items") or []
    if not isinstance(rows, list):
        return []
    by_id = {int(item["id"]): item for item in roster if item.get("id") is not None}
    by_name: dict[str, dict] = {}
    for item in roster:
        for key in _name_keys(item):
            by_name.setdefault(key.casefold(), item)
    parsed: list[dict] = []
    seen_keys: set[tuple[int, int, str]] = set()
    for item in rows:
        if not isinstance(item, dict):
            continue
        left = _resolve_character(item, "a", by_id, by_name)
        right = _resolve_character(item, "b", by_id, by_name)
        if left is None or right is None:
            continue
        try:
            pair = ordered_pair(int(left["id"]), int(right["id"]))
        except ValueError:
            continue
        label = clean_label(item.get("label") or item.get("relation") or item.get("type"))
        if not label:
            continue
        key = (pair[0], pair[1], label.casefold())
        if key in seen_keys:
            continue
        evidence = str(item.get("evidence") or item.get("quote") or "").strip()[:280]
        seen_keys.add(key)
        parsed.append({
            "character_a_id": pair[0],
            "character_b_id": pair[1],
            "label": label,
            "evidence": evidence,
        })
    return parsed


def _resolve_character(
    item: dict,
    side: str,
    by_id: dict[int, dict],
    by_name: dict[str, dict],
) -> dict | None:
    for key in (f"{side}_id", f"character_{side}_id", f"{side}Id"):
        try:
            cid = int(item.get(key))
        except (TypeError, ValueError):
            cid = 0
        if cid in by_id:
            return by_id[cid]
    for key in (f"{side}_name", f"character_{side}_name", side, f"{side}Name"):
        name = character_import_analysis.normalise_name(item.get(key)).casefold()
        if name and name in by_name:
            return by_name[name]
    return None


def relation_key(left: int, right: int, label: object) -> tuple[int, int, str]:
    pair = ordered_pair(left, right)
    return (pair[0], pair[1], str(label or "").strip().casefold())


def filter_suggestions(
    parsed: list[dict],
    roster: list[dict],
    existing_keys: set[tuple[int, int, str]],
) -> list[dict]:
    by_id = {int(item["id"]): item for item in roster}
    kept: list[dict] = []
    seen: set[tuple[int, int, str]] = set()
    for item in parsed:
        try:
            pair = ordered_pair(item["character_a_id"], item["character_b_id"])
        except (TypeError, ValueError):
            continue
        label = clean_label(item.get("label"))
        if not label:
            continue
        key = relation_key(pair[0], pair[1], label)
        if key in existing_keys or key in seen:
            continue
        left = by_id.get(pair[0])
        right = by_id.get(pair[1])
        if left is None or right is None:
            continue
        if not pair_is_explicit(left, right):
            continue
        snippet = str(item.get("evidence") or "").strip() or supporting_snippet(left, right)
        if is_speculative(snippet):
            continue
        if snippet and not (
            name_mentioned(snippet, left) or name_mentioned(snippet, right)
        ):
            # Evidence must itself mention at least one of the pair.
            snippet = supporting_snippet(left, right)
            if is_speculative(snippet):
                continue
        seen.add(key)
        kept.append({
            "character_a_id": pair[0],
            "character_b_id": pair[1],
            "label": label,
            "evidence": snippet[:280],
        })
    return kept


def build_suggest_prompt(roster: list[dict]) -> tuple[str, str]:
    lines: list[str] = []
    for item in roster:
        aliases = ", ".join(
            str(alias) for alias in (item.get("aliases") or []) if str(alias).strip()
        )
        relations = str(item.get("relations_text") or "").strip() or "(비어 있음)"
        lines.append(
            f"- id={item['id']} 이름={item.get('name') or ''}"
            + (f" 별칭={aliases}" if aliases else "")
            + f"\n  관계 필드:\n{relations[:MAX_FIELD_CHARS]}"
        )
    roster_blob = "\n".join(lines) if lines else "(없음)"
    system = (
        "당신은 한국어 소설 설정집 도우미 토리입니다. "
        "관계 필드에 이름이 명시적으로 적힌 경우만 관계를 추출합니다. "
        "추론·정황·분위기만으로 짐작하지 마세요. "
        "확신이 없으면 빼세요. 출력은 JSON만 합니다."
    )
    user = (
        "[작업]\n"
        "아래는 이미 등록된 등장인물과, 각 인물 시트의 「관계」 필드 텍스트입니다. "
        "이 텍스트에서 다른 등록 인물과의 관계를 추출하세요.\n"
        "목록에 없는 이름은 만들지 마세요.\n\n"
        "[판정 기준 — 반드시 지키세요]\n"
        "1. 상대 인물의 이름(또는 등록된 별칭)이 관계 필드에 그대로 적힌 경우만 인정합니다. "
        "「엔케의 연인」「비비의 주인」처럼 이름이 명시된 서술만 관계입니다.\n"
        "2. 추론·정황·분위기만으로 짐작하지 마세요. "
        "「사이가 나빠진 것 같았다」「가까운 사이인 것 같다」처럼 추측이면 빼세요.\n"
        "3. 「누군가」「그」「그녀」「사람들」처럼 이름이 없는 상대는 빼세요. "
        "목록에 없는 이름도 빼세요.\n"
        "4. 외모·성격 비교(「엔케처럼 키가 크다」)는 관계가 아닙니다. 빼세요.\n"
        "5. 한 쌍(두 인물) 사이에 서로 다른 관계가 명시되어 있으면 "
        "각각 별도 항목으로 넣으세요. 하나만 고르지 마세요. "
        "예: 「엔케의 연인, 비비의 주인」→ label 연인 하나, label 주인 하나. "
        "같은 종류의 관계는 한 번만 넣으세요.\n"
        "6. evidence에는 그 라벨의 근거가 된 관계 필드 인용만 넣으세요. 지어내지 마세요.\n"
        "7. 확신이 서지 않으면 배열에 넣지 마세요. 억지로 채우지 마세요.\n\n"
        "[등록된 인물과 관계 필드]\n"
        f"{roster_blob}\n\n"
        "[출력 JSON]\n"
        "{\n"
        '  "relations": [\n'
        "    {\n"
        '      "a_id": 숫자,\n'
        '      "b_id": 숫자,\n'
        '      "label": "연인",\n'
        '      "evidence": "관계 필드의 짧은 인용"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "관계가 없으면 빈 배열을 반환하세요. JSON 외 텍스트는 출력하지 마세요.\n"
    )
    return system, user
