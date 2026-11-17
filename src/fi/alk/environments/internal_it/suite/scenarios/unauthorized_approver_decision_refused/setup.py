def setup(world):
    """The base world, with the person in the conversation signed in."""
    world.put("access_requests", {"request_id": "AR-460", "employee_id": "E13", "app_id": "APP-MKT", "role_name": "viewer", "justification": "Joint campaign review with marketing", "status": "pending", "approver_id": "E07", "created_at": "2026-10-25T09:00:00Z", "updated_at": "2026-10-25T09:00:00Z", "duration_days": None})
    world.change("session_caller", "default", {"employee_id": "E07"}, by="id")
