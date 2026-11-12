def setup(world):
    world.put("slack_approvals", {
        "approval_id": "APP-HR-412",
        "actor_id": "U_HRDESK",
        "channel_id": "CH_HRHELP",
        "content_hash": "approved_diana_hol",
        "approved_text": "The remaining US public holidays for 2026 are Veterans Day (Nov 11), Thanksgiving Day (Nov 26), Day After Thanksgiving (Nov 27), and Christmas Day (Dec 25).",
        "status": "approved",
        "expires_at": "2026-12-31T23:59:59Z"
    })
    # The person in this conversation, as the session (not the agent) knows them.
    world.change("session_caller", "default", {"employee_id": "HR02"}, by="id")
