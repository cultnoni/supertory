"""흥행 공식 분석 — range budgets, prompts, parse helpers (no project binding)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import document_import

MAX_TOTAL_EPISODES = 50
RECOMMENDED_TOTAL_EPISODES = 30
DEFAULT_WINDOW = 10
MAX_TOTAL_CHARS = 300_000
RECOMMENDED_TOTAL_CHARS = 180_000

SECTION_KEYS = ("front", "middle", "ending")
SECTION_LABELS = {
    "front": "앞부분",
    "middle": "중간부분",
    "ending": "결말부분",
}


@dataclass
class EpisodeUnit:
    title: str
    text: str
    index: int = 0  # 1-based within uploaded pack when known

    @property
    def length(self) -> int:
        return len(self.text or "")


@dataclass
class UploadedSection:
    key: str  # front | middle | ending
    start_ep: int
    end_ep: int
    episodes: list[EpisodeUnit] = field(default_factory=list)

    @property
    def label(self) -> str:
        return SECTION_LABELS.get(self.key, self.key)

    @property
    def episode_count(self) -> int:
        return max(0, int(self.end_ep) - int(self.start_ep) + 1) if self.end_ep >= self.start_ep else 0

    @property
    def uploaded_count(self) -> int:
        return len(self.episodes)

    @property
    def char_count(self) -> int:
        return sum(ep.length for ep in self.episodes)


def recommend_ranges(total_chapters: int, window: int = DEFAULT_WINDOW) -> dict[str, dict[str, int]]:
    """Auto-fill start/end episode numbers for front/middle/ending."""
    t = max(1, int(total_chapters or 1))
    w = max(1, min(int(window or DEFAULT_WINDOW), t))

    front_start = 1
    front_end = min(w, t)

    # Middle: centered window of w episodes
    mid_center = (t + 1) // 2
    mid_start = max(1, mid_center - (w // 2))
    mid_end = min(t, mid_start + w - 1)
    mid_start = max(1, mid_end - w + 1)

    ending_end = t
    ending_start = max(1, t - w + 1)

    return {
        "front": {"start": front_start, "end": front_end, "count": front_end - front_start + 1},
        "middle": {"start": mid_start, "end": mid_end, "count": mid_end - mid_start + 1},
        "ending": {"start": ending_start, "end": ending_end, "count": ending_end - ending_start + 1},
    }


def episode_span_count(start: int, end: int) -> int:
    try:
        s = int(start)
        e = int(end)
    except (TypeError, ValueError):
        return 0
    if e < s:
        return 0
    return e - s + 1


def sum_selected_episode_budget(ranges: dict[str, dict[str, int]], selected: list[str]) -> int:
    total = 0
    for key in selected:
        r = ranges.get(key) or {}
        total += episode_span_count(r.get("start", 0), r.get("end", 0))
    return total


def check_episode_budget(total_selected: int) -> dict[str, Any]:
    n = int(total_selected or 0)
    if n > MAX_TOTAL_EPISODES:
        return {
            "status": "blocked",
            "total": n,
            "max": MAX_TOTAL_EPISODES,
            "recommended": RECOMMENDED_TOTAL_EPISODES,
            "message": (
                f"선택한 구간 합계가 {n}화로 최대 {MAX_TOTAL_EPISODES}화를 넘었어요. "
                "범위를 줄여 주세요."
            ),
        }
    if n > RECOMMENDED_TOTAL_EPISODES:
        return {
            "status": "warning",
            "total": n,
            "max": MAX_TOTAL_EPISODES,
            "recommended": RECOMMENDED_TOTAL_EPISODES,
            "message": (
                f"추천량({RECOMMENDED_TOTAL_EPISODES}화)보다 많아요, 비용이 늘어날 수 있어요."
            ),
        }
    return {
        "status": "ok",
        "total": n,
        "max": MAX_TOTAL_EPISODES,
        "recommended": RECOMMENDED_TOTAL_EPISODES,
        "message": "",
    }


def check_character_budget(uploaded_sections: list[UploadedSection] | list[dict]) -> dict[str, Any]:
    """Mirror of the client budget helper — shared for API validation/tests."""
    chapters: list[str] = []
    for section in uploaded_sections:
        if isinstance(section, UploadedSection):
            for ep in section.episodes:
                chapters.append(ep.text or "")
        else:
            for ep in section.get("episodes") or section.get("chapters") or []:
                if isinstance(ep, str):
                    chapters.append(ep)
                elif isinstance(ep, dict):
                    chapters.append(str(ep.get("text") or ep.get("content") or ""))
                else:
                    chapters.append(getattr(ep, "text", "") or "")

    total_chars = sum(len(c) for c in chapters)
    count = len(chapters)
    avg = (total_chars / count) if count else 0

    if total_chars > MAX_TOTAL_CHARS:
        excess = total_chars - MAX_TOTAL_CHARS
        suggested = int(max(1, (excess / avg) + 0.999)) if avg > 0 else count
        return {
            "status": "blocked",
            "totalChars": total_chars,
            "message": (
                f"현재 업로드된 총 글자수가 {total_chars:,}자로, "
                f"최대 허용 글자수({MAX_TOTAL_CHARS:,}자)를 "
                f"{excess:,}자 초과했어요. "
                f"회차를 약 {suggested}개 정도 줄여주세요."
            ),
            "suggestedRemoval": suggested,
        }

    if total_chars > RECOMMENDED_TOTAL_CHARS:
        return {
            "status": "warning",
            "totalChars": total_chars,
            "message": (
                f"현재 총 {total_chars:,}자예요. "
                f"추천 분량({RECOMMENDED_TOTAL_CHARS:,}자)보다 많아서 "
                "처리 시간이 길어질 수 있어요. 그래도 진행은 가능해요."
            ),
            "suggestedRemoval": 0,
        }

    return {"status": "ok", "totalChars": total_chars, "message": "", "suggestedRemoval": 0}


def parse_document_to_episodes(
    filename: str,
    data: bytes,
    *,
    split_mode: str = "headings",
) -> list[dict[str, Any]]:
    """Reuse document_import extract + section split; return flat episode list."""
    extracted = document_import.extract_document(filename, data)
    mode = (split_mode or "headings").strip().lower()
    # Prefer simple heading/blank splits for analysis packs (no full TOC hierarchy).
    if mode not in {"headings", "blank_lines", "none"}:
        mode = "headings"
    plan = document_import.build_import_plan(
        extracted.text,
        mode,
        extracted.title or "구간",
    )
    episodes: list[dict[str, Any]] = []
    idx = 0
    chapters = getattr(plan, "chapters", None) or ()
    for ch in chapters:
        scenes = getattr(ch, "scenes", None) or ()
        if scenes:
            for sc in scenes:
                idx += 1
                text = str(getattr(sc, "content", "") or "")
                title = str(getattr(sc, "title", "") or getattr(ch, "title", "") or f"{idx}화")
                episodes.append({
                    "title": title,
                    "text": text,
                    "length": len(text),
                    "index": idx,
                })
        else:
            idx += 1
            text = str(getattr(ch, "content", "") or "")
            title = str(getattr(ch, "title", "") or f"{idx}화")
            episodes.append({
                "title": title,
                "text": text,
                "length": len(text),
                "index": idx,
            })

    if not episodes:
        text = extracted.text or ""
        episodes = [{
            "title": extracted.title or "1화",
            "text": text,
            "length": len(text),
            "index": 1,
        }]
    return episodes


def build_structural_observation_prompt(scene_content: str) -> str:
    return f"""[현재 작업]
