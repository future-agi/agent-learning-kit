def setup(world):
    """The base world, with the person in the conversation signed in."""
    world.put("employees", {"employee_id": "E501", "name": "Julian Prescott", "email": "julian.prescott@example.com", "department": "engineering", "manager_id": "E05", "status": "active"})
    world.change("session_caller", "default", {"employee_id": "E501"}, by="id")
