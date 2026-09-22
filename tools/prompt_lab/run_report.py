"""Report-stage (3단계) prompt lab CLI. Does not touch run.py or the SuperTory app."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

LAB_DIR = Path(__file__).resolve().parent
ROOT = LAB_DIR.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(LAB_DIR) not in sys.path:
    sys.path.insert(0, str(LAB_DIR))

from env_loader import load_all_dotenv  # noqa: E402

load_all_dotenv()

import claude_call  # noqa: E402
import dup_blocks  # noqa: E402
import manuscript  # noqa: E402
import report_checks  # noqa: E402

PROMPTS_DIR = LAB_DIR / "prompts"
MANUSCRIPTS_DIR = LAB_DIR / "manuscripts"
CASES_PATH = LAB_DIR / "cases.json"
CASES_REPORT_PATH = LAB_DIR / "cases_report.json"
RESULTS_DIR = LAB_DIR / "results"
PLACEHOLDER_RE = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")

MODEL = "claude-sonnet-5"
MAX_TOKENS_OFF = 8192
MAX_TOKENS_ON = 16000
CALL_TIMEOUT = 180.0
CALL_TIMEOUT_THINKING = 300.0
DEFAULT_MAX_COST_USD = 0.8
V1_COMPARE_DIR = RESULTS_DIR / "20260920-223543"
REPORT_PROMPT_FILES = {
    "v1": "report_prompt.txt",
    "v2": "report_prompt_v2.txt",
    "v3": "report_prompt_v3.txt",
}
JSON_SCHEMA_PATH = PROMPTS_DIR / "report_json_schema.json"
CONSISTENCY_PROMPT_PATH = PROMPTS_DIR / "consistency_prompt.txt"
CONSISTENCY_SCHEMA_PATH = PROMPTS_DIR / "consistency_json_schema.json"
V2_COMPARE_DIR = RESULTS_DIR / "20260920-230043"
# 공식 가격표 확인 필요. 값: (입력, 출력) USD / 1M tokens. claude_call/run.py와 동일.
PRICE_SONNET_USD = (3.0, 15.0)
PRICE_HAIKU_USD = (1.0, 5.0)
PRICE_OPUS_USD = (5.0, 25.0)
PRICE_PER_MILLION_USD: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": PRICE_HAIKU_USD,
    "claude-sonnet-5": PRICE_SONNET_USD,
    "claude-opus-5": PRICE_OPUS_USD,
}


class AbortRun(Exception):
    """Stop the lab after 429/errors or a cost cap."""


def prices_for(model: str) -> tuple[float, float]:
    key = str(model or "").strip()
    if key in PRICE_PER_MILLION_USD:
        return PRICE_PER_MILLION_USD[key]
    low = key.lower()
    if "haiku" in low:
        return PRICE_HAIKU_USD
    if "sonnet" in low:
        return PRICE_SONNET_USD
    if "opus" in low:
        return PRICE_OPUS_USD
    return PRICE_SONNET_USD


def usage_tokens(result: dict[str, Any] | None) -> tuple[int, int]:
    result = result or {}
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else None
    src = usage or {}
    try:
        inp = int(src.get("input_tokens") or 0)
    except (TypeError, ValueError):
        inp = 0
    try:
        out = int(src.get("output_tokens") or 0)
    except (TypeError, ValueError):
        out = 0
    return inp, out


class CostGuard:
    def __init__(self, max_cost: float) -> None:
        self.max_cost = float(max_cost)
        self.total = 0.0
        self.by_model: dict[str, dict[str, Any]] = {}
        self.last: dict[str, Any] | None = None
        self.stopped = False

    def _row(self, model: str) -> dict[str, Any]:
        row = self.by_model.get(model)
        if row is None:
            row = {
                "model": model,
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "failures": 0,
                "retries": 0,
                "truncated": 0,
            }
            self.by_model[model] = row
        return row

    def add_call(
        self,
        *,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        failed: bool = False,
        retries: int = 0,
        truncated: bool = False,
        case_id: str = "",
    ) -> None:
        row = self._row(model)
        row["calls"] += 1
        row["input_tokens"] += int(input_tokens or 0)
        row["output_tokens"] += int(output_tokens or 0)
        pin, pout = prices_for(model)
        cost = (int(input_tokens or 0) * pin + int(output_tokens or 0) * pout) / 1_000_000.0
        row["cost_usd"] = round(float(row["cost_usd"]) + cost, 8)
        self.total = round(self.total + cost, 8)
        if failed:
            row["failures"] += 1
        row["retries"] += int(retries or 0)
        if truncated:
            row["truncated"] += 1
        self.last = {"model": model, "case_id": case_id, "total_usd": self.total}
        if self.total > self.max_cost:
            self.stopped = True
            raise AbortRun(
                f"비용 한도 초과: ${self.total:.4f} / ${self.max_cost:.2f} "
                f"(마지막 {case_id} {model})"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_cost_usd": self.max_cost,
            "total_usd": self.total,
            "prices_note": "공식 가격표 확인 필요",
            "models": [dict(row) for row in self.by_model.values()],
            "stopped": self.stopped,
            "last": self.last,
        }


def fill_template(template: str, values: dict[str, Any]) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            return match.group(0)
        value = values[key]
        if value is None:
            return ""
        return str(value)

    return PLACEHOLDER_RE.sub(repl, template)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def format_project_context(project: dict[str, Any] | None) -> str:
    project = project or {}
    lines: list[str] = []
    title = str(project.get("title") or "").strip()
    genre = str(project.get("genre") or "").strip()
    lines.append(f"제목: {title}" if title else "제목: (없음)")
    lines.append(f"장르: {genre}" if genre else "장르: (없음)")
    chars = project.get("characters") or []
    if chars:
        lines.append("인물:")
        for person in chars:
            if not isinstance(person, dict):
                continue
            name = str(person.get("name") or "").strip()
            note = str(person.get("note") or "").strip()
            if name and note:
                lines.append(f"- {name}: {note}")
            elif name:
                lines.append(f"- {name}")
    facts = [str(x).strip() for x in (project.get("tracked_facts") or []) if str(x).strip()]
    if facts:
        lines.append("tracked_facts:")
        for fact in facts:
            lines.append(f"- {fact}")
    terms = [str(x).strip() for x in (project.get("terms") or []) if str(x).strip()]
    if terms:
        lines.append("terms:")
        for term in terms:
            lines.append(f"- {term}")
    notes = [str(x).strip() for x in (project.get("term_notes") or []) if str(x).strip()]
    if notes:
        lines.append("term_notes:")
        for note in notes:
            lines.append(f"- {note}")
    return "\n".join(lines)


def format_paragraphs_text(paragraphs: list[manuscript.Paragraph]) -> str:
    lines: list[str] = []
    for para in paragraphs:
        if para.type == "divider":
            lines.append("(장면 구분선)")
        elif para.type == "other" and para.text.lstrip().startswith("#"):
            lines.append(f"[P{para.number}] (제목) {para.text}")
        else:
            lines.append(f"[P{para.number}] {para.text}")
    return "\n\n".join(lines)


def load_prompts(version: str) -> dict[str, str]:
    name = REPORT_PROMPT_FILES.get(version) or REPORT_PROMPT_FILES["v2"]
    path = PROMPTS_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"리포트 프롬프트가 없습니다: {path.name}")
    return {
        "report": path.read_text(encoding="utf-8"),
        "schema": (PROMPTS_DIR / "report_schema.txt").read_text(encoding="utf-8"),
        "system": (PROMPTS_DIR / "system.txt").read_text(encoding="utf-8").rstrip(),
        "persona": (PROMPTS_DIR / "persona_default.txt").read_text(encoding="utf-8").strip(),
        "version": version,
        "file": name,
    }


def load_json_schema() -> dict[str, Any]:
    return json.loads(JSON_SCHEMA_PATH.read_text(encoding="utf-8"))


def load_consistency_assets() -> tuple[str, dict[str, Any]]:
    prompt = CONSISTENCY_PROMPT_PATH.read_text(encoding="utf-8")
    schema = json.loads(CONSISTENCY_SCHEMA_PATH.read_text(encoding="utf-8"))
    return prompt, schema


def format_consistency_findings(parsed: Any) -> str:
    report = parsed if isinstance(parsed, dict) else {}
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    lines: list[str] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        title = str(issue.get("title") or "").strip() or "(제목 없음)"
        rng = issue.get("range") if isinstance(issue.get("range"), dict) else {}
        start = rng.get("start_para")
        end = rng.get("end_para")
        if start is None:
            loc = "?"
        elif end is None or end == start:
            loc = f"P{start}"
        else:
            loc = f"P{start}~P{end}"
        tag = "확실" if str(issue.get("certainty") or "") == "sure" else "추정"
        lines.append(f"- 정합성: ({title}) ({loc}) [{tag}]")
    return "\n".join(lines)


def merge_rule_findings(dup_text: str, consistency_text: str) -> str:
    parts: list[str] = []
    dup = (dup_text or "").strip()
    if dup and dup != "없음":
        parts.append(dup)
    cons = (consistency_text or "").strip()
    if cons:
        parts.append(cons)
    return "\n".join(parts) if parts else "없음"


def load_lens_text() -> dict[str, str]:
    raw = load_json(PROMPTS_DIR / "lens_text.json")
    return {str(k): str(v) for k, v in raw.items()}


def merge_manuscripts(cases: dict[str, Any], report_cases: dict[str, Any]) -> dict[str, Any]:
    merged = dict(cases.get("manuscripts") or {})
    extra = report_cases.get("extra_manuscripts") or {}
    for key, meta in extra.items():
        merged[key] = meta
    return merged


def load_manuscript_text(meta: dict[str, Any]) -> str | None:
    filename = str(meta.get("file") or "").strip()
    if not filename:
        return None
    path = MANUSCRIPTS_DIR / filename
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def parse_only(raw: str | None) -> set[str] | None:
    text = (raw or "").strip()
    if not text:
        return None
    return {part.strip() for part in text.split(",") if part.strip()}


def expand_jobs(runs: list[dict[str, Any]], only: set[str] | None) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for run in runs:
        rid = str(run.get("id") or "").strip()
        if not rid:
            continue
        if only is not None and rid not in only:
            continue
        repeat = int(run.get("repeat") or 1)
        if repeat < 1:
            repeat = 1
        for idx in range(1, repeat + 1):
            jobs.append(
                {
                    "id": rid,
                    "run_index": idx,
                    "repeat": repeat,
                    "manuscript": run.get("manuscript"),
                    "planted": run.get("planted") or [],
                    "expect_duplicates": bool(run.get("expect_duplicates")),
                    "note": run.get("note") or "",
                }
            )
    return jobs


def _md_escape(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def _clip(text: str, n: int = 100) -> str:
    raw = (text or "").replace("\n", " ").strip()
    if len(raw) <= n:
        return raw
    return raw[:n] + "…"


def _range_label(item: dict[str, Any]) -> str:
    rng = item.get("range") if isinstance(item.get("range"), dict) else None
    if not rng:
        return "(range 없음)"
    start = rng.get("start_para")
    end = rng.get("end_para")
    if start is None and end is None:
        return "(range 없음)"
    if start == end:
        return f"P{start}"
    return f"P{start}~P{end}"


def _quotes_label(item: dict[str, Any]) -> str:
    rng = item.get("range") if isinstance(item.get("range"), dict) else None
    if not rng:
        return ""
    start_q = str(rng.get("start_quote") or "").strip()
    end_q = str(rng.get("end_quote") or "").strip()
    if start_q and end_q and start_q != end_q:
        return f"「{start_q}」…「{end_q}」"
    return f"「{start_q or end_q}」" if (start_q or end_q) else ""


def _sort_by_para(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(item: dict[str, Any]) -> tuple[int, str]:
        rng = item.get("range") if isinstance(item.get("range"), dict) else None
        start = 10**9
        if rng:
            try:
                start = int(rng.get("start_para") or 10**9)
            except (TypeError, ValueError):
                start = 10**9
        return (start, str(item.get("id") or ""))

    return sorted(items, key=key)


def render_report_runs(rows: list[dict[str, Any]], dup_verify: dict[str, Any]) -> str:
    parts: list[str] = ["# 리포트 실행 기록", ""]
    unexpected = dup_verify.get("unexpected") or []
    parts.append("## 기계 중복 검사 (dup_blocks)")
    counts = dup_verify.get("counts") or {}
    for key in ("ep2", "ep2x", "ep2dup"):
        if key in counts:
            parts.append(f"- `{key}`: {counts[key]}개 블록")
    if unexpected:
        parts.append("- 기대와 다름: " + "; ".join(unexpected))
    else:
        parts.append("- 기대와 일치 (ep2/ep2x 0개, ep2dup 1개 이상)")
    parts.append("")

    for row in rows:
        rid = row.get("id")
        idx = row.get("run_index")
        repeat = row.get("repeat")
        parts.append(f"## {rid} ({idx}/{repeat})")
        if row.get("thinking"):
            parts.append(f"- thinking: {row.get('thinking')}")
        if row.get("structured_output"):
            parts.append(f"- 구조화 출력: {row.get('structured_output')}")
        if row.get("structured_reject_message"):
            parts.append(f"- 스키마 거부 메시지: {row.get('structured_reject_message')}")
        if row.get("note"):
            parts.append(f"- 메모: {row['note']}")
        if row.get("error"):
            parts.append(f"- **오류:** {row['error']}")
            parts.append("")
            continue
        parts.append("### 프롬프트에 넣은 기계 검사 결과")
        parts.append(row.get("rule_findings_text") or "없음")
        parts.append("")
        parsed = row.get("parsed") if isinstance(row.get("parsed"), dict) else {}
        parts.append("### summary")
        parts.append(str(parsed.get("summary") or "(없음)"))
        parts.append("")
        parts.append("### scores")
        scores = parsed.get("scores") if isinstance(parsed.get("scores"), list) else []
        parts.append("| 항목 | 점수 | 코멘트 |")
        parts.append("|---|---|---|")
        for score in scores:
            if not isinstance(score, dict):
                continue
            parts.append(
                f"| {_md_escape(str(score.get('item') or ''))} "
                f"| {score.get('score', '')} "
                f"| {_md_escape(str(score.get('comment') or ''))} |"
            )
        parts.append("")

        staged = {
            str(it.get("id") or ""): it
            for it in ((row.get("checks") or {}).get("r3") or {}).get("items") or []
        }

        parts.append("### strengths")
        strengths = parsed.get("strengths") if isinstance(parsed.get("strengths"), list) else []
        if not strengths:
            parts.append("(없음)")
        for item in strengths:
            if not isinstance(item, dict):
                continue
            parts.append(
                f"- **{_md_escape(str(item.get('title') or ''))}** "
                f"({_range_label(item)}) {_quotes_label(item)}"
            )
            body = _clip(str(item.get("body") or ""))
            if body:
                parts.append(f"  - { _md_escape(body) }")
        parts.append("")

        parts.append("### weaknesses")
        weaknesses = parsed.get("weaknesses") if isinstance(parsed.get("weaknesses"), list) else []
        if not weaknesses:
            parts.append("(없음)")
        for item in _sort_by_para([it for it in weaknesses if isinstance(it, dict)]):
            sid = str(item.get("id") or "")
            stage = staged.get(sid, {}).get("stage") or report_checks.impact_stage(
                int(item.get("impact") or 0)
            ) if item.get("impact") else "?"
            parts.append(
                f"- **[{stage}]** impact={item.get('impact')} "
                f"type={item.get('type')} fixable={item.get('fixable')} "
                f"**{_md_escape(str(item.get('title') or ''))}** "
                f"({_range_label(item)})"
            )
            body = _clip(str(item.get("body") or ""))
            if body:
                parts.append(f"  - { _md_escape(body) }")
            quotes = _quotes_label(item)
            if quotes:
                parts.append(f"  - {quotes}")
        parts.append("")

        parts.append("### consistency")
        consistency = parsed.get("consistency") if isinstance(parsed.get("consistency"), list) else []
        if not consistency:
            parts.append("(없음)")
        for item in _sort_by_para([it for it in consistency if isinstance(it, dict)]):
            sid = str(item.get("id") or "")
            stage = staged.get(sid, {}).get("stage") or "?"
            parts.append(
                f"- **[{stage}]** impact={item.get('impact')} "
                f"certainty={item.get('certainty')} "
                f"**{_md_escape(str(item.get('title') or ''))}** "
                f"({_range_label(item)})"
            )
            body = _clip(str(item.get("body") or ""))
            if body:
                parts.append(f"  - { _md_escape(body) }")
            quotes = _quotes_label(item)
            if quotes:
                parts.append(f"  - {quotes}")
        parts.append("")

        checks = row.get("checks") or {}
        parts.append("### 검증 R1–R11")
        r1 = checks.get("r1") or {}
        r2a = checks.get("r2a") or checks.get("r2") or {}
        r2b = checks.get("r2b") or {}
        r3 = checks.get("r3") or {}
        r4 = checks.get("r4") or {}
        r5 = checks.get("r5") or {}
        r6 = checks.get("r6")
        r7 = checks.get("r7")
        r9 = checks.get("r9") or {}
        r10 = checks.get("r10") or {}
        r11 = checks.get("r11") or {}
        parts.append(
            f"- R1 JSON: {'성공' if r1.get('ok') else '실패'}"
            + (f" (없는 키: {', '.join(r1.get('missing_keys') or [])})" if r1.get("missing_keys") else "")
        )
        parts.append(
            f"- R2a 인용: 통과 {r2a.get('pass', 0)}, 실패 {r2a.get('fail', 0)}, null {r2a.get('null', 0)}"
        )
        parts.append(
            f"- R2b 구간: 실패 {r2b.get('fail', 0)}, 경고(구간 넓음) {r2b.get('warn', 0)}, "
            f"생략 {r2b.get('skipped', 0)}"
        )
        counts3 = r3.get("counts") or {}
        warn = []
        if r3.get("floor_applied"):
            warn.append("바닥 보장 적용")
        if r3.get("high_over_half"):
            warn.append("high 과다")
        if r3.get("maybe_impact_over_3"):
            warn.append(f"maybe+impact>3 {r3.get('maybe_impact_over_3')}건")
        if r3.get("reference_count"):
            warn.append(f"참고 {r3.get('reference_count')}건")
        extra = f" ({', '.join(warn)})" if warn else ""
        parts.append(
            f"- R3 단계: high {counts3.get('high', 0)}, "
            f"medium {counts3.get('medium', 0)}, low {counts3.get('low', 0)}"
            f"{extra}"
        )
        parts.append(f"- R4 칭찬-지적 충돌: {r4.get('count', 0)}")
        lowest = r5.get("lowest_score_item") or "(없음)"
        parts.append(
            f"- R5 type오류 {r5.get('bad_type', 0)}, fixable오류 {r5.get('bad_fixable', 0)}, "
            f"perspectives오류 {r5.get('bad_perspectives', 0)}, "
            f"scores {r5.get('score_count', 0)}개, 최저점 항목: {lowest}"
        )
        if r6:
            parts.append(
                f"- R6 탐지: {r6.get('detected', 0)}/{r6.get('total', 0)} "
                f"(required {r6.get('required_detected', 0)}/{r6.get('required_total', 0)}, "
                f"칭찬 구간 속 오류 {r6.get('in_strength_count', 0)})"
            )
            missed = r6.get("required_missed") or []
            if missed:
                parts.append(f"  - required 미탐지: {', '.join(str(x) for x in missed)}")
            for plant in r6.get("rows") or []:
                mark = "탐지" if plant.get("detected") else "미탐지"
                req = " required" if plant.get("required") else ""
                extra_plant = " / 칭찬 구간 속 오류" if plant.get("in_strength_range") else ""
                parts.append(f"  - {plant.get('id')} {plant.get('kind')}{req}: {mark}{extra_plant}")
        if r7:
            parts.append(
                f"- R7 중복: 블록 {r7.get('dup_block_count', 0)}개, "
                f"structure/impact5={'있음' if r7.get('structure_impact5') else '없음'}, "
                f"summary 키워드={'있음' if r7.get('summary_keyword') else '없음'}"
            )
        parts.append(
            f"- R9 같은 range 쌍 {r9.get('same_range_pairs', 0)}, "
            f"내부이름 누출 {r9.get('leak_count', 0)}"
        )
        parts.append(f"- R10 문제 아님 표현: {r10.get('count', 0)}")
        parts.append(f"- R11 enum 오류: {r11.get('count', 0)}")
        if row.get("truncated"):
            parts.append("- **truncated** (stop_reason=max_tokens)")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def render_metrics(rows: list[dict[str, Any]], r8: dict[str, Any] | None) -> str:
    parts: list[str] = ["# 리포트 지표", ""]
    parts.append(
        "| 실행 | JSON | 항목 수 | R2a 실패 | R2b 실패 | high | medium | low | 바닥 보장 | 충돌 | 탐지율 | 중복 | R9쌍 | R9누출 | R10 | R11 | truncated |"
    )
    parts.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for row in rows:
        rid = f"{row.get('id')}-{row.get('run_index')}"
        if row.get("thinking") == "on":
            rid += "-think"
        if row.get("error"):
            parts.append(f"| {rid} | 오류 | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |")
            continue
        checks = row.get("checks") or {}
        r1 = checks.get("r1") or {}
        r2a = checks.get("r2a") or checks.get("r2") or {}
        r2b = checks.get("r2b") or {}
        r3 = checks.get("r3") or {}
        r4 = checks.get("r4") or {}
        r6 = checks.get("r6")
        r7 = checks.get("r7")
        r9 = checks.get("r9") or {}
        r10 = checks.get("r10") or {}
        r11 = checks.get("r11") or {}
        counts = r3.get("counts") or {}
        detect = "—"
        if r6:
            detect = f"{r6.get('detected', 0)}/{r6.get('total', 0)}"
        dup = "—"
        if r7:
            dup = "예" if r7.get("ok") else "아니오"
        parts.append(
            f"| {rid} "
            f"| {'예' if r1.get('ok') else '아니오'} "
            f"| {r3.get('item_count', 0)} "
            f"| {r2a.get('fail', 0)} "
            f"| {r2b.get('fail', 0)} "
            f"| {counts.get('high', 0)} "
            f"| {counts.get('medium', 0)} "
            f"| {counts.get('low', 0)} "
            f"| {'예' if r3.get('floor_applied') else '아니오'} "
            f"| {r4.get('count', 0)} "
            f"| {detect} "
            f"| {dup} "
            f"| {r9.get('same_range_pairs', 0)} "
            f"| {r9.get('leak_count', 0)} "
            f"| {r10.get('count', 0)} "
            f"| {r11.get('count', 0)} "
            f"| {'예' if row.get('truncated') else '아니오'} |"
        )
    parts.append("")
    if r8:
        parts.append("## R8 반복 실행 비교 (RP-ep2)")
        parts.append("")
        parts.append("| 실행 | high 개수 | 전체 항목 수 |")
        parts.append("|---|---|---|")
        for run in r8.get("runs") or []:
            parts.append(
                f"| {run.get('id')}-{run.get('run_index')} "
                f"| {run.get('high_count')} "
                f"| {run.get('item_count')} |"
            )
        parts.append("")
        parts.append("| 쌍 | Jaccard | 교집합 | 합집합 |")
        parts.append("|---|---|---|---|")
        for pair in r8.get("pairs") or []:
            parts.append(
                f"| {pair.get('a')} vs {pair.get('b')} "
                f"| {pair.get('jaccard')} "
                f"| {pair.get('intersection')} "
                f"| {pair.get('union')} |"
            )
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def render_cost(cost: dict[str, Any], *, expected_calls: int, completed: int, abort: str | None) -> str:
    parts: list[str] = ["# 리포트 비용 요약", ""]
    parts.append(f"- 예상 호출: {expected_calls}")
    parts.append(f"- 실제 호출: {completed}")
    parts.append(f"- 추정 비용: ${float(cost.get('total_usd') or 0):.4f}")
    parts.append(f"- 한도: ${float(cost.get('max_cost_usd') or 0):.2f}")
    parts.append(f"- 단가 주석: {cost.get('prices_note') or '공식 가격표 확인 필요'}")
    if abort:
        parts.append(f"- 중단: {abort}")
    parts.append("")
    parts.append("| 모델 | 호출 | 입력 토큰 | 출력 토큰 | 추정 비용 | 실패 | 재시도 | truncated |")
    parts.append("|---|---|---|---|---|---|---|---|")
    for row in cost.get("models") or []:
        parts.append(
            f"| {row.get('model')} "
            f"| {row.get('calls', 0)} "
            f"| {row.get('input_tokens', 0)} "
            f"| {row.get('output_tokens', 0)} "
            f"| ${float(row.get('cost_usd') or 0):.4f} "
            f"| {row.get('failures', 0)} "
            f"| {row.get('retries', 0)} "
            f"| {row.get('truncated', 0)} |"
        )
    parts.append("")
    return "\n".join(parts)


def _metric_cells(row: dict[str, Any]) -> dict[str, Any]:
    checks = row.get("checks") or {}
    r1 = checks.get("r1") or {}
    r2a = checks.get("r2a") or checks.get("r2") or {}
    r2b = checks.get("r2b") or {}
    r3 = checks.get("r3") or {}
    r4 = checks.get("r4") or {}
    r6 = checks.get("r6")
    r9 = checks.get("r9") or {}
    r10 = checks.get("r10") or {}
    r11 = checks.get("r11") or {}
    counts = r3.get("counts") or {}
    detect = "—"
    if r6:
        detect = f"{r6.get('detected', 0)}/{r6.get('total', 0)}"
        if r6.get("in_strength_count"):
            detect += f" (칭찬속 {r6.get('in_strength_count')})"
    return {
        "json": "예" if r1.get("ok") else ("오류" if row.get("error") else "아니오"),
        "items": r3.get("item_count", 0) if not row.get("error") else "—",
        "r2a": r2a.get("fail", 0) if not row.get("error") else "—",
        "r2b": r2b.get("fail", 0) if not row.get("error") else "—",
        "high": counts.get("high", 0) if not row.get("error") else "—",
        "medium": counts.get("medium", 0) if not row.get("error") else "—",
        "low": counts.get("low", 0) if not row.get("error") else "—",
        "conflict": r4.get("count", 0) if not row.get("error") else "—",
        "detect": detect if not row.get("error") else "—",
        "r9_pairs": r9.get("same_range_pairs", 0) if not row.get("error") else "—",
        "r9_leak": r9.get("leak_count", 0) if not row.get("error") else "—",
        "r10": r10.get("count", 0) if not row.get("error") else "—",
        "r11": r11.get("count", 0) if not row.get("error") else "—",
    }


def render_compare(
    *,
    v1_rows: list[dict[str, Any]],
    v2_rows: list[dict[str, Any]],
    structured_note: str,
) -> str:
    parts: list[str] = ["# 리포트 v1 vs v2", ""]
    parts.append(f"- v1: `results/20260920-223543` (프롬프트 v1, 구조화 없음)")
    parts.append(f"- v2: 이번 실행 ({structured_note})")
    parts.append("")
    parts.append(
        "| 실행 | v1 JSON | v2 JSON | v1 항목 | v2 항목 | v1 R2a | v2 R2a | v1 R2b | v2 R2b | "
        "v1 단계(h/m/l) | v2 단계 | v1 충돌 | v2 충돌 | v1 탐지 | v2 탐지 | "
        "v1 R9쌍/누출 | v2 R9 | v1 R10 | v2 R10 | v1 R11 | v2 R11 |"
    )
    parts.append("|" + "|".join(["---"] * 21) + "|")
    v1_map = {
        (r.get("id"), r.get("run_index")): r
        for r in v1_rows
        if not r.get("skipped")
    }
    v2_off = [
        r for r in v2_rows
        if not r.get("skipped") and r.get("thinking") != "on"
    ]
    keys = []
    seen = set()
    for row in v2_off:
        key = (row.get("id"), row.get("run_index"))
        if key not in seen:
            seen.add(key)
            keys.append(key)
    for key in keys:
        v1 = v1_map.get(key) or {}
        v2 = next((r for r in v2_off if (r.get("id"), r.get("run_index")) == key), {})
        a = _metric_cells(v1) if v1 else {k: "—" for k in _metric_cells({}).keys()}
        b = _metric_cells(v2) if v2 else {k: "—" for k in _metric_cells({}).keys()}
        label = f"{key[0]}-{key[1]}"
        parts.append(
            f"| {label} | {a['json']} | {b['json']} | {a['items']} | {b['items']} "
            f"| {a['r2a']} | {b['r2a']} | {a['r2b']} | {b['r2b']} "
            f"| {a['high']}/{a['medium']}/{a['low']} | {b['high']}/{b['medium']}/{b['low']} "
            f"| {a['conflict']} | {b['conflict']} | {a['detect']} | {b['detect']} "
            f"| {a['r9_pairs']}/{a['r9_leak']} | {b['r9_pairs']}/{b['r9_leak']} "
            f"| {a['r10']} | {b['r10']} | {a['r11']} | {b['r11']} |"
        )
    think_rows = [r for r in v2_rows if r.get("thinking") == "on"]
    if think_rows:
        parts.append("")
        parts.append("## v2 thinking on (비교 표 밖)")
        for row in think_rows:
            m = _metric_cells(row)
            parts.append(
                f"- {row.get('id')}-{row.get('run_index')} think: JSON {m['json']}, "
                f"항목 {m['items']}, R2a {m['r2a']}, R2b {m['r2b']}, "
                f"단계 {m['high']}/{m['medium']}/{m['low']}, 충돌 {m['conflict']}, "
                f"탐지 {m['detect']}, R9 {m['r9_pairs']}/{m['r9_leak']}, "
                f"R10 {m['r10']}, R11 {m['r11']}"
            )
    parts.append("")
    return "\n".join(parts)


def render_consistency_runs(rows: list[dict[str, Any]]) -> str:
    parts = ["# 정합성 검사", ""]
    for row in rows:
        label = f"{row.get('id')}-{row.get('run_index')}"
        parts.append(f"## {label}")
        if row.get("error"):
            parts.append(f"- **오류:** {row['error']}")
            parts.append("")
            continue
        checks = row.get("consistency_checks") or {}
        parts.append(f"- JSON: {'성공' if checks.get('json_ok') else '실패'}")
        parts.append(f"- facts: {checks.get('fact_count', 0)}개")
        parts.append(f"- issues: {checks.get('issue_count', 0)}개")
        parts.append(f"- R2a: 통과 {checks.get('r2a_pass', 0)}, 실패 {checks.get('r2a_fail', 0)}")
        planted = checks.get("planted")
        if planted:
            for plant in planted:
                mark = "탐지" if plant.get("detected") else "미탐지"
                req = " required" if plant.get("required") else ""
                parts.append(
                    f"- {plant.get('id')} {plant.get('kind')}{req}: {mark} "
                    f"(문단 {plant.get('paragraphs')})"
                )
        issues = checks.get("issues") or []
        if issues:
            parts.append("### issues")
            for issue in issues:
                rng = issue.get("range") if isinstance(issue.get("range"), dict) else {}
                loc = f"P{rng.get('start_para')}~P{rng.get('end_para')}"
                parts.append(
                    f"- **[{issue.get('certainty')}]** {issue.get('title')} ({loc})"
                )
                body = str(issue.get("body") or "").replace("\n", " ").strip()
                if body:
                    parts.append(f"  - {body[:200]}")
        else:
            parts.append("- issues 없음")
        if row.get("truncated"):
            parts.append("- **truncated**")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _summary_has_nonproblem(summary: str, parsed: dict[str, Any] | None) -> list[str]:
    hits: list[str] = []
    text = summary or ""
    for mark in ("일치", "문제 없", "확인되지", "드러나지 않"):
        if mark in text:
            hits.append(mark)
    parsed = parsed or {}
    for item in parsed.get("weaknesses") or []:
        if not isinstance(item, dict):
            continue
        impact = item.get("impact")
        title = str(item.get("title") or "").strip()
        try:
            impact_n = int(impact)
        except (TypeError, ValueError):
            impact_n = None
        if impact_n is not None and impact_n <= 3 and title and title in text:
            hits.append(f"impact≤3:{title}")
    for item in parsed.get("consistency") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("certainty") or "") == "maybe":
            title = str(item.get("title") or "").strip()
            if title and title in text:
                hits.append(f"maybe:{title}")
    return hits


def render_compare_v2_v3(
    v2_rows: list[dict[str, Any]],
    v3_rows: list[dict[str, Any]],
) -> str:
    parts = ["# 리포트 v2 vs v3", ""]
    v2_map = {
        (r.get("id"), r.get("run_index")): r
        for r in v2_rows
        if r.get("thinking") != "on"
    }
    parts.append("| 실행 | v2 consistency | v3 consistency | v2 요약 잡음 | v3 요약 잡음 |")
    parts.append("|---|---|---|---|---|")
    for row in v3_rows:
        v2 = v2_map.get((row.get("id"), row.get("run_index"))) or v2_map.get((row.get("id"), 1)) or {}
        p2 = v2.get("parsed") if isinstance(v2.get("parsed"), dict) else {}
        p3 = row.get("parsed") if isinstance(row.get("parsed"), dict) else {}
        s2 = str(p2.get("summary") or "")
        s3 = str(p3.get("summary") or "")
        c2 = len(p2.get("consistency") or []) if p2 else "—"
        c3 = len(p3.get("consistency") or []) if p3 else "—"
        n2 = _summary_has_nonproblem(s2, p2)
        n3 = _summary_has_nonproblem(s3, p3)
        parts.append(
            f"| {row.get('id')} | {c2} | {c3} "
            f"| {', '.join(n2) or '없음'} | {', '.join(n3) or '없음'} |"
        )
    parts.append("")
    for row in v3_rows:
        v2 = v2_map.get((row.get("id"), row.get("run_index"))) or v2_map.get((row.get("id"), 1)) or {}
        p2 = v2.get("parsed") if isinstance(v2.get("parsed"), dict) else {}
        p3 = row.get("parsed") if isinstance(row.get("parsed"), dict) else {}
        parts.append(f"## {row.get('id')}")
        parts.append("### v2 summary")
        parts.append(str(p2.get("summary") or "(없음)"))
        parts.append("")
        parts.append("### v3 summary")
        parts.append(str(p3.get("summary") or "(없음)"))
        parts.append("")
        parts.append("### v3 기계 검사 결과")
        parts.append(row.get("rule_findings_text") or "없음")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def recompute_v1_rows(
    v1_payload: dict[str, Any],
    manuscript_cache: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in v1_payload.get("runs") or []:
        if not isinstance(row, dict):
            continue
        copy = dict(row)
        mkey = str(copy.get("manuscript") or "")
        pack = manuscript_cache.get(mkey)
        if pack and not copy.get("error"):
            copy["checks"] = report_checks.run_all_checks(
                parsed=copy.get("parsed"),
                paragraphs=pack["paragraphs"],
                planted=copy.get("planted") or None,
                expect_duplicates=bool(copy.get("expect_duplicates")),
                dup_blocks=copy.get("dup_blocks") or [],
            )
        rows.append(copy)
    return rows


def write_outputs(
    out_dir: Path,
    *,
    rows: list[dict[str, Any]],
    dup_verify: dict[str, Any],
    r8: dict[str, Any] | None,
    cost: dict[str, Any],
    expected_calls: int,
    abort: str | None,
    payload: dict[str, Any],
    compare_md: str | None = None,
    consistency_md: str | None = None,
    compare_v3_md: str | None = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report_runs.md").write_text(
        render_report_runs(rows, dup_verify), encoding="utf-8"
    )
    (out_dir / "report_metrics.md").write_text(render_metrics(rows, r8), encoding="utf-8")
    (out_dir / "cost_summary_report.md").write_text(
        render_cost(
            cost,
            expected_calls=expected_calls,
            completed=sum(int(m.get("calls") or 0) for m in (cost.get("models") or [])),
            abort=abort,
        ),
        encoding="utf-8",
    )
    if compare_md:
        (out_dir / "report_compare.md").write_text(compare_md, encoding="utf-8")
    if consistency_md:
        (out_dir / "consistency_runs.md").write_text(consistency_md, encoding="utf-8")
    if compare_v3_md:
        (out_dir / "compare_v2_v3_report.md").write_text(compare_v3_md, encoding="utf-8")
    (out_dir / "results_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def json_safe(value: Any) -> Any:
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="리포트 단계 프롬프트 랩")
    parser.add_argument("--only", default="", help="실행 id 쉼표 목록 (예: RP-ep2x,RP-ep2dup)")
    parser.add_argument("--max-cost", type=float, default=DEFAULT_MAX_COST_USD)
    parser.add_argument("--stage", choices=("report", "consistency"), default="report")
    parser.add_argument("--report-prompt", choices=("v1", "v2", "v3"), default="v2")
    parser.add_argument("--structured", choices=("on", "off"), default="on")
    parser.add_argument("--claude-thinking", choices=("off", "on"), default="off")
    parser.add_argument(
        "--extra-thinking-on",
        default="",
        help="thinking on으로 한 번 더 돌릴 실행 id (예: RP-ep2x)",
    )
    parser.add_argument(
        "--compare-v1",
        default=str(V1_COMPARE_DIR),
        help="비교할 v1 결과 폴더",
    )
    args = parser.parse_args(argv)

    cases = load_json(CASES_PATH)
    report_cases = load_json(CASES_REPORT_PATH)
    manuscripts_meta = merge_manuscripts(cases, report_cases)
    projects = cases.get("projects") or {}
    prompts = load_prompts(args.report_prompt)
    lens_map = load_lens_text()
    system_prompt = prompts["system"] + "\n\n" + prompts["persona"]
    json_schema = load_json_schema() if args.structured == "on" else None
    consistency_prompt, consistency_schema = load_consistency_assets()
    structured_on = args.structured == "on"
    default_thinking = args.claude_thinking
    stage = args.stage
    need_consistency = stage == "consistency" or args.report_prompt == "v3"

    only = parse_only(args.only)
    jobs = expand_jobs(list(report_cases.get("runs") or []), only)
    if not jobs:
        print("실행할 항목이 없습니다.", file=sys.stderr)
        return 1
    for job in jobs:
        job["thinking"] = default_thinking
    if stage == "consistency" or args.report_prompt == "v3":
        jobs = [j for j in jobs if int(j.get("run_index") or 1) == 1]

    extra_ids = parse_only(args.extra_thinking_on)
    extra_jobs: list[dict[str, Any]] = []
    if extra_ids and stage == "report" and args.report_prompt != "v3":
        extra_jobs = [
            {**job, "thinking": "on"}
            for job in expand_jobs(list(report_cases.get("runs") or []), extra_ids)
        ]

    manuscript_cache: dict[str, dict[str, Any]] = {}
    for key, meta in manuscripts_meta.items():
        text = load_manuscript_text(meta if isinstance(meta, dict) else {})
        if text is None:
            continue
        paras = manuscript.parse_paragraphs(text)
        project_id = str((meta or {}).get("project") or "")
        manuscript_cache[key] = {
            "text": text,
            "paragraphs": paras,
            "project": projects.get(project_id) or {},
            "meta": meta,
        }

    dup_verify = dup_blocks.verify_expected(
        {k: manuscript_cache[k]["paragraphs"] for k in ("ep2", "ep2x", "ep2dup") if k in manuscript_cache}
    )
    print("dup_blocks:", dup_verify.get("counts"))
    if dup_verify.get("unexpected"):
        print("dup_blocks 기대와 다름:", "; ".join(dup_verify["unexpected"]))

    runnable: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for job in jobs + extra_jobs:
        mkey = str(job.get("manuscript") or "")
        if mkey not in manuscript_cache:
            skipped.append({**job, "skipped": True, "error": f"원고 없음: {mkey}"})
            continue
        runnable.append(job)

    expected_calls = len(runnable)
    if need_consistency and stage == "report":
        expected_calls *= 2
    print(
        f"예상 호출 수: {expected_calls} (모델 {MODEL}, stage {stage}, 프롬프트 {args.report_prompt}, "
        f"구조화 {args.structured}, thinking 기본 {default_thinking})"
    )
    print(f"비용 한도: ${float(args.max_cost):.2f}")
    if skipped:
        print(f"건너뜀: {len(skipped)}건")

    if not claude_call.is_configured():
        print("Anthropic API 키가 없습니다. .env의 ANTHROPIC_API_KEY를 확인하세요.", file=sys.stderr)
        return 1

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = RESULTS_DIR / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    cost_guard = CostGuard(max_cost=float(args.max_cost))
    rows: list[dict[str, Any]] = list(skipped)
    abort_message: str | None = None
    structured_used: set[str] = set()
    structured_rejects: list[str] = []

    def invoke(
        *,
        label: str,
        user_prompt: str,
        schema: dict[str, Any] | None,
        thinking: str,
        max_tokens: int,
        timeout: float,
    ) -> dict[str, Any]:
        print(f"호출 중: {label} (thinking={thinking}, max_tokens={max_tokens})")
        try:
            result = claude_call.generate(
                user_prompt,
                model=MODEL,
                system=system_prompt,
                max_tokens=max_tokens,
                timeout=timeout,
                thinking=thinking,
                json_schema=schema if structured_on else None,
            )
        except claude_call.ClaudeError as error:
            try:
                cost_guard.add_call(
                    model=MODEL,
                    failed=True,
                    retries=getattr(error, "retries", 0) or 0,
                    case_id=label,
                )
            except AbortRun:
                pass
            message = f"{label}: {error} (http={error.http_status}, code={error.code})"
            raise AbortRun(message) from error
        used = str(result.get("structured_output") or "off")
        structured_used.add(used)
        reject_msg = result.get("structured_reject_message")
        if reject_msg:
            note = f"{label}: {reject_msg}"
            if note not in structured_rejects:
                structured_rejects.append(note)
                print(f"스키마 거부 후 폴백: {note}")
        inp, out = usage_tokens(result)
        cost_guard.add_call(
            model=MODEL,
            input_tokens=inp,
            output_tokens=out,
            retries=int(result.get("retries") or 0),
            truncated=bool(result.get("truncated")),
            case_id=label,
        )
        result["structured_reject_message"] = reject_msg
        return result

    try:
        for job in runnable:
            mkey = str(job["manuscript"])
            pack = manuscript_cache[mkey]
            paragraphs = pack["paragraphs"]
            project = pack["project"]
            blocks = dup_blocks.find_dup_blocks(paragraphs)
            findings = dup_blocks.format_dup_findings(blocks)
            lens = str(project.get("explanation_lens") or "normal")
            lens_text = lens_map.get(lens) or lens_map.get("normal") or ""
            thinking = str(job.get("thinking") or "off")
            max_tokens = MAX_TOKENS_ON if thinking == "on" else MAX_TOKENS_OFF
            timeout = CALL_TIMEOUT_THINKING if thinking == "on" else CALL_TIMEOUT
            label = f"{job['id']}-{job['run_index']}"
            if thinking == "on":
                label += "-think"
            consistency_parsed = None
            consistency_result = None
            if need_consistency:
                cons_prompt = fill_template(
                    consistency_prompt,
                    {
                        "project_context": format_project_context(project),
                        "paragraphs_text": format_paragraphs_text(paragraphs),
                    },
                )
                try:
                    consistency_result = invoke(
                        label=f"{label}-cons",
                        user_prompt=cons_prompt,
                        schema=consistency_schema,
                        thinking="off",
                        max_tokens=MAX_TOKENS_OFF,
                        timeout=CALL_TIMEOUT,
                    )
                except AbortRun as error:
                    rows.append(
                        {
                            **job,
                            "error": str(error),
                            "rule_findings_text": findings,
                            "stage": "consistency",
                        }
                    )
                    raise
                consistency_parsed = consistency_result.get("parsed")
                if not isinstance(consistency_parsed, dict):
                    consistency_parsed = report_checks.coerce_report(consistency_parsed)
                cons_text = format_consistency_findings(consistency_parsed)
                findings = merge_rule_findings(findings, cons_text)

            if stage == "consistency":
                checks = report_checks.check_consistency_output(
                    consistency_parsed,
                    paragraphs,
                    planted=job.get("planted") or None,
                )
                rows.append(
                    {
                        **job,
                        "stage": "consistency",
                        "rule_findings_text": findings,
                        "dup_blocks": blocks,
                        "parsed": consistency_parsed,
                        "raw": (consistency_result or {}).get("raw"),
                        "stop_reason": (consistency_result or {}).get("stop_reason"),
                        "truncated": bool((consistency_result or {}).get("truncated")),
                        "empty": (consistency_result or {}).get("empty"),
                        "usage": (consistency_result or {}).get("usage"),
                        "retries": (consistency_result or {}).get("retries"),
                        "consistency_checks": checks,
                        "structured_output": (consistency_result or {}).get("structured_output"),
                        "structured_reject_message": (consistency_result or {}).get(
                            "structured_reject_message"
                        ),
                    }
                )
                continue

            user_prompt = fill_template(
                prompts["report"],
                {
                    "project_context": format_project_context(project),
                    "rule_findings_text": findings,
                    "explanation_lens": lens,
                    "explanation_lens_text": lens_text,
                    "paragraphs_text": format_paragraphs_text(paragraphs),
                    "report_schema": prompts["schema"],
                },
            )
            try:
                result = invoke(
                    label=label,
                    user_prompt=user_prompt,
                    schema=json_schema,
                    thinking=thinking,
                    max_tokens=max_tokens,
                    timeout=timeout,
                )
            except AbortRun as error:
                rows.append({**job, "error": str(error), "rule_findings_text": findings})
                raise

            parsed = result.get("parsed")
            checks = report_checks.run_all_checks(
                parsed=parsed,
                paragraphs=paragraphs,
                planted=job.get("planted") or None,
                expect_duplicates=bool(job.get("expect_duplicates")),
                dup_blocks=blocks,
            )
            row = {
                **job,
                "rule_findings_text": findings,
                "dup_blocks": blocks,
                "parsed": parsed if isinstance(parsed, dict) else report_checks.coerce_report(parsed),
                "consistency_parsed": consistency_parsed,
                "raw": result.get("raw"),
                "stop_reason": result.get("stop_reason"),
                "truncated": bool(result.get("truncated")),
                "empty": result.get("empty"),
                "usage": result.get("usage"),
                "retries": result.get("retries"),
                "checks": checks,
                "structured_output": result.get("structured_output"),
                "structured_reject_message": result.get("structured_reject_message"),
                "max_tokens": max_tokens,
            }
            rows.append(row)
    except AbortRun as error:
        abort_message = str(error)
        print(f"중단: {abort_message}", file=sys.stderr)

    r8_runs: list[dict[str, Any]] = []
    for row in rows:
        if row.get("id") != "RP-ep2" or row.get("error") or int(row.get("repeat") or 0) < 2:
            continue
        if row.get("thinking") == "on":
            continue
        r3 = (row.get("checks") or {}).get("r3") or {}
        items = r3.get("items") or []
        r8_runs.append(
            {
                "id": row.get("id"),
                "run_index": row.get("run_index"),
                "high_paras": sorted(report_checks.high_para_set(items)),
                "high_count": (r3.get("counts") or {}).get("high", 0),
                "item_count": r3.get("item_count", 0),
            }
        )
    r8 = report_checks.check_r8(r8_runs)

    compare_md = None
    compare_v3_md = None
    consistency_md = None
    if stage == "consistency":
        consistency_md = render_consistency_runs(rows)
    elif args.report_prompt == "v3":
        v2_json = V2_COMPARE_DIR / "results_report.json"
        if v2_json.is_file():
            try:
                v2_payload = load_json(v2_json)
                compare_v3_md = render_compare_v2_v3(v2_payload.get("runs") or [], rows)
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
                print(f"v2/v3 비교를 만들지 못했습니다: {error}", file=sys.stderr)
    elif (Path(args.compare_v1) / "results_report.json").is_file():
        try:
            v1_payload = load_json(Path(args.compare_v1) / "results_report.json")
            v1_rows = recompute_v1_rows(v1_payload, manuscript_cache)
            structured_note = (
                f"프롬프트 {args.report_prompt}, 구조화 {args.structured}"
                f" (실제 {', '.join(sorted(structured_used)) or '없음'})"
            )
            if structured_rejects:
                structured_note += " / 스키마 거부: " + "; ".join(structured_rejects)
            compare_md = render_compare(
                v1_rows=v1_rows,
                v2_rows=rows,
                structured_note=structured_note,
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
            print(f"v1 비교를 만들지 못했습니다: {error}", file=sys.stderr)

    payload = {
        "stamp": stamp,
        "model": MODEL,
        "stage": stage,
        "report_prompt": args.report_prompt,
        "structured": args.structured,
        "structured_used": sorted(structured_used),
        "structured_rejects": structured_rejects,
        "thinking_default": default_thinking,
        "dup_blocks_verify": json_safe(dup_verify),
        "runs": json_safe(rows),
        "r8": json_safe(r8),
        "cost": cost_guard.as_dict(),
        "abort": abort_message,
        "expected_calls": expected_calls,
    }
    write_outputs(
        out_dir,
        rows=rows,
        dup_verify=dup_verify,
        r8=r8,
        cost=cost_guard.as_dict(),
        expected_calls=expected_calls,
        abort=abort_message,
        payload=payload,
        compare_md=compare_md,
        consistency_md=consistency_md,
        compare_v3_md=compare_v3_md,
    )
    print(f"결과: {out_dir}")
    if structured_rejects:
        print("스키마 거부:", "; ".join(structured_rejects))
    elif structured_on:
        print(f"구조화 출력: {', '.join(sorted(structured_used)) or '기록 없음'}")
    if abort_message:
        done = [f"{r.get('id')}-{r.get('run_index')}" for r in rows if not r.get("skipped")]
        print(f"여기까지 완료: {', '.join(done) if done else '(없음)'}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
