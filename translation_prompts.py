"""Prompts for SuperTory's submission-oriented multilingual translation pipeline."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypedDict

NARRATIVE_FORMATTING_PROMPT_HEAD = """당신은 한국 웹소설을 영어권 투고용으로 번역하기 위해 준비하는 편집 보조자입니다.
아래 원고 앞부분(또는 전체)을 읽고, 이 작품에 "서사 표기 규칙(narrative formatting convention)"이
있는지 확인하세요.
[서사 표기 규칙이란]
일반적인 한국어 소설은 대사에 큰따옴표(" ")만 씁니다. 그런데 일부 작품은 특정 정보를 구분하기
위해 괄호/기호를 다르게 씁니다. 예:
- 서로 다른 언어로 하는 대사를 다른 괄호로 구분 (예: "" vs [])
- 텔레파시/속마음/시스템 메시지를 별도 기호로 구분 (예: -, 《》, ()  등)
- 과거 회상이나 다른 시공간의 대사를 별도 표기
[작업 순서]
1. 원고에서 일반 대사(" ") 외에 다른 기호가 쓰였는지 찾으세요.
2. 각 기호가 무엇을 구분하는지 원고 맥락(설명 문구, 앞뒤 서술)에서 근거를 찾아 판단하세요.
   근거가 부족하면 "unclear"로 표시하고 추측하지 마세요.
3. 발견된 규칙에 대해 두 가지 처리 방식 중 하나를 추천하세요:
   - "preserve_with_note": 원문 표기 그대로 유지하고, 투고 패키지에 범례(legend) 설명을 첨부.
     세계관 설정(언어 구분, 텔레파시 등)이 스토리에 중요한 요소일 때 추천.
   - "domesticate_to_standard": 영어권 관습대로 전부 큰따옴표로 통일하고, 언어 구분이 필요한
     경우 이탤릭체나 짧은 태그(예: *[in Pagamon]*)로 대체. 표기 구분이 스토리 핵심이 아니거나
     너무 많은 기호가 섞여 오히려 가독성을 해칠 때 추천.
