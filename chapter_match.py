"""Match uploaded manuscript text (교정고) to an existing project episode/scene.

Used when a proofreading export (HWPX/DOCX/TXT…) should overwrite the closest
existing 회차 rather than create a new scene.

Strategy:
1. Local scoring (title, episode number, prefix, n-gram overlap) — always available.
2. Optional Gemini JSON judge when GEMINI_API_KEY is configured (higher-level plot cues).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Sequence

import gemini_client

# Minimum local score to accept a match without Gemini confirmation.
LOCAL_ACCEPT_THRESHOLD = 0.38
# Below this after all methods → no match.
HARD_REJECT_THRESHOLD = 0.28


@dataclass(frozen=True)
class EpisodeCandidate:
    """One existing manuscript unit (SuperTory scene = 회차)."""

    scene_id: int
    chapter_id: int
    episode_number: int
    title: str
    preview: str  # first ~100 chars of plain body
    chapter_title: str = ""


@dataclass(frozen=True)
class MatchResult:
    matched_scene_id: int | None
    matched_chapter_id: int | None
    matched_episode_number: int | None
    matched_title: str
    confidence_score: float
    match_reason: str
    method: str  # "local" | "gemini" | "none"
    candidates: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # Spec-compatible aliases (chapter ≈ 회차 in web-novel workflows)
        data["matched_chapter_id"] = (
            f"sc_{self.matched_scene_id}" if self.matched_scene_id is not None else None
        )
        data["matched_chapter_number"] = self.matched_episode_number
        data["matched_scene_id"] = self.matched_scene_id
        data["matched_project_chapter_id"] = self.matched_chapter_id
        return data


_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[“”\"'‘’…·・.,，。!?！？~\-—–:：;；()\[\]{}<>《》〈〉「」『』·\u3000]+")
_EPISODE_NO = re.compile(
    r"(?:"
    r"제\s*(\d+)\s*[화회장편부화]"
    r"|(\d+)\s*화"
    r"|episode\s*(\d+)"
    r"|ep\.?\s*(\d+)"
    r"|#\s*(\d+)"
    r")",
    re.IGNORECASE,
)


def normalize_text(raw: str) -> str:
    text = str(raw or "")
    text = _PUNCT.sub(" ", text)
    text = _WS.sub(" ", text)
    return text.strip().lower()


def plain_preview(text: str, limit: int = 100) -> str:
    cleaned = _WS.sub(" ", str(text or "").strip())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "…"


def extract_episode_number(*chunks: str) -> int | None:
    for chunk in chunks:
        if not chunk:
            continue
        match = _EPISODE_NO.search(str(chunk))
        if not match:
            continue
        for group in match.groups():
            if group is not None:
                try:
                    return int(group)
                except ValueError:
                    continue
    return None


def _char_ngrams(text: str, n: int = 2) -> set[str]:
    if len(text) < n:
        return {text} if text else set()
    return {text[i : i + n] for i in range(len(text) - n + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)


def _prefix_ratio(a: str, b: str, window: int = 120) -> float:
    aa = a[:window]
    bb = b[:window]
    if not aa or not bb:
        return 0.0
    # Longest common prefix length ratio
    n = min(len(aa), len(bb))
    i = 0
    while i < n and aa[i] == bb[i]:
        i += 1
    if i >= 8:
        return i / max(len(aa), len(bb), 1)
    # Fallback: shared window n-grams on prefixes
    return _jaccard(_char_ngrams(aa, 3), _char_ngrams(bb, 3))


def _title_score(target_title: str, cand_title: str) -> float:
    t = normalize_text(target_title)
    c = normalize_text(cand_title)
    if not t or not c:
        return 0.0
    if t == c:
        return 1.0
    if t in c or c in t:
        return 0.85
    return _jaccard(_char_ngrams(t, 2), _char_ngrams(c, 2))


def score_candidate(
    target_text: str,
    target_title: str,
    candidate: EpisodeCandidate,
) -> tuple[float, str]:
    """Return (score 0..1, short reason)."""
    body = normalize_text(target_text)
    title_n = normalize_text(target_title)
    preview_n = normalize_text(candidate.preview)
    cand_title_n = normalize_text(candidate.title)

    # Use first portion of target as "body head" for comparison with stored preview.
    head = body[:400]
    title_s = _title_score(target_title or body[:40], candidate.title)
    prefix_s = _prefix_ratio(head, preview_n, window=100)
    body_s = _jaccard(_char_ngrams(head, 3), _char_ngrams(preview_n + " " + cand_title_n, 3))

    # Episode number bonus
    target_no = extract_episode_number(target_title, target_text[:200])
    no_bonus = 0.0
    no_note = ""
    if target_no is not None and target_no == candidate.episode_number:
        no_bonus = 0.22
        no_note = f"회차 번호 {target_no} 일치"
    elif target_no is not None and abs(target_no - candidate.episode_number) == 0:
        no_bonus = 0.22

    # Weighted blend
    score = (
        0.34 * title_s
        + 0.38 * max(prefix_s, body_s)
        + 0.18 * body_s
        + no_bonus
    )
    score = max(0.0, min(1.0, score))

    reasons: list[str] = []
    if title_s >= 0.7:
        reasons.append("제목 유사")
    if prefix_s >= 0.45 or body_s >= 0.35:
        reasons.append("본문 앞부분 겹침")
    if no_note:
        reasons.append(no_note)
    if not reasons:
        reasons.append("부분 유사")
    reason = f"{candidate.episode_number}화 「{candidate.title or '제목 없음'}」: " + " · ".join(reasons)
    return score, reason


def rank_episodes(
    target_text: str,
    episodes: Sequence[EpisodeCandidate],
    *,
    target_title: str = "",
    top_k: int = 5,
) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for ep in episodes:
        score, reason = score_candidate(target_text, target_title, ep)
        scored.append({
            "scene_id": ep.scene_id,
            "chapter_id": ep.chapter_id,
            "episode_number": ep.episode_number,
            "title": ep.title,
            "preview": ep.preview,
            "score": round(score, 4),
            "reason": reason,
        })
    scored.sort(key=lambda row: (-row["score"], row["episode_number"], row["scene_id"]))
    return scored[: max(1, top_k)]


def match_local(
    target_text: str,
    episodes: Sequence[EpisodeCandidate],
    *,
    target_title: str = "",
) -> MatchResult:
    if not episodes:
        return MatchResult(
            matched_scene_id=None,
            matched_chapter_id=None,
            matched_episode_number=None,
            matched_title="",
            confidence_score=0.0,
            match_reason="비교할 기존 회차가 없습니다.",
            method="none",
            candidates=(),
        )
    text = str(target_text or "").strip()
    if len(text) < 8 and not str(target_title or "").strip():
        return MatchResult(
            matched_scene_id=None,
            matched_chapter_id=None,
            matched_episode_number=None,
            matched_title="",
            confidence_score=0.0,
            match_reason="업로드 텍스트가 너무 짧아 매칭할 수 없습니다.",
            method="none",
            candidates=(),
        )

    ranked = rank_episodes(text, episodes, target_title=target_title, top_k=5)
    best = ranked[0]
    second = ranked[1]["score"] if len(ranked) > 1 else 0.0
    score = float(best["score"])
    # Penalize ties / close runners-up
    if second > 0 and score - second < 0.04:
        score = max(0.0, score - 0.06)

    if score < HARD_REJECT_THRESHOLD:
        return MatchResult(
            matched_scene_id=None,
            matched_chapter_id=None,
            matched_episode_number=None,
            matched_title="",
            confidence_score=round(score, 4),
            match_reason=(
                f"확실한 회차를 찾지 못했습니다. 가장 가까운 후보: "
                f"{best['episode_number']}화 「{best['title']}」(점수 {score:.2f})"
            ),
            method="none",
            candidates=tuple(ranked),
        )

    return MatchResult(
        matched_scene_id=int(best["scene_id"]),
        matched_chapter_id=int(best["chapter_id"]),
        matched_episode_number=int(best["episode_number"]),
        matched_title=str(best["title"] or ""),
        confidence_score=round(score, 4),
        match_reason=str(best["reason"]),
        method="local",
        candidates=tuple(ranked),
    )


_SYSTEM_PROMPT = """[Role]
당신은 소설 원고의 회차 식별 및 매칭 전문 AI입니다.
새로 업로드된 원고(HWP/HWPX 추출 텍스트)의 내용을 분석하여 기존 프로젝트 회차 목록 중 가장 일치하는 회차(Chapter/Scene)를 찾아내야 합니다.

