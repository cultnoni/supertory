"""「내 문체」 프로필: 저장 형식, AI 프롬프트 블록, 로컬 자동 제안.

- 작품 하나에 프로필 하나(`project.style_profile_json`, db/108).
- 시점·시제·금지어는 토리 체크(`project_tory_check`)와 같은 값을 쓴다. 여기서는 받아서 블록에만 넣는다.
- AI 글쓰기 요청(이어쓰기·다듬기·세계관 묘사·직접 요청·브레인스토밍)의 시스템 프롬프트에
  작가 지침 바로 뒤로 들어간다. 우선순위: 이번 요청 > 문체 프로필 > 장르 기준 > 기본.
- 자동 제안은 AI를 부르지 않고 원고에서 직접 센다(비용 0).

설계: docs/design-ai-context-and-style-2026-11.md §3.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Iterable

PRESETS: dict[str, dict[str, Any]] = {
    "speed": {
        "label": "속도감",
        "prompt": "짧은 문장과 빠른 장면 전환. 설명보다 행동과 대사로 밀고 나간다.",
        "values": {"tempo": 5, "sentence_length": "short", "dialogue_ratio": 50, "description_density": 2, "humor": 2},
    },
    "literary": {
        "label": "문예 묘사",
        "prompt": "감각적인 묘사와 여운 있는 문장. 장면의 분위기와 인물의 내면을 충분히 보여 준다.",
        "values": {"tempo": 2, "sentence_length": "long", "dialogue_ratio": 20, "description_density": 5, "humor": 0},
    },
    "hardboiled": {
        "label": "건조한 하드보일드",
        "prompt": "감정을 설명하지 않는 건조한 문장. 사실과 행동만 짧게 적고 수식어를 아낀다.",
        "values": {"tempo": 4, "sentence_length": "short", "dialogue_ratio": 40, "description_density": 2, "humor": 1},
    },
    "healing": {
        "label": "잔잔한 힐링",
        "prompt": "따뜻하고 느긋한 호흡. 일상의 작은 감정과 풍경을 부드럽게 그린다.",
        "values": {"tempo": 2, "sentence_length": "medium", "dialogue_ratio": 40, "description_density": 4, "humor": 2},
    },
    "comic": {
        "label": "코믹 경쾌",
        "prompt": "가볍고 리듬감 있는 문장. 말맛 있는 대사와 반전 있는 개그 타이밍을 살린다.",
        "values": {"tempo": 4, "sentence_length": "short", "dialogue_ratio": 60, "description_density": 2, "humor": 5},
    },
    "custom": {
        "label": "직접 설정",
        "prompt": "",
        "values": {"tempo": 3, "sentence_length": "medium", "dialogue_ratio": 40, "description_density": 3, "humor": 1},
    },
}

SENTENCE_LENGTHS = ("short", "medium", "long")
SENTENCE_LENGTH_LABELS = {"short": "짧게", "medium": "보통", "long": "길게"}

HABITS_MAX_CHARS = 2000
SAMPLE_MAX_CHARS = 800
WORD_MAX_CHARS = 40
WORDS_MAX = 30
PROMPT_BLOCK_MAX_CHARS = 2200

# 이 모드들에서만 프롬프트에 문체를 넣는다(분석·검사·요약 모드는 넣지 않는다).
WRITING_MODES = frozenset({
    "continue", "rewrite", "worlddesc", "free", "brainstorm", "brainstorm_next_exists",
})

_NUMERIC_RANGES = {
    "tempo": (1, 5),
    "description_density": (1, 5),
    "humor": (0, 5),
    "dialogue_ratio": (0, 100),
}


def empty_profile() -> dict[str, Any]:
    return {
        "preset": "",
        "tempo": None,
        "sentence_length": None,
        "dialogue_ratio": None,
        "description_density": None,
        "humor": None,
        "habits_md": "",
        "preferred_words": [],
        "sample_text": "",
    }


def _clean_int(value: Any, low: int, high: int) -> int | None:
    if value is None or value == "":
        return None
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return max(low, min(high, number))


def clean_words(value: Any) -> list[str]:
    if isinstance(value, str):
        items: Iterable[Any] = re.split(r"[,\n]", value)
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        word = re.sub(r"\s+", " ", str(item or "")).strip()[:WORD_MAX_CHARS]
        key = word.lower()
        if not word or key in seen:
            continue
        seen.add(key)
        out.append(word)
        if len(out) >= WORDS_MAX:
            break
    return out


def normalize_profile(raw: Any) -> dict[str, Any]:
    """저장·응답에 쓰는 정규형. 모르는 키는 버린다."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except (TypeError, ValueError):
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    profile = empty_profile()
    preset = str(raw.get("preset") or "").strip()
    profile["preset"] = preset if preset in PRESETS else ""
    for key, (low, high) in _NUMERIC_RANGES.items():
        profile[key] = _clean_int(raw.get(key), low, high)
    length = str(raw.get("sentence_length") or "").strip()
    profile["sentence_length"] = length if length in SENTENCE_LENGTHS else None
    profile["habits_md"] = str(raw.get("habits_md") or "")[:HABITS_MAX_CHARS]
    profile["preferred_words"] = clean_words(raw.get("preferred_words"))
    profile["sample_text"] = str(raw.get("sample_text") or "")[:SAMPLE_MAX_CHARS]
    return profile


