# -*- coding: utf-8 -*-
"""Prompt builders for submission-oriented translation."""

from __future__ import annotations

import json
import unittest

import translation_prompts


CHAPTER = (
    '모나어는 "", 파가몬어는 [], 텔레파시는 -로 표기합니다.\n'
    '"안녕." [크라쉬.] -들리니.-'
)


class NarrativeFormattingPromptTests(unittest.TestCase):
    def test_inserts_chapter_text_at_the_end(self) -> None:
        prompt = translation_prompts.build_narrative_formatting_prompt(CHAPTER)
        self.assertTrue(prompt.startswith("당신은 한국 웹소설을 영어권 투고용으로"))
        self.assertTrue(prompt.endswith(CHAPTER))
        self.assertIn("이제 아래 원고를 같은 방식으로 처리하세요:\n" + CHAPTER, prompt)
        self.assertNotIn("[원문 삽입]", prompt)

    def test_keeps_json_example_braces(self) -> None:
        prompt = translation_prompts.build_narrative_formatting_prompt("본문 {중괄호} 테스트")
        self.assertIn('"detected_conventions"', prompt)
        self.assertIn('"recommended_handling"', prompt)
        self.assertIn("본문 {중괄호} 테스트", prompt)

    def test_empty_chapter_still_builds(self) -> None:
        prompt = translation_prompts.build_narrative_formatting_prompt("")
        self.assertIn("preserve_with_note", prompt)
        self.assertTrue(prompt.endswith("처리하세요:\n"))
        none_prompt = translation_prompts.build_narrative_formatting_prompt(None)  # type: ignore[arg-type]
        self.assertEqual(none_prompt, prompt)


class SceneSplitPromptTests(unittest.TestCase):
    def test_inserts_chapter_text_at_the_end(self) -> None:
        prompt = translation_prompts.build_scene_split_prompt(CHAPTER)
        self.assertTrue(prompt.startswith("당신은 한국 웹소설을 영어권 투고용으로"))
        self.assertTrue(prompt.endswith(CHAPTER))
        self.assertIn("이제 아래 원문을 같은 방식으로 처리하세요:\n" + CHAPTER, prompt)
        self.assertNotIn("[원문 삽입]", prompt)

    def test_keeps_json_example_braces(self) -> None:
        prompt = translation_prompts.build_scene_split_prompt("본문 {중괄호} 테스트")
        self.assertIn('"scenes"', prompt)
        self.assertIn('"relationship_tag"', prompt)
        self.assertIn('"mood_tag"', prompt)
        self.assertIn('"situation_note"', prompt)
        self.assertIn("본문 {중괄호} 테스트", prompt)

    def test_empty_chapter_still_builds(self) -> None:
        prompt = translation_prompts.build_scene_split_prompt("")
        self.assertIn("scene_order", prompt)
        self.assertTrue(prompt.endswith("처리하세요:\n"))
        none_prompt = translation_prompts.build_scene_split_prompt(None)  # type: ignore[arg-type]
        self.assertEqual(none_prompt, prompt)


