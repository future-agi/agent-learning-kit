def setup(world):
    world.put("employees", {"employee_id": "E433", "name": "Chandler Bing", "email": "chandler.bing@example.com", "department": "data_proc", "manager_id": "E06", "status": "active"})
    world.change("session_caller", "default", {"employee_id": "E433"}, by="id")
