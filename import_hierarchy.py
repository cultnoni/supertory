"""Hierarchical document import: 목차 part + 권/부/장/화 (+ transparent chapters).

Binder mapping (SuperTory: part → chapter folder → scene):

  권 → part (missing 권 → auto 「1권」)
  부 → chapter folder when present
  장 → chapter folder if 화/회 children exist; otherwise leaf manuscript under 부/1권
  화·회·숫자 → leaf manuscript

  문서에 부만 + 화:     1권 / 1부 / 1화
  문서에 장만 (leaf):   1권 / 1장폴더 / 원고(제목 또는 첫 문장)
  문서에 부+장 (leaf):  1권 / 1부 / 1장원고…
  문서에 부+장+화:      1권 / 「1부 · 1장」폴더 / 1화…
  문서에 회차만:        1권 / (투명 본편) / 1화…
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Re-use TOC helpers from document_import (lazy to avoid circular import at module load).


TRANSPARENT_CHAPTER_TITLE = "본편"
TRANSPARENT_CHAPTER_MARKER = "supertory:transparent_volume"

TOC_PART_TITLE = "목차"
TOC_CHAPTER_TITLE = "목차"
TOC_SCENE_TITLE = "목차"
UNTITLED_FOLDER = "미정회차"

VOLUME_RE = re.compile(
    r"^(?:제\s*)?(\d+)\s*권\b|^(?:Volume|Vol\.?)\s*(\d+)\b",
    re.IGNORECASE,
)
PART_RE = re.compile(r"^(?:제\s*)?(\d+)\s*부\b", re.IGNORECASE)
# 장 = mid-level (folder or leaf), not the same as 화/회
CHAPTER_JANG_RE = re.compile(r"^(?:제\s*)?(\d+)\s*장\b", re.IGNORECASE)
# 화/회/회차 or bare numbered episode lines (1. 제목 / 1 - 제목)
EPISODE_HWA_RE = re.compile(
    r"^(?:제\s*)?(\d+)\s*(?:회차|회|화)\b|"
    r"^(\d+)\s*[.、．]\s*|"
    r"^(\d+)\s*[-–—]\s*",
    re.IGNORECASE,
)
PROLOGUE_RE = re.compile(
    r"^(?:프롤로그|서문|서장|머리말|서론|prologue)\b",
    re.IGNORECASE,
)
EPILOGUE_RE = re.compile(
    r"^(?:에필로그|맺음말|후기|epilogue)\b",
    re.IGNORECASE,
)
# 목차 앞뒤·본문에 올 수 있는 자유 섹션 (권/부/장/화 아님)
MISC_SECTION_RE = re.compile(
    r"^(?:"
    r"머릿말|머리말|소개|작품\s*소개|책\s*소개|들어가며|시작하며|"
    r"감사|감사의\s*글|감사의\s*말|일러두기|주의|주의사항|"
    r"저자|지은이|옮긴이|역자|추천사|발간사|서문\s*대신|"
    r"본론|결론|부록|참고\s*문헌|참고문헌|색인|미주|주석|"
    r"마치며|나가며|맺으며"
    r")\b",
    re.IGNORECASE,
)
# Subtitle after 장/화 number marker.
EPISODE_SUBTITLE_RE = re.compile(
    r"^(?:제\s*)?\d+\s*(?:회차|회|화|장|편)\s*[.、．:\-–—]?\s*(.*)$|"
    r"^(?:제\s*)?\d+\s*[.、．:\-–—]\s*(.*)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ImportedEpisode:
    title: str
    content: str


@dataclass(frozen=True)
class ImportedFolder:
    """Chapter under a volume part. transparent=True → UI hides the folder row."""
    title: str
    episodes: tuple[ImportedEpisode, ...]
    transparent: bool = False


@dataclass(frozen=True)
class ImportedVolume:
    title: str
    folders: tuple[ImportedFolder, ...]


@dataclass(frozen=True)
class HierarchyImportPlan:
    toc_text: str
    toc_source: str  # source | heuristic | ai
    volumes: tuple[ImportedVolume, ...]
    prologue: ImportedEpisode | None = None
    epilogue: ImportedEpisode | None = None
    warnings: tuple[str, ...] = ()

    @property
    def section_count(self) -> int:
        # 목차 씬 + 권 안 모든 원고 (프롤로그·에필로그·misc 포함, 폴더에 이미 들어 있음)
        count = 1
        for volume in self.volumes:
            for folder in volume.folders:
                count += len(folder.episodes)
        return count

    @property
    def episode_count(self) -> int:
        return self.section_count - 1

    def all_episodes(self) -> list[ImportedEpisode]:
        items: list[ImportedEpisode] = []
        for volume in self.volumes:
            for folder in volume.folders:
                items.extend(folder.episodes)
        return items


def is_transparent_chapter(notes_md: str | None, title: str | None = None) -> bool:
    notes = str(notes_md or "")
    if TRANSPARENT_CHAPTER_MARKER in notes:
        return True
    if title is not None and str(title).strip() == TRANSPARENT_CHAPTER_TITLE and TRANSPARENT_CHAPTER_MARKER in notes:
        return True
    return False


def build_hierarchy_plan(text: str, default_title: str = "원고") -> HierarchyImportPlan:
    """Build 권/부/장/화 plan. TOC page preferred; else heuristic (+ optional AI TOC text)."""
    import document_import as di

    cleaned = di.normalise_whitespace(text)
    if not cleaned:
        raise ValueError("가져올 글이 비어 있습니다.")

    warnings: list[str] = []
    toc_source = "heuristic"
    toc_text = ""
    entries: list[di._TocEntry] = []
    body = cleaned

    # --- Prefer explicit 목차 block ---
    try:
        entries, body, extract_warnings, toc_block = di._extract_toc_entries_and_body(cleaned)
        warnings.extend(extract_warnings)
        toc_text = (toc_block or "").strip()
        if not toc_text and entries:
            toc_text = "목차\n\n" + "\n".join(e.title for e in entries)
        toc_source = "source" if toc_text.strip() else "heuristic"
    except ValueError:
        entries = []
        body = cleaned

    if len(entries) < 1:
        entries = _scan_structure_entries(cleaned)
        body = cleaned
        if entries:
            warnings.append("목차 페이지가 없어 본문 제목으로 구조를 잡았어요.")
        else:
            title = _episode_title_from_content(default_title, cleaned, 1)
            volumes = (
                ImportedVolume(
                    title="1권",
                    folders=(
                        ImportedFolder(
                            title=TRANSPARENT_CHAPTER_TITLE,
                            episodes=(ImportedEpisode(title=title, content=cleaned),),
                            transparent=True,
                        ),
                    ),
                ),
            )
            toc_text = _format_toc_text(None, volumes, None)
            ai_toc = _maybe_ai_toc(cleaned, toc_text)
            if ai_toc:
                toc_text = ai_toc
                toc_source = "ai"
            else:
                toc_source = "heuristic"
                warnings.append("목차·제목을 찾지 못해 1권 통째로 가져왔어요.")
            return HierarchyImportPlan(
                toc_text=toc_text,
                toc_source=toc_source,
                volumes=volumes,
                warnings=tuple(warnings),
            )

    classified = [_classify_entry(e.title) for e in entries]
    positions = di._locate_titles_in_body(body, [e.title for e in entries])
    contents: list[str] = []
    for index, (entry, pos) in enumerate(zip(entries, positions)):
        if pos is None:
            contents.append("")
            continue
        end = len(body)
        for later in positions[index + 1 :]:
            if later is not None and later > pos:
                end = later
                break
        chunk = di._strip_leading_title_line(body[pos:end], entry.title).strip()
        contents.append(chunk)

    matched = sum(1 for p in positions if p is not None)
    missing = sum(1 for p in positions if p is None)
    if missing:
        warnings.append(
            f"목차 {missing}개 항목은 본문에서 제목을 찾지 못해 빈 회차/폴더로 넣었어요."
            + (" (작성된 본문 구간만 채웁니다.)" if matched else "")
        )

    # 목차 앞 소개/머릿말 등 → 1권 안 폴더 (목차 씬 텍스트에는 그대로 포함)
    front_folders = _extract_preamble_folders(cleaned)
    # 본문 첫 매칭 제목 앞의 분류 불가 글 → 미정회차/제목 폴더
    body_prefix_folders = _extract_body_prefix_folders(body, positions)

    prologue, epilogue, volumes = _assemble_hierarchy(
        entries,
        classified,
        contents,
        front_folders=front_folders + body_prefix_folders,
    )

    if toc_source != "source" or not toc_text.strip():
        toc_text = _format_toc_text(prologue, tuple(volumes), epilogue)
        ai_toc = _maybe_ai_toc(cleaned, toc_text)
        if ai_toc:
            toc_text = ai_toc
            toc_source = "ai"
        else:
            toc_source = "heuristic"
            if "목차 페이지가 없어" not in "".join(warnings):
                warnings.append("목차를 휴리스틱으로 다시 구성했어요.")

    return HierarchyImportPlan(
        toc_text=toc_text.strip() or _format_toc_text(prologue, tuple(volumes), epilogue),
        toc_source=toc_source,
        volumes=tuple(volumes),
        prologue=prologue,
        epilogue=epilogue,
        warnings=tuple(warnings),
    )


def _folder_from_named_section(title: str, content: str) -> dict:
    """1권 아래 단일 원고 폴더 (프롤로그·misc·미정회차)."""
    name = (title or "").strip() or UNTITLED_FOLDER
    body = (content or "").strip()
    return {
        "title": name,
        "transparent": False,
        "episodes": [ImportedEpisode(title=name, content=body)],
    }


def _extract_preamble_folders(full_text: str) -> list[dict]:
    """목차 헤딩 앞: 제목만 있는 표지는 제외, 소개/머릿말 등 본문 있는 블록 → 1권 폴더."""
    import document_import as di

    lines = full_text.split("\n")
    heading_index = None
    for index, line in enumerate(lines[:200]):
        if di.TOC_HEADING.match(line.strip()):
            heading_index = index
            break
    if heading_index is None or heading_index == 0:
        return []

    preamble = lines[:heading_index]
    return _blocks_to_misc_folders(preamble, allow_title_page_skip=True)


def _extract_body_prefix_folders(body: str, positions: list[int | None]) -> list[dict]:
    """본문에서 첫 목차 제목 매칭 이전의 분류 불가 글."""
    located = [p for p in positions if p is not None]
    if not body.strip():
        return []
    if not located:
        # 전부 미매칭 → 통째로 미정회차 한 덩어리 (구조 폴더는 빈 슬롯으로 따로 생김)
        text = body.strip()
        if not text:
            return []
        first = text.split("\n", 1)[0].strip()
        if len(first) <= 80 and len(text) > len(first) + 20 and not _looks_like_body_paragraph(first):
            rest = text[len(first) :].lstrip("\n")
            return [_folder_from_named_section(first, rest)]
        return [_folder_from_named_section(UNTITLED_FOLDER, text)]

    first_pos = min(located)
    if first_pos <= 0:
        return []
    prefix = body[:first_pos].strip()
    if not prefix:
        return []
    return _blocks_to_misc_folders(prefix.split("\n"), allow_title_page_skip=False)


def _looks_like_body_paragraph(line: str) -> bool:
    s = (line or "").strip()
    if not s:
        return False
    if len(s) > 70:
        return True
    # 한국어 문장·소개 문단
    if re.search(r"[.。!?…]|다\.?$|요\.?$|음\.?$|다\s", s) and len(s) >= 15:
        return True
    if s.count(" ") >= 2 and len(s) > 22:
        return True
    return False


def _blocks_to_misc_folders(lines: list[str], *, allow_title_page_skip: bool) -> list[dict]:
    """Split lines into titled sections or 미정회차. Short title-only page can be skipped."""
    # Collect non-empty runs
    blocks: list[tuple[str | None, list[str]]] = []  # (heading or None, body lines)
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        # Heading candidate: short line, next has content or blank+content
        is_heading = (
            len(stripped) <= 80
            and not _looks_like_body_paragraph(stripped)
            and not VOLUME_RE.match(stripped)
            and not PART_RE.match(stripped)
            and not CHAPTER_JANG_RE.match(stripped)
            and not EPISODE_HWA_RE.match(stripped)
        )
        if is_heading:
            heading = stripped
            body_lines: list[str] = []
            i += 1
            while i < n:
                s2 = lines[i].strip()
                if not s2:
                    # peek ahead
                    nxt = next((lines[j].strip() for j in range(i + 1, n) if lines[j].strip()), "")
                    if not nxt:
                        i += 1
                        break
                    # blank then another short heading → end this block
                    if len(nxt) <= 80 and not _looks_like_body_paragraph(nxt):
                        i += 1
                        break
                    body_lines.append("")
                    i += 1
                    continue
                # Next short heading ends block (only after real body text)
                if (
                    len(s2) <= 80
                    and not _looks_like_body_paragraph(s2)
                    and any(x.strip() for x in body_lines)
                    and not re.match(r"^[\d]", s2)
                ):
                    break
                body_lines.append(lines[i].rstrip())
                i += 1
            blocks.append((heading, body_lines))
            continue
        # Prose without heading
        body_lines = [line.rstrip()]
        i += 1
        while i < n:
            s2 = lines[i].strip()
            if not s2:
                body_lines.append("")
                i += 1
                continue
            if (
                len(s2) <= 80
                and not _looks_like_body_paragraph(s2)
                and any(x.strip() for x in body_lines)
            ):
                break
            body_lines.append(lines[i].rstrip())
            i += 1
        blocks.append((None, body_lines))

    if allow_title_page_skip and blocks:
        # 표지만 있는 경우 (짧은 제목 1~3개, 본문 없음) → 폴더 생성 안 함 (목차 씬에만 남음)
        only_titles = all(
            h is not None and not any(x.strip() for x in body)
            for h, body in blocks
        )
        if only_titles and len(blocks) <= 4:
            return []

    folders: list[dict] = []
    for heading, body_lines in blocks:
        body = "\n".join(body_lines).strip()
        if heading and not body:
            if allow_title_page_skip:
                continue
            folders.append(_folder_from_named_section(heading, ""))
            continue
        if heading and body:
            # 짧은 부제만 붙은 책 제목(표지)은 폴더로 만들지 않음 → 목차 씬에만 유지
            body_only_short_lines = all(
                len(x.strip()) <= 80 for x in body_lines if x.strip()
            )
            if (
                allow_title_page_skip
                and body_only_short_lines
                and len(body) <= 100
                and not MISC_SECTION_RE.match(heading)
                and not PROLOGUE_RE.match(heading)
            ):
                continue
            folders.append(_folder_from_named_section(heading, body))
            continue
        if body:
            folders.append(_folder_from_named_section(UNTITLED_FOLDER, body))
    return folders


def _assemble_hierarchy(
    entries: list,
    classified: list[dict],
    contents: list[str],
    *,
    front_folders: list[dict] | None = None,
) -> tuple[ImportedEpisode | None, ImportedEpisode | None, list[ImportedVolume]]:
    """Walk classified TOC rows into 권 안 순서: 앞부속 → 프롤로그 → 부/장/화 → 에필로그.

    권 밖 최상위는 목차뿐. 프롤로그·에필로그·소개·미정회차 모두 1권 폴더로 들어간다.
    """
    has_hwa = any(
        c.get("kind") == "episode" and c.get("marker") in {"hwa", "num"}
        for c in classified
    )
    has_jang = any(c.get("kind") == "chapter" for c in classified)
    jang_is_folder = has_hwa and has_jang

    prologue: ImportedEpisode | None = None
    epilogue: ImportedEpisode | None = None

    volumes_acc: list[dict] = []
    current_vol: dict | None = None
    open_bu_folder: dict | None = None
    open_bu_title: str | None = None
    open_jang_folder: dict | None = None
    episode_counter = 0

    def ensure_volume(title: str | None = None) -> dict:
        nonlocal current_vol, open_bu_folder, open_bu_title, open_jang_folder
        if title:
            for vol in volumes_acc:
                if vol["title"] == title:
                    current_vol = vol
                    open_bu_folder = None
                    open_bu_title = None
                    open_jang_folder = None
                    return vol
            vol = {"title": title, "folders": []}
            volumes_acc.append(vol)
            current_vol = vol
            open_bu_folder = None
            open_bu_title = None
            open_jang_folder = None
            return vol
        if current_vol is None:
            vol = {"title": "1권", "folders": []}
            volumes_acc.append(vol)
            current_vol = vol
        return current_vol

    def add_folder(title: str, *, transparent: bool = False) -> dict:
        vol = ensure_volume()
        folder = {"title": title, "transparent": transparent, "episodes": []}
        vol["folders"].append(folder)
        return folder

    def add_named_section_folder(title: str, content: str) -> None:
        nonlocal open_bu_folder, open_bu_title, open_jang_folder
        ensure_volume()
        open_bu_folder = None
        open_bu_title = None
        open_jang_folder = None
        folder = _folder_from_named_section(title, content)
        ensure_volume()["folders"].append(folder)

    def target_folder_for_leaf() -> dict:
        nonlocal open_jang_folder, open_bu_folder
        if jang_is_folder and open_jang_folder is not None:
            return open_jang_folder
        if open_bu_folder is not None:
            return open_bu_folder
        vol = ensure_volume()
        if vol["folders"] and vol["folders"][-1].get("transparent"):
            return vol["folders"][-1]
        return add_folder(TRANSPARENT_CHAPTER_TITLE, transparent=True)

    # 목차 앞 부속물 먼저 (문서 순서)
    if front_folders:
        ensure_volume()
        for ff in front_folders:
            ensure_volume()["folders"].append(ff)

    for entry, kind_info, content in zip(entries, classified, contents):
        kind = kind_info["kind"]
        raw = entry.title

        if kind == "prologue":
            ep = ImportedEpisode(title=raw, content=content or "")
            prologue = ep
            add_named_section_folder(raw, content or "")
            # replace last folder episode with same ep object title already set
            continue
        if kind == "epilogue":
            ep = ImportedEpisode(title=raw, content=content or "")
            epilogue = ep
            add_named_section_folder(raw, content or "")
            continue
        if kind == "misc":
            add_named_section_folder(kind_info.get("title") or raw, content or "")
            continue
        if kind == "volume":
            ensure_volume(kind_info["title"])
            continue
        if kind == "part":
            ensure_volume()
            open_jang_folder = None
            if jang_is_folder:
                open_bu_folder = None
                open_bu_title = kind_info["title"]
            else:
                open_bu_title = None
                open_bu_folder = add_folder(kind_info["title"], transparent=False)
                if content and content.strip():
                    open_bu_folder["_lead"] = content.strip()
            continue
        if kind == "chapter":
            ensure_volume()
            if jang_is_folder:
                title = kind_info["title"]
                if open_bu_title:
                    folder_title = f"{open_bu_title} · {title}"
                else:
                    folder_title = title
                open_jang_folder = add_folder(folder_title, transparent=False)
                if content and content.strip():
                    open_jang_folder["_lead"] = content.strip()
            else:
                open_jang_folder = None
                if open_bu_folder is None:
                    jang_folder = add_folder(kind_info["title"], transparent=False)
                    episode_counter += 1
                    ep_num = kind_info.get("number") or episode_counter
                    ep_title = _resolve_episode_title(raw, content, ep_num)
                    jang_folder["episodes"].append(
                        ImportedEpisode(title=ep_title, content=(content or "").strip())
                    )
                else:
                    lead = open_bu_folder.pop("_lead", "") if open_bu_folder else ""
                    body = (content or "").strip()
                    if lead and body:
                        body = f"{lead}\n\n{body}"
                    elif lead:
                        body = lead
                    episode_counter += 1
                    ep_num = kind_info.get("number") or episode_counter
                    ep_title = _resolve_episode_title(raw, body, ep_num)
                    open_bu_folder["episodes"].append(
                        ImportedEpisode(title=ep_title, content=body)
                    )
            continue

        # episode (화/회/숫자) 또는 "N부를 마치며" 등
        ensure_volume()
        if (
            kind_info.get("marker") == "other"
            and open_bu_folder is None
            and open_jang_folder is None
            and not jang_is_folder
        ):
            # 소속 폴더 없는 자유·마치며 글 → 제목 폴더
            add_named_section_folder(raw, content or "")
            continue
        folder = target_folder_for_leaf()
        lead = folder.pop("_lead", "") if "_lead" in folder else ""
        body = (content or "").strip()
        if lead and body:
            body = f"{lead}\n\n{body}"
        elif lead:
            body = lead
        episode_counter += 1
        ep_num = kind_info.get("number") or episode_counter
        ep_title = _resolve_episode_title(raw, body, ep_num)
        folder["episodes"].append(ImportedEpisode(title=ep_title, content=body))

    if not volumes_acc:
        volumes_acc.append({"title": "1권", "folders": []})

    for vol in volumes_acc:
        for folder in vol["folders"]:
            lead = folder.pop("_lead", "")
            if lead and not folder["episodes"]:
                folder["episodes"].append(
                    ImportedEpisode(title=folder["title"], content=lead)
                )

    volumes: list[ImportedVolume] = []
    for vol in volumes_acc:
        folders = tuple(
            ImportedFolder(
                title=f["title"],
                episodes=tuple(f["episodes"]),
                transparent=bool(f.get("transparent")),
            )
            for f in vol["folders"]
            if f["episodes"] or not f.get("transparent")
        )
        if not folders:
            folders = (
                ImportedFolder(
                    title=TRANSPARENT_CHAPTER_TITLE,
                    episodes=(),
                    transparent=True,
                ),
            )
        volumes.append(ImportedVolume(title=vol["title"], folders=folders))

    return prologue, epilogue, volumes


def _classify_entry(title: str) -> dict:
    raw = title.strip()
    if PROLOGUE_RE.match(raw):
        return {"kind": "prologue", "title": raw}
    if EPILOGUE_RE.match(raw):
        return {"kind": "epilogue", "title": raw}
    if MISC_SECTION_RE.match(raw):
        return {"kind": "misc", "title": raw}
    m = VOLUME_RE.match(raw)
    if m:
        n = int(m.group(1) or m.group(2))
        return {"kind": "volume", "title": f"{n}권", "number": n}
    # "4부를 마치며" — \b가 부|를 사이에서 실패하므로 별도 처리
    m_bu_close = re.match(r"^(?:제\s*)?(\d+)\s*부를\s*\S", raw)
    if m_bu_close:
        return {"kind": "episode", "number": None, "title": raw, "marker": "other"}
    m = PART_RE.match(raw)
    if m:
        n = int(m.group(1))
        rest = raw[m.end() :]
        if rest and re.match(r"^[가-힣]", rest):
            return {"kind": "episode", "number": None, "title": raw, "marker": "other"}
        return {"kind": "part", "title": f"{n}부", "number": n, "full_title": raw}
    m_jang_close = re.match(r"^(?:제\s*)?(\d+)\s*장을\s*\S", raw)
    if m_jang_close:
        return {"kind": "episode", "number": None, "title": raw, "marker": "other"}
    m = CHAPTER_JANG_RE.match(raw)
    if m:
        n = int(m.group(1))
        rest = raw[m.end() :]
        if rest and re.match(r"^[가-힣]", rest) and not re.match(r"^[\s.、．:\-–—]", rest):
            return {"kind": "episode", "number": None, "title": raw, "marker": "other"}
        return {"kind": "chapter", "title": f"{n}장", "number": n, "raw": raw}
    m = EPISODE_HWA_RE.match(raw)
    if m:
        n = int(next(g for g in m.groups() if g))
        if re.match(r"^(?:제\s*)?\d+\s*(?:회차|회|화)\b", raw, re.I):
            marker = "hwa"
        else:
            marker = "num"
        return {"kind": "episode", "number": n, "title": raw, "marker": marker}
    # 구조 마커 없는 자유 제목 → 1권 아래 폴더명
    return {"kind": "misc", "title": raw}


def _resolve_episode_title(raw_title: str, content: str, episode_num: int) -> str:
    """원고명: 제목이 있으면 제목, 회차 표시만 있으면 본문 첫 어절(N회차_…)."""
    raw = raw_title.strip()
    m = EPISODE_SUBTITLE_RE.match(raw)
    subtitle = ""
    if m:
        subtitle = (m.group(1) or m.group(2) or "").strip()
    bare_number = bool(
        re.fullmatch(
            r"(?:제\s*)?\d+\s*(?:회차|회|화|장|편)?",
            raw,
            re.IGNORECASE,
        )
    )
    if subtitle and not bare_number:
        # "1화 첫눈" / "1장. 사랑에…" → 부제
        return subtitle[:120]
    if bare_number or re.fullmatch(r"\d+", raw):
        # 제목 없는 회차 → 본문 앞 어절
        return _episode_title_from_content(raw, content, episode_num)
    # 장/부 마커 없이 자유 제목, 또는 "4부를 마치며"
    if PART_RE.match(raw) and re.match(r"^(?:제\s*)?\d+\s*부[가-힣]", raw):
        return raw[:120]
    if CHAPTER_JANG_RE.match(raw) and re.match(r"^(?:제\s*)?\d+\s*장[가-힣]", raw):
        return raw[:120]
    if not EPISODE_HWA_RE.match(raw) and not CHAPTER_JANG_RE.match(raw) and not VOLUME_RE.match(raw) and not PART_RE.match(raw):
        return raw[:120] or _episode_title_from_content(raw, content, episode_num)
    return _episode_title_from_content(raw, content, episode_num)


def _episode_title_from_content(fallback: str, content: str, episode_num: int) -> str:
    words = _first_words(content, 3)
    if words:
        return f"{episode_num}회차_{words}"[:120]
    if fallback and not re.fullmatch(
        r"(?:제\s*)?\d+\s*(?:회차|회|화|장|편)?", fallback, re.I
    ):
        return fallback[:120]
    return f"{episode_num}회차_"


def _first_words(text: str, count: int) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return ""
    parts = cleaned.split(" ")
    return " ".join(parts[:count]).strip()


def _extract_raw_toc_block(text: str) -> str:
    """Full 목차 page text. Delegates to document_import extraction (shared logic)."""
    import document_import as di

    try:
        _entries, _body, _warnings, toc_block = di._extract_toc_entries_and_body(text)
        if toc_block and toc_block.strip():
            return toc_block.strip()
    except ValueError:
        pass
    return ""


def _scan_structure_entries(text: str) -> list:
    """Scan body lines for 권/부/장/화/프롤로그 headings when no TOC page exists."""
    import document_import as di

    pattern = re.compile(
        r"^(?:#{1,6}\s*)?("
        r"프롤로그\b.*|에필로그\b.*|서문\b.*|맺음말\b.*|"
        r"제?\s*\d+\s*권\b.*|제?\s*\d+\s*부\b.*|제?\s*\d+\s*장\b.*|"
        r"제?\s*\d+\s*(?:회차|회|화|편)\b.*|"
        r"\d+\s*[.、．]\s*.{0,80}"
        r")$",
        re.IGNORECASE,
    )
    entries = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or len(stripped) > 120:
            continue
        if pattern.match(stripped) or VOLUME_RE.match(stripped) or PART_RE.match(stripped) or CHAPTER_JANG_RE.match(stripped):
            title = di._clean_heading_title(stripped)
            title = di.TOC_PAGE_SUFFIX.sub("", title).strip(" .-·")[:120]
            if title:
                entries.append(di._TocEntry(title=title, level=0))
    return entries


def _format_toc_text(
    prologue: ImportedEpisode | None,
    volumes: tuple[ImportedVolume, ...] | list[ImportedVolume],
    epilogue: ImportedEpisode | None,
) -> str:
    lines = ["목차", ""]
    if prologue:
        lines.append(prologue.title)
    for volume in volumes:
        lines.append(volume.title)
        for folder in volume.folders:
            if not folder.transparent:
                lines.append(f"  {folder.title}")
            indent = "  " if folder.transparent else "    "
            for ep in folder.episodes:
                lines.append(f"{indent}{ep.title}")
    if epilogue:
        lines.append(epilogue.title)
    return "\n".join(lines).strip()


def _maybe_ai_toc(manuscript: str, heuristic_toc: str) -> str | None:
    """Optional Gemini rewrite. Returns None if unavailable or on any failure."""
    try:
        import gemini_client
    except Exception:
        return None
    try:
        if not gemini_client.is_configured():
            return None
        sample = manuscript[:6000]
        prompt = (
            "아래는 한국어 소설/원고입니다. 휴리스틱으로 만든 목차를 더 자연스럽게 다듬어 주세요.\n"
            "규칙: '목차'로 시작하고, 권/부/장/회차 계층이 드러나게 한 줄에 한 항목씩만 출력하세요.\n"
            "설명·머리말·코드블록 없이 목차 본문만 출력하세요.\n\n"
            f"[휴리스틱 목차]\n{heuristic_toc}\n\n"
            f"[원고 앞부분]\n{sample}\n"
        )
        result = gemini_client.generate_text(
            prompt,
            system="당신은 한국어 장편 원고의 목차 편집자입니다.",
            temperature=0.2,
            max_output_tokens=2048,
        )
        text = (result or "").strip()
        if not text or len(text) < 5:
            return None
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
        if "목차" not in text.split("\n", 1)[0] and not text.startswith("목차"):
            text = "목차\n\n" + text
        return text
    except Exception:
        return None
