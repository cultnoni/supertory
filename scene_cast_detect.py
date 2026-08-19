"""Detect registered characters that appear vs are only mentioned in a scene.

New (unregistered) names are not created here. Those candidates come from a
separate Gemini call — see build_new_name_prompt / parse_new_name_candidates.
"""

from __future__ import annotations

import json
import re

APPEARS = "appears"
MENTIONED = "mentioned"

MAX_CANDIDATE_CHARS = 12_000
MAX_CANDIDATES = 12

_PARTICLE = (
    r"(?:은|는|이|가|을|를|의|도|만|과|와|랑|야|아|여|께서|에게|한테|께|"
    r"으로|로|부터|까지|이었|였|이다|이야|이라고|이며|이고|이라며|이라)"
)
_AFTER_OK = re.compile(rf"(?:{_PARTICLE}|[\s.,!?…~“”\"'』」\)\]：:]|$)")
_HANGUL = re.compile(r"[가-힣]")

_PERSON_VERB_SRC = (
    r"(?:말했|말했어|말한다|말하며|물었|물었어|되물|대답|답했|중얼|외쳤|소리쳤|"
    r"웃었|미소|고개를|앉아|앉았|일어|걸어|걸었|달려|달렸|들어왔|들어와|나갔|나타났|"
    r"자리에|손을 |문을 |검을 |칼을 |눈을 |바라보|쳐다|돌아섰|다가왔|다가갔|"
    r"내밀|잡았|열었|닫았|끄덕|한숨|침묵|주먹을|뛰었|피했|막았|던졌|"
    r"보았|봤다|보고|들었|듣고|불렀|찾았|기다려|기다렸|울었|울며|나섰|향했|멈췄|"
    r"돌아보|속삭|입을 |말을 )"
)
_APPEAR_AFTER = re.compile(rf"^[은는이가도만]?\s*{_PERSON_VERB_SRC}")
_APPEAR_BEFORE = re.compile(r'[”"』」]\s*$')
_MENTION_HINTS = (
    "떠올",
    "생각났",
    "생각이 났",
    "생각이 들",
    "기억",
    "소문",
    "얘기",
    "이야기",
    "말하길",
    "전해",
    "들었대",
    "들었다더",
    "소식",
    "언급",
    "이름만",
    "이름이 나",
    "에 대해",
    "에 대한",
    "그리워",
    "보고 싶",
    "행방",
    "자취",
    "어디지",
    "어디에",
    "누구냐",
    "누구였",
)


def character_labels(character: dict) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    raw = [character.get("name") or ""]
    aliases = character.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    for item in aliases:
        if isinstance(item, dict):
            raw.append(item.get("alias") or item.get("name") or "")
        else:
            raw.append(item)
    for item in raw:
        text = str(item or "").strip()
        if len(text) < 1:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(text)
    names.sort(key=len, reverse=True)
    return names


def _before_ok(text: str, start: int) -> bool:
    if start <= 0:
        return True
    prev = text[start - 1]
    if _HANGUL.match(prev):
        return False
    return True


def _after_ok(text: str, end: int) -> bool:
    return bool(_AFTER_OK.match(text[end:] or ""))


def find_name_spans(text: str, name: str) -> list[tuple[int, int]]:
    if not text or len(name) < 1:
        return []
    spans: list[tuple[int, int]] = []
    start = 0
    needle = name
    while True:
        index = text.find(needle, start)
        if index < 0:
            break
        end = index + len(needle)
        if _before_ok(text, index) and _after_ok(text, end):
            spans.append((index, end))
        start = index + 1
    return spans


def _sentence_span(text: str, start: int, end: int) -> str:
    left = start
    for index in range(start - 1, -1, -1):
        if text[index] in ".\n!?。…":
            left = index + 1
            break
        if start - index > 90:
            left = index
            break
    else:
        left = 0
    right = min(len(text), end + 90)
    for index in range(end, min(len(text), end + 90)):
        if text[index] in ".\n!?。…":
            right = index
            break
    return text[left:right]


