def setup(world):
    world.put("access_requests", {"request_id": "AR-405", "employee_id": "E05", "app_id": "APP-CRM", "role_name": "sales_admin", "justification": "Sales team management", "status": "pending", "approver_id": "E13", "approved_by": "E07", "created_at": "2026-10-25T11:00:00Z", "updated_at": "2026-10-25T11:00:00Z", "duration_days": None})
    world.change("session_caller", "default", {"employee_id": "E05"}, by="id")
