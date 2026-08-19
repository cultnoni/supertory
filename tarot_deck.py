"""Major-arcana catalog and v2 parsers for Glump character tarot."""

from __future__ import annotations

import json
import random
import re
from typing import Any

TAROT_STYLES = {
    "adventurer": {"id": "adventurer", "zip": "torytarot_style_1", "ko": "모험가 토리"},
    "dreamer": {"id": "dreamer", "zip": "torytarot_style_2", "ko": "몽상가 토리"},
}
DEFAULT_TAROT_STYLE = "adventurer"

TAROT_POSITIONS = ("cause", "incident", "ending")
TAROT_POSITION_LABELS = {
    "cause": "원인",
    "incident": "돌발사건",
    "ending": "결말",
}

MAJOR_ARCANA: list[dict[str, Any]] = [
    {"id": 0, "slug": "the-fool", "en": "The Fool", "ko": "바보"},
    {"id": 1, "slug": "the-magician", "en": "The Magician", "ko": "마법사"},
    {"id": 2, "slug": "the-high-priestess", "en": "The High Priestess", "ko": "여사제"},
    {"id": 3, "slug": "the-empress", "en": "The Empress", "ko": "여황제"},
    {"id": 4, "slug": "the-emperor", "en": "The Emperor", "ko": "황제"},
    {"id": 5, "slug": "the-hierophant", "en": "The Hierophant", "ko": "교황"},
    {"id": 6, "slug": "the-lovers", "en": "The Lovers", "ko": "연인"},
    {"id": 7, "slug": "the-chariot", "en": "The Chariot", "ko": "전차"},
    {"id": 8, "slug": "strength", "en": "Strength", "ko": "힘"},
    {"id": 9, "slug": "the-hermit", "en": "The Hermit", "ko": "은둔자"},
    {"id": 10, "slug": "wheel-of-fortune", "en": "Wheel of Fortune", "ko": "운명의 수레바퀴"},
    {"id": 11, "slug": "justice", "en": "Justice", "ko": "정의"},
    {"id": 12, "slug": "the-hanged-man", "en": "The Hanged Man", "ko": "매달린 사람"},
    {"id": 13, "slug": "death", "en": "Death", "ko": "죽음"},
    {"id": 14, "slug": "temperance", "en": "Temperance", "ko": "절제"},
    {"id": 15, "slug": "the-devil", "en": "The Devil", "ko": "악마"},
    {"id": 16, "slug": "the-tower", "en": "The Tower", "ko": "탑"},
    {"id": 17, "slug": "the-star", "en": "The Star", "ko": "별"},
    {"id": 18, "slug": "the-moon", "en": "The Moon", "ko": "달"},
    {"id": 19, "slug": "the-sun", "en": "The Sun", "ko": "태양"},
    {"id": 20, "slug": "judgement", "en": "Judgement", "ko": "심판"},
    {"id": 21, "slug": "the-world", "en": "The World", "ko": "세계"},
]

_ARCANA_BY_ID = {int(item["id"]): item for item in MAJOR_ARCANA}


def normalize_tarot_style(value: object) -> str:
    key = str(value or "").strip().lower()
    if key in TAROT_STYLES:
        return key
    if key in ("1", "style_1", "style1", "adventurer_tory"):
        return "adventurer"
    if key in ("2", "style_2", "style2", "dreamer_tory"):
        return "dreamer"
    return DEFAULT_TAROT_STYLE


def tarot_image_path(card_id: int, style: object = DEFAULT_TAROT_STYLE) -> str:
    card = _ARCANA_BY_ID.get(int(card_id))
    if not card:
        return ""
    style_key = normalize_tarot_style(style)
    return f"/assets/tarot/{style_key}/{int(card_id):02d}-{card['slug']}.jpg"


def pick_tarot_spread(count: int = 3, rng: random.Random | None = None) -> list[dict[str, Any]]:
    picker = rng or random
    chosen = picker.sample(MAJOR_ARCANA, k=min(count, len(MAJOR_ARCANA)))
    return [dict(item) for item in chosen]


