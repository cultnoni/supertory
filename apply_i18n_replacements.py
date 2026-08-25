#!/usr/bin/env python3
"""Apply ko.json keys to web/app.js and web/index.html in 200-line batches."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KO_PATH = ROOT / "web" / "locales" / "ko.json"
APP_JS = ROOT / "web" / "app.js"
INDEX_HTML = ROOT / "web" / "index.html"
HELPER = ROOT / "i18n_helper.js"
BATCH = 200
SENTINEL = "/* ==== i18n helper (do not duplicate) ===="

KOREAN_RE = re.compile(r"[가-힣]")
JS_STRING_RE = re.compile(
    r"(?P<quote>[\"'`])(?P<body>(?:\\.|(?!(?P=quote)).)*)(?P=quote)"
)
HTML_TEXT_RE = re.compile(r">([^<>{}\n]*[가-힣][^<>{}\n]*)<")
HTML_ATTR_RE = re.compile(
    r"\b(placeholder|title|aria-label|alt|value|data-tooltip)\s*=\s*\"([^\"]*[가-힣][^\"]*)\""
)
COMMENT_LINE_RE = re.compile(r"^\s*(//|\*|/\*)")


def load_lookup() -> dict[str, str]:
    data = json.loads(KO_PATH.read_text(encoding="utf-8"))
    lookup: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(value, str):
            continue
        if value not in lookup:
            lookup[value] = key
    return lookup


def clean_js_body(body: str) -> str:
    return body.replace('\\"', '"').replace("\\'", "'").replace("\\n", " ").strip()


def js_escape_key(key: str) -> str:
    return key.replace("\\", "\\\\").replace("'", "\\'")


def is_comment_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*")


def is_object_key(line: str, start: int, end: int) -> bool:
    rest = line[end:].lstrip()
    if not rest.startswith(":"):
        return False
    before = line[:start].rstrip()
    if before.endswith("?"):
        return False
    if before.endswith("{") or before.endswith(",") or before == "":
        return True
    return False


def already_wrapped(line: str, start: int) -> bool:
    prefix = line[:start].rstrip()
    return prefix.endswith("i18n.t(")


def transform_js_line(line: str, lookup: dict[str, str]) -> tuple[str, int]:
    if not KOREAN_RE.search(line) or is_comment_line(line):
        return line, 0
    if "${" in line:
        return line, 0
    matches = list(JS_STRING_RE.finditer(line))
    if not matches:
        return line, 0
    out = line
    replaced = 0
    for m in reversed(matches):
        body = m.group("body")
        if not KOREAN_RE.search(body):
            continue
        clean = clean_js_body(body)
        key = lookup.get(clean)
        if not key:
            continue
        if already_wrapped(out, m.start()):
            continue
        if is_object_key(out, m.start(), m.end()):
            continue
        repl = f"i18n.t('{js_escape_key(key)}')"
        out = out[: m.start()] + repl + out[m.end() :]
        replaced += 1
    return out, replaced


def attr_data_name(attr: str) -> str:
    if attr == "data-tooltip":
        return "data-i18n-tooltip"
    if attr == "aria-label":
        return "data-i18n-aria-label"
    return f"data-i18n-{attr}"


def transform_html_line(line: str, lookup: dict[str, str], in_script: bool) -> tuple[str, int]:
    if in_script:
        return transform_js_line(line, lookup)
    if not KOREAN_RE.search(line):
        return line, 0
    out = line
    replaced = 0

    attr_matches = list(HTML_ATTR_RE.finditer(out))
    for m in reversed(attr_matches):
        attr, value = m.group(1), m.group(2)
        key = lookup.get(value.strip())
        if not key:
            continue
        data_attr = attr_data_name(attr)
        if data_attr in out[max(0, m.start() - 80) : m.end() + 80]:
            # already nearby; still check exact insertion point
            after = out[m.end() : m.end() + 40]
            before = out[max(0, m.start() - 40) : m.start()]
            if data_attr in after or data_attr in before:
                continue
        insertion = f' {data_attr}="{key}"'
        out = out[: m.end()] + insertion + out[m.end() :]
        replaced += 1

    text_matches = list(HTML_TEXT_RE.finditer(out))
    for m in reversed(text_matches):
        raw = m.group(1)
        key = lookup.get(raw.strip())
        if not key:
            continue
        gt_index = m.start()
        open_lt = out.rfind("<", 0, gt_index)
        if open_lt >= 0:
            open_tag = out[open_lt:gt_index]
            if open_tag.startswith("</") or "data-i18n=" in open_tag:
                continue
            if not re.match(r"<[A-Za-z]", open_tag):
                continue
        elif "data-i18n=" in out:
            continue
        insertion = f' data-i18n="{key}"'
        out = out[:gt_index] + insertion + out[gt_index:]
        replaced += 1
    return out, replaced


def node_check(path: Path) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["node", "--check", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return False, "node not found"
    err = (proc.stderr or proc.stdout or "").strip()
    return proc.returncode == 0, err


def node_check_text(path: Path, lines: list[str]) -> tuple[bool, str]:
    tmp = path.with_name(path.name + ".i18n_check.js")
    tmp.write_bytes("".join(lines).encode("utf-8"))
    try:
        return node_check(tmp)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def write_lines(path: Path, lines: list[str]) -> None:
    data = "".join(lines).encode("utf-8")
    last_err = None
    for attempt in range(12):
        try:
            with open(path, "wb") as handle:
                handle.write(data)
                handle.flush()
            return
        except OSError as exc:
            last_err = exc
            time.sleep(0.25 * (attempt + 1))
    raise last_err


def restore_eol(original: str, new_body: str) -> str:
    if original.endswith("\r\n"):
        return new_body.rstrip("\r\n") + "\r\n" if original.endswith("\r\n") else new_body
    if original.endswith("\n"):
        return new_body.rstrip("\n") + "\n"
    return new_body


def split_keep(text: str) -> list[str]:
    if not text:
        return []
    parts = text.splitlines(keepends=True)
    if text.endswith("\n") or text.endswith("\r"):
        return parts
    return parts


def apply_batches_js(path: Path, lookup: dict[str, str], start_line: int) -> dict:
    text = path.read_text(encoding="utf-8")
    lines = split_keep(text)
    stats = {"batches": 0, "replacements": 0, "reverted_batches": 0, "reverted_lines": 0}
    total = len(lines)
    idx = start_line
    while idx < total:
        end = min(idx + BATCH, total)
        old_batch = lines[idx:end]
        new_batch = []
        batch_repl = 0
        for raw in old_batch:
            eol = "\r\n" if raw.endswith("\r\n") else ("\n" if raw.endswith("\n") else "")
            body = raw[: -len(eol)] if eol else raw
            new_body, n = transform_js_line(body, lookup)
            batch_repl += n
            new_batch.append(new_body + eol)
        stats["batches"] += 1
        if batch_repl == 0:
            print(f"[app.js] batch {idx + 1}-{end}: no changes")
            idx = end
            continue
        lines[idx:end] = new_batch
        ok, err = node_check_text(path, lines)
        if ok:
            stats["replacements"] += batch_repl
            write_lines(path, lines)
            print(f"[app.js] batch {idx + 1}-{end}: +{batch_repl} ok")
        else:
            print(f"[app.js] batch {idx + 1}-{end}: SYNTAX FAIL, trying line-by-line")
            print(f"  {err[:400]}")
            lines[idx:end] = old_batch
            stats["reverted_batches"] += 1
            for i, raw in enumerate(old_batch):
                line_no = idx + i
                eol = "\r\n" if raw.endswith("\r\n") else ("\n" if raw.endswith("\n") else "")
                body = raw[: -len(eol)] if eol else raw
                new_body, n = transform_js_line(body, lookup)
                if n == 0:
                    continue
                lines[line_no] = new_body + eol
                ok2, err2 = node_check_text(path, lines)
                if ok2:
                    stats["replacements"] += n
                    write_lines(path, lines)
                    print(f"  line {line_no + 1}: +{n} ok")
                else:
                    lines[line_no] = raw
                    stats["reverted_lines"] += 1
                    print(f"  line {line_no + 1}: reverted ({err2[:200]})")
        idx = end
    write_lines(path, lines)
    return stats


def html_angle_counts(text: str) -> tuple[int, int]:
    return text.count("<"), text.count(">")


def apply_batches_html(path: Path, lookup: dict[str, str]) -> dict:
    text = path.read_text(encoding="utf-8")
    lines = split_keep(text)
    stats = {"batches": 0, "replacements": 0, "reverted_batches": 0, "reverted_lines": 0}
    in_script = False
    script_flags = []
    for raw in lines:
        body = raw.rstrip("\r\n")
        lower = body.lower()
        flag = in_script
        script_flags.append(flag)
        if not in_script and re.search(r"<script\b", lower) and "</script>" not in lower:
            in_script = True
        elif in_script and "</script>" in lower:
            in_script = False

    idx = 0
    total = len(lines)
    while idx < total:
        end = min(idx + BATCH, total)
        old_batch = lines[idx:end]
        new_batch = []
        batch_repl = 0
        for i, raw in enumerate(old_batch):
            eol = "\r\n" if raw.endswith("\r\n") else ("\n" if raw.endswith("\n") else "")
            body = raw[: -len(eol)] if eol else raw
            new_body, n = transform_html_line(body, lookup, script_flags[idx + i])
            batch_repl += n
            new_batch.append(new_body + eol)
        stats["batches"] += 1
        if batch_repl == 0:
            print(f"[index.html] batch {idx + 1}-{end}: no changes")
            idx = end
            continue
        orig_text = "".join(lines)
        orig_lt, orig_gt = html_angle_counts(orig_text)
        lines[idx:end] = new_batch
        new_text = "".join(lines)
        new_lt, new_gt = html_angle_counts(new_text)
        if (new_lt, new_gt) != (orig_lt, orig_gt):
            print(f"[index.html] batch {idx + 1}-{end}: tag count changed, line-by-line")
            lines[idx:end] = old_batch
            stats["reverted_batches"] += 1
            for i, raw in enumerate(old_batch):
                line_no = idx + i
                eol = "\r\n" if raw.endswith("\r\n") else ("\n" if raw.endswith("\n") else "")
                body = raw[: -len(eol)] if eol else raw
                new_body, n = transform_html_line(body, lookup, script_flags[line_no])
                if n == 0:
                    continue
                prev = "".join(lines)
                plt, pgt = html_angle_counts(prev)
                lines[line_no] = new_body + eol
                cur = "".join(lines)
                clt, cgt = html_angle_counts(cur)
                if (clt, cgt) != (plt, pgt):
                    lines[line_no] = raw
                    stats["reverted_lines"] += 1
                    print(f"  line {line_no + 1}: reverted (tag count)")
                else:
                    stats["replacements"] += n
                    print(f"  line {line_no + 1}: +{n} ok")
        else:
            stats["replacements"] += batch_repl
            write_lines(path, lines)
            print(f"[index.html] batch {idx + 1}-{end}: +{batch_repl} ok")
        idx = end
    write_lines(path, lines)
    return stats


def prepend_helper() -> None:
    app = APP_JS.read_text(encoding="utf-8")
    if SENTINEL in app[:4000]:
        print("helper already present in app.js")
        return
    helper = HELPER.read_text(encoding="utf-8").rstrip() + "\n\n"
    APP_JS.write_text(helper + app, encoding="utf-8")
    print(f"prepended helper ({helper.count(chr(10))} lines)")
    ok, err = node_check(APP_JS)
    if not ok:
        raise SystemExit(f"syntax error after helper prepend:\n{err}")
    print("node --check after helper: ok")


def helper_line_count() -> int:
    text = APP_JS.read_text(encoding="utf-8")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "/* ==== end i18n helper ====" in line:
            nxt = i + 1
            if nxt < len(lines) and lines[nxt].strip() == "":
                return nxt + 1
            return nxt
    return 0


def main() -> None:
    if not KO_PATH.exists():
        raise SystemExit(f"missing {KO_PATH}")
    lookup = load_lookup()
    print(f"lookup keys: {len(lookup)}")
    prepend_helper()
    start = helper_line_count()
    print(f"app.js helper occupies first {start} lines; transforming the rest")
    js_stats = apply_batches_js(APP_JS, lookup, start)
    print("JS stats:", js_stats)
    ok, err = node_check(APP_JS)
    print("final node --check:", "ok" if ok else err)
    if not ok:
        raise SystemExit(1)
    html_stats = apply_batches_html(INDEX_HTML, lookup)
    print("HTML stats:", html_stats)


if __name__ == "__main__":
    main()
