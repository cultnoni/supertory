"""Prompts for SuperTory's submission-oriented multilingual translation pipeline."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import TypedDict

TARGET_LANGUAGE_PROFILES: dict[str, dict[str, str]] = {
    "en": {
        "name": "영어",
        "audience": "영어권",
        "reference_examples": "he/she/they",
        "naming_guidance": (
            "한국어 이름은 통용 로마자 표기를 기본으로 삼고, 영어 철자·발음 규칙과 "
            "영어 단어·비속어의 의도치 않은 중첩을 검토하세요."
        ),
        "submission_guidance": (
            "영어권 에이전트·출판사의 일반적인 투고 관행을 따르세요. 시놉시스는 "
            "3인칭 현재시제로 쓰고, 주요 인물은 첫 등장 때 대문자로 표기하세요."
        ),
        "dialogue_guidance": (
            "영어권 소설의 큰따옴표 대화 관습을 고려하되 원문의 언어·텔레파시 "
            "구분을 잃지 않도록 작품 전체에 적용할 규칙을 제안하세요."
        ),
    },
    "es": {
        "name": "스페인어",
        "audience": "스페인어권",
        "reference_examples": "él/ella/ellos/ellas/usted/ustedes",
        "naming_guidance": (
            "한국어 이름은 일관된 라틴 문자 전사를 기본으로 삼되 스페인어 철자·발음 "
            "규칙에서 오독되지 않는지 검토하세요. 원래 표기에 악센트가 있으면 유지하고, "
            "발음 근거 없이 악센트를 새로 붙이지 마세요. 스페인어 단어·비속어와의 "
            "의도치 않은 중첩도 확인하세요."
        ),
        "submission_guidance": (
            "스페인어권 출판사·에이전트의 일반적인 투고 관행에 맞춰 자연스러운 "
            "로그라인과 시놉시스를 작성하세요. 다른 언어권의 대문자·시제 관행을 "
            "기계적으로 적용하지 마세요."
        ),
        "dialogue_guidance": (
            "스페인어권 소설에서 대화 앞에 긴 줄표(—)를 쓰는 관습을 고려하되, "
            "원문의 인용부호가 언어·텔레파시를 구분한다면 그 기능을 보존할 규칙을 "
            "제안하세요."
        ),
    },
    "fr": {
        "name": "프랑스어",
        "audience": "프랑스어권",
        "reference_examples": "il/elle/ils/elles/on/vous",
        "naming_guidance": (
            "한국어 이름은 일관된 라틴 문자 전사를 기본으로 삼고 프랑스어 발음 "
            "규칙에서 오독되거나 지나치게 어려운 철자가 없는지 검토하세요. 원래 "
            "표기에 é, è, à, ç 같은 악센트가 있으면 유지하되, 확립된 표기나 발음 "
            "근거 없이 악센트를 새로 붙이지 마세요. 프랑스어 단어·비속어와의 "
            "의도치 않은 중첩도 확인하세요."
        ),
        "submission_guidance": (
            "프랑스어권 출판사·에이전트의 일반적인 투고 관행을 따르세요. 로그라인은 "
            "간결하게 쓰고 시놉시스는 현재시제를 기본으로 하되, 다른 언어권의 "
            "대문자 강조를 기계적으로 적용하지 마세요. 개별 출판사 지침이 있다면 "
            "그 지침을 우선하세요."
        ),
        "dialogue_guidance": (
            "프랑스어 출판물의 기메 따옴표(« … »)와 대화 전환용 긴 줄표 관습을 "
            "고려하세요. 다만 기메 사용을 일률적으로 강제하지 말고, 원문의 언어·"
            "텔레파시 구분 기능과 목표 출판 관행을 함께 보존할 규칙을 제안하세요."
        ),
    },
}


def normalize_target_language(target_language: object = "en") -> str:
    code = str(target_language or "en").strip().lower()
    return code if code in TARGET_LANGUAGE_PROFILES else "en"


def target_language_profile(target_language: object = "en") -> dict[str, str]:
    return TARGET_LANGUAGE_PROFILES[normalize_target_language(target_language)]


def _render_language_tokens(text: str, target_language: object = "en") -> str:
    profile = target_language_profile(target_language)
    return (
        str(text)
        .replace("__TARGET_LANGUAGE__", profile["name"])
        .replace("__TARGET_AUDIENCE__", profile["audience"])
        .replace("__REFERENCE_EXAMPLES__", profile["reference_examples"])
        .replace("__NAMING_GUIDANCE__", profile["naming_guidance"])
        .replace("__SUBMISSION_GUIDANCE__", profile["submission_guidance"])
        .replace("__DIALOGUE_GUIDANCE__", profile["dialogue_guidance"])
    )


TRANSLATION_NOTES_LANGUAGE_RULE = (
    "note 설명 문장은 반드시 원고 원문 언어(한국어)로 작성하세요. "
    "대상 언어로 쓰지 마세요. 원문 표현이나 번역 표현을 따옴표로 인용하는 것은 "
    "괜찮습니다(예: \"'하아'라는 감탄사를 'Ugh...'로 의역했어요\"). "
    "설명 문장 자체는 한국어여야 합니다."
)

GLOSSARY_TERM_LOCK_RULE = (
    "[용어 고정 — 문체 다양화보다 우선]\n"
    "확정된 고유명사·용어집 표기는 같은 문단이나 씬에서 아무리 반복되어도 "
    "문체를 다채롭게 하려고 동의어로 바꾸지 마세요. 항상 동일한 번역어를 쓰세요. "
    "일반적인 '같은 단어 반복은 피하라'는 작문 습관보다 이 규칙이 우선합니다. "
    "원문에서 작가가 만든 조어·특수 용어가 한 형태로만 쓰였다면 번역도 한 형태로만 "
    "쓰세요. 평범한 서술 동사·형용사·일반 명사(보다/바라보다, 손, 문 등)는 "
    "자연스러운 문체 다양성을 유지하세요."
)

POLISH_TERM_CONSISTENCY_MISSION = (
    "[핵심 임무 — 반드시 수행. 윤문은 1차 번역의 실수를 걸러내는 마지막 단계입니다]\n"
    "용어집 등록 여부와 무관하게, 챕터 전체를 다시 읽고 같은 사물·개념·행위·작가 "
    "조어를 가리키는 표현이 서로 다른 단어로 번역된 곳이 있는지 스스로 찾아내세요. "
    "발견하면 맥락상 더 적절한 표기로 챕터 전체를 통일하세요. 어느 쪽이 맞는지 "
    "판단하기 어려우면 가장 먼저 등장한 표기로 통일하세요. 이 점검은 선택 사항이 "
    "아닙니다.\n"
    "일반 서술의 문체 다양성(동사 리듬, 대명사, 평범한 배경 명사)은 유지하세요. "
    "특정 대상을 이름처럼 가리키는 반복 표현만 고정합니다."
)


def _omit_non_target_examples(
    text: str,
    target_language: object,
    *,
    example_marker: str,
    resume_marker: str | None = None,
) -> str:
    rendered = _render_language_tokens(text, target_language)
    if normalize_target_language(target_language) == "en":
        return rendered
    start = rendered.find(example_marker)
    if start < 0:
        return rendered
    if not resume_marker:
        return rendered[:start]
    resume = rendered.find(resume_marker, start)
    return rendered[:start] + (rendered[resume:] if resume >= 0 else "")


NARRATIVE_FORMATTING_PROMPT_HEAD = """당신은 한국 웹소설을 __TARGET_AUDIENCE__ 투고용으로 번역하기 위해 준비하는 편집 보조자입니다.
[대상 언어 대화 표기 지침]
__DIALOGUE_GUIDANCE__
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
   - "domesticate_to_standard": __TARGET_AUDIENCE__ 관습에 맞는 표기로 통일하고, 언어 구분이 필요한
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


