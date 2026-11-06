"""이어쓰기 「이 장면 맞춤 제안」: 지금 장면을 읽고 다음 전개 후보 4개를 짧게 받는다.

- AI 요청 모드 `continue_directions` (Gemini 1회). 결과는 이어쓰기 방향 칩으로 보인다.
- 고르면 방향 힌트 칸이 채워지고, 이어쓰기 3버전(후킹·전개·전환)이 모두 그 방향을 따른다.
- 아이디어·브레인스토밍 모드와는 따로 둔다(이어쓰기 창 안에서 바로 쓰는 짧은 칩).

설계: docs/design-ai-context-and-style-2026-11.md §4.1.
"""

from __future__ import annotations

import re

SCENE_TAIL_CHARS = 3500
DIRECTION_MAX_CHARS = 60
DIRECTION_COUNT = 4


def scene_tail(scene_text: str, limit: int = SCENE_TAIL_CHARS) -> str:
    text = str(scene_text or "").strip()
    if len(text) <= limit:
        return text
    return "…" + text[-limit:]


def build_prompt(scene_text: str, *, user_hint: str = "") -> str:
    hint = str(user_hint or "").strip()[:300]
    lines = [
        "[이어쓰기 방향 제안]",
        "아래 장면의 끝에서 바로 이어질 수 있는 다음 전개 후보를 정확히 4개 제안하세요.",
        "",
        "[규칙]",
        "- 한 줄에 하나, 줄 앞에 '- '를 붙이세요. 다른 설명·제목·번호는 쓰지 마세요.",
        f"- 각 후보는 {DIRECTION_MAX_CHARS - 20}자 안팎의 짧은 지시문으로 쓰세요. 예: '- 은서가 숨겨 둔 편지를 들킨다'",
        "- 네 후보는 서로 다른 방향이어야 합니다(사건 / 인물의 선택 / 감정 / 장면 전환 등).",
        "- 이미 확정된 설정과 인물 관계를 깨지 마세요. 새 인물·새 설정은 꼭 필요할 때만.",
        "- 장면 본문을 다시 쓰지 말고 방향만 제시하세요.",
    ]
    if hint:
        lines += ["", f"[작가가 적어 둔 방향 메모 — 참고만]\n{hint}"]
    lines += ["", "[현재 장면 끝부분]", scene_tail(scene_text) or "(아직 없음)"]
    return "\n".join(lines)


_BULLET = re.compile(r"^\s*(?:[-*•·]|\d+[.)]|[①-⑩])\s*")


def parse_directions(text: str) -> list[str]:
    """모델 답에서 후보 줄만 뽑는다. 따옴표·번호를 떼고 길이를 자른다."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("[") or line.endswith(":"):
            continue
        line = _BULLET.sub("", line).strip().strip("'\"“”‘’「」").strip()
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        if len(line) < 2:
            continue
        if len(line) > DIRECTION_MAX_CHARS:
            line = line[: DIRECTION_MAX_CHARS - 1].rstrip() + "…"
        key = line.replace(" ", "")
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
        if len(out) >= DIRECTION_COUNT:
            break
    return out
