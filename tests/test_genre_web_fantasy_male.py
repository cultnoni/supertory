"""무협판타지형(웹소설) 장르 리포트와 카드 (가짜 Claude, 임시 DB)."""

from __future__ import annotations

import unittest

from _genre_harness import GenreRunHarness
from feedback_pipeline import genre_common, genre_layers, genre_web_fantasy_male
from feedback_pipeline.claude_client import FakeClaude, set_client_for_tests
from feedback_pipeline.paragraphs import Paragraph
from test_feedback_pipeline import PipelineFake, _default_consistency, _default_report

PARAS = {
    1: Paragraph(1, "정원이 손을 내밀었다. 승혜는 그 손을 보았다.", "text"),
    2: Paragraph(2, "승혜는 그 말을 되뇌었다. 정원의 눈빛이 흔들렸다.", "text"),
    3: Paragraph(3, "세 번째 문단이다. 정원의 정원이 아니라 그냥 정원이다. 그녀는 고개를 돌렸다.", "text"),
}

BASE_KEYS = ["growth_pace", "exposition", "impact", "trope_use", "term_consistency"]
MURIM_KEYS = BASE_KEYS + ["faction_consistency"]
TRADITIONAL_KEYS = BASE_KEYS + ["rule_consistency"]
WEBNOVEL_MALE = {"purpose": "web_novel", "cluster_id": "webnovel", "sub_genre": "male"}


def _growth_map() -> dict:
    return {
        "events": [
            {"para": 1, "kind": "growth", "what": "경지가 오른다", "quote": "정원이 손을 내밀었다"},
            {"para": 2, "kind": "impact", "what": "일격을 주고받는다", "quote": "정원의 눈빛이 흔들렸다"},
        ],
        "rules": ["내공은 단전에서 나온다"],
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
        "summary": "성장 템포가 자연스럽다.",
        "items": [_item(key) for key in keys],
        "strengths": [
            {"title": "일격의 장면", "body": "타격이 장면으로 전달된다.", "para": 1, "quote": "정원이 손을 내밀었다"}
        ],
        "top_tasks": [{"title": "설정 풀기", "why": "설명을 사건으로 옮겨야 한다."}],
    }


class WebFantasyMaleHelperTests(unittest.TestCase):
    def test_required_keys_differ_by_detail(self) -> None:
        self.assertEqual(
            genre_web_fantasy_male.required_keys({"genre_detail": ""}, has_kept_avoid=False), BASE_KEYS
        )
        self.assertEqual(
            genre_web_fantasy_male.required_keys({"genre_detail": "murim"}, has_kept_avoid=False), MURIM_KEYS
        )
        self.assertEqual(
            genre_web_fantasy_male.required_keys({"genre_detail": "traditional"}, has_kept_avoid=False),
            TRADITIONAL_KEYS,
        )
        self.assertIn(
            "kept_avoid",
            genre_web_fantasy_male.required_keys({"genre_detail": ""}, has_kept_avoid=True),
        )

    def test_legacy_main_genre_maps_to_detail(self) -> None:
        self.assertEqual(
            genre_web_fantasy_male.required_keys({"main_genre": "historical"}, has_kept_avoid=False),
            BASE_KEYS + ["history_crossover"],
        )
        self.assertEqual(
            genre_web_fantasy_male.required_keys({"main_genre": "sports"}, has_kept_avoid=False),
            BASE_KEYS + ["match_rhythm"],
        )

    def test_growth_map_drops_invented_quotes(self) -> None:
        raw = _growth_map()
        raw["events"].append({"para": 1, "kind": "growth", "what": "지어낸 사건", "quote": "원고에 없는 문장"})
        raw["events"].append({"para": 99, "kind": "growth", "what": "없는 문단", "quote": ""})
        growth_map, dropped = genre_web_fantasy_male.normalize_growth_map(raw, PARAS)
        self.assertEqual([event["para"] for event in growth_map["events"]], [1, 2])
        self.assertEqual({row["reason"] for row in dropped}, {"quote_invalid", "para_invalid"})
        self.assertEqual(growth_map["rules"], ["내공은 단전에서 나온다"])

    def test_cards_are_note_only_with_fantasy_male_tags(self) -> None:
        genre = {
            "items": [
                _item("growth_pace", "problem"),
                _item("impact", "room"),
                _item("faction_consistency", "room"),
                _item("trope_use", "works"),
            ],
            "growth_map": _growth_map(),
        }
        cards, dropped = genre_web_fantasy_male.build_cards(genre, list(PARAS.values()), 5)
        self.assertEqual(
            [c["style_type"] for c in cards], ["pace_off", "impact_weak", "faction_break"]
        )
        self.assertEqual(dropped, [])
        for card in cards:
            self.assertIsNone(card["suggestion"])
            self.assertEqual(card["scene_id"], 5)

    def test_protected_paragraphs_and_stripping(self) -> None:
        genre = {"growth_map": _growth_map()}
        self.assertEqual(genre_web_fantasy_male.protected_paragraphs(genre), {1, 2})
        card = {"start_para": 1, "end_para": 1, "suggestion": "고친 문장", "edit_plan": "x", "warnings_json": []}
        self.assertTrue(genre_common.strip_suggestion_near(card, {1}, genre_web_fantasy_male.TOUCH_WARNING))
        self.assertIsNone(card["suggestion"])
        self.assertEqual(card["warnings_json"][0]["code"], "web_fantasy_male_touch")

    def test_registry_and_markdown(self) -> None:
        self.assertIs(genre_layers.layer_for("web_fantasy_male"), genre_web_fantasy_male)
        md = genre_layers.render_genre_md(
            {"group": "web_fantasy_male", "summary": "총평", "items": [_item("growth_pace", "room")], "top_tasks": []}
        )
        self.assertIn("## 웹소설 무협판타지 진단", md)
        self.assertIn("성장 템포", md)


