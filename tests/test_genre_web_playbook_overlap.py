"""웹소설 첨삭 기준집과 AI 패널(도우미) A그룹 판단 기준이 같은 말을 쓰는지."""

from __future__ import annotations

import unittest

import app
from feedback_pipeline import (
    genre_web_fantasy_female,
    genre_web_fantasy_male,
    genre_web_romance,
)

# 도우미 설정 검사 프롬프트와 첨삭 루브릭에 둘 다 들어가야 하는 문장.
# 잘리면 도우미는 지키고 첨삭은 놓치는 오지적(클리셰를 결함으로 봄)이 난다.
CASES = (
    {
        "name": "romance_modern",
        "project": {"main_genre": "romance", "sub_genre": "modern"},
        "layer": genre_web_romance,
        "needles": (
            "판타지 장치 오판 방지",
            "감정선의 인과관계",
            "집착남",
            "회귀/타임슬립",
        ),
    },
    {
        "name": "romance_period_east",
        "project": {"main_genre": "romance", "sub_genre": "period_east"},
        "layer": genre_web_romance,
        "needles": (
            "신분층에 맞는 규칙",
            "궁중/반가/평민",
        ),
    },
    {
        "name": "romfant",
        "project": {"main_genre": "romfant", "sub_genre": ""},
        "layer": genre_web_romance,
        "needles": (
            "극단적 서사 스케일",
            "환생 반복",
        ),
    },
    {
        "name": "bl",
        "project": {"main_genre": "romance", "sub_genre": "bl"},
        "layer": genre_web_romance,
        "needles": ("공수 일관성", "권력역전물"),
    },
    {
        "name": "fantasy_murim",
        "project": {"main_genre": "fantasy", "sub_genre": "male", "genre_detail": "murim"},
        "layer": genre_web_fantasy_male,
        "needles": (
            "정파의 이중잣대가 의도적 설정인지",
            "경지 체계와 세력 논리",
        ),
    },
    {
        "name": "fantasy_historical_legacy",
        "project": {"main_genre": "historical"},
        "layer": genre_web_fantasy_male,
        "needles": ("실존인물 묘사의 의도된 개변",),
    },
    {
        "name": "female_dimension",
        "project": {"main_genre": "female_fantasy", "sub_genre": "dimension"},
        "layer": genre_web_fantasy_female,
        "needles": (
            "이동 계기와 새 세계 생존 규칙",
            "걸크러쉬답지 않다",
        ),
    },
    {
        "name": "female_period_west",
        "project": {"main_genre": "female_fantasy", "sub_genre": "period_west"},
        "layer": genre_web_fantasy_female,
        "needles": ("유럽사 고증 오류는 지적하지 않는다",),
    },
)


def _judge_section(project: dict) -> str:
    return app.format_genre_playbook_judge_section(
        project.get("main_genre") or "",
        project.get("sub_genre") or "",
        project.get("genre_detail") or "",
    )


def _worldscan_prompt(project: dict) -> str:
    return app.SuperToryHandler._build_setting_break_scan_prompt(
        "한 줄 원고",
        main_genre=project.get("main_genre") or "",
        sub_genre=project.get("sub_genre") or "",
        genre_detail=project.get("genre_detail") or "",
    )


class WebPlaybookOverlapTests(unittest.TestCase):
    def test_feedback_keeps_the_helper_judge_rules(self) -> None:
        for case in CASES:
            with self.subTest(case["name"]):
                feedback = case["layer"]._playbook_text(case["project"])
                helper = _judge_section(case["project"])
                self.assertTrue(feedback, case["name"])
                self.assertTrue(helper, case["name"])
                for needle in case["needles"]:
                    self.assertIn(needle, helper, msg=f"helper {case['name']}: {needle}")
                    self.assertIn(needle, feedback, msg=f"feedback {case['name']}: {needle}")

    def test_worldscan_prompt_uses_the_same_judge_rules(self) -> None:
        for case in CASES:
            with self.subTest(case["name"]):
                feedback = case["layer"]._playbook_text(case["project"])
                prompt = _worldscan_prompt(case["project"])
                for needle in case["needles"]:
                    self.assertIn(needle, prompt, msg=f"worldscan {case['name']}: {needle}")
                    self.assertIn(needle, feedback, msg=f"feedback {case['name']}: {needle}")

    def test_romance_does_not_clip_the_fantasy_device_rule(self) -> None:
        text = genre_web_romance._playbook_text({"main_genre": "romance", "sub_genre": "modern"})
        self.assertIn("판타지 장치 오판 방지", text)
        self.assertIn("[캐릭터/관계 참고 어휘]", text)
        self.assertGreater(len(text), 420)

    def test_empty_romance_project_has_no_playbook(self) -> None:
        self.assertEqual(genre_web_romance._playbook_text({}), "")


if __name__ == "__main__":
    unittest.main()
