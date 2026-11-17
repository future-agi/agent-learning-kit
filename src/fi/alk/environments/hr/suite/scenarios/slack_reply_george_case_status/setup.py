def setup(world):
    world.put("slack_approvals", {
        "approval_id": "APP-HR-414",
        "actor_id": "U_HRDESK",
        "channel_id": "CH_HRHELP",
        "content_hash": "approved_george_case",
        "approved_text": "Case CASE-2901 is currently in_review with note: HR02: checking payroll export from January.",
        "status": "approved",
        "expires_at": "2026-12-31T23:59:59Z"
    })
    # The person in this conversation, as the session (not the agent) knows them.
    world.change("session_caller", "default", {"employee_id": "HR02"}, by="id")