class WebFantasyMaleFake(PipelineFake):
    def __init__(self, *args, rubric_keys=None, rubric=None, fail_map=False, world_para3=False, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.rubric_keys = rubric_keys or BASE_KEYS
        self.rubric = rubric
        self.fail_map = fail_map
        self.world_para3 = world_para3

    def generate(self, prompt: str, **kwargs):
        if "「성장·설정 지도」를 만든다" in prompt:
            with self._lock:
                self.prompts.append(prompt)
            if self.fail_map:
                return FakeClaude()._as_result({"parsed": None, "text": "NOT JSON", "usage": dict(self.usage)})
            growth_map = _growth_map()
            if self.world_para3:
                growth_map["events"].append(
                    {"para": 3, "kind": "rule", "what": "고개가 규칙이다", "quote": "그녀는 고개를 돌렸다"}
                )
            return FakeClaude()._as_result({"parsed": growth_map, "usage": dict(self.usage)})
        if "「장르 진단 루브릭」을 판정한다" in prompt:
            with self._lock:
                self.prompts.append(prompt)
            rubric = self.rubric or _rubric(self.rubric_keys)
            return FakeClaude()._as_result({"parsed": rubric, "usage": dict(self.usage)})
        result = super().generate(prompt, **kwargs)
        if "첨삭 카드 1장" in prompt and isinstance(result.get("parsed"), dict):
            result["parsed"]["suggestion"] = "세 번째 문단이다. 그냥 정원이다. 그녀는 고개를 돌렸다."
        return result

    def growth_prompts(self) -> list[str]:
        return [p for p in self.prompts if "성장·설정 지도" in p]


class WebFantasyMaleRunTests(GenreRunHarness):
    def use_fake(self, **kwargs) -> WebFantasyMaleFake:
        fake = WebFantasyMaleFake(consistency=_default_consistency(), report=_default_report(), **kwargs)
        set_client_for_tests(fake)
        return fake

    def genre_cards(self, run: dict) -> list[dict]:
        return [c for c in run["cards"] if str(c.get("report_ref") or "").startswith("GENRE:")]

    def test_fantasy_run_adds_fantasy_male_report(self) -> None:
        fake = self.use_fake()
        project_id, scene_id = self.create_scene("fantasy", **WEBNOVEL_MALE)
        run = self.run_and_wait(project_id, scene_id)
        self.assertEqual(run["status"], "ok", run)
        genre = run["report"]["genre"]
        self.assertEqual(genre["group"], "web_fantasy_male")
        self.assertEqual([item["key"] for item in genre["items"]], BASE_KEYS)
        self.assertTrue(genre["growth_map"]["events"])
        self.assertIn("## 웹소설 무협판타지 진단", run["report_md"])
        self.assertEqual(len(fake.growth_prompts()), 2)
        self.assertEqual(run["params"].get("genre_group"), "web_fantasy_male")
        stages = [row.get("stage") for row in run["params"]["usage"]["stages"]]
        self.assertIn("genre_growth_map", stages)
        self.assertIn("genre_rubric", stages)

    def test_murim_detail_gets_the_extra_item(self) -> None:
        self.use_fake(rubric_keys=MURIM_KEYS)
        project_id, scene_id = self.create_scene("fantasy", genre_detail="murim", **WEBNOVEL_MALE)
        run = self.run_and_wait(project_id, scene_id)
        keys = [item["key"] for item in run["report"]["genre"]["items"]]
        self.assertEqual(keys, MURIM_KEYS)

    def test_female_fantasy_does_not_use_fantasy_male_prompts(self) -> None:
        fake = self.use_fake()
        project_id, scene_id = self.create_scene(
            "female_fantasy", purpose="web_novel", cluster_id="webnovel", sub_genre="dimension"
        )
        self.run_and_wait(project_id, scene_id)
        self.assertEqual(fake.growth_prompts(), [])

    def test_genre_literature_traditional_does_not_use_fantasy_male_prompts(self) -> None:
        fake = self.use_fake()
        project_id, scene_id = self.create_scene("traditional")
        self.run_and_wait(project_id, scene_id)
        self.assertEqual(fake.growth_prompts(), [])

    def test_run_creates_note_only_fantasy_male_cards(self) -> None:
        rubric = _rubric(["growth_pace", "impact"])
        rubric["items"][0]["verdict"] = "problem"
        rubric["items"][1]["verdict"] = "room"
        self.use_fake(
            rubric={**rubric, "items": rubric["items"] + [_item("exposition"), _item("trope_use"), _item("term_consistency")]}
        )
        project_id, scene_id = self.create_scene("fantasy", **WEBNOVEL_MALE)
        run = self.run_and_wait(project_id, scene_id)
        cards = self.genre_cards(run)
        self.assertEqual(sorted(c["style_type"] for c in cards), ["impact_weak", "pace_off"])
        self.assertTrue(all(not c.get("suggestion") for c in cards))
        self.assertEqual(int(run["planned_cards"]), len(run["cards"]))

    def test_suggestion_on_a_growth_paragraph_is_removed(self) -> None:
        self.use_fake(world_para3=True)
        project_id, scene_id = self.create_scene("fantasy", **WEBNOVEL_MALE)
        run = self.run_and_wait(project_id, scene_id)
        w1 = [c for c in run["cards"] if c.get("report_ref") == "W1"][0]
        self.assertFalse(w1.get("suggestion"))
        codes = [w.get("code") for w in (w1.get("warnings") or w1.get("warnings_json") or [])]
        self.assertIn("web_fantasy_male_touch", codes)

    def test_used_cliche_removes_matching_cards(self) -> None:
        rubric = _rubric(BASE_KEYS)
        rubric["items"][0].update(verdict="room", note="회귀 설정이라 설명이 깁니다.")
        self.use_fake(rubric=rubric)
        project_id, scene_id = self.create_scene("fantasy", **WEBNOVEL_MALE)
        status, saved = self.request(
            "POST",
            f"/api/projects/{project_id}/webnovel-cliche",
            {"cliche_used": "회귀 설정이라 설명이 깁니다"},
        )
        self.assertEqual(status, 200, saved)
        run = self.run_and_wait(project_id, scene_id)
        refs = [c.get("report_ref") for c in run["cards"]]
        self.assertNotIn("GENRE:growth_pace", refs)
        reasons = [(row.get("id"), row.get("reason")) for row in run["params"]["dropped"]]
        self.assertIn(("GENRE:growth_pace", "author_promise"), reasons)

    def test_fantasy_male_failure_does_not_stop_the_normal_report(self) -> None:
        self.use_fake(fail_map=True)
        project_id, scene_id = self.create_scene("fantasy", **WEBNOVEL_MALE)
        run = self.run_and_wait(project_id, scene_id)
        self.assertEqual(run["status"], "ok", run)
        self.assertNotIn("genre", run["report"])
        self.assertTrue(run["params"].get("genre_error"))


if __name__ == "__main__":
    unittest.main()
