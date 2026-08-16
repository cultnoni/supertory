"""서사 템포 & 훅 분석기 (mode=temphook) — 원문만, 인덱스 없음."""

from __future__ import annotations

import http.client
import json
import re
import tempfile
import threading
import unittest
from pathlib import Path

import app
import gemini_client

ROOT = Path(__file__).resolve().parents[1]

MIXED_SCENE = """
아침 햇살이 창틀을 더듬었다. 서연은 찻잔을 두 손으로 감싸고 어제 산 콩나물을
다듬었다. 묵연은 마루에 앉아 칼날을 헝겊으로 닦으며 별말 없이 고개를 끄덕였다.
이웃집 아이가 골목을 뛰어가는 소리가 잠깐 스쳤다.

주막 주인이 국을 내왔다. "오늘은 손님이 적네." 서연은 짧게 대답하고 국을 저었다.
창밖으로는 장터가 천천히 열리고 있었다. 먼지가 빛 속에서 떠다녔다.

그런데 문이 열리기도 전에 비명과 쇠붙이 소리가 겹쳤다. 검은 옷의 사내가
식탁을 걷어찼고, 묵연의 칼이 허공을 갈랐다. 서연은 주전자를 집어 문을
막아서며 뒤로 물러섰다. 피가 마루에 튀었다.

사내의 목에서 봉인된 쪽지가 떨어졌다. 「네가 죽인 마왕의 핏줄이 아직 살아 있다.」
묵연이 쪽지를 집으려는 순간, 바깥에서 두번째 칼날이 창문을 꿰뚫고 들어왔다.

해가 기울자 주막은 다시 조용했다. 서연은 깨진 찻잔 조각을 쓸어 모으며
"내일 장도 봐야 하는데" 하고 중얼거렸다. 묵연은 대답하지 않았다.
창가의 쪽지는 이미 바람에 뒤집혀 있었다.
""".strip()

WEAK_ENDING = """
서연은 찻잔을 내려놓았다.

묵연이 고개를 끄덕였다.

두 사람은 주막 문을 닫고 각자의 방으로 올라갔다.
""".strip()


def _extract_json(text: str) -> dict | None:
    source = str(text or "")
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", source, re.I)
    body = fenced.group(1) if fenced else source
    start = body.find("{")
    end = body.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        return json.loads(body[start:end + 1])
    except json.JSONDecodeError:
        return None


