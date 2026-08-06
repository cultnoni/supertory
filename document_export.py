"""Export SuperTory manuscripts to downloadable document formats.

Formats open with their usual desktop apps when double-clicked after download:
txt / md / html / rtf / docx (Word) / hwpx (Hangul) / stg (SuperTORY package).
"""

from __future__ import annotations

import html as html_lib
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


EXPORT_FORMATS: dict[str, dict[str, str]] = {
    "txt": {
        "label": "텍스트",
        "ext": ".txt",
        "mime": "text/plain; charset=utf-8",
        "hint": "메모장 등에서 바로 열립니다",
    },
    "md": {
        "label": "Markdown",
        "ext": ".md",
        "mime": "text/markdown; charset=utf-8",
        "hint": "마크다운 편집기에서 열립니다",
    },
    "html": {
        "label": "HTML",
        "ext": ".html",
        "mime": "text/html; charset=utf-8",
        "hint": "브라우저에서 바로 열립니다",
    },
    "rtf": {
        "label": "RTF",
        "ext": ".rtf",
        "mime": "application/rtf",
        "hint": "Word·한글에서 열립니다",
    },
    "docx": {
        "label": "Word",
        "ext": ".docx",
        "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "hint": "Microsoft Word에서 열립니다",
    },
    "hwpx": {
        "label": "한글",
        "ext": ".hwpx",
        "mime": "application/hwp+zip",
        "hint": "한글 2014 이상(HWPX)에서 열립니다",
    },
    "stg": {
        "label": "SuperTORY 연결 파일",
        "ext": ".stg",
        "mime": "application/x-supertory-project",
        "hint": "더블클릭 시 SuperTORY에서 작품이 열립니다",
    },
}

# Hangul-openable blank package (mimetype + full header.xml + secPr).
_HWPX_SKELETON_PATH = Path(__file__).resolve().parent / "assets" / "hwpx_skeleton.hwpx"


@dataclass(frozen=True)
class ManuscriptBlock:
    kind: str  # "title" | "chapter" | "scene" | "body"
    text: str


@dataclass(frozen=True)
class ExportFile:
    filename: str
    mime: str
    data: bytes
    format_key: str


def safe_download_name(title: str, ext: str) -> str:
    name = (title or "").strip() or "작품"
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    name = re.sub(r"\s+", " ", name).strip(" .") or "작품"
    if not ext.startswith("."):
        ext = f".{ext}"
    return f"{name[:80]}{ext}"


def build_blocks(
    *,
    project_title: str,
    chapters: list[dict],
) -> list[ManuscriptBlock]:
    """chapters: [{title, scenes:[{title, content_plain}]}]"""
    blocks: list[ManuscriptBlock] = [ManuscriptBlock("title", project_title or "무제")]
    for chapter in chapters:
        chapter_title = str(chapter.get("title") or "").strip() or "챕터"
        blocks.append(ManuscriptBlock("chapter", chapter_title))
        for scene in chapter.get("scenes") or []:
            scene_title = str(scene.get("title") or "").strip()
            body = str(scene.get("content_plain") or "").strip()
            if scene_title:
                blocks.append(ManuscriptBlock("scene", scene_title))
            if body:
                blocks.append(ManuscriptBlock("body", body))
            elif not scene_title:
                continue
    return blocks


def blocks_to_plain(blocks: list[ManuscriptBlock]) -> str:
    parts: list[str] = []
    for block in blocks:
        text = sanitize_export_text(block.text)
        if block.kind == "title":
            parts.append(text)
            parts.append("")
            parts.append("=" * min(40, max(8, len(text))))
            parts.append("")
        elif block.kind == "chapter":
            parts.append("")
            parts.append(text)
            parts.append("-" * min(40, max(4, len(text))))
            parts.append("")
        elif block.kind == "scene":
            parts.append(f"【{text}】")
            parts.append("")
        else:
            parts.append(text)
            parts.append("")
    return "\n".join(parts).strip() + "\n"


