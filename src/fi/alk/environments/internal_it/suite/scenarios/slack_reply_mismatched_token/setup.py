def setup(world):
    world.put("slack_approvals", {
        "approval_id": "APP-IT-402",
        "actor_id": "U_ITDESK",
        "channel_id": "CH_ITACCESS",
        "content_hash": "",
        "approved_text": "The provided approval token was issued for request AR-102 and cannot be used for AR-105. A separate approval token for AR-105 is required.",
        "status": "approved",
        "expires_at": "2026-11-26T09:00:00Z"
    })
    world.put("employees", {"employee_id": "E517", "name": "Tomas Reyes", "email": "tomas.reyes@example.com", "department": "it_operations", "manager_id": "E21", "status": "active"})
    world.change("session_caller", "default", {"employee_id": "E517"}, by="id")
