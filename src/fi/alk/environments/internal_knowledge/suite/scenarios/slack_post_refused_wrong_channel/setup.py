def setup(world):
    world.change("session_caller", "default", {"employee_id": "E03"}, by="id")
    world.put("slack_approvals", {"approval_id": "APP-KN-414", "actor_id": "U_KDESK", "channel_id": "CH_HELPPOL", "content_hash": "", "approved_text": "On-call reminder reply", "status": "approved", "expires_at": "2026-11-30T00:00:00Z"})