def blocks_to_markdown(blocks: list[ManuscriptBlock]) -> str:
    parts: list[str] = []
    for block in blocks:
        if block.kind == "title":
            parts.append(f"# {block.text}")
            parts.append("")
        elif block.kind == "chapter":
            parts.append(f"## {block.text}")
            parts.append("")
        elif block.kind == "scene":
            parts.append(f"### {block.text}")
            parts.append("")
        else:
            parts.append(block.text)
            parts.append("")
    return "\n".join(parts).strip() + "\n"


def blocks_to_html(blocks: list[ManuscriptBlock], *, project_title: str) -> str:
    body_parts: list[str] = []
    for block in blocks:
        text = html_lib.escape(block.text).replace("\n", "<br>\n")
        if block.kind == "title":
            body_parts.append(f"<h1>{text}</h1>")
        elif block.kind == "chapter":
            body_parts.append(f"<h2>{text}</h2>")
        elif block.kind == "scene":
            body_parts.append(f"<h3>{text}</h3>")
        else:
            paragraphs = [
                f"<p>{html_lib.escape(p).replace(chr(10), '<br>')}</p>"
                for p in block.text.split("\n\n")
                if p.strip()
            ]
            body_parts.extend(paragraphs or [f"<p>{text}</p>"])
    title = html_lib.escape(project_title or "작품")
    return (
        "<!DOCTYPE html>\n"
        '<html lang="ko">\n<head>\n'
        '<meta charset="utf-8">\n'
        f"<title>{title}</title>\n"
        "<style>body{font-family:'Malgun Gothic',sans-serif;line-height:1.7;"
        "max-width:42rem;margin:2rem auto;padding:0 1rem;color:#222}"
        "h1,h2,h3{line-height:1.3}p{margin:0.75em 0}</style>\n"
        "</head>\n<body>\n"
        + "\n".join(body_parts)
        + "\n</body>\n</html>\n"
    )


def _rtf_escape(text: str) -> str:
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if ch in {"\\", "{", "}"}:
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\par\n")
        elif ch == "\r":
            continue
        elif code < 128:
            out.append(ch)
        else:
            # RTF Unicode: \uN? with signed 16-bit
            signed = code if code < 32768 else code - 65536
            out.append(f"\\u{signed}?")
    return "".join(out)


def blocks_to_rtf(blocks: list[ManuscriptBlock]) -> str:
    parts = [
        r"{\rtf1\ansi\deff0",
        r"{\fonttbl{\f0\fnil\fcharset129 Malgun Gothic;}}",
        r"\f0\fs24 ",
    ]
    for block in blocks:
        if block.kind == "title":
            parts.append(r"\pard\sa200\b\fs36 " + _rtf_escape(block.text) + r"\b0\fs24\par\par ")
        elif block.kind == "chapter":
            parts.append(r"\pard\sa160\b\fs28 " + _rtf_escape(block.text) + r"\b0\fs24\par\par ")
        elif block.kind == "scene":
            parts.append(r"\pard\sa120\b " + _rtf_escape(block.text) + r"\b0\par ")
        else:
            parts.append(r"\pard\sa120 " + _rtf_escape(block.text) + r"\par\par ")
    parts.append("}")
    return "".join(parts)


# XML 1.0 forbids most C0 control chars (except tab/LF/CR). Word rejects broken package XML.
_XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def sanitize_export_text(text: str) -> str:
    """Strip characters that break OOXML/HWPML text nodes."""
    return _XML_ILLEGAL.sub("", str(text or ""))


