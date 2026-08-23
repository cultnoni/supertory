# -*- coding: utf-8 -*-
"""Cluster prompt pipelines: structure split without content drift."""

from __future__ import annotations

import unittest
from pathlib import Path

import app
import prompt_pipelines
import success_pattern


SAMPLE = "비 오는 골목에서 서연은 우산을 고쳐 쥐었다. 묵연이 담담히 말했다."


class PromptPipelineIdTests(unittest.TestCase):
    def test_only_genre_literature_uses_separate_pipeline(self) -> None:
        self.assertEqual(prompt_pipelines.prompt_pipeline_id("genre_literature"), "genre_literature")
        self.assertEqual(prompt_pipelines.prompt_pipeline_id("webnovel"), "webnovel")
        self.assertEqual(prompt_pipelines.prompt_pipeline_id("general_literature"), "webnovel")
        self.assertEqual(prompt_pipelines.prompt_pipeline_id(""), "webnovel")


class PromptCopyIdentityTests(unittest.TestCase):
    """Until the genre-lit tuning pass, copies must match webnovel text exactly."""

    def test_js_has_webnovel_and_genre_lit_builders(self) -> None:
        js = Path("web/app.js").read_text(encoding="utf-8")
        for name in (
            "buildDetailedSceneSummaryPrompt",
            "buildContinuePrompt",
            "buildRewritePrompt",
            "buildFocusedAnalysisPrompt",
            "buildNextIdeaPrompt",
            "buildBrainstormPrompt",
            "buildSettingBreakScanPrompt",
            "buildWorldDescriptionPrompt",
            "buildSubmissionSynopsisPrompt",
            "buildTensionCurvePrompt",
            "buildDescriptionExpandPrompt",
        ):
            self.assertIn(f"function {name}_Webnovel(", js)
            self.assertIn(f"function {name}_GenreLit(", js)
            self.assertIn(f"function {name}(", js)

    def test_python_task_prompts_match_across_clusters(self) -> None:
        cases = [
            (
                "summary",
                lambda cid: app.SuperToryHandler._build_detailed_scene_summary_prompt(SAMPLE, cluster_id=cid),
            ),
            (
                "continue",
                lambda cid: app.SuperToryHandler._build_continue_prompt(SAMPLE, "short", "", "", cluster_id=cid),
            ),
            (
                "rewrite",
                lambda cid: app.SuperToryHandler._build_rewrite_prompt(SAMPLE, "", "", "", cluster_id=cid),
            ),
            (
                "analyze",
                lambda cid: app.SuperToryHandler._build_focused_analysis_prompt(SAMPLE, cluster_id=cid),
            ),
            (
                "ideas",
                lambda cid: app.SuperToryHandler._build_next_idea_prompt(SAMPLE, cluster_id=cid),
            ),
            (
                "brainstorm",
                lambda cid: app.SuperToryHandler._build_brainstorm_prompt(SAMPLE, "", cluster_id=cid),
            ),
            (
                "worldscan",
                lambda cid: app.SuperToryHandler._build_setting_break_scan_prompt(SAMPLE, cluster_id=cid),
            ),
            (
                "worlddesc",
                lambda cid: app.SuperToryHandler._build_world_description_prompt("왕궁", SAMPLE, cluster_id=cid),
            ),
            (
                "temphook",
                lambda cid: app.SuperToryHandler._build_tension_curve_prompt(SAMPLE, cluster_id=cid),
            ),
            (
                "styleblend",
                lambda cid: app.SuperToryHandler._build_style_blend_check_prompt(SAMPLE, SAMPLE, cluster_id=cid),
            ),
            (
                "descexpand",
                lambda cid: app.SuperToryHandler._build_description_expand_prompt(SAMPLE, "", "", "", cluster_id=cid),
            ),
            (
                "subsynopsis",
                lambda cid: app.SuperToryHandler._build_submission_synopsis_prompt("줄거리", None, None, cluster_id=cid),
            ),
        ]
        for label, builder in cases:
            web = builder("webnovel")
            lit = builder("genre_literature")
            default = builder("")
            self.assertEqual(web, lit, msg=label)
            self.assertEqual(default, web, msg=f"default drift {label}")
            self.assertIn("[현재 작업]", web)

    def test_continue_webnovel_body_unchanged_from_named_copy(self) -> None:
        web = app.SuperToryHandler._build_continue_prompt(SAMPLE, "short", "", "")
        self.assertEqual(
            web,
            app.SuperToryHandler._build_continue_prompt_webnovel(SAMPLE, "short", "", ""),
        )

    def test_success_pattern_copies_match(self) -> None:
        web = success_pattern.build_structural_observation_prompt(SAMPLE, cluster_id="webnovel")
        lit = success_pattern.build_structural_observation_prompt(SAMPLE, cluster_id="genre_literature")
        self.assertEqual(web, lit)
        self.assertEqual(web, success_pattern.build_structural_observation_prompt(SAMPLE))


if __name__ == "__main__":
    unittest.main()