class ProperNounFitPromptTests(unittest.TestCase):
    def test_inserts_chapter_text_at_the_end(self) -> None:
        prompt = translation_prompts.build_proper_noun_fit_prompt(CHAPTER)
        self.assertTrue(prompt.startswith("당신은 한국 웹소설을 영어권 투고용으로"))
        self.assertTrue(prompt.endswith(CHAPTER))
        self.assertIn(
            "이제 아래 원문에서 고유명사를 찾아 같은 방식으로 처리하세요:\n" + CHAPTER,
            prompt,
        )
        self.assertNotIn("[원문 삽입]", prompt)

    def test_keeps_json_example_braces(self) -> None:
        prompt = translation_prompts.build_proper_noun_fit_prompt("본문 {중괄호} 테스트")
        self.assertIn('"proper_nouns"', prompt)
        self.assertIn('"fit_judgment"', prompt)
        self.assertIn('"suggested_alternatives"', prompt)
        self.assertIn("본문 {중괄호} 테스트", prompt)

    def test_empty_chapter_still_builds(self) -> None:
        prompt = translation_prompts.build_proper_noun_fit_prompt("")
        self.assertIn("does_not_fit", prompt)
        self.assertTrue(prompt.endswith("처리하세요:\n"))
        none_prompt = translation_prompts.build_proper_noun_fit_prompt(None)  # type: ignore[arg-type]
        self.assertEqual(none_prompt, prompt)

    def test_judges_existing_index_terms_when_provided(self) -> None:
        prompt = translation_prompts.build_proper_noun_fit_prompt(
            CHAPTER, existing_index_terms=["이오나", "아르카디아"]
        )
        self.assertIn("반드시 같은 기준으로 판정하세요", prompt)
        self.assertIn("- 이오나", prompt)
        self.assertIn("- 아르카디아", prompt)
        self.assertTrue(prompt.endswith(CHAPTER))

    def test_without_existing_terms_scans_everything(self) -> None:
        prompt = translation_prompts.build_proper_noun_fit_prompt(CHAPTER)
        self.assertNotIn("건너뛰세요", prompt)
        self.assertNotIn("다시 판정하지 마세요", prompt)
        self.assertNotIn("설정집에 이미 있는 고유명사", prompt)
        self.assertIn(
            "이제 아래 원문에서 고유명사를 찾아 같은 방식으로 처리하세요:\n" + CHAPTER,
            prompt,
        )

    def test_index_block_requires_judgment_and_still_finds_new_terms(self) -> None:
        prompt = translation_prompts.build_proper_noun_fit_prompt(
            CHAPTER, existing_index_terms=["이오나", "아르카디아"]
        )
        self.assertIn(
            "[설정집에 이미 있는 고유명사 — 아래 이름들도 반드시 같은 기준으로 판정하세요]",
            prompt,
        )
        self.assertIn("설정집 이름은 빠짐없이 판정하세요", prompt)
        self.assertIn("위 목록에 없는 고유명사(단역 이름, 아이템명, 작가 조어,", prompt)
        self.assertIn("- 이오나", prompt)
        self.assertIn("- 아르카디아", prompt)
        self.assertNotIn("건너뛰세요", prompt)
        self.assertIn(
            "이제 아래 원문에서 고유명사를 찾아 같은 방식으로 처리하세요:\n" + CHAPTER,
            prompt,
        )

    def test_judges_five_criteria_including_period_and_genre_tone(self) -> None:
        prompt = translation_prompts.build_proper_noun_fit_prompt(CHAPTER)
        self.assertIn("[판단 기준 — 이 5가지 관점에서 판단합니다]", prompt)
        self.assertIn("1. 발음 부자연스러움", prompt)
        self.assertIn("2. 의도치 않은 의미", prompt)
        self.assertIn("3. 기존 유명인/브랜드와 혼동", prompt)
        self.assertIn("4. 희화화 위험", prompt)
        self.assertIn("5. 시대감/장르 톤 부적합", prompt)
        self.assertIn("이 이름이 서사적으로 의도된 것인지 원문 맥락에서 먼저 판단하세요", prompt)
        self.assertIn("캐릭터가 실제로 노년/구세대 인물인가", prompt)
        self.assertIn("촌스러운 이름이 개그 요소, 콤플렉스, 플롯 장치로 쓰이고 있는가", prompt)
        self.assertNotIn("이 4가지 관점에서만 판단합니다", prompt)

    def test_period_tone_few_shots_include_soonja_cases(self) -> None:
        prompt = translation_prompts.build_proper_noun_fit_prompt(CHAPTER)
        self.assertIn('"source_term": "순자"', prompt)
        self.assertIn("20대 세련된 도시 여성", prompt)
        self.assertIn('"suggested_alternatives": ["Sian", "Iris", "Noelle"]', prompt)
        self.assertIn("80대 할머니", prompt)
        self.assertIn("서사적으로 의도된 이름으로 보이므로 그대로 유지하세요", prompt)

    def test_alternative_names_keep_original_feel_and_genre_tone(self) -> None:
        prompt = translation_prompts.build_proper_noun_fit_prompt(CHAPTER)
        self.assertIn("원래 이름이 주던 어감(우아함/발랄함/신비로움/이국적 느낌 등)", prompt)
        self.assertIn("캐릭터의 장르·설정 톤을 유지하는 방향으로 추천하세요", prompt)
        self.assertIn("이국적이고 신비로운 느낌", prompt)

    def test_includes_repeated_coined_terms_as_proper_noun_candidates(self) -> None:
        prompt = translation_prompts.build_proper_noun_fit_prompt(CHAPTER)
        self.assertIn("[찾을 대상]", prompt)
        self.assertIn("작가 조어(사전에 없는 표현 포함)도 고유명사 후보로 포함하세요", prompt)
        self.assertIn("짧은 구간에서 여러 번 반복되는 표현은 우선적으로 후보에 넣으세요", prompt)
        self.assertIn("일반 동사·형용사·흔한 명사", prompt)
        self.assertIn('"source_term": "구속줄"', prompt)
        self.assertIn("작가 조어", prompt)


