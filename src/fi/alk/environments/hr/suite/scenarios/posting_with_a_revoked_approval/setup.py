def setup(world):
    world.put("slack_approvals", {
        "approval_id": "APP-HR-450",
        "actor_id": "U_HRDESK",
        "channel_id": "CH_HRHELP",
        "content_hash": "hash450",
        "approved_text": "In APAC, a medical certificate is required for absences exceeding 2 consecutive working days.",
        "status": "revoked",
        "expires_at": "2026-12-31T23:59:59Z",
    })
    # The person in this conversation, as the session (not the agent) knows them.
    world.change("session_caller", "default", {"employee_id": "HR02"}, by="id")
