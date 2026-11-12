def setup(world):
    world.put("slack_approvals", {
        "approval_id": "APP-BL-403",
        "actor_id": "U_BILLDESK",
        "channel_id": "CH_BILLESC",
        "content_hash": "hash_403",
        "approved_text": "LunarOps' annual term ends on 2027-09-01 and auto-renews; 30 days notice is required.",
        "status": "approved",
        "expires_at": "2026-10-16T12:00:00Z"
    })
    world.put("slack_users", {"user_id": "U_BL12", "name": "Simon Thorne", "email": "simon.thorne@cloudnest.example", "role": "billing_lead"})
    world.change("session_caller", "default", {"actor_id": "U_BL12"}, by="id")