class CultureMarkerPromptTests(unittest.TestCase):
    def test_inserts_chapter_text_and_context(self) -> None:
        prompt = translation_prompts.build_culture_marker_prompt(
            CHAPTER, "tight", "연인-다정", "다정함"
        )
        self.assertTrue(prompt.startswith("당신은 한국 웹소설을 영어권 투고용으로"))
        self.assertTrue(prompt.endswith(CHAPTER))
        self.assertIn("[문화반영범위 설정]: tight\n", prompt)
        self.assertIn(
            "[씬 컨텍스트]: relationship_tag=연인-다정, mood_tag=다정함\n" + CHAPTER,
            prompt,
        )
        self.assertNotIn("[원문 삽입]", prompt)
        self.assertNotIn("{culture_localization_level}", prompt)
        self.assertNotIn("{relationship_tag}", prompt)
        self.assertNotIn("{mood_tag}", prompt)

    def test_keeps_json_example_braces(self) -> None:
        prompt = translation_prompts.build_culture_marker_prompt(
            "본문 {중괄호} 테스트", "moderate", "친구-가벼움", "당황"
        )
        self.assertIn('"culture_markers"', prompt)
        self.assertIn('"localization_level_applied"', prompt)
        self.assertIn('"translated_phrase"', prompt)
        self.assertIn("본문 {중괄호} 테스트", prompt)

    def test_empty_chapter_still_builds(self) -> None:
        prompt = translation_prompts.build_culture_marker_prompt(
            "", "as_is", "가족-그리움", "애틋함"
        )
        self.assertIn("doenjang jjigae", prompt)
        self.assertTrue(prompt.endswith("mood_tag=애틋함\n"))
        none_prompt = translation_prompts.build_culture_marker_prompt(
            None, "as_is", "가족-그리움", "애틋함"  # type: ignore[arg-type]
        )
        self.assertEqual(none_prompt, prompt)


TRANSLATION_KWARGS = {
    "tense": "past",
    "character_voices": "이오나=캐주얼, 메리=격식",
    "proper_nouns_confirmed": "이오나→Iona, 메리→Meri",
    "culture_localization_level": "moderate",
    "relationship_tag": "이웃-정보교환",
    "mood_tag": "걱정/궁금함",
    "narrative_formatting_rules": '모나어="", 텔레파시=—',
    "previous_context_summary": "(없음, 챕터 첫 문단)",
}


