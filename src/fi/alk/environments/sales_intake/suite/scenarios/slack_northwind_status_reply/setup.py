def setup(world):
    world.put("slack_approvals", {
        "approval_id": "APP-SH-402",
        "actor_id": "U_SALESDESK",
        "channel_id": "CH_SALESHANDOFF",
        "content_hash": "sha256:4b8b8664c8b15e910ad21956f5bf642670db9ca6e3fa75a7d61c65aaa11444b6",
        "approved_text": "Northwind Support Co (L01) is currently status new and has not yet been routed.",
        "status": "approved",
        "expires_at": "2026-11-15T15:00:00Z"
    })
    # Who is in this conversation: the session sets it, not anything the desk is told.
    world.put("slack_users", {"user_id": "U_S422", "name": "Ben Okoro", "email": "ben.okoro@brightdesk.example", "role": "sales_ops"})
    world.change("session_caller", "default", {"person_id": "U_S422"}, by="id")
