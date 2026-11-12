def setup(world):
    world.put("employees", {"employee_id": "E408", "name": "Jack Wilson", "email": "jack.wilson@example.com", "department": "compliance", "manager_id": "E06", "status": "active"})
    world.change("session_caller", "default", {"employee_id": "E408"}, by="id")
