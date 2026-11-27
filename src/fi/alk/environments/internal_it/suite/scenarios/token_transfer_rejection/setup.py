def setup(world):
    world.put("employees", {"employee_id": "E437", "name": "Gunther Central", "email": "gunther@example.com", "department": "operations", "manager_id": "E05", "status": "active"})
    world.put("employees", {"employee_id": "E438", "name": "Janice Litman", "email": "janice@example.com", "department": "operations", "manager_id": "E05", "status": "active"})
    world.put("access_requests", {"request_id": "AR-437", "employee_id": "E437", "app_id": "APP-RPT", "role_name": "viewer", "justification": "Operations reports", "status": "approved", "approver_id": "E05", "created_at": "2026-10-24T10:00:00Z", "updated_at": "2026-10-24T12:00:00Z", "duration_days": None})
    world.put("approvals", {"approval_id": "APR-437", "token": "TOK-437-GNT", "request_id": "AR-437", "approver_id": "E05", "employee_id": "E437", "app_id": "APP-RPT", "role_name": "viewer", "status": "valid", "expires_at": "2026-10-31T12:00:00Z", "created_at": "2026-10-24T12:00:00Z"})
    world.put("access_requests", {"request_id": "AR-438", "employee_id": "E438", "app_id": "APP-RPT", "role_name": "viewer", "justification": "Operations reports", "status": "pending", "approver_id": "E05", "created_at": "2026-10-25T10:00:00Z", "updated_at": "2026-10-25T10:00:00Z", "duration_days": None})
    world.change("session_caller", "default", {"employee_id": "E437"}, by="id")