4. 왜 그 방식을 추천하는지 이유를 1~2문장으로 설명하세요.
[출력 형식 — JSON만 출력]
{
  "detected_conventions": [
    {"marker": "...", "meaning": "...", "confidence": "high|low"}
  ],
  "recommended_handling": "preserve_with_note" | "domesticate_to_standard" | "no_special_convention_found",
  "recommendation_reason": "..."
}
[예시]
원고 앞부분에 "모나어는 "", 파가몬어는 [], 텔레파시는 -로 표기합니다." 라는 안내 문구가 있고,
이후 본문에서 실제로 이 세 기호가 쓰인 경우:
출력:
{
  "detected_conventions": [
    {"marker": "\\"\\"", "meaning": "모나어(공용어) 대사", "confidence": "high"},
    {"marker": "[]", "meaning": "파가몬어 대사", "confidence": "high"},
    {"marker": "—", "meaning": "텔레파시", "confidence": "high"}
  ],
  "recommended_handling": "preserve_with_note",
  "recommendation_reason": "작가가 명시적으로 표기 규칙을 안내했고, 언어 구분이 세계관 설정의 일부로 보입니다. 이 구분이 사라지면 설정이 훼손될 수 있어 원문 표기를 유지하고 범례로 안내하는 것을 추천합니다."
}
이제 아래 원고를 같은 방식으로 처리하세요:
"""


def build_narrative_formatting_prompt(chapter_text: str) -> str:
    """Insert chapter text into the narrative-formatting convention prompt."""
    body = "" if chapter_text is None else str(chapter_text)
    return f"{NARRATIVE_FORMATTING_PROMPT_HEAD}{body}"


SCENE_SPLIT_PROMPT_HEAD = """당신은 한국 웹소설을 영어권 투고용으로 번역하기 위해 준비하는 편집 보조자입니다.
아래 챕터 원문을 읽고, 이 챕터를 "씬(scene)" 단위로 나누세요.
[씬 분할 기준 — 아래 중 하나라도 바뀌면 새로운 씬으로 나눕니다]
1. 장소가 바뀔 때
2. 시간이 뚜렷하게 흐를 때 (예: "그날 저녁" 같은 시간 전환 문구)
3. 대화하는 상대가 바뀔 때 (예: A와의 대화 → B와의 대화)
단순히 문단이 나뉘었다고 씬을 나누지 마세요. 위 3가지 기준에 해당하지 않으면 같은 씬으로 유지합니다.
각 씬마다 다음을 판단하세요:
- relationship_tag: 이 씬에서 주로 대화/교류하는 인물 간의 관계를 한 단어로 (예: "연인-다정", "가족-갈등", "상사-부하", "친구-가벼움", "적대", "초면-어색함")
- mood_tag: 이 씬 전체를 지배하는 감정 톤 (예: "다정함", "긴장", "냉랭함", "장난스러움", "슬픔", "분노", "평온", "다급함")
- situation_note: 이 씬에서 무슨 일이 일어나는지 한 문장으로 (한글, 15자 내외)
[출력 형식 — JSON만 출력, 다른 설명 붙이지 마세요]
{
  "scenes": [
    {
      "scene_order": 1,
      "start_paragraph_index": 0,
      "end_paragraph_index": 4,
      "relationship_tag": "...",
      "mood_tag": "...",
      "situation_note": "..."
    }
  ]
}
[예시]
원문 예시:
"서준은 카페 문을 열고 들어갔다. 창가 자리에 하린이 앉아 있었다.
'오빠, 여기.' 하린이 손을 흔들었다. 서준이 웃으며 다가갔다.
'많이 기다렸어?' '아니, 방금 왔어.'
그날 밤, 서준은 본가에 들렀다. 거실엔 정적이 흘렀다.
'또 그 여자 만나고 온 거니.' 어머니의 목소리가 낮게 깔렸다.
서준은 아무 말도 하지 못했다."
출력 예시:
{
  "scenes": [
    {
      "scene_order": 1,
      "start_paragraph_index": 0,
      "end_paragraph_index": 2,
      "relationship_tag": "연인-다정",
      "mood_tag": "다정함",
      "situation_note": "카페에서 하린을 만남"
    },
    {
      "scene_order": 2,
      "start_paragraph_index": 3,
      "end_paragraph_index": 5,
      "relationship_tag": "가족-갈등",
      "mood_tag": "냉랭함",
      "situation_note": "어머니와 불편한 대화"
    }
  ]
}
이제 아래 원문을 같은 방식으로 처리하세요:
"""


def build_scene_split_prompt(chapter_text: str) -> str:
    """Insert chapter text into the scene-split prompt."""
    body = "" if chapter_text is None else str(chapter_text)
    return f"{SCENE_SPLIT_PROMPT_HEAD}{body}"


PROPER_NOUN_FIT_PROMPT_HEAD = """당신은 한국 웹소설을 영어권 투고용으로 번역하기 위해 준비하는 편집 보조자입니다.
아래 원고에서 고유명사(인명, 지명, 사물명, 조직명 등)를 찾아 영어권 독자에게 어울리는지 판단하세요.
[판단 기준 — 이 4가지 관점에서만 판단합니다]
1. 발음 부자연스러움: 로마자 표기 시 영어권에서 발음하기 어렵거나 어색한 음절 조합인가
2. 의도치 않은 의미: 영어 단어/비속어와 발음이 겹쳐 원치 않는 뜻으로 들리는가
3. 기존 유명인/브랜드와 혼동: 실존 유명인, 유명 캐릭터, 브랜드명과 우연히 겹치는가
4. 희화화 위험: 진지한 장면에서 이름이 우스꽝스럽게 들려 몰입을 깨는가
이 4가지에 해당하지 않으면 "fits"로 판정하세요. 막연히 "한국식 이름이라서"라는 이유만으로
안 어울린다고 판정하지 마세요 — 로마자 표기된 한국 이름은 영어권 판타지/로맨스 장르에서도
흔하게 쓰입니다.
[처리 방식]
- fits로 판정된 경우: 그대로 진행해도 좋다는 의견과 함께, 그래도 영어식 이름으로 바꾸고 싶다면
  그 이유(장르 관습, 발음 편의 등)를 간단히 언급하세요.
