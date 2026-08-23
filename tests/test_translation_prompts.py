# -*- coding: utf-8 -*-
"""Prompt builders for submission-oriented translation."""

from __future__ import annotations

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
        self.assertIn("이미 확정된 영문 번역입니다", prompt)


class PolishPromptTests(unittest.TestCase):
    def test_inserts_chapter_text_and_styleguide(self) -> None:
        settings = {"tense": "past", "character_voices": "이오나=캐주얼, 메리=격식"}
        prompt = translation_prompts.build_polish_prompt(CHAPTER, settings)
        self.assertTrue(prompt.startswith("당신은 영어권 출판 편집자입니다"))
        self.assertTrue(prompt.endswith(CHAPTER))
        self.assertIn("- 시제: past\n", prompt)
        self.assertIn("- 인물별 어조: 이오나=캐주얼, 메리=격식\n", prompt)
        self.assertIn("이제 아래 챕터를 윤문하세요.\n" + CHAPTER, prompt)
        self.assertNotIn("[원문 삽입]", prompt)
        self.assertNotIn("{tense}", prompt)
        self.assertNotIn("{character_voices}", prompt)

    def test_keeps_json_example_braces(self) -> None:
        prompt = translation_prompts.build_polish_prompt(
            "본문 {중괄호} 테스트",
            {"tense": "past", "character_voices": "Iona=casual"},
        )
        self.assertIn('"polished_text"', prompt)
        self.assertIn('"change_log"', prompt)
        self.assertIn("본문 {중괄호} 테스트", prompt)

    def test_empty_chapter_still_builds(self) -> None:
        settings = {"tense": "past", "character_voices": "이오나=캐주얼"}
        prompt = translation_prompts.build_polish_prompt("", settings)
        self.assertIn("translationese", prompt)
        self.assertTrue(prompt.endswith("윤문하세요.\n"))
        none_prompt = translation_prompts.build_polish_prompt(None, settings)  # type: ignore[arg-type]
        self.assertEqual(none_prompt, prompt)


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
        self.assertIn("300~500단어", prompt)
        self.assertTrue(prompt.endswith("처리하세요.\n"))
        none_prompt = translation_prompts.build_submission_query_prompt(
            None, settings  # type: ignore[arg-type]
        )
        self.assertEqual(none_prompt, prompt)


if __name__ == "__main__":
    unittest.main()
