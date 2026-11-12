def setup(world):
    world.put("employees", {"employee_id": "E423", "name": "Devin Hayes", "email": "devin.hayes@example.com", "department": "engineering", "manager_id": "E05", "status": "active"})
    world.change("session_caller", "default", {"employee_id": "E423"}, by="id")