class ParagraphTranslationPromptTests(unittest.TestCase):
    def test_inserts_chapter_text_and_settings(self) -> None:
        prompt = translation_prompts.build_paragraph_translation_prompt(
            CHAPTER, TRANSLATION_KWARGS
        )
        self.assertTrue(prompt.startswith("당신은 한국 웹소설을 영어권 투고용으로"))
        self.assertTrue(prompt.endswith(CHAPTER))
        self.assertIn("- 시제: past\n", prompt)
        self.assertIn("- 인물별 어조: 이오나=캐주얼, 메리=격식\n", prompt)
        self.assertIn("이오나→Iona, 메리→Meri", prompt)
        self.assertIn("[문화반영범위]: moderate\n", prompt)
        self.assertIn(
            "[이 씬의 컨텍스트]: relationship_tag=이웃-정보교환, mood_tag=걱정/궁금함\n",
            prompt,
        )
        self.assertIn('모나어="", 텔레파시=—', prompt)
        self.assertIn("(없음, 챕터 첫 문단)", prompt)
        self.assertIn("이제 아래 문단을 번역하세요.\n" + CHAPTER, prompt)
        self.assertNotIn("[원문 삽입]", prompt)
        self.assertNotIn("{tense}", prompt)
        self.assertNotIn("{character_voices}", prompt)
        self.assertNotIn("{proper_nouns_confirmed}", prompt)
        self.assertNotIn("{culture_localization_level}", prompt)
        self.assertNotIn("{relationship_tag}", prompt)
        self.assertNotIn("{mood_tag}", prompt)
        self.assertNotIn("{narrative_formatting_rules}", prompt)
        self.assertNotIn("{previous_context_summary}", prompt)

    def test_keeps_json_example_braces(self) -> None:
        prompt = translation_prompts.build_paragraph_translation_prompt(
            "본문 {중괄호} 테스트", TRANSLATION_KWARGS
        )
        self.assertIn('"translated_text"', prompt)
        self.assertIn('"translation_notes"', prompt)
        self.assertIn("본문 {중괄호} 테스트", prompt)

    def test_empty_chapter_still_builds(self) -> None:
        prompt = translation_prompts.build_paragraph_translation_prompt(
            "", TRANSLATION_KWARGS
        )
        self.assertIn("let out a small sigh", prompt)
        self.assertTrue(prompt.endswith("번역하세요.\n"))
        none_prompt = translation_prompts.build_paragraph_translation_prompt(
            None, TRANSLATION_KWARGS  # type: ignore[arg-type]
        )
        self.assertEqual(none_prompt, prompt)

    def test_empty_settings_still_builds(self) -> None:
        prompt = translation_prompts.build_paragraph_translation_prompt(CHAPTER)
        self.assertTrue(prompt.endswith(CHAPTER))
        self.assertIn("- 시제: \n", prompt)
        self.assertIn("이미 확정된 대상 언어 번역입니다", prompt)

    def test_short_source_adds_never_empty_instruction(self) -> None:
        short = translation_prompts.build_paragraph_translation_prompt("싱긋")
        long = translation_prompts.build_paragraph_translation_prompt(CHAPTER)
        self.assertIn("translated_text를 절대 빈 문자열로", short)
        self.assertIn("[짧은 문단 지침]", short)
        self.assertNotIn("translated_text를 절대 빈 문자열로", long)
        self.assertTrue(short.endswith("싱긋"))

    def test_batch_prompt_keeps_ids_order_context_and_short_instruction(self) -> None:
        prompt = translation_prompts.build_paragraph_translation_batch_prompt(
            [
                {
                    "id": 17,
                    "source_text": "싱긋",
                    "relationship_tag": "초면",
                    "mood_tag": "설렘",
                },
                {
                    "id": 19,
                    "source_text": "그녀가 우산을 내밀었다.",
                    "relationship_tag": "초면",
                    "mood_tag": "설렘",
                },
            ],
            {**TRANSLATION_KWARGS, "target_language": "en"},
        )
        self.assertIn("[대상 언어]: 영어 (en)", prompt)
        self.assertIn("<<<SEGMENT id=17>>>\n", prompt)
        self.assertIn("<<<SEGMENT id=19>>>\n", prompt)
        self.assertLess(prompt.index("id=17"), prompt.index("id=19"))
        self.assertIn("이 문단은 10자 이하입니다", prompt)
        self.assertIn("relationship_tag=초면, mood_tag=설렘", prompt)
        self.assertIn('"paragraphs"', prompt)
        self.assertIn("(없음, 챕터 첫 문단)", prompt)
        self.assertIn("note 설명 문장은 반드시 원고 원문 언어(한국어)", prompt)
        self.assertIn("translated_text에는 한국어 원문을 넣지 말고", prompt)
        self.assertNotIn("출력에는 한국어 원문을 포함하지 말고", prompt)

    def test_translation_notes_must_be_korean_for_all_target_languages(self) -> None:
        marker = "note 설명 문장은 반드시 원고 원문 언어(한국어)"
        for language in ("en", "es", "fr"):
            single = translation_prompts.build_paragraph_translation_prompt(
                CHAPTER, TRANSLATION_KWARGS, target_language=language
            )
            batch = translation_prompts.build_paragraph_translation_batch_prompt(
                [{"id": 1, "source_text": CHAPTER}],
                TRANSLATION_KWARGS,
                target_language=language,
            )
            word = translation_prompts.build_word_context_prompt(
                {"source_text": CHAPTER, "translated_text": "It rained."},
                "rained",
                target_language=language,
            )
            chat = translation_prompts.build_translation_chat_prompt(
                "왜 이렇게 번역했어?",
                {"translated_text": "It rained."},
                target_language=language,
            )
            self.assertIn(marker, single)
            self.assertIn(marker, batch)
            self.assertIn("explanation은 반드시 원고 원문 언어(한국어)", word)
            self.assertIn("답변(response)은 반드시 한국어로 작성하세요", chat)
            self.assertIn("'하아'라는 감탄사를 'Ugh...'로 의역했어요", single)
            self.assertIn("'하아'라는 감탄사를 'Ugh...'로 의역했어요", batch)

    def test_glossary_lock_outranks_stylistic_variation(self) -> None:
        single = translation_prompts.build_paragraph_translation_prompt(
            CHAPTER, TRANSLATION_KWARGS
        )
        batch = translation_prompts.build_paragraph_translation_batch_prompt(
            [{"id": 1, "source_text": CHAPTER}],
            TRANSLATION_KWARGS,
        )
        for prompt in (single, batch):
            self.assertIn("[용어 고정 — 문체 다양화보다 우선]", prompt)
            self.assertIn("동의어로 바꾸지 마세요", prompt)
            self.assertIn("같은 단어 반복은 피하라", prompt)
            self.assertIn("자연스러운 문체 다양성을 유지하세요", prompt)
        self.assertLess(
            single.index("[용어 고정 — 문체 다양화보다 우선]"),
            single.index("직역이 아니라"),
        )
        self.assertLess(
            batch.index("[용어 고정 — 문체 다양화보다 우선]"),
            batch.index("각 문단의 id"),
        )


