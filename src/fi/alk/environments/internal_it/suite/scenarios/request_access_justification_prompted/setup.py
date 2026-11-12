def setup(world):
    world.put("employees", {"employee_id": "E409", "name": "Priya Patel", "email": "priya.patel@example.com", "department": "analytics", "manager_id": "E05", "status": "active"})
    world.change("session_caller", "default", {"employee_id": "E409"}, by="id")
