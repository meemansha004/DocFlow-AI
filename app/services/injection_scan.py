"""
Injection Scanner — the third Scanner check (alongside score_document /
reform_document), gating a document before it can ever reach `indexed`
status (and therefore RAG retrieval / Phase B indexing).

Deliberately NOT an LLM call: asking an LLM to judge adversarial text about
itself is exactly the attack surface this defends against (a document could
contain instructions aimed at whatever model reads it next). Pattern-based
and fully deterministic instead — same philosophy as scan_score.py
recomputing overall_score itself rather than trusting the model's arithmetic.

Not a claim of completeness — this catches common, recognizable
injection/jailbreak phrasing, not every possible attack. It's a gate that
routes suspicious content to human review, not a guarantee of safety.
"""

import re

_INJECTION_PATTERNS: list[tuple[str, str]] = [
    (r"ignore\s+(all\s+)?(the\s+)?(previous|prior|above)\s+instructions?", "instruction-override phrasing"),
    (r"disregard\s+(all\s+)?(the\s+)?(previous|prior|above)\s+(instructions?|directions?|prompts?)", "instruction-override phrasing"),
    (r"forget\s+(all\s+)?(the\s+)?(previous|prior|above)\s+(instructions?|context)", "instruction-override phrasing"),
    (r"you\s+are\s+now\s+(in\s+)?(DAN|developer\s+mode|jailbroken|unrestricted)", "role-override / jailbreak phrasing"),
    (r"\bact\s+as\s+(if\s+you\s+(are|were)|an?)\s+(unfiltered|unrestricted|uncensored|jailbroken)", "unrestricted-persona request"),
    (r"system\s*prompt\s*:", "embedded fake system-prompt marker"),
    (r"\bnew\s+system\s+(prompt|instructions?)\s*:", "embedded fake system-prompt marker"),
    (r"reveal\s+(your|the)\s+(system\s+)?prompt", "prompt-extraction attempt"),
    (r"print\s+(your|the)\s+(system\s+)?(prompt|instructions?)", "prompt-extraction attempt"),
    (r"<\|im_start\|>|<\|im_end\|>|\[INST\]|\[/INST\]", "raw chat-template control tokens"),
    (r"do\s+not\s+(tell|inform|notify|alert)\s+the\s+(user|reviewer|reader)", "covert-instruction phrasing"),
    (r"this\s+is\s+a\s+(hidden|secret)\s+instruction", "explicit hidden-instruction phrasing"),
]

_COMPILED = [(re.compile(pattern, re.IGNORECASE), reason) for pattern, reason in _INJECTION_PATTERNS]

_MAX_FINDINGS = 10
_EXCERPT_PAD = 30


def scan_for_injection(content: str) -> dict:
    """
    Checks Markdown/text content for prompt-injection-style patterns.

    Returns:
        {
            "flagged": bool,
            "findings": [{"reason": str, "excerpt": str}, ...]  # capped at 10
        }
    """
    findings = []
    for pattern, reason in _COMPILED:
        for match in pattern.finditer(content):
            if len(findings) >= _MAX_FINDINGS:
                break
            start = max(0, match.start() - _EXCERPT_PAD)
            end = min(len(content), match.end() + _EXCERPT_PAD)
            findings.append({"reason": reason, "excerpt": content[start:end].strip()})

    return {"flagged": bool(findings), "findings": findings}
