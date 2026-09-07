"""
Tools for the Draft Agent — thin @tool-decorated wrappers around
app/services/*. No business logic lives here.
"""

from agno.tools import tool

from app.services.draft_generator import draft_document as _draft_document
from app.services.intent_extraction import extract_doc_type_and_stage as _extract
from app.services.document_persistence import create_document, PermissionDeniedError, StageNotFoundError

@tool
def draft_document(document_type: str, user_input: str) -> str:
    """
    Drafts a new document from a document type and the user's free-form
    description of what it should contain. Use this when the user wants
    to create, write, or draft a new document — including revisions,
    which call this again with an updated description.

    Args:
        document_type: e.g. "Test Plan", "Design Doc", "Requirements Spec" etc.
        user_input: free-form text describing everything the document
                    should cover — bullets, paragraphs, or a mix
    """
    return _draft_document(document_type, user_input)


@tool
def extract_doc_type_and_stage(user_message: str) -> dict:
    """
    Extracts document type and stage from a user's message about drafting
    a document. Call this when a user first expresses interest in drafting,
    before anything else.

    Args:
        user_message: the user's message describing what they want to draft
    """
    result = _extract(user_message)

    missing = [k for k in ("doc_type", "stage") if not result.get(k)]
    if missing:
        raise ValueError(
            f"Could not determine: {', '.join(missing)}. "
            f"You must ask the user for the missing information before "
            f"calling draft_document — do not guess or proceed without it."
        )

    return result


@tool
def confirm_draft() -> str:
    """
    Call this ONLY when the user has explicitly confirmed they are
    satisfied with the drafted document and it is ready to be checked by
    the Scanner Agent, before upload. Do NOT call this for edit requests
    or ambiguous responses.
    """
    return "draft_confirmed_ready_for_scan"




@tool(show_result=True, stop_after_tool_call=True)
def confirm_upload(document_type: str, stage: str, content: str) -> str:
    """
    Call this ONLY when the user has explicitly confirmed they want to
    upload/save the FINAL, Scanner-approved document. This tool performs
    the actual save to the database, subject to the current user's
    permissions.

    Args:
        document_type: the type of document (e.g. "Test Plan")
        stage: the project stage this document belongs to (must match an
               existing stage name)
        content: the final, approved document content in Markdown
    """
    # WHO is uploading / AS WHICH team comes from the CLI session, never from
    # the LLM's tool arguments. We read it here and pass it to create_document
    # explicitly (create_document no longer reaches into session_context).
    from app.database import SessionLocal
    from app.models.stage import Stage
    from app.services.session_context import get_current_session

    session = get_current_session()
    role = session["role"]
    role_name = role.value if hasattr(role, "value") else str(role)

    db = SessionLocal()
    try:
        stage_row = (
            db.query(Stage)
            .filter(
                Stage.project_id == session["project_id"],
                Stage.name.ilike(stage.strip()),
                Stage.deleted_at.is_(None),
            )
            .first()
        )
        if stage_row is None:
            return f"UPLOAD FAILED: No stage named '{stage}' exists in this project."

        result = create_document(
            db,
            user_id=session["user_id"],
            team_id=session["team_id"],
            project_id=session["project_id"],
            role=role_name,
            document_type=document_type,
            stage_id=stage_row.stage_id,
            content=content,
        )
        return (
            f"Document saved: '{document_type}' in stage '{result.stage_name}' "
            f"(sensitivity: {result.sensitivity_level.name}). "
            f"Document ID: {result.document_id}"
        )
    except PermissionDeniedError as e:
        return f"UPLOAD BLOCKED: {e}"
    except StageNotFoundError as e:
        return f"UPLOAD FAILED: {e}"
    finally:
        db.close()