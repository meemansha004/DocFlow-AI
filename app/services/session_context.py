"""
Current session context — WHO is using this CLI session right now.
Set once at startup via set_current_session(). Read internally by tools
(confirm_upload, etc.) — deliberately NOT an LLM-supplied argument, so
chat input can never spoof which user is performing an action.
"""

_session = {}


def set_current_session(user_id, team_id, project_id, role):
    _session["user_id"] = user_id
    _session["team_id"] = team_id
    _session["project_id"] = project_id
    _session["role"] = role


def get_current_session() -> dict:
    if not _session:
        raise RuntimeError("No session set — call set_current_session() first")
    return dict(_session)