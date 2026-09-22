"""패키지 안 프롬프트·스키마 로더. 동결 실행 파일에서도 같은 경로를 찾는다."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")

CARD_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "target_id": {"type": "string"},
        "kind": {"type": "string"},
        "edit_plan": {"type": "string"},
        "reason": {"type": "string"},
        "suggestion": {},
        "added_facts": {"type": "array", "items": {"type": "string"}},
        "removed_facts": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string"},
    },
    "required": ["reason"],
}


def prompts_dir() -> Path:
    here = Path(__file__).resolve().parent / "prompts"
    if here.is_dir():
        return here
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            frozen = Path(meipass) / "feedback_pipeline" / "prompts"
            if frozen.is_dir():
                return frozen
        exe_dir = Path(sys.executable).resolve().parent
        for candidate in (
            exe_dir / "feedback_pipeline" / "prompts",
            exe_dir / "_internal" / "feedback_pipeline" / "prompts",
        ):
            if candidate.is_dir():
                return candidate
    return here


def _read(name: str) -> str:
    path = prompts_dir() / name
    return path.read_text(encoding="utf-8")


def load_text(name: str) -> str:
    return _read(name)


def load_json(name: str) -> Any:
    return json.loads(_read(name))


def fill_template(template: str, values: dict[str, Any]) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            return match.group(0)
        value = values[key]
        if value is None:
            return ""
        return str(value)

    return PLACEHOLDER_RE.sub(repl, template)


def system_core() -> str:
    return _read("system_core.txt").rstrip()


def report_prompt() -> str:
    return _read("report_prompt.txt")


def report_schema() -> dict[str, Any]:
    return load_json("report_json_schema.json")


def consistency_prompt() -> str:
    return _read("consistency_prompt.txt")


def consistency_schema() -> dict[str, Any]:
    return load_json("consistency_json_schema.json")


def card_prompt() -> str:
    return _read("card_prompt.txt")


def card_reply_prompt() -> str:
    return _read("card_reply_prompt.txt")


def card_schema_text() -> str:
    return _read("card_schema_v4.txt")


def type_guidance() -> dict[str, Any]:
    data = load_json("type_guidance.json")
    return data if isinstance(data, dict) else {}


def lens_text() -> dict[str, str]:
    raw = load_json("lens_text.json")
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def type_guidance_text(guidance: dict[str, Any], style_type: str) -> str:
    entry = guidance.get(style_type) if isinstance(guidance, dict) else None
    if not isinstance(entry, dict):
        entry = guidance.get("other") if isinstance(guidance, dict) else None
    if not isinstance(entry, dict):
        return "(해당 없음)"
    return str(entry.get("guidance") or "(해당 없음)")
