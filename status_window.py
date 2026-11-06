"""게임물 상태창: 작품별 프리셋 저장 형식과 내보내기 변환.

원고 안의 상태창은 ``data-status-window`` 표식이 달린 블록이다(줄마다 ``<div>``). 주석 단락
(``data-author-note``)과 달리 **독자가 보는 글**이라 내보내기·뷰어·글자 수에 그대로 들어간다.

- 프리셋은 ``project.status_window_presets_json``(마이그레이션 109)에 배열로 저장한다.
- 블록은 만든 때의 모양을 들고 있다. 프리셋을 고쳐도 이미 쓴 회차는 바뀌지 않는다.
- 블록의 ``data-sw-export="text_safe"``이면 내보낼 때 틀 글자(━ ┌ │ …)만 있는 줄을 빼고,
  줄 앞뒤의 ``│``를 떼어 어느 글꼴·플랫폼에서도 깨지지 않는 줄글로 바꾼다.

모양을 만드는 규칙(줄 만들기·정리)은 편집기 쪽 ``web/status_window_model.js``가 맡는다.
설계: docs/design-ai-context-and-style-2026-11.md §13.
"""

from __future__ import annotations

import html as html_lib
import json
import re

import author_note_blocks

FRAMES = ("divider", "bracket", "box", "none")
ALIGNS = ("left", "center")
LABEL_STYLES = ("colon", "spaced", "bracket")
VALUE_TYPES = ("text", "number", "ratio")
BACKGROUNDS = ("none", "soft")
FONTS = ("body", "mono")
EXPORT_MODES = ("keep", "text_safe")

MAX_PRESETS = 30
MAX_ITEMS = 40
MAX_NAME = 40
MAX_TITLE = 40
MAX_GROUP = 20
MAX_VALUE = 80

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_OPEN_RE = re.compile(r"(?is)<([a-z][a-z0-9]*)\b([^>]*\bdata-status-window\b[^>]*)>")
_TEXT_SAFE_RE = re.compile(r"""(?is)\bdata-sw-export\s*=\s*["']?text_safe\b""")
# 틀 글자만 있는 줄(구분선·상자 윗줄/아랫줄). 글자나 숫자가 하나라도 있으면 틀이 아니다.
_FRAME_ONLY_RE = re.compile(r"^[\s━─═┄┅┈┉╌╍┌┐└┘├┤┬┴┼┏┓┗┛┣┫│┃║╔╗╚╝╠╣\-=_~·•*]*$")
_SIDE_CHARS = "│┃║"


def _text(value: object, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()
    return text[:limit]


def _choice(value: object, options: tuple[str, ...]) -> str:
    text = str(value or "").strip()
    return text if text in options else options[0]


def normalize_item(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    name = _text(raw.get("name"), MAX_NAME)
    if not name:
        return None
    return {
        "name": name,
        "group": _text(raw.get("group"), MAX_GROUP),
        "default": _text(raw.get("default"), MAX_VALUE),
        "type": _choice(raw.get("type"), VALUE_TYPES),
    }


def normalize_preset(raw: object, *, index: int = 0) -> dict | None:
    if not isinstance(raw, dict):
        return None
    preset_id = str(raw.get("id") or "").strip()
    if not _ID_RE.match(preset_id):
        preset_id = f"p{index + 1}"
    name = _text(raw.get("name"), MAX_NAME) or _text(raw.get("title"), MAX_NAME) or f"#{index + 1}"
    items: list[dict] = []
    for item in raw.get("items") or []:
        normalized = normalize_item(item)
        if normalized:
            items.append(normalized)
        if len(items) >= MAX_ITEMS:
            break
    display = raw.get("display") if isinstance(raw.get("display"), dict) else {}
    return {
        "id": preset_id,
        "name": name,
        "title": _text(raw.get("title"), MAX_TITLE),
        "frame": _choice(raw.get("frame"), FRAMES),
        "align": _choice(raw.get("align"), ALIGNS),
        "label_style": _choice(raw.get("label_style"), LABEL_STYLES),
        "items": items,
        "display": {
            "background": _choice(display.get("background"), BACKGROUNDS),
            "font": _choice(display.get("font"), FONTS),
        },
        "export_mode": _choice(raw.get("export_mode"), EXPORT_MODES),
    }


def normalize_presets(raw: object) -> list[dict]:
    """JSON 문자열이나 목록을 받아 저장 형식으로 맞춘다. 깨진 값은 빈 목록. id가 겹치면 뒤쪽을 고친다."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except (TypeError, ValueError):
            return []
    if not isinstance(raw, list):
        return []
    presets: list[dict] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        preset = normalize_preset(item, index=index)
        if preset is None:
            continue
        base = preset["id"]
        suffix = 2
        while preset["id"] in seen:
            preset["id"] = f"{base[:36]}_{suffix}"
            suffix += 1
        seen.add(preset["id"])
        presets.append(preset)
        if len(presets) >= MAX_PRESETS:
            break
    return presets


# ---------------------------------------------------------------- 내보내기 변환


def _block_lines(inner_html: str) -> list[str]:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", inner_html or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|blockquote)\s*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = html_lib.unescape(text).replace("\xa0", " ").replace("\u200b", "")
    return text.split("\n")


def text_safe_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line or _FRAME_ONLY_RE.match(line):
            continue
        if line[0] in _SIDE_CHARS:
            line = line[1:].strip()
        if line and line[-1] in _SIDE_CHARS:
            line = line[:-1].strip()
        if line:
            out.append(line)
    return out


def to_text_safe_html(content: str) -> str:
    """``data-sw-export="text_safe"`` 상태창 블록만 줄글로 바꾼다. 다른 글은 손대지 않는다."""
    text = content or ""
    if "data-status-window" not in text:
        return text
    out: list[str] = []
    pos = 0
    for match in _OPEN_RE.finditer(text):
        if match.start() < pos:
            continue  # 바깥 블록 안에 들어 있던 것(이미 처리함)
        close_at = author_note_blocks._matching_close_end(text, match.end(), match.group(1))
        if close_at is None:
            continue
        out.append(text[pos:match.start()])
        block = text[match.start():close_at]
        if _TEXT_SAFE_RE.search(match.group(2)):
            inner = text[match.end():close_at]
            inner = re.sub(rf"(?is)</{re.escape(match.group(1))}\s*>\s*$", "", inner)
            lines = text_safe_lines(_block_lines(inner))
            body = "".join(f"<div>{html_lib.escape(line)}</div>" for line in lines)
            block = f"<div data-status-window=\"text\">{body}</div>"
        out.append(block)
        pos = close_at
    out.append(text[pos:])
    return "".join(out)
