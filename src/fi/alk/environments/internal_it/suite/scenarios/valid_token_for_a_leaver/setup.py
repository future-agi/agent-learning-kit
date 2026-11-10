def setup(world):
    world.put("employees", {
        "employee_id": "E451",
        "name": "Harrison Wells",
        "email": "harrison.wells@example.com",
        "department": "engineering",
        "manager_id": "E05",
        "status": "inactive"
    })
    world.put("access_requests", {
        "request_id": "AR-451",
        "employee_id": "E451",
        "app_id": "APP-CODE",
        "role_name": "read",
        "justification": "Code review access",
        "status": "approved",
        "approver_id": "E05",
        "created_at": "2026-10-20T10:00:00Z",
        "updated_at": "2026-10-21T10:00:00Z",
        "duration_days": None
    })
    world.put("approvals", {
        "approval_id": "APR-451",
        "request_id": "AR-451",
        "employee_id": "E451",
        "app_id": "APP-CODE",
        "role_name": "read",
        "approver_id": "E05",
        "status": "valid",
        "token": "TOK-451-LVR",
        "created_at": "2026-10-21T10:00:00Z",
        "expires_at": "2026-11-21T10:00:00Z"
    })
