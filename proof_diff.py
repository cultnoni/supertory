"""Compare original manuscript vs editor-revised text (교정/교열 report).

Produces a structured JSON report:
- typo vs stylistic vs structural (add/delete) chunks
- editor memos / comments extracted from revised text when present
- summary for the author

Local analysis always runs (difflib). When Gemini is configured, the model may
refine classification and overall_comment using the user-facing editor role.
"""

from __future__ import annotations

import json
import re
import difflib
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

import gemini_client

_WS = re.compile(r"\s+")

# Patterns that often appear when HWP/HWPX comments are exported as plain text.
_MEMO_LINE_PATTERNS = [
    re.compile(
        r"^\s*(?:【\s*(?:편집\s*)?(?:메모|주석|코멘트)\s*】|"
        r"\[\s*(?:편집자|에디터|메모|주석|comment)\s*\]|"
        r"(?:※|★|◆)\s*(?:편집\s*)?(?:메모|주석)\s*[:：]?|"
        r"(?:편집자|에디터)\s*(?:메모|의견|코멘트)\s*[:：]|"
        r"MEMO\s*[:：]|COMMENT\s*[:：])\s*(.+?)\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:메모|주석|코멘트)\s*[:：]\s*(.+?)\s*$",
        re.IGNORECASE,
    ),
]
# Inline: sentence【메모: ...】 or (편집 메모: ...)
_INLINE_MEMO = re.compile(
    r"(?P<ctx>.{0,40}?)(?:【\s*(?:메모|주석)\s*[:：]?\s*(?P<m1>.+?)\s*】|"
    r"\(\s*(?:편집\s*)?(?:메모|주석)\s*[:：]\s*(?P<m2>.+?)\s*\)|"
    r"\[\s*(?:메모|주석)\s*[:：]\s*(?P<m3>.+?)\s*\])",
    re.IGNORECASE,
)

# Typo-ish: short token change, hangul jamo-ish fixes, spacing, punctuation only
_PUNCT_ONLY = re.compile(r"^[\s\W_]+$", re.UNICODE)


@dataclass
class EditorMemo:
    location_context: str
    memo_content: str

    def to_dict(self) -> dict[str, str]:
        return {
            "location_context": self.location_context,
            "memo_content": self.memo_content,
        }


@dataclass
class DiffChunk:
    type: str  # modified | added | deleted | typo | stylistic
    original: str
    revised: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "type": self.type,
            "original": self.original,
            "revised": self.revised,
            "reason": self.reason,
        }


@dataclass
class ProofSummary:
    typo_corrections_count: int = 0
    stylistic_edits_count: int = 0
    structural_edits_count: int = 0  # add/delete paragraphs
    editor_memos_count: int = 0
    overall_comment: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProofReport:
    summary: ProofSummary
    editor_memos: list[EditorMemo] = field(default_factory=list)
    diff_chunks: list[DiffChunk] = field(default_factory=list)
    method: str = "local"  # local | gemini | hybrid
    original_preview: str = ""
    revised_preview: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary.to_dict(),
            "editor_memos": [m.to_dict() for m in self.editor_memos],
            "diff_chunks": [c.to_dict() for c in self.diff_chunks],
            "method": self.method,
            "original_preview": self.original_preview,
            "revised_preview": self.revised_preview,
        }


def normalize_for_compare(text: str) -> str:
    t = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    t = t.replace("\u00a0", " ")
    # Collapse spaces but keep newlines as paragraph structure
    lines = [_WS.sub(" ", line).strip() for line in t.split("\n")]
    # Drop empty lines at edges but keep blank-line paragraph breaks as single blank
    out: list[str] = []
    prev_blank = False
    for line in lines:
        if not line:
            if not prev_blank and out:
                out.append("")
            prev_blank = True
        else:
            out.append(line)
            prev_blank = False
    return "\n".join(out).strip()


def split_units(text: str) -> list[str]:
    """Split into sentence/paragraph-ish units for readable diffs."""
    text = normalize_for_compare(text)
    if not text:
        return []
    # Prefer paragraph blocks; fall back to sentences within long paragraphs.
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    units: list[str] = []
    for block in blocks:
        if len(block) <= 180:
            units.append(block)
            continue
        # Sentence split (Korean + Western)
        parts = re.split(r"(?<=[.!?…。！？])\s+|(?<=다\.)\s+|(?<=요\.)\s+", block)
        parts = [p.strip() for p in parts if p and p.strip()]
        if len(parts) <= 1:
            units.append(block)
        else:
            units.extend(parts)
    return units