def build_narrative_formatting_prompt(
    chapter_text: str, target_language: object = "en"
) -> str:
    """Insert chapter text into the narrative-formatting convention prompt."""
    body = "" if chapter_text is None else str(chapter_text)
    head = _omit_non_target_examples(
        NARRATIVE_FORMATTING_PROMPT_HEAD,
        target_language,
        example_marker="[예시]",
        resume_marker="이제 아래 원고",
    )
    return f"{head}{body}"


SCENE_SPLIT_PROMPT_HEAD = """당신은 한국 웹소설을 __TARGET_AUDIENCE__ 투고용으로 번역하기 위해 준비하는 편집 보조자입니다.
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


def build_scene_split_prompt(chapter_text: str, target_language: object = "en") -> str:
    """Insert chapter text into the scene-split prompt."""
    body = "" if chapter_text is None else str(chapter_text)
    head = _omit_non_target_examples(
        SCENE_SPLIT_PROMPT_HEAD,
        target_language,
        example_marker="[예시]",
        resume_marker="이제 아래 원문",
    )
    return f"{head}{body}"


PROPER_NOUN_FIT_PROMPT_HEAD = """당신은 한국 웹소설을 __TARGET_AUDIENCE__ 투고용으로 번역하기 위해 준비하는 편집 보조자입니다.
아래 원고에서 고유명사(인명, 지명, 사물명, 조직명, 작가 조어·특수 용어 등)를 찾아 __TARGET_AUDIENCE__ 독자에게 어울리는지 판단하세요.
[찾을 대상]
인명·지명·사물명·조직명뿐 아니라, 작품 안에서 반복 등장하며 특정 사물이나 개념을
가리키는 것으로 보이는 작가 조어(사전에 없는 표현 포함)도 고유명사 후보로 포함하세요.
짧은 구간에서 여러 번 반복되는 표현은 우선적으로 후보에 넣으세요.
일반 동사·형용사·흔한 명사(손, 문, 눈, 달리다 등)는 넣지 마세요.
[source_term 작성 규칙]
source_term은 반드시 인명·지명·사물명·조직명·조어 같은 명사(구)여야 합니다.
완결된 문장, 절, "~이며 / ~있으며 / ~했다 / ~한다"처럼 서술로 끝나는 표현은
넣지 마세요. 원문 한 문장에서 이름만 잘라 내세요.
예: "도릭스는 출처를 알 수 없는 가공된 운석이며" → "도릭스"
15자를 넘는 후보는 넣지 마세요.
[대상 언어별 이름 지침]
__NAMING_GUIDANCE__
[판단 기준 — 이 5가지 관점에서 판단합니다]
1. 발음 부자연스러움: 대상 언어 표기 시 독자가 발음하기 어렵거나 어색한 조합인가
2. 의도치 않은 의미: 대상 언어의 단어/비속어와 발음이 겹쳐 원치 않는 뜻으로 들리는가
3. 기존 유명인/브랜드와 혼동: 실존 유명인, 유명 캐릭터, 브랜드명과 우연히 겹치는가
4. 희화화 위험: 진지한 장면에서 이름이 우스꽝스럽게 들려 몰입을 깨는가
5. 시대감/장르 톤 부적합: 이름 자체는 문제없지만, 이 캐릭터/사물의 설정(나이, 신분,
   장르 분위기)과 어울리지 않게 지나치게 올드패션이거나 톤이 안 맞는 인상을 주는가.
