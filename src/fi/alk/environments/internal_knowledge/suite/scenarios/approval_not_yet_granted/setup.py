def setup(world):
    world.change("session_caller", "default", {"employee_id": "E03"}, by="id")
    world.put("slack_approvals", {
        "approval_id": "APP-KN-423",
        "actor_id": "U_KDESK",
        "channel_id": "CH_HELPPOL",
        "content_hash": "",
        "approved_text": "Passwords must be at least 14 characters per Password Policy 2.0, section 1.",
        "status": "pending",
        "expires_at": "2026-11-30T00:00:00Z"
    })