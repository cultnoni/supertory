"""첨삭 피드백 파이프라인: 문단·규칙·후처리·FakeClaude 전체 흐름."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import feedback_store
from feedback_pipeline.checks import (
    apply_card_priorities,
    check_v3_unknown_proper_nouns,
    check_v4_outside_sentence_copy,
    check_v6_length,
    check_v8_missing_terms,
    check_v9_repeat_and_pronoun,
    classify_v8_warning,
    missing_terms_only_in_deleted_sentences,
)
from feedback_pipeline.claude_client import ClaudeError, FakeClaude, UiFakeClaude
from feedback_pipeline.cli import main as cli_main
from feedback_pipeline.context import explanation_lens_for_project
from feedback_pipeline.dup_blocks import find_dup_blocks
from feedback_pipeline.paragraphs import (
    paragraphs_from_html,
    paragraphs_from_text,
    to_paragraphs,
)
from feedback_pipeline.report_post import apply_p1_sentence_span, filter_p2_items
from feedback_pipeline.runner import run_feedback

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "feedback_paragraphs.json"

MANUSCRIPT = (
    "정원이 손을 내밀었다. 승혜는 그 손을 보았다.\n\n"
    "승혜는 그 말을 되뇌었다. 정원의 눈빛이 흔들렸다.\n\n"
    "세 번째 문단이다. 정원의 정원이 아니라 그냥 정원이다. 그녀는 고개를 돌렸다.\n\n"
    "네 번째 문단에서 비가 내렸다. 창문이 흔들렸다.\n\n"
    "다섯 번째 문단. 아침이 밝았다."
)

RANGE_P1 = {
    "start_para": 1,
    "end_para": 1,
    "start_quote": "정원이 손을",
    "end_quote": "손을 보았다",
}
RANGE_P2 = {
    "start_para": 2,
    "end_para": 2,
    "start_quote": "승혜는 그 말을",
    "end_quote": "눈빛이 흔들렸다",
}
RANGE_P3 = {
    "start_para": 3,
    "end_para": 3,
    "start_quote": "세 번째 문단이다",
    "end_quote": "고개를 돌렸다",
}
RANGE_P4 = {
    "start_para": 4,
    "end_para": 4,
    "start_quote": "네 번째 문단에서",
    "end_quote": "창문이 흔들렸다",
}
RANGE_MISSING = {
    "start_para": 1,
    "end_para": 1,
    "start_quote": "없는인용시작",
    "end_quote": "없는인용끝",
}


def _card_payload(item_id: str) -> dict:
    return {
        "target_id": item_id,
        "kind": "style",
        "edit_plan": "유지할 문장을 남기고 지적 문장만 줄입니다.",
        "reason": "시험용 이유입니다. 표현을 조금 줄일 수 있습니다.",
        "suggestion": "정원이 손을 내밀었다.",
        "added_facts": [],
        "removed_facts": [],
        "confidence": "medium",
    }


class PipelineFake:
    """정합성·리포트·카드 프롬프트를 구분해 고정 응답을 준다."""

    def __init__(
        self,
        *,
        consistency: dict | None = None,
        report: dict | None = None,
        fail_report: bool = False,
        fail_card_ids: set[str] | None = None,
        usage: dict | None = None,
        card_usage: dict | None = None,
    ) -> None:
        self.prompts: list[str] = []
        self.consistency = consistency or {"facts": [], "issues": []}
        self.report = report or {
            "summary": "고정 리포트",
            "rules": [],
            "scores": [],
            "strengths": [],
            "weaknesses": [],
            "consistency": [],
        }
        self.fail_report = fail_report
        self.fail_card_ids = set(fail_card_ids or [])
        self.usage = usage or {"input_tokens": 10, "output_tokens": 20}
        self.card_usage = card_usage or self.usage
        self._lock = threading.Lock()

    def generate(self, prompt: str, **_kwargs):
        with self._lock:
            self.prompts.append(prompt)
        if "인물·설정·시간·수치에 관한 사실" in prompt:
            return FakeClaude()._as_result(
                {"parsed": self.consistency, "usage": dict(self.usage)}
            )
        if "피드백 리포트를 JSON" in prompt:
            if self.fail_report:
                raise ClaudeError("리포트 실패", code="unknown")
            return FakeClaude()._as_result(
                {"parsed": self.report, "usage": dict(self.usage)}
            )
        if "첨삭 카드 1장" in prompt:
            item_id = ""
            for line in prompt.splitlines():
                if line.startswith("id:"):
                    item_id = line.split("/", 1)[0].replace("id:", "").strip()
                    break
            if item_id in self.fail_card_ids:
                return FakeClaude()._as_result(
                    {"parsed": None, "text": "NOT JSON", "usage": dict(self.card_usage)}
                )
            return FakeClaude()._as_result(
                {"parsed": _card_payload(item_id or "X"), "usage": dict(self.card_usage)}
            )
        raise ClaudeError("알 수 없는 프롬프트", code="unknown")


def _default_consistency() -> dict:
    return {
        "facts": [],
        "issues": [
            {
                "id": "C1",
                "title": "이름 충돌",
                "body": "정원과 다른 호칭이 겹칩니다.",
                "certainty": "sure",
                "impact": 5,
                "perspectives": ["editor"],
                "range": RANGE_P1,
            },
            {
                "id": "C2",
                "title": "나이 표기 모호",
                "body": "나이를 단정하기 어렵습니다.",
                "certainty": "maybe",
                "impact": 3,
                "perspectives": ["editor"],
                "range": RANGE_P2,
            },
            {
                "id": "C3",
                "title": "인용 실패 정합성",
                "body": "이 인용은 원고에 없습니다.",
                "certainty": "sure",
                "impact": 5,
                "perspectives": ["editor"],
                "range": RANGE_MISSING,
            },
        ],
    }


def _default_report() -> dict:
    return {
        "summary": "시험용 리포트 요약입니다.",
        "rules": [],
        "scores": [{"item": "몰입", "score": 4, "comment": "무난합니다."}],
        "strengths": [],
        "weaknesses": [
            {
                "id": "W1",
                "title": "해설이 깁니다",
                "body": "감정을 직접 규정한 문장입니다.",
                "type": "explain_less",
                "fixable": "sentence",
                "impact": 4,
                "certainty": "sure",
                "perspectives": ["editor"],
                "range": RANGE_P3,
            },
            {
                "id": "W2",
                "title": "장면 구조",
                "body": "정보 배치를 장면 단위로 다시 짜는 편이 좋습니다.",
                "type": "info_placement",
                "fixable": "structure",
                "impact": 3,
                "certainty": "sure",
                "perspectives": ["editor"],
                "range": RANGE_P4,
            },
            {
                "id": "W3",
                "title": "취향 문제",
                "body": "고칠 필요는 없습니다.",
                "type": "other",
                "fixable": "none",
                "impact": 2,
                "certainty": "sure",
                "perspectives": ["critic"],
                "range": RANGE_P4,
            },
            {
                "id": "W4",
                "title": "인용 실패 약점",
                "body": "카드가 되면 안 되는 약점입니다.",
                "type": "explain_less",
                "fixable": "sentence",
                "impact": 4,
                "certainty": "sure",
                "perspectives": ["editor"],
                "range": RANGE_MISSING,
            },
        ],
        "consistency": [],
    }


class ParagraphFixtureTests(unittest.TestCase):
    def test_fixtures_match_parsers(self) -> None:
        cases = json.loads(FIXTURES.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(cases), 5)
        for case in cases:
            with self.subTest(case["id"]):
                fn = paragraphs_from_html if case["fn"] == "html" else paragraphs_from_text
                self.assertEqual(fn(case["input"]), case["expected"])


class DupBlockTests(unittest.TestCase):
    def test_duplicated_manuscript_one_block(self) -> None:
        chunk = (
            "정원이 창가에 서서 빗소리를 오래 들었다.\n\n"
            "승혜는 편지를 접어 서랍 맨 아래 넣었다.\n\n"
            "다음 날 아침 시장은 이미 붐비고 있었다."
        )
        paras = paragraphs_from_text(chunk + "\n\n" + chunk)
        blocks = find_dup_blocks(to_paragraphs(paras))
        self.assertEqual(len(blocks), 1)

    def test_normal_manuscript_zero_blocks(self) -> None:
        paras = paragraphs_from_text(MANUSCRIPT)
        self.assertEqual(find_dup_blocks(to_paragraphs(paras)), [])


class CheckRuleTests(unittest.TestCase):
    def test_v3_unknown_name_fails(self) -> None:
        result = check_v3_unknown_proper_nouns(
            suggestion="김철수씨가 손을 내밀었다.",
            original_text="정원이 손을 내밀었다.",
            project={"characters": [{"name": "정원"}]},
        )
        self.assertFalse(result["ok"])
        self.assertIn("김철수씨", result["unknown"])

    def test_v4_copies_neighbor_sentence(self) -> None:
        paras = to_paragraphs(
            paragraphs_from_text(
                "첫 문단입니다. 이웃 문장입니다.\n\n"
                "둘째 문단 대상입니다.\n\n"
                "셋째 문단입니다. 복사될 긴 문장입니다."
            )
        )
        result = check_v4_outside_sentence_copy(
            suggestion="복사될 긴 문장입니다.",
            paragraphs=paras,
            start_para=2,
            end_para=2,
            start_quote="둘째 문단",
            end_quote="대상입니다",
        )
        self.assertFalse(result["ok"])
        self.assertTrue(result["copied"])

    def test_v6_too_long(self) -> None:
        result = check_v6_length(
            suggestion="가" * 80,
            original_text="짧은 원문이다.",
            kind="style",
        )
        self.assertFalse(result["ok"])

    def test_v8_missing_name(self) -> None:
        result = check_v8_missing_terms(
            suggestion="그가 손을 내밀었다.",
            original_text="정원이 손을 내밀었다.",
            project={"characters": [{"name": "정원"}]},
        )
        self.assertTrue(result["warn"])
        self.assertIn("정원", result["missing"])

    def test_v8_sentence_deletion_is_info(self) -> None:
        original = "이오나가 고개를 돌렸다. 로이드는 그 옆에 서 있었다. 바람이 불었다."
        suggestion = "바람이 불었다."
        self.assertTrue(
            missing_terms_only_in_deleted_sentences(
                original, suggestion, ["이오나", "로이드"]
            )
        )
        result = check_v8_missing_terms(
            suggestion=suggestion,
            original_text=original,
            project={"characters": [{"name": "이오나"}, {"name": "로이드"}]},
        )
        warn = classify_v8_warning(
            original_text=original,
            suggestion=suggestion,
            result=result,
        )
        self.assertEqual(warn["code"], "names_removed_by_deletion")
        self.assertEqual(warn["severity"], "info")
        self.assertIn("이오나", warn["message"])
        self.assertIn("로이드", warn["message"])

    def test_v8_rewrite_keeps_warning(self) -> None:
        original = "이오나가 고개를 돌렸다. 바람이 불었다."
        suggestion = "그녀가 고개를 돌렸다. 바람이 불었다."
        self.assertFalse(
            missing_terms_only_in_deleted_sentences(original, suggestion, ["이오나"])
        )
        result = check_v8_missing_terms(
            suggestion=suggestion,
            original_text=original,
            project={"characters": [{"name": "이오나"}]},
        )
        warn = classify_v8_warning(
            original_text=original,
            suggestion=suggestion,
            result=result,
        )
        self.assertEqual(warn["code"], "V8")
        self.assertEqual(warn["severity"], "warn")

    def test_v9_repeat_and_pronoun(self) -> None:
        repeat = check_v9_repeat_and_pronoun(
            suggestion="정원의 정원이 보였다.",
            original_text="정원이 보였다.",
        )
        self.assertTrue(repeat["warn"])
        self.assertTrue(repeat["repeats"])
        pronoun = check_v9_repeat_and_pronoun(
            suggestion="그가 고개를 돌렸다.",
            original_text="그녀가 고개를 돌렸다.",
        )
        self.assertEqual(pronoun["pronoun_shift"], "그녀→그")


class ReportPostTests(unittest.TestCase):
    def test_p1_sentence_over_five_paragraphs_becomes_structure(self) -> None:
        report = {
            "weaknesses": [
                {
                    "id": "W6",
                    "title": "긴 구간",
                    "body": "여섯 문단",
                    "fixable": "sentence",
                    "range": {
                        "start_para": 1,
                        "end_para": 6,
                        "start_quote": "a",
                        "end_quote": "b",
                    },
                }
            ],
            "consistency": [],
        }
        out, changes = apply_p1_sentence_span(report)
        self.assertEqual(out["weaknesses"][0]["fixable"], "structure")
        self.assertTrue(any(row["step"] == "P1" for row in changes))

    def test_p2_drops_확인용_title(self) -> None:
        kept, dropped = filter_p2_items(
            [{"id": "X", "title": "문제 없음이 아니라 확인용", "body": "본문"}]
        )
        self.assertEqual(kept, [])
        self.assertEqual(len(dropped), 1)
        self.assertIn(dropped[0]["marker"], ("확인용", "문제 없"))

    def test_priority_floor(self) -> None:
        small = apply_card_priorities(
            [{"id": 1, "impact": 1}, {"id": 2, "impact": 2}]
        )
        self.assertTrue(all(c["priority"] == "high" for c in small))
        mixed = apply_card_priorities(
            [
                {"id": 1, "impact": 5, "start_para": 1},
                {"id": 2, "impact": 3, "start_para": 2},
                {"id": 3, "impact": 3, "start_para": 3},
                {"id": 4, "impact": 1, "start_para": 4},
                {"id": 5, "impact": 2, "start_para": 5},
            ]
        )
        highs = [c["id"] for c in mixed if c["priority"] == "high"]
        self.assertGreaterEqual(len(highs), 3)
        self.assertIn(1, highs)
        low_only = apply_card_priorities(
            [
                {"id": i, "impact": 1, "start_para": i}
                for i in range(1, 6)
            ]
        )
        refs = [c for c in low_only if c["priority"] == "ref"]
        self.assertEqual(len(refs), 3)


class LensTests(unittest.TestCase):
    def test_explanation_lens_mapping(self) -> None:
        self.assertEqual(explanation_lens_for_project("문학", "에세이"), "strong")
        self.assertEqual(explanation_lens_for_project("문학", "순문학"), "strong")
        self.assertEqual(explanation_lens_for_project("웹소설", "로맨스"), "normal")
        self.assertEqual(explanation_lens_for_project("웹소설", "판타지"), "normal")
        self.assertEqual(explanation_lens_for_project("장르문학", "로맨스"), "normal")
        self.assertEqual(explanation_lens_for_project("동화", "유아"), "normal")


class PipelineFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.connection = app.connect()
        self.project_id = int(
            self.connection.execute(
                "INSERT INTO project(title, main_genre, sub_genre) "
                "VALUES ('첨삭 시험', '웹소설', '로맨스')"
            ).lastrowid
        )
        chapter_id = int(
            self.connection.execute(
                "INSERT INTO chapter(project_id, title, sort_order) VALUES (?, '1장', 0)",
                (self.project_id,),
            ).lastrowid
        )
        self.scene_id = int(
            self.connection.execute(
                "INSERT INTO scene(project_id, chapter_id, title, sort_order) "
                "VALUES (?, ?, '1화', 0)",
                (self.project_id, chapter_id),
            ).lastrowid
        )
        self.connection.execute(
            "INSERT INTO character(project_id, name, short_description, sort_order) "
            "VALUES (?, '정원', '남주', 0)",
            (self.project_id,),
        )
        self.connection.commit()
        self.paragraphs = paragraphs_from_text(MANUSCRIPT)

    def tearDown(self) -> None:
        self.connection.close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def _run(self, claude, paragraphs=None, **options):
        opts = {
            "scene_title": "1화",
            "revision_no": 1,
            "explanation_lens": "normal",
            "max_cost": 2.0,
        }
        opts.update(options)
        return run_feedback(
            self.connection,
            self.project_id,
            self.scene_id,
            paragraphs if paragraphs is not None else self.paragraphs,
            opts,
            claude=claude,
        )

    def test_happy_path_ok_with_priorities(self) -> None:
        fake = PipelineFake(
            consistency=_default_consistency(),
            report=_default_report(),
        )
        run_id = self._run(fake)
        run = feedback_store.get_run(self.connection, run_id)
        self.assertEqual(run["status"], "ok")
        refs = {card.get("report_ref") for card in run["cards"]}
        self.assertIn("C1", refs)
        self.assertIn("C2", refs)
        self.assertIn("W1", refs)
        self.assertIn("W2", refs)
        self.assertNotIn("C3", refs)
        titles = {card.get("report_ref"): card.get("title") for card in run["cards"]}
        self.assertEqual(titles.get("C1"), "이름 충돌")
        self.assertEqual(titles.get("W1"), "해설이 깁니다")
        self.assertEqual(titles.get("W2"), "장면 구조")
        self.assertIn("W3", refs)
        note = next(card for card in run["cards"] if card.get("report_ref") == "W3")
        self.assertIsNone(note.get("suggestion"))
        note_codes = [
            w.get("code") for w in (note.get("warnings") or note.get("warnings_json") or [])
        ]
        self.assertIn("note_only", note_codes)
        self.assertNotIn("W4", refs)
        self.assertIs(run["params"].get("fake"), False)
        self.assertTrue(all(card.get("priority") for card in run["cards"]))
        report_prompts = [p for p in fake.prompts if "피드백 리포트를 JSON" in p]
        self.assertEqual(len(report_prompts), 1)
        self.assertIn("이름 충돌", report_prompts[0])
        self.assertNotIn("나이 표기 모호", report_prompts[0])
        maybe_titles = [
            item.get("title")
            for item in (run.get("report") or {}).get("consistency") or []
        ]
        self.assertIn("나이 표기 모호", maybe_titles)
        dropped = (run.get("params") or {}).get("dropped") or []
        dropped_titles = [row.get("title") for row in dropped]
        self.assertIn("인용 실패 정합성", dropped_titles)
        self.assertIn("인용 실패 약점", dropped_titles)
        by_title = {row.get("title"): row.get("reason") for row in dropped}
        self.assertEqual(by_title.get("인용 실패 정합성"), "quote_invalid")
        self.assertEqual(by_title.get("인용 실패 약점"), "quote_invalid")
        self.assertTrue(all("reason" in row and "id" in row and "title" in row for row in dropped))

    def test_params_records_fake_mode_flag(self) -> None:
        fake = PipelineFake(
            consistency=_default_consistency(),
            report=_default_report(),
        )
        with patch("feedback_pipeline.runner.is_fake_mode", return_value=True):
            run_id = self._run(fake)
        run = feedback_store.get_run(self.connection, run_id)
        self.assertIs(run["params"].get("fake"), True)

    def test_one_card_parse_failure_is_partial(self) -> None:
        fake = PipelineFake(
            consistency=_default_consistency(),
            report=_default_report(),
            fail_card_ids={"C2"},
        )
        run_id = self._run(fake)
        run = feedback_store.get_run(self.connection, run_id)
        self.assertEqual(run["status"], "partial")
        refs = {card.get("report_ref") for card in run["cards"]}
        self.assertIn("C1", refs)
        self.assertIn("C2", refs)
        failed = next(card for card in run["cards"] if card.get("report_ref") == "C2")
        codes = [w.get("code") for w in (failed.get("warnings") or failed.get("warnings_json") or [])]
        self.assertIn("generation_failed", codes)
        dropped_reasons = [
            (row.get("id"), row.get("reason"))
            for row in (run.get("params") or {}).get("dropped") or []
        ]
        self.assertIn(("C2", "generation_failed"), dropped_reasons)

    def test_none_fixable_note_cards_and_dropped_range(self) -> None:
        long_text = (
            MANUSCRIPT
            + "\n\n여섯 번째 문단입니다. 해가 졌습니다.\n\n"
            "일곱 번째 문단입니다. 달이 떴습니다."
        )
        paras = paragraphs_from_text(long_text)
        report = {
            "summary": "참고 카드 시험",
            "rules": [],
            "scores": [],
            "strengths": [],
            "consistency": [],
            "weaknesses": [
                {
                    "id": "N1",
                    "title": "참고 정상",
                    "body": "고칠 필요는 없습니다.",
                    "type": "other",
                    "fixable": "none",
                    "impact": 2,
                    "certainty": "sure",
                    "perspectives": ["critic"],
                    "range": RANGE_P1,
                },
                {
                    "id": "N2",
                    "title": "참고 인용 실패",
                    "body": "위치를 못 찾음",
                    "type": "other",
                    "fixable": "none",
                    "impact": 2,
                    "certainty": "sure",
                    "perspectives": ["critic"],
                    "range": RANGE_MISSING,
                },
                {
                    "id": "N3",
                    "title": "참고 긴 구간",
                    "body": "긴 구간 참고",
                    "type": "other",
                    "fixable": "none",
                    "impact": 2,
                    "certainty": "sure",
                    "perspectives": ["critic"],
                    "range": {
                        "start_para": 1,
                        "end_para": 6,
                        "start_quote": "정원이 손을",
                        "end_quote": "해가 졌습니다",
                    },
                },
            ],
        }
        fake = PipelineFake(
            consistency={"facts": [], "issues": []},
            report=report,
        )
        run_id = self._run(fake, paragraphs=paras)
        run = feedback_store.get_run(self.connection, run_id)
        refs = {card.get("report_ref"): card for card in run["cards"]}
        self.assertIn("N1", refs)
        self.assertNotIn("N2", refs)
        self.assertIn("N3", refs)
        n1 = refs["N1"]
        self.assertEqual(n1.get("kind"), "style")
        self.assertEqual(n1.get("style_type"), "other")
        self.assertIsNone(n1.get("suggestion"))
        self.assertEqual(n1.get("reason"), "고칠 필요는 없습니다.")
        self.assertEqual(n1.get("title"), "참고 정상")
        self.assertEqual(n1.get("start_para"), 1)
        self.assertEqual(n1.get("end_para"), 1)
        n1_codes = [
            w.get("code") for w in (n1.get("warnings") or n1.get("warnings_json") or [])
        ]
        self.assertEqual(n1_codes, ["note_only"])
        n3 = refs["N3"]
        self.assertEqual(n3.get("start_para"), 1)
        self.assertEqual(n3.get("end_para"), 5)
        self.assertEqual(int(n3.get("end_para")) - int(n3.get("start_para")) + 1, 5)
        dropped = (run.get("params") or {}).get("dropped") or []
        self.assertTrue(
            any(
                row.get("id") == "N2" and row.get("reason") == "range_invalid"
                for row in dropped
            ),
            dropped,
        )
        card_prompts = [p for p in fake.prompts if "첨삭 카드 1장" in p]
        self.assertEqual(card_prompts, [])

    def test_v3_clears_suggestion_with_removed_code(self) -> None:
        from feedback_pipeline.runner import _apply_card_rules

        paras = to_paragraphs(paragraphs_from_text(MANUSCRIPT))
        card = {
            "original_text": "정원이 손을 내밀었다.",
            "suggestion": "정원이 손을 내밀었다. Xenophon이 나타났다.",
            "start_para": 1,
            "end_para": 1,
            "start_quote": "정원이 손을",
            "end_quote": "내밀었다",
            "kind": "style",
            "warnings_json": [],
        }
        out = _apply_card_rules(card, paras, {"characters": [], "terms": []})
        self.assertIsNone(out.get("suggestion"))
        codes = [w.get("code") for w in out.get("warnings_json") or []]
        self.assertIn("suggestion_removed", codes)

    def test_warning_messages_are_korean(self) -> None:
        from feedback_pipeline.checks import warning_from_check

        v6 = warning_from_check(
            {"id": "V6", "ok": False, "original_len": 20, "suggestion_len": 4}
        )
        self.assertEqual(v6["message"], "수정안이 원문보다 크게 짧아졌어요")
        v8 = warning_from_check({"id": "V8", "warn": True, "missing": ["정원"]})
        self.assertIn("빠졌어요", v8["message"])
        v9 = warning_from_check(
            {"id": "V9", "warn": True, "repeats": ["정원이 정원이"], "pronoun_shift": "그녀→그"}
        )
        self.assertIn("반복됐어요", v9["message"])
        self.assertIn("인칭이 바뀌었어요", v9["message"])

    def test_report_failure_is_failed(self) -> None:
        fake = PipelineFake(
            consistency=_default_consistency(),
            report=_default_report(),
            fail_report=True,
        )
        run_id = self._run(fake)
        run = feedback_store.get_run(self.connection, run_id)
        self.assertEqual(run["status"], "failed")
        self.assertFalse(run.get("report"))

    def test_cost_cap_is_partial(self) -> None:
        fake = PipelineFake(
            consistency=_default_consistency(),
            report=_default_report(),
            usage={"input_tokens": 400_000, "output_tokens": 400_000},
        )
        run_id = self._run(fake, max_cost=0.5)
        run = feedback_store.get_run(self.connection, run_id)
        self.assertEqual(run["status"], "partial")
        self.assertFalse(run.get("report"))


class UiFakeDiversityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.connection = app.connect()
        self.project_id = int(
            self.connection.execute(
                "INSERT INTO project(title, main_genre, sub_genre) "
                "VALUES ('첨삭 시험', '웹소설', '로맨스')"
            ).lastrowid
        )
        chapter_id = int(
            self.connection.execute(
                "INSERT INTO chapter(project_id, title, sort_order) VALUES (?, '1장', 0)",
                (self.project_id,),
            ).lastrowid
        )
        self.scene_id = int(
            self.connection.execute(
                "INSERT INTO scene(project_id, chapter_id, title, sort_order) "
                "VALUES (?, ?, '1화', 0)",
                (self.project_id, chapter_id),
            ).lastrowid
        )
        self.connection.commit()
        cases = json.loads(FIXTURES.read_text(encoding="utf-8"))
        case = next(item for item in cases if item["id"] == "html-br-only-webnovel")
        self.paragraphs = paragraphs_from_html(case["input"])

    def tearDown(self) -> None:
        self.connection.close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def test_fake_cards_are_diverse_and_spread(self) -> None:
        claude = UiFakeClaude(delay=0)
        run_id = run_feedback(
            self.connection,
            self.project_id,
            self.scene_id,
            self.paragraphs,
            {"scene_title": "1화", "revision_no": 1, "max_cost": 2.0},
            claude=claude,
        )
        run = feedback_store.get_run(self.connection, run_id)
        self.assertEqual(run["status"], "ok", run.get("params"))
        cards = run["cards"]
        self.assertGreaterEqual(len(cards), 8)
        reasons = [str(card.get("reason") or "") for card in cards]
        self.assertEqual(len(reasons), len(set(reasons)), reasons)
        suggestions = [
            str(card.get("suggestion") or "")
            for card in cards
            if card.get("suggestion") not in (None, "")
        ]
        self.assertEqual(len(suggestions), len(set(suggestions)), suggestions)
        self.assertGreaterEqual(len(suggestions), 5)
        without = [card for card in cards if card.get("suggestion") in (None, "")]
        self.assertGreaterEqual(len(without), 3)
        for card in cards:
            sug = card.get("suggestion")
            orig = str(card.get("original_text") or "")
            if sug not in (None, ""):
                self.assertNotEqual(str(sug), orig, card.get("report_ref"))
        spans = [
            card
            for card in cards
            if int(card.get("start_para") or 0) < int(card.get("end_para") or 0)
        ]
        self.assertGreaterEqual(len(spans), 2, [(c.get("report_ref"), c.get("start_para"), c.get("end_para")) for c in cards])
        paras = {int(card.get("start_para") or 0) for card in cards}
        self.assertGreater(len(paras), 1)
        kinds = {str(card.get("kind") or "") for card in cards}
        self.assertIn("structure", kinds)
        self.assertIn("consistency", kinds)
        warned = [
            card
            for card in cards
            if card.get("warnings") or card.get("warnings_json")
        ]
        self.assertGreaterEqual(len(warned), 1)
        maybe = [
            card
            for card in cards
            if str(card.get("kind") or "") == "consistency"
            and str(card.get("certainty") or "") == "maybe"
        ]
        self.assertGreaterEqual(len(maybe), 1)
        self.assertNotIn("시험 모드 고정 이유입니다", "".join(reasons))


class CliFakeTests(unittest.TestCase):
    def test_cli_fake_does_not_touch_app_db(self) -> None:
        original_data = app.DATA_DIR
        original_db = app.DATABASE_PATH
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
            folder = Path(raw)
            manuscript = folder / "ep.md"
            manuscript.write_text("한 문단입니다.\n\n두 번째 문단입니다.", encoding="utf-8")
            cases = folder / "cases.json"
            cases.write_text(
                json.dumps(
                    {
                        "projects": {
                            "t": {
                                "title": "시험",
                                "genre": "로맨스",
                                "characters": [{"name": "정원", "note": "남주"}],
                            }
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            db_path = folder / "tmp.sqlite3"
            code = cli_main(
                [
                    "--manuscript",
                    str(manuscript),
                    "--project-json",
                    f"{cases}:t",
                    "--db",
                    str(db_path),
                ]
            )
            self.assertEqual(code, 0)
            self.assertEqual(app.DATA_DIR, original_data)
            self.assertEqual(app.DATABASE_PATH, original_db)


if __name__ == "__main__":
    unittest.main()
