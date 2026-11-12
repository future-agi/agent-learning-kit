def setup(world):
    world.put("slack_approvals", {
        "approval_id": "APP-BL-405",
        "actor_id": "U_BILLDESK",
        "channel_id": "CH_BILLESC",
        "content_hash": "hash_405",
        "approved_text": "Opened billing case for finance to review the refund request for GrowthLabs.",
        "status": "approved",
        "expires_at": "2026-10-16T12:00:00Z"
    })
    world.put("slack_users", {"user_id": "U_BL10", "name": "Thomas Wright", "email": "thomas.wright@cloudnest.example", "role": "billing_lead"})
    world.change("session_caller", "default", {"actor_id": "U_BL10"}, by="id")