def extract_memos(revised_text: str) -> tuple[str, list[EditorMemo]]:
    """Pull editor comments out of revised text; return cleaned text + memos."""
    memos: list[EditorMemo] = []
    lines = str(revised_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cleaned_lines: list[str] = []
    last_content = ""

    for line in lines:
        stripped = line.strip()
        matched = False
        for pat in _MEMO_LINE_PATTERNS:
            m = pat.match(stripped)
            if m:
                content = (m.group(1) or "").strip()
                if content:
                    memos.append(EditorMemo(
                        location_context=last_content[:80] or "(위치 미상)",
                        memo_content=content,
                    ))
                matched = True
                break
        if matched:
            continue

        # Inline memos on the same line as content
        inline_found = False
        for im in _INLINE_MEMO.finditer(line):
            memo = (im.group("m1") or im.group("m2") or im.group("m3") or "").strip()
            ctx = (im.group("ctx") or "").strip() or last_content[:80]
            if memo:
                memos.append(EditorMemo(location_context=ctx[:80], memo_content=memo))
                inline_found = True
        if inline_found:
            # Remove inline memo markers from visible manuscript
            cleaned = _INLINE_MEMO.sub(lambda m: (m.group("ctx") or ""), line)
            cleaned = cleaned.strip()
            if cleaned:
                cleaned_lines.append(cleaned)
                last_content = cleaned
            continue

        cleaned_lines.append(line)
        if stripped:
            last_content = stripped

    cleaned_text = "\n".join(cleaned_lines)
    # Deduplicate memos
    seen: set[tuple[str, str]] = set()
    unique: list[EditorMemo] = []
    for memo in memos:
        key = (memo.location_context, memo.memo_content)
        if key in seen:
            continue
        seen.add(key)
        unique.append(memo)
    return cleaned_text, unique


def _is_typo_change(a: str, b: str) -> bool:
    a = a.strip()
    b = b.strip()
    if not a or not b:
        return False
    # Pure spacing / punctuation
    a_core = re.sub(r"\s+", "", a)
    b_core = re.sub(r"\s+", "", b)
    if a_core == b_core:
        return True
    # Very short edits (1–3 chars net change) → likely typo/조사
    sm = difflib.SequenceMatcher(None, a, b)
    ratio = sm.ratio()
    if ratio >= 0.88 and abs(len(a) - len(b)) <= 4:
        # Count non-equal opcodes size
        changed = 0
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            changed += max(i2 - i1, j2 - j1)
        if changed <= 6:
            return True
    if ratio >= 0.92:
        return True
    return False


def _classify_reason(kind: str, original: str, revised: str) -> str:
    if kind == "typo":
        return "오탈자·맞춤법·띄어쓰기 또는 짧은 조사 수정"
    if kind == "added":
        return "단락/문장 추가"
    if kind == "deleted":
        return "단락/문장 삭제"
    if kind == "stylistic":
        return "문장 표현·문체 교정"
    return "문맥 표현 교정"


def classify_pair(original: str, revised: str) -> tuple[str, str]:
    """Return (type, reason) for a modified unit pair."""
    if _is_typo_change(original, revised):
        return "typo", _classify_reason("typo", original, revised)
    # Moderate length change with low shared ratio → stylistic
    ratio = difflib.SequenceMatcher(None, original, revised).ratio()
    if ratio < 0.55:
        return "stylistic", _classify_reason("stylistic", original, revised)
    if abs(len(original) - len(revised)) > max(12, int(0.25 * max(len(original), 1))):
        return "stylistic", _classify_reason("stylistic", original, revised)
    return "modified", _classify_reason("modified", original, revised)


def build_diff_chunks(original_text: str, revised_text: str, *, max_chunks: int = 80) -> list[DiffChunk]:
    orig_units = split_units(original_text)
    rev_units = split_units(revised_text)
    matcher = difflib.SequenceMatcher(None, orig_units, rev_units, autojunk=False)
    chunks: list[DiffChunk] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            left = orig_units[i1:i2]
            right = rev_units[j1:j2]
            # Pair by index when counts match; else emit as groups
            if len(left) == len(right):
                for o, r in zip(left, right):
                    kind, reason = classify_pair(o, r)
                    chunks.append(DiffChunk(type=kind, original=o, revised=r, reason=reason))
            else:
                # Zip min, then leftovers as delete/add
                n = min(len(left), len(right))
                for k in range(n):
                    kind, reason = classify_pair(left[k], right[k])
                    chunks.append(DiffChunk(type=kind, original=left[k], revised=right[k], reason=reason))
                for o in left[n:]:
                    chunks.append(DiffChunk(
                        type="deleted", original=o, revised="", reason=_classify_reason("deleted", o, ""),
                    ))
                for r in right[n:]:
                    chunks.append(DiffChunk(
                        type="added", original="", revised=r, reason=_classify_reason("added", "", r),
                    ))
        elif tag == "delete":
            for o in orig_units[i1:i2]:
                chunks.append(DiffChunk(
                    type="deleted", original=o, revised="", reason=_classify_reason("deleted", o, ""),
                ))
        elif tag == "insert":
            for r in rev_units[j1:j2]:
                chunks.append(DiffChunk(
                    type="added", original="", revised=r, reason=_classify_reason("added", "", r),
                ))
        if len(chunks) >= max_chunks:
            break
    return chunks[:max_chunks]


def build_summary(chunks: Sequence[DiffChunk], memos: Sequence[EditorMemo]) -> ProofSummary:
    typo = sum(1 for c in chunks if c.type == "typo")
    stylistic = sum(1 for c in chunks if c.type in {"stylistic", "modified"})
    structural = sum(1 for c in chunks if c.type in {"added", "deleted"})
    memo_n = len(memos)

    parts: list[str] = []
    if typo:
        parts.append(f"오탈자·맞춤법 수정 {typo}건")
    if stylistic:
        parts.append(f"문장·표현 교정 {stylistic}건")
    if structural:
        parts.append(f"단락 추가/삭제 {structural}건")
    if memo_n:
        parts.append(f"편집자 메모 {memo_n}건")
    if not parts:
        overall = "원본과 교정고 사이에 의미 있는 차이가 거의 없습니다."
    else:
        overall = "전반적으로 " + ", ".join(parts) + "이(가) 확인됩니다."
        if memo_n and typo and not stylistic:
            overall = f"맞춤법·조사 수정 위주이며, 편집자 메모 {memo_n}건이 있습니다."
        elif memo_n:
            overall = f"{', '.join(parts[:-1]) + (' · ' if len(parts) > 1 else '')}편집 메모 {memo_n}건을 함께 확인해 주세요." if len(parts) > 1 else overall

    return ProofSummary(
        typo_corrections_count=typo,
        stylistic_edits_count=stylistic,
        structural_edits_count=structural,
        editor_memos_count=memo_n,
        overall_comment=overall,
    )


def analyze_local(original_text: str, revised_text: str) -> ProofReport:
    cleaned_revised, memos = extract_memos(revised_text)
    # Optional second pass: if caller did not pre-clean, still strip light HWP junk
    try:
        import proof_clean
        cleaned_revised = proof_clean.clean_proof_text(cleaned_revised) or cleaned_revised
    except Exception:
        pass
    orig = normalize_for_compare(original_text)
    rev = normalize_for_compare(cleaned_revised)
    chunks = build_diff_chunks(orig, rev)
    summary = build_summary(chunks, memos)
    return ProofReport(
        summary=summary,
        editor_memos=memos,
        diff_chunks=chunks,
        method="local",
        original_preview=(orig[:160] + ("…" if len(orig) > 160 else "")),
        revised_preview=(rev[:160] + ("…" if len(rev) > 160 else "")),
    )


_SYSTEM_PROMPT = """[Role]
당신은 출판 교정/교열 전문 에디터 AI입니다.
[원본 원고]와 [편집자 교정 원고]를 비교 정밀 분석하여 변경된 사항과 편집자의 메모를 파싱하세요.

[Task]
1. 단순 오탈자 수정, 문장 교정, 단락 추가/삭제를 구분하여 분류하세요.
2. 편집자가 남긴 메모(Comments)가 있다면 해당 문맥과 함께 추출하세요.
3. 수정 내역 전체를 작가가 한눈에 볼 수 있도록 요약 보고서를 작성하세요.
4. 설명 없이 JSON 객체만 출력하세요. 마크다운 코드펜스 없이 raw JSON만.

[Output Format (JSON Only)]
{
  "summary": {
    "typo_corrections_count": 4,
    "stylistic_edits_count": 2,
    "editor_memos_count": 1,
    "overall_comment": "전반적으로 어색한 조사 및 맞춤법 수정 위주이며, 후반부 감정선에 메모 1건이 있습니다."
  },
  "editor_memos": [
    {
      "location_context": "그가 천천히 고개를 들었다.",
      "memo_content": "이 부분 주인공의 심리가 조금 더 묘사되면 좋겠습니다."
    }
  ],
  "diff_chunks": [
    {
      "type": "modified",
      "original": "그는 빠르게 달려갔다.",
      "revised": "그는 쏜살같이 달려갔다.",
      "reason": "문맥 표현 매끄럽게 교정"
    }
  ]
}

type은 typo | modified | stylistic | added | deleted 중 하나를 쓰세요.
"""


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        brace = re.search(r"\{[\s\S]*\}", text)
        if not brace:
            return None
        try:
            data = json.loads(brace.group(0))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None


def _report_from_gemini_payload(data: dict[str, Any], fallback: ProofReport) -> ProofReport:
    summary_raw = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    memos_raw = data.get("editor_memos") if isinstance(data.get("editor_memos"), list) else []
    chunks_raw = data.get("diff_chunks") if isinstance(data.get("diff_chunks"), list) else []

    memos: list[EditorMemo] = []
    for item in memos_raw[:40]:
        if not isinstance(item, dict):
            continue
        content = str(item.get("memo_content") or "").strip()
        if not content:
            continue
        memos.append(EditorMemo(
            location_context=str(item.get("location_context") or "(위치 미상)")[:120],
            memo_content=content[:500],
        ))
    # Keep local memos if Gemini missed them
    if not memos and fallback.editor_memos:
        memos = list(fallback.editor_memos)

    chunks: list[DiffChunk] = []
    allowed = {"typo", "modified", "stylistic", "added", "deleted"}
    for item in chunks_raw[:80]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "modified").lower()
        if kind not in allowed:
            kind = "modified"
        original = str(item.get("original") or "")
        revised = str(item.get("revised") or "")
        if not original and not revised:
            continue
        reason = str(item.get("reason") or "").strip() or _classify_reason(kind, original, revised)
        chunks.append(DiffChunk(type=kind, original=original[:800], revised=revised[:800], reason=reason[:200]))
    if not chunks:
        chunks = list(fallback.diff_chunks)

    def _int(key: str, default: int) -> int:
        try:
            return max(0, int(summary_raw.get(key, default)))
        except (TypeError, ValueError):
            return default

    typo = _int("typo_corrections_count", sum(1 for c in chunks if c.type == "typo"))
    stylistic = _int(
        "stylistic_edits_count",
        sum(1 for c in chunks if c.type in {"stylistic", "modified"}),
    )
    # Prefer recount from chunks when AI counts look empty but chunks exist
    if typo == 0 and stylistic == 0 and chunks:
        local_sum = build_summary(chunks, memos)
        typo = local_sum.typo_corrections_count
        stylistic = local_sum.stylistic_edits_count
        structural = local_sum.structural_edits_count
    else:
        structural = sum(1 for c in chunks if c.type in {"added", "deleted"})

    overall = str(summary_raw.get("overall_comment") or "").strip()
    if not overall:
        overall = build_summary(chunks, memos).overall_comment

    return ProofReport(
        summary=ProofSummary(
            typo_corrections_count=typo,
            stylistic_edits_count=stylistic,
            structural_edits_count=structural,
            editor_memos_count=len(memos),
            overall_comment=overall[:500],
        ),
        editor_memos=memos,
        diff_chunks=chunks,
        method="gemini",
        original_preview=fallback.original_preview,
        revised_preview=fallback.revised_preview,
    )


