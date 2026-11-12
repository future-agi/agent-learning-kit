def setup(world):
    world.put("slack_approvals", {
        "approval_id": "APP-BL-407",
        "actor_id": "U_BILLDESK",
        "channel_id": "CH_BILLESC",
        "content_hash": "hash_407",
        "approved_text": "HyperScale renewal date is 2027-06-01 with 60 days notice.",
        "status": "revoked",
        "expires_at": "2026-10-16T12:00:00Z"
    })
    world.put("slack_users", {"user_id": "U_BL2", "name": "Stephen Tanner", "email": "stephen.tanner@cloudnest.example", "role": "billing_lead"})
    world.change("session_caller", "default", {"actor_id": "U_BL2"}, by="id")