이 5가지에 해당하지 않으면 "fits"로 판정하세요. 막연히 "한국식 이름이라서"라는 이유만으로
안 어울린다고 판정하지 마세요 — 전사된 한국 이름은 대상 언어권 판타지/로맨스 장르에서도
흔하게 쓰입니다.
5번 기준을 판단할 때는 이 이름이 서사적으로 의도된 것인지 원문 맥락에서 먼저 판단하세요:
- 캐릭터가 실제로 노년/구세대 인물인가
- 촌스러운 이름이 개그 요소, 콤플렉스, 플롯 장치로 쓰이고 있는가
위에 해당하면 fits로 판정하고 그대로 유지하세요. 위에 해당하지 않는데 이름이
설정과 안 맞아 보이면 does_not_fit으로 판정하고, 그 장르/캐릭터 분위기에
어울리는 대안을 추천하세요.
[처리 방식]
- fits로 판정된 경우: 그대로 진행해도 좋다는 의견과 함께, 그래도 대상 언어권 이름으로 바꾸고 싶다면
  그 이유(장르 관습, 발음 편의 등)를 간단히 언급하세요.
- does_not_fit으로 판정된 경우: 구체적인 이유를 설명하고, 두 가지 선택지를 제시하세요.
  1) 기존 전사 표기 유지 2) 새 이름으로 작명 — 작명 선택 시를 대비해 원래 이름의 어감/
  느낌(강인함, 우아함, 순박함 등)을 살린 대안 이름 2~3개를 미리 추천하세요.
대안 이름을 추천할 때는 원래 이름이 주던 어감(우아함/발랄함/신비로움/이국적 느낌 등)과
캐릭터의 장르·설정 톤을 유지하는 방향으로 추천하세요. 동양풍 설정을 대상 언어권으로 옮길 때도
'이국적이고 신비로운 느낌'처럼 원문이 주던 인상을 대상 언어권 독자에게도 비슷하게 전달하는
이름을 찾으세요.
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
[예시3 — 시대감/장르 톤 does_not_fit]
원문: "스물셋의 패션 에디터 순자가 도심 카페에 들어섰다. 블랙 슬랙스에 단정한 블라우스, 짧은 단발."
출력:
{
  "proper_nouns": [
    {
      "source_term": "순자",
      "term_type": "character",
      "romanized": "Sunja",
      "fit_judgment": "does_not_fit",
      "judgment_reason": "이름 자체에 발음·의미 문제는 없지만, 20대 세련된 도시 여성 이미지와 안 맞고 서사적 의도(노년, 개그, 콤플렉스, 플롯 장치)도 보이지 않습니다. 장르 톤에 맞는 이름으로 바꾸는 것을 추천합니다.",
      "suggested_alternatives": ["Sian", "Iris", "Noelle"]
    }
  ]
}
[예시4 — 시대감/장르 톤 fits]
원문: "여든이 넘은 순자 할머니가 마루에 앉아 손주를 불렀다."
출력:
{
  "proper_nouns": [
    {
      "source_term": "순자",
      "term_type": "character",
      "romanized": "Sunja",
      "fit_judgment": "fits",
      "judgment_reason": "80대 할머니 캐릭터의 나이대·세대감과 잘 어울립니다. 서사적으로 의도된 이름으로 보이므로 그대로 유지하세요.",
      "suggested_alternatives": []
    }
  ]
}
[예시5 — 반복되는 작가 조어]
원문: "구속줄이 손목을 조였다. 구속줄을 끊으려 발버둥 쳤지만 구속줄은 더 팽팽해졌다."
출력:
{
  "proper_nouns": [
    {
      "source_term": "구속줄",
      "term_type": "item",
      "romanized": "restraint cord",
      "fit_judgment": "fits",
      "judgment_reason": "짧은 장면에서 같은 대상을 한 형태로만 가리키는 작가 조어입니다. 사전적 일반명사가 아니라 작품 고유 개념어이므로 후보에 넣고, 이후 번역에서 한 표기로 고정해야 합니다.",
      "suggested_alternatives": []
    }
  ]
}
"""

PROPER_NOUN_FIT_PROMPT_TAIL = """이제 아래 원문에서 고유명사를 찾아 같은 방식으로 처리하세요:
"""


def build_proper_noun_fit_prompt(
    chapter_text: str,
    existing_index_terms: list[str] | None = None,
    target_language: object = "en",
) -> str:
    """Insert chapter text into the proper-noun fit judgment prompt.

    When existing_index_terms is non-empty, the model must judge those
    setting-book names with the same criteria, then also report newly
    found proper nouns from the chapter text.
    """
    body = "" if chapter_text is None else str(chapter_text)
    terms = [
        str(item).strip()
        for item in (existing_index_terms or [])
        if str(item).strip()
    ]
    index_block = ""
    if terms:
        listed = "\n".join(f"- {term}" for term in terms)
        index_block = (
            "[설정집에 이미 있는 고유명사 — 아래 이름들도 반드시 같은 기준으로 판정하세요]\n"
            f"{listed}\n"
            "위 목록의 이름마다 romanized, fit_judgment, judgment_reason,\n"
            "suggested_alternatives를 출력에 포함하세요. 이번 원문에 안 나와도\n"
            "설정집 이름은 빠짐없이 판정하세요.\n"
            "그 다음, 위 목록에 없는 고유명사(단역 이름, 아이템명, 작가 조어,\n"
            "이번 장면에서만 등장하는 지명 등)도 원문에서 찾아 같은 방식으로 판정하세요.\n"
        )
    head = _omit_non_target_examples(
        PROPER_NOUN_FIT_PROMPT_HEAD,
        target_language,
        example_marker="[예시1",
    )
    return f"{head}{index_block}{PROPER_NOUN_FIT_PROMPT_TAIL}{body}"


CULTURE_MARKER_PROMPT_HEAD = """당신은 한국 웹소설을 __TARGET_AUDIENCE__ 투고용으로 번역하기 위해 준비하는 편집 보조자입니다.
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
- as_is (원문유지): 한국 문화 고유성을 최대한 살립니다. 대상 언어권에서 읽을 수 있는
  일관된 전사 표기 유지 + 필요시
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
    target_language: object = "en",
) -> str:
    """Insert level, scene context, and chapter text into the culture-marker prompt."""
    body = "" if chapter_text is None else str(chapter_text)
    level = "" if culture_localization_level is None else str(culture_localization_level)
    relationship = "" if relationship_tag is None else str(relationship_tag)
    mood = "" if mood_tag is None else str(mood_tag)
    return (
        f"{_omit_non_target_examples(CULTURE_MARKER_PROMPT_HEAD, target_language, example_marker='[예시', resume_marker='이제 아래 문단')}"
        f"[대상 언어]: {target_language_profile(target_language)['name']}\n"
        f"[문화반영범위 설정]: {level}\n"
        f"[씬 컨텍스트]: relationship_tag={relationship}, mood_tag={mood}\n"
        f"{body}"
    )


