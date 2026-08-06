"""End-to-end 교정고 pipeline: extract → match → clean → proof-diff.

Diagram:
  [.hwp]  ── pyhwp ──────┐
                         ├── UnifiedProofExtract ──► steps 1·2·3
  [.docx] ── python-docx ┘

Steps (same AI/local prompts as product design):
  1) 회차 매칭     (chapter_match)
  2) 본문 정제     (proof_clean)   — pure body JSON clean_full_text
  3) 교정 비교     (proof_diff)    — summary / memos / diff_chunks
"""

from __future__ import annotations

from typing import Any, Sequence

import chapter_match
import proof_clean
import proof_diff
import proof_extract


def run_proof_pipeline(
    *,
    filename: str,
    data: bytes,
    episodes: Sequence[chapter_match.EpisodeCandidate],
    original_text: str | None = None,
    use_ai: bool = True,
    scene_id_hint: int | None = None,
) -> dict[str, Any]:
    """Run extract + three analysis steps. Does not write the database."""

    # ── 0. Unified parse (HWP / DOCX / other) ─────────────────────────
    extracted = proof_extract.extract_proof_document(filename, data)
    revised_raw = extracted.text
    structured_memos = list(extracted.memos)

    # ── 1. Match episode ──────────────────────────────────────────────
    match_result: chapter_match.MatchResult
    if scene_id_hint is not None:
        # Prefer explicit scene; still score for confidence metadata
        id_map = {ep.scene_id: ep for ep in episodes}
        ep = id_map.get(int(scene_id_hint))
        if ep is None:
            match_result = chapter_match.match_episode(
                revised_raw,
                episodes,
                target_title=extracted.title,
                use_ai=use_ai,
            )
        else:
            # Local score against chosen scene for transparency
            score, reason = chapter_match.score_candidate(revised_raw, extracted.title, ep)
            ranked = chapter_match.rank_episodes(revised_raw, episodes, target_title=extracted.title)
            match_result = chapter_match.MatchResult(
                matched_scene_id=ep.scene_id,
                matched_chapter_id=ep.chapter_id,
                matched_episode_number=ep.episode_number,
                matched_title=ep.title,
                confidence_score=round(max(score, 0.55), 4),
                match_reason=reason or f"지정 회차 {ep.episode_number}화",
                method="hint",
                candidates=tuple(ranked),
            )
    else:
        match_result = chapter_match.match_episode(
            revised_raw,
            episodes,
            target_title=extracted.title,
            use_ai=use_ai,
        )

    # ── 2. Clean body ─────────────────────────────────────────────────
    clean_payload = proof_clean.clean_to_dict(revised_raw)
    clean_text = clean_payload["clean_full_text"]

    # ── 3. Proof diff (needs original) ────────────────────────────────
    proof_payload: dict[str, Any] | None = None
    orig = (original_text or "").strip()
    if not orig and match_result.matched_scene_id is not None:
        # Caller should pass original_text when available; leave null if not
        pass
    if orig and clean_text:
        report = proof_diff.analyze_proof(orig, clean_text, use_ai=use_ai)
        proof_payload = report.to_dict()
        # Merge structured memos from DOCX/HWP comments
        existing = {
            (m.get("location_context"), m.get("memo_content"))
            for m in (proof_payload.get("editor_memos") or [])
            if isinstance(m, dict)
        }
        for memo in structured_memos:
            key = (memo.location_context, memo.memo_content)
            if key in existing:
                continue
            proof_payload.setdefault("editor_memos", []).append(memo.to_dict())
            existing.add(key)
        if proof_payload.get("summary"):
            proof_payload["summary"]["editor_memos_count"] = len(
                proof_payload.get("editor_memos") or []
            )
        proof_payload["clean_full_text"] = clean_text
    elif structured_memos:
        # Diff skipped but still return memos + clean text
        proof_payload = {
            "summary": {
                "typo_corrections_count": 0,
                "stylistic_edits_count": 0,
                "structural_edits_count": 0,
                "editor_memos_count": len(structured_memos),
                "overall_comment": "원본 회차 본문이 없어 교정 비교는 생략하고 메모·정제 결과만 제공합니다.",
            },
            "editor_memos": [m.to_dict() for m in structured_memos],
            "diff_chunks": [],
            "method": "extract-only",
            "clean_full_text": clean_text,
        }

    return {
        "extract": extracted.to_dict(),
        "step1_match": match_result.to_dict(),
        "step2_clean": clean_payload,
        "step3_proof": proof_payload,
        "clean_full_text": clean_text,
        "parser_status": proof_extract.parser_status(),
    }
