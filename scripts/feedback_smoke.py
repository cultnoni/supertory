"""첨삭 피드백 API 연막. 실제 사용자 DB는 건드리지 않는다."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app
import feedback_api
from feedback_pipeline.claude_client import (
    is_configured,
    set_client_for_tests,
    set_configured_for_tests,
)
from feedback_pipeline.cli import _cli_fake


def _request(port: int, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
    import http.client

    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
    response = connection.getresponse()
    raw = response.read().decode("utf-8")
    connection.close()
    return response.status, json.loads(raw) if raw else {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="첨삭 피드백 API 연막 (임시 DB)")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-cost", type=float, default=0.5)
    parser.add_argument(
        "--manuscript",
        default=str(ROOT / "tools" / "prompt_lab" / "manuscripts" / "ep2x.md"),
    )
    args = parser.parse_args(argv)

    if args.live and not is_configured():
        print("ANTHROPIC_API_KEY가 없어 실제 호출을 건너뜁니다.", file=sys.stderr)
        return 2

    manuscript = Path(args.manuscript)
    if not manuscript.is_file():
        print(f"원고가 없습니다: {manuscript}", file=sys.stderr)
        return 1
    content = manuscript.read_text(encoding="utf-8")
    from html import escape as html_escape
    from feedback_pipeline.paragraphs import paragraphs_from_text

    html_parts: list[str] = []
    for para in paragraphs_from_text(content):
        text = html_escape(para["text"])
        kind = para["type"]
        if kind == "divider":
            html_parts.append(f'<div class="manuscript-scene-break">{text}</div>')
        elif kind == "other":
            html_parts.append(f"<h2>{text}</h2>")
        else:
            html_parts.append(f"<p>{text.replace(chr(10), '<br>')}</p>")
    content = "".join(html_parts)

    temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    original_data = app.DATA_DIR
    original_db = app.DATABASE_PATH
    server = None
    thread = None
    try:
        app.DATA_DIR = Path(temp.name) / "data"
        app.DATABASE_PATH = app.DATA_DIR / "supertory.sqlite3"
        app.initialise_database()
        feedback_api.interrupt_stale_runs()
        feedback_api.reset_runtime_for_tests()
        if args.live:
            set_client_for_tests(None)
            set_configured_for_tests(None)
        else:
            set_configured_for_tests(True)
            set_client_for_tests(_cli_fake())

        server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.SuperToryHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_port

        st, project = _request(port, "POST", "/api/projects", {"title": "연막", "main_genre": "웹소설"})
        if st != 201:
            print(f"프로젝트 생성 실패: {st} {project}", file=sys.stderr)
            return 1
        project_id = int(project["id"])
        st, chapter = _request(port, "POST", f"/api/projects/{project_id}/chapters", {"title": "1장"})
        st, scene = _request(port, "POST", f"/api/chapters/{chapter['id']}/scenes", {"title": "2화"})
        st, detail = _request(port, "GET", f"/api/scenes/{scene['id']}")
        st, _saved = _request(
            port,
            "PUT",
            f"/api/scenes/{scene['id']}",
            {
                "title": "2화",
                "content_md": content,
                "status": "draft",
                "row_version": detail.get("row_version") or 1,
            },
        )
        st, created = _request(
            port,
            "POST",
            f"/api/projects/{project_id}/feedback/runs",
            {
                "scene_id": scene["id"],
                "explanation_lens": "strong",
                "max_cost": float(args.max_cost),
            },
        )
        if st == 429:
            print("429: 호출 한도입니다. 여기서 멈춥니다.", file=sys.stderr)
            return 3
        if st not in {200, 201}:
            print(f"실행 생성 실패: {st} {created}", file=sys.stderr)
            return 1
        run_id = int(created["run_id"])
        print(f"run_id={run_id} status={created.get('status')} paragraphs={created.get('paragraph_count')}")
        deadline = time.time() + 180
        run = created
        while time.time() < deadline:
            st, run = _request(port, "GET", f"/api/feedback/runs/{run_id}")
            if st != 200:
                print(f"폴링 실패: {st} {run}", file=sys.stderr)
                return 1
            status = str(run.get("status") or "")
            progress = run.get("progress") or {}
            print(f"  {status} stage={progress.get('stage')} cards={len(run.get('cards') or [])}")
            if status != "running":
                break
            time.sleep(1.0)
        usage = run.get("usage") or {}
        print(f"최종 status={run.get('status')} 비용=${float(usage.get('cost_usd') or 0):.4f}")
        print(f"토큰 in={usage.get('input_tokens')} out={usage.get('output_tokens')}")
        report = run.get("report") if isinstance(run.get("report"), dict) else {}
        print("--- 리포트 요약 ---")
        print(str(report.get("summary") or run.get("report_md") or "")[:800])
        print("--- 카드 ---")
        counts = {"high": 0, "medium": 0, "low": 0, "ref": 0}
        for card in run.get("cards") or []:
            pri = str(card.get("priority") or "")
            if pri in counts:
                counts[pri] += 1
            print(
                f"- {card.get('id')} P{card.get('start_para')}~P{card.get('end_para')} "
                f"{card.get('kind')}/{card.get('style_type')} priority={pri} "
                f"status={card.get('status')}"
            )
        print(f"단계별 개수: {counts} (총 {len(run.get('cards') or [])})")
        return 0
    finally:
        set_client_for_tests(None)
        set_configured_for_tests(None)
        if server is not None:
            server.shutdown()
        if thread is not None:
            thread.join(timeout=5)
            server.server_close()
        app.DATA_DIR = original_data
        app.DATABASE_PATH = original_db
        temp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