PARAGRAPH_TRANSLATION_PROMPT_HEAD = """당신은 한국 웹소설을 __TARGET_AUDIENCE__ 투고용으로 번역하는 전문 번역가입니다.
아래 문단을 지정된 설정에 따라 자연스러운 __TARGET_LANGUAGE__(으)로 번역하세요.

"""

PARAGRAPH_TRANSLATION_PROMPT_TAIL = """
[번역 시 지켜야 할 것]
""" + GLOSSARY_TERM_LOCK_RULE + """
1. 직역이 아니라 __TARGET_AUDIENCE__ 소설처럼 자연스럽게 읽히는 문장으로 만드세요.
   다만 위 용어 고정 규칙과 충돌하면 용어 고정을 따릅니다.
2. 원문의 정보를 임의로 추가하거나 생략하지 마세요.
3. 의역했거나 뉘앙스를 다른 방식으로 옮긴 부분이 있다면 translation_notes에 기록하세요.
   단순 직역인 부분은 굳이 기록하지 않아도 됩니다.
   """ + TRANSLATION_NOTES_LANGUAGE_RULE + """
4. 인칭·성·수·격식 표현(예: __REFERENCE_EXAMPLES__)이 직전 문맥과 일치하는지 확인하세요.

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

SHORT_PARAGRAPH_TRANSLATION_INSTRUCTION = """
[짧은 문단 지침]
이 문단이 의성어, 의태어, 감탄사처럼 짧더라도 translated_text를 절대 빈 문자열로
두지 말고, 대상 언어의 자연스러운 대응 표현(예: 짧은 미소를 나타내는 동작 묘사)을
반드시 채워라. 대응 표현이 애매하면 원문 발음을 대상 언어의 음가에 맞게 전사하거나 가장 근접한
의미의 단어를 사용하라.
"""

PARAGRAPH_SHORT_SOURCE_LIMIT = 10


class ParagraphTranslationSettings(TypedDict, total=False):
    target_language: str
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
    target_language: object | None = None,
) -> str:
    """Insert style, glossary, scene context, and paragraph text into the translation prompt.

    `settings` is a single dict (see ParagraphTranslationSettings). Keep
    `previous_context_summary` as already-confirmed target-language translation, never
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
    language = target_language or _setting(settings, "target_language") or "en"
    extra = ""
    if len(body) <= PARAGRAPH_SHORT_SOURCE_LIMIT:
        extra = SHORT_PARAGRAPH_TRANSLATION_INSTRUCTION
    return (
        f"{_render_language_tokens(PARAGRAPH_TRANSLATION_PROMPT_HEAD, language)}"
        f"[대상 언어]: {target_language_profile(language)['name']}\n"
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
        f"[직전 문맥 — 이미 확정된 대상 언어 번역입니다. 인칭과 시제의 연속성을 위해 참고하세요, 번역 대상 아님]\n"
        f"{previous}\n"
        f"{extra}"
        f"{_omit_non_target_examples(PARAGRAPH_TRANSLATION_PROMPT_TAIL, language, example_marker='[예시]', resume_marker='이제 아래 문단')}"
        f"{body}"
    )


