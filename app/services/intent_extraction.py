import json

from groq import Groq

from app.config import GROQ_API_KEY, GROQ_MODEL

_client = Groq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT = """You extract two pieces of information from a user's message about drafting a document: the document type/name, and the project stage it belongs to.

Respond with ONLY valid JSON, no preamble, no markdown fences:
{"doc_type": "<extracted type or null>", "stage": "<extracted stage or null>"}

If either piece isn't mentioned or is unclear, use null for that field — do not guess or invent a value."""


def extract_doc_type_and_stage(user_message: str) -> dict:
    """
    Returns: {"doc_type": str | None, "stage": str | None}
    """
    response = _client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        temperature=0,
        max_tokens=200,
    )

    raw = response.choices[0].message.content.strip()

    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()

    try:
        parsed = json.loads(raw)
        return {
            "doc_type": parsed.get("doc_type") or None,
            "stage": parsed.get("stage") or None,
        }
    except json.JSONDecodeError:
        return {"doc_type": None, "stage": None}