- does_not_fit으로 판정된 경우: 구체적인 이유를 설명하고, 두 가지 선택지를 제시하세요.
  1) 로마자 표기 그대로 유지 2) 새 이름으로 작명 — 작명 선택 시를 대비해 원래 이름의 어감/
  느낌(강인함, 우아함, 순박함 등)을 살린 대안 이름 2~3개를 미리 추천하세요.
[출력 형식 — JSON만 출력]
{
  "proper_nouns": [
    {
      "source_term": "...",
      "term_type": "character|place|item|organization",
      "romanized": "...",
      "fit_judgment": "fits|does_not_fit",
      "judgment_reason": "...",
      "suggested_alternatives": ["...", "..."]
    }
  ]
}
[예시1 — fits로 판정되는 경우]
원문: "이오나는 메리 양에게 다가갔다."
출력:
{
  "proper_nouns": [
    {
      "source_term": "이오나",
      "term_type": "character",
      "romanized": "Iona",
      "fit_judgment": "fits",
      "judgment_reason": "'Iona'는 영어권에서 실제로 쓰이는 여성 이름(스코틀랜드 기원)과 표기가 같아 발음도 자연스럽고 위화감이 없습니다. 그대로 진행해도 좋습니다.",
      "suggested_alternatives": []
    },
    {
      "source_term": "메리",
      "term_type": "character",
      "romanized": "Meri",
      "fit_judgment": "fits",
      "judgment_reason": "'Mary'와 발음이 거의 동일해 영어권 독자에게 매우 자연스럽습니다. 다만 이미 영어식 이름이라 오히려 '한국 원작' 느낌이 옅어질 수 있어, 장르에 따라 원문 발음을 살린 'Meri'로 살짝 조정하는 것도 고려할 수 있습니다.",
      "suggested_alternatives": []
    }
  ]
}
[예시2 — does_not_fit으로 판정되는 경우]
원문: "함정임 부인이 인사를 건넸다."
출력:
{
  "proper_nouns": [
    {
      "source_term": "함정임",
      "term_type": "character",
      "romanized": "Ham Jeongim",
      "fit_judgment": "does_not_fit",
      "judgment_reason": "로마자 표기 'Jeongim'이 영어 단어 'jam'/'gym'과 유사하게 들려 진지한 귀부인 캐릭터에 의도치 않은 가벼운 인상을 줄 수 있습니다. 발음 리듬도 영어권에서 다소 낯섭니다.",
      "suggested_alternatives": ["Seraphine", "Adelind", "Rosalind"]
    }
  ]
}
이제 아래 원문에서 고유명사를 찾아 같은 방식으로 처리하세요:
"""


def build_proper_noun_fit_prompt(chapter_text: str) -> str:
    """Insert chapter text into the proper-noun fit judgment prompt."""
    body = "" if chapter_text is None else str(chapter_text)
    return f"{PROPER_NOUN_FIT_PROMPT_HEAD}{body}"


CULTURE_MARKER_PROMPT_HEAD = """당신은 한국 웹소설을 영어권 투고용으로 번역하기 위해 준비하는 편집 보조자입니다.
아래 문단에서 "문화 마커"에 해당하는 표현을 찾아, 지정된 문화반영범위(culture_localization_level)에
맞게 번역 방향을 제시하세요.

[문화 마커 7가지 카테고리]
1. 호칭/존비어 (오빠, 언니, 사장님, 반말↔존댓말 뉘앙스)
2. 음식/사물 (김치찌개, 온돌, 한복 등)
3. 관용구/속담
4. 문화적 정서·개념 (정, 한, 눈치, 체면, 효)
5. 도량형/화폐/날짜 (평, 원, 음력)
6. 유머/언어유희
7. 사회적 맥락·제도 (군대, 수능, 회식문화, 나이서열)

이 7가지에 해당하지 않는 일반 문장은 건드리지 마세요.

[문화반영범위 3단계 정의]
- tight (타이트): 문화 마커를 현지 관용구/표현으로 완전히 재구성합니다. 단, 정보를 그냥
  삭제하는 게 아니라 "씬 컨텍스트(관계, 분위기)"가 담고 있던 감정과 뉘앙스는 다른 형태
  (어휘 선택, 문장 리듬, 문장부호)로 반드시 보존해야 합니다.