PARAGRAPH_TRANSLATION_BATCH_PROMPT_HEAD = """당신은 한국 웹소설을 대상 언어의 자연스러운 소설로 번역하는 전문 번역가입니다.
다음은 소설 한 회차 중 연속된 문단들입니다. 각 문단을 대상 언어로 번역하세요.
문단 간 맥락(등장인물 관계, 시점, 어투)을 참고해 전체적으로 일관된 어투를 유지하세요.

[반드시 지켜야 할 것]
""" + GLOSSARY_TERM_LOCK_RULE + """
1. 각 문단의 id와 입력 순서를 그대로 유지하세요.
2. 입력된 모든 문단에 대해 정확히 하나씩 응답하세요. 문단을 합치거나 나누지 마세요.
3. 사건과 의미를 추가·삭제·변경하지 마세요.
4. translated_text에는 한국어 원문을 넣지 말고 대상 언어 번역문만 쓰세요. JSON만 반환하세요.
5. translation_notes에는 의역하거나 문화적 표현을 조정한 경우만 간단히 기록하세요.
   """ + TRANSLATION_NOTES_LANGUAGE_RULE + """

"""

PARAGRAPH_TRANSLATION_BATCH_PROMPT_TAIL = """
[출력 형식 — JSON만 출력]
{
  "paragraphs": [
    {
      "id": 101,
      "translated_text": "...",
      "translation_notes": []
    }
  ]
}

이제 아래 문단을 같은 id와 순서로 모두 번역하세요.
"""


def build_paragraph_translation_batch_prompt(
    paragraphs: Sequence[Mapping[str, object]],
    settings: Mapping[str, object] | None = None,
    target_language: object | None = None,
) -> str:
    """Build one contextual first-pass translation request for ordered segments."""
    items = list(paragraphs or [])
    tense = _setting(settings, "tense")
    voices = _setting(settings, "character_voices")
    nouns = _setting(settings, "proper_nouns_confirmed")
    level = _setting(settings, "culture_localization_level")
    formatting = _setting(settings, "narrative_formatting_rules")
    previous = _setting(settings, "previous_context_summary")
    target = target_language or _setting(settings, "target_language") or "en"
    target_name = target_language_profile(target)["name"]
    blocks: list[str] = []
    for item in items:
        segment_id = int(item.get("id") or 0)
        source = str(item.get("source_text") or "")
        relationship = str(item.get("relationship_tag") or "")
        mood = str(item.get("mood_tag") or "")
        short = (
            "\n[이 문단은 10자 이하입니다. 의성어·의태어·감탄사라도 "
            "translated_text를 비우지 말고 자연스러운 대응 표현을 반드시 채우세요.]"
            if len(source) <= PARAGRAPH_SHORT_SOURCE_LIMIT
            else ""
        )
        blocks.append(
            f"<<<SEGMENT id={segment_id}>>>\n"
            f"[scene relationship_tag={relationship}, mood_tag={mood}]"
            f"{short}\n{source}"
        )
    body = "\n\n".join(blocks)
    return (
        f"{PARAGRAPH_TRANSLATION_BATCH_PROMPT_HEAD}"
        f"[대상 언어]: {target_name} ({normalize_target_language(target)})\n"
        f"[스타일가이드]\n- 시제: {tense}\n- 인물별 어조: {voices}\n\n"
        f"[확정된 고유명사 — 반드시 이 표기를 그대로 사용하세요]\n{nouns}\n\n"
        f"[문화반영범위]: {level}\n"
        f"[대사 표기 규칙]\n{formatting}\n\n"
        f"[직전 확정 번역 문맥 — 번역 대상 아님]\n{previous}\n"
        f"{PARAGRAPH_TRANSLATION_BATCH_PROMPT_TAIL}"
        f"{body}"
    )


CHAPTER_POLISH_PROMPT_HEAD = """당신은 __TARGET_AUDIENCE__ 출판 편집자입니다.
윤문은 문장을 조금 더 자연스럽게 다듬는 작업이기도 하지만, 그보다 먼저
1차 번역에서 같은 대상이 여러 단어로 흔들린 것을 찾아 바로잡는 마지막 방어선입니다.
""" + POLISH_TERM_CONSISTENCY_MISSION + """

다음은 소설 한 회차의 __TARGET_LANGUAGE__ 번역문입니다. 문단 간 흐름, 대명사 지칭,
어색한 연결을 다듬되, 문단 순서와 개수, 각 문단이 담긴 사건/의미는 절대 바꾸지 마세요.
각 문단별로 다듬어진 버전을 반환하세요.

[지켜야 할 것]
1. 입력 문단 개수와 출력 문단 개수는 반드시 같습니다. 문단을 합치거나 나누거나 순서를 바꾸지 마세요.
2. 구분선만 있는 문단(====, ---- 등)은 글자를 그대로 두세요.
3. 의미를 바꾸지 마세요. 사건, 대사 내용, 고유명사·조어 표기는 유지·통일하세요.
4. 같은 문장 종결이 반복되거나 대명사/주어가 과도하게 되풀이되면 호흡을 다듬으세요.
   다만 특정 사물·개념을 가리키는 조어를 동의어로 바꿔 반복을 숨기지는 마세요.
5. 한국어 원문은 주어지지 않았습니다. 번역문만 보고 판단하세요.

"""

