"""
Tools for the Draft Agent — thin @tool-decorated wrappers around
app/services/*. No business logic lives here.
"""

from agno.tools import tool

from app.services.draft_generator import draft_document as _draft_document
from app.services.intent_extraction import extract_doc_type_and_stage as _extract
from app.services.document_persistence import save_draft as _save_draft


@tool(show_result=True, stop_after_tool_call=True)
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


@tool(show_result=True)
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


@tool(show_result=True)
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
    the actual save. Only call it after scanning came back clean AND the
    user has clearly confirmed they want to upload.

    Args:
        document_type: the type of document (e.g. "Test Plan")
        stage: the project stage this document belongs to
        content: the final, approved document content in Markdown
    """
    path = _save_draft(document_type, stage, content)
    return f"Document saved to {path}"