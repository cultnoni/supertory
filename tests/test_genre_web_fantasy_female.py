"""여성향판타지형(웹소설) 장르 리포트와 카드 (가짜 Claude, 임시 DB)."""

from __future__ import annotations

import unittest

from _genre_harness import GenreRunHarness
from feedback_pipeline import genre_common, genre_layers, genre_web_fantasy_female
from feedback_pipeline.claude_client import FakeClaude, set_client_for_tests
from feedback_pipeline.paragraphs import Paragraph
from test_feedback_pipeline import PipelineFake, _default_consistency, _default_report

PARAS = {
    1: Paragraph(1, "정원이 손을 내밀었다. 승혜는 그 손을 보았다.", "text"),
    2: Paragraph(2, "승혜는 그 말을 되뇌었다. 정원의 눈빛이 흔들렸다.", "text"),
    3: Paragraph(3, "세 번째 문단이다. 정원의 정원이 아니라 그냥 정원이다. 그녀는 고개를 돌렸다.", "text"),
}

BASE_KEYS = ["narrative_balance", "payoff_timing", "emotion_line", "world_status"]
DIMENSION_KEYS = BASE_KEYS + ["world_split"]
PERIOD_KEYS = BASE_KEYS + ["status_order"]
WEBNOVEL = {"purpose": "web_novel", "cluster_id": "webnovel"}


def _narrative_map() -> dict:
    return {
        "events": [
            {"para": 1, "kind": "payoff", "what": "쌓인 서러움이 뒤집힌다", "quote": "정원이 손을 내밀었다"},
            {"para": 2, "kind": "emotion", "what": "눈빛으로 감정이 드러난다", "quote": "정원의 눈빛이 흔들렸다"},
        ],
        "threads": ["복수", "성장"],
        "payoffs": [{"para": 1, "what": "손을 내밀어 역전한다"}],
        "settings": ["여주는 이세계 귀족이다"],
    }


def _item(key: str, verdict: str = "works", **extra) -> dict:
    row = {
        "key": key,
        "verdict": verdict,
        "intentional": False,
        "note": "근거가 분명하다.",
        "evidence": [{"para": 1, "quote": "정원이 손을 내밀었다"}],
    }
    row.update(extra)
    return row


def _rubric(keys: list[str]) -> dict:
    return {
        "summary": "복수와 성장이 같이 쌓인다.",
        "items": [_item(key) for key in keys],
        "strengths": [
            {"title": "역전의 장면", "body": "손동작이 반전을 보여 준다.", "para": 1, "quote": "정원이 손을 내밀었다"}
        ],
        "top_tasks": [{"title": "감정 줄이기", "why": "설명을 행동으로 옮겨야 한다."}],
    }


