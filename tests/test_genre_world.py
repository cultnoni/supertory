"""세계형(SF, 정통판타지) 장르 리포트와 카드 (가짜 Claude, 임시 DB)."""

from __future__ import annotations

import unittest

from _genre_harness import GenreRunHarness
from feedback_pipeline import genre_common, genre_layers, genre_world
from feedback_pipeline.claude_client import FakeClaude, set_client_for_tests
from feedback_pipeline.paragraphs import Paragraph
from test_feedback_pipeline import PipelineFake, _default_consistency, _default_report

PARAS = {
    1: Paragraph(1, "정원이 손을 내밀었다. 승혜는 그 손을 보았다.", "text"),
    2: Paragraph(2, "승혜는 그 말을 되뇌었다. 정원의 눈빛이 흔들렸다.", "text"),
    3: Paragraph(3, "세 번째 문단이다. 정원의 정원이 아니라 그냥 정원이다. 그녀는 고개를 돌렸다.", "text"),
}


def _world_map() -> dict:
    return {
        "events": [
            {"para": 1, "kind": "rule", "what": "손짓이 열쇠다", "quote": "정원이 손을 내밀었다"},
            {"para": 2, "kind": "exposition", "what": "설정을 설명한다", "quote": "정원의 눈빛이 흔들렸다"},
        ],
        "rules": ["손짓으로 문이 열린다"],
        "terms": [{"term": "정원", "meaning": "주인공 이름", "para": 1}],
        "changes": [],
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
        "summary": "규칙이 일관되다.",
        "items": [_item(key) for key in keys],
        "strengths": [
            {"title": "규칙의 선명함", "body": "손짓 규칙이 분명하다.", "para": 1, "quote": "정원이 손을 내밀었다"}
        ],
        "top_tasks": [{"title": "설정 풀기", "why": "설명을 사건으로 옮겨야 한다."}],
    }


SF_KEYS = ["rule_consistency", "exposition", "plausibility", "ripple", "term_consistency"]
FANTASY_KEYS = ["rule_consistency", "exposition", "plausibility", "term_consistency",
                "power_rules", "setting_depth", "scale"]


class WorldHelperTests(unittest.TestCase):
    def test_required_keys_differ_by_genre(self) -> None:
        self.assertEqual(genre_world.required_keys("sf", has_kept_promise=False), SF_KEYS)
        self.assertEqual(genre_world.required_keys("traditional", has_kept_promise=False), FANTASY_KEYS)
        self.assertIn("kept_promise", genre_world.required_keys("sf", has_kept_promise=True))
        # 정통판타지는 웹소설과 달리 세계 규칙 항목이 더해진다
        extra = set(FANTASY_KEYS) - {"rule_consistency", "exposition", "plausibility", "term_consistency"}
        self.assertEqual(extra, {"power_rules", "setting_depth", "scale"})

    def test_world_map_drops_invented_quotes(self) -> None:
        raw = _world_map()
        raw["events"].append({"para": 1, "kind": "rule", "what": "지어낸 규칙", "quote": "원고에 없는 문장"})
        raw["events"].append({"para": 99, "kind": "rule", "what": "없는 문단", "quote": ""})
        world_map, dropped = genre_world.normalize_world_map(raw, PARAS)
        self.assertEqual([event["para"] for event in world_map["events"]], [1, 2])
        self.assertEqual({row["reason"] for row in dropped}, {"quote_invalid", "para_invalid"})
        self.assertEqual(world_map["rules"], ["손짓으로 문이 열린다"])

    def test_rubric_uses_world_keys_only(self) -> None:
        # 수수께끼형 항목은 세계형 루브릭에 들어올 수 없다
        raw = _rubric(["rule_consistency", "clue_fairness"])
        rubric, _ = genre_world.normalize_rubric(raw, PARAS, needed=["rule_consistency", "exposition"])
        self.assertEqual([item["key"] for item in rubric["items"]], ["rule_consistency", "exposition"])
        self.assertTrue(rubric["items"][1]["missing"])

    def test_cards_are_note_only_with_world_tags(self) -> None:
        genre = {
            "items": [
                _item("rule_consistency", "problem"),
                _item("exposition", "room"),
                _item("power_rules", "room"),
                _item("ripple", "works"),
            ],
            "world_map": _world_map(),
        }
        cards, dropped = genre_world.build_cards(genre, list(PARAS.values()), 5)
        self.assertEqual([c["style_type"] for c in cards], ["rule_break", "info_dump", "power_rule_break"])
        self.assertEqual([c["impact"] for c in cards], [5, 3, 4])
        self.assertEqual(dropped, [])
        for card in cards:
            self.assertIsNone(card["suggestion"])
            self.assertEqual(card["scene_id"], 5)

    def test_broken_promise_is_not_carded(self) -> None:
        item = _item("rule_consistency", "room", note="마법의 비용이 사라지는 장면이 약하다.")
        genre = {"items": [item]}
        cards, dropped = genre_world.build_cards(
            genre, list(PARAS.values()), 1, promise_broken="마법의 비용이 사라진다"
        )
        self.assertEqual(cards, [])
        self.assertEqual(dropped[0]["reason"], "author_promise")

    def test_protected_paragraphs_and_stripping(self) -> None:
        genre = {"world_map": _world_map()}
        # 규칙(1)은 보호하고 설명 덩어리(2)는 보호하지 않는다
        self.assertEqual(genre_world.protected_paragraphs(genre), {1})
        card = {"start_para": 1, "end_para": 1, "suggestion": "고친 문장", "edit_plan": "x", "warnings_json": []}
        self.assertTrue(genre_common.strip_suggestion_near(card, {1}, genre_world.TOUCH_WARNING))
        self.assertIsNone(card["suggestion"])
        self.assertEqual(card["warnings_json"][0]["code"], "rule_touch")

    def test_registry_and_markdown(self) -> None:
        self.assertIs(genre_layers.layer_for("world"), genre_world)
        self.assertIsNone(genre_layers.layer_for("style"))  # 문체형은 일반문학 엔진을 쓴다
        self.assertIsNone(genre_layers.layer_for(""))
        md = genre_layers.render_genre_md(
            {"group": "world", "summary": "총평", "items": [_item("rule_consistency", "room")], "top_tasks": []}
        )
        self.assertIn("## 장르 진단 (세계형)", md)
        self.assertIn("세계 규칙의 일관성", md)


