def setup(world):
    world.put("employees", {"employee_id": "E403", "name": "Marcus Thorne", "email": "marcus.thorne@example.com", "department": "sales", "manager_id": "E13", "status": "active"})
    world.change("session_caller", "default", {"employee_id": "E403"}, by="id")