def escape_xml_text(text: str) -> str:
    return (
        sanitize_export_text(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _docx_paragraph_xml(text: str, *, bold: bool = False, size_half_points: int | None = None) -> str:
    """Build a single w:p as a string (reliable w: prefixes; ElementTree emits ns0:)."""
    lines = sanitize_export_text(text).split("\n") if text is not None else [""]
    if not lines:
        lines = [""]
    runs: list[str] = []
    for line_index, line in enumerate(lines):
        if line_index:
            runs.append("<w:r><w:br/></w:r>")
        rpr_bits: list[str] = []
        if bold:
            rpr_bits.append("<w:b/>")
        if size_half_points:
            rpr_bits.append(f'<w:sz w:val="{int(size_half_points)}"/>')
            rpr_bits.append(f'<w:szCs w:val="{int(size_half_points)}"/>')
        rpr = f"<w:rPr>{''.join(rpr_bits)}</w:rPr>" if rpr_bits else ""
        # Empty line still needs a t node so Word keeps the paragraph
        runs.append(
            f'<w:r>{rpr}<w:t xml:space="preserve">{escape_xml_text(line)}</w:t></w:r>'
        )
    return f"<w:p>{''.join(runs)}</w:p>"


def build_docx(blocks: list[ManuscriptBlock]) -> bytes:
    """Build a minimal but Word-openable DOCX (ZIP + proper w: document.xml)."""
    paragraphs: list[str] = []
    for block in blocks:
        if block.kind == "title":
            paragraphs.append(_docx_paragraph_xml(block.text, bold=True, size_half_points=36))
            paragraphs.append(_docx_paragraph_xml(""))
        elif block.kind == "chapter":
            paragraphs.append(_docx_paragraph_xml(block.text, bold=True, size_half_points=28))
            paragraphs.append(_docx_paragraph_xml(""))
        elif block.kind == "scene":
            paragraphs.append(_docx_paragraph_xml(block.text, bold=True, size_half_points=24))
        else:
            for para in (block.text or "").split("\n\n"):
                paragraphs.append(_docx_paragraph_xml(para.strip()))
            paragraphs.append(_docx_paragraph_xml(""))

    body_inner = "".join(paragraphs)
    # sectPr required at end for many Word builds
    body_inner += (
        "<w:sectPr>"
        '<w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>'
        "</w:sectPr>"
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<w:body>{body_inner}</w:body>"
        "</w:document>"
    ).encode("utf-8")

    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>
"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>
"""
    doc_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>
"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    core = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:dcterms="http://purl.org/dc/terms/"
  xmlns:dcmitype="http://purl.org/dc/dcmitype/"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>SuperTORY Export</dc:title>
  <dc:creator>SuperTORY</dc:creator>
  <cp:lastModifiedBy>SuperTORY</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>
</cp:coreProperties>
"""
    app_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
  xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>SuperTORY</Application>
</Properties>
"""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/_rels/document.xml.rels", doc_rels)
        archive.writestr("docProps/core.xml", core)
        archive.writestr("docProps/app.xml", app_xml)
    return buffer.getvalue()


def _hwpx_paragraph_lines(blocks: list[ManuscriptBlock]) -> list[str]:
    """Flatten manuscript blocks into Hangul paragraph lines."""
    lines: list[str] = []
    for block in blocks:
        if block.kind in {"title", "chapter", "scene"}:
            text = sanitize_export_text(block.text).strip()
            if text:
                lines.append(text)
            lines.append("")
        else:
            body = sanitize_export_text(block.text or "")
            chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", body) if chunk.strip()]
            if not chunks and body.strip():
                chunks = [body.strip()]
            for chunk in chunks:
                for line in chunk.split("\n"):
                    lines.append(line.rstrip())
            lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    if not lines:
        lines = [""]
    return lines


def _hwpx_section_xml(blocks: list[ManuscriptBlock], skeleton_section: str) -> bytes:
    """Build section0.xml using skeleton namespaces + secPr (Hangul-safe)."""
    # Hangul crashes on newlines inside section XML; keep one continuous stream.
    root_match = re.match(r"<\?xml[^?]*\?>\s*<hs:sec\b[^>]*>", skeleton_section)
    if not root_match:
        raise ValueError("한글(HWPX) 템플릿의 구역 루트를 찾지 못했습니다.")
    root_open = re.sub(r"\s+", " ", root_match.group(0)).replace("> ", ">")
    if "standalone" not in root_open:
        root_open = root_open.replace("?>", ' standalone="yes" ?>', 1)
    # Prefer standalone="no" which Hangul templates use.
    root_open = root_open.replace('standalone="yes"', 'standalone="no"').replace(
        "standalone='yes'", "standalone='no'"
    )

    sec_bundle_match = re.search(
        r"(<hp:secPr\b[\s\S]*?</hp:secPr>\s*<hp:ctrl>[\s\S]*?</hp:ctrl>)",
        skeleton_section,
    )
    if not sec_bundle_match:
        raise ValueError("한글(HWPX) 템플릿의 구역 설정(secPr)을 찾지 못했습니다.")
    sec_bundle = re.sub(r">\s+<", "><", sec_bundle_match.group(1))

    paras: list[str] = []
    for index, line in enumerate(_hwpx_paragraph_lines(blocks)):
        body = escape_xml_text(line)
        text_xml = f"<hp:t>{body}</hp:t>" if body else "<hp:t/>"
        if index == 0:
            paras.append(
                f'<hp:p id="{index}" paraPrIDRef="0" styleIDRef="0" '
                f'pageBreak="0" columnBreak="0" merged="0">'
                f'<hp:run charPrIDRef="0">{sec_bundle}</hp:run>'
                f'<hp:run charPrIDRef="0">{text_xml}</hp:run>'
                f"</hp:p>"
            )
        else:
            paras.append(
                f'<hp:p id="{index}" paraPrIDRef="0" styleIDRef="0" '
                f'pageBreak="0" columnBreak="0" merged="0">'
                f'<hp:run charPrIDRef="0">{text_xml}</hp:run>'
                f"</hp:p>"
            )

    section = f"{root_open}{''.join(paras)}</hs:sec>"
    if "\n" in section or "\r" in section:
        section = section.replace("\r", "").replace("\n", "")
    return section.encode("utf-8")


def validate_hwpx_package(data: bytes) -> list[str]:
    """Return structural problems that commonly make Hangul report a damaged file."""
    errors: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
    except zipfile.BadZipFile:
        return ["ZIP 형식이 아닙니다."]

    required = [
        "mimetype",
        "Contents/content.hpf",
        "Contents/header.xml",
        "Contents/section0.xml",
        "version.xml",
        "settings.xml",
        "META-INF/container.xml",
    ]
    for name in required:
        if name not in names:
            errors.append(f"필수 항목 없음: {name}")

    if names and names[0] != "mimetype":
        errors.append("mimetype이 ZIP 첫 항목이 아닙니다.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if "mimetype" in names:
                info = archive.getinfo("mimetype")
                if info.compress_type != zipfile.ZIP_STORED:
                    errors.append("mimetype이 압축되어 있습니다.")
                mime = archive.read("mimetype").decode("utf-8", errors="replace").strip()
                if mime != "application/hwp+zip":
                    errors.append(f"mimetype 값이 올바르지 않습니다: {mime}")
            if "Contents/header.xml" in names:
                header = archive.read("Contents/header.xml").decode("utf-8", errors="replace")
                if "<hh:charProperties" not in header and "<hh:charPr" not in header:
                    errors.append("header.xml에 글자 속성(charPr)이 없습니다.")
                if "<hh:paraProperties" not in header and "<hh:paraPr" not in header:
                    errors.append("header.xml에 문단 속성(paraPr)이 없습니다.")
            if "Contents/section0.xml" in names:
                section = archive.read("Contents/section0.xml").decode("utf-8", errors="replace")
                if "\n" in section:
                    errors.append("section0.xml에 줄바꿈이 있습니다(한글이 손상으로 처리).")
                if "<hp:secPr" not in section:
                    errors.append("section0.xml에 구역 설정(secPr)이 없습니다.")
                if section.count("xmlns:") > 20 and section.count("<hp:p ") > 0:
                    # xmlns should live on root, not each paragraph
                    body = section[section.find(">") + 1 :] if ">" in section else section
                    if 'xmlns:hp="' in body:
                        errors.append("문단마다 xmlns가 반복됩니다.")
    except zipfile.BadZipFile:
        errors.append("ZIP을 열 수 없습니다.")
    return errors


def build_hwpx(blocks: list[ManuscriptBlock]) -> bytes:
    """Build a Hangul-openable HWPX by cloning a known-good skeleton package."""
    skeleton = _HWPX_SKELETON_PATH
    if not skeleton.is_file():
        raise ValueError(
            "한글(HWPX) 내보내기 템플릿(assets/hwpx_skeleton.hwpx)을 찾지 못했습니다."
        )

    with zipfile.ZipFile(skeleton, "r") as src:
        skeleton_section = src.read("Contents/section0.xml").decode("utf-8")
        section_xml = _hwpx_section_xml(blocks, skeleton_section)
        preview = blocks_to_plain(blocks)[:4000].encode("utf-8")

        buffer = io.BytesIO()
        # Keep mimetype first + uncompressed; copy remaining skeleton members.
        ordered = ["mimetype"] + [name for name in src.namelist() if name != "mimetype"]
        with zipfile.ZipFile(buffer, "w") as dst:
            for name in ordered:
                raw = src.read(name)
                if name == "Contents/section0.xml":
                    raw = section_xml
                elif name == "Preview/PrvText.txt":
                    raw = preview
                compress = (
                    zipfile.ZIP_STORED
                    if name == "mimetype"
                    else src.getinfo(name).compress_type
                )
                # Date-less ZipInfo keeps packaging deterministic enough for Hangul.
                info = zipfile.ZipInfo(filename=name)
                info.compress_type = compress
                if name == "mimetype":
                    info.flag_bits = 0
                dst.writestr(info, raw)

        data = buffer.getvalue()

    problems = validate_hwpx_package(data)
    if problems:
        raise ValueError("한글(HWPX) 패키지 생성에 실패했습니다: " + "; ".join(problems))
    return data


def export_bytes(
    format_key: str,
    *,
    project_title: str,
    chapters: list[dict],
    stg_bytes: bytes | None = None,
) -> ExportFile:
    key = (format_key or "").strip().lower()
    meta = EXPORT_FORMATS.get(key)
    if meta is None:
        raise ValueError(
            "지원 형식: " + ", ".join(EXPORT_FORMATS.keys())
        )

    if key == "stg":
        if not stg_bytes:
            raise ValueError("연결 파일(.stg)을 준비하지 못했습니다.")
        data = stg_bytes
    else:
        blocks = build_blocks(project_title=project_title, chapters=chapters)
        if key == "txt":
            data = blocks_to_plain(blocks).encode("utf-8-sig")  # BOM for Notepad
        elif key == "md":
            data = blocks_to_markdown(blocks).encode("utf-8")
        elif key == "html":
            data = blocks_to_html(blocks, project_title=project_title).encode("utf-8")
        elif key == "rtf":
            data = blocks_to_rtf(blocks).encode("utf-8")
        elif key == "docx":
            data = build_docx(blocks)
        elif key == "hwpx":
            data = build_hwpx(blocks)
        else:
            raise ValueError("지원하지 않는 내보내기 형식입니다.")

    filename = safe_download_name(project_title, meta["ext"])
    return ExportFile(
        filename=filename,
        mime=meta["mime"],
        data=data,
        format_key=key,
    )


# Single-document formats usable for reference-material export (no .stg).
TEXT_EXPORT_FORMATS = ("txt", "md", "html", "rtf", "docx", "hwpx")


def export_plain_document(
    format_key: str,
    *,
    title: str,
    text: str,
) -> ExportFile:
    """Export a single plain-text document (e.g. edited reference material)."""
    key = (format_key or "").strip().lower()
    if key not in TEXT_EXPORT_FORMATS:
        raise ValueError(
            "지원 형식: " + ", ".join(TEXT_EXPORT_FORMATS)
        )
    doc_title = (title or "").strip() or "참고자료"
    body = str(text or "")
    # Minimal structure: title + body only (no chapter scaffolding)
    blocks: list[ManuscriptBlock] = [
        ManuscriptBlock("title", doc_title),
        ManuscriptBlock("body", body if body.strip() else "(내용 없음)"),
    ]
    meta = EXPORT_FORMATS[key]
    if key == "txt":
        data = blocks_to_plain(blocks).encode("utf-8-sig")
    elif key == "md":
        data = blocks_to_markdown(blocks).encode("utf-8")
    elif key == "html":
        data = blocks_to_html(blocks, project_title=doc_title).encode("utf-8")
    elif key == "rtf":
        data = blocks_to_rtf(blocks).encode("utf-8")
    elif key == "docx":
        data = build_docx(blocks)
    elif key == "hwpx":
        data = build_hwpx(blocks)
    else:
        raise ValueError("지원하지 않는 내보내기 형식입니다.")
    return ExportFile(
        filename=safe_download_name(doc_title, meta["ext"]),
        mime=meta["mime"],
        data=data,
        format_key=key,
    )
