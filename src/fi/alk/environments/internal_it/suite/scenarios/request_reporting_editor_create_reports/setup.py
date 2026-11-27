def setup(world):
    world.put("employees", {"employee_id": "E402", "name": "Elena Rostova", "email": "elena.rostova@example.com", "department": "analytics", "manager_id": "E05", "status": "active"})
    world.change("session_caller", "default", {"employee_id": "E402"}, by="id")