def analyze_with_gemini(original_text: str, revised_text: str, *, local: ProofReport | None = None) -> ProofReport:
    base = local or analyze_local(original_text, revised_text)
    if not gemini_client.is_configured():
        return base

    # Small diffs: local is enough
    if len(base.diff_chunks) <= 2 and not base.editor_memos and base.summary.typo_corrections_count + base.summary.stylistic_edits_count <= 2:
        # still try if texts differ a lot but chunker missed — only skip when identical
        if normalize_for_compare(original_text) == normalize_for_compare(extract_memos(revised_text)[0]):
            return base

    cleaned_rev, _ = extract_memos(revised_text)
    # Cap sizes for API
    orig_clip = normalize_for_compare(original_text)[:6000]
    rev_clip = normalize_for_compare(cleaned_rev)[:6000]
    user_prompt = (
        "[Input Data]\n"
        f"1. Original_Text:\n{orig_clip}\n\n"
        f"2. Revised_Text:\n{rev_clip}\n\n"
        "로컬 사전 분석(참고, 틀릴 수 있음):\n"
        f"{json.dumps(base.to_dict(), ensure_ascii=False)[:2500]}\n"
    )
    try:
        raw = gemini_client.generate_text(
            user_prompt,
            system=_SYSTEM_PROMPT,
            temperature=0.2,
            max_output_tokens=4096,
        )
    except gemini_client.GeminiError:
        return base

    parsed = _parse_json_object(raw)
    if not parsed:
        return base
    try:
        report = _report_from_gemini_payload(parsed, base)
        report.method = "hybrid" if base.diff_chunks else "gemini"
        return report
    except Exception:
        return base


def analyze_proof(
    original_text: str,
    revised_text: str,
    *,
    use_ai: bool = True,
) -> ProofReport:
    local = analyze_local(original_text, revised_text)
    if not use_ai or not gemini_client.is_configured():
        return local
    return analyze_with_gemini(original_text, revised_text, local=local)