class WorldFake(PipelineFake):
    def __init__(self, *args, rubric_keys=None, rubric=None, fail_map=False, world_para3=False, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.rubric_keys = rubric_keys or SF_KEYS
        self.rubric = rubric
        self.fail_map = fail_map
        self.world_para3 = world_para3

    def generate(self, prompt: str, **kwargs):
        if "「설정 지도」를 만든다" in prompt:
            with self._lock:
                self.prompts.append(prompt)
            if self.fail_map:
                return FakeClaude()._as_result({"parsed": None, "text": "NOT JSON", "usage": dict(self.usage)})
            world_map = _world_map()
            if self.world_para3:
                world_map["events"].append(
                    {"para": 3, "kind": "rule", "what": "고개가 규칙이다", "quote": "그녀는 고개를 돌렸다"}
                )
            return FakeClaude()._as_result({"parsed": world_map, "usage": dict(self.usage)})
        if "「장르 진단 루브릭」을 판정한다" in prompt:
            with self._lock:
                self.prompts.append(prompt)
            rubric = self.rubric or _rubric(self.rubric_keys)
            return FakeClaude()._as_result({"parsed": rubric, "usage": dict(self.usage)})
        result = super().generate(prompt, **kwargs)
        if "첨삭 카드 1장" in prompt and isinstance(result.get("parsed"), dict):
            result["parsed"]["suggestion"] = "세 번째 문단이다. 그냥 정원이다. 그녀는 고개를 돌렸다."
        return result

    def world_prompts(self) -> list[str]:
        return [p for p in self.prompts if "설정 지도" in p]


class WorldRunTests(GenreRunHarness):
    def use_fake(self, **kwargs) -> WorldFake:
        fake = WorldFake(consistency=_default_consistency(), report=_default_report(), **kwargs)
        set_client_for_tests(fake)
        return fake

    def genre_cards(self, run: dict) -> list[dict]:
        return [c for c in run["cards"] if str(c.get("report_ref") or "").startswith("GENRE:")]

    def test_sf_run_adds_world_report(self) -> None:
        fake = self.use_fake()
        project_id, scene_id = self.create_scene("sf")
        run = self.run_and_wait(project_id, scene_id)
        self.assertEqual(run["status"], "ok", run)
        genre = run["report"]["genre"]
        self.assertEqual(genre["group"], "world")
        self.assertEqual([item["key"] for item in genre["items"]], SF_KEYS)
        self.assertTrue(genre["world_map"]["events"])
        self.assertNotIn("clue_map", genre)
        self.assertIn("## 장르 진단 (세계형)", run["report_md"])
        self.assertEqual(len(fake.world_prompts()), 2)
        self.assertEqual(run["params"].get("genre_group"), "world")
        stages = [row.get("stage") for row in run["params"]["usage"]["stages"]]
        self.assertIn("genre_world_map", stages)
        self.assertIn("genre_rubric", stages)

    def test_traditional_fantasy_gets_the_extra_items(self) -> None:
        self.use_fake(rubric_keys=FANTASY_KEYS)
        project_id, scene_id = self.create_scene("traditional")
        run = self.run_and_wait(project_id, scene_id)
        keys = [item["key"] for item in run["report"]["genre"]["items"]]
        self.assertEqual(keys, FANTASY_KEYS)
        self.assertEqual(run["report"]["genre"]["main_genre"], "traditional")

    def test_world_prompt_carries_the_promises(self) -> None:
        fake = self.use_fake()
        project_id, scene_id = self.create_scene("sf")
        status, saved = self.request(
            "POST",
            f"/api/projects/{project_id}/genre-promise",
            {"promise_kept": "세계 규칙은 끝까지 지킨다"},
        )
        self.assertEqual(status, 200, saved)
        run = self.run_and_wait(project_id, scene_id)
        rubric_prompt = [p for p in fake.prompts if "「장르 진단 루브릭」을 판정한다" in p][0]
        self.assertIn("세계 규칙은 끝까지 지킨다", rubric_prompt)
        self.assertIn("kept_promise", rubric_prompt)
        self.assertIn("참고 의견", rubric_prompt)  # 현실 과학은 판정하지 않는다
        self.assertTrue(run["report"]["genre"]["promise"]["kept"])

    def test_puzzle_projects_do_not_use_world_prompts(self) -> None:
        fake = self.use_fake()
        project_id, scene_id = self.create_scene("mystery")
        self.run_and_wait(project_id, scene_id)
        self.assertEqual(fake.world_prompts(), [])

    def test_run_creates_note_only_world_cards(self) -> None:
        rubric = _rubric(["rule_consistency", "exposition"])
        rubric["items"][0]["verdict"] = "problem"
        rubric["items"][1]["verdict"] = "room"
        self.use_fake(rubric=rubric)
        project_id, scene_id = self.create_scene("sf")
        run = self.run_and_wait(project_id, scene_id)
        cards = self.genre_cards(run)
        self.assertEqual(sorted(c["style_type"] for c in cards), ["info_dump", "rule_break"])
        self.assertTrue(all(not c.get("suggestion") for c in cards))
        self.assertEqual(int(run["planned_cards"]), len(run["cards"]))

    def test_suggestion_on_a_rule_paragraph_is_removed(self) -> None:
        self.use_fake(world_para3=True)
        project_id, scene_id = self.create_scene("sf")
        run = self.run_and_wait(project_id, scene_id)
        w1 = [c for c in run["cards"] if c.get("report_ref") == "W1"][0]
        self.assertFalse(w1.get("suggestion"))
        codes = [w.get("code") for w in (w1.get("warnings") or w1.get("warnings_json") or [])]
        self.assertIn("rule_touch", codes)

    def test_suggestion_stays_when_no_rule_is_touched(self) -> None:
        self.use_fake()
        project_id, scene_id = self.create_scene("sf")
        run = self.run_and_wait(project_id, scene_id)
        w1 = [c for c in run["cards"] if c.get("report_ref") == "W1"][0]
        self.assertTrue(w1.get("suggestion"))

    def test_broken_promise_removes_matching_cards(self) -> None:
        rubric = _rubric(["rule_consistency"])
        rubric["items"][0].update(verdict="room", note="마법의 비용이 사라진다는 설정이 약하다.")
        self.use_fake(rubric=rubric)
        project_id, scene_id = self.create_scene("traditional")
        status, saved = self.request(
            "POST",
            f"/api/projects/{project_id}/genre-promise",
            {"promise_broken": "해설이 깁니다. 마법의 비용이 사라진다"},
        )
        self.assertEqual(status, 200, saved)
        run = self.run_and_wait(project_id, scene_id)
        refs = [c.get("report_ref") for c in run["cards"]]
        self.assertNotIn("W1", refs)
        self.assertNotIn("GENRE:rule_consistency", refs)
        reasons = [(row.get("id"), row.get("reason")) for row in run["params"]["dropped"]]
        self.assertIn(("W1", "author_promise"), reasons)
        self.assertIn(("GENRE:rule_consistency", "author_promise"), reasons)

    def test_world_failure_does_not_stop_the_normal_report(self) -> None:
        self.use_fake(fail_map=True)
        project_id, scene_id = self.create_scene("sf")
        run = self.run_and_wait(project_id, scene_id)
        self.assertEqual(run["status"], "ok", run)
        self.assertNotIn("genre", run["report"])
        self.assertTrue(run["params"].get("genre_error"))


if __name__ == "__main__":
    unittest.main()
