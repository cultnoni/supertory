"""로맨스형(웹소설) 장르 리포트와 카드 (가짜 Claude, 임시 DB)."""

from __future__ import annotations

import unittest

from _genre_harness import GenreRunHarness
from feedback_pipeline import genre_common, genre_layers, genre_web_romance
from feedback_pipeline.claude_client import FakeClaude, set_client_for_tests
from feedback_pipeline.paragraphs import Paragraph
from test_feedback_pipeline import PipelineFake, _default_consistency, _default_report

PARAS = {
    1: Paragraph(1, "정원이 손을 내밀었다. 승혜는 그 손을 보았다.", "text"),
    2: Paragraph(2, "승혜는 그 말을 되뇌었다. 정원의 눈빛이 흔들렸다.", "text"),
    3: Paragraph(3, "세 번째 문단이다. 정원의 정원이 아니라 그냥 정원이다. 그녀는 고개를 돌렸다.", "text"),
}
KEYS = ["chemistry", "banter_pacing", "conflict_credibility", "sub_balance", "cliche_use"]
WEBNOVEL = {"purpose": "web_novel", "cluster_id": "webnovel", "sub_genre": "modern"}
GENRE_LIT_ROMANCE = {"romance_structure": "emotional", "romance_setting": "contemporary"}


