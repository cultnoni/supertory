"""명령줄 실행. 앱의 실제 DB는 건드리지 않는다."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app
import feedback_store
from feedback_pipeline.claude_client import FakeClaude, LiveClaude
from feedback_pipeline.paragraphs import paragraphs_from_text
from feedback_pipeline.runner import run_feedback


def _parse_project_ref(raw: str) -> tuple[Path, str]:
    if ":" not in raw:
        raise ValueError("--project-json은 경로:프로젝트키 형식입니다.")
    path_text, key = raw.rsplit(":", 1)
    path = Path(path_text)
    if not path.is_file():
        raise FileNotFoundError(f"프로젝트 JSON이 없습니다: {path}")
    return path, key.strip()


def _load_project(path: Path, key: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    projects = payload.get("projects") or {}
    project = projects.get(key)
    if not isinstance(project, dict):
        raise KeyError(f"프로젝트 키가 없습니다: {key}")
    return project


def _seed_work(conn, project: dict[str, Any], manuscript: str, scene_title: str) -> tuple[int, int]:
    cursor = conn.execute(
        "INSERT INTO project(title, main_genre, sub_genre) VALUES (?, ?, ?)",
        (
            str(project.get("title") or "첨삭 시험"),
            "문학" if str(project.get("explanation_lens") or "") == "strong" else "웹소설",
            str(project.get("genre") or "로맨스")[:80],
        ),
    )
    project_id = int(cursor.lastrowid)
    chapter_id = int(
        conn.execute(
            "INSERT INTO chapter(project_id, title, sort_order) VALUES (?, '1장', 0)",
            (project_id,),
        ).lastrowid
    )
    scene_id = int(
        conn.execute(
            "INSERT INTO scene(project_id, chapter_id, title, sort_order) VALUES (?, ?, ?, 0)",
            (project_id, chapter_id, scene_title or "1화"),
        ).lastrowid
    )
    conn.execute(
        "INSERT INTO scene_revision(scene_id, revision_no, content_md, word_count, is_current) "
        "VALUES (?, 1, ?, ?, 1)",
        (scene_id, manuscript, len(manuscript)),
    )
    for index, person in enumerate(project.get("characters") or []):
        if not isinstance(person, dict):
            continue
        name = str(person.get("name") or "").strip()
        if not name:
            continue
        note = str(person.get("note") or "").strip()
        cid = int(
            conn.execute(
                "INSERT INTO character(project_id, name, short_description, sort_order) "
                "VALUES (?, ?, ?, ?)",
                (project_id, name, note, index),
            ).lastrowid
        )
        conn.execute(
            "INSERT INTO character_alias(character_id, project_id, alias) VALUES (?, ?, ?)",
            (cid, project_id, name),
        )
    facts = [str(x).strip() for x in (project.get("tracked_facts") or []) if str(x).strip()]
    if facts:
        conn.execute(
            "INSERT OR REPLACE INTO project_index(project_id, tracked_facts_json) VALUES (?, ?)",
            (project_id, json.dumps(facts, ensure_ascii=False)),
        )
    conn.commit()
    return project_id, scene_id


def _cli_fake() -> FakeClaude:
    consistency = {
        "facts": [],
        "issues": [],
    }
    report = {
        "summary": "시험용 고정 리포트입니다. 큰 문제는 없습니다.",
        "rules": [],
        "scores": [{"item": "몰입", "score": 4, "comment": "무난합니다."}],
        "strengths": [],
        "weaknesses": [
            {
                "id": "W1",
                "title": "시험 약점",
                "body": "고정 응답 약점입니다.",
                "type": "explain_less",
                "fixable": "sentence",
                "impact": 3,
                "certainty": "sure",
                "perspectives": ["editor"],
                "range": None,
            }
        ],
        "consistency": [],
    }
    card = {
        "target_id": "W1",
        "kind": "style",
        "edit_plan": "고정",
        "reason": "시험용 이유입니다. 표현을 조금 줄일 수 있습니다.",
        "suggestion": None,
        "added_facts": [],
        "removed_facts": [],
        "confidence": "medium",
    }
    return FakeClaude(responses=[consistency, report, card])


def _print_summary(run: dict[str, Any]) -> None:
    print(f"run_id={run.get('id')} status={run.get('status')} model={run.get('model')}")
    params = run.get("params") or {}
    usage = params.get("usage") or {}
    print(f"비용 추정: ${float(usage.get('cost_usd') or 0):.4f}")
    print(f"토큰: in={usage.get('input_tokens')} out={usage.get('output_tokens')}")
    summary = ""
    report = run.get("report") if isinstance(run.get("report"), dict) else None
    if report:
        summary = str(report.get("summary") or "")
    if not summary:
        summary = str(run.get("report_md") or "")[:400]
    print("--- 리포트 요약 ---")
    print(summary or "(없음)")
    print("--- 카드 ---")
    counts = {"high": 0, "medium": 0, "low": 0, "ref": 0}
    for card in run.get("cards") or []:
        pri = str(card.get("priority") or "")
        if pri in counts:
            counts[pri] += 1
        loc = f"P{card.get('start_para')}~P{card.get('end_para')}"
        warns = card.get("warnings") or card.get("warnings_json") or []
        print(
            f"- {card.get('id')} {loc} {card.get('kind')}/{card.get('style_type')} "
            f"priority={pri} status={card.get('status')} warnings={warns}"
        )
    print(f"단계별 개수: {counts} (총 {len(run.get('cards') or [])})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="첨삭 피드백 파이프라인 (임시 DB)")
    parser.add_argument("--manuscript", required=True)
    parser.add_argument("--project-json", required=True)
    parser.add_argument("--db", default="")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-cost", type=float, default=1.0)
    args = parser.parse_args(argv)

    ms_path = Path(args.manuscript)
    if not ms_path.is_file():
        print(f"원고 파일이 없습니다: {ms_path}", file=sys.stderr)
        return 1
    project_path, project_key = _parse_project_ref(args.project_json)
    project = _load_project(project_path, project_key)
    manuscript = ms_path.read_text(encoding="utf-8")

    temp_dir = None
    if args.db:
        db_path = Path(args.db)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        data_dir = db_path.parent
    else:
        temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        data_dir = Path(temp_dir.name) / "data"
        db_path = data_dir / "supertory.sqlite3"

    original_data = app.DATA_DIR
    original_db = app.DATABASE_PATH
    try:
        app.DATA_DIR = data_dir
        app.DATABASE_PATH = db_path
        app.initialise_database()
        conn = app.connect()
        try:
            project_id, scene_id = _seed_work(conn, project, manuscript, ms_path.stem)
            paragraphs = paragraphs_from_text(manuscript)
            claude = LiveClaude() if args.live else _cli_fake()
            run_id = run_feedback(
                conn,
                project_id,
                scene_id,
                paragraphs,
                {
                    "scene_title": ms_path.stem,
                    "revision_no": 1,
                    "explanation_lens": project.get("explanation_lens"),
                    "max_cost": float(args.max_cost),
                },
                claude=claude,
            )
            conn.commit()
            run = feedback_store.get_run(conn, run_id)
            if run is None:
                print("실행 결과를 읽지 못했습니다.", file=sys.stderr)
                return 1
            _print_summary(run)
            return 0
        finally:
            conn.close()
    finally:
        app.DATA_DIR = original_data
        app.DATABASE_PATH = original_db
        if temp_dir is not None:
            temp_dir.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
