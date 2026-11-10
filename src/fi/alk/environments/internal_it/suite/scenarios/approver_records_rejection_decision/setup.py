def setup(world):
    world.put("employees", {"employee_id": "E415", "name": "Sam Thorne", "email": "sam.thorne@example.com", "department": "finance", "manager_id": "E06", "status": "active"})
    world.put("access_requests", {"request_id": "AR-401", "employee_id": "E415", "app_id": "APP-FIN", "role_name": "accountant", "status": "pending", "approver_id": "E06", "justification": "General ledger entries", "duration_days": None, "created_at": "2026-10-24T10:00:00Z", "updated_at": "2026-10-24T10:00:00Z"})
