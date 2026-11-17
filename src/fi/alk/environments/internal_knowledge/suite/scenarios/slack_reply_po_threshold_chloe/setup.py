def setup(world):
    world.change("session_caller", "default", {"employee_id": "E18"}, by="id")
    world.put("slack_approvals", {"approval_id": "APP-KN-409", "actor_id": "U_KDESK", "channel_id": "CH_HELPPOL", "content_hash": "sha256:38f08b524f61b1d83e07d20b24629b340bdcfa7ebc47aa10ee6861689d7084c6", "approved_text": "Per Procurement Policy 2.0 section 1, a Purchase Order (PO) is required only for purchases exceeding $10,000. A $7,000 purchase does not require a PO.", "status": "approved", "expires_at": "2026-11-30T00:00:00Z"})
