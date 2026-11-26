def setup(world):
    world.put("slack_approvals", {"approval_id": "APP-IT-403", "actor_id": "U_ITDESK", "channel_id": "CH_ITACCESS", "content_hash": "", "approved_text": "Access for AR-102 has been granted and is now active.", "status": "approved", "expires_at": "2026-11-26T09:00:00Z"})
    world.change("session_caller", "default", {"employee_id": "E21"}, by="id")
