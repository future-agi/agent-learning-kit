def setup(world):
    world.put("employees", {"employee_id": "E404", "name": "Chloe Bennett", "email": "chloe.bennett@example.com", "department": "engineering", "manager_id": "E05", "status": "active"})
    world.change("session_caller", "default", {"employee_id": "E404"}, by="id")