def _chemistry_map() -> dict:
    return {
        "events": [
            {"para": 1, "kind": "closer", "what": "손을 내밀어 가까워진다", "quote": "정원이 손을 내밀었다"},
            {"para": 2, "kind": "banter", "what": "밀당이 오간다", "quote": "정원의 눈빛이 흔들렸다"},
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
        "summary": "케미가 자연스럽게 움직인다.",
        "items": [_item(key) for key in keys],
        "strengths": [
            {"title": "손의 장면", "body": "손동작이 케미를 보여 준다.", "para": 1, "quote": "정원이 손을 내밀었다"}
        ],
        "top_tasks": [{"title": "설명 줄이기", "why": "장면으로 옮겨야 한다."}],
    }


class WebRomanceHelperTests(unittest.TestCase):
    def test_required_keys(self) -> None:
        self.assertEqual(genre_web_romance.required_keys(False), KEYS)
        self.assertEqual(genre_web_romance.required_keys(True), KEYS + ["kept_avoid"])

    def test_chemistry_map_drops_invented_quotes(self) -> None:
        raw = _chemistry_map()
        raw["events"].append({"para": 1, "kind": "closer", "what": "지어낸 일", "quote": "원고에 없는 문장"})
        raw["events"].append({"para": 99, "kind": "closer", "what": "없는 문단", "quote": ""})
        chemistry_map, dropped = genre_web_romance.normalize_chemistry_map(raw, PARAS)
        self.assertEqual([event["para"] for event in chemistry_map["events"]], [1, 2])
        self.assertEqual({row["reason"] for row in dropped}, {"quote_invalid", "para_invalid"})
        self.assertEqual(chemistry_map["pairs"][0]["who"], "정원·승혜")
        self.assertEqual(chemistry_map["ends_on"], "설렘과 불안이 섞여 있다")

    def test_cards_are_note_only_with_web_romance_tags(self) -> None:
        genre = {
            "items": [
                _item("chemistry", "room"),
                _item("conflict_credibility", "problem"),
                _item("sub_balance", "room"),
                _item("banter_pacing", "works"),
            ]
        }
        cards, dropped = genre_web_romance.build_cards(genre, list(PARAS.values()), 3)
        self.assertEqual(
            [c["style_type"] for c in cards], ["chemistry_weak", "conflict_forced", "sub_overload"]
        )
        self.assertEqual(dropped, [])
        self.assertTrue(all(not c["suggestion"] for c in cards))

    def test_protected_paragraphs_are_where_the_chemistry_moves(self) -> None:
        genre = {"chemistry_map": _chemistry_map()}
        self.assertEqual(genre_web_romance.protected_paragraphs(genre), {1, 2})
        card = {"start_para": 1, "end_para": 1, "suggestion": "고친 문장", "edit_plan": "x", "warnings_json": []}
        self.assertTrue(genre_common.strip_suggestion_near(card, {1}, genre_web_romance.TOUCH_WARNING))
        self.assertEqual(card["warnings_json"][0]["code"], "web_romance_touch")

    def test_registry_and_markdown(self) -> None:
        self.assertIs(genre_layers.layer_for("web_romance"), genre_web_romance)
        md = genre_layers.render_genre_md(
            {"group": "web_romance", "summary": "총평", "items": [_item("chemistry", "room")], "top_tasks": []}
        )
        self.assertIn("## 웹소설 로맨스 진단", md)
        self.assertIn("케미", md)

    def test_emphasis_uses_the_romance_playbook(self) -> None:
        text = genre_web_romance._emphasis_text({"main_genre": "romance", "sub_genre": "modern"})
        self.assertIn("[로맨스 기준집]", text)
        self.assertEqual(genre_web_romance._emphasis_text({}), "")


class WebRomanceFake(PipelineFake):
    def __init__(self, *args, rubric=None, fail_map=False, conflict_para3=False, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.rubric = rubric
        self.fail_map = fail_map
        self.conflict_para3 = conflict_para3

    def generate(self, prompt: str, **kwargs):
        if "「케미 지도」를 만든다" in prompt:
            with self._lock:
                self.prompts.append(prompt)
            if self.fail_map:
                return FakeClaude()._as_result({"parsed": None, "text": "NOT JSON", "usage": dict(self.usage)})
            chemistry_map = _chemistry_map()
            if self.conflict_para3:
                chemistry_map["events"].append(
                    {"para": 3, "kind": "conflict", "what": "고개를 돌려 외면한다", "quote": "그녀는 고개를 돌렸다"}
                )
            return FakeClaude()._as_result({"parsed": chemistry_map, "usage": dict(self.usage)})
        if "「장르 진단 루브릭」을 판정한다" in prompt:
            with self._lock:
                self.prompts.append(prompt)
            return FakeClaude()._as_result({"parsed": self.rubric or _rubric(KEYS), "usage": dict(self.usage)})
        result = super().generate(prompt, **kwargs)
        if "첨삭 카드 1장" in prompt and isinstance(result.get("parsed"), dict):
            result["parsed"]["suggestion"] = "세 번째 문단이다. 그냥 정원이다. 그녀는 고개를 돌렸다."
        return result

    def chemistry_prompts(self) -> list[str]:
        return [p for p in self.prompts if "케미 지도" in p]

    def rubric_prompt(self) -> str:
        return [p for p in self.prompts if "「장르 진단 루브릭」을 판정한다" in p][0]


class WebRomanceRunTests(GenreRunHarness):
    def use_fake(self, **kwargs) -> WebRomanceFake:
        fake = WebRomanceFake(consistency=_default_consistency(), report=_default_report(), **kwargs)
        set_client_for_tests(fake)
        return fake

    def genre_cards(self, run: dict) -> list[dict]:
        return [c for c in run["cards"] if str(c.get("report_ref") or "").startswith("GENRE:")]

    def test_romance_run_adds_web_romance_report(self) -> None:
        fake = self.use_fake()
        project_id, scene_id = self.create_scene("romance", **WEBNOVEL)
        run = self.run_and_wait(project_id, scene_id)
        self.assertEqual(run["status"], "ok", run)
        genre = run["report"]["genre"]
        self.assertEqual(genre["group"], "web_romance")
        self.assertEqual([item["key"] for item in genre["items"]], KEYS)
        self.assertTrue(genre["chemistry_map"]["events"])
        self.assertIn("## 웹소설 로맨스 진단", run["report_md"])
        self.assertEqual(len(fake.chemistry_prompts()), 2)
        self.assertEqual(run["params"].get("genre_group"), "web_romance")
        stages = [row.get("stage") for row in run["params"]["usage"]["stages"]]
        self.assertIn("genre_chemistry_map", stages)
        self.assertIn("genre_rubric", stages)

    def test_playbook_reaches_the_prompt(self) -> None:
        fake = self.use_fake()
        project_id, scene_id = self.create_scene("romance", **WEBNOVEL)
        self.run_and_wait(project_id, scene_id)
        prompt = fake.rubric_prompt()
        self.assertIn("[로맨스 기준집]", prompt)

    def test_author_cliche_avoid_reaches_the_prompt(self) -> None:
        fake = self.use_fake()
        project_id, scene_id = self.create_scene("romance", **WEBNOVEL)
        status, saved = self.request(
            "POST",
            f"/api/projects/{project_id}/webnovel-cliche",
            {"cliche_avoid": "나쁜 남자 서사는 지양"},
        )
        self.assertEqual(status, 200, saved)
        run = self.run_and_wait(project_id, scene_id)
        self.assertIn("나쁜 남자 서사는 지양", fake.rubric_prompt())
        self.assertIn("kept_avoid", fake.rubric_prompt())
        self.assertTrue(run["report"]["genre"]["promise"]["kept"])

    def test_other_groups_do_not_use_web_romance_prompts(self) -> None:
        fake = self.use_fake()
        project_id, scene_id = self.create_scene("fantasy", **WEBNOVEL)
        self.run_and_wait(project_id, scene_id)
        self.assertEqual(fake.chemistry_prompts(), [])

    def test_genre_literature_romance_does_not_use_web_romance_prompts(self) -> None:
        fake = self.use_fake()
        project_id, scene_id = self.create_scene("romance", **GENRE_LIT_ROMANCE)
        self.run_and_wait(project_id, scene_id)
        self.assertEqual(fake.chemistry_prompts(), [])

    def test_run_creates_note_only_web_romance_cards(self) -> None:
        rubric = _rubric(["chemistry", "conflict_credibility"])
        rubric["items"][0]["verdict"] = "room"
        rubric["items"][1]["verdict"] = "problem"
        self.use_fake(
            rubric={**rubric, "items": rubric["items"] + [_item("banter_pacing"), _item("sub_balance"), _item("cliche_use")]}
        )
        project_id, scene_id = self.create_scene("romance", **WEBNOVEL)
        run = self.run_and_wait(project_id, scene_id)
        cards = self.genre_cards(run)
        self.assertEqual(sorted(c["style_type"] for c in cards), ["chemistry_weak", "conflict_forced"])
        self.assertTrue(all(not c.get("suggestion") for c in cards))
        self.assertEqual(int(run["planned_cards"]), len(run["cards"]))

    def test_suggestion_on_a_moving_paragraph_is_removed(self) -> None:
        self.use_fake(conflict_para3=True)
        project_id, scene_id = self.create_scene("romance", **WEBNOVEL)
        run = self.run_and_wait(project_id, scene_id)
        w1 = [c for c in run["cards"] if c.get("report_ref") == "W1"][0]
        self.assertFalse(w1.get("suggestion"))
        codes = [w.get("code") for w in (w1.get("warnings") or w1.get("warnings_json") or [])]
        self.assertIn("web_romance_touch", codes)

    def test_used_cliche_removes_matching_cards(self) -> None:
        rubric = _rubric(KEYS)
        rubric["items"][0].update(verdict="room", note="재벌남 설정이라 설명이 깁니다.")
        self.use_fake(rubric=rubric)
        project_id, scene_id = self.create_scene("romance", **WEBNOVEL)
        status, saved = self.request(
            "POST",
            f"/api/projects/{project_id}/webnovel-cliche",
            {"cliche_used": "재벌남 설정이라 설명이 깁니다"},
        )
        self.assertEqual(status, 200, saved)
        run = self.run_and_wait(project_id, scene_id)
        refs = [c.get("report_ref") for c in run["cards"]]
        self.assertNotIn("GENRE:chemistry", refs)
        reasons = [(row.get("id"), row.get("reason")) for row in run["params"]["dropped"]]
        self.assertIn(("GENRE:chemistry", "author_promise"), reasons)

    def test_web_romance_failure_does_not_stop_the_normal_report(self) -> None:
        self.use_fake(fail_map=True)
        project_id, scene_id = self.create_scene("romance", **WEBNOVEL)
        run = self.run_and_wait(project_id, scene_id)
        self.assertEqual(run["status"], "ok", run)
        self.assertNotIn("genre", run["report"])
        self.assertTrue(run["params"].get("genre_error"))


if __name__ == "__main__":
    unittest.main()