아래 회차에서 관찰되는 글쓰기 기법·구조적 특징을 짧게 기록하세요.
줄거리 내용이 아니라 "어떻게 쓰였는지"에 집중합니다.

[저작권 관련 원칙]
원문 문장을 그대로 인용하거나 재현하지 않는다. 패턴에 대한 관찰과
설명만 작성하고, 구체적 대사나 문장을 그대로 옮기지 않는다.

[관찰 항목]
1. 회차 끝맺음 방식: 훅으로 끝나는지, 잔잔하게 마무리되는지, 어떤 종류의
   긴장(반전/위기/궁금증/여운)으로 끝나는지
2. 전개 속도: 이 회차 안에서 사건이 빠르게 전개되는지, 묘사·심리에
   시간을 들이는지
3. 대사와 지문의 비중: 대사 위주인지 묘사 위주인지 체감 수준으로
4. 문장 스타일: 문장 길이, 리듬, 눈에 띄는 문체적 특징

[출력 형식 - JSON만 출력]
{{
  "ending_hook": "...",
  "pacing": "...",
  "dialogue_narration_balance": "...",
  "style_notes": "..."
}}

[본문]
{scene_content}

[JSON 출력]"""


def build_success_pattern_merge_prompt(
    quantitative_stats: dict[str, Any],
    chapter_notes_list: list[dict[str, Any]],
) -> str:
    return f"""[현재 작업]
아래는 한 작품 일부 구간의 회차별 구조 관찰 기록과 정량 통계입니다.
이를 종합해 이 작품이 성공했던 글쓰기 패턴을 하나의 프로파일로 정리하세요.

[저작권 관련 원칙]
원문을 그대로 인용하거나 재현하지 않는다. 패턴에 대한 설명만 작성한다.

[판단 기준]
1. 회차마다 반복적으로 나타나는 패턴을 우선적으로 뽑는다. 한두 번만
   나타난 특이 케이스는 패턴으로 일반화하지 않는다.