def classify_hit(text: str, start: int, end: int, *, name: str = "") -> str:
    """Classify a registered-name hit as appearing on-stage vs name-only mention."""
    del name  # matching already used the registered label
    left = text[max(0, start - 28):start]
    right = text[end:min(len(text), end + 36)]
    sentence = _sentence_span(text, start, end)
    if _APPEAR_BEFORE.search(left) and _APPEAR_AFTER.match(right):
        return APPEARS
    if _APPEAR_AFTER.match(right):
        return APPEARS
    if any(hint in sentence for hint in _MENTION_HINTS):
        return MENTIONED
    if re.match(r"[은는이가도만을를에게한테]", right or ""):
        return APPEARS
    return MENTIONED


def detect_known_cast(text: str, characters: list[dict]) -> dict[int, str]:
    """Map character_id → appears|mentioned. Only registered names/aliases."""
    found: dict[int, str] = {}
    occupied = [False] * (len(text) + 1)
    labeled: list[tuple[int, str, list[tuple[int, int, str]]]] = []
    for character in characters:
        try:
            cid = int(character.get("id"))
        except (TypeError, ValueError):
            continue
        spans: list[tuple[int, int, str]] = []
        for label in character_labels(character):
            for start, end in find_name_spans(text, label):
                spans.append((start, end, label))
        if spans:
            labeled.append((cid, "", spans))
    labeled.sort(
        key=lambda item: max((end - start for start, end, _name in item[2]), default=0),
        reverse=True,
    )
    for cid, _unused, spans in labeled:
        kinds: set[str] = set()
        for start, end, label in spans:
            if any(occupied[index] for index in range(start, end)):
                continue
            for index in range(start, end):
                occupied[index] = True
            kinds.add(classify_hit(text, start, end, name=label))
        if APPEARS in kinds:
            found[cid] = APPEARS
        elif MENTIONED in kinds:
            found[cid] = MENTIONED
    return found


def build_new_name_prompt(text: str, known_names: list[str]) -> tuple[str, str]:
    body = str(text or "").strip()
    if len(body) > MAX_CANDIDATE_CHARS:
        body = body[:MAX_CANDIDATE_CHARS] + "\n…(이하 생략)"
    known = "、".join(
        str(item).strip() for item in known_names if str(item or "").strip()
    ) or "(없음)"
    system = (
        "당신은 한국어 소설의 등장인물 이름을 가려내는 도우미입니다. "
        "출력은 JSON만 합니다."
    )
    user = (
        "[작업]\n"
        "이 회차 본문에서 실제 인물처럼 등장한 이름만 뽑아라. "
        "이미 있는 이름은 제외. "
        "일반명사·추상명사·동사 활용형은 절대 포함하지 마라.\n"
        "장면에 나와 말하거나 행동하는 고유 이름만 고른다. "
        "소문·회상·편지처럼 이름만 스친 경우는 빼라.\n"
        "군중·무명 엑스트라·대명사(그/그녀)도 빼라.\n"
        f"최대 {MAX_CANDIDATES}개.\n\n"
        "[이미 있는 인물 이름]\n"
        f"{known}\n\n"
        "[출력 JSON]\n"
        "{\n"
        '  "names": ["이름"]\n'
        "}\n"
        "해당 이름이 없으면 {\"names\": []} 만 출력한다. JSON 외 텍스트는 출력하지 마세요.\n\n"
        "[원고]\n"
        f"{body}"
    )
    return system, user


def parse_new_name_candidates(raw: object, known_names: list[str], *, limit: int = MAX_CANDIDATES) -> list[str]:
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
        rows = data.get("names") or data.get("characters") or data.get("people") or []
    if not isinstance(rows, list):
        return []
    known = {str(item or "").strip().casefold() for item in known_names if str(item or "").strip()}
    found: list[str] = []
    seen: set[str] = set()
    for item in rows:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
        else:
            name = str(item or "").strip()
        if len(name) < 1 or len(name) > 40:
            continue
        key = name.casefold()
        if key in seen or key in known:
            continue
        seen.add(key)
        found.append(name)
        if len(found) >= limit:
            break
    return found
