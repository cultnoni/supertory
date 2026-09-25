"""일반문학 단편 리포트: 합치기, 문체 쓰기 정책, 검증, 웹소설 경로 유지."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import feedback_store
import literary_form
from feedback_pipeline.claude_client import FakeClaude, _payload, estimate_cost_usd
from feedback_pipeline.config import HAIKU_45_MODEL, LITERATURE_STAGE_MODELS
from feedback_pipeline import prompt_loader
from feedback_pipeline.literature_cache import (
    build_cached_system,
    cache_prefix,
    estimate_shared_prefix_cost,
    with_schema_instruction,
)
from feedback_pipeline.literature_cards import (
    _allowed_edit_problem,
    _detection_bundles,
    _scene_blocks,
    apply_suggestion_fixes,
    assign_priority,
    merge_cards,
    screen_detected_cards,
)
from feedback_pipeline.literature_compare import stabilize_comparison
from feedback_pipeline.literature_reuse import load_reuse
from feedback_pipeline.literature_runner import (
    _normalize_rubric,
    _normalize_scene_map,
    prepare_literature_run,
    run_literature_short,
)
from feedback_pipeline.literature_style import apply_style_drafts
from feedback_pipeline.literature_text import MANUSCRIPT_MARK, assemble_manuscript, locate_quote
from feedback_pipeline.literature_verify import verify_literature_report


class StylePolicyTests(unittest.TestCase):
    def test_empty_only_unless_the_writer_asks(self) -> None:
        current = {
            "style_narration": "",
            "style_sentence": "〔토리〕 짧은 문장",
            "style_dialogue": "작가가 적은 대화",
            "style_lexicon": "",
            "style_choice": "반복은 의도",
            "style_habit": "것이다",
        }
        extracted = {
            "style_narration": "1인칭 과거",
            "style_sentence": "더 짧은 문장",
            "style_dialogue": "따옴표 없음",
            "style_lexicon": "감각어",
            "style_choice": "덮어쓰면 안 됨",
        }
        filled = apply_style_drafts(current, extracted, mode="fill_empty")
        self.assertEqual(set(filled), {"style_narration", "style_lexicon"})
        self.assertTrue(filled["style_narration"].startswith("〔토리〕"))
        asked = apply_style_drafts(current, extracted, mode="button")
        self.assertIn("style_sentence", asked)
        self.assertNotIn("style_dialogue", asked)
        self.assertNotIn("style_choice", asked)


class QuoteLocateTests(unittest.TestCase):
    def test_quote_wins_over_a_stale_paragraph_number(self) -> None:
        assembled = {
            "paragraphs": [
                {"n": 1, "local": 1, "scene_id": 10, "scene_title": "1", "text": "그는 문을 열었다."},
                {"n": 2, "local": 1, "scene_id": 11, "scene_title": "2", "text": "그는 푸른 눈을 깜빡였다."},
            ]
        }
        found = locate_quote(assembled, "푸른 눈을 깜빡였다", hinted_para=1)
        self.assertEqual(found["scene_id"], 11)
        self.assertEqual(found["matched"], "quote")
        self.assertEqual(found["para"], 2)


class VerifyTests(unittest.TestCase):
    def test_drops_missing_quotes_scores_and_marks_intentional_choice(self) -> None:
        source = "그는 푸른 눈을 깜빡였다."
        rubric = {
            "items": [{"evidence": [{"quote": "그는 푸른 눈을 깜빡였다."}]}],
            "strengths": [{"quote": "그는 푸른 눈을 깜빡였다."}],
        }
        report = {
            "overview": {"reader": "당선 확률은 80%입니다. 첫 문장이 선명합니다.", "judge": "심사"},
            "reading": {"body": "이 작품은 눈에 관한 이야기로 읽힙니다.", "intent_gap": ""},
            "diagnoses": [
                {
                    "key": "pov",
                    "verdict": "problem",
                    "note": "시점이 흔들립니다.",
                    "evidence": [{"quote": "그는 푸른 눈을 깜빡였다."}],
                },
                {
                    "key": "style",
                    "verdict": "problem",
                    "note": "독백의 구절 반복은 리듬을 위한 의도처럼 보인다.",
                    "evidence": [{"quote": "그는 푸른 눈을 깜빡였다."}],
                },
                {
                    "key": "ending",
                    "verdict": "problem",
                    "note": "결말이 급합니다.",
                    "evidence": [{"quote": "원고에 없는 문장입니다."}],
                },
            ],
            "strengths": [
                {"title": "첫 문장", "body": "선명합니다.", "quote": "그는 푸른 눈을 깜빡였다."},
                {"title": "없는 인용", "body": "수정안: 그는 눈을 감았다.", "quote": "그는 눈을 감았다."},
            ],
            "tasks": ["도입을 살린다."],
        }
        cleaned, log = verify_literature_report(
            report,
            source_text=source,
            rubric=rubric,
            style_choice="독백의 구절 반복은 리듬을 위한 의도",
            contest_on=False,
        )
        actions = {item["action"] for item in log}
        self.assertIn("drop_score", actions)
        self.assertIn("drop_quote", actions)
        self.assertIn("drop_verdict", actions)
        self.assertIn("mark_intentional", actions)
        self.assertIn("drop_judge", actions)
        self.assertNotIn("80%", cleaned["overview"]["reader"])
        self.assertEqual(cleaned["overview"]["judge"], "")
        keys = {item["key"] for item in cleaned["diagnoses"]}
        self.assertNotIn("ending", keys)
        style = next(item for item in cleaned["diagnoses"] if item["key"] == "style")
        self.assertTrue(style["intentional"])
        self.assertEqual(style["verdict"], "room")
        self.assertEqual(len(cleaned["strengths"]), 1)


class LiteratureRunTests(unittest.TestCase):
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
                "INSERT INTO project(title, cluster_id, main_genre, sub_genre, literary_form, "
                "style_narration, style_sentence, style_dialogue, style_lexicon, style_choice) "
                "VALUES ('단편', 'general_literature', 'general_lit', 'mid', 'short', "
                "'1인칭', '짧은 문장', '따옴표', '감각', '독백의 구절 반복은 리듬을 위한 의도')"
            ).lastrowid
        )
        chapter_id = int(
            self.connection.execute(
                "INSERT INTO chapter(project_id, title, sort_order) VALUES (?, '1장', 0)",
                (self.project_id,),
            ).lastrowid
        )
        self.scene_a = self._scene(chapter_id, 0, "앞", "<p>그는 푸른 눈을 깜빡였다.</p>")
        self.scene_b = self._scene(chapter_id, 1, "뒤", "<p>비가 왔다.</p>")
        self.connection.commit()

    def tearDown(self) -> None:
        self.connection.close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def _scene(self, chapter_id: int, order: int, title: str, html: str) -> int:
        scene_id = int(
            self.connection.execute(
                "INSERT INTO scene(project_id, chapter_id, title, sort_order) VALUES (?, ?, ?, ?)",
                (self.project_id, chapter_id, title, order),
            ).lastrowid
        )
        self.connection.execute(
            "INSERT INTO scene_revision(scene_id, revision_no, content_md, word_count, is_current) "
            "VALUES (?, 1, ?, 1, 1)",
            (scene_id, html),
        )
        return scene_id

    def test_whole_work_run_skips_manuscript_in_the_report_call(self) -> None:
        rows = [
            {"id": self.scene_a, "title": "앞", "revision_no": 1, "content_md": "<p>그는 푸른 눈을 깜빡였다.</p>"},
            {"id": self.scene_b, "title": "뒤", "revision_no": 1, "content_md": "<p>비가 왔다.</p>"},
        ]
        assembled = assemble_manuscript(rows)
        self.assertEqual(assembled["scenes"][1]["para_offset"], 1)
        self.assertEqual(assembled["paragraphs"][1]["n"], 2)
        quote = "그는 푸른 눈을 깜빡였다."
        claude = FakeClaude([
            {
                "scenes": [{"id": 1, "start_para": 1, "end_para": 1, "pov": "그", "summary": "눈", "mode": "scene"}],
                "first_sentence": quote,
                "last_sentence": "비가 왔다.",
                "motifs": [],
            },
            {
                "items": [
                    {
                        "key": "opening",
                        "verdict": "works",
                        "note": "첫 문장이 선명합니다.",
                        "evidence": [{"quote": quote, "para": 1}],
                    }
                ],
                "strengths": [{"title": "첫 문장", "body": "선명합니다.", "quote": quote, "para": 1}],
            },
            {
                "overview": {
                    "reader": "당선 확률은 80%입니다. 몰입이 있습니다.",
                    "editor": "구조가 단정합니다.",
                    "critic": "눈의 이미지가 남습니다.",
                    "judge": "본심에 올릴 만합니다.",
                },
                "reading": {"body": "이 작품은 눈에 관한 이야기로 읽힙니다.", "intent_gap": ""},
                "strengths": [{"title": "첫 문장", "body": "선명합니다.", "quote": quote, "para": 1}],
                "diagnoses": [
                    {
                        "key": "opening",
                        "verdict": "works",
                        "note": "첫 문장이 선명합니다.",
                        "evidence": [{"quote": quote, "para": 1}],
                    }
                ],
                "tasks": ["결말을 한 번 더 본다.", "인물의 멈춤을 살핀다.", "이미지를 변주한다."],
                "comparison": None,
            },
        ], default_parsed={"cards": []})
        params = {"usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "stages": []}}
        prepared = prepare_literature_run(
            self.connection,
            self.project_id,
            model="claude-sonnet-5",
            prompt_version="literature-short-v0.1",
            params=params,
        )
        with patch("feedback_pipeline.literature_runner.spell_error_count", return_value=2):
            run_literature_short(
                self.connection,
                self.project_id,
                prepared["run_id"],
                {"max_cost": 2},
                claude=claude,
            )
        self.connection.commit()
        systems = [item.get("system") for item in claude.kwargs]
        self.assertTrue(systems)
        for system in systems:
            self.assertIsInstance(system, list)
            self.assertEqual(system[1].get("cache_control"), {"type": "ephemeral"})
            self.assertIn(MANUSCRIPT_MARK, system[1].get("text") or "")
        # 단계 지시(유저 메시지)에는 원고 전문이 없고, 스키마만 뒤에 붙는다.
        self.assertNotIn(MANUSCRIPT_MARK, claude.prompts[-1])
        self.assertIn("JSON 스키마", claude.prompts[-1])
        self.assertIn("강점", claude.prompts[1])
        # 모든 문학 호출이 같은 캐시 앞부분을 쓴다.
        prefixes = [system[1]["text"] for system in systems]
        self.assertEqual(len(set(prefixes)), 1)
        run = feedback_store.get_run(self.connection, prepared["run_id"])
        self.assertEqual(run["pipeline"], "literature_short")
        self.assertEqual(run["run_kind"], "analyze")
        self.assertEqual(run["cards"], [])
        self.assertNotIn("80%", run["report"]["overview"]["reader"])
        self.assertEqual(run["report"]["overview"]["judge"], "")
        offsets = {
            int(row["scene_id"]): int(row["para_offset"])
            for row in run["scenes"]
        }
        self.assertEqual(offsets[self.scene_b], 1)
        web = feedback_store.create_run(
            self.connection, self.project_id, "analyze", "claude-sonnet-5", "pipeline-v0.5", {}
        )
        stored = self.connection.execute(
            "SELECT pipeline FROM feedback_run WHERE id = ?",
            (web,),
        ).fetchone()[0]
        self.assertEqual(stored, "")
        self.assertIsNone(literary_form.literary_track({
            "cluster_id": "webnovel",
            "main_genre": "fantasy",
            "sub_genre": "regression",
            "literary_form": "short",
        }))

    def test_contest_toggle_is_literary_only(self) -> None:
        status_body = json.dumps({
            "contest_prep": 1,
            "contest_name": "신춘문예",
            "contest_pages_min": 70,
            "contest_pages_max": 80,
        }).encode("utf-8")
        handler = _Handler(status_body)
        saved = app.SuperToryHandler.update_project_settings(handler, self.project_id, json.loads(status_body))
        self.assertEqual(saved["contest_prep"], 1)
        self.assertEqual(saved["contest_name"], "신춘문예")
        self.assertNotIn("contest_prep", literary_form.public_literary_fields({
            "cluster_id": "general_literature",
            "main_genre": "essay",
            "sub_genre": "tbd",
            "contest_prep": 1,
        }))


class CompareStabilityTests(unittest.TestCase):
    def test_untouched_verdicts_stay_and_only_changed_text_is_new(self) -> None:
        previous_paragraphs = [
            {"n": 1, "text": "나는 책을 뒤적였다.", "scene_id": 1, "local": 1},
            {"n": 2, "text": "아내가 저고리를 찾았다.", "scene_id": 1, "local": 2},
        ]
        current_paragraphs = [
            {"n": 1, "text": "나는 책을 뒤적였다.", "scene_id": 1, "local": 1},
            {"n": 2, "text": "그는 아내를 내려다보았다.", "scene_id": 1, "local": 2},
            {"n": 3, "text": "아내가 저고리를 찾았다.", "scene_id": 1, "local": 3},
        ]
        previous = {
            "diagnoses": [
                {"key": "opening", "verdict": "works", "note": "첫 문장이 남는다.", "evidence": [{"quote": "나는 책을 뒤적였다."}]},
                {"key": "pov", "verdict": "works", "note": "시점이 유지된다.", "evidence": [{"quote": "나는 책을 뒤적였다."}]},
                {"key": "scene_summary", "verdict": "room", "note": "요약이 길다.", "evidence": [{"quote": "아내가 저고리를 찾았다."}]},
                {"key": "implication", "verdict": "room", "note": "함축이 약하다.", "evidence": [{"quote": "아내가 저고리를 찾았다."}]},
            ]
        }
        report = {
            "reading": {"body": "같다"},
            "diagnoses": [
                {"key": "pov", "verdict": "problem", "note": "3인칭으로 빠진다.", "evidence": [{"quote": "그는 아내를 내려다보았다."}]},
                {"key": "opening", "verdict": "room", "note": "새 문장 때문에 흔들린다.", "evidence": [{"quote": "나는 책을 뒤적였다."}, {"quote": "그는 아내를 내려다보았다."}]},
                {"key": "scene_summary", "verdict": "works", "note": "리듬이 좋다.", "evidence": [{"quote": "아내가 저고리를 찾았다."}]},
                {"key": "implication", "verdict": "works", "note": "초점이 바뀌었다.", "evidence": [{"quote": "아내가 저고리를 찾았다."}]},
            ],
        }
        cleaned, log = stabilize_comparison(report, previous, previous_paragraphs, current_paragraphs)
        statuses = {item["key"]: item["status"] for item in cleaned["comparison"]["items"]}
        by_key = {item["key"]: item for item in cleaned["diagnoses"]}
        self.assertEqual(statuses["opening"], "same")
        self.assertEqual(statuses["pov"], "new")
        self.assertEqual(by_key["opening"]["verdict"], "works")
        self.assertTrue(any(entry["action"] == "partial_evidence_held" and entry["target"] == "opening" for entry in log))
        self.assertEqual(statuses["scene_summary"], "same")
        self.assertEqual(statuses["implication"], "same")
        self.assertEqual(by_key["scene_summary"]["verdict"], "room")
        self.assertEqual(by_key["implication"]["verdict"], "room")
        self.assertTrue(any(entry["action"] == "keep_verdict" for entry in log))


class ShapeTests(unittest.TestCase):
    def test_korean_scene_map_and_rubric_are_kept(self) -> None:
        assembled = {
            "paragraphs": [
                {"n": 2, "local": 1, "scene_id": 9, "scene_title": "본문", "text": "그것이 어째 없을까?"},
            ]
        }
        scene_map = _normalize_scene_map(
            {
                "장면_지도": [
                    {
                        "장면번호": 1,
                        "문단범위": "P1-P2",
                        "시점인물": "나",
                        "시간_장소": "밤, 방",
                        "등장인물": ["나", "아내"],
                        "한줄요약": "저고리를 찾는다.",
                        "서술유형": "장면",
                    }
                ],
                "첫문장": "그것이 어째 없을까?",
                "마지막문장": "눈물이 넘친다.",
                "반복_이미지_모티프": [{"모티프": "한숨", "등장위치": ["P2"]}],
            }
        )
        self.assertEqual(scene_map["scenes"][0]["pov"], "나")
        self.assertEqual(scene_map["motifs"][0]["image"], "한숨")
        rubric = _normalize_rubric(
            {
                "항목": [
                    {
                        "항목": "도입",
                        "판정": "잘 작동함",
                        "설명": "첫 문장이 남는다.",
                        "근거": [{"인용": "그것이 어째 없을까?"}],
                    }
                ],
                "강점": [{"제목": "첫 문장", "본문": "선명하다.", "인용": "그것이 어째 없을까?"}],
            },
            assembled,
            False,
        )
        self.assertEqual(rubric["items"][0]["key"], "opening")
        self.assertEqual(rubric["items"][0]["verdict"], "works")
        self.assertEqual(rubric["strengths"][0]["quote"], "그것이 어째 없을까?")
        self.assertEqual(rubric["strengths"][0]["para"], 2)

    def test_literature_schemas_name_every_array_item(self) -> None:
        for name in (
            "literature/scene_map_schema.json",
            "literature/rubric_schema.json",
            "literature/report_schema.json",
            "literature/card_schema.json",
            "literature/suggestion_schema.json",
            "literature/select_schema.json",
            "literature/meaning_schema.json",
        ):
            schema = prompt_loader.load_json(name)
            self.assertTrue(_schema_items_are_closed(schema), name)


def _schema_items_are_closed(node) -> bool:
    if isinstance(node, list):
        return all(_schema_items_are_closed(item) for item in node)
    if not isinstance(node, dict):
        return True
    if node.get("type") == "object":
        if node.get("additionalProperties") is not False:
            return False
        required = set(node.get("required") or [])
        if set(node.get("properties") or []) - required:
            return False
    if node.get("type") == "array" and "items" not in node:
        return False
    return all(_schema_items_are_closed(value) for value in node.values())


class SceneBlockTests(unittest.TestCase):
    def test_scene_map_splits_one_binder_scene(self) -> None:
        assembled = {
            "paragraphs": [
                {"n": 1, "scene_id": 6, "text": "첫 문장."},
                {"n": 2, "scene_id": 6, "text": "둘째."},
                {"n": 3, "scene_id": 6, "text": "셋째."},
            ]
        }
        scene_map = {
            "scenes": [
                {"para_range": "P1-P2", "summary": "앞"},
                {"para_range": "P3", "summary": "뒤"},
            ]
        }
        blocks = _scene_blocks(assembled, scene_map)
        self.assertEqual(len(blocks), 2)
        self.assertIn("[P1]", blocks[0]["text"])
        self.assertNotIn("[P3]", blocks[0]["text"])
        self.assertEqual(blocks[1]["before"], "앞")
        self.assertEqual(blocks[1]["scene_id"], 6)


class CardMergeTests(unittest.TestCase):
    def test_overlapping_quotes_merge_before_a_suggestion_exists(self) -> None:
        cards = [
            {"form": "note", "tags": ["repeat"], "priority": "low", "quote": "아내가 양산에 자극을 받은 것이다.", "reason": "반복", "global_para": 3, "original_text": "아내가 양산에 자극을 받은 것이다."},
            {"form": "suggest", "tags": ["grammar"], "priority": "high", "quote": "아내가 양산에 자극을 받은 것이다. 예술가의 처 노릇.", "reason": "비문", "global_para": 3, "original_text": "아내가 양산에 자극을 받은 것이다. 예술가의 처 노릇."},
            {"form": "suggest", "tags": ["excess"], "priority": "medium", "quote": "아내가 양산에 자극을 받은 것이다.", "reason": "설명이 길다", "global_para": 3, "original_text": "아내가 양산에 자극을 받은 것이다."},
        ]
        merged = merge_cards(cards)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["form"], "suggest")
        self.assertEqual(merged[0]["priority"], "high")
        self.assertEqual(merged[0]["tags"], ["repeat", "grammar", "excess"])

    def test_empty_suggestion_retries_once_then_demotes(self) -> None:
        cards = [{"form": "suggest", "tags": ["pov"], "fix_id": "0", "quote": "그는 보았다.", "suggestion": ""}]
        _, log, retry = apply_suggestion_fixes(cards, [], attempt=1)
        self.assertEqual(len(retry), 1)
        self.assertEqual(log[0]["action"], "retry_suggestion")
        _, again, left = apply_suggestion_fixes(retry, [{"id": "0", "cannot_fix": True, "cannot_fix_reason": "목소리", "suggestion": "그는 보았다.", "delete_span": ""}], attempt=2)
        self.assertEqual(left, [])
        self.assertEqual(cards[0]["form"], "note")
        self.assertIn("cannot_fix", again[0]["detail"])

    def test_excess_suggestion_is_the_text_with_the_span_removed(self) -> None:
        cards = [{"form": "suggest", "tags": ["excess"], "fix_id": "0", "quote": "나는 앉았다. 이것은 설명이다.", "suggestion": ""}]
        apply_suggestion_fixes(
            cards,
            [{"id": "0", "cannot_fix": False, "cannot_fix_reason": "", "suggestion": "새 문장이다.", "delete_span": " 이것은 설명이다."}],
            attempt=1,
        )
        self.assertEqual(cards[0]["suggestion"], "나는 앉았다.")
        self.assertEqual(cards[0]["form"], "suggest")


class DetectionRuleTests(unittest.TestCase):
    def test_bundle_stops_at_a_scene_boundary_near_twenty_pages(self) -> None:
        text = "가" * 1500
        blocks = [{"scene_id": 1, "text": text, "before": "앞", "after": "뒤"} for _ in range(3)]
        bundles = _detection_bundles(blocks)
        self.assertEqual(len(bundles), 2)
        self.assertIn(text, bundles[0]["text"])
        self.assertNotEqual(bundles[0]["text"], bundles[1]["text"])

    def test_generic_problem_and_missing_readings_are_dropped(self) -> None:
        cards = [
            {"tags": ["pov"], "reader_problem": "가독성이 떨어진다", "readings": [], "quote": "그는 보았다."},
            {"tags": ["ambiguity"], "reader_problem": "독자는 그것이 양산인지 신인지 알 수 없다.", "readings": ["양산"], "quote": "그것"},
            {"tags": ["pov"], "reader_problem": "독자는 누가 아내를 내려다보는지 알 수 없다.", "readings": [], "quote": "그는 보았다."},
        ]
        kept, log = screen_detected_cards(cards)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["tags"], ["pov"])
        self.assertEqual(len(log), 2)

    def test_priority_follows_the_tag_not_the_model(self) -> None:
        card = {"tags": ["excess"], "quote": "설명", "source_key": ""}
        assign_priority(card, [], "")
        self.assertEqual(card["priority"], "medium")
        card["tags"] = ["pov"]
        assign_priority(card, [], "")
        self.assertEqual(card["priority"], "high")

    def test_pronoun_swap_stays_and_new_words_or_person_flip_are_demoted(self) -> None:
        ok = _allowed_edit_problem(
            "그는 문득 아내를 내려다보며, 그녀의 헌신이 가엾다고 생각했다.",
            "나는 문득 아내를 내려다보며, 아내의 헌신이 가엾다고 생각했다.",
            {"pov"},
            style_narration="1인칭",
            style_dialogue="",
            manuscript="",
        )
        self.assertEqual(ok, "")
        flipped = _allowed_edit_problem(
            "나는 아내를 보았다.",
            "그는 아내를 보았다.",
            {"pov"},
            style_narration="1인칭",
            style_dialogue="",
            manuscript="",
        )
        self.assertIn("3인칭", flipped)
        added = _allowed_edit_problem(
            "나는 감정을 드러내며,",
            "나는 답답하고 이상한 감정을 드러내며,",
            {"pov"},
            style_narration="1인칭",
            style_dialogue="",
            manuscript="",
        )
        self.assertIn("내용어", added)


class CacheAndReuseTests(unittest.TestCase):
    def test_cache_pricing_and_payload_mark_the_prefix(self) -> None:
        self.assertEqual(LITERATURE_STAGE_MODELS["style"], "claude-sonnet-5")
        self.assertEqual(LITERATURE_STAGE_MODELS["verify"], "claude-sonnet-5")
        self.assertEqual(HAIKU_45_MODEL, "claude-haiku-4-5-20251001")
        plain = estimate_cost_usd("claude-sonnet-5", 9000, 0)
        written = estimate_cost_usd("claude-sonnet-5", 0, 0, cache_write_tokens=9000)
        read = estimate_cost_usd("claude-sonnet-5", 0, 0, cache_read_tokens=9000)
        self.assertAlmostEqual(plain, 0.027, places=4)
        self.assertAlmostEqual(written, 0.03375, places=5)
        self.assertAlmostEqual(read, 0.0027, places=4)
        savings = estimate_shared_prefix_cost(9000, 10)
        self.assertLess(savings["with_cache_usd"], savings["without_cache_usd"])
        prefix = cache_prefix("[P1] 문장", "서술: 1인칭")
        common = "공통 시스템"
        body = _payload(
            model="claude-sonnet-5",
            prompt="지시문",
            system=build_cached_system(prefix, common),
            temperature=0.2,
            max_tokens=100,
            omit_temperature=True,
            thinking_disabled=True,
        )
        system = body["system"]
        self.assertEqual(system[0]["text"], common)
        self.assertEqual(system[1]["cache_control"], {"type": "ephemeral"})
        self.assertIn("원고:", system[1]["text"])
        self.assertEqual(body["messages"][0]["content"], "지시문")
        self.assertNotIn("output_config", body)
        # 유저 쪽 cached_prefix는 system 캐시가 있을 때 붙이지 않는다.
        body_legacy = _payload(
            model="claude-sonnet-5",
            prompt="지시문",
            system=None,
            temperature=0.2,
            max_tokens=100,
            omit_temperature=True,
            thinking_disabled=True,
            cached_prefix=prefix,
        )
        content = body_legacy["messages"][0]["content"]
        self.assertEqual(content[0]["cache_control"], {"type": "ephemeral"})
        self.assertIn("원고:", content[0]["text"])
        self.assertEqual(content[1]["text"], "지시문")
        instructed = with_schema_instruction("지시", {"type": "object"})
        self.assertIn("JSON 스키마", instructed)
        self.assertIn('"type": "object"', instructed)

    def test_reuse_from_cards_skips_early_model_calls(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        original_data_dir = app.DATA_DIR
        original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        connection = app.connect()
        try:
            project_id = int(
                connection.execute(
                    "INSERT INTO project(title, cluster_id, main_genre, sub_genre, literary_form, "
                    "style_narration, style_sentence, style_dialogue, style_lexicon, style_choice) "
                    "VALUES ('단편', 'general_literature', 'general_lit', 'mid', 'short', "
                    "'1인칭', '짧은 문장', '따옴표', '감각', '독백의 구절 반복은 리듬을 위한 의도')"
                ).lastrowid
            )
            chapter_id = int(
                connection.execute(
                    "INSERT INTO chapter(project_id, title, sort_order) VALUES (?, '1장', 0)",
                    (project_id,),
                ).lastrowid
            )
            scene_id = int(
                connection.execute(
                    "INSERT INTO scene(project_id, chapter_id, title, sort_order) VALUES (?, ?, '앞', 0)",
                    (project_id, chapter_id),
                ).lastrowid
            )
            html = "<p>그는 푸른 눈을 깜빡였다.</p>"
            connection.execute(
                "INSERT INTO scene_revision(scene_id, revision_no, content_md, word_count, is_current) "
                "VALUES (?, 1, ?, 1, 1)",
                (scene_id, html),
            )
            connection.commit()
            quote = "그는 푸른 눈을 깜빡였다."
            claude = FakeClaude([
                {
                    "scenes": [{"id": 1, "start_para": 1, "end_para": 1, "pov": "그", "summary": "눈", "mode": "scene"}],
                    "first_sentence": quote,
                    "last_sentence": quote,
                    "motifs": [],
                },
                {"items": [], "strengths": []},
                {
                    "overview": {"reader": "몰입", "editor": "단정", "critic": "눈", "judge": ""},
                    "reading": {"body": "눈에 관한 이야기입니다.", "intent_gap": ""},
                    "strengths": [],
                    "diagnoses": [],
                    "tasks": ["하나", "둘", "셋"],
                    "comparison": None,
                },
            ], default_parsed={"cards": [], "keep": [], "drop": [], "fixes": []})
            prepared = prepare_literature_run(
                connection,
                project_id,
                model="claude-sonnet-5",
                prompt_version="literature-short-v0.1",
                params={"usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "stages": []}},
            )
            with patch("feedback_pipeline.literature_runner.spell_error_count", return_value=0):
                run_literature_short(
                    connection, project_id, prepared["run_id"], {"max_cost": 2}, claude=claude
                )
            connection.commit()
            assembled = assemble_manuscript(
                [{"id": scene_id, "title": "앞", "revision_no": 1, "content_md": html}]
            )
            # assemble_manuscript from rows needs source_hash from load - use store hashes
            from feedback_pipeline.literature_text import load_scene_rows

            assembled = assemble_manuscript(load_scene_rows(connection, project_id))
            mismatched, warning = load_reuse(
                connection,
                prepared["run_id"],
                {"scenes": [{"source_hash": "different"}]},
                "cards",
            )
            self.assertIsNone(mismatched)
            self.assertIn("달라", warning)
            reuse, ok = load_reuse(connection, prepared["run_id"], assembled, "cards")
            self.assertEqual(ok, "")
            self.assertEqual(reuse["from"], "cards")
            prepared2 = prepare_literature_run(
                connection,
                project_id,
                model="claude-sonnet-5",
                prompt_version="literature-short-v0.1",
                params={"usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "stages": []}},
            )
            with patch("feedback_pipeline.literature_runner.spell_error_count", return_value=0):
                run_literature_short(
                    connection,
                    project_id,
                    prepared2["run_id"],
                    {"max_cost": 2, "reuse": reuse},
                    claude=claude,
                )
            second = feedback_store.get_run(connection, prepared2["run_id"])
            stages = {
                item.get("stage")
                for item in ((second.get("params") or {}).get("usage") or {}).get("stages") or []
            }
            self.assertNotIn("scene_map", stages)
            self.assertNotIn("rubric", stages)
            self.assertNotIn("report", stages)
            self.assertTrue(second["report"])
            self.assertEqual(second["report"].get("tasks"), ["하나", "둘", "셋"])
        finally:
            connection.close()
            app.DATA_DIR = original_data_dir
            app.DATABASE_PATH = original_database_path
            temporary_directory.cleanup()


class _Handler:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def require_project(self, connection: sqlite3.Connection, project_id: int) -> None:
        return None
