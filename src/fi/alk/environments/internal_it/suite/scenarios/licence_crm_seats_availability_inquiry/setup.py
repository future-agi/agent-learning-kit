def setup(world):
    world.put("employees", {"employee_id": "E427", "name": "Gregory Clark", "email": "gregory.clark@example.com", "department": "sales", "manager_id": "E13", "status": "active"})
    world.change("session_caller", "default", {"employee_id": "E427"}, by="id")
