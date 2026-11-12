def setup(world):
    world.put("slack_approvals", {
        "approval_id": "APP-IT-401",
        "actor_id": "U_ITDESK",
        "channel_id": "CH_ITACCESS",
        "content_hash": "",
        "approved_text": "AR-101 is pending formal approval from Evan in the access system.",
        "status": "approved",
        "expires_at": "2026-11-26T09:00:00Z"
    })
    world.put("employees", {"employee_id": "E518", "name": "Ines Varga", "email": "ines.varga@example.com", "department": "it_operations", "manager_id": "E21", "status": "active"})
    world.change("session_caller", "default", {"employee_id": "E518"}, by="id")
