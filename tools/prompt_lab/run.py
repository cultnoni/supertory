"""Prompt lab CLI: file in → Gemini → results out. Does not touch the SuperTory app."""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
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

import gemini_client  # noqa: E402
from gemini_client import DEFAULT_MODEL, GeminiError  # noqa: E402

import checks  # noqa: E402
import claude_call  # noqa: E402
import gemini_call  # noqa: E402
import korean_speller  # noqa: E402
import manuscript  # noqa: E402

PROMPTS_DIR = LAB_DIR / "prompts"
MANUSCRIPTS_DIR = LAB_DIR / "manuscripts"
CASES_PATH = LAB_DIR / "cases.json"
FALLBACK_MODEL = "gemini-flash-latest"
DEFAULT_APP_URL = "http://127.0.0.1:8766"
ALT_APP_URL = "http://127.0.0.1:8765"
SPELL_CLEAN_KEYS = ("ep2", "ep3")
SPELL_CLEAN_SLEEP = 1.0
SPELL_CLEAN_429_LIMIT = 2
PLACEHOLDER_RE = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")
EXPECT_KEYS = (
    "logic_reversed",
    "undeclared_additions",
    "undeclared_removals",
    "repeats_flagged_pattern",
)
CASE_RANGE_VERSIONS = frozenset({"v2", "v3", "v4"})
CARD_PROMPT_FILES = {
    "v4": ("card_prompt_v4.txt", "card_schema_v4.txt"),
    "v3": ("card_prompt_v3.txt", "card_schema_v3.txt"),
    "v2": ("card_prompt_v2.txt", "card_schema_v2.txt"),
}

# 공식 가격표 확인 필요. 값: (입력, 출력) USD / 1M tokens.
PRICE_HAIKU_USD = (1.0, 5.0)
PRICE_SONNET_USD = (3.0, 15.0)
PRICE_OPUS_USD = (5.0, 25.0)
PRICE_PER_MILLION_USD: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": PRICE_HAIKU_USD,
    "claude-sonnet-5": PRICE_SONNET_USD,
    "claude-opus-5": PRICE_OPUS_USD,
}
DEFAULT_MAX_COST_USD = 5.0


class AbortRun(Exception):
    """Stop the lab after repeated 429 / quota errors or a cost cap."""


