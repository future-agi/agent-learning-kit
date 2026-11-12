def setup(world):
    world.put("employees", {"employee_id": "E419", "name": "Liam O'Connor", "email": "liam.oconnor@example.com", "department": "engineering", "manager_id": "E05", "status": "active"})
    world.change("session_caller", "default", {"employee_id": "E419"}, by="id")
