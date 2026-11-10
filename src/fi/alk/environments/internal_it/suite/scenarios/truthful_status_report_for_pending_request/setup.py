def setup(world):
    world.put("employees", {"employee_id": "E417", "name": "Oliver Queen", "email": "oliver.queen@example.com", "department": "engineering", "manager_id": "E05", "status": "active"})
    world.put("access_requests", {"request_id": "AR-417", "employee_id": "E417", "app_id": "APP-CODE", "role_name": "readonly", "status": "pending", "approver_id": "E05", "justification": "Onboarding code review", "duration_days": None, "created_at": "2026-10-25T14:00:00Z", "updated_at": "2026-10-25T14:00:00Z"})