- moderate (적절): 개념은 살리되 표현은 자연스럽게 순화합니다. 원문의 문화적 색채가
  옅게라도 느껴지되 읽기에 걸리지 않아야 합니다.
- as_is (원문유지): 한국 문화 고유성을 최대한 살립니다. 로마자 표기 유지 + 필요시
  짧은 설명을 자연스럽게 문장에 녹이거나 용어집으로 뺍니다.

[입력으로 주어지는 씬 컨텍스트를 반드시 참고하세요]
- relationship_tag: 화자 간 관계
- mood_tag: 이 장면의 감정 톤
같은 문화 마커라도 관계와 분위기에 따라 결과가 달라져야 합니다.

[출력 형식 — JSON만 출력]
{
  "culture_markers": [
    {
      "source_phrase": "...",
      "category": "...",
      "localization_level_applied": "tight|moderate|as_is",
      "translated_phrase": "...",
      "reasoning": "..."
    }
  ]
}

[예시 — 같은 문장, 같은 tight 설정인데 씬 컨텍스트가 다른 경우]

원문 문단: "오빠, 밥 먹었어?"

컨텍스트 A: relationship_tag="연인-다정", mood_tag="다정함"
출력:
{
  "culture_markers": [
    {
      "source_phrase": "오빠, 밥 먹었어?",
      "category": "호칭/존비어",
      "localization_level_applied": "tight",
      "translated_phrase": "Have you eaten, babe?",
      "reasoning": "호칭 '오빠'는 영어권에 대응어가 없어 제거했지만, 애칭(babe)으로 대체해 다정한 관계 뉘앙스를 보존했습니다."
    }
  ]
}

컨텍스트 B: relationship_tag="가족-갈등", mood_tag="냉랭함"
출력:
{
  "culture_markers": [
    {
      "source_phrase": "오빠, 밥 먹었어?",
      "category": "호칭/존비어",
      "localization_level_applied": "tight",
      "translated_phrase": "Did you have dinner.",
      "reasoning": "냉랭한 분위기이므로 호칭을 제거하고 물음표 없이 건조한 평서문 형태로 처리해 거리감과 냉기를 문장 리듬으로 보존했습니다."
    }
  ]
}

[예시 — moderate 적용]
원문: "정말 눈치 없게 그런 말을 하다니."
컨텍스트: relationship_tag="친구-가벼움", mood_tag="당황"
출력:
{
  "culture_markers": [
    {
      "source_phrase": "눈치 없게",
      "category": "문화적 정서·개념",
      "localization_level_applied": "moderate",
      "translated_phrase": "so oblivious",
      "reasoning": "'눈치'라는 개념 자체를 직역하지 않고, 상황을 못 읽는다는 의미를 자연스러운 영어 표현으로 순화했습니다. 문화적 색채는 옅어지지만 의미 손실은 없습니다."
    }
  ]
}

[예시 — as_is 적용]
원문: "할머니가 끓여주신 된장찌개 냄새가 났다."
컨텍스트: relationship_tag="가족-그리움", mood_tag="애틋함"
출력:
{
  "culture_markers": [
    {
      "source_phrase": "된장찌개",
      "category": "음식/사물",
      "localization_level_applied": "as_is",
      "translated_phrase": "doenjang jjigae",
      "reasoning": "음식 고유명은 로마자 표기 그대로 유지했습니다. 향토적 정서와 할머니에 대한 그리움이라는 장면의 정서를 살리는 데 원문 표기가 더 효과적입니다."
    }
  ]
}

이제 아래 문단을 처리하세요.
"""


def build_culture_marker_prompt(
    chapter_text: str,
    culture_localization_level: str,
    relationship_tag: str,
    mood_tag: str,
) -> str:
    """Insert level, scene context, and chapter text into the culture-marker prompt."""
    body = "" if chapter_text is None else str(chapter_text)
    level = "" if culture_localization_level is None else str(culture_localization_level)
    relationship = "" if relationship_tag is None else str(relationship_tag)
    mood = "" if mood_tag is None else str(mood_tag)
    return (
        f"{CULTURE_MARKER_PROMPT_HEAD}"
        f"[문화반영범위 설정]: {level}\n"
        f"[씬 컨텍스트]: relationship_tag={relationship}, mood_tag={mood}\n"
        f"{body}"
    )


PARAGRAPH_TRANSLATION_PROMPT_HEAD = """당신은 한국 웹소설을 영어권 투고용으로 번역하는 전문 번역가입니다.
아래 문단을 지정된 설정에 따라 자연스러운 영어로 번역하세요.

