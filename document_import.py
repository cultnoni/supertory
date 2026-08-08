"""Extract plain text from common writing documents using only the Python stdlib.

Supported: txt/md, docx, odt, hwpx, rtf, html, epub.
Older binary formats (hwp, doc, pdf) return a clear conversion hint.
"""

from __future__ import annotations

import html.parser
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


MAX_UPLOAD_BYTES = 15 * 1024 * 1024

SUPPORTED_EXTENSIONS = {
    ".txt",
    ".text",
    ".md",
    ".markdown",
    ".docx",
    ".odt",
    ".hwpx",
    ".rtf",
    ".html",
    ".htm",
    ".epub",
    ".csv",
}

UNSUPPORTED_HINTS = {
    ".hwp": "한글(.hwp) 파일은 한글에서 '다른 이름으로 저장' → HWPX 또는 DOCX로 바꾼 뒤 가져와 주세요.",
    ".doc": "옛 Word(.doc) 파일은 Word에서 DOCX로 저장한 뒤 가져와 주세요.",
    ".pdf": "PDF 가져오기는 아직 지원하지 않습니다. 텍스트·Word·HWPX로 저장해 주세요.",
    ".pages": "Pages 파일은 DOCX 또는 텍스트로 보낸 뒤 가져와 주세요.",
    ".hml": "한/글 HML 파일은 HWPX 또는 DOCX로 저장한 뒤 가져와 주세요.",
}


# Work purpose / book category. Keys are stored in project.purpose.
WORK_PURPOSES: dict[str, str] = {
    "general_novel": "일반소설",
    "web_novel": "웹소설",
    "fairy_tale": "동화",
    "short_story": "단편",
    "essay": "에세이",
    "translation": "번역",
    "nonfiction": "정보 전달",
    "paper": "논문",
    "autobiography": "자서전·회고록",
    "poetry": "시",
    "script": "시나리오·희곡",
    "diary": "일기·기록",
    "report": "보고서",
    "column": "칼럼·비평",
    "other": "기타",
}
DEFAULT_WORK_PURPOSE = "general_novel"

TOC_HEADING = re.compile(
    r"^(?:#{1,6}\s*)?(?:목\s*차|차\s*례|차례|목차|contents|table of contents)\s*$",
    re.IGNORECASE,
)
# Leader dots / page numbers often trail TOC lines: "제1장 …… 12"
TOC_PAGE_SUFFIX = re.compile(
    r"[\s\.·⋯…‧･ㆍ]{2,}\s*\d+\s*$|"
    r"\s+\d+\s*$"
)


@dataclass(frozen=True)
class ExtractedDocument:
    title: str
    text: str
    format_name: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImportedSection:
    title: str
    content: str


@dataclass(frozen=True)
class ImportedChapter:
    title: str
    scenes: tuple[ImportedSection, ...]


@dataclass(frozen=True)
class ImportPlan:
    """How a document becomes project outline items.

    When ``hierarchy`` is set (toc/auto), app.py inserts 목차 part + 권/부/회차.
    ``chapters`` remains for flat modes (none/headings/blank_lines) and legacy callers.
    """
    chapters: tuple[ImportedChapter, ...] = ()
    warnings: tuple[str, ...] = ()
    hierarchy: object | None = None  # import_hierarchy.HierarchyImportPlan | None

    @property
    def is_hierarchy(self) -> bool:
        return self.hierarchy is not None

    @property
    def sections(self) -> list[ImportedSection]:
        if self.hierarchy is not None:
            from import_hierarchy import HierarchyImportPlan

            if isinstance(self.hierarchy, HierarchyImportPlan):
                items = [
                    ImportedSection(title="목차", content=self.hierarchy.toc_text),
                ]
                for ep in self.hierarchy.all_episodes():
                    items.append(ImportedSection(title=ep.title, content=ep.content))
                return items
        return [scene for chapter in self.chapters for scene in chapter.scenes]

    @property
    def section_count(self) -> int:
        if self.hierarchy is not None:
            return int(getattr(self.hierarchy, "section_count", 0) or 0)
        return len(self.sections)


