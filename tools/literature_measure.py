"""문학 단편 측정 스크립트. 앱 실시간 실행과는 별도다.

예:
  python tools/literature_measure.py --work bincheo --from cards --reuse-run 9
  python tools/literature_measure.py --work bincheo --batch
  python tools/literature_measure.py --work bincheo --stage-models style=claude-haiku-4-5,verify=claude-haiku-4-5
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app
from feedback_pipeline.claude_client import BatchClaude, LiveClaude
from feedback_pipeline.config import HAIKU_45_MODEL, LITERATURE_PROMPT_VERSION
from feedback_pipeline.literature_cache import estimate_shared_prefix_cost
from feedback_pipeline.literature_reuse import load_reuse
from feedback_pipeline.literature_runner import prepare_literature_run, run_literature_short
from feedback_pipeline.literature_signals import spell_error_count
from feedback_pipeline.literature_text import assemble_manuscript, load_scene_rows
from feedback_store import get_run

DEFAULT_WORKS = {
    "bincheo": {
        "title": "빈처",
        "path": Path(r"C:\Users\cultn\AppData\Local\Temp\supertory-lit-live\bincheo.txt"),
        "intent": "가난한 부부의 일상과 아내의 헌신을 그린다.",
    },
    "ready": {
        "title": "레디메이드 인생",
        "path": Path(r"C:\Users\cultn\AppData\Local\Temp\supertory-lit-live\ready.txt"),
        "intent": "지식인의 무력한 삶을 그린다.",
    },
}


def to_html(text: str) -> tuple[str, int]:
    parts = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    return "".join("<p>" + html.escape(part) + "</p>" for part in parts), len(parts)


def spell_limited(text: str) -> int | None:
    box: dict[str, int | None] = {}

    def run() -> None:
        try:
            box["n"] = spell_error_count(text)
        except Exception:
            box["n"] = None

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(60)
    return box.get("n")


def make_project(conn, title: str, text: str, intent: str) -> int:
    project_id = int(
        conn.execute(
            "INSERT INTO project(title, cluster_id, main_genre, sub_genre, literary_form, purpose, intent_md) "
            "VALUES (?, 'general_literature', 'general_lit', 'mid', 'short', 'literature', ?)",
            (title, intent),
        ).lastrowid
    )
    chapter_id = int(
        conn.execute(
            "INSERT INTO chapter(project_id, title, sort_order) VALUES (?, '본문', 0)",
            (project_id,),
        ).lastrowid
    )
    body, count = to_html(text)
    scene_id = int(
        conn.execute(
            "INSERT INTO scene(project_id, chapter_id, title, sort_order) VALUES (?, ?, ?, 0)",
            (project_id, chapter_id, title),
        ).lastrowid
    )
    conn.execute(
        "INSERT INTO scene_revision(scene_id, revision_no, content_md, word_count, is_current) "
        "VALUES (?, 1, ?, ?, 1)",
        (scene_id, body, count),
    )
    return project_id


def parse_stage_models(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for chunk in str(raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise SystemExit(f"stage-models 형식은 style={HAIKU_45_MODEL} 입니다: {chunk}")
        stage, model = chunk.split("=", 1)
        out[stage.strip()] = model.strip()
    return out


def estimate_cache_savings(prefix_tokens: int, stage_count: int) -> dict[str, float]:
    return estimate_shared_prefix_cost(prefix_tokens, stage_count)


def run_one(
    conn,
    *,
    project_id: int,
    label: str,
    reuse_run: int | None,
    from_stage: str,
    batch: bool,
    stage_models: dict[str, str],
    max_cost: float,
) -> dict:
    assembled = assemble_manuscript(load_scene_rows(conn, project_id))
    options: dict = {"max_cost": max_cost, "autocommit": True}
    if stage_models:
        options["stage_models"] = stage_models
    if reuse_run:
        reuse, warning = load_reuse(conn, reuse_run, assembled, from_stage)
        if warning:
            print(f"WARN {label}: {warning}", flush=True)
        if reuse is None:
            raise SystemExit(warning or "재사용할 수 없습니다.")
        options["reuse"] = reuse
        print(f"REUSE {label} from={from_stage} source={reuse_run}", flush=True)
    params = {
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "cost_input_usd": 0.0,
            "cost_cache_write_usd": 0.0,
            "cost_cache_read_usd": 0.0,
            "cost_output_usd": 0.0,
            "cost_usd": 0.0,
            "stages": [],
        },
        "progress": {"stage": "queued", "done": 0, "total": 6},
        "measure": {"batch": batch, "reuse_run": reuse_run, "from": from_stage or None},
    }
    prepared = prepare_literature_run(
        conn,
        project_id,
        model="claude-sonnet-5",
        prompt_version=LITERATURE_PROMPT_VERSION,
        params=params,
    )
    print(f"START {label} run={prepared['run_id']} pages={prepared['paper_pages']}", flush=True)
    import feedback_pipeline.literature_runner as runner

    original = runner.spell_error_count
    runner.spell_error_count = spell_limited
    client = BatchClaude() if batch else LiveClaude()
    started = time.perf_counter()
    try:
        run_literature_short(
            conn,
            project_id,
            prepared["run_id"],
            options,
            claude=client,
        )
    finally:
        runner.spell_error_count = original
    elapsed = round(time.perf_counter() - started, 1)
    conn.commit()
    run = get_run(conn, prepared["run_id"])
    usage = (run.get("params") or {}).get("usage") or {}
    cards = run.get("cards") or []
    digest = {
        "label": label,
        "run_id": prepared["run_id"],
        "status": run.get("status"),
        "pages": prepared["paper_pages"],
        "elapsed_s": elapsed,
        "cost_usd": usage.get("cost_usd"),
        "cost_input_usd": usage.get("cost_input_usd"),
        "cost_cache_write_usd": usage.get("cost_cache_write_usd"),
        "cost_cache_read_usd": usage.get("cost_cache_read_usd"),
        "cost_output_usd": usage.get("cost_output_usd"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "card_count": len(cards),
        "stages": usage.get("stages") or [],
        "batch": batch,
        "reuse_run": reuse_run,
        "from": from_stage or None,
    }
    print(json.dumps(digest, ensure_ascii=False), flush=True)
    return digest


def main() -> None:
    parser = argparse.ArgumentParser(description="문학 단편 측정 (재사용·Batch·단계 모델)")
    parser.add_argument("--work", choices=sorted(DEFAULT_WORKS), default="bincheo")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--reuse-run", type=int, default=None, help="이전 실행 id")
    parser.add_argument(
        "--from",
        dest="from_stage",
        default="cards",
        choices=("style", "scene_map", "rubric", "report", "cards"),
        help="이 단계부터 다시 돌린다",
    )
    parser.add_argument("--batch", action="store_true", help="측정만 Batch API로 보낸다")
    parser.add_argument(
        "--stage-models",
        default="",
        help=f"예: style={HAIKU_45_MODEL},verify={HAIKU_45_MODEL}",
    )
    parser.add_argument("--max-cost", type=float, default=2.0)
    parser.add_argument("--estimate-only", action="store_true", help="빈처 기준 캐시 비용만 출력")
    args = parser.parse_args()

    if args.estimate_only:
        # 빈처 ~68매, 문단 번호 원고+설정집 대략 9천 토큰, 캐시를 쓰는 호출 약 10회
        print(json.dumps(estimate_cache_savings(9000, 10), ensure_ascii=False, indent=2))
        return

    work = DEFAULT_WORKS[args.work]
    text_path = work["path"]
    if not text_path.is_file():
        raise SystemExit(f"원고 파일이 없습니다: {text_path}")
    text = text_path.read_text(encoding="utf-8")

    out = args.data_dir or Path(
        rf"C:\Users\cultn\AppData\Local\Temp\supertory-lit-measure-{args.work}"
    )
    data = out / "data"
    if not args.reuse_run:
        if data.exists():
            shutil.rmtree(data)
        data.mkdir(parents=True)
    else:
        data.mkdir(parents=True, exist_ok=True)

    app.DATA_DIR = data
    app.DATABASE_PATH = data / "supertory.sqlite3"
    app.initialise_database()
    conn = app.connect()
    try:
        row = conn.execute(
            "SELECT id FROM project WHERE title = ? ORDER BY id DESC LIMIT 1",
            (work["title"],),
        ).fetchone()
        if row is None:
            project_id = make_project(conn, work["title"], text, work["intent"])
            conn.commit()
        else:
            project_id = int(row["id"])
        digest = run_one(
            conn,
            project_id=project_id,
            label=work["title"],
            reuse_run=args.reuse_run,
            from_stage=args.from_stage,
            batch=bool(args.batch),
            stage_models=parse_stage_models(args.stage_models),
            max_cost=args.max_cost,
        )
        (out / "digest.json").write_text(json.dumps(digest, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "cache_estimate.json").write_text(
            json.dumps(estimate_cache_savings(9000, 10), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
