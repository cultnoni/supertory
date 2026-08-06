"""Clean HWP/HWPX proof exports into pure manuscript body text.

Removes editor comments, typesetting leftovers, control symbols, and noisy
whitespace while keeping web-novel / print-friendly paragraph breaks.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# ── Editor / revision / typesetting noise ─────────────────────────────

_MEMO_LINE = re.compile(
    r"^\s*(?:"
    r"【\s*(?:편집\s*)?(?:메모|주석|코멘트|의견)\s*】|"
    r"\[\s*(?:편집자|에디터|메모|주석|comment|editor)\s*\]|"
    r"(?:※|★|◆|▶|■)\s*(?:편집\s*)?(?:메모|주석|의견)\s*[:：]?|"
    r"(?:편집자|에디터|교정자|교열자)\s*(?:메모|의견|코멘트|주석)\s*[:：]|"
    r"MEMO\s*[:：]|COMMENT\s*[:：]|"
    r"(?:메모|주석|코멘트)\s*[:：]"
    r")\s*.*$",
    re.IGNORECASE,
)

_INLINE_MEMO = re.compile(
    r"【\s*(?:메모|주석|코멘트|의견)\s*[:：]?\s*.+?\s*】|"
    r"\(\s*(?:편집\s*)?(?:메모|주석|코멘트)\s*[:：]\s*.+?\s*\)|"
    r"\[\s*(?:메모|주석|코멘트)\s*[:：]\s*.+?\s*\]",
    re.IGNORECASE,
)

# Tracked-change / insert-delete markers often surviving plain-text export
_REVISION_MARKERS = re.compile(
    r"\{\+(?P<ins1>.+?)\+\}|"
    r"\[-(?P<del1>.+?)-\]|"
    r"\{\+\+(?P<ins2>.+?)\+\+\}|"
    r"<<\s*(?:삽입|추가)\s*[:：]?\s*(?P<ins3>.+?)\s*>>|"
    r"<<\s*(?:삭제)\s*[:：]?\s*(?P<del2>.+?)\s*>>|"
    r"\{\{삭제:(?P<del3>.+?)\}\}|"
    r"\{\{추가:(?P<ins4>.+?)\}\}",
    re.IGNORECASE | re.DOTALL,
)

# Standalone typesetting / layout junk lines
_JUNK_LINE = re.compile(
    r"^\s*(?:"
    r"[-–—_=*]{3,}|"  # rules
    r"[·•●○□■◇◆]+|"
    r"(?:쪽|페이지|page)\s*[:：]?\s*\d+|"
    r"\d+\s*/\s*\d+\s*(?:쪽|page)?|"
    r"[-–—]\s*\d+\s*[-–—]|"
    r"<\s*(?:페이지|쪽|page)\s*(?:나눔|break)?\s*>|"
    r"\[?\s*(?:강제\s*)?(?:쪽\s*나눔|단\s*나눔|페이지\s*나누기)\s*\]?|"
    r"(?:머리말|바닥글|각주|미주)\s*[:：].*|"
    r"(?:FORM\s*FEED|PAGE\s*BREAK)"
    r")\s*$",
    re.IGNORECASE,
)

# Soft hyphen, zero-width, BOM, other controls (keep \n \t)
_ZW = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff\u00ad\u180e]")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# HWP / common leftover symbols (not Korean punctuation used in prose)
_NOISE_SYMBOLS = re.compile(
    r"[□■◇◆○●◎☆★▶▷►◁◀♤♠♡♥♧♣⊙◈▣◐◑♠♣♥♦※〓↔↑↓←→⇐⇒]"
)

# Collapse spaces (not newlines)
_SPACES = re.compile(r"[^\S\n]+")
# 3+ blank lines → 1 blank line between paragraphs
_MULTI_BLANK = re.compile(r"\n{3,}")
# Trailing spaces on each line
_TRAIL = re.compile(r"[ \t\u3000]+\n")
_TRAIL_END = re.compile(r"[ \t\u3000]+$")


def _apply_revision_markers(text: str) -> str:
    """Accept insertions, drop deletions from common export markups."""

    def repl(match: re.Match[str]) -> str:
        for key in ("ins1", "ins2", "ins3", "ins4"):
            val = match.group(key)
            if val is not None:
                return val
        # deletions → empty
        return ""

    return _REVISION_MARKERS.sub(repl, text)


def _strip_inline_noise(line: str) -> str:
    line = _INLINE_MEMO.sub("", line)
    line = _NOISE_SYMBOLS.sub("", line)
    # orphan empty brackets after stripping
    line = re.sub(r"【\s*】|\[\s*\]|\(\s*\)", "", line)
    return line


def _is_page_number_only(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if _JUNK_LINE.match(s):
        return True
    # pure digits / roman-ish page markers
    if re.fullmatch(r"\d{1,4}", s):
        return True
    if re.fullmatch(r"[ivxlcdmIVXLCDM]{1,8}", s):
        return True
    return False


def _normalize_paragraph_indent(line: str) -> str:
    """Keep at most one fullwidth space indent; drop random leading tabs."""
    if not line:
        return line
    # Convert leading tabs/mixed spaces to optional single fullwidth indent
    m = re.match(r"^([ \t\u3000]+)", line)
    if not m:
        return line
    lead = m.group(1)
    rest = line[len(lead) :]
    if not rest:
        return ""
    # Dialogue / quotes often start flush or with one indent — keep one fullwidth if any indent existed
    if "\u3000" in lead or len(lead) >= 2:
        return "\u3000" + rest.lstrip(" \t\u3000")
    # single ASCII space indent → drop (export noise); keep content flush
    return rest.lstrip(" \t")


def clean_proof_text(raw: str) -> str:
    """Return pure manuscript body from HWP proof extract."""
    text = str(raw or "")
    if not text.strip():
        return ""

    # Unicode normalize + kill zero-width / controls
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ").replace("\u2028", "\n").replace("\u2029", "\n\n")
    text = _ZW.sub("", text)
    text = _CTRL.sub("", text)
    text = text.replace("\f", "\n\n")  # form feed → paragraph break

    text = _apply_revision_markers(text)

    lines_out: list[str] = []
    for line in text.split("\n"):
        if _MEMO_LINE.match(line):
            continue
        if _is_page_number_only(line):
            continue
        cleaned = _strip_inline_noise(line)
        cleaned = _normalize_paragraph_indent(cleaned)
        cleaned = _SPACES.sub(" ", cleaned).rstrip()
        # Drop lines that became empty after stripping symbols only
        if not cleaned.strip():
            lines_out.append("")
            continue
        # Skip lines that are only leftover punctuation noise
        if re.fullmatch(r"[\W_·•…\.\,\-/\\|]+", cleaned.strip()):
            # keep genuine ellipsis-only? rare — drop
            if cleaned.strip() not in {"…", "...", "——", "—"}:
                lines_out.append("")
                continue
        lines_out.append(cleaned)

    text = "\n".join(lines_out)
    text = _TRAIL.sub("\n", text)
    text = _MULTI_BLANK.sub("\n\n", text)
    text = text.strip("\n")
    text = _TRAIL_END.sub("", text)

    # Final pass: no trailing spaces inside paragraphs; ensure paragraphs use \n\n
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    # Within a paragraph, single newlines → space (HWP hard breaks mid-sentence)
    # unless line looks like dialogue turn (starts with quote or fullwidth indent)
    normalized_paras: list[str] = []
    for para in paragraphs:
        raw_lines = [ln.rstrip() for ln in para.split("\n") if ln.strip() != ""]
        if not raw_lines:
            continue
        if len(raw_lines) == 1:
            normalized_paras.append(raw_lines[0])
            continue
        # Multiple lines in one block: join soft-wrapped lines, keep line if dialogue-like
        buf: list[str] = [raw_lines[0]]
        for ln in raw_lines[1:]:
            if re.match(r'^[「『"“\'\u3000]', ln) or re.match(r"^[-–—]", ln):
                buf.append(ln)
            else:
                # soft wrap: join with space unless CJK-to-CJK (no space)
                prev = buf[-1]
                if prev and ln and _is_cjk(prev[-1]) and _is_cjk(ln[0]):
                    buf[-1] = prev + ln
                else:
                    buf[-1] = (prev + " " + ln).replace("  ", " ")
        normalized_paras.append("\n".join(buf) if len(buf) > 1 else buf[0])

    return "\n\n".join(normalized_paras).strip()


def _is_cjk(ch: str) -> bool:
    if not ch:
        return False
    o = ord(ch)
    return (
        0xAC00 <= o <= 0xD7A3  # Hangul syllables
        or 0x1100 <= o <= 0x11FF
        or 0x3130 <= o <= 0x318F
        or 0x4E00 <= o <= 0x9FFF
        or 0x3400 <= o <= 0x4DBF
        or 0x3040 <= o <= 0x30FF
    )


def clean_to_dict(raw: str) -> dict[str, Any]:
    """Spec output: { clean_full_text }."""
    return {"clean_full_text": clean_proof_text(raw)}