CHAPTER_POLISH_PROMPT_TAIL = """
[출력 형식 — JSON만 출력]
{
  "paragraphs": [
    {"index": 1, "polished_text": "..."},
    {"index": 2, "polished_text": "..."}
  ]
}

[예시]
입력:
<<<PARAGRAPH 1>>>
Iona stood in front of the bakery. The bakery was closed. Iona sighed.
<<<PARAGRAPH 2>>>
She looked up at the chimney. The chimney was quiet. She felt disappointed.
출력:
{
  "paragraphs": [
    {
      "index": 1,
      "polished_text": "Iona stood before the shuttered bakery and let out a sigh."
    },
    {
      "index": 2,
      "polished_text": "She looked up at the quiet chimney, disappointment settling over her."
    }
  ]
}

[예시2 — 같은 대상을 가리키는 조어가 흔들린 경우]
입력:
<<<PARAGRAPH 1>>>
The restraint cord snapped tight around her wrist.
<<<PARAGRAPH 2>>>
She clawed at the binding cord, but the restraint cord only pulled tighter.
출력:
{
  "paragraphs": [
    {
      "index": 1,
      "polished_text": "The restraint cord snapped tight around her wrist."
    },
    {
      "index": 2,
      "polished_text": "She clawed at the restraint cord, but it only pulled tighter."
    }
  ]
}

이제 아래 회차를 같은 문단 개수와 순서로 윤문하세요.
"""


def format_chapter_polish_paragraphs(paragraphs: Sequence[object] | None) -> str:
    items = list(paragraphs or [])
    parts: list[str] = []
    for index, item in enumerate(items, start=1):
        text = "" if item is None else str(item)
        parts.append(f"<<<PARAGRAPH {index}>>>\n{text}")
    return "\n\n".join(parts)


def build_chapter_polish_prompt(
    paragraphs: Sequence[object] | None,
    settings: Mapping[str, object] | None = None,
    *,
    target_start: int | None = None,
    target_end: int | None = None,
    target_language: object = "en",
) -> str:
    """Build a translation-only chapter polish prompt. Paragraph count must be preserved."""
    items = list(paragraphs or [])
    body = format_chapter_polish_paragraphs(items)
    tense = _setting(settings, "tense")
    voices = _setting(settings, "character_voices")
    nouns = _setting(settings, "proper_nouns_confirmed")
    glossary = (
        "[확정 용어집 — 있으면 이 표기로 통일하세요. 없어도 위 핵심 임무는 반드시 수행하세요]\n"
        f"{nouns}\n"
        if str(nouns).strip()
        else "[확정 용어집 없음 — 등록 여부와 무관하게 위 핵심 임무를 반드시 수행하세요]\n"
    )
    first = max(1, int(target_start or 1))
    last = min(len(items), int(target_end or len(items))) if items else 0
    target = (
        f"[이번 응답 대상]\n전체 {len(items)}개 문단을 모두 문맥으로 읽되, "
        f"이번 응답에는 index {first}~{last} 문단만 반환하세요. "
        "index 번호는 전체 회차 기준 번호를 유지하세요.\n"
        if items and (first != 1 or last != len(items))
        else ""
    )
    return (
        f"{_render_language_tokens(CHAPTER_POLISH_PROMPT_HEAD, target_language)}"
        f"[대상 언어]: {target_language_profile(target_language)['name']}\n"
        f"[스타일가이드]\n"
        f"- 시제: {tense}\n"
        f"- 인물별 어조: {voices}\n"
        f"{glossary}"
        f"{target}"
        f"{_omit_non_target_examples(CHAPTER_POLISH_PROMPT_TAIL, target_language, example_marker='[예시]', resume_marker='이제 아래 회차')}"
        f"{body}"
    )


def build_polish_prompt(
    chapter_text: str | Sequence[object] | None,
    settings: Mapping[str, object] | None = None,
    target_language: object = "en",
) -> str:
    """Back-compat wrapper: a string is treated as a single paragraph."""
    if isinstance(chapter_text, (list, tuple)):
        paragraphs: Sequence[object] = chapter_text
    elif chapter_text is None:
        paragraphs = []
    else:
        paragraphs = [str(chapter_text)]
    return build_chapter_polish_prompt(
        paragraphs, settings, target_language=target_language
    )


