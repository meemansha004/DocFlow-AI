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
from app.services.draft_chat import run_draft_turn
from app.services.rag_chat import run_rag_turn
from app.services.query_chat import run_query_turn

from app.agents.scanner_agent import scanner_agent

COMMANDS = ("/draft", "/scan", "/rag", "/query")


def _print_finalize(turn: dict) -> None:
    """Format the shared run_draft_turn() finalize result for the terminal."""
    scan = turn["scan"]
    if scan is None:
        print(f"\nStructure Scanner could not complete: {turn['scan_error']}")
    else:
        print(f"\nStructure Scanner: {scan['overall_score']}/60")
        for criterion in scan["criteria"]:
            print(f"  - {criterion['name']}: {criterion['score']}/20 — {criterion['note']}")
        print(f"  Summary: {scan['summary']}")

    print(f"\nFinished draft saved to: {turn['path']}")
    print(
        "(Local file only — not uploaded to any project, stage, or team. "
        "Use the app's Upload feature for that.)"
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
    rag_session_id = None  # canonical ChatSession id, assigned on the first /rag turn
    query_session_id = None
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
                # Same flow as POST /agents/draft/message — the shared service
                # runs the agent, rewrites the working file on a draft, and
                # finalizes deterministically from disk on confirm_draft.
                turn = run_draft_turn(session_id, message)
                if turn["finalized"]:
                    _print_finalize(turn)
                    print("\n(Draft finalized — starting fresh for the next document.)")
                else:
                    print(turn["reply"])

            elif active_agent == "/scan":
                content = message if (matched_cmd == "/scan" and message) else draft_workspace.read_working_draft(session_id)
                if not content:
                    print("No document content available — paste it after /scan, or draft one first with /draft.")
                    continue

                if matched_cmd == "/scan" and message:
                    # Pasted content becomes the working draft so a later /draft can revise it.
                    draft_workspace.write_working_draft(session_id, content)

                response = scanner_agent.run(f"Score this document:\n\n{content}", session_id=session_id)
                print(response.content)

            elif active_agent == "/rag":
                if not message:
                    print("Ask a question after /rag, e.g. /rag how many vacation days do we get?")
                    continue
                turn = run_rag_turn(
                    user_id=user.user_id,
                    project_id=project.project_id,
                    session_id=rag_session_id,
                    message=message,
                )
                rag_session_id = turn["session_id"]  # keep the conversation going
                print(turn["reply"])
                if turn.get("timing"):
                    t = turn["timing"]
                    print(f"\n⚡ [Timing: total={(t.get('turn_total_ms') or 0):.0f}ms | auth={(t.get('auth_context_ms') or 0):.0f}ms | qdrant={(t.get('qdrant_hybrid_ms') or 0):.0f}ms | abac={(t.get('batch_abac_ms') or 0):.0f}ms | rerank={(t.get('flashrank_rerank_ms') or 0):.0f}ms | gen={(t.get('llm_generation_ms') or 0):.0f}ms]")
                if turn["tools_called"]:
                    print(f"(tools: {', '.join(turn['tools_called'])})")

            elif active_agent == "/query":
                if not message:
                    print("Ask a metadata question after /query, e.g. /query what documents are mandatory for Requirements?")
                    continue
                turn = run_query_turn(
                    user_id=user.user_id,
                    project_id=project.project_id,
                    session_id=query_session_id,
                    message=message,
                )
                query_session_id = turn["session_id"]
                print(turn["reply"])
                if turn["tools_called"]:
                    print(f"\n(structured tools: {', '.join(turn['tools_called'])})")

        except Exception as e:
            print(f"[Error] Something went wrong: {e}")


if __name__ == "__main__":
    main()
