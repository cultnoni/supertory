"""Convert straight quotes in manuscript HTML to typographic curly quotes.

Only ASCII straight quotes (and equivalent HTML entities) are rewritten.
Existing curly quotes are left unchanged, so the transform is idempotent.
Markup tags are skipped so attributes like class="..." stay intact.
"""

from __future__ import annotations

DOUBLE_OPEN = "\u201c"  # “
DOUBLE_CLOSE = "\u201d"  # ”
SINGLE_OPEN = "\u2018"  # ‘
SINGLE_CLOSE = "\u2019"  # ’

_DOUBLE_ENTITIES = ("&quot;", "&#34;", "&#x22;", "&#X22;")
_SINGLE_ENTITIES = ("&apos;", "&#39;", "&#x27;", "&#X27;")


def _is_word_char(ch: str) -> bool:
    return bool(ch) and (ch.isalnum() or ch == "_")


def _match_entity(html: str, index: int, entities: tuple[str, ...]) -> str | None:
    for entity in entities:
        if html.startswith(entity, index):
            return entity
    return None


def convert_typographic_quotes(html: str | None) -> str:
    """Replace straight " / ' in text nodes with “ ” / ‘ ’. Tags are skipped."""
    source = "" if html is None else str(html)
    if not source:
        return source

    out: list[str] = []
    i = 0
    n = len(source)
    in_tag = False
    double_open = False
    single_open = False
    last_text = ""

    def next_text_char(start: int) -> str:
        j = start
        nested_tag = False
        while j < n:
            ch = source[j]
            if nested_tag:
                if ch == ">":
                    nested_tag = False
                j += 1
                continue
            if ch == "<":
                nested_tag = True
                j += 1
                continue
            double_ent = _match_entity(source, j, _DOUBLE_ENTITIES)
            if double_ent:
                return '"'
            single_ent = _match_entity(source, j, _SINGLE_ENTITIES)
            if single_ent:
                return "'"
            return ch
        return ""

    while i < n:
        ch = source[i]
        if in_tag:
            if ch == ">":
                in_tag = False
            out.append(ch)
            i += 1
            continue
        if ch == "<":
            in_tag = True
            out.append(ch)
            i += 1
            continue

        double_ent = _match_entity(source, i, _DOUBLE_ENTITIES)
        if double_ent or ch == '"':
            i += len(double_ent) if double_ent else 1
            if double_open:
                out.append(DOUBLE_CLOSE)
                double_open = False
            else:
                out.append(DOUBLE_OPEN)
                double_open = True
            last_text = out[-1]
            continue

        single_ent = _match_entity(source, i, _SINGLE_ENTITIES)
        if single_ent or ch == "'":
            i += len(single_ent) if single_ent else 1
            nxt = next_text_char(i)
            if _is_word_char(last_text) and _is_word_char(nxt):
                out.append(SINGLE_CLOSE)
            elif single_open:
                out.append(SINGLE_CLOSE)
                single_open = False
            else:
                out.append(SINGLE_OPEN)
                single_open = True
            last_text = out[-1]
            continue

        out.append(ch)
        last_text = ch
        i += 1
    return "".join(out)
