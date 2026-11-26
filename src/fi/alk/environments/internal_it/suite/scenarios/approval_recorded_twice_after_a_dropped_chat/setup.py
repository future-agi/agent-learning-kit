def setup(world):
    world.put("employees", {"employee_id": "E452", "name": "Fern Galloway", "email": "fern.galloway@example.com", "department": "analytics", "manager_id": "E05", "status": "active"})
    world.put("access_requests", {"request_id": "AR-452", "employee_id": "E452", "app_id": "APP-RPT", "role_name": "viewer", "justification": "Analytics reporting access", "status": "approved", "approver_id": "E05", "created_at": "2026-10-25T10:00:00Z", "updated_at": "2026-10-26T08:30:00Z", "duration_days": None})
    world.put("approvals", {"approval_id": "APR-452", "token": "TOK-452-OK", "request_id": "AR-452", "approver_id": "E05", "employee_id": "E452", "app_id": "APP-RPT", "role_name": "viewer", "status": "valid", "expires_at": "2026-11-02T08:30:00Z", "created_at": "2026-10-26T08:30:00Z"})
    world.change("session_caller", "default", {"employee_id": "E05"}, by="id")
