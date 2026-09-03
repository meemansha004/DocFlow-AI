"""from app.agents.team import docflow_team

response = docflow_team.run(
    "Draft me a short Test Plan. It should cover functional testing of "
    "login and checkout for the mobile app, owned by QA, running for one "
    "week after build delivery."
)

print("=== TEAM-LEVEL TOOLS/MEMBER CALLS ===")
print(response.tools if hasattr(response, "tools") else "No .tools attribute")

print("\n=== MEMBER RESPONSES ===")
if hasattr(response, "member_responses"):
    for i, member_resp in enumerate(response.member_responses):
        print(f"--- Member {i} ---")
        print("Agent/tools used:", getattr(member_resp, "tools", "N/A"))
        print("Content preview:", str(member_resp.content)[:200])
else:
    print("No .member_responses attribute")

print("\n=== FINAL CONTENT ===")
print(response.content)"""

from app.tools.draft_tools import extract_doc_type_and_stage

# Should succeed
print(extract_doc_type_and_stage.entrypoint("I want to draft a Test Plan for the Testing stage"))

# Should raise ValueError
try:
    print(extract_doc_type_and_stage.entrypoint("I need a design doc"))
except ValueError as e:
    print("Correctly raised:", e)