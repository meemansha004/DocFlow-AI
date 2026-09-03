from agno.tools import tool
from app.services.scan_score import score_document as _score_document
from app.services.scan_reformer import reform_document as _reform_document

@tool
def score_document(document_markdown: str) -> dict:
    """
    Scores a document's Markdown content for structural quality.
    Use this whenever a document needs to be scored, reviewed, or checked
    for quality — you must call this tool to get an accurate score;
    never estimate a score yourself.

    Args:
        document_markdown: the document's content in Markdown format
    """
    return _score_document(document_markdown)

@tool
def reform_document(document_markdown: str, scan_result: dict) -> str:
    """
    Reformats a document that scored poorly on structural quality — fixes
    headings, labeling, and organization using only what's already in the
    document. Never invents new content. Use this after score_document has
    returned a low score, passing that same scan result in.

    Args:
        document_markdown: the original document's content
        scan_result: the dict previously returned by score_document
    """
    return _reform_document(document_markdown, scan_result)