class PolishPromptTests(unittest.TestCase):
    def test_inserts_chapter_text_and_styleguide(self) -> None:
        settings = {"tense": "past", "character_voices": "이오나=캐주얼, 메리=격식"}
        paragraphs = ["Iona stopped.", "She looked back."]
        prompt = translation_prompts.build_chapter_polish_prompt(paragraphs, settings)
        self.assertTrue(prompt.startswith("당신은 영어권 출판 편집자입니다"))
        self.assertTrue(prompt.endswith("<<<PARAGRAPH 2>>>\nShe looked back."))
        self.assertIn("- 시제: past\n", prompt)
        self.assertIn("- 인물별 어조: 이오나=캐주얼, 메리=격식\n", prompt)
        self.assertIn("<<<PARAGRAPH 1>>>\nIona stopped.", prompt)
        self.assertIn('"paragraphs"', prompt)
        self.assertNotIn("{tense}", prompt)
        self.assertNotIn("{character_voices}", prompt)
        self.assertIn("마지막 방어선", prompt)
        self.assertIn("[핵심 임무 — 반드시 수행", prompt)
        self.assertIn("용어집 등록 여부와 무관하게", prompt)
        self.assertIn("가장 먼저 등장한 표기로 통일하세요", prompt)
        self.assertIn("확정 용어집 없음", prompt)
        self.assertIn("조어를 동의어로 바꿔 반복을 숨기지는 마세요", prompt)
        self.assertIn("binding cord", prompt)
        self.assertIn("restraint cord", prompt)
        self.assertNotIn("원문 대조 없이 번역문 자체의 자연스러움만", prompt)

    def test_keeps_json_example_braces(self) -> None:
        prompt = translation_prompts.build_chapter_polish_prompt(
            ["Text with {braces}."],
            {"tense": "past", "character_voices": "Iona=casual"},
        )
        self.assertIn('"polished_text"', prompt)
        self.assertIn('"index"', prompt)
        self.assertIn("Text with {braces}.", prompt)

    def test_empty_chapter_still_builds(self) -> None:
        settings = {"tense": "past", "character_voices": "이오나=캐주얼"}
        prompt = translation_prompts.build_chapter_polish_prompt([], settings)
        self.assertIn("번역문만 보고 판단", prompt)
        self.assertTrue(prompt.endswith("윤문하세요.\n"))

    def test_batch_output_still_receives_full_chapter_context(self) -> None:
        paragraphs = [f"Paragraph {index}." for index in range(1, 51)]
        prompt = translation_prompts.build_chapter_polish_prompt(
            paragraphs, {}, target_start=41, target_end=50
        )
        self.assertIn("전체 50개 문단을 모두 문맥으로 읽되", prompt)
        self.assertIn("index 41~50 문단만 반환", prompt)
        self.assertIn("<<<PARAGRAPH 1>>>\nParagraph 1.", prompt)
        self.assertTrue(prompt.endswith("<<<PARAGRAPH 50>>>\nParagraph 50."))

    def test_polish_includes_glossary_when_provided_but_still_requires_self_check(self) -> None:
        prompt = translation_prompts.build_chapter_polish_prompt(
            ["The binding cord snapped."],
            {"proper_nouns_confirmed": "구속줄→restraint cord"},
        )
        self.assertIn("구속줄→restraint cord", prompt)
        self.assertIn("없어도 위 핵심 임무는 반드시 수행하세요", prompt)
        self.assertIn("용어집 등록 여부와 무관하게", prompt)


