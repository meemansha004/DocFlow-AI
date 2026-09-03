""

import ast
import json
import uuid

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


def _parse_result(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            try:
                return ast.literal_eval(raw)
            except (ValueError, SyntaxError):
                return None
    return None


def _update_draft_context(response, ctx):
    extract_result = _parse_result(_get_tool_result(response, "extract_doc_type_and_stage"))
    if extract_result:
        ctx["doc_type"] = extract_result.get("doc_type") or ctx["doc_type"]
        ctx["stage"] = extract_result.get("stage") or ctx["stage"]

    draft_result = _get_tool_result(response, "draft_document")
    if draft_result:
        ctx["last_draft_content"] = draft_result
        ctx["has_draft"] = True

    if _was_tool_called(response, "confirm_draft"):
        ctx["draft_confirmed"] = True

    if _was_tool_called(response, "confirm_upload"):
        ctx.update({
            "doc_type": None,
            "stage": None,
            "draft_confirmed": False,
            "has_draft": False,
            "last_draft_content": None,
            "last_scan_clean": False,
            "last_scan_summary": None,
        })


def _update_scan_context(response, ctx):
    score_result = _parse_result(_get_tool_result(response, "score_document"))
    if score_result and "overall_score" in score_result:
        ctx["last_scan_clean"] = score_result["overall_score"] >= 36
        ctx["last_scan_summary"] = score_result.get("summary")


def _build_context_prefix(ctx) -> str:
    parts = []
    if ctx["doc_type"]:
        parts.append(f"document_type={ctx['doc_type']}")
    if ctx["stage"]:
        parts.append(f"stage={ctx['stage']}")
    parts.append(f"draft_exists={ctx['has_draft']}")
    parts.append(f"draft_confirmed={ctx['draft_confirmed']}")
    if ctx["draft_confirmed"]:
        parts.append(f"scan_clean={ctx['last_scan_clean']}")

    prefix = f"[Known so far: {', '.join(parts)}]\n"

    if ctx["has_draft"] and not ctx["draft_confirmed"]:
        prefix += (
            f"\n[Current draft content — if the user requests any edit, you "
            f"MUST pass this FULL content back into draft_document's "
            f"user_input, with only the requested change applied. Never "
            f"call draft_document with just a description of the change — "
            f"always include the complete current text:\n\n"
            f"{ctx['last_draft_content']}\n]\n"
        )

    return prefix + "\n"


def main():
    print("=" * 60)
    print("DocFlow AI — Chat Interface")
    print("Commands: /draft  /scan  /rag  /query   ('exit' to quit)")
    print("=" * 60)

    session_id = str(uuid.uuid4())
    active_agent = None

    draft_context = {
        "doc_type": None,
        "stage": None,
        "draft_confirmed": False,
        "has_draft": False,
        "last_draft_content": None,
        "last_scan_clean": False,
        "last_scan_summary": None,
    }

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
                prefix = _build_context_prefix(draft_context)
                response = drafting_agent.run(prefix + (message or "continue"), session_id=session_id)
                print(response.content)
                _update_draft_context(response, draft_context)

                if _was_tool_called(response, "confirm_draft"):
                    print("\n(Draft finalized — type /scan to check it for quality.)")
                if _was_tool_called(response, "confirm_upload"):
                    print("\n(Uploaded — session reset for a new document.)")

            elif active_agent == "/scan":
                content = message if (matched_cmd == "/scan" and message) else draft_context["last_draft_content"]
                if not content:
                    print("No document content available — paste it after /scan, or draft one first with /draft.")
                    continue

                if matched_cmd == "/scan" and message:
                    draft_context["last_draft_content"] = content
                    draft_context["has_draft"] = True

                response = scanner_agent.run(f"Score this document:\n\n{content}", session_id=session_id)
                print(response.content)
                _update_scan_context(response, draft_context)

                if draft_context["last_scan_clean"]:
                    print("\n(Scan clean — type /draft and confirm you'd like to upload.)")
                else:
                    print("\n(Issues found — type /draft to revise, then /scan again.)")
                    if not draft_context["doc_type"] or not draft_context["stage"]:
                        print("(Note: this document has no tracked type/stage yet — /draft will ask for it before revising.)")

            elif active_agent == "/rag":
                print(rag_stub(message))

            elif active_agent == "/query":
                print(query_stub(message))

        except Exception as e:
            print(f"[Error] Something went wrong: {e}")


if __name__ == "__main__":
    main()