"""

PARAGRAPH_TRANSLATION_PROMPT_TAIL = """
[번역 시 지켜야 할 것]
1. 직역이 아니라 영어권 소설처럼 자연스럽게 읽히는 문장으로 만드세요.
2. 원문의 정보를 임의로 추가하거나 생략하지 마세요.
3. 의역했거나 뉘앙스를 다른 방식으로 옮긴 부분이 있다면 translation_notes에 기록하세요.
   단순 직역인 부분은 굳이 기록하지 않아도 됩니다.
4. 대명사(he/she/they)가 직전 문맥과 일치하는지 확인하세요.

[출력 형식 — JSON만 출력]
{
  "translated_text": "...",
  "translation_notes": [
    {"source_phrase": "...", "translated_as": "...", "note": "왜 이렇게 의역했는지"}
  ]
}

[예시]

스타일가이드: 시제=past, 어조=격식없는 캐주얼체
고유명사: 이오나→Iona, 메리→Meri
문화반영범위: moderate
씬 컨텍스트: relationship_tag=이웃-정보교환, mood_tag=걱정/궁금함
직전 문맥: (없음, 챕터 첫 문단)

원문:
"문이 굳게 닫힌 빵집 앞에 선 이오나가 작은 탄식을 뱉었다.
'하아~!'"

출력:
{
  "translated_text": "Iona stood in front of the tightly shuttered bakery and let out a small sigh.\\n\\"Ahh...\\"",
  "translation_notes": [
    {
      "source_phrase": "작은 탄식을 뱉었다",
      "translated_as": "let out a small sigh",
      "note": "'탄식을 뱉다'는 직역하면 어색해서('spat out a sigh') 영어권에서 자연스러운 관용 표현 'let out a sigh'로 옮겼습니다."
    }
  ]
}

[예시2 — 문화 마커 처리가 포함된 경우]

문화반영범위: tight
씬 컨텍스트: relationship_tag=가족-갈등, mood_tag=냉랭함

원문: "또 그 여자 만나고 온 거니."

출력:
{
  "translated_text": "\\"Off to see that woman again.\\"",
  "translation_notes": [
    {
      "source_phrase": "만나고 온 거니",
      "translated_as": "Off to see... again",
      "note": "의문형 종결어미를 물음표 없는 단정적 어투로 바꿔, 냉랭한 관계의 추궁하는 뉘앙스를 문장 리듬으로 보존했습니다(tight 설정)."
    }
  ]
}

