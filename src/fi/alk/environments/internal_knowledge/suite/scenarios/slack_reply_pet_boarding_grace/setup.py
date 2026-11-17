def setup(world):
    world.change("session_caller", "default", {"employee_id": "E17"}, by="id")
    world.put("slack_approvals", {"approval_id": "APP-KN-410", "actor_id": "U_KDESK", "channel_id": "CH_HELPPOL", "content_hash": "sha256:f947b96f5c4bf96031e912c6e32ffe65cc07da7f4653539f4fb850d9fc948d4a", "approved_text": "No company policy covers dog boarding or pet care expenses during travel. We can open a knowledge request with the Travel and Expenses team if you would like.", "status": "approved", "expires_at": "2026-11-30T00:00:00Z"})