def merge_profile(current: Any, patch: dict[str, Any]) -> dict[str, Any]:
    """patch에 있는 키만 바꾼다. 프리셋을 고르면서 비어 있는 다이얼만 프리셋 기본값으로 채운다.

    이미 있는 값은 덮어쓰지 않는다(다이얼을 손으로 바꿔 「직접 설정」이 되어도 나머지 값이 그대로 남게).
    다른 프리셋의 값으로 바꾸려면 화면이 값을 함께 보낸다.
    """
    base = normalize_profile(current)
    merged = {**base, **{key: value for key, value in (patch or {}).items() if key in base}}
    preset = str(merged.get("preset") or "")
    if "preset" in (patch or {}) and preset in PRESETS:
        for key, value in PRESETS[preset]["values"].items():
            if merged.get(key) is None:
                merged[key] = value
    return normalize_profile(merged)


def has_content(profile: dict[str, Any]) -> bool:
    if profile.get("preset"):
        return True
    return bool(
        str(profile.get("habits_md") or "").strip()
        or profile.get("preferred_words")
        or str(profile.get("sample_text") or "").strip()
    )


def _scale_text(value: int, low_word: str, high_word: str, top: int = 5) -> str:
    if value <= 2:
        side = low_word
    elif value >= 4:
        side = high_word
    else:
        side = "보통"
    return f"{top}단계 중 {value} ({side})"


def build_prompt_block(
    profile: Any,
    *,
    viewpoint_person: str | None = None,
    viewpoint_tense: str | None = None,
    forbidden_words: Iterable[str] = (),
) -> str:
    """시스템 프롬프트에 넣을 [작가 문체 프로필] 블록. 쓸 내용이 없으면 빈 문자열."""
    data = normalize_profile(profile)
    forbidden = clean_words(list(forbidden_words or []))
    person = {"first": "1인칭", "third": "3인칭"}.get(str(viewpoint_person or ""))
    tense = {"past": "과거형", "present": "현재형"}.get(str(viewpoint_tense or ""))
    if not has_content(data) and not forbidden and not (person or tense):
        return ""

    lines: list[str] = []
    preset = data["preset"]
    if preset:
        info = PRESETS[preset]
        for key, value in info["values"].items():
            if data.get(key) is None:
                data[key] = value
        if info["prompt"]:
            lines.append(f"- 기본 결: {info['label']} — {info['prompt']}")
        if person or tense:
            lines.append(f"- 시점·시제: {' · '.join(x for x in (person, tense) if x)}")
        if data["tempo"] is not None:
            lines.append(f"- 전개 속도: {_scale_text(data['tempo'], '느긋한 편', '빠른 편')}")
        if data["sentence_length"]:
            lines.append(f"- 문장 길이: {SENTENCE_LENGTH_LABELS[data['sentence_length']]}")
        if data["dialogue_ratio"] is not None:
            lines.append(f"- 대사 비중: 본문의 약 {data['dialogue_ratio']}%")
        if data["description_density"] is not None:
            lines.append(f"- 묘사 밀도: {_scale_text(data['description_density'], '적은 편', '풍부한 편')}")
        if data["humor"] is not None:
            humor = data["humor"]
            lines.append("- 유머: 쓰지 않음" if humor == 0 else f"- 유머: {_scale_text(humor, '가볍게', '많이')}")
    elif person or tense:
        lines.append(f"- 시점·시제: {' · '.join(x for x in (person, tense) if x)}")
    if data["preferred_words"]:
        lines.append(f"- 즐겨 쓰는 표현: {', '.join(data['preferred_words'])}")
    if forbidden:
        lines.append(f"- 쓰지 않는 표현(본문에 넣지 말 것): {', '.join(forbidden)}")
    habits = str(data["habits_md"] or "").strip()
    if habits:
        lines.append(f"- 문체 습관·메모:\n{habits[:1000]}")

    block = (
        "[작가 문체 프로필]\n"
        "이 작가의 문체입니다. 본문을 쓰거나 고칠 때 이 결을 따르세요. "
        "우선순위: 이번 요청 > 이 문체 프로필 > 장르 기준 > 기본 작법. 서로 부딪히면 앞의 것을 따르세요.\n"
        + "\n".join(lines)
    )
    sample = str(data["sample_text"] or "").strip()
    if sample:
        block += (
            "\n[문체 예시 — 작가가 직접 고른 본문 발췌. 내용·설정은 가져오지 말고 문장의 결만 참고]\n"
            + sample
        )
    if len(block) > PROMPT_BLOCK_MAX_CHARS:
        block = block[:PROMPT_BLOCK_MAX_CHARS] + "…"
    return block + "\n\n"