def normalise_purpose(value: object) -> str:
    if value is None or value == "":
        return DEFAULT_WORK_PURPOSE
    key = str(value).strip().lower()
    # Legacy single "novel" purpose → 일반소설
    if key == "novel":
        return "general_novel"
    if key in WORK_PURPOSES and key != "novel":
        return key
    # Allow Korean labels from the UI.
    for purpose_key, label in WORK_PURPOSES.items():
        if purpose_key == "novel":
            continue
        if key == label or key == label.replace("·", ""):
            return purpose_key
    aliases = {
        "소설": "general_novel",
        "일반소설": "general_novel",
        "장편": "general_novel",
        "웹소설": "web_novel",
        "웹": "web_novel",
        "동화": "fairy_tale",
        "아동": "fairy_tale",
        "어린이": "fairy_tale",
        "단편": "short_story",
        "에세이": "essay",
        "수필": "essay",
        "번역": "translation",
        "정보": "nonfiction",
        "정보전달": "nonfiction",
        "논픽션": "nonfiction",
        "설명": "nonfiction",
        "논문": "paper",
        "학술": "paper",
        "자서전": "autobiography",
        "회고록": "autobiography",
        "시": "poetry",
        "시나리오": "script",
        "희곡": "script",
        "각본": "script",
        "일기": "diary",
        "기록": "diary",
        "보고서": "report",
        "칼럼": "column",
        "비평": "column",
        "기타": "other",
    }
    if key in aliases:
        return aliases[key]
    raise ValueError(
        "작품 종류를 확인해 주세요. "
        + ", ".join(WORK_PURPOSES.values())
    )