class SubmissionQueryPromptTests(unittest.TestCase):
    def test_inserts_synopsis_and_proper_nouns(self) -> None:
        settings = {"proper_nouns_confirmed": "이오나→Iona, 메리→Meri"}
        prompt = translation_prompts.build_submission_query_prompt(CHAPTER, settings)
        self.assertTrue(prompt.startswith("당신은 한국 웹소설을 영어권 에이전트"))
        self.assertTrue(prompt.endswith(CHAPTER))
        self.assertIn("이오나→Iona, 메리→Meri", prompt)
        self.assertIn("이제 아래 시놉시스를 처리하세요.\n" + CHAPTER, prompt)
        self.assertNotIn("[원문 삽입]", prompt)
        self.assertNotIn("{proper_nouns_confirmed}", prompt)

    def test_keeps_json_example_braces(self) -> None:
        prompt = translation_prompts.build_submission_query_prompt(
            "본문 {중괄호} 테스트",
            {"proper_nouns_confirmed": "이오나→Iona"},
        )
        self.assertIn('"logline"', prompt)
        self.assertIn('"synopsis"', prompt)
        self.assertIn("본문 {중괄호} 테스트", prompt)

    def test_empty_chapter_still_builds(self) -> None:
        settings = {"proper_nouns_confirmed": "이오나→Iona"}
        prompt = translation_prompts.build_submission_query_prompt("", settings)
        self.assertIn("[대상 시장 지침]", prompt)
        self.assertTrue(prompt.endswith("처리하세요.\n"))
        none_prompt = translation_prompts.build_submission_query_prompt(
            None, settings  # type: ignore[arg-type]
        )
        self.assertEqual(none_prompt, prompt)


QA_SETTINGS = {
    "source_text": "밥은 먹었니.",
    "translated_text": "Have you eaten, babe?",
    "dragged_text": "Have you eaten, babe?",
    "tense": "past",
    "character_voices": "이오나=캐주얼, 메리=격식",
    "relationship_tag": "연인-걱정",
    "mood_tag": "불안",
    "culture_localization_level": "moderate",
    "chat_history": "작가: 이 장면 톤이 맞나요?\n토리: 걱정이 묻어 있는 장면이에요.",
}


