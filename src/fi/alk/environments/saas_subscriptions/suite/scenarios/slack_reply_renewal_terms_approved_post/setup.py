def setup(world):
    world.put("slack_approvals", {
        "approval_id": "APP-BL-403",
        "actor_id": "U_BILLDESK",
        "channel_id": "CH_BILLESC",
        "content_hash": "hash_403",
        "approved_text": "LunarOps' subscription term ends on 2027-09-01 with 30 days notice required.",
        "status": "approved",
        "expires_at": "2026-10-16T12:00:00Z"
    })
    world.put("slack_users", {"user_id": "U_BL11", "name": "David Chen", "email": "david.chen@cloudnest.example", "role": "billing_lead"})
    world.change("session_caller", "default", {"actor_id": "U_BL11"}, by="id")
