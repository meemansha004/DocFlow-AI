"""
On-disk working copy of the in-progress chat draft.

The draft being edited in a /draft conversation lives in a session-scoped
working file — drafts/.wip/<session_id>.md — rewritten on every revision. That
file, NOT any in-memory variable, is the source of truth for "the current
draft": it is inspectable mid-conversation, and it is the exact bytes that get
scanned and saved when the user finalizes. The LLM never carries draft content
through a tool argument.

Still fully decoupled from persistence (MERGE_DECISIONS §4): no DB, no ABAC,
no stage / team / project.
"""

from pathlib import Path

from app.services.draft_export import DRAFTS_DIR, save_draft
from app.services.scan_score import ScoringError, score_document

WIP_DIR = DRAFTS_DIR / ".wip"

_current_session_id: str | None = None


def set_session(session_id: str) -> None:
    """Bind the workspace to a chat session. Call once at CLI startup."""
    global _current_session_id
    _current_session_id = session_id


def _wip_path() -> Path:
    if not _current_session_id:
        raise RuntimeError("draft_workspace.set_session() has not been called")
    return WIP_DIR / f"{_current_session_id}.md"


def write_working_draft(content: str) -> str:
    """Overwrite the working file with the latest draft. Returns its path."""
    WIP_DIR.mkdir(parents=True, exist_ok=True)
    path = _wip_path()
    path.write_text(content, encoding="utf-8")
    return str(path)


def read_working_draft() -> str | None:
    """Current draft text from disk, or None if no working file exists yet."""
    path = _wip_path()
    return path.read_text(encoding="utf-8") if path.exists() else None


def has_working_draft() -> bool:
    return _wip_path().exists()


def finalize() -> dict:
    """
    Finalize the conversation's draft:
      1. read the working file (the real, final draft — nothing the LLM could
         have substituted),
      2. score it via score_document(),
      3. save it into drafts/ with draft_export's slug+timestamp name,
      4. delete the working file.

    Returns {"scan", "scan_error", "path", "content"}.
    """
    path = _wip_path()
    if not path.exists():
        raise RuntimeError("No working draft to finalize")
    content = path.read_text(encoding="utf-8")

    scan: dict | None = None
    scan_error: str | None = None
    try:
        scan = score_document(content)
    except ScoringError as exc:
        scan_error = str(exc)
    except Exception as exc:  # network / rate-limit / etc — saving must not fail
        scan_error = f"{type(exc).__name__}: {exc}"

    final_path = save_draft(content)
    path.unlink()

    return {"scan": scan, "scan_error": scan_error, "path": final_path, "content": content}
