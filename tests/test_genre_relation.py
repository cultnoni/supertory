"""관계형(로맨스) 장르 리포트와 카드 (가짜 Claude, 임시 DB)."""

from __future__ import annotations

import unittest

from _genre_harness import GenreRunHarness
from feedback_pipeline import genre_common, genre_layers, genre_relation
from feedback_pipeline.claude_client import FakeClaude, set_client_for_tests
from feedback_pipeline.paragraphs import Paragraph
from test_feedback_pipeline import PipelineFake, _default_consistency, _default_report

PARAS = {
    1: Paragraph(1, "정원이 손을 내밀었다. 승혜는 그 손을 보았다.", "text"),
    2: Paragraph(2, "승혜는 그 말을 되뇌었다. 정원의 눈빛이 흔들렸다.", "text"),
    3: Paragraph(3, "세 번째 문단이다. 정원의 정원이 아니라 그냥 정원이다. 그녀는 고개를 돌렸다.", "text"),
}
KEYS = ["relationship_arc", "emotion_line", "conflict_drive", "pacing"]
EMOTIONAL = {"romance_structure": "emotional", "romance_setting": "contemporary"}


def _relation_map() -> dict:
    return {
        "events": [
            {"para": 1, "kind": "closer", "what": "손을 내밀어 가까워진다", "quote": "정원이 손을 내밀었다"},
            {"para": 2, "kind": "tell", "what": "감정을 설명한다", "quote": "정원의 눈빛이 흔들렸다"},
        ],
        "pairs": [{"who": "정원·승혜", "at_start": "어색한 사이", "at_end": "조금 가까워진 사이"}],
        "obstacles": ["신분 차이"],
        "ends_on": "설렘과 불안이 섞여 있다",
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
        "summary": "관계가 자연스럽게 움직인다.",
        "items": [_item(key) for key in keys],
        "strengths": [
            {"title": "손의 장면", "body": "손동작이 감정을 보여 준다.", "para": 1, "quote": "정원이 손을 내밀었다"}
        ],
        "top_tasks": [{"title": "감정 설명 줄이기", "why": "행동으로 옮겨야 한다."}],
    }


class RelationHelperTests(unittest.TestCase):
    def test_required_keys(self) -> None:
        self.assertEqual(genre_relation.required_keys(False), KEYS)
        self.assertEqual(genre_relation.required_keys(True), KEYS + ["kept_promise"])

    def test_relation_map_drops_invented_quotes(self) -> None:
        raw = _relation_map()
        raw["events"].append({"para": 1, "kind": "closer", "what": "지어낸 일", "quote": "원고에 없는 문장"})
        raw["events"].append({"para": 99, "kind": "closer", "what": "없는 문단", "quote": ""})
        relation_map, dropped = genre_relation.normalize_relation_map(raw, PARAS)
        self.assertEqual([event["para"] for event in relation_map["events"]], [1, 2])
        self.assertEqual({row["reason"] for row in dropped}, {"quote_invalid", "para_invalid"})
        self.assertEqual(relation_map["pairs"][0]["who"], "정원·승혜")
        self.assertEqual(relation_map["ends_on"], "설렘과 불안이 섞여 있다")

    def test_cards_are_note_only_with_relation_tags(self) -> None:
        genre = {
            "items": [
                _item("emotion_line", "room"),
                _item("relationship_arc", "problem"),
                _item("conflict_drive", "room"),
                _item("pacing", "works"),
            ]
        }
        cards, dropped = genre_relation.build_cards(genre, list(PARAS.values()), 3)
        self.assertEqual([c["style_type"] for c in cards], ["emotion_telling", "arc_leap", "conflict_weak"])
        self.assertEqual([c["impact"] for c in cards], [4, 5, 3])
        self.assertEqual(dropped, [])
        self.assertTrue(all(not c["suggestion"] for c in cards))

    def test_protected_paragraphs_are_where_the_relation_moves(self) -> None:
        genre = {"relation_map": _relation_map()}
        self.assertEqual(genre_relation.protected_paragraphs(genre), {1})  # tell(2)은 보호하지 않는다
        card = {"start_para": 1, "end_para": 1, "suggestion": "고친 문장", "edit_plan": "x", "warnings_json": []}
        self.assertTrue(genre_common.strip_suggestion_near(card, {1}, genre_relation.TOUCH_WARNING))
        self.assertEqual(card["warnings_json"][0]["code"], "relation_touch")

    def test_registry_and_markdown(self) -> None:
        self.assertIs(genre_layers.layer_for("relation"), genre_relation)
        md = genre_layers.render_genre_md(
            {"group": "relation", "summary": "총평", "items": [_item("emotion_line", "room")], "top_tasks": []}
        )
        self.assertIn("## 장르 진단 (관계형)", md)
        self.assertIn("감정선", md)

    def test_emphasis_uses_the_romance_playbook_and_axes(self) -> None:
        text = genre_relation._emphasis_text({"romance_structure": "emotional", "romance_setting": "contemporary"})
        self.assertIn("정서형", text)
        self.assertIn("[로맨스 기준집]", text)
        self.assertNotIn("사극", text)
        period = genre_relation._emphasis_text({"romance_structure": "plot_driven", "romance_setting": "period"})
        self.assertIn("사건형", period)
        self.assertIn("사극", period)
        self.assertIn("사극 추가 점검", period)
        self.assertEqual(genre_relation._emphasis_text({}).count("정서형"), 0)


