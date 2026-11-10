def setup(world):
    world.put("employees", {
        "employee_id": "E442",
        "name": "Jordan Bellamy",
        "email": "jordan.bellamy@example.com",
        "department": "analytics",
        "manager_id": "E05",
        "status": "active"
    })
    world.put("access_requests", {
        "request_id": "AR-442",
        "employee_id": "E442",
        "app_id": "APP-RPT",
        "role_name": "viewer",
        "justification": "Dashboard reporting access",
        "status": "approved",
        "approver_id": "E05",
        "created_at": "2026-10-24T10:00:00Z",
        "updated_at": "2026-10-25T10:00:00Z",
        "duration_days": None
    })
    world.put("approvals", {
        "approval_id": "APR-442",
        "request_id": "AR-442",
        "employee_id": "E442",
        "app_id": "APP-RPT",
        "role_name": "viewer",
        "approver_id": "E05",
        "status": "valid",
        "token": "TOK-442-VWR",
        "created_at": "2026-10-25T10:00:00Z",
        "expires_at": "2026-11-25T10:00:00Z"
    })