class TempoHookTests(unittest.TestCase):
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
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=180)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    def test_ui_entry_is_confirm_category_not_viewer(self) -> None:
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        check_i = html.find('label="토리와 확인해요"')
        plot_i = html.find('value="plottwist"')
        tempo_i = html.find('value="temphook"')
        world_i = html.find('value="worldscan"')
        help_i = html.find('label="토리와 구상해요"')
        self.assertGreater(check_i, 0)
        self.assertGreater(plot_i, check_i)
        self.assertGreater(tempo_i, plot_i)
        self.assertGreater(world_i, tempo_i)
        self.assertGreater(help_i, world_i)
        self.assertIn("서사 템포", html[tempo_i:tempo_i + 80])
        self.assertIn('id="tempoHookTargetModal"', html)
        self.assertIn('id="tempoHookTargetConfirm"', html)
        self.assertIn("function openTempoHookTargetModal", app_js)
        self.assertIn("function openTempoHookModal", app_js)
        self.assertIn("function confirmTempoHookTarget", app_js)
        self.assertIn("AI_DEDICATED_TARGET_MODAL_MODES", app_js)
        self.assertIn("openTempoHookTargetModal()", app_js)
        target_fn = app_js.find("function openTempoHookTargetModal")
        run_fn = app_js.find("async function runTempoHookAnalysis")
        self.assertGreater(target_fn, 0)
        self.assertGreater(run_fn, 0)
        self.assertNotIn("scene.status", app_js[target_fn:target_fn + 900])
        self.assertNotIn("scene.status", app_js[run_fn:run_fn + 1200])

    def test_prompt_contract_no_index_no_core_identity(self) -> None:
        curve = app.SuperToryHandler._build_tension_curve_prompt(MIXED_SCENE)
        score = app.SuperToryHandler._build_cliffhanger_score_prompt("마지막 문단")
        rewrite = app.SuperToryHandler._build_ending_rewrite_prompt("마지막 문단", "훅이 약함")
        for prompt in (curve, score, rewrite):
            self.assertIn("[현재 작업]", prompt)
            self.assertNotIn("Core Identity", prompt)
            self.assertNotIn("프로젝트 누적 정보", prompt)
        self.assertIn("점수를 중간에 몰아주지 않는다", curve)
        self.assertIn("다음 화를 보고 싶게 만드는 힘", score)
        self.assertIn("즉각적 위기 노출형", rewrite)
        self.assertIn("정보 공백형", rewrite)
        self.assertIn("감정 절정형", rewrite)

    def test_dry_run_uses_task_prompt_without_index(self) -> None:
        task = app.SuperToryHandler._build_tension_curve_prompt(MIXED_SCENE)
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "temphook",
                "dry_run": True,
                "temphook_kind": "curve",
                "project_title": "템포",
                "scene_title": "1화",
                "scene_content": MIXED_SCENE,
                "task_prompt": task,
            },
        )
        self.assertEqual(status, 200, result)
        full = result.get("full_prompt") or ""
        self.assertIn("점수를 중간에 몰아주지 않는다", full)
        self.assertNotIn("[프로젝트 누적 정보", full)
        self.assertNotIn("현재 원고:\n", full)

    def test_replace_last_three_paragraphs_only(self) -> None:
        source = "하나\n\n둘\n\n셋\n\n넷\n\n다섯"
        replacement = "새1\n\n새2\n\n새3"
        out = app.SuperToryHandler._replace_last_three_paragraphs(source, replacement)
        self.assertEqual(out, "하나\n\n둘\n\n새1\n\n새2\n\n새3")
        kept = app.SuperToryHandler._split_plain_paragraphs(out)
        self.assertEqual(kept[:2], ["하나", "둘"])
        self.assertEqual(kept[-3:], ["새1", "새2", "새3"])

    def test_segment_position_pct_from_index(self) -> None:
        segs = [{"segment_index": i, "score": i} for i in range(1, 9)]
        n = len(segs)
        pcts = [round(item["segment_index"] / n * 100) for item in segs]
        self.assertEqual(pcts[-1], 100)
        self.assertTrue(pcts == sorted(pcts))
        self.assertGreater(pcts[0], 0)
        self.assertLess(pcts[0], pcts[-1])

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_curve_has_variation(self) -> None:
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "temphook",
                "temphook_kind": "curve",
                "project_title": "템포",
                "scene_title": "3화",
                "scene_content": MIXED_SCENE,
                "task_prompt": app.SuperToryHandler._build_tension_curve_prompt(MIXED_SCENE),
            },
        )
        self.assertEqual(status, 200, result)
        data = _extract_json(result.get("text") or "")
        self.assertTrue(data, result.get("text"))
        segs = data.get("segments") or []
        self.assertGreaterEqual(len(segs), 6, segs)
        self.assertLessEqual(len(segs), 12, segs)
        scores = [float(item.get("score")) for item in segs]
        self.assertGreaterEqual(max(scores) - min(scores), 3, scores)
        mid = {7, 8}
        self.assertFalse(all(int(s) in mid for s in scores), scores)

    @unittest.skipUnless(gemini_client.is_configured(), "Gemini API key not configured")
    def test_live_weak_ending_three_styles(self) -> None:
        status, result = self.request(
            "POST",
            "/api/ai/assist",
            {
                "mode": "temphook",
                "temphook_kind": "rewrite",
                "project_title": "템포",
                "scene_title": "3화",
                "scene_content": WEAK_ENDING,
                "last_three_paragraphs": WEAK_ENDING,
                "cliffhanger_reason": "장면이 그냥 마무리되어 다음 화가 급하지 않음",
                "task_prompt": app.SuperToryHandler._build_ending_rewrite_prompt(
                    WEAK_ENDING,
                    "장면이 그냥 마무리되어 다음 화가 급하지 않음",
                ),
            },
        )
        self.assertEqual(status, 200, result)
        text = result.get("text") or ""
        self.assertIn("즉각적 위기 노출형", text)
        self.assertIn("정보 공백형", text)
        self.assertIn("감정 절정형", text)
        versions = re.findall(r"##\s*버전\s*\d+\s*[:：]\s*([^\n]+)", text)
        self.assertGreaterEqual(len(versions), 3, text)
        bodies = re.split(r"##\s*버전\s*\d+\s*[:：][^\n]+\n", text)[1:]
        bodies = [part.strip() for part in bodies if part.strip()]
        self.assertGreaterEqual(len(bodies), 3, text)
        self.assertGreaterEqual(len(set(bodies[:3])), 3, bodies[:3])
