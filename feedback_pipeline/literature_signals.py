"""단편 리포트 1단계. 코드로만 뽑는 통계와 국소 신호."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from feedback_pipeline.literature_text import paper_pages
from feedback_pipeline.config import LONG_QUOTE_MONOLOGUE_CHARS

_SENTENCE_SPLIT = re.compile(r"(?<=[\.!?…。])\s+")
_WORD = re.compile(r"[가-힣]{2,}")
_CHAIN_UI = re.compile(r"[가-힣]{1,8}의[가-힣]{0,6}의")
_JEOK = re.compile(r"[가-힣]{1,6}적(?:인|으로)?")
_GEOSIDA = re.compile(r"것이다")
_ADVERBS = (
    "매우",
    "너무",
    "정말",
    "갑자기",
    "천천히",
    "가만히",
    "조용히",
    "살짝",
    "완전히",
    "이미",
    "아직",
    "다시",
    "계속",
    "점점",
    "문득",
    "한참",
    "마침내",
    "결국",
)
_PASSIVE = re.compile(r"(?:되었다|됐다|받았다|당했다|여겼다|보였다|느껴졌다|지어졌다)")
_DIALOGUE = re.compile(r"[「」『』\"“”]")
_QUOTE_OPEN_CLOSE = (
    ("「", "」"),
    ("『", "』"),
    ("“", "”"),
)


def _sentences(text: str) -> list[str]:
    parts = [part.strip() for part in _SENTENCE_SPLIT.split(text or "") if part.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _spike(local: float, average: float, floor: float) -> bool:
    if local < floor:
        return False
    if average <= 0:
        return local >= floor
    return local >= average * 1.8 and local >= average + 1


def extract_quote_spans(text: str) -> list[str]:
    """따옴표·낫표 안 구간만 추출한다."""
    source = str(text or "")
    spans: list[str] = []
    for open_ch, close_ch in _QUOTE_OPEN_CLOSE:
        start = -1
        for index, ch in enumerate(source):
            if ch == open_ch and start < 0:
                start = index + 1
            elif ch == close_ch and start >= 0:
                spans.append(source[start:index])
                start = -1
    # ASCII 큰따옴표: 짝을 이룬 구간만
    buf: list[str] = []
    in_quote = False
    for ch in source:
        if ch == '"':
            if in_quote:
                spans.append("".join(buf))
                buf = []
                in_quote = False
            else:
                in_quote = True
                buf = []
            continue
        if in_quote:
            buf.append(ch)
    return spans


def count_dialogue_chars(
    text: str,
    *,
    long_quote_limit: int | None = None,
) -> dict[str, Any]:
    """대화 글자 수. 긴 따옴표 구간(기본 300자+)은 인용·독백으로 따로 센다.

    이전에는 따옴표가 하나라도 있으면 문단 전체를 대화로 세어 비율이 과대 계상됐다.
    """
    limit = int(LONG_QUOTE_MONOLOGUE_CHARS if long_quote_limit is None else long_quote_limit)
    spans = extract_quote_spans(text)
    dialogue = 0
    monologue = 0
    for span in spans:
        n = len(span)
        if n >= limit:
            monologue += n
        else:
            dialogue += n
    quote_used = bool(spans) or bool(_DIALOGUE.search(text or ""))
    return {
        "dialogue_chars": dialogue,
        "monologue_chars": monologue,
        "quote_used": quote_used,
        "quote_span_count": len(spans),
    }


def dialogue_ratio_label(ratio: float) -> str:
    value = float(ratio or 0.0)
    if value < 0.12:
        return "대화가 거의 없는 장"
    if value < 0.35:
        return "대화가 일부인 장"
    if value < 0.65:
        return "대화와 서술이 섞인 장"
    return "대화가 대부분인 장"


def collect_signals(
    paragraphs: list[dict[str, Any]],
    *,
    spell_error_count: int | None = None,
    contest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    texts = [str(item.get("text") or "") for item in paragraphs]
    plain = "\n\n".join(texts)
    sentences = [sent for text in texts for sent in _sentences(text)]
    lengths = [len(sent) for sent in sentences]
    para_lengths = [len(text) for text in texts]
    dialogue_chars = 0
    monologue_chars = 0
    quote_used = False
    for text in texts:
        metrics = count_dialogue_chars(text)
        dialogue_chars += int(metrics["dialogue_chars"])
        monologue_chars += int(metrics["monologue_chars"])
        quote_used = quote_used or bool(metrics["quote_used"])
    plain_len = max(1, len(plain))
    dialogue_ratio = dialogue_chars / plain_len
    monologue_ratio = monologue_chars / plain_len
    word_counts: Counter[str] = Counter()
    for text in texts:
        word_counts.update(_WORD.findall(text))
    total_words = sum(word_counts.values()) or 1

    def pattern_count(pattern: re.Pattern[str], text: str) -> int:
        return len(pattern.findall(text))

    chain_all = [_CHAIN_UI.findall(text) for text in texts]
    jeok_all = [_JEOK.findall(text) for text in texts]
    geosida_all = [_GEOSIDA.findall(text) for text in texts]
    adverb_all = [
        sum(text.count(word) for word in _ADVERBS) for text in texts
    ]
    passive_all = [len(_PASSIVE.findall(text)) for text in texts]
    averages = {
        "chain": _mean([len(hits) for hits in chain_all]),
        "jeok": _mean([len(hits) for hits in jeok_all]),
        "geosida": _mean([len(hits) for hits in geosida_all]),
        "adverb": _mean([float(n) for n in adverb_all]),
        "passive": _mean([float(n) for n in passive_all]),
    }
    signals: list[dict[str, Any]] = []
    for index, item in enumerate(paragraphs):
        text = texts[index]
        local_words = Counter(_WORD.findall(text))
        local_total = sum(local_words.values()) or 1
        for word, count in local_words.items():
            if count < 3 or word_counts[word] < 4:
                continue
            local_rate = count / local_total
            global_rate = word_counts[word] / total_words
            if local_rate >= global_rate * 2.5 and local_rate >= 0.08:
                signals.append(
                    {
                        "kind": "repeat",
                        "para": item.get("n"),
                        "scene_id": item.get("scene_id"),
                        "detail": word,
                    }
                )
                break
        checks = (
            ("translation_ui", len(chain_all[index]), averages["chain"], 2),
            ("translation_jeok", len(jeok_all[index]), averages["jeok"], 2),
            ("translation_geosida", len(geosida_all[index]), averages["geosida"], 2),
            ("adverb", adverb_all[index], averages["adverb"], 3),
            ("passive", passive_all[index], averages["passive"], 2),
        )
        for kind, local, average, floor in checks:
            if _spike(float(local), float(average), float(floor)):
                signals.append(
                    {
                        "kind": kind,
                        "para": item.get("n"),
                        "scene_id": item.get("scene_id"),
                        "detail": str(local),
                    }
                )
    pages = paper_pages(plain)
    contest = contest or {}
    page_note = ""
    if int(contest.get("contest_prep") or 0):
        low = contest.get("contest_pages_min")
        high = contest.get("contest_pages_max")
        if low is not None or high is not None:
            page_note = _page_fit(pages, low, high)
    summary = _summary(
        sentence_count=len(sentences),
        sentence_lengths=lengths,
        paragraph_count=len(texts),
        paragraph_lengths=para_lengths,
        dialogue_ratio=dialogue_ratio,
        quote_used=quote_used,
        pages=pages,
        spell_error_count=spell_error_count,
        signals=signals[:24],
        page_note=page_note,
    )
    return {
        "sentence_count": len(sentences),
        "paragraph_count": len(texts),
        "mean_sentence_length": round(_mean([float(n) for n in lengths]), 1),
        "mean_paragraph_length": round(_mean([float(n) for n in para_lengths]), 1),
        "dialogue_ratio": round(dialogue_ratio, 3),
        "monologue_ratio": round(monologue_ratio, 3),
        "dialogue_label": dialogue_ratio_label(dialogue_ratio),
        "quote_used": quote_used,
        "paper_pages": pages,
        "spell_error_count": spell_error_count,
        "page_note": page_note,
        "signals": signals[:24],
        "summary": summary,
    }


def _page_fit(pages: float, low: Any, high: Any) -> str:
    try:
        low_n = None if low is None else float(low)
        high_n = None if high is None else float(high)
    except (TypeError, ValueError):
        return ""
    if low_n is not None and pages < low_n:
        return f"원고지 약 {pages}매로, 규정 최소 {int(low_n)}매보다 짧습니다."
    if high_n is not None and pages > high_n:
        return f"원고지 약 {pages}매로, 규정 최대 {int(high_n)}매보다 깁니다."
    if low_n is not None or high_n is not None:
        return f"원고지 약 {pages}매로, 설정한 분량 규정 안에 있습니다."
    return ""


def _summary(
    *,
    sentence_count: int,
    sentence_lengths: list[int],
    paragraph_count: int,
    paragraph_lengths: list[int],
    dialogue_ratio: float,
    quote_used: bool,
    pages: float,
    spell_error_count: int | None,
    signals: list[dict[str, Any]],
    page_note: str,
) -> str:
    spell = "확인하지 못함" if spell_error_count is None else str(spell_error_count)
    lines = [
        f"문장 수 {sentence_count}, 평균 문장 길이 {round(_mean([float(n) for n in sentence_lengths]), 1)}자.",
        f"문단 수 {paragraph_count}, 평균 문단 길이 {round(_mean([float(n) for n in paragraph_lengths]), 1)}자.",
        f"대화로 보이는 비중 {round(dialogue_ratio * 100)}%, 따옴표 사용 {'있음' if quote_used else '없음'}.",
        f"원고지 약 {pages}매(공백 포함 글자 수 / 200, 근사치).",
        f"맞춤법 검사 신호 개수 {spell}. 개별 오류는 진단이 아니다.",
        "아래 신호는 이 원고 평균보다 튀는 구간뿐이며, 그 자체로 문제가 아니다.",
    ]
    if page_note:
        lines.append(page_note)
    if signals:
        for item in signals:
            lines.append(
                f"- {item.get('kind')} P{item.get('para')} {item.get('detail') or ''}".rstrip()
            )
    else:
        lines.append("- 평균 대비 두드러진 구간 없음")
    return "\n".join(lines)


def spell_error_count(text: str) -> int | None:
    try:
        from korean_speller import check_text
    except ImportError:
        return None
    try:
        result = check_text(text)
    except Exception:
        return None
    if not isinstance(result, dict):
        return None
    try:
        return int(result.get("error_count") or 0)
    except (TypeError, ValueError):
        return None
