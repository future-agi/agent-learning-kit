def setup(world):
    world.put("employees", {"employee_id": "E429", "name": "Irfan Malcolm", "email": "irfan.malcolm@example.com", "department": "engineering", "manager_id": "E05", "status": "active"})
    world.change("session_caller", "default", {"employee_id": "E429"}, by="id")
