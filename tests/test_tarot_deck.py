"""Tarot deck image catalog for Glump character tarot."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DECK_JSON = ROOT / "web" / "tori_tarot_data_v3.json"


class ToryTarotDataV3Tests(unittest.TestCase):
    def test_front_images_exist_for_both_styles(self) -> None:
        data = json.loads(DECK_JSON.read_text(encoding="utf-8"))
        cards = data["cards"]
        self.assertEqual([card["id"] for card in cards], list(range(22)))
        missing = []
        for card in cards:
            for style in ("adventurer", "dreamer"):
                rel = str(card["images"][style]).lstrip("/")
                path = ROOT / rel
                if not path.is_file():
                    missing.append(f"{card['id']} {card['name']} {style}: {rel}")
        self.assertEqual(missing, [])

    def test_card_backs_are_blank_until_assets_exist(self) -> None:
        data = json.loads(DECK_JSON.read_text(encoding="utf-8"))
        backs = data["meta"]["cardBack"]
        self.assertEqual(backs.get("adventurer"), "")
        self.assertEqual(backs.get("dreamer"), "")


if __name__ == "__main__":
    unittest.main()
