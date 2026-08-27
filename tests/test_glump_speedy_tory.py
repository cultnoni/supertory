"""SpeedyTORY is listed in Glump ER and opens a coming-soon screen."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class GlumpSpeedyToryUiTests(unittest.TestCase):
    def test_home_card_and_coming_soon_step(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        mascot = ROOT / "web" / "final_mascots" / "speedytory-clean.webp"
        home_idx = html.find('id="glumpErStepHome"')
        card_idx = html.find('data-glump-tool="speedy_tory"')
        step_idx = html.find('id="glumpErStepSpeedy"')
        self.assertGreater(home_idx, 0)
        self.assertGreater(card_idx, home_idx)
        self.assertGreater(step_idx, card_idx)
        self.assertIn("/final_mascots/speedytory-clean.webp", html)
        self.assertIn("속독 연습하기", html)
        self.assertIn("준비중", html)
        self.assertTrue(mascot.is_file(), mascot)
        self.assertIn('speedy_tory: i18n.t(\'app.SpeedyTORY\')', js)
        self.assertIn("function openGlumpSpeedyToryStep()", js)

    def test_locales_keep_speedy_keys_in_sync(self) -> None:
        keys = ("app.SpeedyTORY", "index.속독_연습하기", "index.준비중")
        for name in ("ko", "en", "es"):
            data = json.loads((ROOT / "web" / "locales" / f"{name}.json").read_text(encoding="utf-8"))
            for key in keys:
                self.assertIn(key, data, f"{name}.json missing {key}")
                self.assertTrue(str(data[key]).strip(), f"{name}.json empty {key}")


class GitsiBlurGuideUiTests(unittest.TestCase):
    def test_popover_explains_blur_icon(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        meet = (ROOT / "web" / "gitsi-meet.html").read_text(encoding="utf-8")
        self.assertIn("gitsi-blur-guide-icon", html)
        self.assertIn("짓시_화면_블러_안내_앞", html)
        self.assertIn("gitsiBlurHint", meet)
        self.assertIn("function showBlurHint()", meet)
        for name in ("ko", "en", "es"):
            data = json.loads((ROOT / "web" / "locales" / f"{name}.json").read_text(encoding="utf-8"))
            self.assertIn("index.짓시_화면_블러_안내_앞", data)
            self.assertIn("index.짓시_화면_블러_안내_뒤", data)


if __name__ == "__main__":
    unittest.main()
