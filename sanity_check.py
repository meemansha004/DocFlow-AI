"""
TEST 1-
from docling.document_converter import DocumentConverter

converter = DocumentConverter()
result = converter.convert(r"F:\AeroAssist_Synthetic_Corpus\documnets (DOCX)\doc_015_test_plan.docx")
print(result.document.export_to_markdown()[:500])

from app.services.document_parser import parse_document_to_markdown

with open(r"F:\AeroAssist_Synthetic_Corpus\documnets (DOCX)\doc_015_test_plan.docx", "rb") as f:
    data = f.read()
md = parse_document_to_markdown(
    data,
    mime_type ="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    filename="doc_015_test_plan.docx"
)

print(md[:500])

TEST 2-
from dotenv import load_dotenv
from agno.agent import Agent
from agno.models.groq import Groq
load_dotenv()

agent = Agent(
    model=Groq(id="openai/gpt-oss-120b"),
    markdown=True,
)

agent.print_response("In one sentence, what is a foreign key in a relational database?")

TEST 3-
from app.services.draft_generator import draft_document


doc = draft_document(
    document_type="Test Plan",
    user_input=
    We need to test the search and booking flow for the AeroAssist pilot.
    QA team is running this. Should cover functional testing of the 5 core
    intents, and we also want regression testing included since this builds
    on the previous release.
    
)

output_path = "draft_output1.md"
with open(output_path, "w", encoding="utf-8") as f:
    f.write(doc)

print(f"Draft saved to {output_path}")


#TEST 4-
from app.agents.drafting_agent import drafting_agent

response = drafting_agent.run(
    "Draft me a Design Doc. It should cover our new notification service, "
    "which uses a queue-based fanout with a dispatcher routing to channel "
    "handlers. Push goes through FCM, email through SES, SMS vendor isn't "
    "picked yet. Also flag that retry policy for failed SMS isn't decided."
)

output_path = "draft_output3.md"
with open(output_path, "w", encoding="utf-8") as f:
    f.write(response.content)

print(f"Draft saved to {output_path}")



TEST 5-

from app.agents.scanner_agent import scanner_agent

document_to_score = # Design Doc

## Overview
TBD

## Architecture
See diagram (attached separately)

## Test Cases
- Login works
- Logout works
- TODO: add more

## Requirements
This section covers the requirements for the notification system, which uses a queue based architecture with a dispatcher that routes to channel handlers for push email and sms delivery with retry logic and monitoring built in from day one

## Open Questions
[fill in later]

## Sign-off
Owner: ___________
Date: ___________
 # your same test document

response = scanner_agent.run(f"Please score this document:\n\n{document_to_score}")

print("=== TOOLS USED ===")
print(response.tools)

print("\n=== FINAL CONTENT ===")
print(response.content)

"""
#TEST 6
from app.services.scan_reformer import reform_document

document_markdown = """
# Notification Service

We built a notification service. It uses a queue. Messages go into the queue and a dispatcher picks them up. The dispatcher looks at the channel field and sends to the right handler. For push we use FCM. For email we use SES. Both handlers have retry logic with exponential backoff, max 3 attempts. The queue is Amazon SQS. We chose SQS because our infra already runs on AWS and it integrates well with our existing services. The dispatcher runs as a Lambda function triggered by SQS events. Test cases include: sending a push notification and confirming FCM receives it, sending an email and confirming SES accepts it, simulating a failure and confirming retry happens 3 times then gives up, confirming dead letter queue captures failed messages after max retries. Owner is the Platform team. Reviewed by Sarah Chen on 2026-04-02.
"""

# Real scan_result from the Scanner agent test we just confirmed working
scan_result = {
    "overall_score": 35,
    "criteria": [
        {"name": "structural_clarity", "score": 15, "note": "Headings are clearly marked and follow a logical order from Overview to Sign-off, despite placeholder content."},
        {"name": "completeness", "score": 4, "note": "Multiple sections contain placeholders like 'TBD', 'See diagram (attached separately)', and blank Owner/Date fields, indicating missing substantive content."},
        {"name": "labeling_accuracy", "score": 16, "note": "Each heading accurately reflects the brief content provided, e.g., Test Cases list a few items and note a TODO."},
    ],
    "summary": "Completeness is the main issue, with many sections left as placeholders.",
}

reformed = reform_document(document_markdown, scan_result)

output_path = "reform_output.md"
with open(output_path, "w", encoding="utf-8") as f:
    f.write(reformed)

print(f"Reformed doc saved to {output_path}")