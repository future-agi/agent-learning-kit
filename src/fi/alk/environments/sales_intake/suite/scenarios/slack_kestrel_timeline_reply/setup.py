def setup(world):
    world.put("slack_approvals", {
        "approval_id": "APP-SH-403",
        "actor_id": "U_SALESDESK",
        "channel_id": "CH_SALESHANDOFF",
        "content_hash": "sha256:1b999d306327709ed50335497b9e8389d4449c77d5b4e426834e550a0a313609",
        "approved_text": "Jonas Becker from Kestrel Mobility confirmed in email that their timeline is 0-3 months.",
        "status": "approved",
        "expires_at": "2026-11-15T15:00:00Z"
    })
    # Who is in this conversation: the session sets it, not anything the desk is told.
    world.put("slack_users", {"user_id": "U_S421", "name": "Ines Carvalho", "email": "ines.carvalho@brightdesk.example", "role": "sales_ops"})
    world.change("session_caller", "default", {"person_id": "U_S421"}, by="id")
