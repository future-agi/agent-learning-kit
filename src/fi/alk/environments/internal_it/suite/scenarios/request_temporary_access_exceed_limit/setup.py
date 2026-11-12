def setup(world):
    world.put("employees", {"employee_id": "E420", "name": "Priya Sharma", "email": "priya.sharma@example.com", "department": "finance", "manager_id": "E06", "status": "active"})
    world.change("session_caller", "default", {"employee_id": "E420"}, by="id")