class TranslationQaPromptTests(unittest.TestCase):
    def test_inserts_question_and_context(self) -> None:
        prompt = translation_prompts.build_translation_qa_prompt(
            "이 문장 조금 더 슬프게 바꿀 수 있어?", QA_SETTINGS
        )
        self.assertTrue(prompt.startswith('당신은 "토리"입니다'))
        self.assertTrue(prompt.endswith("이 문장 조금 더 슬프게 바꿀 수 있어?"))
        self.assertIn("원문: 밥은 먹었니.\n", prompt)
        self.assertIn("현재 번역: Have you eaten, babe?\n", prompt)
        self.assertIn("사용자가 지목한 부분: Have you eaten, babe?\n", prompt)
        self.assertIn("- 시제: past\n", prompt)
        self.assertIn("- 인물별 어조: 이오나=캐주얼, 메리=격식\n", prompt)
        self.assertIn(
            "[씬 컨텍스트]: relationship_tag=연인-걱정, mood_tag=불안\n",
            prompt,
        )
        self.assertIn("[문화반영범위]: moderate\n", prompt)
        self.assertIn("작가: 이 장면 톤이 맞나요?", prompt)
        self.assertNotIn("[원문 삽입]", prompt)
        self.assertNotIn("{source_text}", prompt)
        self.assertNotIn("{translated_text}", prompt)
        self.assertNotIn("{dragged_text}", prompt)
        self.assertNotIn("{tense}", prompt)
        self.assertNotIn("{character_voices}", prompt)
        self.assertNotIn("{relationship_tag}", prompt)
        self.assertNotIn("{mood_tag}", prompt)
        self.assertNotIn("{culture_localization_level}", prompt)
        self.assertNotIn("{chat_history}", prompt)

    def test_keeps_json_example_braces(self) -> None:
        prompt = translation_prompts.build_translation_qa_prompt(
            "본문 {중괄호} 테스트", QA_SETTINGS
        )
        self.assertIn('"response"', prompt)
        self.assertIn('"suggested_revision"', prompt)
        self.assertIn("Have you eaten anything today?", prompt)
        self.assertIn("왜 물음표를 없앤 거야?", prompt)
        self.assertIn("본문 {중괄호} 테스트", prompt)

    def test_empty_question_still_builds(self) -> None:
        prompt = translation_prompts.build_translation_qa_prompt("", QA_SETTINGS)
        self.assertIn("JSON만 출력", prompt)
        self.assertTrue(prompt.endswith("이제 아래 사용자 질문에 답하세요.\n"))
        none_prompt = translation_prompts.build_translation_qa_prompt(
            None, QA_SETTINGS  # type: ignore[arg-type]
        )
        self.assertEqual(none_prompt, prompt)

    def test_chat_prompt_alias_matches_qa_builder(self) -> None:
        via_qa = translation_prompts.build_translation_qa_prompt(
            "이 문장 조금 더 슬프게 바꿀 수 있어?", QA_SETTINGS
        )
        via_chat = translation_prompts.build_translation_chat_prompt(
            "이 문장 조금 더 슬프게 바꿀 수 있어?", QA_SETTINGS
        )
        self.assertIs(
            translation_prompts.build_translation_chat_prompt,
            translation_prompts.build_translation_qa_prompt,
        )
        self.assertEqual(via_chat, via_qa)
        self.assertIn("원문: 밥은 먹었니.\n", via_chat)
        self.assertIn("- 시제: past\n", via_chat)

    def test_parses_json_response_and_revision(self) -> None:
        raw = json.dumps(
            {
                "response": "걱정이 묻어 있게 바꿨어요.",
                "suggested_revision": "Have you eaten anything today?",
            },
            ensure_ascii=False,
        )
        response, revision = translation_prompts.parse_translation_qa_output(raw)
        self.assertEqual(response, "걱정이 묻어 있게 바꿨어요.")
        self.assertEqual(revision, "Have you eaten anything today?")

    def test_parses_plain_text_when_json_missing(self) -> None:
        response, revision = translation_prompts.parse_translation_qa_output(
            "지금은 이 문단만 보고 답할게요."
        )
        self.assertEqual(response, "지금은 이 문단만 보고 답할게요.")
        self.assertEqual(revision, "")


class WordContextPromptTests(unittest.TestCase):
    def test_inserts_segment_and_word(self) -> None:
        prompt = translation_prompts.build_word_context_prompt(
            {
                "source_text": "이오나는 서둘러 집으로 발길을 옮겼다.",
                "translated_text": "Iona hurried home.",
            },
            "hurried",
            "발길을 옮겼다 → hurried: 서두름으로 압축",
        )
        self.assertTrue(prompt.startswith('당신은 "토리"입니다. 영어권 투고용으로'))
        self.assertIn("원문: 이오나는 서둘러 집으로 발길을 옮겼다.\n", prompt)
        self.assertIn("현재 번역: Iona hurried home.\n", prompt)
        self.assertIn("사용자가 클릭한 단어: hurried\n", prompt)
        self.assertIn("발길을 옮겼다 → hurried: 서두름으로 압축", prompt)

        self.assertIn("이제 아래 질문에 답하세요.\n", prompt)
        self.assertNotIn("[원문 삽입]", prompt)
        self.assertNotIn("{word}", prompt)
        self.assertNotIn("{source_text}", prompt)
        self.assertNotIn("{translated_text}", prompt)
        self.assertNotIn("{existing_translation_notes}", prompt)

    def test_keeps_json_example_braces(self) -> None:
        prompt = translation_prompts.build_word_context_prompt(
            {"source_text": "본문 {중괄호}", "translated_text": "body {braces}"},
            "small",
            "",
        )
        self.assertIn('"explanation"', prompt)
        self.assertIn("본문 {중괄호}", prompt)
        self.assertIn("body {braces}", prompt)

    def test_empty_inputs_still_build(self) -> None:
        prompt = translation_prompts.build_word_context_prompt(None, None, None)
        self.assertIn("JSON만 출력", prompt)
        self.assertIn("사용자가 클릭한 단어: \n", prompt)
        none_prompt = translation_prompts.build_word_context_prompt({}, "", "")
        self.assertIn("사용자가 클릭한 단어: \n", none_prompt)


