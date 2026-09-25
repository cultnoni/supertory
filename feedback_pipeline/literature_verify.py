"""리포트 검증. 근거 없는 판정, 수정 문장, 점수, 당선 예측을 걷어 낸다."""

from __future__ import annotations

import re
from typing import Any

from feedback_pipeline.literature_text import loose_norm, quote_in_text

_PERCENT = re.compile(r"\d+(?:\.\d+)?\s*(?:%|퍼센트|점)")
_SCORE = re.compile(r"(?:점수|평점|등급)\s*[:：]?\s*[A-F0-9]")
_PRIZE = re.compile(r"(?:당선\s*확률|당선\s*가능|수상\s*가능|예선\s*통과\s*확률)")
_REWRITE = re.compile(
    r"(?:수정안|고치면|이렇게\s*바꾸|다음과\s*같이\s*고치|→\s*[\"“「])"
)


def _log(log: list[dict[str, str]], action: str, target: str, detail: str) -> None:
    log.append({"action": action, "target": target, "detail": detail[:240]})


def _clean_prose(text: str, log: list[dict[str, str]], target: str) -> str:
    kept: list[str] = []
    for sentence in re.split(r"(?<=[\.!?。])\s+|\n+", str(text or "")):
        piece = sentence.strip()
        if not piece:
            continue
        if _PERCENT.search(piece) or _SCORE.search(piece) or _PRIZE.search(piece):
            _log(log, "drop_score", target, piece)
            continue
        if _REWRITE.search(piece):
            _log(log, "drop_rewrite", target, piece)
            continue
        kept.append(piece)
    return " ".join(kept).strip()


def _evidence_ok(item: dict[str, Any], source: str, allowed: set[str]) -> bool:
    quote = str(item.get("quote") or "").strip()
    if not quote_in_text(quote, source):
        return False
    if allowed and loose_norm(quote) not in allowed and not any(
        loose_norm(quote) in prior or prior in loose_norm(quote) for prior in allowed
    ):
        return False
    return True


def _allowed_quotes(rubric: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for item in list(rubric.get("items") or []) + list(rubric.get("strengths") or []):
        if not isinstance(item, dict):
            continue
        evidences = item.get("evidence")
        if isinstance(evidences, list):
            for evidence in evidences:
                if isinstance(evidence, dict):
                    norm = loose_norm(str(evidence.get("quote") or ""))
                    if len(norm) >= 4:
                        found.add(norm)
        norm = loose_norm(str(item.get("quote") or ""))
        if len(norm) >= 4:
            found.add(norm)
    return found


def _choice_covers(note: str, quote: str, choice: str) -> bool:
    source = loose_norm(choice)
    if len(source) < 4:
        return False
    for raw in re.split(r"[,，.。\n]", choice):
        phrase = loose_norm(raw)
        if len(phrase) < 4:
            continue
        if phrase in loose_norm(note) or phrase in loose_norm(quote):
            return True
    return False


def verify_literature_report(
    report: dict[str, Any],
    *,
    source_text: str,
    rubric: dict[str, Any],
    style_choice: str,
    contest_on: bool,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    log: list[dict[str, str]] = []
    cleaned = dict(report or {})
    allowed = _allowed_quotes(rubric)
    if not contest_on:
        overview = dict(cleaned.get("overview") or {})
        if str(overview.get("judge") or "").strip():
            _log(log, "drop_judge", "overview", str(overview.get("judge") or ""))
            overview["judge"] = ""
            cleaned["overview"] = overview
        items = []
        for item in cleaned.get("diagnoses") or []:
            if isinstance(item, dict) and str(item.get("key") or "") == "novelty":
                _log(log, "drop_novelty", "diagnoses", str(item.get("note") or ""))
                continue
            items.append(item)
        cleaned["diagnoses"] = items

    overview = {}
    for key, value in dict(cleaned.get("overview") or {}).items():
        overview[key] = _clean_prose(str(value or ""), log, f"overview.{key}")
    cleaned["overview"] = overview
    reading = dict(cleaned.get("reading") or {})
    reading["body"] = _clean_prose(str(reading.get("body") or ""), log, "reading")
    reading["intent_gap"] = _clean_prose(str(reading.get("intent_gap") or ""), log, "reading.intent")
    cleaned["reading"] = reading

    diagnoses: list[dict[str, Any]] = []
    for item in cleaned.get("diagnoses") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        note = _clean_prose(str(item.get("note") or ""), log, key or "diagnosis")
        evidence = []
        for raw in item.get("evidence") or []:
            if not isinstance(raw, dict):
                continue
            if _evidence_ok(raw, source_text, allowed):
                evidence.append(raw)
            else:
                _log(log, "drop_quote", key or "diagnosis", str(raw.get("quote") or ""))
        verdict = str(item.get("verdict") or "")
        intentional = bool(item.get("intentional"))
        quotes = " ".join(str(row.get("quote") or "") for row in evidence)
        if verdict == "problem" and _choice_covers(note, quotes, style_choice):
            intentional = True
            verdict = "room"
            _log(log, "mark_intentional", key or "diagnosis", note or quotes)
        if not evidence:
            _log(log, "drop_verdict", key or "diagnosis", note)
            continue
        diagnoses.append({**item, "note": note, "evidence": evidence, "verdict": verdict, "intentional": intentional})
    cleaned["diagnoses"] = diagnoses

    strengths = []
    for item in cleaned.get("strengths") or []:
        if not isinstance(item, dict):
            continue
        if not _evidence_ok(item, source_text, allowed):
            _log(log, "drop_quote", "strength", str(item.get("quote") or ""))
            continue
        strengths.append(
            {
                **item,
                "body": _clean_prose(str(item.get("body") or ""), log, "strength"),
            }
        )
    cleaned["strengths"] = strengths
    tasks = []
    for task in cleaned.get("tasks") or []:
        piece = _clean_prose(str(task or ""), log, "task")
        if piece:
            tasks.append(piece)
    cleaned["tasks"] = tasks[:3]
    return cleaned, log