class SpellAppError(Exception):
    def __init__(self, message: str, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


def is_claude_model(name: str) -> bool:
    return str(name or "").strip().lower().startswith("claude-")


def prices_for(model: str) -> tuple[float, float]:
    # 공식 가격표 확인 필요.
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
    return (0.10, 0.40)


def usage_tokens(result: dict[str, Any] | None) -> tuple[int, int]:
    result = result or {}
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else None
    meta = result.get("usage_metadata") if isinstance(result.get("usage_metadata"), dict) else None
    src = usage or meta or {}
    inp = src.get("input_tokens") or src.get("promptTokenCount") or src.get("prompt_token_count") or 0
    out = (
        src.get("output_tokens")
        or src.get("candidatesTokenCount")
        or src.get("candidates_token_count")
        or 0
    )
    thoughts = src.get("thoughtsTokenCount") or src.get("thoughts_token_count") or 0
    try:
        inp_i = int(inp or 0)
    except (TypeError, ValueError):
        inp_i = 0
    try:
        out_i = int(out or 0) + int(thoughts or 0)
    except (TypeError, ValueError):
        out_i = 0
    return inp_i, out_i


class CostGuard:
    """Accumulate estimated USD cost from usage and stop past --max-cost."""

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
                "temperature_omitted": 0,
                "empty": 0,
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
        temperature_omitted: bool = False,
        empty: bool = False,
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
        if temperature_omitted:
            row["temperature_omitted"] += 1
        if empty:
            row["empty"] += 1
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


def is_rate_limit_error(error: GeminiError) -> bool:
    return error.http_status == 429 or error.code in {"rate_limit", "quota"}


def generate_or_abort(prompt: str, *, where: str, **kwargs: Any) -> dict[str, Any]:
    model = str(kwargs.get("model") or "")
    try:
        if is_claude_model(model):
            thinking = str(kwargs.get("claude_thinking") or kwargs.get("thinking") or "off")
            return claude_call.generate(
                prompt,
                model=model,
                system=kwargs.get("system"),
                temperature=float(kwargs.get("temperature") or 0.2),
                max_tokens=int(
                    kwargs.get("max_output_tokens")
                    or kwargs.get("max_tokens")
                    or claude_call.claude_max_tokens(thinking)
                ),
                timeout=float(kwargs.get("timeout") or claude_call.DEFAULT_TIMEOUT),
                thinking=thinking,
            )
        return gemini_call.generate(prompt, **kwargs)
    except GeminiError as error:
        if is_rate_limit_error(error):
            raise AbortRun(
                f"{where}: HTTP {error.http_status or '?'} code={error.code}"
            ) from error
        raise


def gemini_meta(result: dict[str, Any] | None) -> dict[str, Any]:
    result = result or {}
    return {
        "finish_reason": result.get("finish_reason"),
        "usage_metadata": result.get("usage_metadata"),
        "usage": result.get("usage"),
        "empty": bool(result.get("empty")),
        "thinking_budget": result.get("thinking_budget"),
        "prompt_feedback": result.get("prompt_feedback"),
        "candidate_count": result.get("candidate_count"),
        "retries": result.get("retries"),
        "temperature_omitted": result.get("temperature_omitted"),
        "sampling_omitted": result.get("sampling_omitted"),
        "thinking_disabled": result.get("thinking_disabled"),
        "thinking": result.get("thinking"),
        "stop_reason": result.get("stop_reason") or result.get("finish_reason"),
        "truncated": bool(result.get("truncated")),
    }


def derive_v2_verdict(parsed: Any) -> dict[str, Any]:
    data = parsed if isinstance(parsed, dict) else {}
    source = data.get("source_units") if isinstance(data.get("source_units"), list) else []
    new_units = data.get("new_units") if isinstance(data.get("new_units"), list) else []
    reversals = data.get("reversals") if isinstance(data.get("reversals"), list) else []
    repeats = data.get("repeats") if isinstance(data.get("repeats"), list) else []
    removal_hits: list[Any] = []
    for item in source:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").strip().lower()
        if status in {"removed", "changed"} and not bool(item.get("declared")):
            removal_hits.append(item)
    addition_hits: list[Any] = []
    for item in new_units:
        if not isinstance(item, dict):
            continue
        if not bool(item.get("declared")):
            addition_hits.append(item)
    return {
        "undeclared_removals": bool(removal_hits),
        "undeclared_additions": bool(addition_hits),
        "logic_reversed": len(reversals) > 0,
        "repeats_flagged_pattern": len(repeats) > 0,
        "undeclared_removal_units": removal_hits,
        "undeclared_addition_units": addition_hits,
        "reversals": reversals,
        "repeats": repeats,
        "source_units": source,
        "new_units": new_units,
    }


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


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


def load_cases() -> dict[str, Any]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def parse_case_ids(raw: str) -> list[str] | None:
    text = (raw or "all").strip()
    if not text or text.lower() == "all":
        return None
    return [part.strip() for part in text.split(",") if part.strip()]


def type_guidance_text(guidance: dict[str, Any], style_type: str) -> str:
    entry = guidance.get(style_type) if isinstance(guidance, dict) else None
    if not isinstance(entry, dict):
        entry = guidance.get("other") if isinstance(guidance, dict) else None
    if not isinstance(entry, dict):
        return "(해당 없음)"
    return str(entry.get("guidance") or "(해당 없음)")


def load_prompts(verify_version: str = "v1", card_version: str = "v1") -> dict[str, str]:
    verify_name = "verify_prompt_v2.txt" if verify_version == "v2" else "verify_prompt.txt"
    card_name, schema_name = CARD_PROMPT_FILES.get(
        card_version, ("card_prompt.txt", "card_schema.txt")
    )
    return {
        "system": load_text(PROMPTS_DIR / "system.txt"),
        "card": load_text(PROMPTS_DIR / card_name),
        "schema": load_text(PROMPTS_DIR / schema_name),
        "verify": load_text(PROMPTS_DIR / verify_name),
        "verify_version": verify_version,
        "card_version": card_version,
        "guidance": load_text(PROMPTS_DIR / "type_guidance.json"),
    }


def load_manuscript_cache(bundle: dict[str, Any]) -> tuple[dict[str, list], list[str]]:
    missing: list[str] = []
    cache: dict[str, list] = {}
    meta = bundle.get("manuscripts") or {}
    for key, info in meta.items():
        filename = str((info or {}).get("file") or f"{key}.md")
        path = MANUSCRIPTS_DIR / filename
        if not path.is_file():
            missing.append(f"{key} ({filename})")
            continue
        cache[key] = manuscript.parse_paragraphs(load_text(path))
    return cache, missing


def project_for(bundle: dict[str, Any], manuscript_key: str) -> dict[str, Any] | None:
    ms = (bundle.get("manuscripts") or {}).get(manuscript_key) or {}
    project_key = ms.get("project")
    projects = bundle.get("projects") or {}
    found = projects.get(project_key) if project_key else None
    return found if isinstance(found, dict) else None


def as_card(parsed: Any) -> dict[str, Any]:
    if isinstance(parsed, list) and parsed:
        parsed = parsed[0]
    if isinstance(parsed, dict):
        return parsed
    return {}


def dumps_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def locate_or_skip(
    *,
    case_id: str,
    manuscript_key: str,
    start_quote: str,
    end_quote: str,
    cache: dict[str, list],
    missing_ms: list[str],
    skipped: list[dict[str, str]],
) -> tuple[list | None, manuscript.RangeHit | None]:
    if manuscript_key not in cache:
        reason = f"원고 파일 없음: {manuscript_key}"
        print(f"건너뜀 {case_id}: {reason}", flush=True)
        skipped.append({"id": case_id, "reason": reason, "quote_kind": "", "quote": ""})
        return None, None
    paragraphs = cache[manuscript_key]
    try:
        hit = manuscript.locate_quote_range(
            paragraphs,
            start_quote,
            end_quote,
            case_id=case_id,
            manuscript=manuscript_key,
        )
    except manuscript.LocateError as error:
        print(str(error), flush=True)
        skipped.append(
            {
                "id": case_id,
                "reason": str(error),
                "quote_kind": error.quote_kind,
                "quote": error.quote,
            }
        )
        return None, None
    return paragraphs, hit


def run_card_case(
    case: dict[str, Any],
    *,
    bundle: dict[str, Any],
    cache: dict[str, list],
    missing_ms: list[str],
    prompts: dict[str, str],
    guidance: dict[str, Any],
    models: list[str],
    runs: int,
    skipped: list[dict[str, str]],
    json_mime_ok: dict[str, bool],
    halt: list[str],
    thinking_budget: int | None = None,
    verify_version: str = "v1",
    verify_max_tokens: int = 1024,
    card_version: str = "v1",
    ai_verify: bool = True,
    cost_guard: CostGuard | None = None,
    claude_thinking: str = "off",
) -> dict[str, Any] | None:
    case_id = str(case.get("id") or "")
    ms_key = str(case.get("manuscript") or "")
    paragraphs, hit = locate_or_skip(
        case_id=case_id,
        manuscript_key=ms_key,
        start_quote=str(case.get("start_quote") or ""),
        end_quote=str(case.get("end_quote") or ""),
        cache=cache,
        missing_ms=missing_ms,
        skipped=skipped,
    )
    if paragraphs is None or hit is None:
        return None
    project = project_for(bundle, ms_key)
    kind = str(case.get("kind") or "style")
    style_type = str(case.get("style_type") or "other")
    perspectives = case.get("perspectives") or []
    if not isinstance(perspectives, list):
        perspectives = [perspectives]
    total = paragraphs[-1].number if paragraphs else 0
    target_nums = list(range(hit.start_para, hit.end_para + 1))
    context_nums = manuscript.context_numbers(hit.start_para, hit.end_para, total)
    before_txt, after_txt = manuscript.in_paragraph_outside(paragraphs, hit)
    values = {
        "card_schema": prompts["schema"],
        "item_id": case_id,
        "kind": kind,
        "style_type": style_type,
        "certainty": case.get("certainty") or "",
        "item_title": case.get("item_title") or "",
        "item_body": case.get("item_body") or "",
        "perspectives": ", ".join(str(p) for p in perspectives),
        "suggestion_enabled": "true",
        "type_guidance": type_guidance_text(guidance, style_type),
        "start_para": str(hit.start_para),
        "end_para": str(hit.end_para),
        "target_paragraphs_text": manuscript.format_para_block(paragraphs, target_nums),
        "target_range_text": hit.original_text or "(없음)",
        "before_in_paragraph": before_txt,
        "after_in_paragraph": after_txt,
        "context_paragraphs_text": manuscript.format_para_block(paragraphs, context_nums),
        "strength_ranges_text": "(없음)",
        "project_terms_text": manuscript.format_project_terms(project),
        "alternate_rule": "",
    }
    user_prompt = fill_template(prompts["card"], values)
    temperature = 0.4 if kind == "style" else 0.2
    model_runs: list[dict[str, Any]] = []

    def bill_call(
        model_name: str,
        result: dict[str, Any] | None = None,
        *,
        failed: bool = False,
    ) -> None:
        if cost_guard is None:
            return
        inp, out = usage_tokens(result)
        cost_guard.add_call(
            model=model_name,
            input_tokens=inp,
            output_tokens=out,
            failed=failed,
            retries=int((result or {}).get("retries") or 0),
            temperature_omitted=bool((result or {}).get("temperature_omitted")),
            empty=bool((result or {}).get("empty") or failed),
            truncated=bool((result or {}).get("truncated")),
            case_id=case_id,
        )

    for model in models:
        claude = is_claude_model(model)
        timeout = 60 if claude else 30
        claude_tokens = claude_call.claude_max_tokens(claude_thinking) if claude else 2048
        for run_i in range(1, runs + 1):
            print(f"카드 {case_id} · {model} · run {run_i}/{runs}", flush=True)
            record: dict[str, Any] = {
                "model": model,
                "run": run_i,
                "temperature": None if (claude and claude_call.is_sampling_locked_model(model)) else temperature,
                "max_output_tokens": claude_tokens if claude else 2048,
                "timeout": timeout,
                "json_output": not claude,
                "provider": "claude" if claude else "gemini",
                "claude_thinking": claude_thinking if claude else None,
            }
            try:
                result = generate_or_abort(
                    user_prompt,
                    where=f"카드 {case_id} {model} run {run_i}",
                    model=model,
                    system=prompts["system"],
                    temperature=temperature,
                    max_output_tokens=claude_tokens if claude else 2048,
                    timeout=timeout,
                    json_output=not claude,
                    thinking_budget=None if claude else thinking_budget,
                    claude_thinking=claude_thinking,
                )
            except AbortRun as error:
                record["error"] = {
                    "code": "cost_limit" if str(error).startswith("비용 한도") else "rate_limit",
                    "http_status": None if str(error).startswith("비용 한도") else 429,
                    "message": str(error),
                }
                model_runs.append(record)
                halt.append(str(error))
                break
            except (GeminiError, claude_call.ClaudeError) as error:
                record["error"] = {
                    "code": error.code,
                    "http_status": error.http_status,
                    "message": str(error),
                    "retries": getattr(error, "retries", None),
                }
                if error.http_status in {404, 400}:
                    record["error"]["hint"] = (
                        f"모델 이름이 유효하지 않을 수 있습니다: {model}"
                    )
                try:
                    bill_call(model, failed=True)
                except AbortRun as cost_error:
                    halt.append(str(cost_error))
                    model_runs.append(record)
                    break
                model_runs.append(record)
                fatal = (
                    isinstance(error, claude_call.ClaudeError)
                    or error.http_status in {401, 403}
                    or error.code in {"auth"}
                )
                if fatal:
                    halt.append(
                        f"{case_id} {model}: HTTP {error.http_status or '?'} code={error.code}"
                    )
                    break
                continue
            json_mime_ok[model] = bool(result.get("json_mime_used"))
            record.update(gemini_meta(result))
            over_cost = False
            try:
                bill_call(model, result, failed=bool(result.get("empty")))
            except AbortRun as error:
                over_cost = True
                halt.append(str(error))
            if result.get("empty"):
                record["error"] = {
                    "code": "empty",
                    "message": "빈 응답",
                    "finish_reason": result.get("finish_reason"),
                    "usage_metadata": result.get("usage_metadata"),
                }
                model_runs.append(record)
                if over_cost or halt:
                    break
                continue
            card = as_card(result.get("parsed"))
            record["json_mime_used"] = result.get("json_mime_used")
            record["parse_ok"] = result.get("parsed") is not None
            record["raw"] = result.get("raw") if result.get("parsed") is None else None
            record["card"] = card
            record["reason"] = card.get("reason")
            record["edit_plan"] = card.get("edit_plan")
            record["suggestion"] = card.get("suggestion")
            record["added_facts"] = card.get("added_facts") if "added_facts" in card else []
            record["removed_facts"] = card.get("removed_facts") if "removed_facts" in card else []
            record["confidence"] = card.get("confidence")
            if card_version in CASE_RANGE_VERSIONS:
                record["range_source"] = "case"
                record["start_para"] = hit.start_para
                record["end_para"] = hit.end_para
                record["start_quote"] = hit.start_quote
                record["end_quote"] = hit.end_quote
            record["rules"] = checks.run_rule_checks(
                paragraphs=paragraphs,
                start_para=hit.start_para,
                end_para=hit.end_para,
                original_text=hit.original_text,
                card=card,
                project=project,
                kind=kind,
                case_start_quote=hit.start_quote,
                case_end_quote=hit.end_quote,
                skip_v1=(card_version in CASE_RANGE_VERSIONS),
            )
            if not ai_verify:
                record["verify"] = {"skipped": True}
                model_runs.append(record)
                if over_cost or halt:
                    break
                continue
            verify_prompt = fill_template(
                prompts["verify"],
                {
                    "original_text": hit.original_text,
                    "suggestion": dumps_json(card.get("suggestion")),
                    "reason": card.get("reason") or "",
                    "added_facts": dumps_json(card.get("added_facts") or []),
                    "removed_facts": dumps_json(card.get("removed_facts") or []),
                },
            )
            try:
                verify = generate_or_abort(
                    verify_prompt,
                    where=f"카드검증 {case_id} {model} run {run_i}",
                    model=model,
                    system=prompts["system"],
                    temperature=0.0,
                    max_output_tokens=verify_max_tokens,
                    timeout=timeout,
                    json_output=not claude,
                    thinking_budget=None if claude else thinking_budget,
                )
                try:
                    bill_call(model, verify, failed=bool(verify.get("empty")))
                except AbortRun as error:
                    over_cost = True
                    halt.append(str(error))
                record["verify"] = {
                    "parsed": verify.get("parsed"),
                    "parse_ok": verify.get("parsed") is not None,
                    "raw": verify.get("raw") if verify.get("parsed") is None else None,
                    "json_mime_used": verify.get("json_mime_used"),
                    **gemini_meta(verify),
                }
                if verify_version == "v2":
                    derived = derive_v2_verdict(verify.get("parsed"))
                    record["verify"]["derived"] = derived
                json_mime_ok[model] = json_mime_ok.get(model, True) and bool(
                    verify.get("json_mime_used")
                )
                if verify.get("empty"):
                    record["verify"]["error"] = {
                        "code": "empty",
                        "message": "빈 응답",
                        "finish_reason": verify.get("finish_reason"),
                        "usage_metadata": verify.get("usage_metadata"),
                    }
                elif verify.get("parsed") is None:
                    finish = str(verify.get("finish_reason") or "")
                    record["verify"]["error"] = {
                        "code": "parse",
                        "message": "JSON 파싱 실패",
                        "finish_reason": verify.get("finish_reason"),
                        "usage_metadata": verify.get("usage_metadata"),
                        "truncated": finish.upper() in {"MAX_TOKENS", "LENGTH"},
                    }
            except AbortRun as error:
                record["verify"] = {
                    "error": {
                        "code": "cost_limit" if str(error).startswith("비용 한도") else "rate_limit",
                        "http_status": None if str(error).startswith("비용 한도") else 429,
                        "message": str(error),
                    }
                }
                model_runs.append(record)
                halt.append(str(error))
                break
            except (GeminiError, claude_call.ClaudeError) as error:
                record["verify"] = {
                    "error": {
                        "code": error.code,
                        "http_status": error.http_status,
                        "message": str(error),
                    }
                }
                try:
                    bill_call(model, failed=True)
                except AbortRun as cost_error:
                    halt.append(str(cost_error))
                    model_runs.append(record)
                    break
            model_runs.append(record)
            if over_cost or halt:
                break
        if halt:
            break
    return {
        "id": case_id,
        "manuscript": ms_key,
        "kind": kind,
        "style_type": style_type,
        "certainty": case.get("certainty"),
        "perspectives": perspectives,
        "item_title": case.get("item_title"),
        "item_body": case.get("item_body"),
        "check": case.get("check") or [],
        "start_para": hit.start_para,
        "end_para": hit.end_para,
        "original_text": hit.original_text,
        "start_quote": hit.start_quote,
        "end_quote": hit.end_quote,
        "runs": model_runs,
    }


def verdict_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return bool(value)


def compare_expect(expect: dict[str, Any], parsed: Any) -> dict[str, Any]:
    verdict = parsed if isinstance(parsed, dict) else {}
    rows: dict[str, Any] = {}
    expect = expect or {}
    for key in EXPECT_KEYS:
        actual = verdict.get(key)
        scored = key in expect
        if key in {"undeclared_additions", "undeclared_removals"}:
            got = verdict_bool(actual)
        else:
            got = verdict_bool(actual)
        wanted = bool(expect[key]) if scored else None
        rows[key] = {
            "wanted": wanted,
            "got": got,
            "ok": (got == wanted) if scored else None,
            "scored": scored,
            "value": actual,
        }
    rows["_logic_note"] = verdict.get("logic_note") if isinstance(verdict, dict) else None
    return rows


def compare_rule_expect(rule_expect: dict[str, Any], rules: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {str(item.get("id")): item for item in rules}
    rows: dict[str, Any] = {}
    for key, wanted in (rule_expect or {}).items():
        item = by_id.get(str(key))
        actual_ok = bool(item.get("ok")) if item else None
        want_fail = str(wanted).strip().lower() in {"fail", "false", "0"}
        want_ok = not want_fail if str(wanted).strip().lower() in {"fail", "pass", "ok", "true", "false"} else None
        if want_ok is None:
            continue
        rows[key] = {
            "wanted": "fail" if want_fail else "pass",
            "got": None if actual_ok is None else ("pass" if actual_ok else "fail"),
            "ok": actual_ok is not None and actual_ok == (not want_fail),
        }
    return rows


def run_verifier_cases(
    bundle: dict[str, Any],
    *,
    cache: dict[str, list],
    missing_ms: list[str],
    prompts: dict[str, str],
    models: list[str],
    ids: list[str] | None,
    skipped: list[dict[str, str]],
    json_mime_ok: dict[str, bool],
    halt: list[str],
    runs: int = 1,
    thinking_budget: int | None = None,
    verify_version: str = "v1",
    verify_max_tokens: int = 1024,
    card_version: str = "v1",
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for case in bundle.get("verifier_cases") or []:
        case_id = str(case.get("id") or "")
        if ids is not None and case_id not in ids:
            continue
        ms_key = str(case.get("manuscript") or "")
        paragraphs, hit = locate_or_skip(
            case_id=case_id,
            manuscript_key=ms_key,
            start_quote=str(case.get("start_quote") or ""),
            end_quote=str(case.get("end_quote") or ""),
            cache=cache,
            missing_ms=missing_ms,
            skipped=skipped,
        )
        if paragraphs is None or hit is None:
            continue
        project = project_for(bundle, ms_key)
        fake_card = {
            "start_quote": case.get("start_quote"),
            "end_quote": case.get("end_quote"),
            "start_para": hit.start_para,
            "end_para": hit.end_para,
            "reason": case.get("reason"),
            "suggestion": case.get("suggestion"),
            "added_facts": case.get("added_facts") or [],
            "removed_facts": case.get("removed_facts") or [],
        }
        rules = checks.run_rule_checks(
            paragraphs=paragraphs,
            start_para=hit.start_para,
            end_para=hit.end_para,
            original_text=hit.original_text,
            card=fake_card,
            project=project,
            kind="style",
            case_start_quote=hit.start_quote,
            case_end_quote=hit.end_quote,
        )
        rule_cmp = compare_rule_expect(case.get("rule_expect") or {}, rules)
        model_rows: list[dict[str, Any]] = []
        verify_prompt = fill_template(
            prompts["verify"],
            {
                "original_text": hit.original_text,
                "suggestion": case.get("suggestion") or "",
                "reason": case.get("reason") or "",
                "added_facts": dumps_json(case.get("added_facts") or []),
                "removed_facts": dumps_json(case.get("removed_facts") or []),
            },
        )
        for model in models:
            for run_i in range(1, max(1, int(runs or 1)) + 1):
                print(f"검증기 {case_id} · {model} · run {run_i}/{runs}", flush=True)
                row: dict[str, Any] = {"model": model, "run": run_i}
                try:
                    verify = generate_or_abort(
                        verify_prompt,
                        where=f"검증기 {case_id} {model} run {run_i}",
                        model=model,
                        system=prompts["system"],
                        temperature=0.0,
                        max_output_tokens=verify_max_tokens,
                        timeout=30,
                        json_output=True,
                        thinking_budget=thinking_budget,
                    )
                    json_mime_ok[model] = json_mime_ok.get(model, True) and bool(
                        verify.get("json_mime_used")
                    )
                    row.update(gemini_meta(verify))
                    row["parsed"] = verify.get("parsed")
                    row["parse_ok"] = verify.get("parsed") is not None
                    row["raw"] = verify.get("raw") if verify.get("parsed") is None else None
                    row["json_mime_used"] = verify.get("json_mime_used")
                    if verify.get("empty"):
                        row["error"] = {
                            "code": "empty",
                            "message": "빈 응답",
                            "finish_reason": verify.get("finish_reason"),
                            "usage_metadata": verify.get("usage_metadata"),
                        }
                    elif verify.get("parsed") is None:
                        finish = str(verify.get("finish_reason") or "")
                        row["error"] = {
                            "code": "parse",
                            "message": "JSON 파싱 실패",
                            "finish_reason": verify.get("finish_reason"),
                            "usage_metadata": verify.get("usage_metadata"),
                            "truncated": finish.upper() in {"MAX_TOKENS", "LENGTH"},
                        }
                    elif verify_version == "v2":
                        derived = derive_v2_verdict(verify.get("parsed"))
                        row["derived"] = derived
                        row["expect_cmp"] = compare_expect(case.get("expect") or {}, derived)
                    else:
                        row["expect_cmp"] = compare_expect(
                            case.get("expect") or {}, verify.get("parsed")
                        )
                except AbortRun as error:
                    row["error"] = {
                        "code": "rate_limit",
                        "http_status": 429,
                        "message": str(error),
                    }
                    model_rows.append(row)
                    halt.append(str(error))
                    break
                except GeminiError as error:
                    row["error"] = {
                        "code": error.code,
                        "http_status": error.http_status,
                        "message": str(error),
                    }
                    if error.http_status in {404, 400}:
                        row["error"]["hint"] = f"모델 이름이 유효하지 않을 수 있습니다: {model}"
                model_rows.append(row)
            if halt:
                break
        out.append(
            {
                "id": case_id,
                "manuscript": ms_key,
                "original_text": hit.original_text,
                "suggestion": case.get("suggestion"),
                "reason": case.get("reason"),
                "expect": case.get("expect") or {},
                "rule_expect": case.get("rule_expect") or {},
                "rules": rules,
                "rule_cmp": rule_cmp,
                "models": model_rows,
            }
        )
        if halt:
            break
    return out


def run_spellcheck_cases(
    bundle: dict[str, Any],
    *,
    cache: dict[str, list],
    skipped: list[dict[str, str]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, case in enumerate(bundle.get("spellcheck_cases") or [], start=1):
        ms_key = str(case.get("manuscript") or "")
        quote = str(case.get("quote") or "")
        expect_fix = str(case.get("expect_fix") or "")
        case_id = f"SP{index:02d}"
        if ms_key not in cache:
            reason = f"원고 파일 없음: {ms_key}"
            print(f"건너뜀 {case_id}: {reason}", flush=True)
            skipped.append({"id": case_id, "reason": reason, "quote_kind": "quote", "quote": quote})
            continue
        paragraphs = cache[ms_key]
        para = None
        for item in paragraphs:
            if manuscript.find_in_paragraph(item, quote) is not None or quote in item.text:
                para = item
                break
        if para is None:
            msg = f"[{case_id}] 맞춤법 quote를 찾지 못함 ({ms_key}): {quote!r}"
            print(msg, flush=True)
            skipped.append(
                {"id": case_id, "reason": msg, "quote_kind": "quote", "quote": quote}
            )
            continue
        print(f"맞춤법 {case_id} · P{para.number} · {quote!r}", flush=True)
        row: dict[str, Any] = {
            "id": case_id,
            "manuscript": ms_key,
            "quote": quote,
            "expect_fix": expect_fix,
            "paragraph": para.number,
        }
        try:
            payload = korean_speller.check_text(para.text)
        except korean_speller.SpellerError as error:
            row["error"] = str(error)
            out.append(row)
            continue
        errors = payload.get("errors") or []
        caught = []
        for err in errors:
            original = str(err.get("original") or "")
            if quote == original or quote in original or original in quote:
                caught.append(err)
        row["caught"] = bool(caught)
        row["engine_originals"] = [str(e.get("original") or "") for e in errors]
        suggestions: list[str] = []
        for err in caught or errors:
            for sug in err.get("suggestions") or []:
                suggestions.append(str(sug))
        row["engine_suggestions"] = suggestions
        row["fix_match"] = expect_fix in suggestions if expect_fix else False
        if caught:
            first = (caught[0].get("suggestions") or [None])
            row["first_suggestion"] = first[0] if first else None
            row["fix_match"] = expect_fix in (caught[0].get("suggestions") or [])
        out.append(row)
    return out


def is_spell_rate_limit(error: SpellAppError) -> bool:
    if error.http_status == 429:
        return True
    text = str(error).lower()
    tokens = ("429", "quota", "rate_limit", "rate limit", "resource_exhausted")
    return any(token in text for token in tokens)


def probe_app_url(base: str) -> bool:
    url = base.rstrip("/") + "/api/ai/status"
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            response.read(256)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def resolve_app_url(preferred: str | None) -> str | None:
    candidates: list[str] = []
    if preferred:
        candidates.append(preferred.rstrip("/"))
    for item in (DEFAULT_APP_URL, ALT_APP_URL):
        if item not in candidates:
            candidates.append(item)
    for url in candidates:
        if probe_app_url(url):
            return url
    return None


def post_app_spellcheck(base: str, text: str, prefer: str) -> dict[str, Any]:
    url = base.rstrip("/") + "/api/spellcheck"
    payload = json.dumps({"text": text, "prefer": prefer}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(detail)
        except json.JSONDecodeError:
            parsed = {"error": detail[:300]}
        raise SpellAppError(
            str(parsed.get("error") or parsed), http_status=int(error.code)
        ) from error


def run_spellcheck_via_app(
    bundle: dict[str, Any],
    *,
    cache: dict[str, list],
    skipped: list[dict[str, str]],
    app_url: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, case in enumerate(bundle.get("spellcheck_cases") or [], start=1):
        ms_key = str(case.get("manuscript") or "")
        quote = str(case.get("quote") or "")
        expect_fix = str(case.get("expect_fix") or "")
        case_id = f"SP{index:02d}"
        if ms_key not in cache:
            skipped.append({"id": case_id, "reason": f"원고 파일 없음: {ms_key}", "quote_kind": "quote", "quote": quote})
            continue
        para = None
        for item in cache[ms_key]:
            if manuscript.find_in_paragraph(item, quote) is not None or quote in item.text:
                para = item
                break
        if para is None:
            skipped.append({"id": case_id, "reason": f"quote 없음: {quote!r}", "quote_kind": "quote", "quote": quote})
            continue
        row: dict[str, Any] = {
            "id": case_id,
            "manuscript": ms_key,
            "quote": quote,
            "expect_fix": expect_fix,
            "paragraph": para.number,
            "via": "app",
            "app_url": app_url,
            "prefers": {},
        }
        for prefer in ("auto", "public"):
            print(f"앱 맞춤법 {case_id} prefer={prefer} · P{para.number}", flush=True)
            try:
                payload = post_app_spellcheck(app_url, para.text, prefer)
            except Exception as error:
                row["prefers"][prefer] = {"error": str(error)}
                continue
            errors = payload.get("errors") or []
            caught = []
            for err in errors:
                if not isinstance(err, dict):
                    continue
                original = str(err.get("original") or "")
                if quote == original or quote in original or original in quote:
                    caught.append(err)
            suggestions: list[str] = []
            for err in caught or errors:
                if not isinstance(err, dict):
                    continue
                for sug in err.get("suggestions") or []:
                    suggestions.append(str(sug))
            row["prefers"][prefer] = {
                "provider": payload.get("provider") or payload.get("provider_label"),
                "message": payload.get("message"),
                "caught": bool(caught),
                "fix_match": expect_fix in suggestions if expect_fix else False,
                "suggestions": suggestions[:8],
                "error_count": payload.get("error_count"),
            }
        out.append(row)
    return out


def _md_cell(text: Any) -> str:
    return str(text or "").replace("|", "\\|").replace("\r\n", " ").replace("\n", " ").strip()


def run_spell_clean(
    cache: dict[str, list],
    *,
    app_url: str,
    halt: list[str],
) -> dict[str, Any]:
    hits: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    last: dict[str, Any] | None = None
    consecutive_429 = 0
    first_call = True
    for ms_key in SPELL_CLEAN_KEYS:
        paragraphs = cache.get(ms_key) or []
        body = [p for p in paragraphs if getattr(p, "type", "") == "body" and str(p.text or "").strip()]
        para_hits = 0
        paras_with = set()
        checked = 0
        for para in body:
            last = {"manuscript": ms_key, "paragraph": para.number, "total_body": len(body)}
            if not first_call:
                time.sleep(SPELL_CLEAN_SLEEP)
            first_call = False
            print(
                f"깨끗한 원고 맞춤법 {ms_key} P{para.number} ({checked + 1}/{len(body)})",
                flush=True,
            )
            payload = None
            for attempt in range(3):
                try:
                    payload = post_app_spellcheck(app_url, para.text, "auto")
                    consecutive_429 = 0
                    break
                except SpellAppError as error:
                    if is_spell_rate_limit(error):
                        consecutive_429 += 1
                        msg = (
                            f"{ms_key} P{para.number}: HTTP {error.http_status or '?'} "
                            f"{error} (연속 {consecutive_429}회, 시도 {attempt + 1}/3)"
                        )
                        print(msg, flush=True)
                        if consecutive_429 >= SPELL_CLEAN_429_LIMIT or attempt >= 2:
                            halt.append(msg)
                            payload = None
                            break
                        time.sleep(5.0 * (attempt + 1))
                        continue
                    hits.append(
                        {
                            "manuscript": ms_key,
                            "paragraph": para.number,
                            "original": "",
                            "suggestions": [],
                            "help": "",
                            "error": str(error),
                        }
                    )
                    payload = None
                    break
                except Exception as error:
                    hits.append(
                        {
                            "manuscript": ms_key,
                            "paragraph": para.number,
                            "original": "",
                            "suggestions": [],
                            "help": "",
                            "error": str(error),
                        }
                    )
                    payload = None
                    break
            if halt:
                break
            checked += 1
            if payload is None:
                continue
            errors = payload.get("errors") or []
            for err in errors:
                if not isinstance(err, dict):
                    continue
                original = str(err.get("original") or "").strip()
                suggestions = err.get("suggestions") or []
                if isinstance(suggestions, str):
                    suggestions = [suggestions]
                suggestions = [str(s).strip() for s in suggestions if str(s).strip()]
                if not original and not suggestions:
                    continue
                para_hits += 1
                paras_with.add(para.number)
                hits.append(
                    {
                        "manuscript": ms_key,
                        "paragraph": para.number,
                        "original": original,
                        "suggestions": suggestions,
                        "help": str(err.get("help") or err.get("info") or "").strip(),
                        "provider": payload.get("provider") or payload.get("provider_label"),
                    }
                )
        summaries.append(
            {
                "manuscript": ms_key,
                "paragraphs": len(body),
                "checked": checked,
                "suggestion_count": para_hits,
                "paragraphs_with_suggestions": len(paras_with),
            }
        )
        if halt:
            break
    return {
        "app_url": app_url,
        "prefer": "auto",
        "sleep_seconds": SPELL_CLEAN_SLEEP,
        "summaries": summaries,
        "hits": hits,
        "last": last,
        "aborted": halt[-1] if halt else None,
    }


def write_spell_clean_md(path: Path, payload: dict[str, Any]) -> None:
    hits = payload.get("hits") or []
    suggestions = [h for h in hits if not h.get("error")]
    errors = [h for h in hits if h.get("error")]
    lines = [
        "# 깨끗한 원고 맞춤법 오탐",
        "",
        f"- 앱: {payload.get('app_url')}",
        f"- prefer: {payload.get('prefer')}",
        f"- 문단 사이 대기: {payload.get('sleep_seconds')}초",
        "",
        "## 요약",
        "",
        "| 원고 | 문단 수 | 검사한 문단 | 제안 총 개수 | 제안이 있는 문단 수 | 호출 실패 |",
        "|---|---|---|---|---|---|",
    ]
    errors_by_ms: dict[str, int] = {}
    for hit in errors:
        key = str(hit.get("manuscript") or "")
        errors_by_ms[key] = errors_by_ms.get(key, 0) + 1
    for row in payload.get("summaries") or []:
        ms = str(row.get("manuscript") or "")
        lines.append(
            f"| {ms} | {row.get('paragraphs')} | {row.get('checked')} | "
            f"{row.get('suggestion_count')} | {row.get('paragraphs_with_suggestions')} | "
            f"{errors_by_ms.get(ms, 0)} |"
        )
    lines.append("")
    if payload.get("aborted"):
        last = payload.get("last") or {}
        lines.append(
            f"- **중단:** {payload.get('aborted')} "
            f"(마지막: {last.get('manuscript')} P{last.get('paragraph')})"
        )
        lines.append("")
    lines.append("## 제안 목록")
    lines.append("")
    if not suggestions:
        lines.append("제안 없음.")
        lines.append("")
    else:
        lines.append("| 원고 | 문단 | 원문 조각 | 제안 | 이유 |")
        lines.append("|---|---|---|---|---|")
        for hit in suggestions:
            sug = ", ".join(hit.get("suggestions") or []) or "—"
            lines.append(
                f"| {hit.get('manuscript')} | {hit.get('paragraph')} | {_md_cell(hit.get('original'))} | "
                f"{_md_cell(sug)} | {_md_cell(hit.get('help')) or '—'} |"
            )
        lines.append("")
    if errors:
        lines.append("## 호출 실패")
        lines.append("")
        lines.append("| 원고 | 문단 | 이유 |")
        lines.append("|---|---|---|")
        for hit in errors:
            lines.append(
                f"| {hit.get('manuscript')} | {hit.get('paragraph')} | {_md_cell(hit.get('error'))} |"
            )
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def md_escape(text: Any) -> str:
    return str(text or "").replace("\r\n", "\n")


def verifier_flag_tuple(row: dict[str, Any]) -> dict[str, bool | None]:
    cmp = row.get("expect_cmp") or {}
    out: dict[str, bool | None] = {}
    for key in EXPECT_KEYS:
        item = cmp.get(key) or {}
        if row.get("error") or row.get("empty"):
            out[key] = None
        else:
            out[key] = bool(item.get("got"))
    return out


def _stability_md_lines(verifier: list[dict[str, Any]]) -> list[str]:
    lines = [
        "## 안정성 (같은 케이스·같은 모델, run 비교)",
        "",
        "| 케이스 | 모델 | 항목 | run1 | run2 | run3 | 3번 모두 같은가 |",
        "|---|---|---|---|---|---|---|",
    ]
    for case in verifier:
        by_model: dict[str, list[dict[str, Any]]] = {}
        for row in case.get("models") or []:
            by_model.setdefault(str(row.get("model") or ""), []).append(row)
        for model, rows in by_model.items():
            rows = sorted(rows, key=lambda r: int(r.get("run") or 1))
            flags = [verifier_flag_tuple(r) for r in rows]
            for key in EXPECT_KEYS:
                cells = []
                for i in range(3):
                    if i >= len(flags) or flags[i].get(key) is None:
                        cells.append("—")
                    else:
                        cells.append("true" if flags[i].get(key) else "false")
                present = [f.get(key) for f in flags if f.get(key) is not None]
                same = "예" if len(present) >= 2 and len(set(present)) == 1 and len(present) == len(flags) else "아니오"
                if len(present) < 2:
                    same = "—"
                elif len(set(present)) == 1 and len(present) == len(flags):
                    same = "예"
                else:
                    same = "아니오"
                lines.append(
                    f"| {case.get('id')} | {model} | `{key}` | {cells[0]} | {cells[1]} | {cells[2]} | {same} |"
                )
    lines.append("")
    return lines


def format_rules(rules: list[dict[str, Any]]) -> str:
    if not rules:
        return "(없음)"
    lines = []
    for item in rules:
        if item.get("warn") and item.get("ok"):
            flag = "경고"
        elif item.get("ok"):
            flag = "통과"
        else:
            flag = "실패"
        extra = {k: v for k, v in item.items() if k not in {"id", "ok"}}
        extra_s = f" {dumps_json(extra)}" if extra else ""
        lines.append(f"- {item.get('id')}: {flag}{extra_s}")
    return "\n".join(lines)


def write_report_md(path: Path, payload: dict[str, Any]) -> None:
    lines: list[str] = [
        "# 프롬프트 랩 결과",
        "",
        f"- 시각: {payload.get('created_at')}",
        f"- 모델: {', '.join(payload.get('models') or [])}",
        f"- JSON MIME (`responseMimeType=application/json`): {dumps_json(payload.get('json_mime'))}",
        f"- 검증 프롬프트: {payload.get('verify_prompt') or 'v1'}",
        f"- 카드 프롬프트: {payload.get('card_prompt') or 'v1'}",
        f"- thinkingBudget: {payload.get('thinking_budget')}",
        f"- AI 검증: {'켜짐' if payload.get('ai_verify', True) else '건너뜀'}",
        "",
    ]
    if payload.get("aborted"):
        lines.append(f"- **중단:** {payload.get('aborted')}")
        lines.append("")
    missing = payload.get("missing_manuscripts") or []
    if missing:
        lines.append("## 없는 원고")
        for item in missing:
            lines.append(f"- {item}")
        lines.append("")
    skipped = payload.get("skipped") or []
    if skipped:
        lines.append("## 건너뛴 케이스")
        for item in skipped:
            lines.append(f"- {item.get('id')}: {item.get('reason')}")
        lines.append("")

    for case in payload.get("card_runs") or []:
        lines.append(f"## {case.get('id')} — {case.get('item_title')}")
        lines.append("")
        lines.append(f"- 원고: `{case.get('manuscript')}` P{case.get('start_para')}–P{case.get('end_para')}")
        lines.append(f"- kind / type: `{case.get('kind')}` / `{case.get('style_type')}`")
        lines.append("")
        lines.append("### 원문 구간")
        lines.append("")
        lines.append("```")
        lines.append(md_escape(case.get("original_text")))
        lines.append("```")
        lines.append("")
        lines.append("### 입력한 항목")
        lines.append("")
        lines.append(f"- item_title: {case.get('item_title')}")
        lines.append("")
        lines.append(md_escape(case.get("item_body")))
        lines.append("")
        for run in case.get("runs") or []:
            title = f"### 모델 결과 — {run.get('model')}"
            if int(run.get("run") or 1) > 1:
                title += f" (run {run.get('run')})"
            lines.append(title)
            lines.append("")
            if run.get("error") or run.get("empty"):
                err = run.get("error") or {}
                lines.append(f"- 오류: {err.get('message') or '빈 응답'} (code={err.get('code')}, http={err.get('http_status')})")
                if err.get("hint"):
                    lines.append(f"- {err['hint']}")
                if run.get("empty") or err.get("code") == "empty":
                    lines.append(
                        f"- finishReason: {run.get('finish_reason') or err.get('finish_reason')}"
                    )
                    lines.append(
                        f"- usageMetadata: {dumps_json(run.get('usage_metadata') or err.get('usage_metadata'))}"
                    )
                lines.append("")
                continue
            if run.get("edit_plan"):
                lines.append(f"- edit_plan: {md_escape(run.get('edit_plan'))}")
            lines.append(f"- reason: {md_escape(run.get('reason'))}")
            lines.append(f"- suggestion: {md_escape(run.get('suggestion'))}")
            lines.append(f"- added_facts: {dumps_json(run.get('added_facts'))}")
            lines.append(f"- removed_facts: {dumps_json(run.get('removed_facts'))}")
            lines.append(f"- confidence: {run.get('confidence')}")
            lines.append(
                f"- 모델·옵션: model={run.get('model')}, temperature={run.get('temperature')}, "
                f"maxOutputTokens={run.get('max_output_tokens')}, timeout={run.get('timeout')}s, "
                f"json_mime={run.get('json_mime_used')}"
            )
            lines.append("")
            lines.append("#### 규칙 검사")
            lines.append("")
            lines.append(format_rules(run.get("rules") or []))
            lines.append("")
            verify = run.get("verify") or {}
            if verify.get("skipped") or payload.get("ai_verify") is False:
                lines.append("#### AI 검증")
                lines.append("")
                lines.append("건너뜀 (`--no-ai-verify`)")
                lines.append("")
                continue
            lines.append("#### AI 검증")
            lines.append("")
            if verify.get("error"):
                lines.append(dumps_json(verify["error"]))
                if verify.get("finish_reason") or (verify.get("error") or {}).get("finish_reason"):
                    lines.append(
                        f"- finishReason: {verify.get('finish_reason') or (verify.get('error') or {}).get('finish_reason')}"
                    )
            elif verify.get("parsed") is not None:
                lines.append("```json")
                lines.append(json.dumps(verify["parsed"], ensure_ascii=False, indent=2))
                lines.append("```")
            else:
                lines.append(
                    f"파싱 실패. finishReason={verify.get('finish_reason')}"
                )
                lines.append("raw:")
                lines.append("```")
                lines.append(md_escape(verify.get("raw")))
                lines.append("```")
            lines.append("")
        lines.append("")

    verifier = payload.get("verifier") or []
    if verifier:
        lines.append("## 검증기 단독 시험")
        lines.append("")
        lines.append("| 케이스 | 모델 | 항목 | 기대 | 실제 | 일치 |")
        lines.append("|---|---|---|---|---|---|")
        for case in verifier:
            for row in case.get("models") or []:
                model = row.get("model")
                if row.get("error") and row.get("error", {}).get("code") != "empty":
                    expect_cell = f"오류 {row['error'].get('code')}"
                    lines.append(
                        f"| {case.get('id')} | {row.get('model')} r{row.get('run') or 1} | (오류) | — | {expect_cell} | 아니오 |"
                    )
                    continue
                if row.get("empty") or (row.get("error") or {}).get("code") == "empty":
                    lines.append(
                        f"| {case.get('id')} | {row.get('model')} r{row.get('run') or 1} | (빈 응답) | — | "
                        f"finish={row.get('finish_reason')} | 아니오 |"
                    )
                    continue
                cmp = row.get("expect_cmp") or {}
                for key in EXPECT_KEYS:
                    item = cmp.get(key) or {}
                    wanted = item.get("wanted")
                    got = item.get("got")
                    scored = item.get("scored")
                    if scored is False:
                        want_s = "— (expect 없음)"
                        match_s = "—"
                    else:
                        want_s = "true" if wanted else "false"
                        match_s = "예" if item.get("ok") else "아니오"
                    got_s = "true" if got else "false"
                    lines.append(
                        f"| {case.get('id')} | {model} r{row.get('run') or 1} | `{key}` | {want_s} | {got_s} | {match_s} |"
                    )
        lines.append("")
        for case in verifier:
            lines.append(f"### {case.get('id')}")
            lines.append("")
            if case.get("rule_expect"):
                lines.append(f"- rule_expect: {dumps_json(case.get('rule_cmp') or case.get('rule_expect'))}")
                lines.append("")
            for row in case.get("models") or []:
                run_n = row.get("run") or 1
                lines.append(f"#### {row.get('model')} · run {run_n}")
                lines.append("")
                if row.get("empty") or (row.get("error") and row.get("error", {}).get("code") == "empty"):
                    lines.append(
                        f"- 빈 응답 finishReason={row.get('finish_reason') or (row.get('error') or {}).get('finish_reason')} "
                        f"usage={dumps_json(row.get('usage_metadata') or (row.get('error') or {}).get('usage_metadata'))}"
                    )
                    lines.append("")
                if row.get("error") and row.get("error", {}).get("code") != "empty":
                    lines.append(dumps_json(row["error"]))
                    lines.append("")
                    continue
                derived = row.get("derived") if isinstance(row.get("derived"), dict) else None
                parsed = row.get("parsed") if isinstance(row.get("parsed"), dict) else {}
                if derived:
                    lines.append("**v2 판정에 걸린 항목**")
                    lines.append("")
                    lines.append(f"- undeclared_removals: {dumps_json(derived.get('undeclared_removal_units') or [])}")
                    lines.append(f"- undeclared_additions: {dumps_json(derived.get('undeclared_addition_units') or [])}")
                    lines.append(f"- reversals: {dumps_json(derived.get('reversals') or [])}")
                    lines.append(f"- repeats: {dumps_json(derived.get('repeats') or [])}")
                    lines.append("")
                else:
                    lines.append(f"- logic_note: {md_escape(parsed.get('logic_note'))}")
                    lines.append(f"- undeclared_additions: {dumps_json(parsed.get('undeclared_additions') or [])}")
                    lines.append(f"- undeclared_removals: {dumps_json(parsed.get('undeclared_removals') or [])}")
                    lines.append("")
                if parsed:
                    lines.append("```json")
                    lines.append(json.dumps(parsed, ensure_ascii=False, indent=2))
                    lines.append("```")
                    lines.append("")
        if any(len(case.get("models") or []) > 1 for case in verifier):
            lines.extend(_stability_md_lines(verifier))
        lines.append("")

    spell = payload.get("spellcheck") or []
    if spell:
        lines.append("## 맞춤법 시험")
        lines.append("")
        if any(row.get("via") == "app" for row in spell):
            lines.append("| 케이스 | prefer | 검출 | expect_fix | 제안 | 일치 | provider |")
            lines.append("|---|---|---|---|---|---|---|")
            for row in spell:
                prefers = row.get("prefers") or {}
                for prefer in ("auto", "public"):
                    data = prefers.get(prefer) or {}
                    if data.get("error"):
                        lines.append(
                            f"| {row.get('id')} | {prefer} | 오류 | {row.get('expect_fix')} | {data.get('error')} | 아니오 | — |"
                        )
                        continue
                    caught = "예" if data.get("caught") else "아니오"
                    match = "예" if data.get("fix_match") else "아니오"
                    sug = ", ".join(data.get("suggestions") or []) or "—"
                    lines.append(
                        f"| {row.get('id')} | {prefer} | {caught} | {row.get('expect_fix')} | {sug} | {match} | {data.get('provider') or '—'} |"
                    )
        else:
            lines.append("| 케이스 | 원고 | quote | 검출 | expect_fix | 엔진 제안 | 일치 |")
            lines.append("|---|---|---|---|---|---|---|")
            for row in spell:
                if row.get("error"):
                    lines.append(
                        f"| {row.get('id')} | {row.get('manuscript')} | {row.get('quote')} | 오류 | {row.get('expect_fix')} | {row.get('error')} | 아니오 |"
                    )
                    continue
                caught = "예" if row.get("caught") else "아니오"
                match = "예" if row.get("fix_match") else "아니오"
                sug = ", ".join(row.get("engine_suggestions") or []) or "—"
                lines.append(
                    f"| {row.get('id')} | {row.get('manuscript')} | {row.get('quote')} | {caught} | {row.get('expect_fix')} | {sug} | {match} |"
                )
        lines.append("")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_scoring_md(path: Path, payload: dict[str, Any], rubric: dict[str, Any]) -> None:
    items = rubric.get("items") or []
    keys = [str(item.get("key")) for item in items]
    lines = [
        "# 채점표",
        "",
        rubric.get("scale") or "",
        "",
    ]
    for item in items:
        lines.append(
            f"- `{item.get('key')}` {item.get('label')}: "
            f"0={item.get('0')} / 1={item.get('1')} / 2={item.get('2')}"
        )
    extra = rubric.get("extra")
    if extra:
        lines.append(f"- {extra}")
    decision = rubric.get("decision_rule")
    if decision:
        lines.append(f"- 판정: {decision}")
    lines.append("")
    lines.append("빈칸을 0~2로 채운 뒤 `python tools/prompt_lab/score.py <이 파일>` 로 평균을 계산합니다.")
    lines.append("")
    header = ["case_id", "model", "run", "style_type"] + keys + ["vs_typetak", "memo"]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---"] * len(header)) + "|")
    for case in payload.get("card_runs") or []:
        for run in case.get("runs") or []:
            if run.get("error"):
                continue
            cells = [
                str(case.get("id")),
                str(run.get("model")),
                str(run.get("run") or 1),
                str(case.get("style_type") or ""),
            ]
            cells.extend([""] * len(keys))
            cells.extend(["", ""])
            lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _expect_match_ok(cmp: dict[str, Any]) -> bool | None:
    scored = [v for k, v in cmp.items() if k in EXPECT_KEYS and v.get("scored")]
    if not scored:
        return None
    return all(v.get("ok") for v in scored)


def print_verifier_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    print("\n검증기 단독 시험", flush=True)
    for case in rows:
        expect = case.get("expect") or {}
        print(f"\n{case.get('id')}  expect={dumps_json(expect)}", flush=True)
        rule_cmp = case.get("rule_cmp") or {}
        if rule_cmp:
            rule_cell = "OK" if all(v.get("ok") for v in rule_cmp.values()) else "FAIL"
            print(f"  rule_expect: {rule_cell} {dumps_json(rule_cmp)}", flush=True)
        for row in case.get("models") or []:
            model = str(row.get("model") or "")
            run_n = row.get("run") or 1
            label = f"{model} run{run_n}"
            if row.get("error") or row.get("empty"):
                err = row.get("error") or {}
                extra = ""
                if err.get("code") == "empty" or row.get("empty"):
                    extra = (
                        f" finish={row.get('finish_reason') or err.get('finish_reason')}"
                        f" usage={dumps_json(row.get('usage_metadata') or err.get('usage_metadata'))}"
                    )
                print(
                    f"  {label}: ERROR {err.get('code')} {err.get('message')}{extra}",
                    flush=True,
                )
                continue
            cmp = row.get("expect_cmp") or {}
            overall = _expect_match_ok(cmp)
            overall_s = "—" if overall is None else ("일치" if overall else "불일치")
            print(f"  {label}  전체:{overall_s}", flush=True)
            for key in EXPECT_KEYS:
                item = cmp.get(key) or {}
                if item.get("scored") is False:
                    want_s = "—"
                    match_s = "—"
                else:
                    want_s = "true" if item.get("wanted") else "false"
                    match_s = "일치" if item.get("ok") else "불일치"
                got_s = "true" if item.get("got") else "false"
                print(
                    f"    {key:<26} 기대 {want_s:<5} 실제 {got_s:<5} {match_s}",
                    flush=True,
                )
            derived = row.get("derived") if isinstance(row.get("derived"), dict) else None
            if derived:
                for label_k, key in (
                    ("undeclared_removals", "undeclared_removal_units"),
                    ("undeclared_additions", "undeclared_addition_units"),
                    ("reversals", "reversals"),
                    ("repeats", "repeats"),
                ):
                    items = derived.get(key) or []
                    if items:
                        print(f"    {label_k}: {dumps_json(items)}", flush=True)
            else:
                parsed = row.get("parsed") if isinstance(row.get("parsed"), dict) else {}
                note = parsed.get("logic_note") or ""
                if note:
                    print(f"    logic_note: {note}", flush=True)
                adds = parsed.get("undeclared_additions") or []
                rems = parsed.get("undeclared_removals") or []
                if adds:
                    print(f"    additions: {dumps_json(adds)}", flush=True)
                if rems:
                    print(f"    removals: {dumps_json(rems)}", flush=True)
        by_model: dict[str, list] = {}
        for row in case.get("models") or []:
            by_model.setdefault(str(row.get("model") or ""), []).append(row)
        for model, rows in by_model.items():
            if len(rows) < 2:
                continue
            flags = [verifier_flag_tuple(r) for r in sorted(rows, key=lambda r: int(r.get("run") or 1))]
            print(f"  {model} 안정성", flush=True)
            for key in EXPECT_KEYS:
                vals = [f.get(key) for f in flags]
                same = len({v for v in vals if v is not None}) <= 1 and None not in vals
                print(f"    {key:<26} {vals}  3번 모두 같은가: {'예' if same else '아니오'}", flush=True)


def print_spell_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    print("\n맞춤법 시험", flush=True)
    if any(row.get("via") == "app" for row in rows):
        print(f"{'id':<6} {'prefer':<8} {'caught':<8} {'fix':<8} provider", flush=True)
        for row in rows:
            prefers = row.get("prefers") or {}
            for prefer in ("auto", "public"):
                data = prefers.get(prefer) or {}
                if data.get("error"):
                    print(f"{row.get('id'):<6} {prefer:<8} ERR      {data.get('error')}", flush=True)
                    continue
                print(
                    f"{row.get('id'):<6} {prefer:<8} "
                    f"{'Y' if data.get('caught') else 'N':<8} "
                    f"{'Y' if data.get('fix_match') else 'N':<8} "
                    f"{data.get('provider') or ''}",
                    flush=True,
                )
        return
    print(f"{'id':<6} {'quote':<16} {'caught':<8} {'fix':<8}", flush=True)
    for row in rows:
        if row.get("error"):
            print(f"{row.get('id'):<6} {str(row.get('quote')):<16} ERR      {row.get('error')}", flush=True)
            continue
        print(
            f"{row.get('id'):<6} {str(row.get('quote')):<16} "
            f"{'Y' if row.get('caught') else 'N':<8} "
            f"{'Y' if row.get('fix_match') else 'N':<8}",
            flush=True,
        )


def parse_cases_for(items: list[str] | None) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for item in items or []:
        model, _, ids = str(item).partition("=")
        model = model.strip()
        if not model:
            continue
        out[model] = [part.strip() for part in ids.split(",") if part.strip()]
    return out


def models_for_case(case_id: str, models: list[str], cases_for: dict[str, list[str]]) -> list[str]:
    chosen: list[str] = []
    for model in models:
        allowed = cases_for.get(model)
        if allowed is not None and case_id not in allowed:
            continue
        chosen.append(model)
    return chosen


def planned_call_pairs(
    cases: list[dict[str, Any]],
    models: list[str],
    cases_for: dict[str, list[str]],
    runs: int,
    *,
    extra_verify: bool,
) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    copies = 2 if extra_verify else 1
    for case in cases:
        case_id = str(case.get("id") or "")
        for model in models_for_case(case_id, models, cases_for):
            for _ in range(max(1, runs) * copies):
                pairs.append((case_id, model))
    return pairs


def estimate_cost_usd(
    pairs: list[tuple[str, str]],
    reuse_payload: dict[str, Any] | None,
) -> float:
    avgs: dict[str, tuple[float, float]] = {}
    if reuse_payload:
        buckets: dict[str, list[tuple[int, int]]] = {}
        for case in reuse_payload.get("card_runs") or []:
            for run in case.get("runs") or []:
                model = str(run.get("model") or "")
                inp, out = usage_tokens(run)
                buckets.setdefault(model, []).append((inp, out))
        for model, items in buckets.items():
            if items:
                avgs[model] = (sum(i for i, _ in items) / len(items), 1000.0)
    total = 0.0
    for _case_id, model in pairs:
        inp, out = avgs.get(model, (4800.0, 1000.0))
        pin, pout = prices_for(model)
        total += (inp * pin + out * pout) / 1_000_000.0
    return total


def merge_reuse_runs(
    case: dict[str, Any],
    reuse_by: dict[str, dict[str, Any]],
    reuse_models: set[str],
) -> None:
    old = reuse_by.get(str(case.get("id")))
    if not old:
        return
    extra = [row for row in (old.get("runs") or []) if str(row.get("model") or "") in reuse_models]
    case["runs"] = extra + list(case.get("runs") or [])


def count_empty_truncated(payload: dict[str, Any], called_models: list[str]) -> tuple[int, int]:
    empty = 0
    truncated = 0
    called = set(called_models)
    for case in payload.get("card_runs") or []:
        for run in case.get("runs") or []:
            if str(run.get("model") or "") not in called:
                continue
            if run.get("empty") or (run.get("error") or {}).get("code") == "empty":
                empty += 1
            if run.get("truncated"):
                truncated += 1
    return empty, truncated


def default_models() -> list[str]:
    first = DEFAULT_MODEL
    models = [first]
    if FALLBACK_MODEL not in models:
        models.append(FALLBACK_MODEL)
    return models


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="SuperTory 첨삭 카드 프롬프트 랩")
    parser.add_argument("--models", nargs="+", default=None, help="모델 이름 (gemini-... 또는 claude-...)")
    parser.add_argument("--cases", default="all", help="all 또는 C01,R06")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--verifier-only", action="store_true")
    parser.add_argument("--spell-only", action="store_true")
    parser.add_argument(
        "--verify-prompt",
        choices=("v1", "v2"),
        default="v1",
        help="검증 프롬프트 버전 (기본 v1=verify_prompt.txt, v2=verify_prompt_v2.txt)",
    )
    parser.add_argument(
        "--card-prompt",
        choices=("v1", "v2", "v3", "v4"),
        default="v1",
        help="카드 프롬프트 버전 (v1 / v2 / v3 / v4)",
    )
    parser.add_argument(
        "--no-ai-verify",
        action="store_true",
        help="카드별 AI 검증 호출을 건너뜁니다. 규칙 검사(V6, V8, V9 등)는 그대로 실행합니다.",
    )
    parser.add_argument(
        "--max-cost",
        type=float,
        default=DEFAULT_MAX_COST_USD,
        help="추정 비용 한도(USD). 넘으면 중단합니다. 기본 5. 단가는 공식 가격표 확인 필요.",
    )
    parser.add_argument(
        "--claude-thinking",
        choices=("off", "on"),
        default="off",
        help="Claude Sonnet/Opus 5 thinking. 기본 off(비활성, max_tokens=4096). on이면 끄지 않고 max_tokens=16000.",
    )
    parser.add_argument(
        "--reuse-from",
        default=None,
        help="기존 results 폴더. 지정한 모델 결과는 다시 호출하지 않고 재사용합니다.",
    )
    parser.add_argument(
        "--reuse-models",
        nargs="+",
        default=None,
        help="--reuse-from 에서 가져올 모델 이름",
    )
    parser.add_argument(
        "--cases-for",
        action="append",
        default=[],
        help="특정 모델만 돌릴 케이스. 예: claude-opus-5=C03,C04",
    )
    parser.add_argument(
        "--thinking-budget",
        type=int,
        default=None,
        help="Gemini thinkingConfig.thinkingBudget. 0이면 생각 토큰을 끕니다.",
    )
    parser.add_argument(
        "--spell-via-app",
        action="store_true",
        help="앱 서버 /api/spellcheck 를 prefer=auto, public 으로 호출합니다.",
    )
    parser.add_argument(
        "--spell-clean",
        action="store_true",
        help="ep2.md·ep3.md 본문 문단을 prefer=auto 로 검사해 오탐을 모읍니다.",
    )
    parser.add_argument(
        "--app-url",
        default=None,
        help=f"앱 주소 (기본 {DEFAULT_APP_URL}, 없으면 {ALT_APP_URL})",
    )
    args = parser.parse_args(argv)

    models = args.models or default_models()
    models = [str(m).strip() for m in models if str(m).strip()]
    if not models:
        print("모델 이름이 비어 있습니다.", file=sys.stderr)
        return 2
    for name in models:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
            print(f"모델 이름이 유효하지 않습니다: {name}", file=sys.stderr)
            return 2

    exclusive_spell = args.spell_only or args.spell_via_app or args.spell_clean
    if not exclusive_spell:
        need_gemini = any(not is_claude_model(m) for m in models)
        need_claude = any(is_claude_model(m) for m in models)
        if need_gemini and not gemini_client.is_configured():
            print(
                "Gemini API 키가 없습니다. 프로젝트 폴더의 .env에 GEMINI_API_KEY를 넣어 주세요.",
                file=sys.stderr,
            )
            return 2
        if need_claude and not claude_call.is_configured():
            print(
                "Anthropic API 키가 없습니다. 프로젝트 폴더의 .env에 ANTHROPIC_API_KEY를 넣어 주세요.",
                file=sys.stderr,
            )
            return 2

    bundle = load_cases()
    verify_version = str(args.verify_prompt or "v1")
    card_version = str(args.card_prompt or "v1")
    thinking_budget = args.thinking_budget
    verify_max_tokens = 1024
    prompts = load_prompts(verify_version, card_version)
    guidance = json.loads(prompts["guidance"])
    cache, missing = load_manuscript_cache(bundle)
    if missing:
        print("없는 원고 파일: " + ", ".join(missing), flush=True)

    case_ids = parse_case_ids(args.cases)
    skipped: list[dict[str, str]] = []
    json_mime_ok: dict[str, bool] = {}
    card_runs: list[dict[str, Any]] = []
    verifier_rows: list[dict[str, Any]] = []
    spell_rows: list[dict[str, Any]] = []
    spell_clean: dict[str, Any] | None = None
    halt: list[str] = []
    app_url_used: str | None = None

    exclusive = args.verifier_only or args.spell_only or args.spell_via_app or args.spell_clean
    run_cards = not exclusive
    run_verifier = args.verifier_only or (not exclusive and case_ids is None)
    run_spell = args.spell_only or (not exclusive and case_ids is None)
    run_spell_app = bool(args.spell_via_app)
    run_spell_clean_flag = bool(args.spell_clean)

    if args.verifier_only and args.spell_only:
        run_cards = False
        run_verifier = True
        run_spell = True

    if args.no_ai_verify and not args.verifier_only:
        run_verifier = False

    cost_guard = CostGuard(max_cost=float(args.max_cost if args.max_cost is not None else DEFAULT_MAX_COST_USD))
    ai_verify = not bool(args.no_ai_verify)

    shared_kwargs = {
        "thinking_budget": thinking_budget,
        "verify_version": verify_version,
        "verify_max_tokens": verify_max_tokens,
        "card_version": card_version,
    }

    reuse_payload: dict[str, Any] | None = None
    reuse_models = [str(m).strip() for m in (args.reuse_models or []) if str(m).strip()]
    if args.reuse_from:
        reuse_path = Path(args.reuse_from)
        if not reuse_path.is_absolute():
            reuse_path = (ROOT / reuse_path).resolve()
        results_file = reuse_path / "results.json" if reuse_path.is_dir() else reuse_path
        if not results_file.is_file():
            print(f"재사용할 결과가 없습니다: {results_file}", file=sys.stderr)
            return 2
        reuse_payload = json.loads(results_file.read_text(encoding="utf-8"))
        if not reuse_models:
            reuse_models = [
                str(m)
                for m in (reuse_payload.get("models") or [])
                if str(m) not in models
            ]
        print(f"재사용: {results_file} / 모델 {', '.join(reuse_models)}", flush=True)

    cases_for = parse_cases_for(args.cases_for)
    claude_thinking = str(args.claude_thinking or "off")

    if run_cards:
        selected_cases = [
            case
            for case in (bundle.get("cases") or [])
            if case_ids is None or str(case.get("id") or "") in case_ids
        ]
        n_runs = max(1, int(args.runs or 1))
        pairs = planned_call_pairs(selected_cases, models, cases_for, n_runs, extra_verify=ai_verify)
        est = estimate_cost_usd(pairs, reuse_payload)
        print(
            f"예상 호출: {len(pairs)}회"
            + (" (카드+검증)" if ai_verify else " (AI 검증 없음)")
            + f", 예상 비용 ${est:.4f} (출력 가정 1000토큰/회, 공식 가격표 확인 필요), "
            f"한도 ${cost_guard.max_cost:.2f}",
            flush=True,
        )
        reuse_by = {
            str(c.get("id")): c
            for c in ((reuse_payload or {}).get("card_runs") or [])
        }
        for case in selected_cases:
            case_id = str(case.get("id") or "")
            case_models = models_for_case(case_id, models, cases_for)
            if not case_models:
                continue
            result = run_card_case(
                case,
                bundle=bundle,
                cache=cache,
                missing_ms=missing,
                prompts=prompts,
                guidance=guidance,
                models=case_models,
                runs=n_runs,
                skipped=skipped,
                json_mime_ok=json_mime_ok,
                halt=halt,
                ai_verify=ai_verify,
                cost_guard=cost_guard,
                claude_thinking=claude_thinking,
                **shared_kwargs,
            )
            if result is not None:
                if reuse_models:
                    merge_reuse_runs(result, reuse_by, set(reuse_models))
                card_runs.append(result)
            if halt:
                print(f"중단: {halt[-1]}", flush=True)
                break

    verifier_ids = None
    if args.verifier_only and case_ids:
        verifier_ids = case_ids
    if run_verifier:
        if halt:
            print("한도 중단으로 검증기 호출을 건너뜁니다.", flush=True)
        else:
            verifier_rows = run_verifier_cases(
                bundle,
                cache=cache,
                missing_ms=missing,
                prompts=prompts,
                models=models,
                ids=verifier_ids,
                skipped=skipped,
                json_mime_ok=json_mime_ok,
                halt=halt,
                runs=max(1, int(args.runs or 1)),
                **shared_kwargs,
            )
            print_verifier_table(verifier_rows)
            if halt:
                print(f"429/한도 반복으로 중단: {halt[-1]}", flush=True)

    if run_spell_app:
        app_url_used = resolve_app_url(args.app_url)
        if not app_url_used:
            print(
                "앱 서버가 꺼져 있어 --spell-via-app 을 건너뜁니다. "
                f"{DEFAULT_APP_URL} 또는 {ALT_APP_URL} 에서 /api/spellcheck 를 받을 수 없습니다. "
                "저장소 루트에서 start_supertory.bat 또는 python app.py 로 켠 뒤 다시 실행하세요.",
                flush=True,
            )
        else:
            print(f"앱 맞춤법: {app_url_used}", flush=True)
            spell_rows = run_spellcheck_via_app(
                bundle, cache=cache, skipped=skipped, app_url=app_url_used
            )
            print_spell_table(spell_rows)
    elif run_spell:
        spell_rows = run_spellcheck_cases(bundle, cache=cache, skipped=skipped)
        print_spell_table(spell_rows)

    if run_spell_clean_flag:
        app_url_used = app_url_used or resolve_app_url(args.app_url)
        if not app_url_used:
            print(
                "앱 서버가 꺼져 있어 --spell-clean 을 건너뜁니다. "
                f"{DEFAULT_APP_URL} 또는 {ALT_APP_URL} 에서 /api/spellcheck 를 받을 수 없습니다. "
                "저장소 루트에서 start_supertory.bat 또는 python app.py 로 켠 뒤 다시 실행하세요.",
                flush=True,
            )
            spell_clean = {
                "app_url": None,
                "prefer": "auto",
                "sleep_seconds": SPELL_CLEAN_SLEEP,
                "summaries": [],
                "hits": [],
                "last": None,
                "aborted": "앱 서버 없음",
            }
        else:
            print(f"깨끗한 원고 맞춤법: {app_url_used}", flush=True)
            missing_clean = [key for key in SPELL_CLEAN_KEYS if key not in cache]
            if missing_clean:
                print("없는 원고: " + ", ".join(missing_clean), flush=True)
            spell_clean = run_spell_clean(cache, app_url=app_url_used, halt=halt)
            for row in spell_clean.get("summaries") or []:
                print(
                    f"  {row.get('manuscript')}: 문단 {row.get('paragraphs')} "
                    f"(검사 {row.get('checked')}) · 제안 {row.get('suggestion_count')} · "
                    f"제안 있는 문단 {row.get('paragraphs_with_suggestions')}",
                    flush=True,
                )
            if halt:
                last = spell_clean.get("last") or {}
                print(
                    f"429/한도 반복으로 중단: {halt[-1]} "
                    f"(마지막 {last.get('manuscript')} P{last.get('paragraph')})",
                    flush=True,
                )

    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
    out_dir = LAB_DIR / "results" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    mime_summary = {
        model: json_mime_ok.get(model)
        for model in models
    }
    combined_models: list[str] = []
    for name in list(reuse_models or []) + list(models):
        if name and name not in combined_models:
            combined_models.append(name)
    payload_models = combined_models or models
    payload = {
        "created_at": stamp,
        "models": payload_models,
        "called_models": models,
        "reused_models": reuse_models,
        "reused_from": str(args.reuse_from) if args.reuse_from else None,
        "claude_thinking": str(args.claude_thinking or "off"),
        "json_mime": mime_summary,
        "gemini_client_supports_json_mime": False,
        "gemini_client_generate_text_args": [
            "prompt",
            "system",
            "temperature",
            "max_output_tokens",
            "timeout",
        ],
        "lab_wrapper": (
            "tools/prompt_lab/gemini_call.py, tools/prompt_lab/claude_call.py"
        ),
        "missing_manuscripts": missing,
        "skipped": skipped,
        "aborted": halt[-1] if halt else None,
        "verify_prompt": verify_version,
        "card_prompt": card_version,
        "thinking_budget": thinking_budget,
        "ai_verify": ai_verify,
        "app_url": app_url_used,
        "card_runs": card_runs,
        "verifier": verifier_rows,
        "spellcheck": spell_rows,
        "spell_clean": spell_clean,
        "cost": cost_guard.as_dict(),
    }
    (out_dir / "results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_report_md(out_dir / "report.md", payload)
    write_scoring_md(out_dir / "scoring.md", payload, bundle.get("rubric") or {})
    empty_n, trunc_n = count_empty_truncated(payload, models)
    if empty_n or trunc_n:
        print(f"빈 응답 {empty_n}건, truncated {trunc_n}건", flush=True)
    else:
        print("빈 응답 0건, truncated 0건", flush=True)
    if args.reuse_from:
        write_cost_summary_md(out_dir / "cost_summary_v2.md", payload)
        if payload.get("card_runs"):
            write_blind_compare(
                out_dir / "compare_blind_v2.md",
                out_dir / "blind_key_v2.json",
                payload,
            )
            print(f"블라인드 비교: {out_dir / 'compare_blind_v2.md'}", flush=True)
        print(f"비용 요약: {out_dir / 'cost_summary_v2.md'}", flush=True)
    else:
        write_cost_summary_md(out_dir / "cost_summary.md", payload)
        if payload.get("card_runs") and len(payload.get("models") or []) >= 2:
            write_blind_compare(out_dir / "compare_blind.md", out_dir / "blind_key.json", payload)
            print(f"블라인드 비교: {out_dir / 'compare_blind.md'}", flush=True)
        print(f"비용 요약: {out_dir / 'cost_summary.md'}", flush=True)
    if spell_clean is not None:
        write_spell_clean_md(out_dir / "spell_clean.md", spell_clean)
    print(f"\n결과: {out_dir}", flush=True)
    return 1 if halt else 0


def _lite_run(case: dict[str, Any]) -> dict[str, Any] | None:
    runs = case.get("runs") or []
    for row in runs:
        if "lite" in str(row.get("model") or "") and not (
            row.get("error") and row.get("error", {}).get("code") not in {"empty", "parse"}
        ):
            return row
    return runs[0] if runs else None


def _rule_summary(rules: list[dict[str, Any]]) -> str:
    bits: list[str] = []
    for item in rules or []:
        rid = item.get("id")
        if item.get("warn") and item.get("ok"):
            extra = {
                k: v
                for k, v in item.items()
                if k not in {"id", "ok", "skipped", "warn"} and v not in (None, [], False)
            }
            bits.append(f"{rid} 경고" + (f" {dumps_json(extra)}" if extra else ""))
        elif not item.get("ok"):
            extra = {k: v for k, v in item.items() if k not in {"id", "ok", "skipped"}}
            bits.append(f"{rid} 실패 {dumps_json(extra)}")
    return "; ".join(bits) if bits else "통과"


def _verify_summary(verify: dict[str, Any] | None) -> str:
    verify = verify or {}
    if verify.get("error"):
        err = verify["error"]
        finish = err.get("finish_reason") or verify.get("finish_reason")
        extra = f" finishReason={finish}" if finish else ""
        return f"오류 {err.get('code')}{extra}"
    parsed = verify.get("parsed") if isinstance(verify.get("parsed"), dict) else {}
    derived = verify.get("derived") if isinstance(verify.get("derived"), dict) else None
    src = derived or parsed
    flags = []
    for key in EXPECT_KEYS:
        val = src.get(key) if isinstance(src, dict) else None
        if derived:
            flags.append(f"{key}={'T' if val else 'F'}")
        elif key in {"undeclared_additions", "undeclared_removals"}:
            flags.append(f"{key}={'T' if verdict_bool(val) else 'F'}")
        else:
            flags.append(f"{key}={'T' if verdict_bool(val) else 'F'}")
    return ", ".join(flags) if flags else "(없음)"


def write_card_v1_v2_compare(
    path: Path,
    v1: dict[str, Any],
    v2: dict[str, Any],
    *,
    left_name: str = "v1",
    right_name: str = "v2",
) -> None:
    v1_by = {str(c.get("id")): c for c in v1.get("card_runs") or []}
    v2_by = {str(c.get("id")): c for c in v2.get("card_runs") or []}
    order = [
        "C01", "C02", "C03", "C04", "C05", "C06", "C07",
        "D01", "D02", "D03", "D04",
        "R01", "R02", "R03", "R04", "R05", "R06", "R07", "R08",
        "X01", "X02", "X03", "X03b", "X04",
    ]
    ids = [i for i in order if i in v1_by or i in v2_by]
    for extra in list(v2_by) + list(v1_by):
        if extra not in ids:
            ids.append(extra)
    lines = [
        f"# 카드 프롬프트 {left_name} vs {right_name}",
        "",
        f"- {left_name}: {v1.get('created_at')} / 카드 `{v1.get('card_prompt') or left_name}`",
        f"- {right_name}: {v2.get('created_at')} / 카드 `{v2.get('card_prompt') or right_name}`",
        "",
        "lite 모델(`gemini-flash-lite-latest`) 결과만 나란히 둡니다.",
        "",
    ]
    for case_id in ids:
        left = v1_by.get(case_id)
        right = v2_by.get(case_id)
        title = (right or left or {}).get("item_title") or ""
        lines.append(f"## {case_id} — {title}")
        lines.append("")
        orig = (right or left or {}).get("original_text") or ""
        lines.append("**원문**")
        lines.append("")
        lines.append("```")
        lines.append(md_escape(orig))
        lines.append("```")
        lines.append("")
        run1 = _lite_run(left) if left else None
        run2 = _lite_run(right) if right else None
        lines.append(f"**{left_name} suggestion**")
        lines.append("")
        if run1 and run1.get("edit_plan"):
            lines.append(f"- edit_plan: {md_escape(run1.get('edit_plan'))}")
            lines.append("")
        lines.append(md_escape(run1.get("suggestion") if run1 else "(없음)") or "(없음)")
        lines.append("")
        lines.append(f"**{right_name} suggestion**")
        lines.append("")
        if run2 and run2.get("edit_plan"):
            lines.append(f"- edit_plan: {md_escape(run2.get('edit_plan'))}")
            lines.append("")
        lines.append(md_escape(run2.get("suggestion") if run2 else "(없음)") or "(없음)")
        lines.append("")
        r1 = _rule_summary(run1.get("rules") if run1 else [])
        r2 = _rule_summary(run2.get("rules") if run2 else [])
        vsum1 = _verify_summary(run1.get("verify") if run1 else {})
        vsum2 = _verify_summary(run2.get("verify") if run2 else {})
        lines.append(f"| | {left_name} | {right_name} |")
        lines.append("|---|---|---|")
        lines.append(f"| 규칙 검사 | {r1.replace('|', '\\|')} | {r2.replace('|', '\\|')} |")
        lines.append(f"| AI 검증 | {vsum1.replace('|', '\\|')} | {vsum2.replace('|', '\\|')} |")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _run_for_model(case: dict[str, Any], model: str) -> dict[str, Any] | None:
    for row in case.get("runs") or []:
        if str(row.get("model") or "") == model:
            return row
    return None


def _rule_focus_summary(rules: list[dict[str, Any]], ids: tuple[str, ...] = ("V6", "V8", "V9")) -> str:
    by = {str(item.get("id")): item for item in rules or []}
    bits: list[str] = []
    for rid in ids:
        item = by.get(rid)
        if not item:
            bits.append(f"{rid} (없음)")
            continue
        if item.get("skipped"):
            bits.append(f"{rid} 건너뜀")
            continue
        extra = {
            k: v
            for k, v in item.items()
            if k not in {"id", "ok", "skipped", "warn"} and v not in (None, [], False)
        }
        extra_txt = f" {dumps_json(extra)}" if extra else ""
        if item.get("warn") and item.get("ok"):
            bits.append(f"{rid} 경고{extra_txt}")
        elif not item.get("ok"):
            bits.append(f"{rid} 실패{extra_txt}")
        else:
            bits.append(f"{rid} 통과")
    return "; ".join(bits)


def write_cost_summary_md(path: Path, payload: dict[str, Any]) -> None:
    cost = payload.get("cost") if isinstance(payload.get("cost"), dict) else {}
    lines = [
        "# 비용 요약",
        "",
        "단가는 상수이며 **공식 가격표 확인 필요**. 금액은 usage 토큰 × 단가 추정입니다.",
        "",
        f"- 한도: ${float(cost.get('max_cost_usd') or DEFAULT_MAX_COST_USD):.2f}",
        f"- 합계: ${float(cost.get('total_usd') or 0):.4f}",
    ]
    if cost.get("stopped"):
        last = cost.get("last") or {}
        lines.append(f"- **한도 초과로 중단:** 마지막 {last.get('case_id')} {last.get('model')}")
    if payload.get("aborted"):
        lines.append(f"- 중단 사유: {payload.get('aborted')}")
    lines.extend(
        [
            "",
            "| 모델 | 호출 수 | 입력 토큰 | 출력 토큰 | 추정 비용(USD) | 실패 | 빈 응답 | truncated | 재시도 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    rows = cost.get("models") or []
    by_name = {str(row.get("model")): row for row in rows if isinstance(row, dict)}
    order = list(payload.get("called_models") or payload.get("models") or [])
    for extra in by_name:
        if extra not in order:
            order.append(extra)
    for name in order:
        row = by_name.get(name) or {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0,
            "failures": 0,
            "retries": 0,
            "empty": 0,
            "truncated": 0,
        }
        lines.append(
            f"| {name} | {row.get('calls') or 0} | {row.get('input_tokens') or 0} | "
            f"{row.get('output_tokens') or 0} | {float(row.get('cost_usd') or 0):.4f} | "
            f"{row.get('failures') or 0} | {row.get('empty') or 0} | "
            f"{row.get('truncated') or 0} | {row.get('retries') or 0} |"
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_blind_compare(path: Path, key_path: Path, payload: dict[str, Any]) -> None:
    models = [str(m) for m in (payload.get("models") or [])]
    labels = [chr(ord("A") + i) for i in range(len(models))]
    rng = random.Random()
    seed = rng.randint(1, 10**9)
    rng.seed(seed)
    key: dict[str, Any] = {
        "created_at": payload.get("created_at"),
        "seed": seed,
        "cases": {},
    }
    lines = [
        "# 블라인드 비교",
        "",
        "모델 이름은 가렸습니다. 라벨은 케이스마다 순서가 다르며, 수정안 수만큼만 붙입니다.",
        "채점은 사람이 합니다.",
        "",
    ]
    order = [
        "C01", "C02", "C03", "C04", "C05", "C06", "C07",
        "D01", "D02", "D03", "D04",
        "R01", "R02", "R03", "R04", "R05", "R06", "R07", "R08",
        "X01", "X02", "X03", "X03b", "X04",
    ]
    cases = list(payload.get("card_runs") or [])
    by_id = {str(c.get("id")): c for c in cases}
    ids = [i for i in order if i in by_id]
    for extra in by_id:
        if extra not in ids:
            ids.append(extra)
    for case_id in ids:
        case = by_id[case_id]
        present = [
            model
            for model in models
            if _run_for_model(case, model) is not None
        ]
        if not present:
            continue
        case_labels = [chr(ord("A") + i) for i in range(len(present))]
        shuffled = list(present)
        rng.shuffle(shuffled)
        mapping = {label: model for label, model in zip(case_labels, shuffled)}
        key["cases"][case_id] = mapping
        title = case.get("item_title") or ""
        lines.append(f"## {case_id} — {title}")
        lines.append("")
        lines.append("**원문**")
        lines.append("")
        lines.append("```")
        lines.append(md_escape(case.get("original_text")))
        lines.append("```")
        lines.append("")
        for label in case_labels:
            model = mapping[label]
            run = _run_for_model(case, model)
            lines.append(f"### {label}")
            lines.append("")
            if run is None:
                lines.append("**(실행 안 됨)**")
                lines.append("")
                continue
            err = run.get("error") or {}
            if err:
                lines.append(f"- 오류: {err.get('message') or '실패'} (code={err.get('code')})")
                lines.append("")
                continue
            suggestion = run.get("suggestion")
            lines.append("**수정안**")
            lines.append("")
            lines.append("```")
            lines.append(md_escape(suggestion if suggestion is not None else "(없음)"))
            lines.append("```")
            lines.append("")
            if run.get("edit_plan"):
                lines.append(f"- edit_plan: {md_escape(run.get('edit_plan'))}")
            else:
                lines.append("- edit_plan: (없음)")
            lines.append(f"- 규칙 검사: {_rule_focus_summary(run.get('rules') or [])}")
            lines.append("")
        lines.append("")
    key_path.write_text(json.dumps(key, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