class WebFantasyFemaleHelperTests(unittest.TestCase):
    def test_required_keys_differ_by_sub_genre(self) -> None:
        self.assertEqual(
            genre_web_fantasy_female.required_keys({"sub_genre": ""}, has_kept_avoid=False), BASE_KEYS
        )
        self.assertEqual(
            genre_web_fantasy_female.required_keys({"sub_genre": "modern"}, has_kept_avoid=False), BASE_KEYS
        )
        self.assertEqual(
            genre_web_fantasy_female.required_keys({"sub_genre": "dimension"}, has_kept_avoid=False),
            DIMENSION_KEYS,
        )
        self.assertEqual(
            genre_web_fantasy_female.required_keys({"sub_genre": "period_east"}, has_kept_avoid=False),
            PERIOD_KEYS,
        )
        self.assertEqual(
            genre_web_fantasy_female.required_keys({"sub_genre": "period_west"}, has_kept_avoid=False),
            PERIOD_KEYS,
        )
        self.assertIn(
            "kept_avoid",
            genre_web_fantasy_female.required_keys({"sub_genre": ""}, has_kept_avoid=True),
        )

    def test_narrative_map_drops_invented_quotes(self) -> None:
        raw = _narrative_map()
        raw["events"].append({"para": 1, "kind": "payoff", "what": "지어낸 사건", "quote": "원고에 없는 문장"})
        raw["events"].append({"para": 99, "kind": "payoff", "what": "없는 문단", "quote": ""})
        narrative_map, dropped = genre_web_fantasy_female.normalize_narrative_map(raw, PARAS)
        self.assertEqual([event["para"] for event in narrative_map["events"]], [1, 2])
        self.assertEqual({row["reason"] for row in dropped}, {"quote_invalid", "para_invalid"})
        self.assertEqual(narrative_map["threads"], ["복수", "성장"])
        self.assertEqual(narrative_map["settings"], ["여주는 이세계 귀족이다"])

    def test_cards_are_note_only_with_fantasy_female_tags(self) -> None:
        genre = {
            "items": [
                _item("narrative_balance", "problem"),
                _item("emotion_line", "room"),
                _item("status_order", "room"),
                _item("payoff_timing", "works"),
            ],
            "narrative_map": _narrative_map(),
        }
        cards, dropped = genre_web_fantasy_female.build_cards(genre, list(PARAS.values()), 5)
        self.assertEqual(
            [c["style_type"] for c in cards], ["narrative_imbalance", "emotion_told", "status_break"]
        )
        self.assertEqual(dropped, [])
        for card in cards:
            self.assertIsNone(card["suggestion"])
            self.assertEqual(card["scene_id"], 5)

    def test_protected_paragraphs_and_stripping(self) -> None:
        genre = {"narrative_map": _narrative_map()}
        self.assertEqual(genre_web_fantasy_female.protected_paragraphs(genre), {1, 2})
        card = {"start_para": 1, "end_para": 1, "suggestion": "고친 문장", "edit_plan": "x", "warnings_json": []}
        self.assertTrue(genre_common.strip_suggestion_near(card, {1}, genre_web_fantasy_female.TOUCH_WARNING))
        self.assertIsNone(card["suggestion"])
        self.assertEqual(card["warnings_json"][0]["code"], "web_fantasy_female_touch")

    def test_registry_and_markdown(self) -> None:
        self.assertIs(genre_layers.layer_for("web_fantasy_female"), genre_web_fantasy_female)
        md = genre_layers.render_genre_md(
            {
                "group": "web_fantasy_female",
                "summary": "총평",
                "items": [_item("narrative_balance", "room")],
                "top_tasks": [],
            }
        )
        self.assertIn("## 웹소설 여성향판타지 진단", md)
        self.assertIn("서사 균형", md)

    def test_emphasis_uses_the_female_playbook(self) -> None:
        text = genre_web_fantasy_female._emphasis_text(
            {"main_genre": "female_fantasy", "sub_genre": "dimension"}
        )
        self.assertIn("[여성향 판타지 기준집]", text)
        self.assertIn("world_split", text)
        self.assertIn("새 세계의 생존 규칙", text)
        modern = genre_web_fantasy_female._emphasis_text(
            {"main_genre": "female_fantasy", "sub_genre": "modern"}
        )
        self.assertIn("[여성향 판타지 기준집]", modern)
        self.assertNotIn("world_split", modern)
        self.assertNotIn("status_order", modern)


