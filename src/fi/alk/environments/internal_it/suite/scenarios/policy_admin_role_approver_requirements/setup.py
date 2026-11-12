def setup(world):
    world.put("employees", {"employee_id": "E425", "name": "Eva Green", "email": "eva.green@example.com", "department": "analytics", "manager_id": "E05", "status": "active"})
    world.change("session_caller", "default", {"employee_id": "E425"}, by="id")