def serialize_spread_card(
    card: dict[str, Any],
    position: str,
    text: str,
    style: object = DEFAULT_TAROT_STYLE,
) -> dict[str, Any]:
    card_id = int(card["id"])
    return {
        "id": card_id,
        "slug": card["slug"],
        "name": card["ko"],
        "name_en": card["en"],
        "position": position,
        "position_label": TAROT_POSITION_LABELS.get(position, position),
        "text": str(text or "").strip(),
        "image": tarot_image_path(card_id, style),
    }


def _load_json_object(raw: object) -> dict:
    cleaned = str(raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}|\[.*\]", cleaned, re.S)
        if not match:
            raise ValueError("json")
        data = json.loads(match.group(0))
    if isinstance(data, list):
        return {"cards": data}
    if not isinstance(data, dict):
        raise ValueError("json")
    return data


def _position_texts_from_payload(data: dict) -> dict[str, str]:
    positions = data.get("positions") if isinstance(data.get("positions"), dict) else {}
    texts: dict[str, str] = {}
    aliases = {
        "cause": ("cause", "원인", "origin"),
        "incident": ("incident", "돌발사건", "event", "twist"),
        "ending": ("ending", "결말", "finale", "resolution"),
    }
    for key, names in aliases.items():
        value = ""
        for name in names:
            raw = positions.get(name)
            if raw:
                value = str(raw).strip()
                break
            if data.get(name):
                value = str(data.get(name)).strip()
                break
        texts[key] = value
    if all(texts.values()):
        return texts
    cards = data.get("cards") or data.get("items") or data.get("list")
    if isinstance(cards, list):
        for index, position in enumerate(TAROT_POSITIONS):
            if texts.get(position):
                continue
            if index >= len(cards) or not isinstance(cards[index], dict):
                continue
            item = cards[index]
            texts[position] = str(
                item.get("text")
                or item.get("meaning")
                or item.get("reading")
                or ""
            ).strip()
    return texts


def parse_character_tarot_v2(
    raw: object,
    picked: list[dict[str, Any]],
    style: object = DEFAULT_TAROT_STYLE,
) -> dict[str, Any]:
    if len(picked) < 3:
        raise ValueError("json")
    data = _load_json_object(raw)
    texts = _position_texts_from_payload(data)
    if any(not texts.get(key) for key in TAROT_POSITIONS):
        raise ValueError("json")
    general = str(
        data.get("generalTip")
        or data.get("general_tip")
        or data.get("tip")
        or ""
    ).strip()
    if not general:
        general = texts["ending"]
    cards = [
        serialize_spread_card(picked[index], position, texts[position], style)
        for index, position in enumerate(TAROT_POSITIONS)
    ]
    return {
        "cards": cards,
        "positions": {
            "cause": texts["cause"],
            "incident": texts["incident"],
            "ending": texts["ending"],
        },
        "generalTip": general,
    }


def combined_tarot_note_body(payload: dict[str, Any]) -> str:
    cards = list(payload.get("cards") or [])
    representative = cards[-1] if cards else {}
    number = representative.get("id", "")
    name = representative.get("name") or ""
    positions = payload.get("positions") or {}
    cause = str(positions.get("cause") or "")
    incident = str(positions.get("incident") or "")
    ending = str(positions.get("ending") or "")
    return f"🃏 [{number} {name}]: {cause} / {incident} / {ending}".strip()


def tarot_comment_line(payload: dict[str, Any]) -> str:
    cards = list(payload.get("cards") or [])
    representative = cards[-1] if cards else {}
    name = str(representative.get("name") or "타로")
    ending = str((payload.get("positions") or {}).get("ending") or "")
    tip = ending or str(payload.get("generalTip") or "")
    tip = re.sub(r"\s+", " ", tip).strip()
    if len(tip) > 160:
        tip = tip[:159] + "…"
    return f"/* 🃏 [타로 팁 - {name} 카드]: {tip} */"
