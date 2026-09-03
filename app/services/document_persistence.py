"""
Document persistence — Phase 5, fast-tracked.

Currently a LOCAL FILE SAVE (drafts/ folder) — will be swapped for real
Document/DocumentVersion DB rows once ABAC/session auth is integrated.
"""

import os
import re
from datetime import datetime

DRAFTS_DIR = "drafts"


def save_draft(document_type: str, stage: str, content: str) -> str:
    os.makedirs(DRAFTS_DIR, exist_ok=True)
    safe_type = re.sub(r"[^a-zA-Z0-9]+", "-", document_type.lower()).strip("-")
    safe_stage = re.sub(r"[^a-zA-Z0-9]+", "-", stage.lower()).strip("-")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"{DRAFTS_DIR}/{safe_type}-{safe_stage}-{timestamp}.md"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    return filename