class WebFantasyFemaleFake(PipelineFake):
    def __init__(self, *args, rubric_keys=None, rubric=None, fail_map=False, payoff_para3=False, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.rubric_keys = rubric_keys or BASE_KEYS
        self.rubric = rubric
        self.fail_map = fail_map
        self.payoff_para3 = payoff_para3

    def generate(self, prompt: str, **kwargs):
        if "「서사 지도」를 만든다" in prompt:
            with self._lock:
                self.prompts.append(prompt)
            if self.fail_map:
                return FakeClaude()._as_result({"parsed": None, "text": "NOT JSON", "usage": dict(self.usage)})
            narrative_map = _narrative_map()
            if self.payoff_para3:
                narrative_map["events"].append(
                    {"para": 3, "kind": "payoff", "what": "고개를 돌려 역전한다", "quote": "그녀는 고개를 돌렸다"}
                )
            return FakeClaude()._as_result({"parsed": narrative_map, "usage": dict(self.usage)})
        if "「장르 진단 루브릭」을 판정한다" in prompt:
            with self._lock:
                self.prompts.append(prompt)
            rubric = self.rubric or _rubric(self.rubric_keys)
            return FakeClaude()._as_result({"parsed": rubric, "usage": dict(self.usage)})
        result = super().generate(prompt, **kwargs)
        if "첨삭 카드 1장" in prompt and isinstance(result.get("parsed"), dict):
            result["parsed"]["suggestion"] = "세 번째 문단이다. 그냥 정원이다. 그녀는 고개를 돌렸다."
        return result

    def narrative_prompts(self) -> list[str]:
        return [p for p in self.prompts if "서사 지도" in p]

    def rubric_prompt(self) -> str:
        return [p for p in self.prompts if "「장르 진단 루브릭」을 판정한다" in p][0]


class WebFantasyFemaleRunTests(GenreRunHarness):
    def use_fake(self, **kwargs) -> WebFantasyFemaleFake:
        fake = WebFantasyFemaleFake(consistency=_default_consistency(), report=_default_report(), **kwargs)
        set_client_for_tests(fake)
        return fake

    def genre_cards(self, run: dict) -> list[dict]:
        return [c for c in run["cards"] if str(c.get("report_ref") or "").startswith("GENRE:")]

    def test_female_fantasy_run_adds_report(self) -> None:
        fake = self.use_fake()
        project_id, scene_id = self.create_scene("female_fantasy", **WEBNOVEL)
        run = self.run_and_wait(project_id, scene_id)
        self.assertEqual(run["status"], "ok", run)
        genre = run["report"]["genre"]
        self.assertEqual(genre["group"], "web_fantasy_female")
        self.assertEqual([item["key"] for item in genre["items"]], BASE_KEYS)
        self.assertTrue(genre["narrative_map"]["events"])
        self.assertIn("## 웹소설 여성향판타지 진단", run["report_md"])
        self.assertEqual(len(fake.narrative_prompts()), 2)
        self.assertEqual(run["params"].get("genre_group"), "web_fantasy_female")
        stages = [row.get("stage") for row in run["params"]["usage"]["stages"]]
        self.assertIn("genre_narrative_map", stages)
        self.assertIn("genre_rubric", stages)

    def test_dimension_sub_gets_the_extra_item(self) -> None:
        self.use_fake(rubric_keys=DIMENSION_KEYS)
        project_id, scene_id = self.create_scene("female_fantasy", sub_genre="dimension", **WEBNOVEL)
        run = self.run_and_wait(project_id, scene_id)
        keys = [item["key"] for item in run["report"]["genre"]["items"]]
        self.assertEqual(keys, DIMENSION_KEYS)

    def test_period_sub_gets_the_status_item(self) -> None:
        self.use_fake(rubric_keys=PERIOD_KEYS)
        project_id, scene_id = self.create_scene("female_fantasy", sub_genre="period_east", **WEBNOVEL)
        run = self.run_and_wait(project_id, scene_id)
        keys = [item["key"] for item in run["report"]["genre"]["items"]]
        self.assertEqual(keys, PERIOD_KEYS)

    def test_playbook_reaches_the_prompt(self) -> None:
        fake = self.use_fake(rubric_keys=DIMENSION_KEYS)
        project_id, scene_id = self.create_scene("female_fantasy", sub_genre="dimension", **WEBNOVEL)
        self.run_and_wait(project_id, scene_id)
        prompt = fake.rubric_prompt()
        self.assertIn("[여성향 판타지 기준집]", prompt)
        self.assertIn("world_split", prompt)

    def test_fantasy_male_does_not_use_female_prompts(self) -> None:
        fake = self.use_fake()
        project_id, scene_id = self.create_scene("fantasy", sub_genre="male", **WEBNOVEL)
        self.run_and_wait(project_id, scene_id)
        self.assertEqual(fake.narrative_prompts(), [])

    def test_genre_literature_does_not_use_female_prompts(self) -> None:
        fake = self.use_fake()
        project_id, scene_id = self.create_scene("traditional")
        self.run_and_wait(project_id, scene_id)
        self.assertEqual(fake.narrative_prompts(), [])

    def test_run_creates_note_only_fantasy_female_cards(self) -> None:
        rubric = _rubric(["narrative_balance", "emotion_line"])
        rubric["items"][0]["verdict"] = "problem"
        rubric["items"][1]["verdict"] = "room"
        self.use_fake(
            rubric={
                **rubric,
                "items": rubric["items"] + [_item("payoff_timing"), _item("world_status")],
            }
        )
        project_id, scene_id = self.create_scene("female_fantasy", **WEBNOVEL)
        run = self.run_and_wait(project_id, scene_id)
        cards = self.genre_cards(run)
        self.assertEqual(sorted(c["style_type"] for c in cards), ["emotion_told", "narrative_imbalance"])
        self.assertTrue(all(not c.get("suggestion") for c in cards))
        self.assertEqual(int(run["planned_cards"]), len(run["cards"]))

    def test_suggestion_on_a_payoff_paragraph_is_removed(self) -> None:
        self.use_fake(payoff_para3=True)
        project_id, scene_id = self.create_scene("female_fantasy", **WEBNOVEL)
        run = self.run_and_wait(project_id, scene_id)
        w1 = [c for c in run["cards"] if c.get("report_ref") == "W1"][0]
        self.assertFalse(w1.get("suggestion"))
        codes = [w.get("code") for w in (w1.get("warnings") or w1.get("warnings_json") or [])]
        self.assertIn("web_fantasy_female_touch", codes)

    def test_author_cliche_avoid_reaches_the_prompt(self) -> None:
        fake = self.use_fake()
        project_id, scene_id = self.create_scene("female_fantasy", **WEBNOVEL)
        status, saved = self.request(
            "POST",
            f"/api/projects/{project_id}/webnovel-cliche",
            {"cliche_avoid": "남주가 대신 해결하는 전개는 지양"},
        )
        self.assertEqual(status, 200, saved)
        run = self.run_and_wait(project_id, scene_id)
        self.assertIn("남주가 대신 해결하는 전개는 지양", fake.rubric_prompt())
        self.assertIn("kept_avoid", fake.rubric_prompt())
        self.assertTrue(run["report"]["genre"]["promise"]["kept"])

    def test_used_cliche_removes_matching_cards(self) -> None:
        rubric = _rubric(BASE_KEYS)
        rubric["items"][0].update(verdict="room", note="회귀 설정이라 설명이 깁니다.")
        self.use_fake(rubric=rubric)
        project_id, scene_id = self.create_scene("female_fantasy", **WEBNOVEL)
        status, saved = self.request(
            "POST",
            f"/api/projects/{project_id}/webnovel-cliche",
            {"cliche_used": "회귀 설정이라 설명이 깁니다"},
        )
        self.assertEqual(status, 200, saved)
        run = self.run_and_wait(project_id, scene_id)
        refs = [c.get("report_ref") for c in run["cards"]]
        self.assertNotIn("GENRE:narrative_balance", refs)
        reasons = [(row.get("id"), row.get("reason")) for row in run["params"]["dropped"]]
        self.assertIn(("GENRE:narrative_balance", "author_promise"), reasons)

    def test_fantasy_female_failure_does_not_stop_the_normal_report(self) -> None:
        self.use_fake(fail_map=True)
        project_id, scene_id = self.create_scene("female_fantasy", **WEBNOVEL)
        run = self.run_and_wait(project_id, scene_id)
        self.assertEqual(run["status"], "ok", run)
        self.assertNotIn("genre", run["report"])
        self.assertTrue(run["params"].get("genre_error"))


if __name__ == "__main__":
    unittest.main()
