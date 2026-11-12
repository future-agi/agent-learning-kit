def setup(world):
    world.put("slack_approvals", {
        "approval_id": "APP-BL-402",
        "actor_id": "U_BILLDESK",
        "channel_id": "CH_BILLESC",
        "content_hash": "hash_402",
        "approved_text": "There is no record of a 20% discount for BetaTech. We can open a billing case for finance to review.",
        "status": "approved",
        "expires_at": "2026-10-16T12:00:00Z"
    })
    world.put("slack_users", {"user_id": "U_BL8", "name": "Rachel Adams", "email": "rachel.adams@cloudnest.example", "role": "billing_lead"})
    world.change("session_caller", "default", {"actor_id": "U_BL8"}, by="id")
