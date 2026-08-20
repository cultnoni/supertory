"""Tests for document text extraction and import API."""

from __future__ import annotations

import base64
import io
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

import app
import document_import
import import_hierarchy


def make_docx(paragraphs: list[str]) -> bytes:
    document = Element("w:document", {"xmlns:w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"})
    body = SubElement(document, "w:body")
    for text in paragraphs:
        paragraph = SubElement(body, "w:p")
        run = SubElement(paragraph, "w:r")
        node = SubElement(run, "w:t")
        node.text = text
    xml = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + tostring(document, encoding="utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types></Types>")
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


def make_hwpx(paragraphs: list[str]) -> bytes:
    section = Element("hs:sec", {
        "xmlns:hs": "http://www.hancom.co.kr/hwpml/2011/section",
        "xmlns:hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    })
    for text in paragraphs:
        paragraph = SubElement(section, "hp:p")
        run = SubElement(paragraph, "hp:run")
        node = SubElement(run, "hp:t")
        node.text = text
    xml = b'<?xml version="1.0" encoding="UTF-8"?>' + tostring(section, encoding="utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/hwp+zip")
        archive.writestr("Contents/section0.xml", xml)
    return buffer.getvalue()


class DocumentImportUnitTests(unittest.TestCase):
    def test_plain_text_and_korean_encoding(self) -> None:
        extracted = document_import.extract_document("메모.txt", "첫 문장입니다.\n\n둘째 문장.".encode("cp949"))
        self.assertIn("첫 문장입니다.", extracted.text)
        self.assertEqual(extracted.format_name, "txt")

    def test_docx_extraction(self) -> None:
        data = make_docx(["비가 내렸다.", "문이 열렸다."])
        extracted = document_import.extract_document("소설.docx", data)
        self.assertEqual(extracted.text, "비가 내렸다.\n\n문이 열렸다.")

    def test_hwpx_extraction(self) -> None:
        data = make_hwpx(["한강 위로 안개가 끼었다.", "배가 천천히 떠났다."])
        extracted = document_import.extract_document("원고.hwpx", data)
        self.assertIn("한강 위로 안개가 끼었다.", extracted.text)
        self.assertIn("배가 천천히 떠났다.", extracted.text)

    def test_hwp_is_rejected_with_hint(self) -> None:
        with self.assertRaises(ValueError) as context:
            document_import.extract_document("옛파일.hwp", b"not-a-real-hwp")
        self.assertIn("HWPX", str(context.exception))

    def test_split_by_headings(self) -> None:
        text = "# 서장\n시작.\n\n# 본장\n가운데.\n\n제2장 결말\n끝."
        sections = document_import.split_into_sections(text, "headings", "전체")
        titles = [section.title for section in sections]
        self.assertIn("서장", titles)
        self.assertIn("본장", titles)
        self.assertTrue(any("결말" in title or "제2장" in title for title in titles))

    def test_purpose_normalisation(self) -> None:
        self.assertEqual(document_import.normalise_purpose("essay"), "essay")
        self.assertEqual(document_import.normalise_purpose("논문"), "paper")
        self.assertEqual(document_import.normalise_purpose("정보 전달"), "nonfiction")

    def test_legacy_blank_lines_still_split_on_one_blank(self) -> None:
        text = "첫 단락입니다.\n\n둘째 단락입니다.\n\n셋째 단락입니다."
        sections = document_import.split_into_sections(text, "blank_lines", "전체")
        self.assertEqual(len(sections), 3)
        self.assertIn("첫 단락", sections[0].content)
        self.assertNotIn("둘째", sections[0].content)

    def test_blank_threshold_keeps_single_blank_as_paragraph(self) -> None:
        text = "한 줄.\n\n또 한 줄.\n\n\n다음 씬 첫 줄."
        sections = document_import.split_into_sections(
            text,
            "blank_lines",
            "전체",
            delimiter_config={"presets": ["blank"], "blank_line_threshold": 2},
        )
        self.assertEqual(len(sections), 2)
        self.assertIn("한 줄.", sections[0].content)
        self.assertIn("또 한 줄.", sections[0].content)
        self.assertIn("\n\n", sections[0].content)
        self.assertIn("다음 씬 첫 줄.", sections[1].content)
        self.assertNotIn("다음 씬", sections[0].content)

    def test_delimiter_markers_or_blank_lines(self) -> None:
        text = "첫 씬입니다.\n\n같은 씬 문단.\n***\n둘째 씬입니다.\n\n\n셋째 씬입니다."
        sections = document_import.split_into_sections(
            text,
            "blank_lines",
            "전체",
            delimiter_config={
                "presets": ["asterisk", "blank"],
                "blank_line_threshold": 2,
            },
        )
        self.assertEqual(len(sections), 3)
        self.assertIn("첫 씬", sections[0].content)
        self.assertIn("같은 씬", sections[0].content)
        self.assertIn("둘째 씬", sections[1].content)
        self.assertIn("셋째 씬", sections[2].content)
        self.assertNotIn("***", sections[0].content)
        self.assertNotIn("***", sections[1].content)

    def test_custom_delimiter_marker(self) -> None:
        text = "앞 장면.\n///\n뒷 장면."
        sections = document_import.split_into_sections(
            text,
            "blank_lines",
            "전체",
            delimiter_config={"presets": [], "custom": "///", "blank_line_threshold": 0},
        )
        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0].content, "앞 장면.")
        self.assertEqual(sections[1].content, "뒷 장면.")

    def test_hash_and_dash_presets(self) -> None:
        text = "하나\n#\n둘\n---\n셋"
        sections = document_import.split_into_sections(
            text,
            "blank_lines",
            "전체",
            delimiter_config={"presets": ["hash", "dash"]},
        )
        self.assertEqual([section.content for section in sections], ["하나", "둘", "셋"])

    def test_numbered_order_splits_episodes_and_keeps_title_line(self) -> None:
        text = "1화 시작\n첫 본문.\n2화 이어서\n둘째 본문.\n제 3화 끝\n셋째 본문."
        sections = document_import.split_into_sections(
            text,
            "blank_lines",
            "전체",
            delimiter_config={"presets": ["numbered"], "blank_line_threshold": 0},
        )
        self.assertEqual(len(sections), 3)
        self.assertTrue(sections[0].title.startswith("1화"))
        self.assertIn("첫 본문", sections[0].content)
        self.assertNotIn("둘째 본문", sections[0].content)
        self.assertTrue(sections[1].title.startswith("2화"))
        self.assertIn("제 3화", sections[2].title)
        self.assertIn("셋째 본문", sections[2].content)

    def test_numbered_dot_titles_split_episodes(self) -> None:
        text = "1. 첫 회\n본문 A\n2. 둘째 회\n본문 B"
        sections = document_import.split_into_sections(
            text,
            "blank_lines",
            "전체",
            delimiter_config={"presets": ["numbered"]},
        )
        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0].title, "1. 첫 회")
        self.assertIn("본문 A", sections[0].content)
        self.assertEqual(sections[1].title, "2. 둘째 회")

    def test_headings_become_chapter_folders(self) -> None:
        text = "제 1장 시작\n본문 하나.\n\n제 1부 다음\n본문 둘."
        plan = document_import.build_import_plan(text, "headings", "전체")
        self.assertEqual(len(plan.chapters), 2)
        self.assertEqual(len(plan.chapters[0].scenes), 1)
        self.assertIn("제 1장", plan.chapters[0].title)
        self.assertIn("제 1부", plan.chapters[1].title)