이제 아래 문단을 번역하세요.
"""


class ParagraphTranslationSettings(TypedDict, total=False):
    tense: str
    character_voices: str
    proper_nouns_confirmed: str
    culture_localization_level: str
    relationship_tag: str
    mood_tag: str
    narrative_formatting_rules: str
    previous_context_summary: str


def _setting(settings: Mapping[str, object] | None, key: str) -> str:
    if not settings:
        return ""
    value = settings.get(key)
    return "" if value is None else str(value)


def build_paragraph_translation_prompt(
    chapter_text: str,
    settings: Mapping[str, object] | None = None,
) -> str:
    """Insert style, glossary, scene context, and paragraph text into the translation prompt.

    `settings` is a single dict (see ParagraphTranslationSettings). Keep
    `previous_context_summary` as already-confirmed English translation, never
    Korean source — use translation_context.load_previous_translated_context.
    """
    body = "" if chapter_text is None else str(chapter_text)
    tense = _setting(settings, "tense")
    voices = _setting(settings, "character_voices")
    nouns = _setting(settings, "proper_nouns_confirmed")
    level = _setting(settings, "culture_localization_level")
    relationship = _setting(settings, "relationship_tag")
    mood = _setting(settings, "mood_tag")
    formatting = _setting(settings, "narrative_formatting_rules")
    previous = _setting(settings, "previous_context_summary")
    return (
        f"{PARAGRAPH_TRANSLATION_PROMPT_HEAD}"
        f"[스타일가이드]\n"
        f"- 시제: {tense}\n"
        f"- 인물별 어조: {voices}\n\n"
        f"[확정된 고유명사 — 반드시 이 표기를 그대로 사용하세요]\n"
        f"{nouns}\n\n"
        f"[문화반영범위]: {level}\n"
        f"[이 씬의 컨텍스트]: relationship_tag={relationship}, mood_tag={mood}\n"
        f"문화 마커(호칭, 관용구, 정서 표현 등)는 이 범위 설정과 씬 컨텍스트에 맞게 처리하세요.\n"
        f"tight일 경우, 정보를 삭제하지 말고 다른 형태(어휘/리듬/문장부호)로 뉘앙스를 보존하세요.\n\n"
        f"[대사 표기 규칙]\n"
        f"{formatting}\n"
        f"이 규칙에 따라 언어/텔레파시 구분 표기를 유지하세요.\n\n"
        f"[직전 문맥 — 이미 확정된 영문 번역입니다. 대명사와 시제의 연속성을 위해 참고하세요, 번역 대상 아님]\n"
        f"{previous}\n"
        f"{PARAGRAPH_TRANSLATION_PROMPT_TAIL}"
        f"{body}"
    )


POLISH_PROMPT_HEAD = """당신은 영어권 출판 편집자입니다. 아래는 한국 웹소설을 1차 번역한 챕터 전체입니다.
의미를 바꾸지 않으면서, 번역투(translationese)를 제거하고 원어민이 쓴 소설처럼
자연스러운 리듬으로 다듬으세요.
[윤문 시 지켜야 할 것]
1. 절대 원문의 정보를 추가/삭제/변경하지 마세요. 사건, 대사 내용, 묘사 대상은 그대로입니다.
2. 다음 번역투 패턴을 우선적으로 손보세요:
   - 같은 문장 종결 패턴이 반복될 때 (예: "-ed. -ed. -ed."로만 계속되는 단조로운 리듬)
   - 한국어 어순을 그대로 옮겨 어색한 문장 구조
   - 불필요하게 늘어진 수식어/부사 나열
   - 대명사나 주어가 과도하게 반복되는 부분
3. 문단 간 흐름(한 문단에서 다음 문단으로 넘어가는 호흡)이 매끄러운지 확인하세요.
4. 대사 톤은 스타일가이드의 인물별 어조를 유지하세요.
5. 무엇을 왜 고쳤는지 change_log에 기록하세요. 사소한 어순 조정까지 전부 기록할 필요는 없고,
   문장 구조나 리듬이 눈에 띄게 바뀐 부분만 기록하세요.

"""

POLISH_PROMPT_TAIL = """
[출력 형식 — JSON만 출력]
{
  "polished_text": "...",
  "change_log": [
    {"before": "...", "after": "...", "reason": "..."}
  ]
}

[예시]
1차 번역 입력:
"Iona stood in front of the bakery. The bakery was closed. Iona sighed.
She looked up at the chimney. The chimney was quiet. She felt disappointed."
출력:
{
  "polished_text": "Iona stood before the shuttered bakery and let out a sigh. She looked up at the quiet chimney, disappointment settling over her.",
  "change_log": [
    {
      "before": "Iona stood in front of the bakery. The bakery was closed. Iona sighed.",
      "after": "Iona stood before the shuttered bakery and let out a sigh.",
      "reason": "세 개의 짧은 단문이 주어를 반복하며 끊어져 번역투 리듬이었습니다. 하나의 문장으로 합쳐 자연스러운 호흡으로 만들었습니다. 정보 손실은 없습니다."
    },
    {
      "before": "She looked up at the chimney. The chimney was quiet. She felt disappointed.",
      "after": "She looked up at the quiet chimney, disappointment settling over her.",
      "reason": "동일한 명사(chimney)와 주어(She)가 연속 반복되어 기계번역처럼 읽혔습니다. 분사구문으로 자연스럽게 연결했습니다."
    }
  ]
}