[Task]
- Target_Text의 제목, 등장인물, 줄거리, 문맥을 Chapter_List와 비교하세요.
- 가장 높은 연관성을 보이는 회차의 ID와 매칭 신뢰도(Confidence Score)를 산출하세요.
- 반드시 제공된 Chapter_List에 있는 scene_id만 고르세요. 없으면 matched_scene_id를 null로 두세요.
- 설명 없이 JSON 객체만 출력하세요.

[Output Format (JSON Only)]
{
  "matched_scene_id": 123,
  "matched_episode_number": 15,
  "matched_title": "제15화: 약속의 장소",
  "confidence_score": 0.95,
  "match_reason": "등장인물 대사와 장소 묘사가 기존 15화의 전반부와 일치함."
}
"""


def _parse_gemini_json(raw: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    # Strip markdown fences if present
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        # Try first {...} block
        brace = re.search(r"\{[\s\S]*\}", text)
        if not brace:
            return None
        try:
            data = json.loads(brace.group(0))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None


def match_with_gemini(
    target_text: str,
    episodes: Sequence[EpisodeCandidate],
    *,
    target_title: str = "",
    local_fallback: MatchResult | None = None,
) -> MatchResult:
    """Ask Gemini to pick the best episode; validate against the candidate list."""
    if not episodes:
        return local_fallback or match_local(target_text, episodes, target_title=target_title)
    if not gemini_client.is_configured():
        return local_fallback or match_local(target_text, episodes, target_title=target_title)

    local = local_fallback or match_local(target_text, episodes, target_title=target_title)
    # Strong local match: skip network
    if local.matched_scene_id is not None and local.confidence_score >= 0.82:
        return local

    chapter_list = [
        {
            "scene_id": ep.scene_id,
            "episode_number": ep.episode_number,
            "title": ep.title,
            "preview": ep.preview[:100],
            "chapter_title": ep.chapter_title,
        }
        for ep in episodes[:80]  # cap prompt size
    ]
    target_clip = str(target_text or "")[:3500]
    user_prompt = (
        "[Input Data]\n"
        f"1. Target_Title: {target_title or '(없음)'}\n"
        f"2. Target_Text:\n{target_clip}\n\n"
        f"3. Chapter_List (JSON):\n{json.dumps(chapter_list, ensure_ascii=False)}\n"
    )
    try:
        raw = gemini_client.generate_text(
            user_prompt,
            system=_SYSTEM_PROMPT,
            temperature=0.15,
            max_output_tokens=512,
        )
    except gemini_client.GeminiError:
        return local

    parsed = _parse_gemini_json(raw)
    if not parsed:
        return local

    id_map = {ep.scene_id: ep for ep in episodes}
    raw_id = parsed.get("matched_scene_id")
    scene_id: int | None = None
    try:
        if raw_id is not None and str(raw_id).strip() != "":
            scene_id = int(raw_id)
    except (TypeError, ValueError):
        # Allow ch_015 / sc_123 style
        m = re.search(r"(\d+)", str(raw_id or ""))
        if m:
            maybe = int(m.group(1))
            if maybe in id_map:
                scene_id = maybe
            else:
                # treat as episode number
                for ep in episodes:
                    if ep.episode_number == maybe:
                        scene_id = ep.scene_id
                        break

    if scene_id is None or scene_id not in id_map:
        return local

    ep = id_map[scene_id]
    try:
        conf = float(parsed.get("confidence_score", 0.7))
    except (TypeError, ValueError):
        conf = 0.7
    conf = max(0.0, min(1.0, conf))
    reason = str(parsed.get("match_reason") or "").strip() or f"Gemini가 {ep.episode_number}화로 판정"
    title = str(parsed.get("matched_title") or ep.title or "")

    # Blend with local score for calibration
    local_score_for = 0.0
    for row in local.candidates:
        if int(row.get("scene_id") or 0) == scene_id:
            local_score_for = float(row.get("score") or 0)
            break
    blended = max(conf, local_score_for)
    if local_score_for > 0:
        blended = 0.55 * conf + 0.45 * local_score_for
    blended = max(0.0, min(1.0, blended))

    if blended < HARD_REJECT_THRESHOLD:
        return local

    return MatchResult(
        matched_scene_id=ep.scene_id,
        matched_chapter_id=ep.chapter_id,
        matched_episode_number=ep.episode_number,
        matched_title=title,
        confidence_score=round(blended, 4),
        match_reason=reason,
        method="gemini",
        candidates=local.candidates,
    )


def match_episode(
    target_text: str,
    episodes: Sequence[EpisodeCandidate],
    *,
    target_title: str = "",
    use_ai: bool = True,
) -> MatchResult:
    """Public entry: local first, optional Gemini when useful."""
    local = match_local(target_text, episodes, target_title=target_title)
    if not use_ai:
        return local
    if local.matched_scene_id is not None and local.confidence_score >= 0.82:
        return local
    if not gemini_client.is_configured():
        return local
    return match_with_gemini(
        target_text,
        episodes,
        target_title=target_title,
        local_fallback=local,
    )
