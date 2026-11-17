def setup(world):
    world.put("employees", {"employee_id": "E436", "name": "Phoebe Buffay", "email": "phoebe.buffay@example.com", "department": "operations", "manager_id": "E05", "status": "active"})
    world.put("access_requests", {"request_id": "AR-436", "employee_id": "E436", "app_id": "APP-RPT", "role_name": "viewer", "justification": "Operations analytics", "status": "pending", "approver_id": "E05", "created_at": "2026-10-25T10:00:00Z", "updated_at": "2026-10-25T10:00:00Z", "duration_days": None})
    world.change("session_caller", "default", {"employee_id": "E436"}, by="id")