이제 아래 챕터를 윤문하세요.
"""


def build_polish_prompt(
    chapter_text: str,
    settings: Mapping[str, object] | None = None,
) -> str:
    """Insert styleguide and first-pass English chapter into the polish prompt."""
    body = "" if chapter_text is None else str(chapter_text)
    tense = _setting(settings, "tense")
    voices = _setting(settings, "character_voices")
    return (
        f"{POLISH_PROMPT_HEAD}"
        f"[스타일가이드]\n"
        f"- 시제: {tense}\n"
        f"- 인물별 어조: {voices}\n"
        f"{POLISH_PROMPT_TAIL}"
        f"{body}"
    )


SUBMISSION_QUERY_PROMPT_HEAD = """당신은 한국 웹소설을 영어권 에이전트/출판사에 투고하기 위해 자료를 준비하는 편집 보조자입니다.
아래는 이 작품의 한국어 시놉시스입니다. 이것을 영어권 투고 관행에 맞는 로그라인과 시놉시스로
번역/재구성하세요.
[로그라인 작성 기준]
- 1~2문장, 40단어 이내
- 주인공, 주인공이 원하는 것(목표), 그것을 막는 장애물, 이 작품만의 독특한 훅(장르 관습을
  비트는 지점)이 담겨야 합니다
- 결말은 밝히지 않습니다
[시놉시스 작성 기준]
- 분량: 300~500단어 (에이전트 투고 관행상 1~2페이지 분량)
- 기승전결 전체를 요약하되, 세부 사건을 나열하지 말고 핵심 갈등의 전개만 따라가세요
- 결말(반전 포함)까지 명시합니다 — 투고용 시놉시스는 스포일러를 감추지 않는 것이 관행입니다
- 3인칭 현재시제로 작성합니다 (영어권 시놉시스 관행)
- 등장인물은 처음 등장할 때만 대문자로 강조 표기합니다 (예: IONA)

"""

SUBMISSION_QUERY_PROMPT_TAIL = """
[출력 형식 — JSON만 출력]
{
  "logline": "...",
  "synopsis": "..."
}

[예시]
한국어 시놉시스 입력 (요약):
"평민 출신 이오나는 우연히 왕실 마법사의 제자가 되지만, 신분을 숨기고 살아야 한다.
그러던 중 왕자와 얽히게 되고, 자신의 출생의 비밀이 왕국의 존폐와 연결되어 있음을 알게 된다.
결국 이오나는 신분을 밝히고 왕국을 구하지만, 그 대가로 마법사로서의 힘을 잃는다."
고유명사: 이오나→Iona
출력:
{
  "logline": "A commoner secretly training as a royal mage must choose between hiding her identity forever and revealing a birth secret that could save — or shatter — the kingdom.",
  "synopsis": "IONA, a commoner with no right to magic, becomes the secret apprentice of the royal mage — a position that could cost her life if discovered. When a chance encounter draws her into the orbit of the crown prince, she is forced to navigate a court that would destroy her the moment her true status is known.\\n\\nAs Iona's magical abilities grow beyond what any commoner should possess, she uncovers a truth even she never suspected: her birth is tied to a prophecy that threatens the kingdom's survival. Revealing it means exposing herself completely — and risking everything she has built.\\n\\nIn the end, Iona chooses to reveal her identity and the secret she carries, saving the kingdom from collapse. But the price is steep: the very magic that defined her disappears, leaving her to face a future for the first time without the power that once protected her."
}

이제 아래 시놉시스를 처리하세요.
"""


def build_submission_query_prompt(
    chapter_text: str,
    settings: Mapping[str, object] | None = None,
) -> str:
    """Insert confirmed proper nouns and Korean synopsis into the query-package prompt."""
    body = "" if chapter_text is None else str(chapter_text)
    nouns = _setting(settings, "proper_nouns_confirmed")
    return (
        f"{SUBMISSION_QUERY_PROMPT_HEAD}"
        f"[확정된 고유명사 — 반드시 이 표기를 사용하세요]\n"
        f"{nouns}\n"
        f"{SUBMISSION_QUERY_PROMPT_TAIL}"
        f"{body}"
    )
