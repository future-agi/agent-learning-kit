def setup(world):
    world.put("employees", {"employee_id": "E424", "name": "Daniel Craig", "email": "daniel.craig@example.com", "department": "finance", "manager_id": "E06", "status": "active"})
    world.change("session_caller", "default", {"employee_id": "E424"}, by="id")
