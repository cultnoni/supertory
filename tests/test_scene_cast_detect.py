"""Scene manuscript cast detection: appears vs mentioned, plus new names."""

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

    def test_extracts_new_appearing_name(self) -> None:
        names = detect.extract_new_appearing_names("하린이 들어왔다.", ["서윤"])
        self.assertEqual(names, ["하린"])

    def test_skips_stopwords_and_known_names(self) -> None:
        text = "사람이 들어왔다. 서윤이 말했다."
        names = detect.extract_new_appearing_names(text, ["서윤"])
        self.assertEqual(names, [])

    def test_mentioned_unknown_name_is_not_auto_created(self) -> None:
        names = detect.extract_new_appearing_names("하린 생각이 났다.", [])
        self.assertEqual(names, [])

    def test_bare_possessive_name_is_mentioned(self) -> None:
        characters = [{"id": 1, "name": "서윤"}]
        found = detect.detect_known_cast("서윤의 편지가 왔다.", characters)
        self.assertEqual(found, {1: detect.MENTIONED})

    def test_story_noun_is_not_auto_created(self) -> None:
        self.assertEqual(detect.extract_new_appearing_names("그 이야기가 길었다.", []), [])
        self.assertEqual(detect.extract_new_appearing_names("기억이 났다.", []), [])

    def test_single_syllable_known_name_appears(self) -> None:
        characters = [{"id": 1, "name": "진"}]
        found = detect.detect_known_cast("진이 문을 열었다.", characters)
        self.assertEqual(found, {1: detect.APPEARS})

    def test_extracts_single_syllable_new_name(self) -> None:
        names = detect.extract_new_appearing_names("솔이 들어왔다.", ["진"])
        self.assertEqual(names, ["솔"])

    def test_particle_is_not_auto_created_as_name(self) -> None:
        self.assertEqual(detect.extract_new_appearing_names("이가 말했다.", []), [])

    def test_mention_in_previous_sentence_does_not_taint_next_name(self) -> None:
        text = (
            "서윤이 문을 열고 들어왔다.\n"
            "해인이 어디 갔는지 떠올렸다.\n"
            "민재가 창밖을 보았다."
        )
        characters = [{"id": 1, "name": "서윤"}, {"id": 2, "name": "해인"}]
        found = detect.detect_known_cast(text, characters)
        self.assertEqual(found[1], detect.APPEARS)
        self.assertEqual(found[2], detect.MENTIONED)
        self.assertEqual(detect.extract_new_appearing_names(text, ["서윤", "해인"]), ["민재"])


if __name__ == "__main__":
    unittest.main()
