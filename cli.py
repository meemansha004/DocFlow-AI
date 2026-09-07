"""
DocFlow AI — CLI chat interface, command-routed.

Routing is 100% deterministic: the user's slash command decides which agent
handles a message. Session context (who's using this CLI, acting as which
team/project) is established once at startup — never LLM-controlled.

The /draft flow (Phase 4 pivot) is fully decoupled from persistence: it drafts,
revises, then finalizes to a LOCAL FILE under drafts/. It never touches the
documents table, the users table, or has_permission(). Real persistence is the
separate authenticated POST /documents/upload endpoint.

The current draft lives in a session-scoped working file on disk
(drafts/.wip/<session_id>.md) — that file is the single source of truth for
"the current draft", read directly when building each turn's context and when
finalizing. See app/services/draft_workspace.py.
"""

import uuid

from app.database import SessionLocal
from app.services.session_startup import select_current_user
from app.services.session_context import set_current_session
from app.services import draft_workspace

from app.agents.drafting_agent import drafting_agent
from app.agents.scanner_agent import scanner_agent

COMMANDS = ("/draft", "/scan", "/rag", "/query")


def _get_tool_result(response, tool_name: str):
    if not response.tools:
        return None
    match = next((t for t in response.tools if t.tool_name == tool_name), None)
    return match.result if match else None


def _was_tool_called(response, tool_name: str) -> bool:
    return _get_tool_result(response, tool_name) is not None


def _finalize_from_disk() -> None:
    """
    Runs after the Drafting Agent calls confirm_draft. Deterministic: reads the
    working file, scores it, moves it into drafts/, deletes the working file.
    The draft content comes straight from disk — the LLM never carries it.
    """
    if not draft_workspace.has_working_draft():
        print("\n(Nothing to finalize — no draft exists yet.)")
        return

    outcome = draft_workspace.finalize()
    scan = outcome["scan"]
    if scan is None:
        print(f"\nStructure Scanner could not complete: {outcome['scan_error']}")
    else:
        print(f"\nStructure Scanner: {scan['overall_score']}/60")
        for criterion in scan["criteria"]:
            print(f"  - {criterion['name']}: {criterion['score']}/20 — {criterion['note']}")
        print(f"  Summary: {scan['summary']}")

    print(f"\nFinished draft saved to: {outcome['path']}")
    print(
        "(Local file only — not uploaded to any project, stage, or team. "
        "Use the app's Upload feature for that.)"
    )


def _build_context_prefix() -> str:
    # Source of truth: the working file on disk, not any in-memory copy.
    current = draft_workspace.read_working_draft()
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


def main():
    print("=" * 60)
    print("DocFlow AI — Chat Interface")
    print("=" * 60)

    db = SessionLocal()
    user, team, project, role = select_current_user(db)
    set_current_session(
        user_id=user.user_id,
        team_id=team.team_id,
        project_id=project.project_id,
        role=role,
    )
    db.close()
    print(
        f"\nSession active: {user.email} — acting as "
        f"{role.value if hasattr(role, 'value') else role} on "
        f"{team.name} ({project.name})"
    )
    print("\nCommands: /draft  /scan  /rag  /query   ('exit' to quit)")
    print("=" * 60)

    session_id = str(uuid.uuid4())
    draft_workspace.set_session(session_id)
    active_agent = None

    while True:
        raw = input("\nYou: ").strip()

        if raw.lower() in ("exit", "quit"):
            print("Goodbye.")
            break
        if not raw:
            continue

        matched_cmd = next((c for c in COMMANDS if raw.startswith(c)), None)
        if matched_cmd:
            active_agent = matched_cmd
            message = raw[len(matched_cmd):].strip()
        else:
            message = raw

        if active_agent is None:
            print("\nPlease start with /draft, /scan, /rag, or /query.")
            continue

        print(f"\n--- {active_agent} agent working ---\n")

        try:
            if active_agent == "/draft":
                prefix = _build_context_prefix()
                response = drafting_agent.run(prefix + (message or "continue"), session_id=session_id)

                # confirm_draft is a pure signal. If the agent called it (even
                # if it malformed the args — small models sometimes do), the
                # user has approved: finalize deterministically from disk. Its
                # own return ("confirmed") isn't worth printing.
                if _was_tool_called(response, "confirm_draft"):
                    _finalize_from_disk()
                    print("\n(Draft finalized — starting fresh for the next document.)")
                else:
                    print(response.content)

            elif active_agent == "/scan":
                content = message if (matched_cmd == "/scan" and message) else draft_workspace.read_working_draft()
                if not content:
                    print("No document content available — paste it after /scan, or draft one first with /draft.")
                    continue

                if matched_cmd == "/scan" and message:
                    # Pasted content becomes the working draft so a later /draft can revise it.
                    draft_workspace.write_working_draft(content)

                response = scanner_agent.run(f"Score this document:\n\n{content}", session_id=session_id)
                print(response.content)

            elif active_agent == "/rag":
                print(rag_stub(message))

            elif active_agent == "/query":
                print(query_stub(message))

        except Exception as e:
            print(f"[Error] Something went wrong: {e}")


if __name__ == "__main__":
    main()
