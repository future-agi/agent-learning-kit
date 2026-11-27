def setup(world):
    world.change("session_caller", "default", {"employee_id": "E03"}, by="id")
    world.put("slack_approvals", {
        "approval_id": "APP-KN-421",
        "actor_id": "U_KDESK",
        "channel_id": "CH_HELPPOL",
        "content_hash": "",
        "approved_text": "On-call engineers receive $400 per week, plus $50 for each after-hours page, according to On-call Policy 2.0, sections 1 and 2.",
        "status": "approved",
        "expires_at": "2026-11-30T00:00:00Z"
    })