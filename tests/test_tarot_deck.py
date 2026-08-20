"""Tarot deck image catalog for Glump character tarot."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import tarot_deck

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

    def test_card_backs_exist_for_both_styles(self) -> None:
        data = json.loads(DECK_JSON.read_text(encoding="utf-8"))
        backs = data["meta"]["cardBack"]
        missing = []
        for style in ("adventurer", "dreamer"):
            rel = str(backs.get(style, "")).lstrip("/")
            if not rel or not (ROOT / rel).is_file():
                missing.append(f"{style}: {rel}")
        self.assertEqual(missing, [])

    def test_selected_cards_keep_user_order(self) -> None:
        cards = tarot_deck.selected_tarot_cards([16, 0, 21])
        self.assertEqual([card["id"] for card in cards], [16, 0, 21])
        with self.assertRaisesRegex(ValueError, "서로 다른"):
            tarot_deck.selected_tarot_cards([16, 16, 21])
        with self.assertRaisesRegex(ValueError, "3장"):
            tarot_deck.selected_tarot_cards([16, 21])

    def test_v2_parser_uses_picked_cards_and_positions(self) -> None:
        picked = tarot_deck.selected_tarot_cards([16, 18, 17])
        raw = json.dumps(
            {
                "positions": {
                    "cause": "기둥이 먼저 금이 갔습니다.",
                    "incident": "달빛이 출구를 슬쩍 바꿨습니다.",
                    "ending": "별이 마지막 문을 표시했습니다.",
                },
                "generalTip": "다음 장면에는 잘못 고른 출구를 써 보세요.",
            },
            ensure_ascii=False,
        )
        result = tarot_deck.parse_character_tarot_v2(raw, picked, "dreamer")
        self.assertEqual([card["id"] for card in result["cards"]], [16, 18, 17])
        self.assertEqual(
            [card["position"] for card in result["cards"]],
            ["cause", "incident", "ending"],
        )
        self.assertTrue(result["cards"][0]["image"].endswith("16-the-tower.webp"))
        self.assertIn("잘못 고른 출구", result["generalTip"])

    def test_tarot_flow_copy_exists_in_all_locales(self) -> None:
        required = {
            "app.누구에게_타로를_볼까요",
            "app.카드_세_장을_차례대로_골라_주세요",
            "app.선택한_카드_뒤집기",
            "app.원인",
            "app.돌발사건",
            "app.결말",
            "app.종합_한마디",
        }
        for locale in ("ko", "en", "es"):
            path = ROOT / "web" / "locales" / f"{locale}.json"
            strings = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(required - strings.keys(), set(), locale)


if __name__ == "__main__":
    unittest.main()
