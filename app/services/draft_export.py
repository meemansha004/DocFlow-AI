"""
Local-file export for finished chat drafts.

Per the Phase 4 pivot (MERGE_DECISIONS §4 "Drafting agent — decoupled from
persistence"): the drafting conversation ends by writing the final,
user-approved draft to a plain Markdown file under drafts/. NO database, NO
auth, NO stage / team / project. Real persistence (Document rows,
has_permission(), sensitivity) is an entirely separate path via the
authenticated POST /documents/upload endpoint.
"""

import re
from datetime import datetime
from pathlib import Path

DRAFTS_DIR = Path("drafts")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "draft"


def _first_heading(content: str) -> str | None:
    for line in content.splitlines():
        if line.lstrip().startswith("#"):
            heading = line.lstrip("#").strip()
            if heading:
                return heading
    return None


def save_draft(content: str, *, name_hint: str | None = None) -> str:
    """
    Write ``content`` to ``drafts/<slug>-<YYYYMMDD-HHMMSS>.md`` and return the
    path as a string. The slug is derived from ``name_hint`` if given, else the
    document's first Markdown heading, else ``"draft"``.
    """
    DRAFTS_DIR.mkdir(exist_ok=True)
    hint = name_hint or _first_heading(content) or "draft"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = DRAFTS_DIR / f"{_slug(hint)}-{timestamp}.md"
    # write_bytes, not write_text: on Windows write_text rewrites "\n" as
    # "\r\n", which would make the saved file's bytes differ from the exact
    # content that was scanned and shown to the user.
    path.write_bytes(content.encode("utf-8"))
    return str(path)
