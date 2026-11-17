def setup(world):
    world.put("slack_approvals", {
        "approval_id": "APP-SH-411",
        "actor_id": "U_SALESDESK",
        "channel_id": "CH_SALESHANDOFF",
        "content_hash": "hash_l08",
        "approved_text": "Fernhill Logistics summary",
        "status": "pending",
        "expires_at": "2026-11-20T00:00:00Z",
    })
    # Who is in this conversation: the session sets it, not anything the desk is told.
    world.change("session_caller", "default", {"person_id": "U_A03"}, by="id")
