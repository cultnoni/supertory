"""Detect characters that appear vs are only mentioned in a scene."""

from __future__ import annotations

import re

APPEARS = "appears"
MENTIONED = "mentioned"

_PARTICLE = (
    r"(?:은|는|이|가|을|를|의|도|만|과|와|랑|야|아|여|께서|에게|한테|께|"
    r"으로|로|부터|까지|이었|였|이다|이야|이라고|이며|이고|이라며|이라)"
)
_AFTER_OK = re.compile(rf"(?:{_PARTICLE}|[\s.,!?…~“”\"'』」\)\]：:]|$)")
_HANGUL = re.compile(r"[가-힣]")

_APPEAR_AFTER = re.compile(
    r"^[은는이가도만]?\s*"
    r"(?:말했|말했어|말한다|말하며|물었|물었어|되물|대답|답했|중얼|외쳤|소리쳤|"
    r"웃었|미소|고개를|앉아|앉았|일어|걸어|달려|들어왔|들어와|나갔|나타났|"
    r"자리에|손을 |문을 |검을 |칼을 |눈을 |바라보|쳐다|돌아섰|다가왔|다가갔|"
    r"내밀|잡았|열었|닫았|끄덕|한숨|침묵|주먹을|뛰었|피했|막았|던졌)"
)
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
_NEW_NAME = re.compile(rf"(?<![가-힣])([가-힣]{{1,4}})(?={_PARTICLE})")
_STOPWORDS = {
    "사람", "남자", "여자", "아이", "녀석", "놈", "애", "왕", "공주", "황제",
    "대신", "장군", "기사", "마법사", "선생", "선생님", "아저씨", "아줌마",
    "언니", "오빠", "형", "누나", "동생", "엄마", "아빠", "아버지", "어머니",
    "할머니", "할아버지", "손님", "주인", "점원", "병사", "모두", "지금",
    "오늘", "내일", "그때", "여기", "저기", "거기", "이번", "다음", "처음",
    "마지막", "정말", "그냥", "조금", "다시", "아직", "이미", "갑자기",
    "결국", "그래서", "그러나", "하지만", "그리고", "또는", "아니", "무슨",
    "어떤", "그것", "이것", "저것", "우리", "너희", "자기", "자신", "마음",
    "얼굴", "눈빛", "목소리", "손길", "기운", "침묵", "공기", "하늘", "바람",
    "불빛", "세상", "미래", "과거", "운명", "사랑", "죽음", "생명", "전쟁",
    "평화", "제국", "왕국", "도시", "마을", "거리", "골목", "방", "문", "창",
    "검", "칼", "마법", "시간", "순간", "사실", "정도", "만큼", "때문",
    "이상", "이하", "동안", "사이", "토리", "그들", "그녀", "그이", "당신",
    "제가", "나는", "너는", "저는", "내가", "네가", "누가", "무엇",
    "어디", "언제", "왜", "어떻게", "그래", "네", "예", "응", "아니야",
    # 조사·대명사 한 글자 — 외자 이름 허용 시 오탐 방지
    "이", "가", "은", "는", "을", "를", "의", "도", "만", "과", "와",
    "저", "나", "너", "그", "내", "제", "뭐", "곧", "참", "좀", "더",
    "잘", "또", "다", "못", "안", "꼭", "막", "딱",
}


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


def classify_hit(text: str, start: int, end: int) -> str:
    """Classify a name hit as appearing on-stage vs name-only mention."""
    left = text[max(0, start - 28):start]
    right = text[end:min(len(text), end + 36)]
    sentence = _sentence_span(text, start, end)
    if _APPEAR_BEFORE.search(left) and _APPEAR_AFTER.match(right):
        return APPEARS
    if _APPEAR_AFTER.match(right):
        return APPEARS
    mention = sum(1 for hint in _MENTION_HINTS if hint in sentence)
    if mention:
        return MENTIONED
    if re.match(r"[은는이가도만을를에게한테]", right or ""):
        return APPEARS
    return MENTIONED


def detect_known_cast(text: str, characters: list[dict]) -> dict[int, str]:
    """Map character_id → appears|mentioned. Appearing wins if both occur."""
    found: dict[int, str] = {}
    occupied = [False] * (len(text) + 1)
    labeled: list[tuple[int, str, list[tuple[int, int]]]] = []
    for character in characters:
        try:
            cid = int(character.get("id"))
        except (TypeError, ValueError):
            continue
        spans: list[tuple[int, int]] = []
        for label in character_labels(character):
            spans.extend(find_name_spans(text, label))
        if spans:
            labeled.append((cid, "", spans))
    labeled.sort(key=lambda item: max((end - start for start, end in item[2]), default=0), reverse=True)
    for cid, _unused, spans in labeled:
        kinds: set[str] = set()
        for start, end in spans:
            if any(occupied[index] for index in range(start, end)):
                continue
            for index in range(start, end):
                occupied[index] = True
            kinds.add(classify_hit(text, start, end))
        if APPEARS in kinds:
            found[cid] = APPEARS
        elif MENTIONED in kinds:
            found[cid] = MENTIONED
    return found


def extract_new_appearing_names(text: str, known_labels: list[str], *, limit: int = 12) -> list[str]:
    known = {str(item or "").strip().casefold() for item in known_labels if str(item or "").strip()}
    found: list[str] = []
    seen: set[str] = set()
    for match in _NEW_NAME.finditer(text or ""):
        name = match.group(1)
        key = name.casefold()
        if key in seen or key in known or name in _STOPWORDS:
            continue
        kind = classify_hit(text, match.start(1), match.end(1))
        if kind != APPEARS:
            continue
        if not _APPEAR_AFTER.match(text[match.end(1):match.end(1) + 36]):
            # New names need a stronger appear cue than known-character fallback.
            right = text[match.end(1):match.end(1) + 36]
            if not re.match(r"[은는이가]\s", right) and not _APPEAR_BEFORE.search(
                text[max(0, match.start(1) - 12):match.start(1)]
            ):
                continue
        seen.add(key)
        found.append(name)
        if len(found) >= limit:
            break
    return found
