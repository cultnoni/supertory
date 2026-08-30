"""Smoke: every 도우미 dropdown mode is wired (UI + dry_run API)."""

from __future__ import annotations

import http.client
import json
import re
import tempfile
import threading
import unittest
from pathlib import Path

import app

ROOT = Path(__file__).resolve().parents[1]

HELPER_MODES = [
    "free",
    "summarize",
    "foreshadow",
    "plottwist",
    "worldscan",
    "dupcheck",
    "temphook",
    "ideas",
    "brainstorm",
    "analyze",
    "continue",
    "rewrite",
    "worlddesc",
    "subsynopsis",
]

SCENE = (
    "서연은 문을 밀고 들어갔다. 묵연이 뒤를 이었다. "
    "등잔 연기가 코끝을 스쳤다. 멀리서 북소리가 한 번 울렸다."
)

FORESHADOW = {
    "title": "북쪽 탑의 불빛",
    "target": "12장",
    "buildup": ["1장에서 붉은 불빛을 본다", "3장에서 탑을 언급한다"],
}


class AssistModesSmokeTests(unittest.TestCase):
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
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=60)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def base_body(self, mode: str, **extra) -> dict:
        body = {
            "mode": mode,
            "dry_run": True,
            "project_title": "스모크",
            "main_genre": "판타지",
            "sub_genre": "무협",
            "main_genre_label": "판타지",
            "sub_genre_label": "무협",
            "scene_content": SCENE,
            "prompt": "",
            "user_prompt": "",
        }
        body.update(extra)
        return body

    def test_dropdown_categories_and_labels(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        # Order: free → 확인해요 → 구상해요 → 함께 써요
        free_i = html.find('value="free"')
        check_i = html.find('label="토리와 확인해요"')
        help_i = html.find('label="토리와 구상해요"')
        write_i = html.find('label="토리와 함께 써요"')
        self.assertTrue(free_i > 0 and check_i > free_i and help_i > check_i and write_i > help_i, html[free_i:write_i + 40])

        expected_labels = {
            "free": "직접 작성하기",
            "summarize": "회차 요약",
            "foreshadow": "떡밥·복선 탐색기",
            "plottwist": "반전 &amp; 개연성 검사기",
            "worldscan": "설정 붕괴 감지기",
            "dupcheck": "중복 체크",
            "temphook": "서사 템포 &amp; 훅 분석기",
            "ideas": "다음 아이디어 제안",
            "brainstorm": "브레인스토밍",
            "analyze": "피드백 요청",
            "continue": "이어서 쓰기",
            "rewrite": "글 다듬기",
            "worlddesc": "세계관 묘사",
            "subsynopsis": "투고·공모전용 시놉시스",
        }
        for mode, label in expected_labels.items():
            self.assertRegex(html, rf'value="{mode}"[^>]*>\s*{re.escape(label)}')

        # Hidden required must not remain on worldDescSubject (blocks other modes).
        self.assertNotRegex(
            html,
            r'id="worldDescSubject"[^>]*\srequired',
            "worldDescSubject required blocks form submit for other modes",
        )
        self.assertIn('id="aiForm"', html)
        self.assertIn("novalidate", html[html.find('id="aiForm"'): html.find('id="aiForm"') + 120])

        # Client routes exist
        self.assertIn('if (mode === "subsynopsis")', app_js)
        self.assertIn("function buildWorldDescriptionPrompt", app_js)
        self.assertIn("function buildDetailedSceneSummaryPrompt", app_js)
        self.assertIn("function buildTensionCurvePrompt", app_js)
        self.assertIn("function buildCliffhangerScorePrompt", app_js)
        self.assertIn("function buildEndingRewritePrompt", app_js)
        self.assertIn("function openTempoHookModal", app_js)
        self.assertIn("function openTempoHookTargetModal", app_js)
        self.assertIn('id="tempoHookTargetConfirm"', html)
        self.assertIn('value="temphook"', html)
        self.assertIn("function buildCharacterDebatePrompt", app_js)
        self.assertIn("function setCharListMode", app_js)
        self.assertNotIn('value="chardebate"', html)
        self.assertNotIn("function openCharDebateModal", app_js)
        self.assertIn("function buildNextIdeaPrompt", app_js)
        self.assertIn("function buildBrainstormPrompt", app_js)
        self.assertIn("function buildSubmissionSynopsisPrompt", app_js)
        self.assertIn("async function executeRewriteAssist", app_js)
        self.assertIn("INDEX_AWARE_ASSIST_MODES", app_js)

        for mode in HELPER_MODES:
            self.assertIn(f'"{mode}"', app_js)

    def test_dry_run_each_helper_mode(self) -> None:
        failures: list[str] = []
        for mode in HELPER_MODES:
            extra: dict = {}
            if mode == "free":
                extra = {"prompt": "이 장면을 한 문장으로 요약해 줘.", "user_prompt": "이 장면을 한 문장으로 요약해 줘."}
            elif mode == "continue":
                extra = {"length_mode": "short", "user_hint": ""}
            elif mode == "rewrite":
                extra = {
                    "scene_content": "문이 열렸다.",
                    "selected_text": "문이 열렸다.",
                    "context_before": "복도 끝에서 ",
                    "context_after": " 바람이 들어왔다.",
                }
            elif mode == "worlddesc":
                extra = {"target_subject": "왕궁 내부 묘사", "prompt": "왕궁 내부 묘사"}
            elif mode == "brainstorm":
                extra = {"user_topic": "조연 인물", "prompt": "조연 인물"}
            elif mode in {"foreshadow", "plottwist"}:
                extra = {"foreshadow": FORESHADOW}
            elif mode == "subsynopsis":
                extra = {
                    "scene_content": "",
                    "outline_summary": "주인공이 시험을 보고 왕궁에 들어가 음모를 밝혀 평화를 되찾는다.",
                    "indexed_prompt": "[프로젝트 누적 정보]\n세계관: 동양풍\n\n[작업]\n시놉시스 작성",
                }
            elif mode == "dupcheck":
                extra = {
                    "neighbor_scenes": [
                        {"index": 1, "title": "1화", "content": "등잔 연기가 코끝을 스쳤다."},
                    ],
                    "local_hits": [{"phrase": "등잔 연기", "where": "1화", "kind": "표현"}],
                }

            status, result = self.request("POST", "/api/ai/assist", self.base_body(mode, **extra))
            if status != 200:
                failures.append(f"{mode}: HTTP {status} {result}")
                continue
            full = str(result.get("full_prompt") or result.get("prompt") or result.get("text") or "")
            if mode != "subsynopsis" and mode != "free" and not full and not result.get("ok", True):
                failures.append(f"{mode}: empty dry_run payload {result}")
            # dry_run should not call Gemini — expect prompt/system fields
            if not (result.get("full_prompt") or result.get("system") or result.get("instruction") or result.get("text") is not None or result.get("dry_run")):
                # Accept any structured 200 dry_run response
                if "error" in result:
                    failures.append(f"{mode}: {result}")

        self.assertFalse(failures, "\n".join(failures))

    def _helper_extra(self, mode: str) -> dict:
        extra: dict = {}
        if mode == "free":
            extra = {"prompt": "이 장면을 한 문장으로 요약해 줘.", "user_prompt": "이 장면을 한 문장으로 요약해 줘."}
        elif mode == "continue":
            extra = {"length_mode": "short", "user_hint": ""}
        elif mode == "rewrite":
            extra = {
                "scene_content": "문이 열렸다.",
                "selected_text": "문이 열렸다.",
                "context_before": "복도 끝에서 ",
                "context_after": " 바람이 들어왔다.",
            }
        elif mode == "worlddesc":
            extra = {"target_subject": "왕궁 내부 묘사", "prompt": "왕궁 내부 묘사"}
        elif mode == "brainstorm":
            extra = {"user_topic": "조연 인물", "prompt": "조연 인물"}
        elif mode in {"foreshadow", "plottwist"}:
            extra = {"foreshadow": FORESHADOW}
        elif mode == "subsynopsis":
            extra = {
                "scene_content": "",
                "outline_summary": "주인공이 시험을 보고 왕궁에 들어가 음모를 밝혀 평화를 되찾는다.",
            }
        elif mode == "dupcheck":
            extra = {
                "neighbor_scenes": [
                    {"index": 1, "title": "1화", "content": "등잔 연기가 코끝을 스쳤다."},
                ],
                "local_hits": [{"phrase": "등잔 연기", "where": "1화", "kind": "표현"}],
            }
        return extra

    def test_genre_literature_dry_run_matches_webnovel(self) -> None:
        """Until genre-lit tuning, both pipelines must emit the same task prompt."""
        modes = list(HELPER_MODES)
        failures: list[str] = []
        for mode in modes:
            extra = self._helper_extra(mode)
            web_status, web = self.request(
                "POST", "/api/ai/assist", self.base_body(mode, cluster_id="webnovel", **extra)
            )
            lit_status, lit = self.request(
                "POST",
                "/api/ai/assist",
                self.base_body(mode, cluster_id="genre_literature", **extra),
            )
            if web_status != 200 or lit_status != 200:
                failures.append(f"{mode}: HTTP web={web_status} lit={lit_status}")
                continue
            web_full = str(web.get("full_prompt") or "")
            lit_full = str(lit.get("full_prompt") or "")
            if web_full != lit_full:
                failures.append(f"{mode}: full_prompt mismatch ({len(web_full)} vs {len(lit_full)})")
        self.assertFalse(failures, "\n".join(failures))

    def test_mode_specific_validation(self) -> None:
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            self.base_body("free", scene_content="", prompt="", user_prompt=""),
        )
        self.assertEqual(status, 400, result)

        status, result = self.request(
            "POST",
            "/api/ai/assist",
            self.base_body("worlddesc", target_subject="", prompt="", scene_content=SCENE),
        )
        self.assertEqual(status, 400, result)

        status, result = self.request(
            "POST",
            "/api/ai/assist",
            self.base_body("foreshadow", foreshadow={"title": "", "target": "", "buildup": []}),
        )
        self.assertEqual(status, 400, result)

        status, result = self.request(
            "POST",
            "/api/ai/assist",
            self.base_body("dupcheck", scene_content=""),
        )
        self.assertEqual(status, 400, result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
