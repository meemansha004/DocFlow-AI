"""
The two direct LLM calls behind the RAG tools — grounded Q&A over retrieved
chunks, and whole-document summarisation. Both are plain Groq chat
completions (NO tools bound), low/zero temperature, prompts in
app/services/rag/rag_prompts.py.

Deliberately NOT here:
  * any post-generation groundedness / self-check pass — skipped per the
    confirmed RAG design (see DEFERRED_ITEMS.md #15).
  * any tool/function calling on these completions — the model can only
    return text; it cannot take an action from inside its own answer.
"""

from groq import Groq

from app.config import GROQ_API_KEY, GROQ_MODEL
from app.services.rag.rag_prompts import build_answer_messages, build_summary_messages

_client = Groq(api_key=GROQ_API_KEY)


class GenerationError(Exception):
    """Groq returned nothing usable for an answer or a summary."""


# For qwen models, "none" disables reasoning overhead entirely.
# For gpt-oss models, "low" keeps minimal chain-of-thought.
_REASONING_EFFORT = "none" if "qwen" in GROQ_MODEL.lower() else "low"


def _complete(messages: list[dict], *, temperature: float, max_tokens: int) -> str:
    response = _client.chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        reasoning_effort=_REASONING_EFFORT,
    )
    content = response.choices[0].message.content
    if not content or not content.strip():
        raise GenerationError("Groq returned an empty response")
    return content.strip()


def generate_answer(question: str, sources: list[dict]) -> str:
    """
    Grounded answer to `question` from `sources`.

    sources: [{"label": "Source 1 — Requirements stage", "section": str|None,
               "text": str}, ...]  — labels are built by the caller
    (rag_tools) and are the ONLY citation labels the model is allowed to use.

    Low temperature. Every claim is cited with its source + stage. If the
    sources don't answer the question the model returns the fixed
    "don't contain an answer" sentence (the caller may still override the
    whole flow — e.g. when nothing was retrieved at all).
    """
    if not sources:
        raise GenerationError("generate_answer called with no sources")
    messages = build_answer_messages(question, sources)
    return _complete(messages, temperature=0.1, max_tokens=600)


def summarize_full_document(title: str, full_text: str) -> str:
    """Faithful summary of the ENTIRE document text (not chunks/fragments)."""
    if not full_text or not full_text.strip():
        raise GenerationError("summarize_full_document called with empty text")
    messages = build_summary_messages(title, full_text)
    return _complete(messages, temperature=0.2, max_tokens=700)
