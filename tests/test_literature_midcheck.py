"""중간 점검·가벼운 장 기록·대화 비율 단위 테스트."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import app
from feedback_pipeline.claude_client import FakeClaude
from feedback_pipeline.config import LONG_QUOTE_MONOLOGUE_CHARS, MIDCHECK_VERIFY_LIMIT
from feedback_pipeline.literature_context import assemble_unit, unit_source_hash
from feedback_pipeline.literature_midcheck import (
    apply_verify_revisions,
    classify_motif_status,
    drop_sentence_from_body,
    filter_manuscript_revisions,
    maybe_create_midcheck_nudge,
    midcheck_nudge_dedupe_key,
    prepare_midcheck_run,
    reading_section_title,
    render_midcheck_md,
    rewrite_out_of_range_units,
    run_literature_midcheck,
    scrub_system_jargon,
    select_units,
    soften_unverified_unit_claims,
)
from feedback_pipeline.literature_signals import (
    collect_signals,
    count_dialogue_chars,
    dialogue_ratio_label,
)
from feedback_pipeline.literature_unit_work import save_light_unit_work
from feedback_pipeline.literature_units import load_literary_units


class MidcheckUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_data_dir = app.DATA_DIR
        self.original_database_path = app.DATABASE_PATH
        app.DATA_DIR = Path(self.temporary_directory.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        self.connection = app.connect()

    def tearDown(self) -> None:
        self.connection.close()
        app.DATA_DIR = self.original_data_dir
        app.DATABASE_PATH = self.original_database_path
        self.temporary_directory.cleanup()

    def _long_project(self, chapters: int = 10) -> tuple[int, list[int]]:
        pid = int(
            self.connection.execute(
                "INSERT INTO project(title, cluster_id, main_genre, sub_genre, literary_form, intent_md) "
                "VALUES ('중간점검', 'general_literature', 'general_lit', 'mid', 'long', '의도')"
            ).lastrowid
        )
        scene_ids = []
        for i in range(chapters):
            ch = int(
                self.connection.execute(
                    "INSERT INTO chapter(project_id, title, sort_order) VALUES (?, ?, ?)",
                    (pid, f"{i + 1}장", i),
                ).lastrowid
            )
            sid = int(
                self.connection.execute(
                    "INSERT INTO scene(project_id, chapter_id, title, sort_order) VALUES (?, ?, ?, 0)",
                    (pid, ch, f"{i + 1}회"),
                ).lastrowid
            )
            self.connection.execute(
                "INSERT INTO scene_revision(scene_id, revision_no, content_md, word_count, is_current) "
                "VALUES (?, 1, ?, 1, 1)",
                (sid, f"<p>{i + 1}장 본문 영채와 형식</p>"),
            )
            scene_ids.append(sid)
        return pid, scene_ids

    def test_nudge_dedupe_at_multiples_of_ten(self) -> None:
        self.assertIsNone(maybe_create_midcheck_nudge(self.connection, 1, 9))
        pid, _ = self._long_project(10)
        first = maybe_create_midcheck_nudge(self.connection, pid, 10)
        self.assertTrue(first["created"])
        self.assertEqual(first["notification"]["dedupe_key"], midcheck_nudge_dedupe_key(10))
        again = maybe_create_midcheck_nudge(self.connection, pid, 10)
        self.assertFalse(again["created"])
        labels = [a["label"] for a in first["notification"]["actions"]]
        self.assertEqual(labels, ["점검하기", "나중에", "다시 묻지 않기"])

    def test_open_bait_is_not_neglect(self) -> None:
        self.assertEqual(
            classify_motif_status(intentionally_open=True, status_hint="방치 의심"),
            "열어둔 복선",
        )
        self.assertEqual(
            classify_motif_status(intentionally_open=False, status_hint="방치 의심"),
            "방치 의심",
        )

    def test_verify_limit_constant(self) -> None:
        self.assertEqual(MIDCHECK_VERIFY_LIMIT, 5)

    def test_light_work_skips_summary_only_note(self) -> None:
        pid, _scene_ids = self._long_project(4)
        from feedback_pipeline.literature_settings_write import add_bait_row

        open_id = add_bait_row(self.connection, pid, "개", intentionally_open=True)
        add_bait_row(self.connection, pid, "빙수", intentionally_open=False)
        units = load_literary_units(self.connection, pid)
        for unit in units:
            assembled = assemble_unit(self.connection, pid, list(unit.get("scene_ids") or []))
            save_light_unit_work(
                self.connection,
                pid,
                unit,
                unit_source_hash(assembled),
                {
                    "new_events": [f"{unit['ord']+1}장 사건"],
                    "character_changes": ["형식 동요"],
                    "motifs_new": [],
                    "motifs_returned": [],
                    "baits_resolved": [],
                    "one_line": f"{unit['ord']+1}장 한 줄",
                },
                model="test",
            )
        self.connection.commit()
        prepared = prepare_midcheck_run(
            self.connection,
            pid,
            start_ord=None,
            end_ord=None,
            model="claude-sonnet-5",
            prompt_version="literature-midcheck-v0.2",
            params={"usage": {"stages": [], "cost_usd": 0.0}},
        )
        needs = [
            {
                "claim": "5장에서 누구와 무슨 대화가 오갔는지 불확실하다",
                "reason": "확인",
                "unit_nos": [5],
                "keywords": ["영채"],
            },
            *[
                {
                    "claim": f"{i}장 복선",
                    "reason": "확인",
                    "unit_nos": [i],
                    "keywords": ["영채"],
                }
                for i in range(1, 5)
            ],
        ]
        fake = FakeClaude(
            [
                {
                    "reading": {
                        "body": "개화기 사랑 이야기다. 5장에서 누구와 무슨 대화가 오갔는지 불확실하다.",
                        "intent_gap": "",
                    },
                    "role_map": [
                        {"unit_no": i, "role": "진행", "similar_streak": False, "blurry": True}
                        for i in range(1, 5)
                    ],
                    "character_arcs": [{"name": "이형식", "change": "1~4장 변화", "stalled": ""}],
                    "motifs": [
                        {
                            "id": open_id,
                            "name": "개",
                            "first_unit": 1,
                            "last_unit": 4,
                            "status": "방치 의심",
                            "intentionally_open": True,
                            "in_settings": True,
                        }
                    ],
                    "patterns": ["대화 장면 반복"],
                    "pacing": [{"units": "2~3장", "note": "늘어짐"}],
                    "revisions": ["2~3장을 합치는 것을 고려"],
                    "needs_verify": needs,
                },
                {
                    "decision": "revise",
                    "revised_claim": "5장은 영채와 형식의 회포 장면이다.",
                    "note": "수정",
                },
                *[{"decision": "keep", "revised_claim": "", "note": "유지"} for _ in range(4)],
                {
                    "body": "개화기 사랑 이야기다. 5장은 영채와 형식의 회포 장면이다.",
                    "intent_gap": "",
                },
                {
                    "items": [
                        {
                            "claim": "5장에서 누구와 무슨 대화가 오갔는지 불확실하다",
                            "still_present": False,
                            "note": "반영됨",
                            "leftover_sentence": "",
                        }
                    ]
                },
            ]
        )
        report = run_literature_midcheck(
            self.connection,
            pid,
            prepared["run_id"],
            {"max_cost": 2, "autocommit": True, "verify_limit": 5},
            claude=fake,
        )
        self.assertEqual(report["summary_only_count"], 0)
        self.assertFalse(report.get("top_notes"))
        self.assertEqual(report["motifs"][0]["status"], "열어둔 복선")
        self.assertEqual(report["blurry_count"], 0)
        self.assertNotIn("불확실", report["reading"]["body"])
        self.assertIn("회포", report["reading"]["body"])
        md = render_midcheck_md(report)
        self.assertNotIn("summary_only", md)
        self.assertNotIn("0.", md)

    def test_leftover_sentence_rewrite_then_drop(self) -> None:
        pid, _ = self._long_project(4)
        from feedback_pipeline.literature_settings_write import add_bait_row

        open_id = add_bait_row(self.connection, pid, "개", intentionally_open=True)
        units = load_literary_units(self.connection, pid)
        for unit in units:
            assembled = assemble_unit(self.connection, pid, list(unit.get("scene_ids") or []))
            save_light_unit_work(
                self.connection,
                pid,
                unit,
                unit_source_hash(assembled),
                {
                    "new_events": [f"{unit['ord']+1}장 사건"],
                    "character_changes": ["형식 동요"],
                    "motifs_new": [],
                    "motifs_returned": [],
                    "baits_resolved": [],
                    "one_line": f"{unit['ord']+1}장 한 줄",
                },
                model="test",
            )
        self.connection.commit()
        prepared = prepare_midcheck_run(
            self.connection,
            pid,
            start_ord=None,
            end_ord=None,
            model="claude-sonnet-5",
            prompt_version="literature-midcheck-v0.3",
            params={"usage": {"stages": [], "cost_usd": 0.0}},
        )
        needs = [
            {
                "claim": "월향이 영채와 동일 인물로 확정되는지",
                "reason": "확인",
                "unit_nos": [4],
                "keywords": ["월향"],
            },
            *[
                {
                    "claim": f"{i}장 복선",
                    "reason": "확인",
                    "unit_nos": [i],
                    "keywords": ["영채"],
                }
                for i in range(1, 4)
            ],
        ]
        fake = FakeClaude(
            [
                {
                    "reading": {
                        "body": "형식이 월향을 영채로 추정한다. 화류촌에서 영채와 재회한다.",
                        "intent_gap": "",
                    },
                    "role_map": [
                        {"unit_no": i, "role": "진행", "similar_streak": False, "blurry": False}
                        for i in range(1, 5)
                    ],
                    "character_arcs": [{"name": "이형식", "change": "1~4장 변화", "stalled": ""}],
                    "motifs": [
                        {
                            "id": open_id,
                            "name": "개",
                            "first_unit": 1,
                            "last_unit": 4,
                            "status": "진행 중",
                            "intentionally_open": True,
                            "in_settings": True,
                        }
                    ],
                    "patterns": [],
                    "pacing": [],
                    "revisions": [
                        "영채 시점을 보강한다",
                        "천 원 모티프를 설정집에 등록해 진행을 명시화한다",
                    ],
                    "needs_verify": needs,
                },
                {
                    "decision": "revise",
                    "revised_claim": "월향=영채는 추측만 제시된다",
                    "note": "확정 없음",
                },
                *[{"decision": "keep", "revised_claim": "", "note": "유지"} for _ in range(3)],
                {
                    "body": "형식이 월향을 영채로 추정한다. 화류촌에서 영채와 재회한다.",
                    "intent_gap": "",
                },
                {
                    "items": [
                        {
                            "claim": "월향이 영채와 동일 인물로 확정되는지",
                            "still_present": True,
                            "note": "재회한다 단정",
                            "leftover_sentence": "화류촌에서 영채와 재회한다.",
                        }
                    ]
                },
                {
                    "body": "형식이 월향을 영채로 추정한다. 화류촌에서 영채와 재회한다.",
                    "intent_gap": "",
                },
                {
                    "items": [
                        {
                            "claim": "월향이 영채와 동일 인물로 확정되는지",
                            "still_present": True,
                            "note": "여전히 단정",
                            "leftover_sentence": "화류촌에서 영채와 재회한다.",
                        }
                    ]
                },
            ]
        )
        report = run_literature_midcheck(
            self.connection,
            pid,
            prepared["run_id"],
            {"max_cost": 2, "autocommit": True, "verify_limit": 5},
            claude=fake,
        )
        body = report["reading"]["body"]
        self.assertNotIn("재회한다", body)
        self.assertIn("추정한다", body)
        actions = [item["action"] for item in report.get("verification_log") or []]
        self.assertIn("leftover_sentence_rewrite", actions)
        self.assertIn("leftover_sentence_drop", actions)
        self.assertIn("drop_tool_revision", actions)
        self.assertTrue(all("설정집" not in x for x in report.get("revisions") or []))

    def test_drop_sentence_and_tool_revision_filter(self) -> None:
        body = drop_sentence_from_body(
            "앞. 화류촌에서 영채와 재회한다. 뒤.",
            "화류촌에서 영채와 재회한다.",
        )
        self.assertNotIn("재회한다", body)
        kept, log = filter_manuscript_revisions(
            [
                "7~9장을 합친다",
                "천 원 모티프를 설정집에 등록해 명시화한다",
            ]
        )
        self.assertEqual(kept, ["7~9장을 합친다"])
        self.assertEqual(log[0]["action"], "drop_tool_revision")

    def test_select_units_and_soften(self) -> None:
        units = [{"ord": i, "id": i + 1, "title": f"{i+1}장", "scene_ids": [i + 1]} for i in range(5)]
        picked = select_units(units, start_ord=1, end_ord=3)
        self.assertEqual([u["ord"] for u in picked], [1, 2, 3])
        text, log = soften_unverified_unit_claims(
            "9장에서 복선이 회수된다.",
            known_unit_nos={1, 2, 3},
            evidence="1장 요약",
        )
        self.assertIn("뒷부분", text)
        self.assertNotIn("맞을까요", text)
        self.assertFalse(log)

    def test_jargon_and_range_rewrite(self) -> None:
        self.assertNotIn("summary_only", scrub_system_jargon("4장은 summary_only 처리다"))
        self.assertEqual(
            rewrite_out_of_range_units("10장에서 끝난다", min_unit=4, max_unit=7),
            "뒷부분에서 끝난다",
        )
        self.assertEqual(
            reading_section_title({"range": {"whole": False, "start_unit_no": 4, "end_unit_no": 7}}),
            "토리가 읽은 4~7장",
        )

    def test_apply_verify_revisions(self) -> None:
        body, log = apply_verify_revisions(
            "5장에서 누구와 무슨 대화가 오갔는지 불확실하다. 다른 문장.",
            [
                {
                    "decision": "revise",
                    "claim": "5장에서 누구와 무슨 대화가 오갔는지 불확실하다",
                    "revised_claim": "5장은 영채와 형식의 회포 장면이다",
                }
            ],
        )
        self.assertIn("회포", body)
        self.assertNotIn("불확실", body)
        self.assertTrue(log)

    def test_long_quote_not_counted_as_dialogue(self) -> None:
        short = '그가 말했다. "안녕." 그리고 갔다.'
        long_inner = "가" * (LONG_QUOTE_MONOLOGUE_CHARS + 20)
        long = f'그녀가 말했다. "{long_inner}" 끝.'
        short_m = count_dialogue_chars(short)
        long_m = count_dialogue_chars(long)
        self.assertGreater(short_m["dialogue_chars"], 0)
        self.assertEqual(long_m["dialogue_chars"], 0)
        self.assertGreater(long_m["monologue_chars"], LONG_QUOTE_MONOLOGUE_CHARS)
        # 문단 전체가 아니라 따옴표 안만
        paras = [{"n": 1, "text": long, "scene_id": 1}]
        signals = collect_signals(paras, spell_error_count=0, contest={})
        self.assertLess(signals["dialogue_ratio"], 0.2)
        self.assertEqual(dialogue_ratio_label(0.05), "대화가 거의 없는 장")


if __name__ == "__main__":
    unittest.main()
