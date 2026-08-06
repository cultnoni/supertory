"""Korean spelling / spacing checker via 바른한글(구 부산대) 공개 검사기.

Uses only the Python standard library. The remote site is Cloudflare-protected
and may block automated access; callers should handle SpellerError and use a
fallback (e.g. Gemini).
"""

from __future__ import annotations

import html as html_lib
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

SPELLER_URL = "https://nara-speller.co.kr/old_speller/results"
SPELLER_REFERER = "https://nara-speller.co.kr/old_speller/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
MAX_TOKENS_PER_CHUNK = 250
MAX_CHARS_TOTAL = 12000
MAX_RETRIES = 2

METHOD_LABELS = {
    1: "띄어쓰기·참고",
    2: "맞춤법·오용",
    3: "문맥·표현",
    4: "문체·순화",
    5: "부호·조사",
    6: "기타",
    7: "외래어·영문",
}


class SpellerError(Exception):
    """Raised when the external checker cannot be used."""


def _tokenize_for_limit(text: str) -> list[str]:
    return re.findall(r"\S+|\n+", text)


def chunk_text(text: str, max_tokens: int = MAX_TOKENS_PER_CHUNK) -> list[str]:
    cleaned = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not cleaned:
        return []
    if len(cleaned) > MAX_CHARS_TOTAL:
        cleaned = cleaned[:MAX_CHARS_TOTAL]

    tokens = _tokenize_for_limit(cleaned)
    if not tokens:
        return [cleaned]

    chunks: list[str] = []
    current: list[str] = []
    count = 0
    for token in tokens:
        is_break = token.startswith("\n")
        weight = 0 if is_break else 1
        if count + weight > max_tokens and current:
            chunks.append(_join_tokens(current).strip())
            current = []
            count = 0
        current.append(token)
        count += weight
    if current:
        chunks.append(_join_tokens(current).strip())
    return [c for c in chunks if c]


def _join_tokens(tokens: list[str]) -> str:
    out: list[str] = []
    for token in tokens:
        if token.startswith("\n"):
            out.append(token)
        else:
            if out and not out[-1].endswith("\n") and not out[-1].endswith(" "):
                out.append(" ")
            out.append(token)
    return "".join(out)


def _extract_json_after_data_equals(html: str) -> Any:
    """Parse `data = [...]` / `data = {...}` with string-aware bracket matching."""
    if "Just a moment" in html or "cf-browser-verification" in html:
        if "errInfo" not in html and "orgStr" not in html:
            raise SpellerError(
                "맞춤법 사이트가 자동 접속을 막고 있습니다(Cloudflare)."
            )

    match = re.search(r"\bdata\s*=\s*([\[{])", html)
    if not match:
        raise SpellerError(
            "맞춤법 결과를 해석하지 못했습니다. 사이트가 일시적으로 막혔거나 형식이 바뀌었을 수 있습니다."
        )

    start = match.start(1)
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(html)):
        ch = html[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                raw = html[start : i + 1]
                try:
                    return json.loads(raw)
                except json.JSONDecodeError as error:
                    raise SpellerError("맞춤법 결과 JSON을 읽지 못했습니다.") from error
    raise SpellerError("맞춤법 결과 블록이 중간에 끊겼습니다.")


def _post_chunk(text: str, timeout: float = 25.0) -> dict[str, Any]:
    payload = urllib.parse.urlencode({"text1": text.replace("\n", "\r\n")}).encode("utf-8")
    request = urllib.request.Request(
        SPELLER_URL,
        data=payload,
        method="POST",
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Origin": "https://nara-speller.co.kr",
            "Referer": SPELLER_REFERER,
        },
    )
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            html = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        if error.code in {403, 429, 503}:
            raise SpellerError(
                f"맞춤법 사이트가 요청을 거부했습니다({error.code}). 잠시 후 다시 시도하거나 Gemini 검사를 이용해 주세요."
            ) from error
        raise SpellerError(f"맞춤법 서버 응답 오류 ({error.code})") from error
    except urllib.error.URLError as error:
        raise SpellerError(f"맞춤법 서버에 연결하지 못했습니다: {error.reason}") from error
    except TimeoutError as error:
        raise SpellerError("맞춤법 서버 응답이 너무 늦습니다.") from error

    data = _extract_json_after_data_equals(html)
    if isinstance(data, list):
        if not data:
            return {"str": text, "errInfo": []}
        page = data[0] if isinstance(data[0], dict) else {}
        return page if isinstance(page, dict) else {"str": text, "errInfo": []}
    if isinstance(data, dict):
        return data
    return {"str": text, "errInfo": []}