SUBMISSION_QUERY_PROMPT_HEAD = """당신은 한국 웹소설을 __TARGET_AUDIENCE__ 에이전트/출판사에 투고하기 위해 자료를 준비하는 편집 보조자입니다.
아래는 이 작품의 한국어 시놉시스입니다. 이것을 대상 언어권 투고 관행에 맞는 로그라인과 시놉시스로
번역/재구성하세요.
[대상 시장 지침]
__SUBMISSION_GUIDANCE__
[로그라인 작성 기준]
- 간결한 1~2문장으로 작성합니다
- 주인공, 주인공이 원하는 것(목표), 그것을 막는 장애물, 이 작품만의 독특한 훅(장르 관습을
  비트는 지점)이 담겨야 합니다
- 결말은 밝히지 않습니다
[시놉시스 작성 기준]
- 해당 언어권 에이전트의 일반적인 샘플 시놉시스 분량을 따릅니다
- 기승전결 전체를 요약하되, 세부 사건을 나열하지 말고 핵심 갈등의 전개만 따라가세요
- 결말(반전 포함)까지 명시합니다 — 투고용 시놉시스는 스포일러를 감추지 않는 것이 관행입니다
- 인칭·시제·인물명 강조 방식은 위 대상 시장 지침을 따릅니다

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
    target_language: object = "en",
) -> str:
    """Insert confirmed proper nouns and Korean synopsis into the query-package prompt."""
    body = "" if chapter_text is None else str(chapter_text)
    nouns = _setting(settings, "proper_nouns_confirmed")
    culture = _setting(settings, "culture_localization_level")
    return (
        f"{_render_language_tokens(SUBMISSION_QUERY_PROMPT_HEAD, target_language)}"
        f"[대상 언어]: {target_language_profile(target_language)['name']}\n"
        f"[문화반영범위]: {culture}\n"
        f"[확정된 고유명사 — 반드시 이 표기를 사용하세요]\n"
        f"{nouns}\n"
        f"{_omit_non_target_examples(SUBMISSION_QUERY_PROMPT_TAIL, target_language, example_marker='[예시]', resume_marker='이제 아래 시놉시스')}"
        f"{body}"
    )


WORD_CONTEXT_PROMPT_HEAD = """당신은 "토리"입니다. __TARGET_AUDIENCE__ 투고용으로 번역된 한국 웹소설의 특정 단어 선택에 대해
간단히 설명합니다.
"""

WORD_CONTEXT_PROMPT_RULES = """[답변 원칙]
1. 이 단어가 원문의 어떤 뉘앙스/느낌을 살리기 위해 선택되었는지 1~2문장으로 설명하세요.
2. 단순 사전적 정의를 반복하지 마세요 — "왜 이 단어를 골랐는지"에 집중하세요.
3. 특별한 의역 의도가 없는 평범한 단어라면, 그렇다고 짧게 말해도 됩니다.
4. 장황하게 설명하지 마세요. 2문장 이내로 답하세요.
5. explanation은 반드시 원고 원문 언어(한국어)로 작성하세요. 대상 언어로 쓰지 마세요.
   원문 표현이나 번역 표현을 따옴표로 인용하는 것은 괜찮습니다.
[출력 형식 — JSON만 출력]
{
  "explanation": "..."
}
[예시1 — 의역 의도가 있는 경우]
원문: "이오나는 서둘러 집으로 발길을 옮겼다."
번역: "Iona hurried home."
클릭한 단어: "hurried"
출력:
{
  "explanation": "원문의 '발길을 옮겼다'는 문학적 표현을 직역하면 어색해서, 서두르는 행동 자체를 담은 'hurried'로 압축했어요. 눈 내리는 날씨에 서둘러 귀가하는 다급함이 자연스럽게 전달돼요."
}
[예시2 — 특별한 의도 없는 경우]
원문: "이오나가 작은 탄식을 뱉었다."
번역: "Iona let out a small sigh."
클릭한 단어: "small"
출력:
{
  "explanation": "이 부분은 특별한 의역 없이 원문의 '작은'을 그대로 옮긴 표현이에요."
}
이제 아래 질문에 답하세요.
"""


def build_word_context_prompt(
    segment_text_info: Mapping[str, object] | str | None,
    word: object,
    existing_translation_notes: object = None,
    target_language: object = "en",
) -> str:
    """Fill paragraph text, clicked word, and existing notes into the word-context prompt."""
    if isinstance(segment_text_info, Mapping):
        source = "" if segment_text_info.get("source_text") is None else str(
            segment_text_info.get("source_text")
        )
        translated = "" if segment_text_info.get("translated_text") is None else str(
            segment_text_info.get("translated_text")
        )
    else:
        source = "" if segment_text_info is None else str(segment_text_info)
        translated = ""
    token = "" if word is None else str(word)
    notes = (
        ""
        if existing_translation_notes is None
        else str(existing_translation_notes)
    )
    return (
        f"{_render_language_tokens(WORD_CONTEXT_PROMPT_HEAD, target_language)}"
        f"[대상 언어]: {target_language_profile(target_language)['name']}\n"
        f"{_omit_non_target_examples(WORD_CONTEXT_PROMPT_RULES, target_language, example_marker='[예시1', resume_marker='이제 아래 질문')}"
        f"[이 문단의 정보]\n"
        f"원문: {source}\n"
        f"현재 번역: {translated}\n"
        f"사용자가 클릭한 단어: {token}\n"
        f"[이미 기록된 의역 노트 — 있으면 참고만, 중복 설명하지 마세요]\n"
        f"{notes}\n"
    )


TRANSLATION_QA_PROMPT_HEAD = """당신은 "토리"입니다. 한국 웹소설을 __TARGET_AUDIENCE__ 투고용으로 번역하는 작업을 돕는 AI
편집 파트너로서, 작가(사용자)의 질문에 답합니다.
"""

TRANSLATION_QA_PROMPT_RULES = """[답변 원칙]
1. 사용자가 "왜 이렇게 번역했는지" 물으면, 그 부분의 번역 근거를 구체적으로 설명하세요.
   막연한 설명 대신 원문의 어떤 뉘앙스를 살리려 했는지 짚어주세요.