class _HTMLTextExtractor(html.parser.HTMLParser):
    BLOCK_TAGS = {
        "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
        "section", "article", "blockquote", "pre", "hr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if lowered in self.BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if lowered in self.BLOCK_TAGS - {"br", "hr"}:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and data:
            self._chunks.append(data)

    def text(self) -> str:
        return normalise_whitespace("".join(self._chunks))


def normalise_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ").replace("\u200b", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def decode_text_bytes(data: bytes) -> str:
    """Try encodings common for Korean and Western writing files.

    UTF-16 is only used when a BOM (or many NUL bytes) is present, because
    decoding arbitrary bytes as UTF-16 almost always "succeeds" with garbage.
    """
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16")
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig")

    sample = data[:200]
    if sample.count(b"\x00") >= 4:
        for encoding in ("utf-16-le", "utf-16-be"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue

    for encoding in ("utf-8", "cp949", "euc-kr"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def title_from_filename(filename: str) -> str:
    stem = Path(filename).stem.strip() or "가져온 글"
    return stem[:120]


def extension_of(filename: str) -> str:
    return Path(filename).suffix.lower()


def extract_document(filename: str, data: bytes) -> ExtractedDocument:
    if not data:
        raise ValueError("파일이 비어 있습니다.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"파일이 너무 큽니다. {MAX_UPLOAD_BYTES // (1024 * 1024)}MB 이하만 가져올 수 있어요.")

    extension = extension_of(filename)
    if extension in UNSUPPORTED_HINTS:
        raise ValueError(UNSUPPORTED_HINTS[extension])
    if extension not in SUPPORTED_EXTENSIONS and extension:
        raise ValueError(
            f"지원하지 않는 파일 형식입니다 ({extension}). "
            "텍스트, Markdown, Word(DOCX), 한글(HWPX), ODT, RTF, HTML, EPUB을 사용해 주세요."
        )

    extractors = {
        ".txt": _extract_plain,
        ".text": _extract_plain,
        ".md": _extract_plain,
        ".markdown": _extract_plain,
        ".csv": _extract_plain,
        ".docx": _extract_docx,
        ".odt": _extract_odt,
        ".hwpx": _extract_hwpx,
        ".rtf": _extract_rtf,
        ".html": _extract_html,
        ".htm": _extract_html,
        ".epub": _extract_epub,
    }
    # No extension: treat as plain text when it looks readable.
    if not extension:
        text = normalise_whitespace(decode_text_bytes(data))
        if not text:
            raise ValueError("글 내용을 찾지 못했습니다.")
        return ExtractedDocument(title=title_from_filename(filename or "가져온 글"), text=text, format_name="text")

    text, warnings = extractors[extension](data)
    text = normalise_whitespace(text)
    if not text:
        raise ValueError("파일에서 글 내용을 찾지 못했습니다. 다른 형식으로 저장해 다시 시도해 주세요.")
    return ExtractedDocument(
        title=title_from_filename(filename),
        text=text,
        format_name=extension.lstrip("."),
        warnings=tuple(warnings),
    )


def split_into_sections(text: str, mode: str, default_title: str) -> list[ImportedSection]:
    """Backward-compatible flat section list."""
    return list(build_import_plan(text, mode, default_title).sections)


def build_import_plan(text: str, mode: str, default_title: str) -> ImportPlan:
    cleaned = normalise_whitespace(text)
    if not cleaned:
        raise ValueError("가져올 글이 비어 있습니다.")

    mode = (mode or "none").strip().lower()
    if mode in {"none", ""}:
        return ImportPlan(
            chapters=(ImportedChapter(
                title=default_title,
                scenes=(ImportedSection(title=default_title, content=cleaned),),
            ),)
        )

    if mode in {"toc", "auto"}:
        return _plan_with_hierarchy(cleaned, default_title, require_toc=(mode == "toc"))

    if mode == "headings":
        sections = _split_by_headings(cleaned, default_title)
        # Heading splits often map to chapters (제1장, # 제목).
        return _plan_from_flat_sections(sections, default_title, as_chapters=True)

    if mode == "blank_lines":
        sections = _split_by_blank_lines(cleaned, default_title)
        return _plan_from_flat_sections(sections, default_title, as_chapters=False)

    raise ValueError("글 나누기 방식이 올바르지 않습니다.")


def _plan_with_hierarchy(text: str, default_title: str, *, require_toc: bool) -> ImportPlan:
    """Hierarchical 목차/권/부/회차 plan. ``toc`` mode errors if structure is empty."""
    from import_hierarchy import HierarchyImportPlan, build_hierarchy_plan

    hierarchy = build_hierarchy_plan(text, default_title)
    assert isinstance(hierarchy, HierarchyImportPlan)
    if require_toc and hierarchy.section_count <= 1 and not any(
        folder.episodes for volume in hierarchy.volumes for folder in volume.folders
    ) and hierarchy.prologue is None and hierarchy.epilogue is None:
        raise ValueError(
            "문서에서 목차(차례)를 찾지 못했습니다. "
            "글 앞에 '목차'를 두고 항목을 나열하거나, 제목마다 나누기를 사용해 주세요."
        )
    # Compat flatten: one ImportedChapter per episode (legacy unit tests / flat consumers)
    flat_chapters: list[ImportedChapter] = []
    if hierarchy.prologue:
        flat_chapters.append(
            ImportedChapter(
                title=hierarchy.prologue.title,
                scenes=(ImportedSection(title=hierarchy.prologue.title, content=hierarchy.prologue.content),),
            )
        )
    for volume in hierarchy.volumes:
        for folder in volume.folders:
            for ep in folder.episodes:
                flat_chapters.append(
                    ImportedChapter(
                        title=ep.title,
                        scenes=(ImportedSection(title=ep.title, content=ep.content),),
                    )
                )
    if hierarchy.epilogue:
        flat_chapters.append(
            ImportedChapter(
                title=hierarchy.epilogue.title,
                scenes=(ImportedSection(title=hierarchy.epilogue.title, content=hierarchy.epilogue.content),),
            )
        )
    if not flat_chapters:
        flat_chapters.append(
            ImportedChapter(
                title=default_title,
                scenes=(ImportedSection(title=default_title, content=text),),
            )
        )
    return ImportPlan(
        chapters=tuple(flat_chapters),
        warnings=hierarchy.warnings,
        hierarchy=hierarchy,
    )


def _plan_from_flat_sections(
    sections: list[ImportedSection],
    default_title: str,
    *,
    as_chapters: bool,
) -> ImportPlan:
    if not sections:
        sections = [ImportedSection(title=default_title, content="")]
    if as_chapters:
        chapters = tuple(
            ImportedChapter(title=section.title, scenes=(section,))
            for section in sections
        )
        return ImportPlan(chapters=chapters)
    return ImportPlan(
        chapters=(ImportedChapter(title=default_title, scenes=tuple(sections)),)
    )


def _split_by_headings(text: str, default_title: str) -> list[ImportedSection]:
    # Markdown headings, "제N장", "Chapter N", numbered chapter-like lines.
    pattern = re.compile(
        r"(?m)^(#{1,6}\s+.+|"
        r"제\s*\d+\s*[장편부막]\s*.*|"
        r"Chapter\s+\d+\s*.*|"
        r"CHAPTER\s+\d+\s*.*|"
        r"\d+\.\s+.{1,80})$"
    )
    matches = list(pattern.finditer(text))
    if not matches:
        return [ImportedSection(title=default_title, content=text)]

    sections: list[ImportedSection] = []
    if matches[0].start() > 0:
        prologue = text[: matches[0].start()].strip()
        # Avoid treating a leading 목차 block as "서두" body content when possible.
        if prologue and not TOC_HEADING.search(prologue.split("\n", 1)[0]):
            sections.append(ImportedSection(title="서두", content=prologue))

    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        title = _clean_heading_title(match.group(0))
        if body or title:
            sections.append(ImportedSection(title=title or f"장면 {index + 1}", content=body))
    return sections


def _clean_heading_title(raw: str) -> str:
    title = raw.strip()
    title = re.sub(r"^#{1,6}\s+", "", title)
    return title[:120] or "새 씬"


def _split_by_blank_lines(text: str, default_title: str) -> list[ImportedSection]:
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    if len(blocks) <= 1:
        return [ImportedSection(title=default_title, content=text)]
    sections: list[ImportedSection] = []
    for index, block in enumerate(blocks, start=1):
        first_line = block.split("\n", 1)[0].strip()
        title = first_line[:40] if len(first_line) <= 40 else f"장면 {index}"
        sections.append(ImportedSection(title=title or f"장면 {index}", content=block))
    return sections


@dataclass(frozen=True)
class _TocEntry:
    title: str
    level: int  # 0 = chapter, 1+ = nested scene


def _split_by_toc(text: str, default_title: str) -> ImportPlan:
    entries, body, warnings, _toc_block = _extract_toc_entries_and_body(text)
    if len(entries) < 2:
        raise ValueError(
            "문서에서 목차(차례)를 찾지 못했습니다. "
            "글 앞에 '목차'를 두고 항목을 나열하거나, 제목마다 나누기를 사용해 주세요."
        )

    positions = _locate_titles_in_body(body, [entry.title for entry in entries])
    located = [(entry, pos) for entry, pos in zip(entries, positions) if pos is not None]
    if len(located) < 2:
        raise ValueError(
            "목차 항목은 있지만 본문에서 같은 제목을 찾지 못했습니다. "
            "목차 제목과 본문 제목이 같은지 확인해 주세요."
        )

    # Slice content for each located entry.
    chunks: list[tuple[_TocEntry, str]] = []
    for index, (entry, start) in enumerate(located):
        end = located[index + 1][1] if index + 1 < len(located) else len(body)
        # Drop the title line itself from content when it sits at the start.
        content = body[start:end]
        content = _strip_leading_title_line(content, entry.title)
        chunks.append((entry, content.strip()))

    chapters = _toc_chunks_to_chapters(chunks, default_title)
    missing = len(entries) - len(located)
    if missing > 0:
        warnings = warnings + (f"목차 {missing}개 항목은 본문에서 찾지 못해 건너뛰었어요.",)
    return ImportPlan(chapters=tuple(chapters), warnings=warnings)


def _struct_id(title: str) -> tuple[str, int | None] | None:
    """Structural heading id: ('prologue', None), ('epilogue', None), ('장', 1), ('부', 2), …"""
    raw = (title or "").strip()
    if not raw:
        return None
    if re.match(r"^(?:프롤로그|서문|서장|머리말|서론|prologue)\b", raw, re.I):
        return ("prologue", None)
    if re.match(r"^(?:에필로그|맺음말|후기|결론|epilogue)\b", raw, re.I):
        return ("epilogue", None)
    m = re.match(
        r"^(?:제\s*)?(\d+)\s*(장|부|화|회차|회|편)\b",
        raw,
        re.I,
    )
    if not m:
        return None
    unit = m.group(2)
    if unit in {"회", "회차"}:
        unit = "회"
    return (unit, int(m.group(1)))


def _is_heading_line(line: str, *, toc_title: str = "") -> bool:
    """True if line looks like a section heading, not a long body paragraph."""
    stripped = (line or "").strip()
    if not stripped:
        return False
    limit = max(len(toc_title) + 24, 100) if toc_title else 100
    return len(stripped) <= limit


def _titles_compatible(toc_title: str, body_line: str) -> bool:
    """Exact key match, or same 장/부/프롤로그 structural marker on a heading line."""
    if _titles_match(toc_title, body_line):
        return True
    if not _is_heading_line(body_line, toc_title=toc_title):
        return False
    a = _struct_id(toc_title)
    b = _struct_id(body_line)
    if a and b and a == b:
        return True
    return False


def _is_toc_structure_restart(existing_titles: list[str], new_title: str) -> bool:
    """Detect body restart: e.g. 1장 again after 23장, or 프롤로그 again after 부/장."""
    if len(existing_titles) < 4:
        return False
    new_id = _struct_id(new_title)
    if not new_id:
        return False
    prev_ids = [sid for t in existing_titles if (sid := _struct_id(t))]
    if not prev_ids:
        return False

    kind, num = new_id
    if kind == "prologue":
        had_prologue = any(p[0] == "prologue" for p in prev_ids)
        had_body_struct = any(p[0] in {"장", "부", "화", "편", "회"} for p in prev_ids)
        return had_prologue and had_body_struct

    if kind in {"장", "화", "회", "편"} and num is not None:
        prev_nums = [p[1] for p in prev_ids if p[0] == kind and p[1] is not None]
        if prev_nums:
            peak = max(prev_nums)
            # Number sequence restarts (23장 … then 1장 in body)
            if peak >= 3 and num < peak and num <= 2:
                return True
        # After several 부 headings, a lone early 장 may still be TOC — only restart
        # when we already saw this unit climb past `num`.
        return False

    if kind == "부" and num is not None:
        prev_parts = [p[1] for p in prev_ids if p[0] == "부" and p[1] is not None]
        if prev_parts and num == 1 and max(prev_parts) >= 2:
            return True
    return False


def _extract_toc_entries_and_body(
    text: str,
) -> tuple[list[_TocEntry], str, tuple[str, ...], str]:
    """Return (entries, body, warnings, toc_block_text).

    ``toc_block_text`` is the full front-matter + 목차 page used for the 목차 scene:
    everything from the **start of the document through the last 목차 entry**
    (book title, subtitle, author lines before 「목차」, then the 목차 list itself).

    Body starts after the 목차 page. Heading lines that look like a structural
    *restart* (1장 after 23장, second 프롤로그, …) are not absorbed into the TOC.
    """
    lines = text.split("\n")
    heading_index = None
    for index, line in enumerate(lines[:200]):
        if TOC_HEADING.match(line.strip()):
            heading_index = index
            break

    entries: list[_TocEntry] = []
    body_start_line = 0
    warnings: list[str] = []

    if heading_index is not None:
        # Collect TOC lines after the heading until body seems to begin.
        # Long nonfiction TOCs (5부 × 5장+) need a generous window.
        raw_entries: list[tuple[int, str, int]] = []  # level, title, line_index
        end = min(len(lines), heading_index + 1 + 250)
        cursor = heading_index + 1

        def _existing_titles() -> list[str]:
            return [t for _, t, _ in raw_entries]

        while cursor < end:
            line = lines[cursor]
            stripped = line.strip()
            if not stripped:
                # Stop only when body clearly starts — NOT on every blank.
                # Real TOCs use blank lines between 부/장 groups.
                if len(raw_entries) >= 2:
                    next_nonempty = next(
                        (lines[i].strip() for i in range(cursor + 1, end) if lines[i].strip()),
                        "",
                    )
                    if next_nonempty and raw_entries:
                        cleaned_next = _clean_toc_title(next_nonempty)
                        if _titles_compatible(raw_entries[0][1], next_nonempty) or (
                            cleaned_next
                            and _is_toc_structure_restart(_existing_titles(), cleaned_next)
                        ):
                            break
                    if next_nonempty and TOC_HEADING.match(next_nonempty):
                        cursor += 1
                        continue
                    # Double blank + next line not TOC-like → end of TOC page
                    if (
                        cursor + 1 < len(lines)
                        and not lines[cursor + 1].strip()
                        and len(raw_entries) >= 2
                    ):
                        after = next(
                            (
                                lines[i].strip()
                                for i in range(cursor + 2, min(end, cursor + 8))
                                if lines[i].strip()
                            ),
                            "",
                        )
                        if after and (
                            _titles_compatible(raw_entries[0][1], after)
                            or not _looks_like_toc_entry(after)
                            or _is_toc_structure_restart(
                                _existing_titles(), _clean_toc_title(after)
                            )
                        ):
                            cursor += 1
                            break
                cursor += 1
                continue
            if _looks_like_toc_entry(stripped):
                title = _clean_toc_title(stripped)
                if title and not TOC_HEADING.match(title):
                    # Body restart mid-stream (no blank): 1장 after 23장, etc.
                    if _is_toc_structure_restart(_existing_titles(), title):
                        break
                    level = _toc_level(line, title)
                    raw_entries.append((level, title, cursor))
                cursor += 1
                continue
            # Non-TOC-looking line after we have entries → body begins here.
            if len(raw_entries) >= 2:
                break
            cursor += 1

        if len(raw_entries) >= 2:
            entries = [_TocEntry(title=title, level=level) for level, title, _ in raw_entries]
            last_entry_line = raw_entries[-1][2]
            # 목차 씬: 문서 맨 앞(제목·부제 등) ~ 목차 마지막 항목까지.
            # 「목차」 헤딩만 쓰면 앞 표지가 통째로 빠지므로 0부터 포함.
            toc_block = "\n".join(lines[0 : last_entry_line + 1]).strip()
            body_start_line = cursor
            # Prefer starting body at the first reappearance of the first title
            # (exact or structural: 프롤로그 ↔ 프롤로그: …).
            first_title = entries[0].title
            for index in range(cursor, len(lines)):
                if _titles_compatible(first_title, lines[index].strip()):
                    body_start_line = index
                    break
            body = "\n".join(lines[body_start_line:]).strip()
            return entries, body or text, tuple(warnings), toc_block

        warnings.append("목차 제목은 있으나 항목을 충분히 읽지 못해, 본문 제목으로 다시 시도합니다.")

    # Fallback: no explicit 목차 heading — treat leading short list-like lines as TOC
    # only when those titles also appear later in the body.
    probe = _guess_leading_toc(lines)
    if probe is not None:
        return probe

    raise ValueError(
        "문서에서 목차(차례)를 찾지 못했습니다. "
        "글 앞에 '목차' 또는 '차례'를 두고 항목을 적어 주세요."
    )


def _guess_leading_toc(
    lines: list[str],
) -> tuple[list[_TocEntry], str, tuple[str, ...], str] | None:
    """If the document starts with a short list of titles that reappear later, treat as TOC."""
    candidates: list[tuple[int, str, int]] = []  # level, title, line_index
    for index, line in enumerate(lines[:80]):
        stripped = line.strip()
        if not stripped:
            if len(candidates) >= 2:
                break
            continue
        if len(stripped) > 200:
            break
        if not _looks_like_toc_entry(stripped) and not re.match(
            r"^(제\s*\d+|Chapter\s+\d+|\d+\.|\d+\-\d+|#)", stripped, re.I
        ):
            if len(candidates) >= 2:
                break
            if index > 5:
                break
            continue
        title = _clean_toc_title(stripped)
        if title:
            candidates.append((_toc_level(line, title), title, index))
        if len(candidates) >= 60:
            break

    if len(candidates) < 2:
        return None

    body_text = "\n".join(lines)
    rest = "\n".join(lines[min(len(lines), 5 + len(candidates)) :])
    found = 0
    for _, title, _ in candidates:
        if _find_title_position(rest, title) is not None:
            found += 1
    if found < max(2, len(candidates) // 2 + 1):
        return None

    entries = [_TocEntry(title=title, level=level) for level, title, _ in candidates]
    positions = _locate_titles_in_body(body_text, [e.title for e in entries])
    if sum(1 for p in positions if p is not None) < 2:
        return None
    first_pos = next(p for p in positions if p is not None)
    last_line = candidates[-1][2]
    toc_block = "\n".join(lines[0 : last_line + 1]).strip()
    if not toc_block.lower().startswith("목") and "목차" not in toc_block[:20]:
        toc_block = "목차\n\n" + toc_block
    return (
        entries,
        body_text[first_pos:].strip(),
        ("앞에 있는 목록을 목차로 인식했어요.",),
        toc_block,
    )


def _looks_like_toc_entry(line: str) -> bool:
    # Nonfiction 부 제목 can be long (subtitle after colon).
    if len(line) > 200:
        return False
    if TOC_HEADING.match(line):
        return False
    # Page leaders or numbering strongly suggest TOC.
    if TOC_PAGE_SUFFIX.search(line) and re.search(r"[\.·⋯…‧･ㆍ]{2,}\s*\d+\s*$", line):
        return True
    if re.match(
        r"^(?:"
        r"제\s*\d+\s*[장편부막절]|"
        r"Chapter\s+\d+|CHAPTER\s+\d+|"
        r"\d+\s*(?:장|부|편|막|절|화|회|회차)\b|"
        r"\d+(?:\.\d+)*\.?|"
        r"부록|서문|서장|에필로그|프롤로그|머리말|맺음말|결론|서론|본론|"
        r"\d+\s*부\s*를\s*마치"
        r")",
        line,
        re.IGNORECASE,
    ):
        return True
    # Indented short lines in TOC blocks.
    if re.match(r"^\d+(?:\.\d+)+\s+\S", line):
        return True
    # Plain title lines with leader dots
    if re.search(r"[\.·⋯…]{3,}", line):
        return True
    return False


def _clean_toc_title(line: str) -> str:
    title = line.strip()
    title = re.sub(r"^#{1,6}\s+", "", title)
    title = TOC_PAGE_SUFFIX.sub("", title)
    title = re.sub(r"[\.·⋯…‧･ㆍ\s]+$", "", title)
    title = title.strip(" .-·")
    return title[:120]


def _toc_level(raw_line: str, title: str) -> int:
    indent = len(raw_line) - len(raw_line.lstrip(" \t"))
    if indent >= 2 or raw_line.startswith("\t"):
        return 1
    # 1.2 / 1.2.3 style nested numbers
    if re.match(r"^\d+\.\d+", title):
        return 1
    # Chapter-like always top level
    if re.match(r"^(제\s*\d+\s*[장편부막]|Chapter\s+\d+|부록|서문|프롤로그|에필로그|머리말|맺음말)", title, re.I):
        return 0
    # "1. 제목" under chapters often still top-level in flat TOC; treat as top unless indented
    return 0


def _titles_match(left: str, right: str) -> bool:
    return _title_key(left) == _title_key(right) and bool(_title_key(left))


def _title_key(title: str) -> str:
    text = _clean_heading_title(title)
    text = TOC_PAGE_SUFFIX.sub("", text)
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[\.·⋯…‧･ㆍ:#]+", "", text)
    return text.casefold()


def _find_title_position(text: str, title: str, start: int = 0) -> int | None:
    """Find a body heading line for this TOC title (exact or structural 1장/1부/프롤로그)."""
    key = _title_key(title)
    if not key and not _struct_id(title):
        return None
    offset = start
    remaining = text[start:]
    for line in remaining.split("\n"):
        stripped = line.strip()
        if stripped and _titles_compatible(title, stripped):
            return offset
        offset += len(line) + 1
    return None


def _locate_titles_in_body(body: str, titles: list[str]) -> list[int | None]:
    """Locate each TOC title in body in order. Unmatched → None (empty episode, no inventing).

    Does **not** re-scan from the start for out-of-order hits — that caused unfinished
    manuscripts to glue leftover body into the wrong later 목차 items.
    """
    positions: list[int | None] = []
    cursor = 0
    for title in titles:
        pos = _find_title_position(body, title, cursor)
        positions.append(pos)
        if pos is not None:
            # Advance past this heading line so the next title cannot rematch it.
            line_end = body.find("\n", pos)
            cursor = (line_end + 1) if line_end >= 0 else (pos + 1)
    return positions


def _strip_leading_title_line(content: str, title: str) -> str:
    lines = content.split("\n")
    if not lines:
        return content
    if _titles_compatible(title, lines[0].strip()) or _titles_match(lines[0].strip(), title):
        return "\n".join(lines[1:]).lstrip("\n")
    return content


def _toc_chunks_to_chapters(
    chunks: list[tuple[_TocEntry, str]],
    default_title: str,
) -> list[ImportedChapter]:
    has_nested = any(entry.level > 0 for entry, _ in chunks)
    if not has_nested:
        # Flat TOC → each item becomes its own chapter (one scene).
        return [
            ImportedChapter(
                title=entry.title or default_title,
                scenes=(ImportedSection(title=entry.title or default_title, content=content),),
            )
            for entry, content in chunks
        ]

    chapters: list[ImportedChapter] = []
    current_title: str | None = None
    intro_content = ""
    scenes: list[ImportedSection] = []

    def flush() -> None:
        nonlocal current_title, intro_content, scenes
        if current_title is None:
            return
        built = list(scenes)
        if intro_content.strip():
            built.insert(0, ImportedSection(title=current_title, content=intro_content.strip()))
        if not built:
            built = [ImportedSection(title=current_title, content="")]
        chapters.append(ImportedChapter(title=current_title, scenes=tuple(built)))
        current_title = None
        intro_content = ""
        scenes = []

    for entry, content in chunks:
        if entry.level == 0:
            flush()
            current_title = entry.title or default_title
            intro_content = content
            scenes = []
        else:
            if current_title is None:
                current_title = default_title
            scenes.append(ImportedSection(title=entry.title, content=content))

    flush()
    return chapters or [
        ImportedChapter(
            title=default_title,
            scenes=(ImportedSection(title=default_title, content=""),),
        )
    ]


def _extract_plain(data: bytes) -> tuple[str, list[str]]:
    return decode_text_bytes(data), []


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _extract_docx(data: bytes) -> tuple[str, list[str]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise ValueError("올바른 Word(DOCX) 파일이 아닙니다.") from error
    try:
        document_xml = archive.read("word/document.xml")
    except KeyError as error:
        raise ValueError("Word 파일 안에 본문을 찾지 못했습니다.") from error
    finally:
        archive.close()

    root = ET.fromstring(document_xml)
    paragraphs: list[str] = []
    for element in root.iter():
        if _local(element.tag) != "p":
            continue
        parts: list[str] = []
        for child in element.iter():
            name = _local(child.tag)
            if name == "t" and child.text:
                parts.append(child.text)
            elif name in {"br", "cr", "tab"}:
                parts.append("\n" if name != "tab" else "\t")
        paragraph = "".join(parts).strip()
        if paragraph:
            paragraphs.append(paragraph)
    return "\n\n".join(paragraphs), []


def _extract_odt(data: bytes) -> tuple[str, list[str]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise ValueError("올바른 ODT 파일이 아닙니다.") from error
    try:
        content_xml = archive.read("content.xml")
    except KeyError as error:
        raise ValueError("ODT 파일 안에 본문을 찾지 못했습니다.") from error
    finally:
        archive.close()

    root = ET.fromstring(content_xml)
    paragraphs: list[str] = []
    for element in root.iter():
        if _local(element.tag) not in {"p", "h"}:
            continue
        text = "".join(element.itertext()).strip()
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs), []


def _extract_hwpx(data: bytes) -> tuple[str, list[str]]:
    """Hancom HWPX is a ZIP package of XML sections."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise ValueError("올바른 한글(HWPX) 파일이 아닙니다.") from error

    section_names = sorted(
        name for name in archive.namelist()
        if re.search(r"(?i)contents/section\d+\.xml$", name.replace("\\", "/"))
    )
    if not section_names:
        # Some exports nest differently; fall back to any section*.xml.
        section_names = sorted(
            name for name in archive.namelist()
            if re.search(r"(?i)section\d+\.xml$", name.replace("\\", "/"))
        )
    if not section_names:
        archive.close()
        raise ValueError("한글(HWPX) 파일 안에 본문 구역을 찾지 못했습니다.")

    paragraphs: list[str] = []
    try:
        for name in section_names:
            root = ET.fromstring(archive.read(name))
            for paragraph in _hwpx_paragraphs(root):
                if paragraph:
                    paragraphs.append(paragraph)
        if not paragraphs:
            # Last-resort: gather every text node tagged as t.
            chunks: list[str] = []
            for name in section_names:
                root = ET.fromstring(archive.read(name))
                for element in root.iter():
                    if _local(element.tag) == "t" and element.text:
                        chunks.append(element.text)
            if chunks:
                return "\n".join(chunks), ["일부 서식은 반영되지 않았을 수 있어요."]
    finally:
        archive.close()

    if not paragraphs:
        raise ValueError("한글(HWPX) 본문을 읽지 못했습니다.")
    return "\n\n".join(paragraphs), []


def _hwpx_paragraphs(root: ET.Element) -> list[str]:
    results: list[str] = []
    for element in root.iter():
        if _local(element.tag) != "p":
            continue
        # Skip nested paragraphs if any; we only want direct-ish paragraph blocks.
        parts: list[str] = []
        for child in element.iter():
            if child is element:
                continue
            name = _local(child.tag)
            if name == "t" and child.text:
                parts.append(child.text)
            elif name in {"lineBreak", "line-break", "br"}:
                parts.append("\n")
            elif name == "tab":
                parts.append("\t")
        text = "".join(parts).strip()
        if text:
            results.append(text)
    return results


def _extract_html(data: bytes) -> tuple[str, list[str]]:
    parser = _HTMLTextExtractor()
    parser.feed(decode_text_bytes(data))
    parser.close()
    return parser.text(), []


def _extract_epub(data: bytes) -> tuple[str, list[str]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise ValueError("올바른 EPUB 파일이 아닙니다.") from error

    try:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = None
        for element in container.iter():
            if _local(element.tag) == "rootfile":
                rootfile = element.attrib.get("full-path")
                break
        if not rootfile:
            raise ValueError("EPUB 목차 정보를 찾지 못했습니다.")

        package = ET.fromstring(archive.read(rootfile))
        package_dir = str(Path(rootfile).parent)
        if package_dir == ".":
            package_dir = ""

        manifest: dict[str, str] = {}
        for element in package.iter():
            if _local(element.tag) == "item":
                item_id = element.attrib.get("id")
                href = element.attrib.get("href")
                if item_id and href:
                    manifest[item_id] = href

        spine_hrefs: list[str] = []
        for element in package.iter():
            if _local(element.tag) == "itemref":
                idref = element.attrib.get("idref")
                if idref and idref in manifest:
                    spine_hrefs.append(manifest[idref])

        if not spine_hrefs:
            # Fall back to all xhtml/html files.
            spine_hrefs = [
                name for name in archive.namelist()
                if name.lower().endswith((".xhtml", ".html", ".htm"))
            ]

        chapters: list[str] = []
        for href in spine_hrefs:
            path = href if not package_dir else f"{package_dir}/{href}".replace("\\", "/")
            path = path.lstrip("./")
            # Normalise zip path
            while "//" in path:
                path = path.replace("//", "/")
            try:
                raw = archive.read(path)
            except KeyError:
                # Try basename match
                basename = Path(href).name
                match = next((name for name in archive.namelist() if name.endswith(basename)), None)
                if not match:
                    continue
                raw = archive.read(match)
            chapter_text, _ = _extract_html(raw)
            if chapter_text:
                chapters.append(chapter_text)
    finally:
        archive.close()

    if not chapters:
        raise ValueError("EPUB에서 글을 찾지 못했습니다.")
    return "\n\n".join(chapters), []


def _extract_rtf(data: bytes) -> tuple[str, list[str]]:
    """A deliberately small RTF stripper for manuscript drafts."""
    source = decode_text_bytes(data)
    if not source.lstrip().startswith("{\\rtf"):
        # Some editors omit the marker after encoding mishaps; still try.
        pass

    # Convert hex escapes \'hh before stripping controls.
    def replace_hex(match: re.Match[str]) -> str:
        try:
            return bytes([int(match.group(1), 16)]).decode("cp1252", errors="ignore")
        except ValueError:
            return ""

    source = re.sub(r"\\'([0-9a-fA-F]{2})", replace_hex, source)
    # Unicode escapes: \uN?
    def replace_unicode(match: re.Match[str]) -> str:
        try:
            code = int(match.group(1))
            if code < 0:
                code += 65536
            return chr(code)
        except ValueError:
            return ""

    source = re.sub(r"\\u(-?\d+)\??", replace_unicode, source)
    source = source.replace("\\par", "\n").replace("\\line", "\n").replace("\\tab", "\t")
    source = re.sub(r"\\[a-zA-Z]+-?\d*[ ]?", "", source)
    source = source.replace("{", "").replace("}", "")
    source = source.replace("\\", "")
    return source, ["RTF 서식은 단순 글자로만 가져옵니다."]