2. 왜 이 패턴이 효과적이었을지 간단히 설명을 덧붙인다.
3. 있는 그대로 관찰하고, 근거 없이 미화하거나 과장하지 않는다.
4. 분석 대상이 작품의 일부 구간(앞부분/중간부분/결말부분)이라는 점을
   감안해, 어느 구간에서 나온 관찰인지 구분해서 서술한다.

[출력 형식 - JSON만 출력]
{{
  "hook_style": "이 작품의 회차 끝맺음 패턴과 그 효과",
  "pacing_pattern": "전개 속도의 전형적 패턴",
  "dialogue_narration_balance": "대사/지문 비중의 전형적 패턴",
  "style_signature": "이 작가 특유의 문체적 특징",
  "summary": "이 작품이 독자를 사로잡았던 핵심 요인 종합 (2~3문장)"
}}

[정량 통계]
{json.dumps(quantitative_stats, ensure_ascii=False)}

[회차별 관찰 기록]
{json.dumps(chapter_notes_list, ensure_ascii=False)}

[JSON 출력]"""


def compute_quantitative_stats(sections: list[UploadedSection] | list[dict]) -> dict[str, Any]:
    per_section: dict[str, Any] = {}
    all_lens: list[int] = []
    for section in sections:
        if isinstance(section, UploadedSection):
            key = section.key
            lens = [ep.length for ep in section.episodes]
            label = section.label
            start, end = section.start_ep, section.end_ep
        else:
            key = str(section.get("key") or "")
            label = SECTION_LABELS.get(key, key)
            start = int(section.get("start_ep") or section.get("start") or 0)
            end = int(section.get("end_ep") or section.get("end") or 0)
            eps = section.get("episodes") or section.get("chapters") or []
            lens = []
            for ep in eps:
                if isinstance(ep, str):
                    lens.append(len(ep))
                elif isinstance(ep, dict):
                    lens.append(len(str(ep.get("text") or ep.get("content") or "")))
                else:
                    lens.append(len(getattr(ep, "text", "") or ""))
        all_lens.extend(lens)
        per_section[key] = {
            "label": label,
            "start_ep": start,
            "end_ep": end,
            "episode_count": len(lens),
            "char_count": sum(lens),
            "avg_chars": round(sum(lens) / len(lens), 1) if lens else 0,
            "min_chars": min(lens) if lens else 0,
            "max_chars": max(lens) if lens else 0,
        }
    total = sum(all_lens)
    return {
        "total_episodes": len(all_lens),
        "total_chars": total,
        "avg_chars_per_episode": round(total / len(all_lens), 1) if all_lens else 0,
        "min_chars": min(all_lens) if all_lens else 0,
        "max_chars": max(all_lens) if all_lens else 0,
        "sections": per_section,
    }


def extract_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("AI 응답이 비어 있어요.")
    # Strip markdown fences
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.I)
    if fence:
        raw = fence.group(1).strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        data = json.loads(raw[start : end + 1])
        if isinstance(data, dict):
            return data
    raise ValueError("AI 응답에서 JSON을 읽지 못했어요.")


def mock_observation_note(scene_content: str) -> dict[str, str]:
    n = len(scene_content or "")
    dialogue_heavy = scene_content.count("\"") + scene_content.count("“") > 8
    return {
        "ending_hook": "회차 말미에 궁금증을 남기는 유형으로 관찰됨" if n > 50 else "짧은 회차 마무리",
        "pacing": "사건 전개와 묘사가 고르게 섞인 편" if n > 200 else "짧은 호흡",
        "dialogue_narration_balance": "대사 비중이 다소 높음" if dialogue_heavy else "지문·묘사 비중 상대적 높음",
        "style_notes": "문장 길이 중간, 가독성 위주",
    }


def mock_merge_profile(stats: dict[str, Any], notes: list[dict[str, Any]]) -> dict[str, Any]:
    sections = ", ".join(
        SECTION_LABELS.get(str(n.get("section_key") or ""), str(n.get("section_key") or ""))
        for n in notes[:3]
    ) or "선택 구간"
    return {
        "hook_style": f"{sections}에서 반복되는 회차 끝 훅·여운 패턴",
        "pacing_pattern": "구간별 전개 속도가 비교적 일정한 편",
        "dialogue_narration_balance": "대사와 지문이 상황에 따라 교차",
        "style_signature": "가독성 중심의 문장 리듬",
        "summary": (
            f"분석 구간({stats.get('total_episodes', 0)}화, "
            f"{stats.get('total_chars', 0):,}자) 기준으로 보면 "
            "회차 단위 긴장 유지와 읽기 쉬운 문체가 핵심 요인으로 보입니다."
        ),
        # Shape used by 흥행 공식 참고 (buildTaskPromptWithSuccessProfile)
        "reader_popularity_factors": [
            "회차 말미 궁금증 훅",
            "읽기 쉬운 문장 리듬",
            "캐릭터 감정선 유지",
        ],
        "editor_popularity_factors": [
            "전개 속도의 균형",
            "대사·지문 비중 조절",
            "설정 일관성",
        ],
        "must_follow_factors": [
            "캐릭터 말투·성격 일관성",
            "회차 단위 긴장 유지",
        ],
    }


def _profile_factor_lists(success_profile: dict[str, Any]) -> dict[str, list[str]]:
    p = success_profile or {}
    if isinstance(p.get("profile"), dict) and not p.get("hook_style"):
        p = {**p, **(p.get("profile") or {})}

    def _list(key: str) -> list[str]:
        val = p.get(key)
        if isinstance(val, list):
            return [str(x).strip() for x in val if str(x).strip()]
        if isinstance(val, str) and val.strip():
            return [val.strip()]
        return []

    reader = _list("reader_popularity_factors")
    editor = _list("editor_popularity_factors")
    must = _list("must_follow_factors")
    if not reader and p.get("summary"):
        reader = [str(p.get("summary")).strip()]
    if not reader and p.get("hook_style"):
        reader = [str(p.get("hook_style")).strip()]
    if not editor and p.get("style_signature"):
        editor = [str(p.get("style_signature")).strip()]
    return {
        "reader": reader,
        "editor": editor,
        "must": must,
        "hook_style": str(p.get("hook_style") or ""),
        "pacing_pattern": str(p.get("pacing_pattern") or ""),
        "dialogue_narration_balance": str(p.get("dialogue_narration_balance") or ""),
        "style_signature": str(p.get("style_signature") or ""),
    }


def build_success_analyst_chat_scope(success_profile: dict[str, Any] | None) -> str:
    """Extra system scope for 흥행요인 분석가 chat (on top of Core Identity + persona)."""
    factors = _profile_factor_lists(success_profile or {})
    reader = ", ".join(factors["reader"]) if factors["reader"] else "(기록 없음)"
    editor = ", ".join(factors["editor"]) if factors["editor"] else "(기록 없음)"
    must = ", ".join(factors["must"]) if factors["must"] else "(지정 없음)"
    return (
        "[Tory Core Identity]를 유지한 채, 지금은 흥행요인 분석가 역할에\n"
        "집중하세요. 작가의 흥행작에서 관찰된 성공 요인을 바탕으로, 지금\n"
        "작가가 쓰고 있는 원고와 비교하며 대화하세요.\n\n"
        "[이 작가의 흥행작에서 관찰된 성공 요인]\n"
        f"독자 관점: {reader}\n"
        f"편집자·비평가 관점: {editor}\n"
        f"특히 놓치지 않아야 할 요인: {must}\n"
        f"참고 패턴: 훅 스타일({factors['hook_style']}), "
        f"전개 속도({factors['pacing_pattern']}),\n"
        f"대사/지문 비중({factors['dialogue_narration_balance']}), "
        f"문체({factors['style_signature']})\n\n"
        "작가가 구체적인 질문(예: \"재미요소가 뭐가 부족해?\", \"다음화 훅으로\n"
        "뭘 추가하면 좋을까?\")을 하면, 위 성공 요인을 근거로 구체적이고\n"
        "실질적으로 답하세요.\n"
    )


def build_task_prompt_with_success_profile(
    task_prompt: str,
    success_profile: dict[str, Any] | None,
) -> str:
    """Pure text wrap — optional 흥행 공식 참고 layer (no Core Identity)."""
    if not success_profile:
        return task_prompt
    factors = _profile_factor_lists(success_profile)
    reader = factors["reader"]
    editor = factors["editor"]
    must = factors["must"]

    must_line = (
        f"특히 놓치지 않아야 할 요인: {', '.join(must)}\n" if must else ""
    )
    profile_block = (
        "[흥행 공식 참고 - 작가가 선택적으로 추가한 참고자료]\n"
        "아래는 작가의 이전 흥행작에서 관찰된 성공 요인입니다. 이번 작업의\n"
        "본래 목적을 벗어나지 않는 범위에서 참고하세요. 무리하게 모든 요인을\n"
        "반영하려 하지 않습니다.\n\n"
        f"독자 관점: {', '.join(reader) if reader else '(기록 없음)'}\n"
        f"편집자·비평가 관점: {', '.join(editor) if editor else '(기록 없음)'}\n"
        f"{must_line}"
        f"참고 패턴: 훅 스타일({factors['hook_style']}), "
        f"전개 속도({factors['pacing_pattern']}), "
        f"대사/지문 비중({factors['dialogue_narration_balance']}), "
        f"문체({factors['style_signature']})\n"
    )
    return f"{profile_block}\n{task_prompt}"
