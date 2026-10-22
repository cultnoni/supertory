"""web/samples/ko.json — 첫 실행 예시 작품 데이터가 앱이 저장할 수 있는 모양인지 확인."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

SAMPLES = Path(__file__).resolve().parents[1] / "web" / "samples" / "ko.json"
ROLES = {"protagonist", "antagonist", "supporting", "minor"}
APPEARANCE = {"primary", "supporting", "cameo", "mentioned"}
STATUSES = {"idea", "outline", "draft", "revision", "complete"}
IDEA_COLORS = {"yellow", "pink", "blue", "green", "orange", "purple"}


class SampleWorksDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pack = json.loads(SAMPLES.read_text(encoding="utf-8"))

    def test_three_genre_samples(self) -> None:
        works = self.pack["works"]
        self.assertEqual(len(works), 3)
        purposes = {w["purpose"] for w in works}
        self.assertEqual(purposes, {"literature", "genre_literature", "web_novel"})

    def test_each_work_has_episodes_and_codex(self) -> None:
        for work in self.pack["works"]:
            scenes = [s for c in work["chapters"] for s in c["scenes"]]
            self.assertGreaterEqual(len(scenes), 2, work["title"])
            self.assertLessEqual(len(scenes), 3, work["title"])
            for scene in scenes:
                self.assertTrue(scene["content_html"].startswith("<p>"), scene["title"])
                self.assertIn(scene["status"], STATUSES, scene["title"])
            settings = work["settings"]
            for key in ("synopsis_md", "logline_md", "intro_md", "intent_md", "keywords", "world"):
                self.assertTrue(settings.get(key), f"{work['title']} 설정집 {key}")
            for key in ("characters", "relations", "items", "dictionary", "ideas", "baits", "scene_members"):
                self.assertTrue(work.get(key), f"{work['title']} {key}")

    def test_statuses_vary_so_every_color_shows(self) -> None:
        statuses = [s["status"] for w in self.pack["works"] for c in w["chapters"] for s in c["scenes"]]
        self.assertEqual(set(statuses), STATUSES)
        for work in self.pack["works"]:
            own = {s["status"] for c in work["chapters"] for s in c["scenes"]}
            self.assertGreaterEqual(len(own), 2, work["title"])

    def test_references_are_consistent(self) -> None:
        for work in self.pack["works"]:
            keys = {c["key"] for c in work["characters"]}
            self.assertEqual(len(keys), len(work["characters"]))
            for c in work["characters"]:
                self.assertIn(c["role"], ROLES)
            for rel in work["relations"]:
                self.assertIn(rel["a"], keys)
                self.assertIn(rel["b"], keys)
                self.assertNotEqual(rel["a"], rel["b"])
            for item in work["items"]:
                if item["owner"]:
                    self.assertIn(item["owner"], keys)
            scene_keys = {
                f"{ci}:{si}"
                for ci, c in enumerate(work["chapters"])
                for si, _ in enumerate(c["scenes"])
            }
            for key, info in work["scene_members"].items():
                self.assertIn(key, scene_keys)
                member_keys = {m[0] for m in info["members"]}
                self.assertTrue(member_keys <= keys)
                self.assertIn(info["pov"], member_keys)
                for _, role in info["members"]:
                    self.assertIn(role, APPEARANCE)
            for idea in work["ideas"]:
                self.assertIn(idea["color"], IDEA_COLORS)
            for bait in work["baits"]:
                for ref in (bait.get("plant_scene"), bait.get("recover_scene")):
                    if ref:
                        self.assertIn(f"{ref[0]}:{ref[1]}", scene_keys)

    def test_literary_sample_has_form_and_style(self) -> None:
        literary = [w for w in self.pack["works"] if w["purpose"] == "literature"][0]
        self.assertEqual(literary["literary_form"], "short")
        self.assertTrue(all(literary["settings"]["style"].values()))


if __name__ == "__main__":
    unittest.main()
