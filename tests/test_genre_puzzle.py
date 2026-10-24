"""수수께끼형 장르 리포트: 정보 공개 지도와 장르 진단 (가짜 Claude, 임시 DB)."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

import app
import feedback_api
from feedback_pipeline import genre_puzzle
from feedback_pipeline.claude_client import (
    FakeClaude,
    set_client_for_tests,
    set_configured_for_tests,
)
from feedback_pipeline.paragraphs import Paragraph
from test_feedback_pipeline import (
    MANUSCRIPT,
    PipelineFake,
    _default_consistency,
    _default_report,
)

PARAS = {
    1: Paragraph(1, "정원이 손을 내밀었다. 승혜는 그 손을 보았다.", "text"),
    2: Paragraph(2, "승혜는 그 말을 되뇌었다. 정원의 눈빛이 흔들렸다.", "text"),
}


def _clue_map() -> dict:
    return {
        "events": [
            {"para": 1, "kind": "clue", "what": "정원이 손을 내민다", "quote": "정원이 손을 내밀었다"},
            {"para": 2, "kind": "tension", "what": "눈빛이 흔들린다", "quote": "정원의 눈빛이 흔들렸다"},
        ],
        "reader_knows_at_end": ["정원은 무언가를 숨긴다"],
        "reader_does_not_know": ["손에 든 것"],
        "open_questions": [{"question": "정원은 왜 흔들렸나", "para": 2}],
        "resolved_questions": [],
        "turns": [],
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


def _rubric(keys: list[str], **override) -> dict:
    return {
        "summary": "단서가 공정하게 놓여 있다.",
        "items": [_item(key) for key in keys],
        "strengths": [
            {"title": "첫 단서", "body": "손동작이 단서가 된다.", "para": 1, "quote": "정원이 손을 내밀었다"}
        ],
        "top_tasks": [{"title": "의문 회수", "why": "정원의 흔들림을 풀어야 한다."}],
        **override,
    }


class NormalizeTests(unittest.TestCase):
    def test_required_keys_by_genre(self) -> None:
        self.assertNotIn("threat_motive", genre_puzzle.required_keys("mystery", has_kept_promise=False))
        self.assertIn("threat_motive", genre_puzzle.required_keys("thriller", has_kept_promise=False))
        self.assertNotIn("kept_promise", genre_puzzle.required_keys("mystery", has_kept_promise=False))
        self.assertIn("kept_promise", genre_puzzle.required_keys("mystery", has_kept_promise=True))

    def test_clue_map_drops_events_with_invented_quotes(self) -> None:
        raw = _clue_map()
        raw["events"].append({"para": 1, "kind": "clue", "what": "지어낸 일", "quote": "원고에 없는 문장"})
        raw["events"].append({"para": 99, "kind": "clue", "what": "없는 문단", "quote": ""})
        clue_map, dropped = genre_puzzle.normalize_clue_map(raw, PARAS)
        self.assertEqual([event["para"] for event in clue_map["events"]], [1, 2])
        self.assertEqual({row["reason"] for row in dropped}, {"quote_invalid", "para_invalid"})

    def test_rubric_removes_bad_quotes_and_flags_unverified(self) -> None:
        raw = _rubric(["clue_fairness"])
        raw["items"][0]["verdict"] = "problem"
        raw["items"][0]["evidence"] = [{"para": 1, "quote": "원고에 없는 문장"}]
        rubric, dropped = genre_puzzle.normalize_rubric(raw, PARAS, needed=["clue_fairness"])
        item = rubric["items"][0]
        self.assertEqual(item["evidence"], [])
        self.assertTrue(item["unverified"])
        self.assertEqual(item["verdict"], "problem")
        self.assertEqual(dropped[0]["reason"], "quote_invalid")

    def test_intentional_problem_becomes_room(self) -> None:
        raw = _rubric(["deception"])
        raw["items"][0].update(verdict="problem", intentional=True)
        rubric, _ = genre_puzzle.normalize_rubric(raw, PARAS, needed=["deception"])
        self.assertEqual(rubric["items"][0]["verdict"], "room")
        self.assertTrue(rubric["items"][0]["intentional"])

    def test_missing_required_items_are_filled_with_none(self) -> None:
        rubric, _ = genre_puzzle.normalize_rubric(
            _rubric(["clue_fairness"]), PARAS, needed=["clue_fairness", "payoff"]
        )
        by_key = {item["key"]: item for item in rubric["items"]}
        self.assertTrue(by_key["payoff"]["missing"])
        self.assertEqual(by_key["payoff"]["verdict"], "none")
        self.assertEqual(genre_puzzle.missing_keys(rubric), ["payoff"])

    def test_items_not_needed_are_dropped(self) -> None:
        rubric, _ = genre_puzzle.normalize_rubric(
            _rubric(["clue_fairness", "threat_motive"]), PARAS, needed=["clue_fairness"]
        )
        self.assertEqual([item["key"] for item in rubric["items"]], ["clue_fairness"])

    def test_strengths_need_a_real_quote(self) -> None:
        raw = _rubric(["clue_fairness"])
        raw["strengths"].append({"title": "지어낸 강점", "body": "", "para": 1, "quote": "없는 문장"})
        rubric, _ = genre_puzzle.normalize_rubric(raw, PARAS, needed=["clue_fairness"])
        self.assertEqual([s["title"] for s in rubric["strengths"]], ["첫 단서"])


class GenreFake(PipelineFake):
    def __init__(self, *args, rubric_keys=None, fail_map=False, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.rubric_keys = rubric_keys or [
            "clue_fairness", "disclosure_order", "payoff", "twist_plausibility", "tension_curve", "deception",
        ]
        self.fail_map = fail_map

    def generate(self, prompt: str, **kwargs):
        if "「정보 공개 지도」를 만든다" in prompt:
            with self._lock:
                self.prompts.append(prompt)
            if self.fail_map:
                return FakeClaude()._as_result({"parsed": None, "text": "NOT JSON", "usage": dict(self.usage)})
            return FakeClaude()._as_result({"parsed": _clue_map(), "usage": dict(self.usage)})
        if "「장르 진단 루브릭」을 판정한다" in prompt:
            with self._lock:
                self.prompts.append(prompt)
            return FakeClaude()._as_result({"parsed": _rubric(self.rubric_keys), "usage": dict(self.usage)})
        return super().generate(prompt, **kwargs)

    def genre_prompts(self) -> list[str]:
        return [p for p in self.prompts if "정보 공개 지도" in p]


class PuzzleRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        feedback_api.reset_runtime_for_tests()
        set_configured_for_tests(True)
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        deadline = time.time() + 20
        while time.time() < deadline and feedback_api._active_count():
            time.sleep(0.05)
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        set_client_for_tests(None)
        set_configured_for_tests(None)
        feedback_api.reset_runtime_for_tests()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        import http.client

        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=30)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        return response.status, (json.loads(raw) if raw else {})

    def create_scene(self, main_genre: str, **extra) -> tuple[int, int]:
        status, project = self.request(
            "POST",
            "/api/projects",
            {"title": "추리", "purpose": "genre_literature", "main_genre": main_genre,
             "cluster_id": "genre_literature", **extra},
        )
        self.assertEqual(status, 201, project)
        project_id = int(project["id"])
        status, chapter = self.request("POST", f"/api/projects/{project_id}/chapters", {"title": "1장"})
        status, scene = self.request("POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "1화"})
        status, detail = self.request("GET", f"/api/scenes/{scene['id']}")
        status, saved = self.request(
            "PUT",
            f"/api/scenes/{scene['id']}",
            {"title": "1화", "content_md": MANUSCRIPT, "status": "draft", "row_version": detail.get("row_version") or 1},
        )
        self.assertEqual(status, 200, saved)
        return project_id, int(scene["id"])

    def run_and_wait(self, project_id: int, scene_id: int) -> dict:
        status, created = self.request(
            "POST", f"/api/projects/{project_id}/feedback/runs", {"scene_id": scene_id}
        )
        self.assertIn(status, (200, 201), created)
        run_id = int(created["run_id"])
        deadline = time.time() + 30
        run: dict = {}
        while time.time() < deadline:
            status, run = self.request("GET", f"/api/feedback/runs/{run_id}")
            if run.get("status") in {"ok", "partial", "failed"}:
                return run
            time.sleep(0.05)
        self.fail(f"실행이 끝나지 않았습니다: {run}")

    def use_fake(self, **kwargs) -> GenreFake:
        fake = GenreFake(consistency=_default_consistency(), report=_default_report(), **kwargs)
        set_client_for_tests(fake)
        return fake

    def test_mystery_run_adds_genre_report(self) -> None:
        fake = self.use_fake()
        project_id, scene_id = self.create_scene("mystery")
        run = self.run_and_wait(project_id, scene_id)
        self.assertEqual(run["status"], "ok", run)
        genre = run["report"]["genre"]
        self.assertEqual(genre["group"], "puzzle")
        self.assertEqual(
            [item["key"] for item in genre["items"]],
            ["clue_fairness", "disclosure_order", "payoff", "twist_plausibility", "tension_curve", "deception"],
        )
        self.assertTrue(genre["clue_map"]["events"])
        self.assertIn("## 장르 진단", run["report_md"])
        self.assertEqual(len(fake.genre_prompts()), 2)
        self.assertEqual(run["params"].get("genre_group"), "puzzle")
        stages = [row.get("stage") for row in run["params"]["usage"]["stages"]]
        self.assertIn("genre_clue_map", stages)
        self.assertIn("genre_rubric", stages)

    def test_thriller_requires_threat_motive(self) -> None:
        keys = ["clue_fairness", "disclosure_order", "payoff", "twist_plausibility", "tension_curve", "deception"]
        self.use_fake(rubric_keys=keys + ["threat_motive"])
        project_id, scene_id = self.create_scene("thriller")
        run = self.run_and_wait(project_id, scene_id)
        self.assertIn("threat_motive", [item["key"] for item in run["report"]["genre"]["items"]])

    def test_author_promises_reach_the_prompt(self) -> None:
        fake = self.use_fake()
        project_id, scene_id = self.create_scene("mystery")
        status, saved = self.request(
            "POST",
            f"/api/projects/{project_id}/genre-promise",
            {"promise_broken": "서술자가 독자를 속인다", "promise_kept": "단서를 먼저 보여 준다"},
        )
        self.assertEqual(status, 200, saved)
        run = self.run_and_wait(project_id, scene_id)
        rubric_prompt = [p for p in fake.prompts if "「장르 진단 루브릭」을 판정한다" in p][0]
        self.assertIn("서술자가 독자를 속인다", rubric_prompt)
        self.assertIn("단서를 먼저 보여 준다", rubric_prompt)
        self.assertIn("kept_promise", rubric_prompt)
        self.assertTrue(run["report"]["genre"]["promise"]["kept"])

    def test_other_groups_do_not_run_the_genre_stage(self) -> None:
        fake = self.use_fake()
        project_id, scene_id = self.create_scene("sf")
        run = self.run_and_wait(project_id, scene_id)
        self.assertEqual(run["status"], "ok", run)
        self.assertNotIn("genre", run["report"])
        self.assertEqual(fake.genre_prompts(), [])

    def test_genre_failure_does_not_stop_the_normal_report(self) -> None:
        self.use_fake(fail_map=True)
        project_id, scene_id = self.create_scene("mystery")
        run = self.run_and_wait(project_id, scene_id)
        self.assertEqual(run["status"], "ok", run)
        self.assertNotIn("genre", run["report"])
        self.assertTrue(run["params"].get("genre_error"))
        self.assertTrue(run["report"].get("summary"))


if __name__ == "__main__":
    unittest.main()