2. 사용자가 "이렇게 바꿔줘" 같은 수정 요청을 하면, 설명만 하지 말고 실제 대안 문장을
   1~2개 제시하세요. 요청한 방향(더 슬프게, 더 격식있게 등)이 왜 그 문장으로
   구현되는지 짧게 덧붙이세요.
3. 원문에 없는 내용을 새로 지어내지 마세요. 번역 뉘앙스 조정이지 창작이 아닙니다.
4. 답변(response)은 반드시 한국어로 작성하세요. 대상 언어 표현은 인용만 하고,
   suggested_revision만 대상 언어로 쓰세요. 불필요한 서두나 격식 있는 인사는 생략하세요.
5. 이 문단 범위를 벗어난 질문(작품 전체 방향, 다른 챕터 등)이면, 지금은 이 문단에
   집중된 대화라는 걸 안내하고 범위 내에서 답할 수 있는 부분만 답하세요.
[출력 형식 — JSON만 출력]
{
  "response": "...",
  "suggested_revision": "..."
}
suggested_revision 값은 수정 제안이 있으면 대안 문장, 없으면 빈 문자열입니다.
[예시1 — 수정 요청]
드래그한 부분: "Have you eaten, babe?"
사용자 질문: "이 문장 조금 더 슬프게 바꿀 수 있어? 사실 이 장면에서 여자친구가 아파서 걱정하는 거거든"
출력:
{
  "response": "아, 걱정하는 뉘앙스라면 가벼운 애칭보다 직접적인 안부 확인이 더 어울릴 것 같아요. 'babe' 대신 이름을 부르면서 조금 더 조심스러운 어조로 바꿔봤어요.",
  "suggested_revision": "Have you eaten anything today? You should, even if it's just a little."
}
[예시2 — 이유를 묻는 질문]
드래그한 부분: "Did you have dinner."
사용자 질문: "왜 물음표를 없앤 거야?"
출력:
{
  "response": "원문이 냉랭한 가족 갈등 장면이라, 의문형보다 건조한 평서문이 추궁하는 듯한 거리감을 더 잘 살린다고 판단했어요. 물음표를 넣으면 오히려 부드러운 인상이 될 수 있어서요.",
  "suggested_revision": ""
}
이제 아래 사용자 질문에 답하세요.
"""


def build_translation_qa_prompt(
    user_question: str,
    settings: Mapping[str, object] | None = None,
    target_language: object = "en",
) -> str:
    """Fill paragraph context and the writer's question into the Tory QA prompt."""
    question = "" if user_question is None else str(user_question)
    source_text = _setting(settings, "source_text")
    translated_text = _setting(settings, "translated_text")
    dragged_text = _setting(settings, "dragged_text")
    tense = _setting(settings, "tense")
    voices = _setting(settings, "character_voices")
    relationship = _setting(settings, "relationship_tag")
    mood = _setting(settings, "mood_tag")
    culture = _setting(settings, "culture_localization_level")
    history = _setting(settings, "chat_history")
    return (
        f"{_render_language_tokens(TRANSLATION_QA_PROMPT_HEAD, target_language)}"
        f"[대상 언어]: {target_language_profile(target_language)['name']}\n"
        f"[이 문단의 정보]\n"
        f"원문: {source_text}\n"
        f"현재 번역: {translated_text}\n"
        f"사용자가 지목한 부분: {dragged_text}\n"
        f"[스타일가이드]\n"
        f"- 시제: {tense}\n"
        f"- 인물별 어조: {voices}\n"
        f"[씬 컨텍스트]: relationship_tag={relationship}, mood_tag={mood}\n"
        f"[문화반영범위]: {culture}\n"
        f"[지난 대화 기록]\n"
        f"{history}\n"
        f"{_omit_non_target_examples(TRANSLATION_QA_PROMPT_RULES, target_language, example_marker='[예시1', resume_marker='이제 아래 사용자 질문')}"
        f"{question}"
    )


def parse_translation_qa_output(raw: str | None) -> tuple[str, str]:
    """Return (response, suggested_revision) from a Tory QA JSON reply."""
    text = "" if raw is None else str(raw).strip()
    if not text:
        return "", ""
    cleaned = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    data: dict | None = None
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            data = parsed
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                data = parsed
    if not data:
        return text, ""
    response = str(data.get("response") or "").strip()
    revision = str(
        data.get("suggested_revision") or data.get("suggested_revision") or ""
    ).strip()
    if not response:
        return text, revision
    return response, revision


build_translation_chat_prompt = build_translation_qa_prompt
