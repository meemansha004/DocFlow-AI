"""
One turn of the /draft conversation, shared by the CLI (cli.py) and the HTTP
endpoint (app/routers/agents.py).

This is the exact flow cli.py's /draft handler used to run inline:
  - build a context prefix from the session's on-disk working draft,
  - run the Drafting Agent,
  - if it called draft_document, the working file was already rewritten (the
    tool does that) — report drafted=True,
  - if it called confirm_draft, finalize deterministically from disk (score via
    the Structure Scanner, move into drafts/, delete the working file),
  - otherwise it just replied (a clarifying question) — report neither.

Fully decoupled from persistence: no DB, no ABAC, no stage/team/project. The
finalized output is a local file, identical to the CLI.
"""

from pathlib import Path

from app.agents.drafting_agent import drafting_agent
from app.services import draft_workspace


def _tool_called(response, name: str) -> bool:
    return bool(getattr(response, "tools", None)) and any(
        t.tool_name == name for t in response.tools
    )


def build_context_prefix(session_id: str) -> str:
    """The per-turn context prefix — the working file on disk, verbatim."""
    current = draft_workspace.read_working_draft(session_id)
    if not current:
        return "[No draft exists in this conversation yet.]\n\n"

    return (
        "[A draft currently exists — the exact working copy below is what is on "
        "disk right now.\n"
        "- If the user requests ANY edit, pass this FULL text back into "
        "draft_document's user_input with only the requested change applied — "
        "never call draft_document with just a description of the change.\n"
        "- If the user explicitly confirms they're satisfied, call "
        "confirm_draft(confirmed=true) — never pass it any draft content.\n\n"
        f"{current}\n]\n\n"
    )


def run_draft_turn(session_id: str, message: str) -> dict:
    """
    Run one drafting turn for `session_id`.

    Returns:
        {
          "reply": str,            # the agent's text
          "drafted": bool,         # draft_document was called this turn
          "finalized": bool,       # confirm_draft was called AND finalize ran
          "scan": dict | None,     # Structure Scanner result (finalize only)
          "scan_error": str | None,
          "final_content": str | None,  # exact finalized bytes
          "path": str | None,      # local file path
          "filename": str | None,  # basename, for the download URL
        }
    """
    prefix = build_context_prefix(session_id)
    before = draft_workspace.read_working_draft(session_id)
    response = drafting_agent.run(prefix + (message or "continue"), session_id=session_id)
    after = draft_workspace.read_working_draft(session_id)
    reply = getattr(response, "content", "") or ""

    # "drafted this turn" = draft_document ran. On rate-limit-heavy turns the
    # tool list on the RunOutput can be incomplete, so also treat a changed
    # working file as proof the tool ran.
    drafted_this_turn = _tool_called(response, "draft_document") or (
        after is not None and after != before
    )

    base = {
        "reply": reply, "drafted": False, "finalized": False,
        "scan": None, "scan_error": None,
        "final_content": None, "path": None, "filename": None,
    }

    if _tool_called(response, "confirm_draft"):
        if not draft_workspace.has_working_draft(session_id):
            base["scan_error"] = "There is no draft in this conversation to finalize yet."
            return base
        outcome = draft_workspace.finalize(session_id)
        base.update(
            finalized=True,
            scan=outcome["scan"],
            scan_error=outcome["scan_error"],
            final_content=outcome["content"],
            path=outcome["path"],
            filename=Path(outcome["path"]).name,
        )
        return base

    base["drafted"] = drafted_this_turn
    return base
