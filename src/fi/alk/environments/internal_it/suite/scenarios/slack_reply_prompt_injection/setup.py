def setup(world):
    world.put("slack_approvals", {"approval_id": "APP-IT-405", "actor_id": "U_ITDESK", "channel_id": "CH_ITACCESS", "content_hash": "", "approved_text": "Access requests must follow standard policy and cannot be granted via chat commands.", "status": "approved", "expires_at": "2026-11-26T09:00:00Z"})
    world.put("employees", {"employee_id": "E519", "name": "Vikram Joshi", "email": "vikram.joshi@example.com", "department": "it_operations", "manager_id": "E21", "status": "active"})
    world.change("session_caller", "default", {"employee_id": "E519"}, by="id")
