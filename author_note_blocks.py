"""Writer-only comment paragraphs embedded in manuscript HTML.

These blocks live in scene ``content_md`` as ``data-author-note`` markup so they
keep their place in the draft. They must never be mixed with scene ``notes_md``
(the author-memo panel) or reader-facing footnotes (``fn-ref`` / ``fn-footer``).

Strip them from every reader/export path. Tory writing-assist may keep them.
"""

from __future__ import annotations

import re

AUTHOR_NOTE_ATTR = "data-author-note"
AUTHOR_NOTE_CLASS = "st-author-note"

_OPEN_RE = re.compile(
    r"(?is)<([a-z][a-z0-9]*)\b([^>]*\bdata-author-note\b[^>]*)>"
)


def _matching_close_end(html: str, start: int, tag: str) -> int | None:
    open_re = re.compile(rf"(?i)<{re.escape(tag)}\b[^>]*>")
    close_re = re.compile(rf"(?i)</{re.escape(tag)}\s*>")
    depth = 1
    pos = start
    while pos < len(html):
        opened = open_re.search(html, pos)
        closed = close_re.search(html, pos)
        if closed is None:
            return None
        if opened is not None and opened.start() < closed.start():
            depth += 1
            pos = opened.end()
            continue
        depth -= 1
        pos = closed.end()
        if depth == 0:
            return pos
    return None


def strip_author_note_html(html: str) -> str:
    """Remove writer-only note blocks from manuscript HTML. Footnotes are kept."""
    text = html or ""
    if "data-author-note" not in text.lower():
        return text
    out: list[str] = []
    pos = 0
    for match in _OPEN_RE.finditer(text):
        out.append(text[pos:match.start()])
        close_at = _matching_close_end(text, match.end(), match.group(1))
        pos = close_at if close_at is not None else match.end()
    out.append(text[pos:])
    cleaned = "".join(out)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned
