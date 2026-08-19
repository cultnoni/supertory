"""Scene manuscript cast detection: registered names only, plus Gemini parse."""

from __future__ import annotations

import unittest

import scene_cast_detect as detect


class SceneCastDetectTests(unittest.TestCase):
    def test_known_character_action_is_appearing(self) -> None:
        characters = [{"id": 1, "name": "서윤", "aliases": ["여우"]}]
        found = detect.detect_known_cast("서윤이 문을 열었다.", characters)
        self.assertEqual(found, {1: detect.APPEARS})

    def test_alias_matches_the_same_character(self) -> None:
        characters = [{"id": 1, "name": "서윤", "aliases": ["여우"]}]
        found = detect.detect_known_cast("여우가 말했다.", characters)
        self.assertEqual(found, {1: detect.APPEARS})

    def test_memory_cue_is_mentioned(self) -> None:
        characters = [{"id": 2, "name": "민재"}]
        found = detect.detect_known_cast("민재 생각이 났다.", characters)
        self.assertEqual(found, {2: detect.MENTIONED})

    def test_appear_wins_over_mention_in_same_scene(self) -> None:
        characters = [{"id": 1, "name": "서윤"}]
        text = "서윤 소문이 돌았다. 그런데 서윤이 문을 열고 들어왔다."
        found = detect.detect_known_cast(text, characters)
        self.assertEqual(found, {1: detect.APPEARS})

    def test_two_characters_split_appear_and_mention(self) -> None:
        characters = [{"id": 1, "name": "서윤"}, {"id": 2, "name": "민재"}]
        text = "서윤이 물었다. 민재는 어디지?"
        found = detect.detect_known_cast(text, characters)
        self.assertEqual(found[1], detect.APPEARS)
        self.assertEqual(found[2], detect.MENTIONED)

    def test_does_not_match_name_inside_longer_hangul(self) -> None:
        characters = [{"id": 1, "name": "서윤"}]
        found = detect.detect_known_cast("김서윤이 웃었다.", characters)
        self.assertEqual(found, {})

    def test_unregistered_name_is_not_detected(self) -> None:
        found = detect.detect_known_cast("하린이 들어왔다.", [{"id": 1, "name": "서윤"}])
        self.assertEqual(found, {})

    def test_single_syllable_known_name_appears(self) -> None:
        characters = [{"id": 1, "name": "진"}]
        found = detect.detect_known_cast("진이 문을 열었다.", characters)
        self.assertEqual(found, {1: detect.APPEARS})

    def test_bare_possessive_name_is_mentioned(self) -> None:
        characters = [{"id": 1, "name": "서윤"}]
        found = detect.detect_known_cast("서윤의 편지가 왔다.", characters)
        self.assertEqual(found, {1: detect.MENTIONED})

    def test_parse_candidates_skips_known_and_junk_shape(self) -> None:
        raw = '{"names": ["하린", "서윤", "몸가짐", "하린"]}'
        names = detect.parse_new_name_candidates(raw, ["서윤"])
        self.assertEqual(names, ["하린", "몸가짐"])

    def test_parse_candidates_empty_on_bad_json(self) -> None:
        self.assertEqual(detect.parse_new_name_candidates("아님", []), [])

    def test_prompt_excludes_known_names(self) -> None:
        system, user = detect.build_new_name_prompt("하린이 말했다.", ["서윤"])
        self.assertIn("JSON만", system)
        self.assertIn("일반명사·추상명사·동사 활용형은 절대 포함하지 마라", user)
        self.assertIn("서윤", user)
        self.assertIn("하린이 말했다.", user)


if __name__ == "__main__":
    unittest.main()