def _normalize_error(item: dict[str, Any], offset: int) -> dict[str, Any] | None:
    original = str(item.get("orgStr") or "").strip()
    if not original:
        return None
    cand = str(item.get("candWord") or "")
    parts = re.split(r"[|｜\u001e]+", cand)
    suggestions = [p.strip() for p in parts if p and p.strip() and p.strip() != original]
    seen: set[str] = set()
    unique: list[str] = []
    for s in suggestions:
        if s not in seen:
            seen.add(s)
            unique.append(s)

    help_text = str(item.get("help") or "")
    help_text = re.sub(r"<[^>]+>", " ", help_text)
    help_text = re.sub(r"\s+", " ", html_lib.unescape(help_text)).strip()

    try:
        start = int(item.get("start", 0)) + offset
        end = int(item.get("end", start + len(original))) + offset
    except (TypeError, ValueError):
        start = offset
        end = offset + len(original)

    try:
        method = int(item.get("correctMethod", 0) or 0)
    except (TypeError, ValueError):
        method = 0

    return {
        "original": original,
        "suggestions": unique[:8],
        "help": help_text,
        "start": max(0, start),
        "end": max(start, end),
        "method": method,
        "method_label": METHOD_LABELS.get(method, "기타"),
    }


def check_text(text: str) -> dict[str, Any]:
    """Run spelling check on plain text. Returns structured errors."""
    plain = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not plain:
        return {
            "ok": True,
            "provider": "nara-speller",
            "provider_label": "바른한글(공개 맞춤법 검사)",
            "errors": [],
            "error_count": 0,
            "checked_chars": 0,
            "chunk_count": 0,
            "message": "검사할 글이 없습니다.",
        }

    chunks = chunk_text(plain)
    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            all_errors: list[dict[str, Any]] = []
            cursor = 0
            for chunk in chunks:
                idx = plain.find(chunk, cursor)
                if idx < 0:
                    idx = cursor
                page = _post_chunk(chunk)
                err_info = page.get("errInfo") or []
                if not isinstance(err_info, list):
                    err_info = []
                for item in err_info:
                    if not isinstance(item, dict):
                        continue
                    normalized = _normalize_error(item, offset=idx)
                    if normalized:
                        all_errors.append(normalized)
                cursor = idx + max(len(chunk), 1)

            return {
                "ok": True,
                "provider": "nara-speller",
                "provider_label": "바른한글(공개 맞춤법 검사)",
                "errors": all_errors,
                "error_count": len(all_errors),
                "checked_chars": len(plain),
                "chunk_count": len(chunks),
                "message": (
                    f"오류 {len(all_errors)}건을 찾았습니다."
                    if all_errors
                    else "눈에 띄는 맞춤법·띄어쓰기 오류가 없습니다."
                ),
            }
        except SpellerError as error:
            last_error = error
            if attempt < MAX_RETRIES:
                continue
            break
        except Exception as error:  # noqa: BLE001
            last_error = SpellerError(str(error))
            if attempt < MAX_RETRIES:
                continue
            break

    assert last_error is not None
    raise last_error if isinstance(last_error, SpellerError) else SpellerError(str(last_error))
