"""web/samples/{ko,en,es}.json — 첫 실행 예시 작품 데이터가 앱이 저장할 수 있는 모양인지 확인."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

SAMPLES_DIR = Path(__file__).resolve().parents[1] / "web" / "samples"
LANGS = ("ko", "en", "es")
ROLES = {"protagonist", "antagonist", "supporting", "minor"}
APPEARANCE = {"primary", "supporting", "cameo", "mentioned"}
STATUSES = {"idea", "outline", "draft", "revision", "complete"}
IDEA_COLORS = {"yellow", "pink", "blue", "green", "orange", "purple"}
WORLD_FIELDS = {
    "reality", "era", "locale", "heritage", "special", "rules", "limits", "extreme_event",
    "extreme_impact", "power", "daily", "class", "factions", "conflict", "geo_terrain",
    "geo_regions", "geo_routes",
}


class SampleWorksDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.packs = {
            lang: json.loads((SAMPLES_DIR / f"{lang}.json").read_text(encoding="utf-8"))
            for lang in LANGS
        }

    def each_work(self):
        for lang, pack in self.packs.items():
            self.assertEqual(pack["language"], lang)
            for work in pack["works"]:
                yield lang, work

    def test_three_genre_samples_per_language(self) -> None:
        for lang, pack in self.packs.items():
            works = pack["works"]
            self.assertEqual(len(works), 3, lang)
            self.assertEqual(
                {w["purpose"] for w in works},
                {"literature", "genre_literature", "web_novel"},
                lang,
            )

    def test_each_work_has_episodes_and_codex(self) -> None:
        for lang, work in self.each_work():
            label = f"{lang}/{work['title']}"
            scenes = [s for c in work["chapters"] for s in c["scenes"]]
            self.assertGreaterEqual(len(scenes), 2, label)
            self.assertLessEqual(len(scenes), 3, label)
            for scene in scenes:
                self.assertTrue(scene["content_html"].startswith("<p>"), scene["title"])
                self.assertIn(scene["status"], STATUSES, scene["title"])
            settings = work["settings"]
            for key in ("synopsis_md", "logline_md", "intro_md", "intent_md", "keywords", "world"):
                self.assertTrue(settings.get(key), f"{label} 설정집 {key}")
            self.assertTrue(set(settings["world"]) <= WORLD_FIELDS, label)
            self.assertGreaterEqual(len(settings["world"]), 10, label)
            for key in ("characters", "relations", "items", "dictionary", "ideas", "baits", "scene_members"):
                self.assertTrue(work.get(key), f"{label} {key}")

    def test_webnovel_episodes_have_real_webnovel_length(self) -> None:
        # 웹소설 한 화는 3~4천 자(한국어). 영어·스페인어는 같은 분량이 되도록 단어 수로 확인한다.
        import html
        import re

        for lang, work in self.each_work():
            if work["purpose"] != "web_novel":
                continue
            for scene in (s for c in work["chapters"] for s in c["scenes"]):
                text = html.unescape(re.sub(r"<[^>]+>", "", scene["content_html"]))
                label = f"{lang}/{scene['title']}"
                if lang == "ko":
                    size = len(text)
                    self.assertGreaterEqual(size, 3000, label)
                    self.assertLessEqual(size, 4500, label)
                else:
                    size = len(re.sub(r"<[^>]+>", " ", html.unescape(scene["content_html"])).split())
                    self.assertGreaterEqual(size, 1000, label)
                    self.assertLessEqual(size, 1800, label)

    def test_statuses_vary_so_every_color_shows(self) -> None:
        for lang, pack in self.packs.items():
            statuses = [s["status"] for w in pack["works"] for c in w["chapters"] for s in c["scenes"]]
            self.assertEqual(set(statuses), STATUSES, lang)
            for work in pack["works"]:
                own = {s["status"] for c in work["chapters"] for s in c["scenes"]}
                self.assertGreaterEqual(len(own), 2, f"{lang}/{work['title']}")

    def test_references_are_consistent(self) -> None:
        for lang, work in self.each_work():
            label = f"{lang}/{work['title']}"
            keys = {c["key"] for c in work["characters"]}
            self.assertEqual(len(keys), len(work["characters"]), label)
            for c in work["characters"]:
                self.assertIn(c["role"], ROLES, label)
            for rel in work["relations"]:
                self.assertIn(rel["a"], keys, label)
                self.assertIn(rel["b"], keys, label)
                self.assertNotEqual(rel["a"], rel["b"], label)
            for item in work["items"]:
                if item["owner"]:
                    self.assertIn(item["owner"], keys, label)
            scene_keys = {
                f"{ci}:{si}"
                for ci, c in enumerate(work["chapters"])
                for si, _ in enumerate(c["scenes"])
            }
            for key, info in work["scene_members"].items():
                self.assertIn(key, scene_keys, label)
                member_keys = {m[0] for m in info["members"]}
                self.assertTrue(member_keys <= keys, label)
                self.assertIn(info["pov"], member_keys, label)
                for _, role in info["members"]:
                    self.assertIn(role, APPEARANCE, label)
            for idea in work["ideas"]:
                self.assertIn(idea["color"], IDEA_COLORS, label)
            for bait in work["baits"]:
                for ref in (bait.get("plant_scene"), bait.get("recover_scene")):
                    if ref:
                        self.assertIn(f"{ref[0]}:{ref[1]}", scene_keys, label)

    def test_literary_sample_has_form_and_style(self) -> None:
        for lang, pack in self.packs.items():
            literary = [w for w in pack["works"] if w["purpose"] == "literature"][0]
            self.assertEqual(literary["literary_form"], "short", lang)
            self.assertTrue(all(literary["settings"]["style"].values()), lang)

    def test_samples_are_written_in_their_language(self) -> None:
        def hangul(text: str) -> int:
            return sum(1 for ch in text if "가" <= ch <= "힣")

        for lang, work in self.each_work():
            blob = json.dumps(work, ensure_ascii=False)
            if lang == "ko":
                self.assertGreater(hangul(blob), 500, work["title"])
            else:
                # 영어·스페인어 예시는 한글이 거의 없어야 한다(작품 속 고유명사 정도만 허용).
                self.assertLess(hangul(blob), 20, f"{lang}/{work['title']}")


if __name__ == "__main__":
    unittest.main()
