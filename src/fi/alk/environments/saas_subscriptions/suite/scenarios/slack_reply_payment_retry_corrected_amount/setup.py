def setup(world):
    world.put("slack_approvals", {
        "approval_id": "APP-BL-404",
        "actor_id": "U_BILLDESK",
        "channel_id": "CH_BILLESC",
        "content_hash": "hash_404",
        "approved_text": "Confirmed: inv_11_addon total is $33.00 with tax, retrying on 2026-10-17.",
        "status": "approved",
        "expires_at": "2026-10-16T12:00:00Z"
    })
    world.put("slack_users", {"user_id": "U_BL9", "name": "Sarah Jenkins", "email": "sarah.jenkins@cloudnest.example", "role": "billing_lead"})
    world.change("session_caller", "default", {"actor_id": "U_BL9"}, by="id")
