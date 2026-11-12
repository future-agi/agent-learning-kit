def setup(world):
    world.put("slack_approvals", {
        "approval_id": "APP-BL-401",
        "actor_id": "U_BILLDESK",
        "channel_id": "CH_BILLESC",
        "content_hash": "hash_401",
        "approved_text": "FinTrack dispute is in review with finance under case BC-301. No credit has been issued yet.",
        "status": "approved",
        "expires_at": "2026-10-16T12:00:00Z"
    })
    world.put("slack_users", {"user_id": "U_BL13", "name": "Elena Rostova", "email": "elena.rostova@cloudnest.example", "role": "billing_lead"})
    world.change("session_caller", "default", {"actor_id": "U_BL13"}, by="id")
