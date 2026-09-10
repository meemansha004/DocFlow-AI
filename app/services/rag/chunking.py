"""
Heading-based chunking for RAG indexing (Phase B) — deliberately NOT generic
token-window splitting.

  1. Split on REAL Markdown headings (#, ##, ###) — each heading's body is a
     candidate chunk, with section_title set directly from that heading's text.
  2. A section that fits within SECTION_MAX_TOKENS becomes exactly ONE chunk,
     as-is, no further splitting.
  3. A section over that budget is split FURTHER, within itself: by #### (or
     deeper) sub-headings if present, else by paragraph boundaries. Every
     resulting sub-chunk keeps the SAME parent section_title, so it's still
     traceable to where it came from.
  4. Small sections are never merged with neighbors, even one-liners — a
     deliberate simplicity choice, not an oversight.
"""

import re

from app.services.rag.embedding import count_tokens

# ~500-600 tokens is the target ceiling for a single-heading section before
# it gets split further. BAAI/bge-small-en-v1.5's real max sequence length
# is 512 tokens, so this sits right at that boundary; the contextual header
# added at embedding time (see app/services/indexing.py) adds a few more
# tokens on top of a chunk already at this ceiling. fastembed truncates
# silently past 512 rather than erroring — an accepted edge case at this
# size, not a correctness bug in the chunker itself.
SECTION_MAX_TOKENS = 550

_HEADING_RE = re.compile(r"^(#{1,3})[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_SUBHEADING_RE = re.compile(r"^(#{4,6})[ \t]+(.+?)[ \t]*$", re.MULTILINE)


def _split_by_regex(text: str, pattern: re.Pattern) -> list[tuple[str, str]]:
    """[(heading_text, body), ...] split on `pattern`'s heading lines, in
    order. Content before the first match (if any) gets heading_text=''."""
    matches = list(pattern.finditer(text))
    if not matches:
        return [("", text)]

    parts: list[tuple[str, str]] = []
    if matches[0].start() > 0:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            parts.append(("", preamble))

    for i, m in enumerate(matches):
        title = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        parts.append((title, body))
    return parts


def _split_into_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _split_into_lines(text: str) -> list[str]:
    return [l.strip() for l in text.split("\n") if l.strip()]


def _pack_units(units: list[str], max_tokens: int, join: str) -> list[str]:
    """Greedily packs text units (paragraphs or lines) into <= max_tokens
    chunks, joining kept units with `join`. Assumes no single unit alone
    exceeds max_tokens (callers split further first when one does)."""
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for u in units:
        u_tokens = count_tokens(u)
        if current and current_tokens + u_tokens > max_tokens:
            chunks.append(join.join(current))
            current, current_tokens = [], 0
        current.append(u)
        current_tokens += u_tokens
    if current:
        chunks.append(join.join(current))
    return chunks


def _pack_paragraphs(paragraphs: list[str], max_tokens: int) -> list[str]:
    """
    Greedily packs paragraphs into chunks of <= max_tokens each. A single
    paragraph that's ALREADY over max_tokens on its own (e.g. a large
    Markdown table — no blank lines between rows, so it's one "paragraph"
    by the blank-line split above) is split further by LINE boundaries and
    re-packed, rather than kept whole. Only a single LINE still over
    max_tokens on its own becomes an oversized chunk — the practical floor;
    splitting mid-sentence/mid-cell buys nothing at this scale.
    """
    chunks: list[str] = []
    pending: list[str] = []
    pending_tokens = 0

    def flush() -> None:
        nonlocal pending, pending_tokens
        if pending:
            chunks.append("\n\n".join(pending))
            pending, pending_tokens = [], 0

    for p in paragraphs:
        p_tokens = count_tokens(p)
        if p_tokens > max_tokens:
            flush()
            chunks.extend(_pack_units(_split_into_lines(p), max_tokens, join="\n"))
            continue
        if pending and pending_tokens + p_tokens > max_tokens:
            flush()
        pending.append(p)
        pending_tokens += p_tokens
    flush()
    return chunks


def _split_large_section(content: str, max_tokens: int) -> list[str]:
    """A section over max_tokens: prefer #### (or deeper) sub-headings as
    split points when present, else fall back to paragraph packing."""
    if _SUBHEADING_RE.search(content):
        out: list[str] = []
        for sub_title, sub_body in _split_by_regex(content, _SUBHEADING_RE):
            piece = f"#### {sub_title}\n\n{sub_body}" if sub_title else sub_body
            if count_tokens(piece) <= max_tokens:
                out.append(piece)
            else:
                out.extend(_pack_paragraphs(_split_into_paragraphs(piece), max_tokens))
        return out
    return _pack_paragraphs(_split_into_paragraphs(content), max_tokens)


def chunk_document(markdown: str, max_tokens: int = SECTION_MAX_TOKENS) -> list[dict]:
    """
    Returns [{"section_title": str, "chunk_text": str, "token_count": int}, ...]
    in document order.

    `chunk_text` is the clean, original section content — no contextual
    header baked in. The header (project/stage/section context) is added
    ONLY to the text that gets embedded (app/services/indexing.py), never to
    what's stored/returned here.
    """
    chunks: list[dict] = []
    for title, content in _split_by_regex(markdown, _HEADING_RE):
        if not content:
            continue
        tokens = count_tokens(content)
        if tokens <= max_tokens:
            chunks.append({"section_title": title, "chunk_text": content, "token_count": tokens})
        else:
            for piece in _split_large_section(content, max_tokens):
                chunks.append({
                    "section_title": title,
                    "chunk_text": piece,
                    "token_count": count_tokens(piece),
                })
    return chunks