class RelationFake(PipelineFake):
    def __init__(self, *args, rubric=None, fail_map=False, conflict_para3=False, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.rubric = rubric
        self.fail_map = fail_map
        self.conflict_para3 = conflict_para3

    def generate(self, prompt: str, **kwargs):
        if "「관계 지도」를 만든다" in prompt:
            with self._lock:
                self.prompts.append(prompt)
            if self.fail_map:
                return FakeClaude()._as_result({"parsed": None, "text": "NOT JSON", "usage": dict(self.usage)})
            relation_map = _relation_map()
            if self.conflict_para3:
                relation_map["events"].append(
                    {"para": 3, "kind": "conflict", "what": "고개를 돌려 외면한다", "quote": "그녀는 고개를 돌렸다"}
                )
            return FakeClaude()._as_result({"parsed": relation_map, "usage": dict(self.usage)})
        if "「장르 진단 루브릭」을 판정한다" in prompt:
            with self._lock:
                self.prompts.append(prompt)
            return FakeClaude()._as_result({"parsed": self.rubric or _rubric(KEYS), "usage": dict(self.usage)})
        result = super().generate(prompt, **kwargs)
        if "첨삭 카드 1장" in prompt and isinstance(result.get("parsed"), dict):
            result["parsed"]["suggestion"] = "세 번째 문단이다. 그냥 정원이다. 그녀는 고개를 돌렸다."
        return result

    def relation_prompts(self) -> list[str]:
        return [p for p in self.prompts if "관계 지도" in p]

    def rubric_prompt(self) -> str:
        return [p for p in self.prompts if "「장르 진단 루브릭」을 판정한다" in p][0]


class RelationRunTests(GenreRunHarness):
    def use_fake(self, **kwargs) -> RelationFake:
        fake = RelationFake(consistency=_default_consistency(), report=_default_report(), **kwargs)
        set_client_for_tests(fake)
        return fake

    def genre_cards(self, run: dict) -> list[dict]:
        return [c for c in run["cards"] if str(c.get("report_ref") or "").startswith("GENRE:")]

    def test_romance_run_adds_relation_report(self) -> None:
        fake = self.use_fake()
        project_id, scene_id = self.create_scene("romance", **EMOTIONAL)
        run = self.run_and_wait(project_id, scene_id)
        self.assertEqual(run["status"], "ok", run)
        genre = run["report"]["genre"]
        self.assertEqual(genre["group"], "relation")
        self.assertEqual([item["key"] for item in genre["items"]], KEYS)
        self.assertTrue(genre["relation_map"]["events"])
        self.assertNotIn("clue_map", genre)
        self.assertIn("## 장르 진단 (관계형)", run["report_md"])
        self.assertEqual(len(fake.relation_prompts()), 2)
        self.assertEqual(run["params"].get("genre_group"), "relation")
        stages = [row.get("stage") for row in run["params"]["usage"]["stages"]]
        self.assertIn("genre_relation_map", stages)
        self.assertIn("genre_rubric", stages)

    def test_axes_and_playbook_reach_the_prompt(self) -> None:
        fake = self.use_fake()
        project_id, scene_id = self.create_scene(
            "romance", romance_structure="plot_driven", romance_setting="period"
        )
        self.run_and_wait(project_id, scene_id)
        prompt = fake.rubric_prompt()
        self.assertIn("사건형", prompt)
        self.assertIn("사극", prompt)
        self.assertIn("[로맨스 기준집]", prompt)

    def test_author_promises_reach_the_prompt(self) -> None:
        fake = self.use_fake()
        project_id, scene_id = self.create_scene("romance", **EMOTIONAL)
        status, saved = self.request(
            "POST",
            f"/api/projects/{project_id}/genre-promise",
            {"promise_kept": "고백은 마지막 회차까지 아낀다"},
        )
        self.assertEqual(status, 200, saved)
        run = self.run_and_wait(project_id, scene_id)
        self.assertIn("고백은 마지막 회차까지 아낀다", fake.rubric_prompt())
        self.assertIn("kept_promise", fake.rubric_prompt())
        self.assertTrue(run["report"]["genre"]["promise"]["kept"])

    def test_other_groups_do_not_use_relation_prompts(self) -> None:
        fake = self.use_fake()
        project_id, scene_id = self.create_scene("mystery")
        self.run_and_wait(project_id, scene_id)
        self.assertEqual(fake.relation_prompts(), [])

    def test_run_creates_note_only_relation_cards(self) -> None:
        rubric = _rubric(["emotion_line", "relationship_arc"])
        rubric["items"][0]["verdict"] = "room"
        rubric["items"][1]["verdict"] = "problem"
        self.use_fake(rubric={**rubric, "items": rubric["items"] + [_item("conflict_drive"), _item("pacing")]})
        project_id, scene_id = self.create_scene("romance", **EMOTIONAL)
        run = self.run_and_wait(project_id, scene_id)
        cards = self.genre_cards(run)
        self.assertEqual(sorted(c["style_type"] for c in cards), ["arc_leap", "emotion_telling"])
        self.assertTrue(all(not c.get("suggestion") for c in cards))
        self.assertEqual(int(run["planned_cards"]), len(run["cards"]))

    def test_suggestion_on_a_moving_paragraph_is_removed(self) -> None:
        self.use_fake(conflict_para3=True)
        project_id, scene_id = self.create_scene("romance", **EMOTIONAL)
        run = self.run_and_wait(project_id, scene_id)
        w1 = [c for c in run["cards"] if c.get("report_ref") == "W1"][0]
        self.assertFalse(w1.get("suggestion"))
        codes = [w.get("code") for w in (w1.get("warnings") or w1.get("warnings_json") or [])]
        self.assertIn("relation_touch", codes)

    def test_suggestion_stays_when_the_relation_is_not_touched(self) -> None:
        self.use_fake()
        project_id, scene_id = self.create_scene("romance", **EMOTIONAL)
        run = self.run_and_wait(project_id, scene_id)
        w1 = [c for c in run["cards"] if c.get("report_ref") == "W1"][0]
        self.assertTrue(w1.get("suggestion"))

    def test_broken_promise_removes_matching_cards(self) -> None:
        rubric = _rubric(KEYS)
        rubric["items"][1].update(verdict="room", note="감정을 직접 설명하지 않는 절제가 약하다.")
        self.use_fake(rubric=rubric)
        project_id, scene_id = self.create_scene("romance", **EMOTIONAL)
        status, saved = self.request(
            "POST",
            f"/api/projects/{project_id}/genre-promise",
            {"promise_broken": "해설이 깁니다. 감정을 직접 설명하지 않는다"},
        )
        self.assertEqual(status, 200, saved)
        run = self.run_and_wait(project_id, scene_id)
        refs = [c.get("report_ref") for c in run["cards"]]
        self.assertNotIn("W1", refs)
        self.assertNotIn("GENRE:emotion_line", refs)
        reasons = [(row.get("id"), row.get("reason")) for row in run["params"]["dropped"]]
        self.assertIn(("W1", "author_promise"), reasons)
        self.assertIn(("GENRE:emotion_line", "author_promise"), reasons)

    def test_relation_failure_does_not_stop_the_normal_report(self) -> None:
        self.use_fake(fail_map=True)
        project_id, scene_id = self.create_scene("romance", **EMOTIONAL)
        run = self.run_and_wait(project_id, scene_id)
        self.assertEqual(run["status"], "ok", run)
        self.assertNotIn("genre", run["report"])
        self.assertTrue(run["params"].get("genre_error"))


if __name__ == "__main__":
    unittest.main()