class MultilingualPromptTests(unittest.TestCase):
    def test_all_translation_prompts_use_spanish_profile_without_english_bias(self) -> None:
        prompts = [
            translation_prompts.build_narrative_formatting_prompt(CHAPTER, "es"),
            translation_prompts.build_scene_split_prompt(CHAPTER, "es"),
            translation_prompts.build_proper_noun_fit_prompt(
                CHAPTER, target_language="es"
            ),
            translation_prompts.build_culture_marker_prompt(
                CHAPTER, "moderate", "친구", "평온", "es"
            ),
            translation_prompts.build_paragraph_translation_prompt(
                CHAPTER, TRANSLATION_KWARGS, target_language="es"
            ),
            translation_prompts.build_paragraph_translation_batch_prompt(
                [{"id": 1, "source_text": CHAPTER}],
                TRANSLATION_KWARGS,
                target_language="es",
            ),
            translation_prompts.build_chapter_polish_prompt(
                ["La lluvia caía."], target_language="es"
            ),
            translation_prompts.build_submission_query_prompt(
                CHAPTER,
                {"culture_localization_level": "moderate"},
                target_language="es",
            ),
            translation_prompts.build_word_context_prompt(
                {"source_text": "비가 왔다.", "translated_text": "Llovía."},
                "llovía",
                target_language="es",
            ),
            translation_prompts.build_translation_chat_prompt(
                "더 자연스럽게 바꿔줘.",
                {"translated_text": "Llovía."},
                target_language="es",
            ),
        ]
        for prompt in prompts:
            self.assertTrue("스페인어" in prompt or "스페인어권" in prompt)
            self.assertNotIn("영어권", prompt)
            self.assertNotIn("English", prompt)
            self.assertNotIn("he/she/they", prompt)
        self.assertIn("악센트가 있으면 유지", prompts[2])
        self.assertIn("él/ella/ellos/ellas", prompts[4])

    def test_all_translation_prompts_use_french_profile(self) -> None:
        prompts = [
            translation_prompts.build_narrative_formatting_prompt(CHAPTER, "fr"),
            translation_prompts.build_scene_split_prompt(CHAPTER, "fr"),
            translation_prompts.build_proper_noun_fit_prompt(
                CHAPTER, target_language="fr"
            ),
            translation_prompts.build_culture_marker_prompt(
                CHAPTER, "moderate", "친구", "평온", "fr"
            ),
            translation_prompts.build_paragraph_translation_prompt(
                CHAPTER, TRANSLATION_KWARGS, target_language="fr"
            ),
            translation_prompts.build_paragraph_translation_batch_prompt(
                [{"id": 1, "source_text": CHAPTER}],
                TRANSLATION_KWARGS,
                target_language="fr",
            ),
            translation_prompts.build_chapter_polish_prompt(
                ["La pluie tombait."], target_language="fr"
            ),
            translation_prompts.build_submission_query_prompt(
                CHAPTER,
                {"culture_localization_level": "moderate"},
                target_language="fr",
            ),
            translation_prompts.build_word_context_prompt(
                {"source_text": "비가 왔다.", "translated_text": "Il pleuvait."},
                "pleuvait",
                target_language="fr",
            ),
            translation_prompts.build_translation_chat_prompt(
                "더 자연스럽게 바꿔줘.",
                {"translated_text": "Il pleuvait."},
                target_language="fr",
            ),
        ]
        for prompt in prompts:
            self.assertTrue("프랑스어" in prompt or "프랑스어권" in prompt)
            self.assertNotIn("영어권", prompt)
            self.assertNotIn("English", prompt)
            self.assertNotIn("he/she/they", prompt)
        self.assertIn("기메 따옴표(« … »)", prompts[0])
        self.assertIn("é, è, à, ç", prompts[2])
        self.assertIn("프랑스어 발음", prompts[2])
        self.assertIn("il/elle/ils/elles", prompts[4])
        self.assertIn("개별 출판사 지침", prompts[7])


if __name__ == "__main__":
    unittest.main()
