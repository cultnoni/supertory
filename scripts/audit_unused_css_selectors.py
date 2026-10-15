#!/usr/bin/env python3
"""Report CSS selectors in styles.css / island.css that never appear in HTML/JS.

Only flags *simple* id and class selectors (no combinators). Review by hand before
deleting — dynamic class names and server-rendered fragments can false-positive.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

SIMPLE_CLASS_RE = re.compile(r"(?<![.#\w-])\.([A-Za-z_][\w-]*)\b")
SIMPLE_ID_RE = re.compile(r"(?<![.#\w-])#([A-Za-z_][\w-]*)\b")
SKIP_PSEUDO = re.compile(r":(?:root|host|fullscreen|defined)\b")


def collect_refs(paths: list[Path]) -> set[str]:
    refs: set[str] = set()
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="ignore")
        refs.update(re.findall(r'id=["\']([^"\']+)["\']', text))
        refs.update(re.findall(r'class=["\']([^"\']+)["\']', text))
        # JS string classList / querySelector crumbs
        refs.update(re.findall(r'["\']([A-Za-z_][\w-]{2,})["\']', text))
        refs.update(re.findall(r"#([A-Za-z_][\w-]*)", text))
        refs.update(re.findall(r"\.([A-Za-z_][\w-]*)", text))
    # Expand space-separated class attrs
    expanded: set[str] = set()
    for item in refs:
        expanded.update(part for part in item.split() if part)
    return expanded


def selectors_from_css(path: Path) -> set[tuple[str, str]]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    # Drop comments
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    found: set[tuple[str, str]] = set()
    for match in SIMPLE_CLASS_RE.finditer(text):
        name = match.group(1)
        if SKIP_PSEUDO.search(match.group(0)):
            continue
        found.add(("class", name))
    for match in SIMPLE_ID_RE.finditer(text):
        found.add(("id", match.group(1)))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--css",
        nargs="*",
        default=["web/island.css"],
        help="CSS files to audit (default: island.css only)",
    )
    parser.add_argument("--limit", type=int, default=80)
    args = parser.parse_args()

    sources: list[Path] = []
    for path in list(WEB.rglob("*.html")) + list(WEB.rglob("*.js")):
        if "vendor" in path.parts or "node_modules" in path.parts:
            continue
        if not path.is_file():
            continue
        sources.append(path)
    refs = collect_refs(sources)

    unused: list[str] = []
    for rel in args.css:
        path = ROOT / rel
        if not path.is_file():
            print(f"skip missing {rel}", file=sys.stderr)
            continue
        for kind, name in sorted(selectors_from_css(path)):
            if name not in refs:
                unused.append(f"{rel}: {kind}.{name}" if kind == "class" else f"{rel}: #{name}")

    print(f"candidates (review before delete): {len(unused)}")
    for line in unused[: args.limit]:
        print(line)
    if len(unused) > args.limit:
        print(f"... +{len(unused) - args.limit} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