# ── 내 원고에서 자동 제안 (AI 없음) ─────────────────────────────

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…。])\s+|\n+")
_QUOTED = re.compile(r"[\"“”「『][^\"“”「」『』\n]{1,400}[\"“”」』]")
_WORD = re.compile(r"[가-힣A-Za-z]{2,}")
_STOPWORDS = frozenset(
    "그리고 그러나 하지만 그런데 그래서 그렇게 이렇게 저렇게 그것 이것 저것 그녀 그는 그가 그를 "
    "있었다 없었다 했다 하는 하고 했던 있는 없는 것이 것을 것은 수가 수도 때문에 정도 다시 이미 "
    "아직 조금 정말 너무 그냥 모두 모든 어떤 무엇 누구 어디 지금 오늘 그때 순간 생각 사람".split()
)

MIN_SUGGEST_CHARS = 1000

# 낱말 끝 조사 한 글자(은·는·이·가·을·를·의·에·와·과·도·로)를 떼어 「도윤은」·「도윤이」를 「도윤」으로 센다.
_PARTICLE_ENDINGS = frozenset("은는이가을를의에와과도로")


def _strip_particle(word: str) -> str:
    if len(word) >= 3 and word[-1] in _PARTICLE_ENDINGS:
        return word[:-1]
    return word


def suggest_from_texts(texts: Iterable[str]) -> dict[str, Any]:
    """원고 글(평문 목록)에서 문장 길이·대사 비중·자주 쓰는 낱말을 센다."""
    joined = "\n".join(str(text or "") for text in texts).strip()
    total_chars = len(re.sub(r"\s+", "", joined))
    if total_chars < MIN_SUGGEST_CHARS:
        return {"ok": True, "enough": False, "chars": total_chars, "min_chars": MIN_SUGGEST_CHARS}

    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(joined) if len(s.strip()) >= 2]
    sentence_chars = [len(re.sub(r"\s+", "", s)) for s in sentences]
    avg_sentence = sum(sentence_chars) / max(1, len(sentence_chars))
    if avg_sentence < 22:
        sentence_length = "short"
    elif avg_sentence > 42:
        sentence_length = "long"
    else:
        sentence_length = "medium"
    if avg_sentence < 18:
        tempo = 5
    elif avg_sentence < 26:
        tempo = 4
    elif avg_sentence < 36:
        tempo = 3
    elif avg_sentence < 48:
        tempo = 2
    else:
        tempo = 1

    quoted_chars = sum(len(re.sub(r"\s+", "", m.group(0))) for m in _QUOTED.finditer(joined))
    dialogue_ratio = int(round(min(100.0, quoted_chars * 100.0 / max(1, total_chars)) / 10.0) * 10)

    counts = Counter(
        word for word in (_strip_particle(raw) for raw in _WORD.findall(joined))
        if len(word) >= 2 and word not in _STOPWORDS
    )
    frequent = [word for word, count in counts.most_common(40) if count >= 3][:10]

    return {
        "ok": True,
        "enough": True,
        "chars": total_chars,
        "sentence_count": len(sentence_chars),
        "avg_sentence_chars": round(avg_sentence, 1),
        "values": {
            "tempo": tempo,
            "sentence_length": sentence_length,
            "dialogue_ratio": dialogue_ratio,
        },
        "frequent_words": frequent,
    }