class HierarchyImportPlanTests(unittest.TestCase):
    def test_toc_present_source_and_prologue(self) -> None:
        text = """
목차

서문 ············· 1
제1장 만남 ······· 3
제2장 이별 ······· 10

서문

비가 내렸다.

제1장 만남

두 사람이 카페에서 만났다.

제2장 이별

기차가 떠났다.
""".strip()
        plan = document_import.build_import_plan(text, "toc", "원고")
        self.assertTrue(plan.is_hierarchy)
        h = plan.hierarchy
        self.assertEqual(h.toc_source, "source")
        self.assertIn("서문", h.toc_text)
        self.assertIsNotNone(h.prologue)
        self.assertEqual(h.prologue.title, "서문")
        self.assertIn("비가 내렸다.", h.prologue.content)
        self.assertEqual(len(h.volumes), 1)
        self.assertEqual(h.volumes[0].title, "1권")
        # 장 only (no 화) → 서문(프롤로그) 폴더 + each 장 folder under 1권
        folders = h.volumes[0].folders
        self.assertEqual([f.title for f in folders], ["서문", "1장", "2장"])
        self.assertEqual([ep.title for ep in folders[1].episodes], ["제1장 만남"])
        self.assertEqual([ep.title for ep in folders[2].episodes], ["제2장 이별"])
        self.assertIn("카페", folders[1].episodes[0].content)

    def test_toc_absent_heuristic_fallback(self) -> None:
        text = """
1권

1화

가난한 청년 준호는 골목을 걸었다.

2화 첫눈

눈이 내렸다.
""".strip()
        plan = document_import.build_import_plan(text, "auto", "원고")
        self.assertTrue(plan.is_hierarchy)
        h = plan.hierarchy
        self.assertIn(h.toc_source, {"heuristic", "ai"})
        self.assertTrue(h.toc_text.startswith("목차") or "목차" in h.toc_text.split("\n")[0])
        self.assertEqual(h.volumes[0].title, "1권")
        eps = h.volumes[0].folders[0].episodes
        self.assertGreaterEqual(len(eps), 2)
        # Number-only 1화 → N회차_세어절
        self.assertTrue(eps[0].title.startswith("1회차_"), eps[0].title)
        self.assertIn("가난한", eps[0].title)
        # Subtitle kept with 화 번호
        self.assertEqual(eps[1].title, "2화 첫눈")

    def test_title_key_strips_corner_brackets(self) -> None:
        self.assertEqual(
            document_import._title_key("소개"),
            document_import._title_key("【소개】"),
        )
        self.assertEqual(
            document_import._title_key("1화.한 겨울 밤의 꿈"),
            document_import._title_key("【1화.한 겨울 밤의 꿈】"),
        )
        self.assertTrue(
            document_import._titles_match("소개", "【소개】"),
        )

    def test_bracket_markers_are_episodes_not_misc_or_prologue(self) -> None:
        text = """
1권
----

【소개】

소개 본문입니다.

【줄거리】

줄거리 본문입니다.

1부
----

【1화.한 겨울 밤의 꿈】

꿈 장면이 시작된다.

【프롤로그+한겨울밤의 꿈】

하위 원고 본문입니다.

【2화.파가몬 제국에 가다】

제국으로 떠난다.

1. 스무 살이 된 걸 축하한다.

리스트 본문은 회차가 아니다.
""".strip()
        # Keep Gemini from rewriting the assembled TOC text.
        original_ai = import_hierarchy._maybe_ai_toc
        import_hierarchy._maybe_ai_toc = lambda *a, **k: None
        try:
            h = import_hierarchy.build_hierarchy_plan(text)
        finally:
            import_hierarchy._maybe_ai_toc = original_ai
        self.assertEqual(h.volumes[0].title, "1권")
        folders = {f.title: f for f in h.volumes[0].folders}
        self.assertIn("1부", folders)
        hon = next(
            f for f in h.volumes[0].folders
            if f.transparent or f.title == import_hierarchy.TRANSPARENT_CHAPTER_TITLE
        )
        hon_titles = [ep.title for ep in hon.episodes]
        self.assertIn("소개", hon_titles)
        self.assertIn("줄거리", hon_titles)
        intro = next(ep for ep in hon.episodes if ep.title == "소개")
        plot = next(ep for ep in hon.episodes if ep.title == "줄거리")
        self.assertIn("소개 본문", intro.content)
        self.assertIn("줄거리 본문", plot.content)
        self.assertNotIn("1화 본문", intro.content)
        part = folders["1부"]
        part_titles = [ep.title for ep in part.episodes]
        self.assertIn("1화.한 겨울 밤의 꿈", part_titles)
        self.assertIn("프롤로그+한겨울밤의 꿈", part_titles)
        self.assertTrue(any("파가몬" in t for t in part_titles), part_titles)
        ep1 = next(ep for ep in part.episodes if ep.title == "1화.한 겨울 밤의 꿈")
        child = next(ep for ep in part.episodes if ep.title == "프롤로그+한겨울밤의 꿈")
        ep2 = next(ep for ep in part.episodes if "파가몬" in ep.title)
        self.assertIn("꿈 장면", ep1.content)
        self.assertNotIn("하위 원고", ep1.content)
        self.assertIn("하위 원고", child.content)
        self.assertIn("제국으로", ep2.content)
        all_titles = [ep.title for ep in h.all_episodes()]
        self.assertFalse(any("스무 살이" in t for t in all_titles), all_titles)
        self.assertIn("스무 살이", ep2.content)

    def test_export_cover_title_page_is_skipped(self) -> None:
        text = """
노예 수집하는 레이디 (개정판) 백업

====================


1권
----

【소개】

소개 본문입니다.

1부
----

【1화.한 겨울 밤의 꿈】

꿈 장면이 시작된다.
""".strip()
        original_ai = import_hierarchy._maybe_ai_toc
        import_hierarchy._maybe_ai_toc = lambda *a, **k: None
        try:
            h = import_hierarchy.build_hierarchy_plan(text)
        finally:
            import_hierarchy._maybe_ai_toc = original_ai
        folder_titles = [f.title for f in h.volumes[0].folders]
        self.assertNotIn("노예 수집하는 레이디 (개정판) 백업", folder_titles)
        self.assertFalse(any("=" in t for t in folder_titles), folder_titles)
        self.assertIn("소개", [ep.title for f in h.volumes[0].folders for ep in f.episodes])

    def test_preamble_cover_with_copy_suffix_is_not_untitled(self) -> None:
        """목차 앞 작품명+(3) 표지는 미정회차로 넣지 않음."""
        text = """
노예 수집하는 레이디 (개정판) 백업 (3)

========================

목차
1권
1부
1화.한 겨울 밤의 꿈

1권

1부

1화.한 겨울 밤의 꿈

꿈 장면이 시작된다.
""".strip()
        original_ai = import_hierarchy._maybe_ai_toc
        import_hierarchy._maybe_ai_toc = lambda *a, **k: None
        try:
            h = import_hierarchy.build_hierarchy_plan(text)
        finally:
            import_hierarchy._maybe_ai_toc = original_ai
        folder_titles = [f.title for f in h.volumes[0].folders]
        episode_titles = [ep.title for f in h.volumes[0].folders for ep in f.episodes]
        self.assertNotIn(import_hierarchy.UNTITLED_FOLDER, folder_titles)
        self.assertNotIn(import_hierarchy.UNTITLED_FOLDER, episode_titles)
        self.assertNotIn("노예 수집하는 레이디 (개정판) 백업 (3)", folder_titles)
        self.assertIn("1화.한 겨울 밤의 꿈", episode_titles)

    def test_numbered_dot_fallback_without_brackets(self) -> None:
        text = """
1권

1. 첫 회
첫 본문.

2. 둘째 회
둘째 본문.
""".strip()
        original_ai = import_hierarchy._maybe_ai_toc
        import_hierarchy._maybe_ai_toc = lambda *a, **k: None
        try:
            h = import_hierarchy.build_hierarchy_plan(text)
        finally:
            import_hierarchy._maybe_ai_toc = original_ai
        eps = h.volumes[0].folders[0].episodes
        self.assertEqual(len(eps), 2)
        self.assertIn("첫 본문", eps[0].content)
        self.assertIn("둘째 본문", eps[1].content)

    def test_volume_only_transparent_chapter(self) -> None:
        text = """
목차
1권
1화 시작
2화 끝

1권

1화 시작

해가 떴다.

2화 끝

달이 떴다.
""".strip()
        h = import_hierarchy.build_hierarchy_plan(text)
        self.assertEqual([v.title for v in h.volumes], ["1권"])
        self.assertEqual(len(h.volumes[0].folders), 1)
        self.assertTrue(h.volumes[0].folders[0].transparent)
        self.assertEqual([e.title for e in h.volumes[0].folders[0].episodes], ["1화 시작", "2화 끝"])

    def test_volume_and_parts(self) -> None:
        text = """
목차
1권
1부
1화 아침
2부
2화 저녁

1권

1부

1화 아침

해가 떴다.

2부

2화 저녁

달이 떴다.
""".strip()
        h = import_hierarchy.build_hierarchy_plan(text)
        self.assertEqual(h.volumes[0].title, "1권")
        folders = h.volumes[0].folders
        self.assertEqual([f.title for f in folders], ["1부", "2부"])
        self.assertFalse(folders[0].transparent)
        self.assertEqual(folders[0].episodes[0].title, "1화 아침")
        self.assertEqual(folders[1].episodes[0].title, "2화 저녁")

    def test_prologue_folder_episode(self) -> None:
        text = """
목차
프롤로그
1화 본편시작

프롤로그

옛날 이야기.

1화 본편시작

본문이 시작된다.
""".strip()
        h = import_hierarchy.build_hierarchy_plan(text)
        self.assertIsNotNone(h.prologue)
        self.assertEqual(h.prologue.title, "프롤로그")
        self.assertIn("옛날", h.prologue.content)
        # 프롤로그 폴더 다음 본편 회차
        titles = [f.title for f in h.volumes[0].folders]
        self.assertEqual(titles[0], "프롤로그")
        hwa = next(f for f in h.volumes[0].folders if f.title == import_hierarchy.TRANSPARENT_CHAPTER_TITLE or f.episodes and f.episodes[0].title == "1화 본편시작")
        self.assertEqual(hwa.episodes[0].title, "1화 본편시작")

    def test_unmatched_toc_creates_empty_episode(self) -> None:
        text = """
목차
1화 있는회차
2화 없는회차

1화 있는회차

본문이 있다.
""".strip()
        h = import_hierarchy.build_hierarchy_plan(text)
        eps = h.volumes[0].folders[0].episodes
        self.assertEqual(len(eps), 2)
        self.assertEqual(eps[0].title, "1화 있는회차")
        self.assertIn("본문", eps[0].content)
        self.assertEqual(eps[1].title, "2화 없는회차")
        self.assertEqual(eps[1].content, "")
        self.assertTrue(any("빈 회차" in w for w in h.warnings))

    def test_episode_auto_title_from_body_words(self) -> None:
        text = """
목차
1화
2화

1화

세 단어 제목 후보가 여기.

2화

다른 본문입니다.
""".strip()
        h = import_hierarchy.build_hierarchy_plan(text)
        eps = h.volumes[0].folders[0].episodes
        self.assertEqual(eps[0].title, "1회차_세 단어 제목")
        self.assertEqual(eps[1].title, "2회차_다른 본문입니다.")

    def test_preface_before_toc_included_in_toc_scene(self) -> None:
        """Title/subtitle lines before 「목차」 go into the 목차 scene, not dropped."""
        text = """
선 밖
부모도 전문가도 아닌 자의 요즘 육아 관찰기

목차

프롤로그: 나는 부모가 아니다
1장. 사랑에 눈이 멀면
2장. 엄마 내가 힘 더 세

프롤로그: 나는 부모가 아니다

프롤로그 본문.

1장. 사랑에 눈이 멀면

1장 본문.

2장. 엄마 내가 힘 더 세

2장 본문.
""".strip()
        h = import_hierarchy.build_hierarchy_plan(text)
        self.assertEqual(h.toc_source, "source")
        self.assertIn("선 밖", h.toc_text)
        self.assertIn("부모도 전문가도 아닌 자의 요즘 육아 관찰기", h.toc_text)
        self.assertIn("목차", h.toc_text)
        self.assertIn("프롤로그", h.toc_text)
        # Preamble must not be treated as body chapters
        self.assertNotIn("부모도 전문가도 아닌", (h.prologue.content if h.prologue else ""))
        self.assertIn("프롤로그 본문", h.prologue.content)
        folder_titles = [f.title for f in h.volumes[0].folders]
        self.assertIn("1장", folder_titles)
        self.assertIn("2장", folder_titles)
        self.assertTrue(any(t.startswith("프롤로그") for t in folder_titles))
        self.assertNotIn("선 밖", folder_titles)  # 표지 제목은 목차 씬에만
        jang1 = next(f for f in h.volumes[0].folders if f.title == "1장")
        self.assertEqual(jang1.episodes[0].title, "1장. 사랑에 눈이 멀면")

    def test_front_matter_and_misc_under_volume(self) -> None:
        """소개/머릿말·미정 글은 1권 안 순서; 권 밖은 목차만."""
        text = """
선 밖
부모도 전문가도 아닌 자의 요즘 육아 관찰기

머릿말

이 책을 쓰는 이유를 밝힌다. 머릿말 본문 UNIQUE_HEAD.

목차

프롤로그: 시작
1화 본편

프롤로그: 시작

프롤로그 글.

1화 본편

본문 회차.
""".strip()
        h = import_hierarchy.build_hierarchy_plan(text)
        self.assertEqual(h.volumes[0].title, "1권")
        titles = [f.title for f in h.volumes[0].folders]
        self.assertIn("머릿말", titles)
        self.assertTrue(any("프롤로그" in t for t in titles))
        # 머릿말이 프롤로그보다 앞 (문서 순서)
        self.assertLess(titles.index("머릿말"), next(i for i, t in enumerate(titles) if "프롤로그" in t))
        head = next(f for f in h.volumes[0].folders if f.title == "머릿말")
        self.assertIn("UNIQUE_HEAD", head.episodes[0].content)
        # 표지 제목만 있는 줄은 1권 폴더로 안 들어감 (목차 씬에만)
        self.assertNotIn("선 밖", titles)
        self.assertIn("선 밖", h.toc_text)

    def test_incomplete_body_leaves_later_parts_empty(self) -> None:
        """Full 목차 + body only through 2부 → 3~5부 folders exist but stay empty."""
        toc = """
목차

프롤로그: 나는 부모가 아니다

1부: 훈육이 아니라 학술 연구
1장. 사랑에 눈이 멀면 보이지 않는 것들이 있다
2장. 엄마 내가 힘 더 세
3장. 무한루프에 빠진 현대 육아의 모순
4장. 상처 주지 않으려다
5장. 공부할수록 길을 잃는

2부: 체벌의 역사
6장. 매가 없으면 어떻게 훈육하는가
7장. 서양 육아는 원래 다정했을까
8장. 비체벌 육아
9장. 체벌만 금지
10장. 스웨덴의 반전

3부: 공감 육아
11장. 속상했어
12장. 기질마다
13장. 부모의 눈
14장. 일곱 살

4부: 층간소음
15장. 두 정답
16장. 테러리스트
17장. 거실에서
18장. 노키즈존

5부: 매운맛 권위
19장. 구시대 체벌
20장. 냉정한 법관
21장. 아파하는가
22장. 부서지지 않는
23장. 정신과 의사
""".strip()
        # Body uses short headings (common) and only reaches 2부.
        body = """
프롤로그

프롤로그 본문 UNIQUE_PROLOGUE.

1부

1장

1장 본문 UNIQUE_CH1.

2장

2장 본문 UNIQUE_CH2.

3장

3장 본문 UNIQUE_CH3.

4장

4장 본문 UNIQUE_CH4.

5장

5장 본문 UNIQUE_CH5.

2부

6장

6장 본문 UNIQUE_CH6.

7장

7장 본문 UNIQUE_CH7.

8장

8장 본문 UNIQUE_CH8.

9장

9장 본문 UNIQUE_CH9.

10장

10장 본문 UNIQUE_CH10. 작성은 여기까지.
""".strip()
        h = import_hierarchy.build_hierarchy_plan(toc + "\n\n" + body)
        self.assertEqual(h.toc_source, "source")
        self.assertIn("5부", h.toc_text)
        self.assertIn("23장", h.toc_text)
        self.assertIsNotNone(h.prologue)
        self.assertIn("UNIQUE_PROLOGUE", h.prologue.content)
        folders = {f.title: f for f in h.volumes[0].folders}
        for need in ("1부", "2부", "3부", "4부", "5부"):
            self.assertIn(need, folders)
        self.assertTrue(any("프롤로그" in t for t in folders))
        # 부+장(leaf): 1권 / N부 / 장 원고
        self.assertIn("UNIQUE_CH1", folders["1부"].episodes[0].content)
        self.assertEqual(folders["1부"].episodes[0].title, "1장. 사랑에 눈이 멀면 보이지 않는 것들이 있다")
        self.assertIn("UNIQUE_CH5", folders["1부"].episodes[4].content)
        self.assertIn("UNIQUE_CH10", folders["2부"].episodes[4].content)
        for part in ("3부", "4부", "5부"):
            self.assertTrue(folders[part].episodes, msg=f"{part} should keep 목차 slots")
            for ep in folders[part].episodes:
                self.assertEqual(
                    ep.content.strip(),
                    "",
                    msg=f"{part}/{ep.title} must be empty when body not written",
                )
            blob = " ".join(ep.content for ep in folders[part].episodes)
            self.assertNotIn("UNIQUE_", blob)
        self.assertEqual(len(folders["1부"].episodes), 5)
        self.assertEqual(len(folders["2부"].episodes), 5)
        self.assertEqual(len(folders["3부"].episodes), 4)
        self.assertEqual(len(folders["5부"].episodes), 5)

    def test_bu_jang_hwa_nesting(self) -> None:
        """부+장+화 → 1권 / 「1부 · 1장」폴더 / 화 원고."""
        text = """
목차
1부
1장
1화 아침
2화 점심
2장
3화 저녁

1부

1장

1화 아침

아침 본문.

2화 점심

점심 본문.

2장

3화 저녁

저녁 본문.
""".strip()
        h = import_hierarchy.build_hierarchy_plan(text)
        self.assertEqual(h.volumes[0].title, "1권")
        titles = [f.title for f in h.volumes[0].folders]
        self.assertEqual(titles, ["1부 · 1장", "1부 · 2장"])
        self.assertEqual(
            [e.title for e in h.volumes[0].folders[0].episodes],
            ["1화 아침", "2화 점심"],
        )
        self.assertEqual(
            [e.title for e in h.volumes[0].folders[1].episodes],
            ["3화 저녁"],
        )

    def test_jang_folder_with_hwa_only(self) -> None:
        """장+화 (부 없음) → 1권 / 1장폴더 / 화."""
        text = """
목차
1장
1화
2화 부제

1장

1화

첫번째 본문 단어들입니다.

2화 부제

두번째.
""".strip()
        h = import_hierarchy.build_hierarchy_plan(text)
        self.assertEqual([f.title for f in h.volumes[0].folders], ["1장"])
        eps = h.volumes[0].folders[0].episodes
        self.assertTrue(eps[0].title.startswith("1회차_"), eps[0].title)
        self.assertIn("첫번째", eps[0].title)
        self.assertEqual(eps[1].title, "2화 부제")

    def test_nonfiction_multipart_toc_full_block(self) -> None:
        """Long 부/장 목차 must keep ALL groups in 목차 scene (not stop after first blank)."""
        toc = """
목차

프롤로그: 나는 부모가 아니다

1부: 훈육이 아니라 학술 연구를 해야 하는 부모들 : 선 밖에서 바라본 요즘 육아의 민낯
1장. 사랑에 눈이 멀면 보이지 않는 것들이 있다
2장. 엄마, 내가 힘 더 세
3장. 무한루프에 빠진 현대 육아의 모순
4장. 상처 주지 않으려다 주도권을 헌납한 부모들
5장. 공부할수록 길을 잃는 요즘 부모들의 패러독스

2부: 체벌의 역사와 비체벌 육아라는 미완성의 실험
6장. 매가 없으면 어떻게 훈육하는가
7장. 서양 육아는 원래 다정했을까
8장. 비체벌 육아는 고작 몇십 년 된 미완성의 실험이다
9장. 체벌만 금지하고 무기는 뺏어간 현대 육아의 모순
10장. 스웨덴의 반전

3부: 도대체 공감 육아, 감정코칭이 뭔데?
11장. 속상했어가 가르쳐준 것과 망가뜨린 것
12장. 기질마다, 상황마다 다르게
13장. 부모의 눈에 보이지 않는 것이 선 밖에서는 보일 때
14장. 일곱 살 권력자의 탄생

4부: 층간소음 앞에서도 마음을 읽어라 하십니까?
15장. 두 정답이 충돌할 때
16장. 훈육하다가 테러리스트가 된 부모들
17장. 거실에서 공공장소로
18장. 노키즈존이 진짜 거부하는 것
4부를 마치며 — 그 뒷모습이 뭉클했다

5부: 때리지 않아도 무서운 매운맛 권위를 찾아서
19장. 구시대 체벌보다 지독한 현대식 단호함
20장. 사랑을 하면서도 규칙을 세우는 냉정한 법관이 될 수 있을까
21장. 나는 지금 아이를 위해 아파하는가
22장. 사소한 거절에도 부서지지 않는 아이로 키우려면
23장. 그런데 스웨덴 안에서 한 정신과 의사가 손을 들었다
""".strip()
        # Body: each heading reappears so content can be sliced.
        body_bits = []
        for line in toc.splitlines():
            s = line.strip()
            if not s or s == "목차":
                continue
            body_bits.append(s)
            body_bits.append(f"본문 ({s[:12]})")
            body_bits.append("")
        text = toc + "\n\n" + "\n".join(body_bits)
        h = import_hierarchy.build_hierarchy_plan(text)
        self.assertEqual(h.toc_source, "source")
        # Full TOC text must include later 부/장 (bug was truncating after first blank group)
        self.assertIn("5부", h.toc_text)
        self.assertIn("23장", h.toc_text)
        self.assertIn("4부를 마치며", h.toc_text)
        self.assertIn("2부", h.toc_text)
        self.assertIsNotNone(h.prologue)
        folders = h.volumes[0].folders
        by_title = {f.title: f for f in folders}
        self.assertIn("1부", by_title)
        self.assertIn("5부", by_title)
        self.assertTrue(any("프롤로그" in t for t in by_title))
        self.assertEqual(len(by_title["1부"].episodes), 5)
        self.assertEqual(len(by_title["2부"].episodes), 5)
        self.assertEqual(len(by_title["3부"].episodes), 4)
        self.assertEqual(len(by_title["4부"].episodes), 5)  # 15–18장 + 4부를 마치며
        self.assertEqual(len(by_title["5부"].episodes), 5)
        closing = by_title["4부"].episodes[-1].title
        self.assertIn("마치며", closing)
        # 목차 + 프롤로그 1 + 본문 회차 24
        self.assertEqual(h.section_count, 1 + 1 + 24)


class DocumentImportApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
        import http.client

        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def test_import_creates_project_chapter_and_scene(self) -> None:
        content = "봄비가 창을 두드렸다.\n\n주인공은 편지를 펼쳤다."
        payload = {
            "filename": "봄비.txt",
            "content_base64": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "destination": "new_project",
            "split": "none",
            "project_title": "봄비 이야기",
            "purpose": "essay",
            "main_genre": "일상",
            "sub_genre": "",
        }
        status, result = self.request("POST", "/api/import", payload)
        self.assertEqual(status, 201)
        self.assertTrue(result["created_project"])
        self.assertEqual(result["section_count"], 1)
        self.assertEqual(result["format"], "txt")
        self.assertEqual(result["purpose"], "essay")

        status, scene = self.request("GET", f"/api/scenes/{result['scene_ids'][0]}")
        self.assertEqual(status, 200)
        self.assertIn("봄비가 창을 두드렸다.", scene["content_md"])
        self.assertEqual(scene["status"], "draft")

        status, projects = self.request("GET", "/api/projects")
        self.assertEqual(status, 200)
        self.assertEqual(projects[0]["purpose"], "essay")

    def test_import_by_toc_makes_hierarchy_parts(self) -> None:
        content = """
목차
서문
본론
결론

서문
여기는 서문.

본론
여기는 본론.

결론
여기는 결론.
""".strip()
        payload = {
            "filename": "논문초고.txt",
            "content_base64": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "destination": "new_project",
            "split": "toc",
            "project_title": "짧은 논문",
            "purpose": "paper",
            "main_genre": "학술",
            "sub_genre": "",
        }
        status, result = self.request("POST", "/api/import", payload)
        self.assertEqual(status, 201)
        self.assertTrue(result.get("hierarchy"))
        self.assertGreaterEqual(result.get("part_count", 0), 2)  # 목차 + 1권
        self.assertEqual(result["purpose"], "paper")

        status, outline = self.request("GET", f"/api/projects/{result['project_id']}/outline")
        self.assertEqual(status, 200)
        part_titles = [part["title"] for part in outline["parts"]]
        self.assertEqual(part_titles[0], "목차")
        # 권 밖은 목차뿐; 서문·본론·결론은 1권 안 폴더
        self.assertIn("1권", part_titles)
        self.assertNotIn("서문", part_titles)
        vol = next(p for p in outline["parts"] if p["title"] == "1권")
        ch_titles = [ch["title"] for ch in vol["chapters"]]
        self.assertIn("서문", ch_titles)
        self.assertIn("본론", ch_titles)
        self.assertIn("결론", ch_titles)
        # 목차 scene content
        toc_chapter = outline["parts"][0]["chapters"][0]
        self.assertEqual(toc_chapter["title"], "목차")
        toc_scene_id = toc_chapter["scenes"][0]["id"]
        status, toc_scene = self.request("GET", f"/api/scenes/{toc_scene_id}")
        self.assertEqual(status, 200)
        self.assertIn("목차", toc_scene["content_md"])

        # 서문·본론·결론은 이름 있는 폴더 (투명 본편 불필요)
        for ch in vol["chapters"]:
            if ch["title"] in {"서문", "본론", "결론"}:
                self.assertFalse(ch.get("transparent"))

    def test_import_docx_into_existing_project(self) -> None:
        status, project = self.request(
            "POST", "/api/projects", {"title": "기존 소설", "main_genre": "판타지"}
        )
        self.assertEqual(status, 201)
        data = make_docx(["첫 문단", "둘째 문단"])
        payload = {
            "filename": "초고.docx",
            "content_base64": base64.b64encode(data).decode("ascii"),
            "destination": "new_chapter",
            "split": "blank_lines",
            "chapter_title": "가져온 초고",
        }
        status, result = self.request("POST", f"/api/projects/{project['id']}/import", payload)
        self.assertEqual(status, 201)
        self.assertEqual(result["section_count"], 2)
        self.assertEqual(len(result["scene_ids"]), 2)

        status, outline = self.request("GET", f"/api/projects/{project['id']}/outline")
        self.assertEqual(status, 200)
        titles = [chapter["title"] for chapter in outline["chapters"]]
        self.assertIn("가져온 초고", titles)

    def test_import_preview_and_custom_delimiters(self) -> None:
        content = "한 줄.\n\n같은 씬.\n\n\n다음 씬.\n///\n마지막 씬."
        payload = {
            "filename": "웹소설.txt",
            "content_base64": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "split": "blank_lines",
            "delimiter_config": {
                "presets": ["blank"],
                "blank_line_threshold": 2,
                "custom": "///",
            },
        }
        status, preview = self.request("POST", "/api/import/preview", payload)
        self.assertEqual(status, 200)
        self.assertEqual(preview["section_count"], 3)
        self.assertGreaterEqual(len(preview["scenes"]), 3)
        self.assertIn("한 줄", preview["scenes"][0]["title"] + preview["scenes"][0]["preview"])

        status, project = self.request(
            "POST", "/api/projects", {"title": "구분 저장", "main_genre": "판타지"}
        )
        self.assertEqual(status, 201)
        import_payload = {
            **payload,
            "destination": "new_chapter",
            "chapter_title": "가져온 웹소설",
        }
        status, result = self.request(
            "POST", f"/api/projects/{project['id']}/import", import_payload
        )
        self.assertEqual(status, 201)
        self.assertEqual(result["section_count"], 3)
        status, projects = self.request("GET", "/api/projects")
        self.assertEqual(status, 200)
        saved = next(item for item in projects if item["id"] == project["id"])
        config = saved.get("import_delimiter_config") or {}
        self.assertIn("blank", config.get("presets") or [])
        self.assertEqual(config.get("blank_line_threshold"), 2)
        self.assertEqual(config.get("custom"), "///")


if __name__ == "__main__":
    unittest.main()
