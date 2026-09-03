"""
Structure Scanner — reformation function (Phase 3, step 6).

Plain Python function, independently callable/testable — same pattern as
scan_scorer.py and draft_generator.py.
"""

from groq import Groq

from app.services.reform_prompts import build_reformation_messages
from app.config import GROQ_API_KEY, GROQ_MODEL

_client = Groq(api_key=GROQ_API_KEY)


class ReformationError(Exception):
    """Raised when Groq fails to produce a usable reformed document."""
    pass


def reform_document(document_markdown: str, scan_result: dict) -> str:
    """
    Reformats a poorly-scoring document to fix structure/labeling/formatting,
    without inventing any new content. Genuine gaps in the original stay
    honestly marked in the output.

    Args:
        document_markdown: the original document's content
        scan_result: the dict returned by score_document(), used to guide
                     what needs fixing

    Returns:
        The reformed document as Markdown.

    Raises:
        ReformationError: if Groq returns an empty/unusable response.
    """
    messages = build_reformation_messages(document_markdown, scan_result)

    response = _client.chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
        temperature=0.2,  # low — this is a corrective/faithful task, not creative
        max_tokens=4000,
    )

    content = response.choices[0].message.content

    if not content or not content.strip():
        raise ReformationError("Groq returned an empty response")

    content = content.strip()

    # Same defensive preamble-strip as draft_generator.py
    lines = content.split("\n")
    if lines and not lines[0].lstrip().startswith("#"):
        for i, line in enumerate(lines):
            if line.lstrip().startswith("#"):
                content = "\n".join(lines[i:])
                break

    return content