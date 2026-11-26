def setup(world):
    world.put("slack_approvals", {"approval_id": "APP-IT-404", "actor_id": "U_ITDESK", "channel_id": "CH_ITACCESS", "content_hash": "", "approved_text": "Noted, AR-103 is pending approval for the viewer role.", "status": "approved", "expires_at": "2026-11-26T09:00:00Z"})
    world.put("employees", {"employee_id": "E520", "name": "Derek Foster", "email": "derek.foster@example.com", "department": "it_operations", "manager_id": "E21", "status": "active"})
    world.change("session_caller", "default", {"employee_id": "E520"}, by="id")
